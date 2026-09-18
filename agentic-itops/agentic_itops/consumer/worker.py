from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import timedelta
import json
import math
import time
from typing import Callable

from .contracts import ContractError, digest, utc
from .detectors import DetectorPolicy
from .evidence import IngestionService
from .logs import CompleteBatch, IncompleteQuery, LogsReader, QueryWindow
from .processing import AnalysisScope, persist_snapshot, process_snapshot
from .storage_contract import EvidenceRef, EvidenceStore, LostLease, WorkLease, WorkRequest


@dataclass(frozen=True)
class JobOutcome:
    result: dict
    next_work: tuple[WorkRequest, ...] = ()


class ConsumerWorker:
    def __init__(
        self, store, evidence: EvidenceStore, reader: LogsReader,
        scopes: dict[str, AnalysisScope], policies: list[DetectorPolicy], owner: str,
        *, handlers: dict[str, Callable[[WorkLease, Callable[[], None]], JobOutcome]] | None = None,
        max_query_attempts: int = 64, max_ingestion_seconds: int = 600,
    ):
        if not isinstance(owner, str) or not owner.strip():
            raise ContractError("an explicit worker lease owner is required")
        if any(run_id != scope.run_id or scope.producer.key != reader.producer.key
               for run_id, scope in scopes.items()):
            raise ContractError("worker runs must belong to its configured producer")
        self.store, self.evidence, self.reader = store, evidence, reader
        self.scopes, self.policies, self.owner = dict(scopes), list(policies), owner
        self.handlers = dict(handlers or {})
        if any(type(value) is not int or value < 1 for value in (max_query_attempts, max_ingestion_seconds)):
            raise ContractError("source-query attempt and duration limits must be positive integers")
        self.max_query_attempts, self.max_ingestion_seconds = max_query_attempts, max_ingestion_seconds
        if any(kind not in ("reasoning", "postmortem", "hygiene", "analysis", "projection", "investigation", "replacement")
               for kind in self.handlers):
            raise ContractError("unsupported worker handler")

    def process_once(self, kind: str, *, job_id: str | None = None) -> bool:
        if kind not in ("ingest", "coverage", "detector") and kind not in self.handlers:
            raise ContractError("worker kind has no bound implementation")
        lease = self.store.claim(
            kind, self.owner, lease_seconds=180,
            scopes=tuple((scope.producer.key, run_id) for run_id, scope in self.scopes.items()),
            **({"job_id": job_id} if job_id is not None else {}),
        )
        if lease is None:
            return False
        current = [lease]

        def guard():
            current[0] = self.store.renew(current[0], lease_seconds=180)

        try:
            guard()
            if kind == "ingest":
                outcome = self._ingest(lease, guard, lambda: current[0])
            elif kind in ("coverage", "detector"):
                outcome = self._detect(lease, guard)
            else:
                outcome = self.handlers[kind](lease, guard)
            guard()
            if not isinstance(outcome, JobOutcome):
                raise ContractError("worker handler did not return a typed durable outcome")
            self.store.complete(current[0], outcome.result, outcome.next_work)
        except LostLease:
            raise
        except Exception as exc:
            message = json.dumps(f"{type(exc).__name__}: {exc}", ensure_ascii=True)[1:-1]
            if len(message) > 2000:
                message = message[:1970] + " [diagnostic text truncated]"
            self.store.fail(current[0], type(exc).__name__, message)
            raise
        return True

    def _scope(self, payload: dict) -> AnalysisScope:
        scope = self.scopes.get(payload.get("run_id"))
        if scope is None or payload.get("producer_key") != scope.producer.key:
            raise ContractError("work is outside the server-configured producer/run scope")
        if (payload.get("IsSynthetic") is not True or payload.get("OperationalUse") != "prohibited"
                or payload.get("execution_allowed") is not False):
            raise ContractError("work lacks its synthetic no-operation overlay")
        return scope

    def _ingest(self, lease: WorkLease, guard: Callable[[], None],
                current_lease: Callable[[], WorkLease]) -> JobOutcome:
        scope = self._scope(lease.payload)
        if "source" in lease.payload:
            selected = lease.payload["source"]["surfaces"]
            if not selected or not set(selected).issubset(scope.surfaces):
                raise ContractError("ingestion source surfaces exceed configured scope")
            scope = replace(scope, surfaces=tuple(selected))
        if "ingestion_window" in lease.payload:
            interval = lease.payload["ingestion_window"]
            if not isinstance(interval, dict) or set(interval) != {"start", "end"}:
                raise ContractError("invalid bounded ingestion slice")
            start, end = utc(interval["start"]), utc(interval["end"])
            if not scope.ingestion_start <= start < end <= scope.ingestion_end or end - start > timedelta(minutes=15):
                raise ContractError("ingestion slice exceeds configured scope")
            scope = replace(scope, ingestion_start=start, ingestion_end=end)
        reader = self.reader
        limit, deadline = self.max_query_attempts, time.monotonic() + self.max_ingestion_seconds
        if math.ceil((scope.ingestion_end - scope.ingestion_start).total_seconds() / 900) > limit:
            raise IncompleteQuery("declared scan exceeds its source-query attempt budget")
        attempts = 0

        class GuardedReader:
            def read(self, window):
                nonlocal attempts
                if attempts >= limit or time.monotonic() >= deadline:
                    raise IncompleteQuery("source scan attempt/duration budget exhausted; capture remains incomplete")
                attempts += 1
                guard()
                result = reader.read(window)
                guard()
                return result

        store = self.store

        class FencedIngestion:
            def commit_ingestion(self, batch, reference):
                return store.commit_ingestion(batch, reference, lease=current_lease())

        service = IngestionService(GuardedReader(), self.evidence, FencedIngestion())
        cursor = scope.ingestion_start
        commits = []
        while cursor < scope.ingestion_end:
            end = min(cursor + timedelta(minutes=15), scope.ingestion_end)
            window = QueryWindow(
                scope.run_id, scope.surfaces, scope.baseline_start, scope.reference_time, cursor, end,
                min(scope.baseline_start, scope.ingestion_start), max(scope.reference_time, scope.ingestion_end),
            )
            commits.extend(service.ingest(window))
            cursor = end
        return JobOutcome({
            "status": "complete_as_queried", "batch_ids": [item.batch_id for item in commits],
            "child_job_ids": sorted({job_id for item in commits for job_id in item.job_ids}),
            "IsSynthetic": True, "OperationalUse": "prohibited", "execution_allowed": False,
        })

    def _detect(self, lease: WorkLease, guard: Callable[[], None]) -> JobOutcome:
        scope = self._scope(lease.payload)
        try:
            reference = EvidenceRef(**lease.payload["evidence"])
        except (KeyError, TypeError) as exc:
            raise ContractError("ingestion work has no valid evidence reference") from exc
        batch = CompleteBatch.from_document(self.evidence.read(reference))
        if (batch.producer.key != scope.producer.key or batch.window.run_id != scope.run_id
                or batch.window.to_dict() != lease.payload.get("window")
                or digest([batch.producer.key, batch.window.key, reference.sha256]) != lease.payload.get("batch_id")):
            raise ContractError("ingestion work does not match its committed evidence")
        if not set(batch.window.surfaces).issubset(scope.surfaces):
            raise ContractError("batch surfaces exceed configured scope")
        scope = replace(scope, surfaces=batch.window.surfaces)
        guard()
        receipts = self.store.lookup_evidence(scope.producer, scope.run_id,
                                             scope.baseline_start, scope.reference_time)
        report = process_snapshot(scope, tuple(item["evidence"] for item in receipts),
                                  self.evidence, self.policies)
        snapshot = persist_snapshot(report, self.evidence)
        guard()
        next_work = tuple(WorkRequest(
            "reasoning", item["evidence"]["sha256"], {
                "schema_version": "1", "producer_key": scope.producer.key, "run_id": scope.run_id,
                "incident_id": item["incident_id"], "candidate_evidence": item["evidence"],
                "IsSynthetic": True, "OperationalUse": "prohibited", "execution_allowed": False,
            },
        ) for item in report["candidates"])
        return JobOutcome({
            "status": report["condition"], "coverage": report["coverage"],
            "detector_snapshot": asdict(snapshot), "candidate_count": len(next_work),
            "IsSynthetic": True, "OperationalUse": "prohibited", "execution_allowed": False,
        }, next_work)
