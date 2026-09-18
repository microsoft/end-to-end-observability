"""Explicit operator requests over committed observations, never detector episodes."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import re

from .consumer.contracts import ContractError, canonical, digest, iso, strict_json, utc
from .consumer.budget import UsageAuthorization
from .consumer.logs import CompleteBatch
from .consumer.processing import process_snapshot
from .consumer.storage_contract import EvidenceRef
from .review.contracts import identifier, identity_oid, investigation_payload


KIND = "operator_requested_investigation"

DEFAULT_BATCH_LIMITS = {
    "max_total_nanodollars": 5_000_000_000, "max_total_model_calls": 72,
    "max_total_dispatches": 48, "max_total_embedding_calls": 10,
    "max_workflow_seconds": 120,
}


def batch_limits(value=None):
    limits = DEFAULT_BATCH_LIMITS if value is None else value
    if (not isinstance(limits, dict) or set(limits) - {"usage_mode"} != set(DEFAULT_BATCH_LIMITS)
            or limits.get("usage_mode", "enforced") not in ("enforced", "observe_only")
            or any(type(limits[key]) is not int or not 1 <= limits[key] <= 10 ** 15
                   for key in DEFAULT_BATCH_LIMITS)
            or limits["max_workflow_seconds"] > 180):
        raise ContractError("explicit bounded operator batch limits are required")
    return dict(limits)


def approval(value):
    if value is None:
        return None
    required = {"approval_id", "actor_oid", "expires_at_utc"}
    optional = {"limits", "detector_authorization", "replacement_authorization", "usage_policy"}
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - optional:
        raise ContractError("explicit operator batch approval identity, actor and expiry required")
    identifier(value["approval_id"], "approval ID")
    actor = identity_oid(value["actor_oid"])
    expiry, now = utc(value["expires_at_utc"]), datetime.now(timezone.utc)
    if not now < expiry or (expiry.year, expiry.month) != (now.year, now.month):
        raise ContractError("operator batch approval must expire within the current accounting month")
    if "limits" in value:
        if not isinstance(value["limits"], dict):
            raise ContractError("explicit operator batch limits must be an object")
        batch_limits(value["limits"])
    mode = value.get("limits", {}).get("usage_mode", "enforced")
    if "usage_policy" in value:
        policy = value["usage_policy"]
        if (not isinstance(policy, dict) or set(policy) != {
                "mode", "chat_policy_sha256", "embedding_policy_sha256"}
                or policy["mode"] != "observe_only" or mode != "observe_only"
                or any(not isinstance(policy[key], str) or not re.fullmatch("[a-f0-9]{64}", policy[key])
                       for key in ("chat_policy_sha256", "embedding_policy_sha256"))):
            raise ContractError("observe-only approval requires exact chat and embedding policy hashes")
    elif mode != "enforced":
        raise ContractError("observe-only batch limits require explicit usage policy approval")
    if "detector_authorization" in value:
        detector_authorization(value["detector_authorization"])
    if "replacement_authorization" in value:
        replacement_authorization(value["replacement_authorization"])
        if "detector_authorization" not in value:
            raise ContractError("replacement approval requires the exact detector candidate")
    return {**value, "actor_oid": actor}


def require_approval(configured, actor):
    if (configured is None or identity_oid(actor) != configured["actor_oid"]
            or utc(configured["expires_at_utc"]) <= datetime.now(timezone.utc)):
        raise ContractError("operator investigation has no current actor-bound batch approval")


def replacement_authorization(value):
    if (not isinstance(value, dict) or set(value) != {"failed_workflow_id", "max_output_tokens"}
            or not isinstance(value["failed_workflow_id"], str)
            or not re.fullmatch("[a-f0-9]{64}", value["failed_workflow_id"])
            or type(value["max_output_tokens"]) is not int or value["max_output_tokens"] != 8192):
        raise ContractError("replacement requires one exact failed workflow and approved 8192-token ceiling")
    return dict(value)


def require_replacement(configured, failed_workflow_id, actor):
    require_approval(configured, actor)
    selected = replacement_authorization(configured.get("replacement_authorization"))
    if selected["failed_workflow_id"] != failed_workflow_id:
        raise ContractError("replacement differs from the explicitly approved failed predecessor")
    return selected


def detector_authorization(value):
    if (not isinstance(value, dict) or set(value) != {
            "producer_key", "run_id", "policy_sha256", "candidate_sha256"}
            or any(not isinstance(value[key], str) or not re.fullmatch("[a-f0-9]{64}", value[key])
                   for key in ("producer_key", "policy_sha256", "candidate_sha256"))
            or not isinstance(value["run_id"], str) or not value["run_id"].strip()
            or len(value["run_id"]) > 256):
        raise ContractError("detector authorization requires one exact source, policy and candidate hash")
    return dict(value)


def validate_detector_configuration(configured, scope, policies):
    enabled = [policy for policy in policies if policy.enabled]
    selected = configured.get("detector_authorization") if configured is not None else None
    if selected is None:
        if configured is not None and enabled:
            raise ContractError("the single operator acceptance batch cannot enable detector-driven model fan-out")
        return
    selected = detector_authorization(selected)
    if (selected["producer_key"] != scope.producer.key or selected["run_id"] != scope.run_id
            or len(enabled) != 1 or enabled[0].sha256 != selected["policy_sha256"]
            or enabled[0].entity_id is None or enabled[0].location_id is None
            or enabled[0].surface not in scope.surfaces):
        raise ContractError("detector approval differs from the single enabled exact-signal policy/source")


def require_detector_candidate(configured, candidate, reference, scope, policies):
    if configured is None or "detector_authorization" not in configured:
        raise ContractError("single operator batch does not authorize unrelated detector model work")
    require_approval(configured, configured["actor_oid"])
    validate_detector_configuration(configured, scope, policies)
    selected = detector_authorization(configured["detector_authorization"])
    if ("trigger" in candidate or reference.sha256 != selected["candidate_sha256"]
            or digest(candidate) != selected["candidate_sha256"]
            or candidate["source"]["producer_key"] != selected["producer_key"]
            or candidate["source"]["run_id"] != selected["run_id"]
            or candidate["detector"]["policy_sha256"] != selected["policy_sha256"]):
        raise ContractError("detector candidate differs from its exact bounded authorization")


def usage_authorization(limits, configured, kind):
    if kind not in ("chat", "embedding"):
        raise ContractError("unsupported usage authorization kind")
    if limits.caps_enforced:
        if configured is not None and configured.get("usage_policy") is not None:
            raise ContractError("approved usage mode differs from the configured policy")
        return None
    configured = approval(configured)
    if configured is None:
        raise ContractError("observe-only usage requires explicit operator approval")
    require_approval(configured, configured["actor_oid"])
    policy = configured.get("usage_policy", {})
    if (policy.get("mode") != limits.usage_mode
            or policy.get(kind + "_policy_sha256") != limits.sha256
            or limits.deadline_seconds > batch_limits(configured.get("limits"))["max_workflow_seconds"]):
        raise ContractError("observe-only usage differs from the exact approved policy or deadline")
    return UsageAuthorization(configured["approval_id"], configured["actor_oid"],
                              configured["expires_at_utc"], limits.sha256)


def validate_batch_budgets(chat, embedding, limits=None, *, configured=None):
    envelope = batch_limits(limits)
    if not (chat.usage_mode == embedding.usage_mode == envelope.get("usage_mode", "enforced")):
        raise ContractError("chat, embedding and batch usage modes must match")
    usage_authorization(chat, configured, "chat")
    usage_authorization(embedding, configured, "embedding")
    if (chat.deadline_seconds > envelope["max_workflow_seconds"]
            or embedding.deadline_seconds > chat.deadline_seconds):
        raise ContractError("operator batch exceeds its explicitly approved deadline")
    if chat.caps_enforced and (
            chat.monthly_nanodollars + embedding.monthly_nanodollars > envelope["max_total_nanodollars"]
            or chat.api_calls > min(24, envelope["max_total_model_calls"])
            or chat.tool_calls > min(16, envelope["max_total_dispatches"])
            or embedding.api_calls > min(5, envelope["max_total_embedding_calls"])):
        raise ContractError("operator batch exceeds its explicitly approved envelope")


def build_investigation(scope, payload, request_job_id, created_at, receipts, evidence, configured):
    investigation_payload({key: payload[key] for key in ("source", "window", "target", "reason")})
    require_approval(configured, payload["requested_by_oid"])
    if "detector_authorization" in configured:
        raise ContractError("a detector-bound approval cannot authorize an operator investigation")
    selected_scope = replace(scope, surfaces=(payload["target"]["surface"],))
    references = tuple(item["evidence"] for item in receipts)
    report = process_snapshot(selected_scope, references, evidence, [])
    if report["coverage"] != "complete_as_queried":
        raise ContractError("operator investigation requires complete committed ingestion coverage")
    records = {}
    for reference in report["source_evidence"]:
        batch = CompleteBatch.from_document(evidence.read(EvidenceRef(**reference)))
        for item in batch.records:
            row = item.observation.row
            if (row["Domain"] == payload["source"]["domain"]
                    and row["Surface"] == payload["target"]["surface"]
                    and row["EntityId"] == payload["target"]["entity_id"]
                    and row["LocationId"] == payload["target"]["location_id"]
                    and scope.baseline_start <= utc(row["ObservedAt"]) < scope.reference_time
                    and scope.ingestion_start <= item.ingested_at < scope.ingestion_end):
                records[row["EventId"]] = item.observation
    if not 1 <= len(records) <= 256:
        raise ContractError("operator target must have 1..256 real retained observations; no truncation")
    observations = sorted(records.values(), key=lambda item: (utc(item.row["ObservedAt"]), item.row["EventId"]))
    first = observations[0].row
    source = {**payload["source"], "surfaces": [payload["target"]["surface"]]}
    trigger = {"kind": KIND, "approval_id": configured["approval_id"],
               "request_job_id": request_job_id, "requested_by_oid": payload["requested_by_oid"],
               "requested_at_utc": iso(created_at), "reason": payload["reason"],
               "detected_incident": False, "source_evidence": report["source_evidence"]}
    target = {"kind": "synthetic_entity", "domain": first["Domain"], "surface": first["Surface"],
              "entity_id": first["EntityId"], "location_id": first["LocationId"],
              "producer_key": scope.producer.key}
    identity = "investigation-" + digest([KIND, configured["approval_id"], scope.producer.key, scope.run_id])
    candidate = {
        "schema_version": "1", "incident_id": identity, "incident_fingerprint": identity,
        "source": source, "trigger": trigger, "finding_membership": [],
        "correlation_basis": "operator_selected_real_observations_not_a_detector_episode",
        "observed_lifecycle": "unknown", "onset_at": None, "confirmed_at": None, "recovered_at": None,
        "observed_transitions": [], "severity": None, "severity_basis": "not_a_detected_incident",
        "target": {**target, "id": "urn:agentic-itops:synthetic-entity:" + digest(target),
                   "component": first["Payload"].get("measurement", "inventory_position")},
        "detector": {"status": "not_invoked", "policy_id": None, "policy_version": None,
                     "policy_sha256": None, "analysis_sha256": None, "baseline": None, "unit": None},
        "evidence_refs": [{"event_id": item.row["EventId"], "event_key": item.event_key,
                          "source_sha256": item.source_sha256} for item in observations],
        "observation_projection": "source fields excluding service-assigned TimeGenerated",
        "observations": [{key: value for key, value in item.row.items() if key != "TimeGenerated"}
                         for item in observations],
        "IsSynthetic": True, "OperationalUse": "prohibited", "execution_allowed": False,
    }
    validate_investigation(candidate)
    if len(canonical(candidate)) > 1_000_000:
        raise ContractError("operator investigation evidence exceeds its bounded context")
    return candidate


def validate_investigation(candidate):
    trigger = candidate.get("trigger")
    if not isinstance(trigger, dict) or set(trigger) != {
            "kind", "approval_id", "request_job_id", "requested_by_oid", "requested_at_utc",
            "reason", "detected_incident", "source_evidence"}:
        raise ContractError("typed operator-investigation provenance required")
    if (trigger["kind"] != KIND or trigger["detected_incident"] is not False
            or candidate["observed_lifecycle"] != "unknown" or candidate["observed_transitions"]
            or candidate["finding_membership"] or candidate["detector"]["status"] != "not_invoked"
            or any(candidate[key] is not None for key in ("onset_at", "confirmed_at", "recovered_at"))):
        raise ContractError("operator request cannot claim detector or recovery evidence")
    if (candidate["detector"] != {
            "status": "not_invoked", "policy_id": None, "policy_version": None,
            "policy_sha256": None, "analysis_sha256": None, "baseline": None, "unit": None}
            or not isinstance(trigger["request_job_id"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", trigger["request_job_id"])
            or not isinstance(trigger["source_evidence"], list) or not trigger["source_evidence"]
            or len(trigger["source_evidence"]) > 64):
        raise ContractError("operator request requires bounded source provenance, not a detector policy")
    identity_oid(trigger["requested_by_oid"])
    identifier(trigger["approval_id"])
    utc(trigger["requested_at_utc"])
    expected = "investigation-" + digest([KIND, trigger["approval_id"],
                                         candidate["source"]["producer_key"], candidate["source"]["run_id"]])
    if candidate["incident_id"] != expected or candidate["incident_fingerprint"] != expected:
        raise ContractError("operator request identity differs from approved batch")
