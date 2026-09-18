"""One explicit UI action, one exact private worker job; no polling or impersonation."""
from urllib.parse import urlsplit
import re
import time
from datetime import datetime, timezone

from .contracts import OVERLAY, ReviewError, require, source, utc


def descendants(job):
    result = job.get("result") or {}
    return list(dict.fromkeys([*job.get("next_job_ids", ()), *result.get("child_job_ids", ())]))


def descendant_source(submitter, root_id, target_id, root):
    """Resolve legacy child scope only through an already-authorized SQL root."""
    root_payload = root.get("payload") or {}
    root_source = source(root.get("source") or root_payload.get("source") or {})
    bounds = root_payload.get("window")
    transitions = {
        "analysis": {"ingest"}, "ingest": {"coverage", "detector"},
        "coverage": {"reasoning"}, "detector": {"reasoning"},
        "investigation": {"reasoning"}, "replacement": {"reasoning"}, "reasoning": {"projection"},
        "postmortem": {"projection"}, "hygiene": {"projection"}, "projection": set(),
    }
    pending, seen, found = [(root_id, root_source, None)], {}, None
    deadline = time.monotonic() + 10
    while pending:
        require(time.monotonic() < deadline, "job scope graph deadline exhausted", 409)
        job_id, inherited, parent_kind = pending.pop(0)
        require(isinstance(job_id, str) and re.fullmatch(r"[a-f0-9]{64}", job_id),
                "invalid durable child identity", 409)
        job = root if job_id == root_id else submitter.get_job(job_id)
        require(isinstance(job, dict), "durable child job is unavailable", 409)
        kind = job.get("kind")
        require(kind in transitions and (parent_kind is None or kind in transitions[parent_kind]),
                "invalid source lineage transition", 409)
        payload = job.get("payload") or {}
        require(isinstance(payload, dict) and all(
            type(payload.get(key)) is type(value) and payload.get(key) == value
            for key, value in OVERLAY.items()), "invalid descendant safety overlay", 409)
        explicit = job.get("source") or payload.get("source")
        resolved = source(explicit) if explicit else inherited
        if job.get("source") and payload.get("source"):
            require(job["source"] == payload["source"], "conflicting explicit child sources", 403)
        require(all(resolved[key] == inherited[key] for key in ("producer_key", "run_id", "domain"))
                and set(resolved["surfaces"]) <= set(inherited["surfaces"]),
                "descendant source differs from authorized lineage", 403)
        require(explicit or all(payload.get(key) == inherited[key] for key in ("producer_key", "run_id")),
                "unscoped child lacks matching producer/run", 403)
        for key in ("producer_key", "run_id"):
            require(key not in payload or payload[key] == resolved[key], "conflicting child scope", 403)
        window = payload.get("window")
        require(window is None or isinstance(window, dict), "invalid child window", 409)
        if window is not None and "observed_start" in window:
            require(isinstance(bounds, dict) and window.get("run_id") == resolved["run_id"]
                    and isinstance(window.get("surfaces"), list) and bool(window["surfaces"])
                    and set(window["surfaces"]) <= set(resolved["surfaces"]),
                    "child window exceeds authorized source", 403)
            require(utc(bounds["start_utc"]) <= utc(window["observed_start"])
                    < utc(window["observed_end"]) <= utc(bounds["end_utc"]),
                    "child observation window exceeds authorized root", 403)
            resolved = source({**resolved, "surfaces": list(window["surfaces"])})
        if job_id in seen:
            require(seen[job_id] == resolved, "ambiguous descendant source lineage", 403)
            continue
        seen[job_id] = resolved
        require(len(seen) <= 64, "job scope graph exceeds bounded traversal", 409)
        if job_id == target_id:
            found = resolved
        if job.get("status") != "completed":
            continue
        children = descendants(job)
        require(len(children) <= 64 and root_id not in children, "invalid bounded child graph", 409)
        pending.extend((child, resolved, kind) for child in children)
    require(found is not None, "job is not a durable descendant of the authorized root", 403)
    return found


