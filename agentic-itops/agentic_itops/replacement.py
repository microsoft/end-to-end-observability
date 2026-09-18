"""One explicit replacement; failed attempts and their accounting remain immutable."""
from dataclasses import asdict

from .consumer.contracts import ContractError, digest
from .consumer.storage_contract import IntegrityConflict, WorkRequest
from .investigations import batch_limits, require_replacement, usage_authorization
from .service_backends import OVERLAY


def failed_predecessor(row, job, candidate, reference):
    if (job["kind"] != "reasoning" or job["status"] != "failed" or job["error_code"] != "AgentFailure"
            or row["job_kind"] != "reasoning" or row["revision"] != 1 or row["result"] is not None
            or row["projected"] or row["workflow_id"] != job["job_id"]
            or row["incident_id"] != candidate["incident_id"] or "trigger" in candidate
            or row["candidate"] != asdict(reference)
            or job["payload"].get("candidate_evidence") != asdict(reference)
            or job["payload"].get("incident_id") != candidate["incident_id"]
            or "replacement_of" in job["payload"]
            or any(row[key] != candidate["source"][key] for key in ("producer_key", "run_id"))):
        raise IntegrityConflict("replacement requires the exact terminal failed first reasoning attempt without a result")


def request(configured, failed_workflow_id, candidate, reference, *, kind):
    require_replacement(configured, failed_workflow_id, configured["actor_oid"])
    if kind not in {"replacement", "reasoning"}:
        raise ContractError("unsupported replacement step")
    payload = {
        "replacement_of": failed_workflow_id, "replacement_approval_id": configured["approval_id"],
        "requested_by_oid": configured["actor_oid"], "source": candidate["source"],
        "incident_id": candidate["incident_id"], "candidate_evidence": asdict(reference), **OVERLAY,
    }
    return WorkRequest(kind, digest(["one-explicit-replacement", kind, configured["approval_id"],
                                    failed_workflow_id]), payload)


def require_capacity(chat, embedding, failed_workflow_id, configured):
    from .review.search_delivery import BudgetLedgerEmbeddingGate
    limits = batch_limits(configured.get("limits"))
    if not (chat.limits.usage_mode == embedding.limits.usage_mode == limits.get("usage_mode", "enforced")):
        raise ContractError("replacement usage modes must match")
    usage_authorization(chat.limits, configured, "chat")
    usage_authorization(embedding.limits, configured, "embedding")
    selected = require_replacement(configured, failed_workflow_id, configured["actor_oid"])
    if chat.limits.max_output_tokens != selected["max_output_tokens"]:
        raise ContractError("runtime output ceiling differs from explicit replacement approval")
    prior = chat.inspect_workflow(failed_workflow_id)
    vector_id = BudgetLedgerEmbeddingGate(embedding, workflow_id=failed_workflow_id).workflow_id
    vector = embedding.inspect_workflow(vector_id)
    if (prior.get("status") == "not_admitted" or prior.get("api_calls", 0) <= 0
            or prior.get("call_states") != {"settled": prior["api_calls"]}
            or (vector.get("status") != "not_admitted"
                and (set(vector.get("call_states", {})) - {"settled"}
                     or vector.get("call_states", {}).get("settled", 0) != vector.get("api_calls")))):
        raise IntegrityConflict("replacement requires fully settled predecessor provider accounting")
    totals = {"chat": chat.inspect_scope(), "embedding": embedding.inspect_scope()}
    if any(value.get("scope_basis") != "all_history_for_configured_scope" for value in totals.values()):
        raise IntegrityConflict("replacement requires all-history cumulative accounting")
    if chat.limits.caps_enforced and (
            totals["chat"]["api_calls"] + chat.limits.api_calls > limits["max_total_model_calls"]
            or totals["chat"]["tool_calls"] + chat.limits.tool_calls > limits["max_total_dispatches"]
            or totals["embedding"]["api_calls"] + embedding.limits.api_calls > limits["max_total_embedding_calls"]
            or sum(value["accounted_nanodollars"] for value in totals.values()) >= limits["max_total_nanodollars"]):
        raise ContractError("insufficient remaining cumulative envelope for the bounded replacement")
    return totals
