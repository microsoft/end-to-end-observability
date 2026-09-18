"""Bounded, evidence-backed implementations of the portfolio's mediated tools.

No request accepts a model-selected resource, workspace, KQL, or source identity.
ARM context is explicitly inapplicable to synthetic entity URNs; it is never
represented by an invented resource or by an empty successful ARM query.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Callable

from jsonschema import Draft202012Validator

from .consumer.contracts import ContractError, canonical, digest, strict_json
from .consumer.storage_contract import EvidenceRef
from .portfolio import OPERATIONS, compile_portfolio
from .runtime import WorkflowContext


OVERLAY = {"IsSynthetic": True, "OperationalUse": "prohibited", "execution_allowed": False}
CONTEXT_REQUEST = {
    "type": "object", "properties": {
        "workflow_id": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        "context": {"type": "object", "properties": {
            "key": {"type": "string"}, "sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
            "byte_length": {"type": "integer", "minimum": 1, "maximum": 16000000}},
            "required": ["key", "sha256", "byte_length"], "additionalProperties": False}},
    "required": ["workflow_id", "context"], "additionalProperties": False,
}
CONTEXT_OUTPUT = {
    "type": "object", "properties": {
        "status": {"type": "string", "enum": ["evidence_context", "not_applicable"]},
        "reason": {"type": "string"}, "source": {"type": "object"},
        "evidence": {"type": "array", "items": {"type": "object"}},
        "IsSynthetic": {"type": "boolean", "enum": [True]},
        "OperationalUse": {"type": "string", "enum": ["prohibited"]},
        "execution_allowed": {"type": "boolean", "enum": [False]}},
    "required": ["status", "reason", "source", "evidence", *OVERLAY],
    "additionalProperties": False,
}
MONITOR_OPERATIONS = {
    "get_observation_context": {
        "function_name": "monitor_observation_context",
        "description": "Read the committed EnergyObservations evidence for this exact synthetic incident.",
        "request_schema": CONTEXT_REQUEST, "output_schema": CONTEXT_OUTPUT},
    "get_platform_context": {
        "function_name": "monitor_platform_context",
        "description": "Check whether the trusted target has an applicable Azure Monitor ARM context.",
        "request_schema": CONTEXT_REQUEST, "output_schema": CONTEXT_OUTPUT},
}
HTTP_OPERATIONS = {
    "normalizeSignals": ("POST", "normalize"),
    "evaluateChangeRequest": ("POST", "evaluate"),
    "runKqlQuery": ("POST", "query"),
    "getResourceContext": ("GET", "resource"),
    "getRecentChanges": ("GET", "changes"),
    "analyzeAlertHygiene": ("POST", "analyze"),
}


def backend_request(operation, context):
    """Assemble original OpenAPI request contracts exclusively from trusted input."""
    candidate = context.candidate
    if operation in {"normalizeSignals", "analyzeAlertHygiene"}:
        return {"payloads": candidate["observations"]}
    if operation == "getResourceContext":
        return {"resource_id": candidate["target"]["id"]}
    if operation == "getRecentChanges":
        return {"resource_id": candidate["target"]["id"], "service": candidate["source"]["domain"], "hours": 2}
    if operation == "runKqlQuery":
        import json
        return {"query": "EnergyObservations_CL\n| where RunId == " + json.dumps(candidate["source"]["run_id"])
                + "\n| where EventId in (" + ", ".join(json.dumps(item["event_id"])
                                                      for item in candidate["evidence_refs"]) + ")",
                "timespan": "PT2H"}
    if operation == "evaluateChangeRequest":
        plan = context.stages.get("remediation-planner", {}).get("plan")
        return {"action": plan["proposed_action"] if plan is not None else "none",
                "resource_id": candidate["target"]["id"], "environment": "unknown",
                "service": candidate["source"]["domain"], "estimated_affected": 1,
                "incident_fingerprint": candidate["incident_fingerprint"],
                "justification": plan["rationale"] if plan is not None else "No finalized proposal."}
    raise ContractError("unknown OpenAPI request assembly operation")


def deployment_bindings(root, *, tools_origin: str, tools_audience: str,
                        model_deployment: str, search_binding: dict) -> dict:
    """Construct real contracts; Search snapshot eligibility is supplied, not invented."""
    from .portfolio import _url, _literal
    from .portfolio_dispatch import trusted_search_binding

    _url(tools_origin, "tools origin")
    _literal(tools_audience, "tools audience")
    if model_deployment != "gpt-5-6-luna":
        raise ContractError("the approved all-nine deployment is explicitly gpt-5-6-luna")
    trusted_search_binding(search_binding)
    source = compile_portfolio(root)
    return {
        "models": {agent.model_reference: model_deployment for agent in source.agents},
        "openapi": {name: {"backend": "openapi_http", "base_url": tools_origin.rstrip("/") + "/api",
                           "audience": tools_audience} for name in OPERATIONS},
        "mcp": {"azure_monitor": {
            "backend": "registered_handler", "handler": "itops.monitor.v1",
            "auth": "managed_identity", "allowed_tools": list(MONITOR_OPERATIONS),
            "operations": MONITOR_OPERATIONS}},
        "search": {"search_knowledge": strict_json(canonical(search_binding))},
    }


def check_context(context: WorkflowContext, producer_key: str, run_id: str) -> None:
    candidate = context.candidate
    if (any(candidate.get(k) != v or type(candidate.get(k)) is not type(v) for k, v in OVERLAY.items())
            or candidate.get("source", {}).get("producer_key") != producer_key
            or candidate.get("source", {}).get("run_id") != run_id):
        raise ContractError("tool context is outside the configured synthetic source/run")
    target = candidate.get("target", {})
    if (target.get("kind") != "synthetic_entity"
            or not target.get("id", "").startswith("urn:agentic-itops:synthetic-entity:")):
        raise ContractError("this runtime does not authorize real ARM resource targets")
    refs = {item["event_id"]: item["source_sha256"] for item in candidate["evidence_refs"]}
    rows = candidate["observations"]
    if (not refs or len(refs) != len(candidate["evidence_refs"]) or len(rows) != len(refs)
            or any(refs.get(row["EventId"]) != digest(row) or row["RunId"] != run_id for row in rows)):
        raise ContractError("tool observation projection does not match immutable evidence")


class EvidenceBackends:
    """The six OpenAPI operations and two explicitly declared Monitor operations."""

    def __init__(self, producer_key: str, run_id: str):
        self.producer_key, self.run_id = producer_key, run_id

    def call(self, operation: str, context: WorkflowContext) -> dict:
        check_context(context, self.producer_key, self.run_id)
        candidate = context.candidate
        source, target = candidate["source"], candidate["target"]
        if operation == "normalizeSignals":
            # Candidate membership and fingerprints were produced by deterministic
            # derive_candidates, not by translating observations into fake alerts.
            return {
                "incidents": [candidate], "failures": [],
                "report": {"status": ("operator_requested_investigation" if "trigger" in candidate
                                      else "normalized_detector_candidate"),
                           "trigger_provenance": candidate.get("trigger", {"kind": "detector_episode"}),
                           "correlation_basis": candidate["correlation_basis"],
                           "observation_count": len(candidate["observations"]),
                           "candidate_sha256": digest(candidate)}, **OVERLAY}
        if operation == "runKqlQuery":
            rows = candidate["observations"]
            columns = sorted(rows[0])
            return {
                "columns": columns, "rows": [[row[name] for name in columns] for row in rows],
                "row_count": len(rows), "status": "immutable_query_evidence",
                "table": "EnergyObservations_CL", "source": source,
                "query_authority": "committed_ingestion_receipt; no arbitrary KQL",
                **OVERLAY}
        if operation in ("getResourceContext", "getRecentChanges"):
            return {
                "status": "not_applicable", "found": False,
                "reason": "synthetic_entity_has_no_authorized_ARM_mapping",
                "target": target["id"], "source": source,
                "changes" if operation == "getRecentChanges" else "resources": [],
                "coverage": "not_queried_not_an_empty_Azure_result", **OVERLAY}
        if operation == "evaluateChangeRequest":
            receipt = context.gate_receipt
            if receipt is None or receipt.get("effect") == "not_evaluated":
                return {"status": "deferred", "reason": "No finalized action proposal; execution remains prohibited."}
            if (receipt.get("effect") != "deny"
                    or receipt.get("proposal_sha256") != digest(context.stages["remediation-planner"]["plan"])
                    or receipt.get("target") != target["id"]
                    or receipt.get("receipt_sha256") != digest(
                        {key: value for key, value in receipt.items() if key != "receipt_sha256"})):
                raise ContractError("tool cannot return an unbound safety gate receipt")
            return receipt
        if operation == "analyzeAlertHygiene":
            transitions = candidate["observed_transitions"]
            return {
                "status": "insufficient_alert_history",
                "reason": "EnergyObservations are source observations, not Azure alert-rule firing exports.",
                "detector_policy": candidate["detector"],
                "observed_transition_count": len(transitions),
                "observed_transitions": transitions,
                "rule_noise_scores": [], "estimated_volume_reduction_pct": None,
                "intervention_or_auto_resolution_proven": False, **OVERLAY}
        if operation in MONITOR_OPERATIONS:
            applicable = operation == "get_observation_context"
            return {
                "status": "evidence_context" if applicable else "not_applicable",
                "reason": ("committed_EnergyObservations_only" if applicable else
                           "synthetic_entity_has_no_authorized_ARM_mapping"),
                "source": source, "evidence": candidate["observations"] if applicable else [],
                **OVERLAY}
        raise ContractError("operation is not in the reviewed backend registry")


class TrustedDispatch:
    def __init__(self, portfolio, call: Callable, search: Callable):
        self.portfolio, self.call, self.search = portfolio, call, search

    def __call__(self, agent: str, function: str, arguments: dict, context: WorkflowContext) -> dict:
        if arguments != {}:
            raise ContractError("models may not supply trusted backend arguments")
        functions = {item.name: item for item in self.portfolio.functions_for(agent)}
        if function not in functions:
            raise ContractError("function is not declared for this exact agent")
        requirement = functions[function]
        if requirement.source_tool == "search_knowledge":
            result = self.search(context)
        else:
            result = self.call(agent, requirement.backend_operation, context)
        Draft202012Validator(requirement.output_schema).validate(result)
        return result


class RemoteEvidenceTools:
    """Worker-to-tools call; tokens and context IDs never come from model output."""

    def __init__(self, origin, audience, credential, client, evidence, *, token_scope):
        from .portfolio import _url
        from .service_auth import application_token_scope
        _url(origin, "tools origin")
        self.origin, self.audience = origin.rstrip("/"), audience
        self.token_scope = application_token_scope(token_scope)
        self.credential, self.client, self.evidence = credential, client, evidence

    def __call__(self, agent, operation, context):
        reference = self.evidence.put(canonical({
            "workflow_id": context.workflow_id, "candidate": context.candidate,
            "stages": context.stages, "gate_receipt": context.gate_receipt,
            "trigger_payload": context.trigger_payload,
        }))
        headers = {"Authorization": "Bearer " + self.credential.get_token(
            self.token_scope).token}
        if operation in HTTP_OPERATIONS:
            method, route = HTTP_OPERATIONS[operation]
            headers.update({"X-Itops-Agent": agent, "X-Itops-Workflow": context.workflow_id,
                            "X-Itops-Context": canonical(asdict(reference)).decode()})
            request = backend_request(operation, context)
            response = self.client.request(
                method, self.origin + "/api/" + route, headers=headers,
                **({"params": request} if method == "GET" else {"json": request}),
                timeout=15, follow_redirects=False)
        else:
            response = self.client.post(
                self.origin + "/api/dispatch", headers=headers,
                json={"agent": agent, "operation": operation,
                      "request": {"workflow_id": context.workflow_id, "context": asdict(reference)}},
                timeout=15, follow_redirects=False)
        response.raise_for_status()
        if len(response.content) > 1_000_000:
            raise ContractError("tool response exceeds bounded model context")
        return strict_json(response.content)