class _PrivateAdvancer:
    def __init__(self, origin, credential, client, *, token_scope):
        from ..service_auth import application_token_scope
        parsed = urlsplit(origin)
        require(parsed.scheme == "https" and parsed.hostname and not parsed.username
                and not parsed.password and origin == f"https://{parsed.netloc}"
                and not parsed.query and not parsed.fragment, "explicit private service HTTPS origin required")
        self.origin, self.credential, self.client = origin, credential, client
        self.token_scope = application_token_scope(token_scope)


class RemoteWorkAdvancer(_PrivateAdvancer):
    def queue_projection(self, workflow_id, idempotency_key):
        require(isinstance(workflow_id, str) and re.fullmatch(r"[a-f0-9]{64}", workflow_id),
                "exact completed workflow digest required")
        require(isinstance(idempotency_key, str) and re.fullmatch(r"[a-f0-9]{64}", idempotency_key),
                "actor-bound projection request digest required")
        return self._queue_result_job("project", {"workflow_id": workflow_id},
                                      {"Idempotency-Key": idempotency_key})

    def queue_replacement(self, workflow_id, actor):
        from .contracts import identity_oid
        require(isinstance(workflow_id, str) and re.fullmatch(r"[a-f0-9]{64}", workflow_id),
                "exact failed workflow digest required")
        return self._queue_result_job("replacement", {"workflow_id": workflow_id,
                                                      "actor_oid": identity_oid(actor)}, {})

    def _queue_result_job(self, action, body, extra_headers):
        from ..service_auth import request_document
        response = self.client.post(
            self.origin + "/api/results/" + action, json=body,
            headers={"Authorization": "Bearer " + self.credential.get_token(self.token_scope).token, **extra_headers},
            timeout=45, follow_redirects=False)
        response.raise_for_status()
        value = request_document(response.content, maximum=4096)
        require(set(value) == {"job_id", "status", "IsSynthetic", "OperationalUse", "execution_allowed"}
                and isinstance(value["job_id"], str) and re.fullmatch(r"[a-f0-9]{64}", value["job_id"])
                and value["status"] == "queued" and value["IsSynthetic"] is True
                and value["OperationalUse"] == "prohibited" and value["execution_allowed"] is False,
                "result job enqueue response differs from its non-operational contract", 503)
        return value["job_id"]

    def readiness(self, job_id):
        from ..service_auth import request_document
        require(isinstance(job_id, str) and re.fullmatch(r"[a-f0-9]{64}", job_id),
                "exact worker job digest required")
        response = self.client.get(
            self.origin + "/api/jobs/" + job_id + "/readiness",
            headers={"Authorization": "Bearer " + self.credential.get_token(self.token_scope).token},
            timeout=60, follow_redirects=False)
        response.raise_for_status()
        value = request_document(response.content, maximum=16384)
        require(set(value) == {"job_id", "status", "sql_job_readable", "candidate_evidence_sha256",
                               "search", "projection", "IsSynthetic", "OperationalUse", "execution_allowed"}
                and value["job_id"] == job_id and value["status"] == "verified"
                and value["sql_job_readable"] is True
                and isinstance(value["candidate_evidence_sha256"], str)
                and re.fullmatch(r"[a-f0-9]{64}", value["candidate_evidence_sha256"])
                and value["IsSynthetic"] is True and value["OperationalUse"] == "prohibited"
                and value["execution_allowed"] is False,
                "worker readiness response differs from the requested scope", 503)
        probe = value["search"]
        require(isinstance(probe, dict) and set(probe) == {
                    "status", "probe_kind", "request_id", "returned_hit_count",
                    "inference_performed", "embedding_performed"}
                and probe["status"] == "verified" and probe["probe_kind"] == "fixed_vector_contract_only"
                and isinstance(probe["request_id"], str) and bool(probe["request_id"])
                and type(probe["returned_hit_count"]) is int and 0 <= probe["returned_hit_count"] <= 3
                and probe["inference_performed"] is False and probe["embedding_performed"] is False,
                "worker readiness did not prove a model-free bounded Search request", 503)
        projection = value["projection"]
        require(projection == {
                    "status": "verified", "required_role": "Results.Project",
                    "probe_kind": "projection_authentication_only",
                    "inference_performed": False, "embedding_performed": False,
                    "result_written": False}
                and all(projection[key] is False for key in (
                    "inference_performed", "embedding_performed", "result_written")),
                "worker readiness did not prove read-only projection authentication", 503)
        return value

    def budget(self, job_id):
        from ..service_auth import request_document
        require(isinstance(job_id, str) and re.fullmatch(r"[a-f0-9]{64}", job_id),
                "exact worker job digest required")
        response = self.client.get(
            self.origin + "/api/jobs/" + job_id + "/budget",
            headers={"Authorization": "Bearer " + self.credential.get_token(self.token_scope).token},
            timeout=30, follow_redirects=False)
        response.raise_for_status()
        value = request_document(response.content, maximum=16384)
        require(value.get("job_id") == job_id
                and set(value) == {"job_id", "chat", "embedding", "scope_totals", "monthly_limits",
                                   "IsSynthetic", "OperationalUse", "execution_allowed"}
                and value["IsSynthetic"] is True and value["OperationalUse"] == "prohibited"
                and value["execution_allowed"] is False,
                "worker budget response differs from the requested scope", 503)
        totals = value["scope_totals"]
        require(isinstance(totals, dict) and set(totals) == {"chat", "embedding"},
                "worker budget scope totals are absent", 503)
        for total in totals.values():
            require(isinstance(total, dict) and set(total) == {
                        "scope_basis", "workflow_count", "api_calls", "tool_calls", "call_states",
                        "accounted_nanodollars", "accounting_basis"}
                    and total["scope_basis"] == "all_history_for_configured_scope"
                    and total["accounting_basis"] == "conservative_reservations_not_a_billing_invoice"
                    and all(type(total[key]) is int and total[key] >= 0
                            for key in ("workflow_count", "api_calls", "tool_calls", "accounted_nanodollars"))
                    and isinstance(total["call_states"], dict)
                    and set(total["call_states"]) <= {"reserved", "settled", "unknown", "overrun"}
                    and all(type(count) is int and count > 0 for count in total["call_states"].values())
                    and sum(total["call_states"].values()) == total["api_calls"],
                    "worker budget scope totals are invalid", 503)
        months = value["monthly_limits"]
        require(isinstance(months, dict) and set(months) == {"chat", "embedding"},
                "worker monthly limits are absent", 503)
        for month in months.values():
            required = {
                        "month_key", "configured_limit_nanodollars", "stored_limit_nanodollars",
                        "effective_limit_nanodollars", "accounted_nanodollars"}
            metadata = {"usage_mode", "caps_enforced", "policy_hash"}
            require(isinstance(month, dict) and set(month) in (required, required | metadata)
                    and isinstance(month["month_key"], str)
                    and re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month["month_key"])
                    and type(month["configured_limit_nanodollars"]) is int
                    and month["configured_limit_nanodollars"] > 0
                    and type(month["accounted_nanodollars"]) is int and month["accounted_nanodollars"] >= 0
                    and (month["stored_limit_nanodollars"] is None
                         or type(month["stored_limit_nanodollars"]) is int and month["stored_limit_nanodollars"] > 0),
                    "worker monthly limits are invalid", 503)
            mode = month.get("usage_mode", "enforced")
            require(mode in ("enforced", "observe_only")
                    and (not metadata <= set(month) or (
                        type(month["caps_enforced"]) is bool
                        and month["caps_enforced"] == (mode == "enforced")
                        and isinstance(month["policy_hash"], str)
                        and re.fullmatch("[a-f0-9]{64}", month["policy_hash"]))),
                    "worker usage enforcement metadata is invalid", 503)
            stored = month["stored_limit_nanodollars"]
            effective = (
                        min(stored, month["configured_limit_nanodollars"])
                        if stored is not None else month["configured_limit_nanodollars"])
            require((mode == "observe_only" and month["effective_limit_nanodollars"] is None
                     or mode == "enforced" and type(month["effective_limit_nanodollars"]) is int
                     and month["effective_limit_nanodollars"] == effective)
                    and (stored is not None or month["accounted_nanodollars"] == 0),
                    "worker effective monthly limit is inconsistent", 503)
        return value

    def advance(self, job_id):
        from ..service_auth import request_document
        require(isinstance(job_id, str) and re.fullmatch(r"[a-f0-9]{64}", job_id),
                "exact worker job digest required")
        response = self.client.post(
            self.origin + "/api/jobs/" + job_id + "/advance", json={},
            headers={"Authorization": "Bearer " + self.credential.get_token(self.token_scope).token},
            timeout=210, follow_redirects=False)
        response.raise_for_status()
        value = request_document(response.content, maximum=65536)
        require(value.get("job_id") == job_id, "worker acknowledged another job", 503)
        return value


