"""Authoritative SQL lifecycle input and immutable, resumable review projection."""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import re

from .application_results import IndeterminateAttempt, persist_result
from .consumer.contracts import ContractError, canonical, digest, iso, strict_json, utc
from .consumer.storage_contract import EvidenceRef, IntegrityConflict, WorkRequest
from .consumer.worker import JobOutcome
from .review.contracts import ReviewError, digest as review_digest, identifier, identity_oid
from .service_backends import OVERLAY, check_context
from .runtime import WorkflowContext


class ApplicationJobs:
    def __init__(self, store, evidence, results, repository, runtime, scope, retained_until, *,
                 projector=None, investigation_approval=None, detector_policies=(),
                 replacement_approval_loader=None, replacement_accounting=None):
        from .investigations import approval
        self.investigation_approval = approval(investigation_approval)
        self.detector_policies = tuple(detector_policies)
        self.replacement_approval_loader = replacement_approval_loader
        self.replacement_accounting = replacement_accounting
        self.store, self.evidence, self.results = store, evidence, results
        self.repository, self.runtime, self.scope = repository, runtime, scope
        if projector is None:
            if getattr(store, "sql_server", True):
                raise ContractError("production jobs require an explicitly bound remote result projector")
            projector = repository.register_result
        if not callable(projector):
            raise ContractError("result projector must be an explicit callable")
        self.projector = projector
        self.retained_until = utc(retained_until)
        self.handlers = {"analysis": self.analysis, "reasoning": self.reasoning,
                         "postmortem": self.lifecycle, "hygiene": self.hygiene,
                         "projection": self.project, "investigation": self.investigation,
                         "replacement": self.replacement}

    def _scope(self, payload):
        source = payload.get("source", payload)
        if (source.get("producer_key") != self.scope.producer.key
                or source.get("run_id") != self.scope.run_id
                or any(payload.get(key) != value for key, value in OVERLAY.items())):
            raise ContractError("job differs from configured non-operational source/run")
        return source

    def _window(self, payload):
        source = self._scope(payload)
        expected = {"start_utc": iso(self.scope.baseline_start), "end_utc": iso(self.scope.reference_time)}
        window = payload.get("window")
        if (not isinstance(window, dict) or set(window) != set(expected)
                or any(utc(window[key]) != utc(value) for key, value in expected.items())
                or not set(source.get("surfaces", ())).issubset(self.scope.surfaces)):
            raise ContractError("browser window must match the approved server-held observation window")

    def analysis(self, lease, guard):
        self._window(lease.payload)
        next_work = []
        cursor = self.scope.ingestion_start
        while cursor < self.scope.ingestion_end:
            end = min(cursor + timedelta(minutes=15), self.scope.ingestion_end)
            payload = {
                "producer_key": self.scope.producer.key, "run_id": self.scope.run_id,
                "source": lease.payload["source"],
                "ingestion_window": {"start": iso(cursor), "end": iso(end)}, **OVERLAY}
            next_work.append(WorkRequest("ingest", digest(payload), payload))
            cursor = end
        guard()
        return JobOutcome({"status": "bounded_ingestion_enqueued", "slice_count": len(next_work), **OVERLAY},
                          tuple(next_work))

    def investigation(self, lease, guard):
        from .investigations import build_investigation
        self._window(lease.payload)
        receipts = self.store.lookup_evidence(
            self.scope.producer, self.scope.run_id, self.scope.baseline_start, self.scope.reference_time)
        candidate = build_investigation(
            self.scope, lease.payload, lease.job_id, self.store.get_job(lease.job_id)["created_at"],
            receipts, self.evidence, self.investigation_approval)
        reference = self.evidence.put(canonical(candidate))
        if self.evidence.read(reference) != canonical(candidate):
            raise IntegrityConflict("investigation evidence failed immutable readback")
        guard()
        payload = {"source": candidate["source"], "incident_id": candidate["incident_id"],
                   "candidate_evidence": asdict(reference), **OVERLAY}
        return JobOutcome({"status": "operator_investigation_prepared", "detected_incident": False,
                           "candidate_evidence": asdict(reference), **OVERLAY},
                          (WorkRequest("reasoning", candidate["incident_id"], payload),))

    def _candidate(self, payload, configured=None):
        configured = self.investigation_approval if configured is None else configured
        self._scope(payload)
        reference = EvidenceRef(**payload["candidate_evidence"])
        candidate = strict_json(self.evidence.read(reference))
        if configured is not None and "trigger" not in candidate:
            from .investigations import require_detector_candidate
            require_detector_candidate(
                configured, candidate, reference, self.scope, self.detector_policies)
        check_context(WorkflowContext("validation", candidate), self.scope.producer.key, self.scope.run_id)
        if candidate["incident_id"] != payload["incident_id"]:
            raise ContractError("job candidate identity differs from immutable evidence")
        if (not set(candidate["source"]["surfaces"]).issubset(self.scope.surfaces)
                or any(not self.scope.baseline_start <= utc(row["ObservedAt"]) < self.scope.reference_time
                       for row in candidate["observations"])):
            raise ContractError("candidate evidence is outside the approved observation window/surfaces")
        if "trigger" in candidate:
            from .investigations import build_investigation, validate_investigation
            validate_investigation(candidate)
            requested = self.store.get_job(candidate["trigger"]["request_job_id"])
            if requested["kind"] != "investigation":
                raise ContractError("operator provenance is not a durable investigation request")
            receipts = self.store.lookup_evidence(
                self.scope.producer, self.scope.run_id, self.scope.baseline_start, self.scope.reference_time)
            committed = {item["evidence"].sha256: item for item in receipts}
            bound = []
            for item in candidate["trigger"]["source_evidence"]:
                receipt = committed.get(item["sha256"])
                if receipt is None or asdict(receipt["evidence"]) != item:
                    raise ContractError("investigation source lacks a matching SQL ingestion receipt")
                bound.append(receipt)
            verified = build_investigation(self.scope, requested["payload"], requested["job_id"],
                                           requested["created_at"], bound, self.evidence,
                                           configured)
            if verified != candidate:
                raise ContractError("operator investigation differs from its SQL-authorized request and real observations")
        elif configured is not None:
            from .consumer.processing import process_snapshot
            receipts = self.store.lookup_evidence(
                self.scope.producer, self.scope.run_id, self.scope.baseline_start, self.scope.reference_time)
            report = process_snapshot(
                replace(self.scope, surfaces=tuple(candidate["source"]["surfaces"])),
                tuple(item["evidence"] for item in receipts), self.evidence, list(self.detector_policies))
            expected = {"incident_id": candidate["incident_id"], "evidence": asdict(reference)}
            if report["coverage"] != "complete_as_queried" or expected not in report["candidates"]:
                raise ContractError("approved detector candidate lacks matching SQL-committed source derivation")
        return candidate, reference

    def _replacement_details(self, workflow_id, actor):
        from .investigations import approval, require_replacement
        from .replacement import failed_predecessor
        configured = (self.replacement_approval_loader() if self.replacement_approval_loader is not None
                      else self.investigation_approval)
        configured = approval(configured)
        require_replacement(configured, workflow_id, identity_oid(actor))
        if self.replacement_accounting is None:
            raise ContractError("replacement requires independent cumulative accounting")
        row, job = self.results.get(workflow_id), self.store.get_job(workflow_id)
        candidate, reference = self._candidate(job["payload"], configured)
        failed_predecessor(row, job, candidate, reference)
        self.replacement_accounting(workflow_id, configured)
        return configured, candidate, reference

    def enqueue_replacement(self, workflow_id, actor):
        from .replacement import request
        configured, candidate, reference = self._replacement_details(workflow_id, actor)
        return self.store.enqueue(request(configured, workflow_id, candidate, reference, kind="replacement"))

    def _require_failed_revision(self, workflow_id, candidate, reference):
        revision = self.repository.get_revision(candidate["incident_id"], 1)
        diagnostic = revision["result"]
        if (self._base_revision(candidate["incident_id"]) != 1 or diagnostic["status"] != "failed"
                or diagnostic["workflow_id"] != workflow_id
                or diagnostic["coverage"].get("report_kind") != "failed_workflow_diagnostic"
                or diagnostic["coverage"].get("failed_workflow_id") != workflow_id
                or diagnostic["coverage"].get("candidate_evidence") != asdict(reference)):
            raise IntegrityConflict("replacement requires the independently projected failed-workflow diagnostic")

    def replacement(self, lease, guard):
        from .replacement import request
        self._scope(lease.payload)
        failed = lease.payload["replacement_of"]
        configured, candidate, reference = self._replacement_details(failed, lease.payload["requested_by_oid"])
        if lease.payload != request(configured, failed, candidate, reference, kind="replacement").payload:
            raise IntegrityConflict("replacement preparation differs from its exact server-held authorization")
        guard()
        self.projector.project_failure(failed)
        guard()
        self._require_failed_revision(failed, candidate, reference)
        return JobOutcome({"status": "failed_diagnostic_projected_replacement_queued",
                           "failed_workflow_id": failed, "analysis_revision": 1, **OVERLAY},
                          (request(configured, failed, candidate, reference, kind="reasoning"),))

    def _model_approval(self, candidate):
        if "trigger" in candidate:
            from .investigations import require_approval
            require_approval(self.investigation_approval, candidate["trigger"]["requested_by_oid"])
        elif self.investigation_approval is not None:
            from .investigations import require_approval
            require_approval(self.investigation_approval, self.investigation_approval["actor_oid"])

    def _base_revision(self, incident_id):
        try:
            return self.repository.current_revision(incident_id)
        except ReviewError as exc:
            if exc.status != 404:
                raise
            return 0

    def _next_projection(self, workflow_id, reference):
        payload = {"producer_key": self.scope.producer.key, "run_id": self.scope.run_id,
                   "workflow_id": workflow_id, "result_evidence": asdict(reference), **OVERLAY}
        return JobOutcome({"status": "result_persisted", "result_id": workflow_id,
                           "result_evidence": asdict(reference), **OVERLAY},
                          (WorkRequest("projection", workflow_id, payload),))

    def reasoning(self, lease, guard):
        replay = self._replay(lease)
        if replay is not None:
            return replay
        candidate, reference = self._candidate(lease.payload)
        replacement_of = lease.payload.get("replacement_of")
        if replacement_of is not None or (
                self.investigation_approval is not None
                and "replacement_authorization" in self.investigation_approval):
            from .replacement import request
            configured, candidate, reference = self._replacement_details(
                replacement_of, lease.payload.get("requested_by_oid"))
            if lease.payload != request(configured, replacement_of, candidate, reference, kind="reasoning").payload:
                raise IntegrityConflict("reasoning differs from the one approved replacement")
            self._require_failed_revision(replacement_of, candidate, reference)
        base = self._base_revision(candidate["incident_id"])
        row, fresh = self.results.begin(lease, candidate, reference, base_revision=base,
                                        portfolio_hash=self.runtime.portfolio_hash,
                                        single_model_batch=self.investigation_approval is not None,
                                        replacement_of=replacement_of)
        if not fresh:
            return self._next_projection(lease.job_id, EvidenceRef(**row["result"]))
        def guarded():
            guard()
            self._model_approval(candidate)
        guarded()
        result = self.runtime.analyze(
            lease.job_id, reference, producer_key=self.scope.producer.key, run_id=self.scope.run_id,
            analysis_revision=row["revision"], evidence_retained_until=self.retained_until, guard=guarded)
        guarded()
        final = persist_result(result, self.evidence)
        self.results.finish(lease, final)
        return self._next_projection(lease.job_id, final)

    def _replay(self, lease):
        self._scope(lease.payload)
        try:
            row = self.results.get(lease.job_id)
        except KeyError:
            return None
        if row["candidate"] != lease.payload["candidate_evidence"] or row["incident_id"] != lease.payload["incident_id"]:
            raise IntegrityConflict("durable final receipt differs from queued immutable source")
        if row["result"] is not None:
            return self._next_projection(lease.job_id, EvidenceRef(**row["result"]))
        raise IndeterminateAttempt("prior model attempt has no final receipt; reconcile, do not rerun")

    def project(self, lease, guard):
        self._scope(lease.payload)
        row = self.results.get(lease.payload["workflow_id"])
        if row["result"] != lease.payload["result_evidence"]:
            raise IntegrityConflict("projection payload differs from durable receipt")
        result = strict_json(self.evidence.read(EvidenceRef(**row["result"])))
        if (result["result_id"] != row["workflow_id"] or result["analysis_revision"] != row["revision"]
                or result["source"]["producer_key"] != self.scope.producer.key
                or result["source"]["run_id"] != self.scope.run_id):
            raise IntegrityConflict("projection result scope/revision differs from SQL authority")
        guard()
        revision = self.projector(result)
        guard()
        self.results.projected(lease, row["workflow_id"])
        return JobOutcome({"status": "review_projected", "result_id": result["result_id"],
                           "analysis_revision": result["analysis_revision"],
                           "etag": revision["etag"], **OVERLAY})

    def enqueue_projection(self, workflow_id, idempotency_key):
        from .review.contracts import result_header, source as source_contract
        if not isinstance(idempotency_key, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", idempotency_key):
            raise ContractError("explicit bounded projection retry idempotency key required")
        row = self.results.get(workflow_id)
        self._scope({"producer_key": row["producer_key"], "run_id": row["run_id"], **OVERLAY})
        if row["result"] is None:
            raise IntegrityConflict("indeterminate model work cannot be replayed as a final result")
        request = self._next_projection(workflow_id, EvidenceRef(**row["result"])).next_work[0]
        header = result_header(strict_json(self.evidence.read(EvidenceRef(**row["result"]))))
        if (header["workflow_id"] != workflow_id or header["result_id"] != workflow_id
                or header["incident_id"] != row["incident_id"] or header["analysis_revision"] != row["revision"]):
            raise IntegrityConflict("projection recovery result differs from its durable workflow receipt")
        source = source_contract(self._scope(header))
        return self.store.enqueue(WorkRequest(
            "projection", digest(["explicit-projection-retry", workflow_id, idempotency_key]),
            {**request.payload, "source": source}))

    def enqueue_postmortem(self, incident_id):
        identifier(incident_id, "incident_id")
        revision = self.repository.current_revision(incident_id)
        current = self.repository.get_revision(incident_id, revision)
        self._current(current, "postmortem")
        closure = self._closure_receipt(current)
        result = current["result"]
        reference = result["coverage"]["candidate_evidence"]
        payload = {"incident_id": incident_id, "analysis_revision": revision,
                   "review_etag": current["etag"], "base_result_hash": current["result_hash"],
                   "closure_receipt_id": closure["receipt_id"], "closure_receipt_sha256": digest(closure),
                   "candidate_evidence": reference, "source": result["source"], **OVERLAY}
        return self.store.enqueue(WorkRequest("postmortem", digest(payload), payload))

    @staticmethod
    def _closure_receipt(revision):
        result = revision["result"]
        history = revision.get("review_history")
        if not isinstance(history, list):
            raise ContractError("postmortem requires the authoritative SQL closure receipt")
        matches = [
            item for item in history if isinstance(item, dict)
            and item.get("action") == "close" and item.get("review_state") == "closed"
            and item.get("incident_id") == result["incident_id"]
            and item.get("analysis_revision") == result["analysis_revision"]
            and item.get("result_hash") == revision["result_hash"]
            and item.get("etag") == revision["etag"]
        ]
        if len(matches) != 1:
            raise ContractError("postmortem requires one exact revision/hash/etag-bound closure receipt")
        receipt = matches[0]
        identifier(receipt.get("receipt_id"), "closure receipt ID")
        identity_oid(receipt.get("actor_oid"))
        utc(receipt["created_at_utc"])
        if (receipt.get("execution_status") != "not_performed"
                or any(type(receipt.get(key)) is not type(value) or receipt.get(key) != value
                       for key, value in OVERLAY.items())):
            raise ContractError("closure receipt cannot imply operational execution")
        return strict_json(canonical(receipt))

    def _current(self, revision, kind):
        result = revision["result"]
        self._scope({**result["source"], **OVERLAY})
        if (not revision["eligible"] or result["analysis_revision"] !=
                self.repository.current_revision(result["incident_id"])):
            raise ContractError("lifecycle requires the current eligible SQL review revision")
        if kind in {"postmortem", "hygiene"} and revision["review_state"] != "closed":
            raise ContractError("lifecycle requires authoritative human closure")
        if kind in {"postmortem", "hygiene"}:
            self._closure_receipt(revision)
        if not result["evidence_refs"] or any(
                not ref["verified"] or utc(ref["retained_until_utc"]) <= datetime.now(timezone.utc)
                for ref in result["evidence_refs"]):
            raise ContractError("lifecycle supporting evidence is missing, invalid or expired")
        return result

    def hygiene(self, lease, guard):
        if "lifecycle_request" in lease.payload:
            return self.lifecycle_requested(lease, guard)
        if "candidate_evidence" in lease.payload:
            return self.lifecycle(lease, guard)
        self._window(lease.payload)
        requested_source = lease.payload["source"]
        entries = self.results.candidates(self.scope.producer.key, self.scope.run_id)
        requests = []
        seen = set()
        for entry in entries:
            if entry["incident_id"] in seen:
                continue
            seen.add(entry["incident_id"])
            revision = self.repository.current_revision(entry["incident_id"])
            current = self.repository.get_revision(entry["incident_id"], revision)
            actual_source = current["result"]["source"]
            if (actual_source["domain"] != requested_source["domain"]
                    or not set(actual_source["surfaces"]).issubset(requested_source["surfaces"])):
                continue
            result = self._current(current, "hygiene")
            closure = self._closure_receipt(current)
            payload = {
                "incident_id": entry["incident_id"], "analysis_revision": revision,
                "review_etag": current["etag"], "base_result_hash": current["result_hash"],
                "candidate_evidence": result["coverage"]["candidate_evidence"], "source": result["source"],
                "closure_receipt_id": closure["receipt_id"], "closure_receipt_sha256": digest(closure), **OVERLAY}
            requests.append(WorkRequest("hygiene", digest([lease.job_id, payload]), payload))
        guard()
        return JobOutcome({"status": "lifecycle_enqueued" if requests else "insufficient_evidence",
                           "candidate_count": len(requests), **OVERLAY}, tuple(requests))

    def lifecycle(self, lease, guard):
        if "lifecycle_request" in lease.payload:
            return self.lifecycle_requested(lease, guard)
        replay = self._replay(lease)
        if replay is not None:
            return replay
        candidate, reference = self._candidate(lease.payload)
        existing = None
        try:
            existing = self.results.get(lease.job_id)
        except KeyError:
            pass
        if existing is not None and existing["result"] is not None:
            return self._next_projection(lease.job_id, EvidenceRef(**existing["result"]))
        current = self.repository.get_revision(candidate["incident_id"], lease.payload["analysis_revision"])
        base = self._current(current, lease.kind)
        if base["coverage"]["candidate_evidence"] != asdict(reference):
            raise ContractError("lifecycle candidate is not the exact reviewed evidence revision")
        if current["etag"] != lease.payload["review_etag"] or current["result_hash"] != lease.payload["base_result_hash"]:
            raise ContractError("lifecycle review changed since its authorized trigger")
        closure = self._closure_receipt(current)
        if closure is not None and (
                closure["receipt_id"] != lease.payload.get("closure_receipt_id")
                or digest(closure) != lease.payload.get("closure_receipt_sha256")):
            raise ContractError("postmortem closure receipt changed since the authorized trigger")
        row, fresh = self.results.begin(lease, candidate, reference,
                                        base_revision=base["analysis_revision"],
                                        portfolio_hash=self.runtime.portfolio_hash,
                                        single_model_batch=self.investigation_approval is not None)
        if not fresh:
            return self._next_projection(lease.job_id, EvidenceRef(**row["result"]))

        def guarded():
            guard()
            self._model_approval(candidate)
            checked = self.repository.get_revision(candidate["incident_id"], base["analysis_revision"])
            self._current(checked, lease.kind)
            if checked["etag"] != current["etag"]:
                raise ContractError("authoritative lifecycle review changed during inference")
            if closure is not None and self._closure_receipt(checked) != closure:
                raise ContractError("postmortem closure receipt changed during inference")

        name = "postmortem-drafter" if lease.kind == "postmortem" else "alert-hygiene"
        payload = {
            "source": base["source"], "incident": candidate, "prior_result": base,
            "review_state": current["review_state"], "savings_estimate_supported": False,
            "closure_basis": ("observed_recovery" if candidate["observed_lifecycle"] in {"recovery_observed", "resolved"}
                              else "administrative_closure"), **OVERLAY}
        if closure is not None:
            payload.update(closure_receipt_id=closure["receipt_id"], closure_receipt_sha256=digest(closure))
        receipts = []
        output = self.runtime.run_lifecycle_stage(
            name, lease.job_id, payload, {item["evidence_id"] for item in base["evidence_refs"]},
            guard=guarded, candidate=candidate, receipt_sink=receipts.append)
        result = strict_json(canonical(base))
        result.update(result_id=lease.job_id, workflow_id=lease.job_id, analysis_revision=row["revision"],
                      created_at_utc=iso(datetime.now(timezone.utc)))
        result["stage_results"][name] = {
            "status": "completed" if output["status"] == "completed" else "incomplete",
            "triggered": True, "output": output, "agent_version": self.runtime.agents[name].reference.version,
            "evidence_refs": [item["source_id"] for item in output["citations"]]}
        if output["status"] != "completed":
            result["status"] = "incomplete"
        result["coverage"]["lifecycle_trigger"] = {
            "base_result_hash": current["result_hash"], "review_etag": current["etag"],
            "review_state": current["review_state"], "provider_receipts": receipts[0]["provider_receipts"]}
        if closure is not None:
            result["coverage"]["lifecycle_trigger"].update(
                closure_receipt_id=closure["receipt_id"], closure_receipt_sha256=digest(closure))
        for document_id, document in receipts[0]["knowledge"].items():
            if document_id not in {ref["evidence_id"] for ref in result["evidence_refs"]}:
                result["evidence_refs"].append({
                    "evidence_id": document_id, "sha256": document["sha256"], "verified": True,
                    "retained_until_utc": iso(min(self.retained_until, utc(document["retained_until_utc"])))})
            result["knowledge_dependencies"] = sorted(set(result["knowledge_dependencies"]) | {document["publication_id"]})
        if name == "postmortem-drafter" and output["status"] == "completed":
            text = canonical(output).decode()
            result["postmortem_draft"] = {
                "draft_id": lease.job_id, "text": text, "sha256": review_digest(text),
                "authorship": {"kind": "ai"}, "applicability": "synthetic energy utilities; operational use prohibited"}
        guarded()
        final = persist_result(result, self.evidence)
        self.results.finish(lease, final)
        return self._next_projection(lease.job_id, final)

    def lifecycle_requested(self, lease, guard):
        self._scope(lease.payload)
        if lease.payload["lifecycle_request"] != lease.kind:
            raise ContractError("lifecycle request kind mismatch")
        current = self.repository.get_revision(lease.payload["incident_id"], lease.payload["analysis_revision"])
        result = self._current(current, lease.kind)
        if current["etag"] != lease.payload["review_etag"] or current["result_hash"] != lease.payload["base_result_hash"]:
            raise ContractError("human closure changed since lifecycle request")
        closure = self._closure_receipt(current)
        payload = {key: value for key, value in lease.payload.items() if key != "lifecycle_request"}
        payload.update(candidate_evidence=result["coverage"]["candidate_evidence"],
                       closure_receipt_id=closure["receipt_id"], closure_receipt_sha256=digest(closure))
        guard()
        return JobOutcome({"status": "human_closed_lifecycle_enqueued", **OVERLAY},
                          (WorkRequest(lease.kind, digest(payload), payload),))
