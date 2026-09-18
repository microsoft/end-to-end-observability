"""SQL-fenced attempt receipts and immutable result-to-review projection.

An interrupted attempt without a final receipt is indeterminate, never silently
rerun. A completed receipt can be projected repeatedly without inference or
renewing any approval. Schema creation belongs to a separate migration identity.
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import re

from .consumer.contracts import ContractError, canonical, digest, strict_json
from .consumer.storage_contract import EvidenceRef, IntegrityConflict


class IndeterminateAttempt(IntegrityConflict):
    pass


class ApplicationResults:
    def __init__(self, workflow_store):
        self.store = workflow_store
        self.table = workflow_store._table("application_runs")

    def migrate(self):
        """Local fixture bootstrap only; production DDL belongs to deployment.migrate."""
        if self.store.sql_server:
            raise ContractError("production application schema must use the deployment.migrate hash ledger")
        script = Path(__file__).with_name("consumer").joinpath(
            "runtime_sql", "application.local-test.sql").read_text(encoding="utf-8")
        with self.store._transaction() as cursor:
            for statement in script.split("-- batch"):
                if statement.strip():
                    cursor.execute(statement)

    def get(self, workflow_id):
        if not isinstance(workflow_id, str) or not re.fullmatch(r"[a-f0-9]{64}", workflow_id):
            raise ContractError("an exact durable workflow digest is required")
        with self.store._transaction() as cursor:
            row = self.store._one(cursor, f"SELECT * FROM {self.table} WHERE workflow_id=?", (workflow_id,))
            if row is None:
                raise KeyError(workflow_id)
            return self._decode(row)

    @staticmethod
    def _decode(row):
        row["candidate"] = strict_json(row.pop("candidate_json"))
        result = row.pop("result_json")
        row["result"] = strict_json(result) if result is not None else None
        return row

    def begin(self, lease, candidate, reference, *, base_revision, portfolio_hash, single_model_batch=False,
              replacement_of=None):
        if type(single_model_batch) is not bool:
            raise ContractError("explicit model batch admission mode required")
        if replacement_of is not None and (
                not single_model_batch or lease.kind != "reasoning"
                or not isinstance(replacement_of, str) or not re.fullmatch("[a-f0-9]{64}", replacement_of)):
            raise ContractError("replacement is limited to explicitly bounded reasoning admission")
        workflow_id = lease.job_id
        encoded = canonical(asdict(reference)).decode()
        with self.store._transaction() as cursor:
            self.store._insert_lock(cursor)
            self.store._verify_lease(cursor, lease)
            row = self.store._one(cursor, f"SELECT * FROM {self.table} WHERE workflow_id=?", (workflow_id,))
            if row:
                if (row["candidate_json"] != encoded or row["portfolio_hash"] != portfolio_hash
                        or row["incident_id"] != candidate["incident_id"]):
                    raise IntegrityConflict("attempt source/portfolio is immutable")
                if row["result_json"] is None:
                    raise IndeterminateAttempt("prior model attempt has no final receipt; reconcile, do not rerun")
                return self._decode(row), False
            if single_model_batch or candidate.get("trigger", {}).get("kind") == "operator_requested_investigation":
                if lease.kind not in {"reasoning", "postmortem", "hygiene"}:
                    raise ContractError("bounded batch permits exactly three named model workflow kinds")
                previous = self.store._one(cursor,
                    f"SELECT workflow_id FROM {self.table} WHERE incident_id=? AND job_kind=?",
                    (candidate["incident_id"], lease.kind))
                if previous is not None:
                    if replacement_of is None:
                        raise IntegrityConflict("approved bounded batch already reserved this model workflow kind")
                    count = self.store._one(cursor,
                        f"SELECT COUNT(*) AS total FROM {self.table} WHERE incident_id=? AND job_kind=?",
                        (candidate["incident_id"], lease.kind))
                    if count["total"] != 1 or previous["workflow_id"] != replacement_of:
                        raise IntegrityConflict("the one approved replacement has already been reserved")
                    from .replacement import failed_predecessor
                    prior = self.store._one(cursor, f"SELECT * FROM {self.table} WHERE workflow_id=?",
                                            (replacement_of,))
                    job = self.store._one(cursor,
                        f"SELECT * FROM {self.store._table('jobs')} WHERE job_id=?", (replacement_of,))
                    job["payload"] = strict_json(job["payload_json"])
                    failed_predecessor(self._decode(prior), job, candidate, reference)
                    if base_revision != 1:
                        raise IntegrityConflict("replacement requires the truthful failed first review revision")
                elif replacement_of is not None:
                    raise IntegrityConflict("approved failed predecessor is absent")
            if type(base_revision) is not int or base_revision < 0:
                raise ContractError("explicit authoritative base revision required")
            row = self.store._one(
                cursor, f"SELECT MAX(revision) AS revision FROM {self.table} WHERE incident_id=?",
                (candidate["incident_id"],))
            if row["revision"] is not None and row["revision"] > base_revision:
                raise IntegrityConflict("an earlier reserved revision has not been projected")
            revision = base_revision + 1
            cursor.execute(
                f"INSERT INTO {self.table} (workflow_id,incident_id,revision,producer_key,run_id,"
                "candidate_json,portfolio_hash,job_kind,fencing_token,result_json,projected) "
                "VALUES (?,?,?,?,?,?,?,?,?,NULL,0)",
                (workflow_id, candidate["incident_id"], revision, candidate["source"]["producer_key"],
                 candidate["source"]["run_id"], encoded, portfolio_hash, lease.kind, lease.fencing_token))
            self.store._verify_lease(cursor, lease)
        return self.get(workflow_id), True

    def finish(self, lease, reference):
        encoded = canonical(asdict(reference)).decode()
        with self.store._transaction() as cursor:
            self.store._verify_lease(cursor, lease)
            row = self.store._one(cursor, f"SELECT * FROM {self.table} WHERE workflow_id=?", (lease.job_id,))
            if row is None or row["fencing_token"] != lease.fencing_token:
                raise IntegrityConflict("attempt is not owned by this fence")
            if row["result_json"] is not None and row["result_json"] != encoded:
                raise IntegrityConflict("final result receipt cannot be rewritten")
            cursor.execute(f"UPDATE {self.table} SET result_json=? WHERE workflow_id=?",
                           (encoded, lease.job_id))
            self.store._verify_lease(cursor, lease)

    def projected(self, lease, workflow_id):
        with self.store._transaction() as cursor:
            self.store._verify_lease(cursor, lease)
            row = self.store._one(cursor, f"SELECT result_json FROM {self.table} WHERE workflow_id=?",
                                  (workflow_id,))
            if row is None or row["result_json"] is None:
                raise IntegrityConflict("projection requires an immutable final receipt")
            cursor.execute(f"UPDATE {self.table} SET projected=1 WHERE workflow_id=?", (workflow_id,))
            self.store._verify_lease(cursor, lease)

    def candidates(self, producer_key, run_id, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ContractError("candidate enumeration must be explicitly bounded")
        with self.store._transaction() as cursor:
            top = f"TOP ({limit + 1}) " if self.store.sql_server else ""
            tail = "" if self.store.sql_server else f" LIMIT {limit + 1}"
            cursor.execute(
                f"SELECT {top}workflow_id,incident_id,candidate_json FROM {self.table} "
                "WHERE producer_key=? AND run_id=? AND projected=1 AND job_kind='reasoning' "
                f"ORDER BY workflow_id{tail}", (producer_key, run_id))
            rows = cursor.fetchall()
        if len(rows) > limit:
            raise ContractError("candidate enumeration exceeds configured bounded batch")
        return [{"workflow_id": row[0], "incident_id": row[1], "candidate": strict_json(row[2])}
                for row in rows]

    def authorize_context(self, workflow_id, candidate):
        with self.store._transaction() as cursor:
            job = self.store._one(
                cursor, f"SELECT * FROM {self.store._table('jobs')}{self.store._locked()} WHERE job_id=?",
                (workflow_id,))
            row = self.store._one(cursor, f"SELECT * FROM {self.table}{self.store._locked()} WHERE workflow_id=?",
                                  (workflow_id,))
            if (row is None or job is None or row["result_json"] is not None or job["status"] != "leased"
                    or row["fencing_token"] != job["fencing_token"]):
                raise IntegrityConflict("tools require an active, unreplayed application attempt")
            if self.store._lease(job).expires_at <= self.store._now(cursor):
                raise IntegrityConflict("tools attempt lease expired")
            row = self._decode(row)
        if (row["candidate"]["sha256"] != digest(candidate)
                or row["incident_id"] != candidate["incident_id"]
                or row["producer_key"] != candidate["source"]["producer_key"]
                or row["run_id"] != candidate["source"]["run_id"]):
            raise IntegrityConflict("tool candidate differs from SQL-authorized attempt")
        return row


def persist_result(result, evidence):
    from .review.contracts import result_header
    validated = result_header(result)
    reference = evidence.put(canonical(validated))
    if strict_json(evidence.read(reference)) != validated:
        raise IntegrityConflict("final result read-after-write verification failed")
    return reference