class RemotePublisherAdvancer(_PrivateAdvancer):
    def advance(self, outbox_id):
        from ..service_auth import request_document
        from .publisher import canonical_uuid
        canonical_uuid(outbox_id)
        response = self.client.post(
            self.origin + "/api/publisher/run-once", json={"outbox_id": outbox_id},
            headers={"Authorization": "Bearer " + self.credential.get_token(self.token_scope).token},
            timeout=165, follow_redirects=False)
        response.raise_for_status()
        value = request_document(response.content, maximum=65536)
        require(value.get("outbox_id") == outbox_id
                and value.get("status") in {"attempted", "not_claimed"}
                and type(value.get("reconciliation_jobs")) is int and value["reconciliation_jobs"] == 0
                and value.get("IsSynthetic") is True and value.get("OperationalUse") == "prohibited"
                and value.get("execution_allowed") is False,
                "publisher did not acknowledge the exact bounded outbox request", 503)
        receipt = value.get("receipt")
        require((value["status"] == "not_claimed" and receipt is None)
                or (value["status"] == "attempted" and isinstance(receipt, dict)
                    and receipt.get("outbox_id") == outbox_id),
                "publisher receipt differs from the selected outbox item", 503)
        return value


def select_work(submitter, root_id):
    """Follow only immutable SQL child receipts, never browser-supplied kinds."""
    pending, seen = [root_id], set()
    deadline = time.monotonic() + 10
    while pending:
        require(time.monotonic() < deadline, "job graph selection deadline exhausted", 409)
        job_id = pending.pop(0)
        require(isinstance(job_id, str) and re.fullmatch(r"[a-f0-9]{64}", job_id),
                "invalid durable child job identity", 409)
        if job_id in seen:
            continue
        seen.add(job_id)
        require(len(seen) <= 64, "job graph exceeds one bounded advancement", 409)
        job = submitter.get_job(job_id)
        require(job is not None, "durable child job is unavailable", 409)
        expiry = job.get("expires_at")
        expired = (job["status"] == "leased" and isinstance(expiry, datetime)
                   and expiry <= datetime.now(timezone.utc))
        if job["status"] == "queued" or expired:
            return {"root_job_id": root_id, "job_id": job_id, "status": "selected"}
        if job["status"] != "completed":
            return {"root_job_id": root_id, "job_id": job_id, "status": job["status"],
                    "error_code": job.get("error_code")}
        children = descendants(job)
        require(len(children) <= 64, "job fan-out exceeds bounded advancement", 409)
        pending.extend(children)
    return {"root_job_id": root_id, "status": "completed", "job_id": None}
