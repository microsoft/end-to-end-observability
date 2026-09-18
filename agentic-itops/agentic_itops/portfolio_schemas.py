"""Versioned, offline output contracts for all nine prompt agents.

These contracts validate structure, not truth, authorization or effective RBAC.
The planner's ``plan`` union deliberately distinguishes a proposal from no action.
Legacy deterministic modules do not consume or change these prompt contracts.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Any, Collection


CONTRACT_VERSION = "us93-portfolio-v1"
INDUSTRIES = (
    "energy_utilities", "manufacturing", "financial_services", "healthcare",
    "retail_supply_chain", "public_sector",
)


class PortfolioError(ValueError):
    """Invalid source, unresolved binding, or invalid candidate stage output."""


def text(*, nullable: bool = False, nonempty: bool = False) -> dict:
    value: dict = {"type": ["string", "null"] if nullable else "string"}
    if nonempty:
        value["minLength"] = 1
    return value


def enum(*values: str) -> dict:
    return {"type": "string", "enum": list(values)}


def array(items: dict, minimum: int = 0) -> dict:
    return {"type": "array", "items": items, "minItems": minimum}


def obj(**properties: dict) -> dict:
    return {
        "type": "object", "properties": properties,
        "required": list(properties), "additionalProperties": False,
    }


STRINGS = array(text(nonempty=True))
CITATION = obj(
    source_id=text(nonempty=True), revision=text(nullable=True),
    locator=text(nullable=True),
)
HYPOTHESIS = obj(
    hypothesis=text(nonempty=True), confidence=enum("high", "medium", "low", "none"),
    evidence=array(text(nonempty=True), 1), contradicting_evidence=STRINGS,
    next_check=text(),
)
TIMELINE = array(obj(at=text(nonempty=True), observation=text(nonempty=True)))
ACTION_PLAN = obj(
    # Same language as ^(?!none$).+, including newline boundaries, without lookaround.
    proposed_action={"type": "string", "minLength": 1, "pattern":
                     r"^([^n\n]|n($|[^o])|no($|[^n])|non($|[^e])|none([^\n]|\n[\s\S]))"},
    target_resource_id=text(nonempty=True), source_runbook=text(nullable=True),
    classification=enum("mitigation", "fix"),
    blast_radius=obj(
        scope=text(nonempty=True), estimated_affected={"type": "integer", "minimum": 1},
        degradation=text(nonempty=True),
    ),
    preconditions=STRINGS, rollback=text(nonempty=True),
    expected_outcome=text(nonempty=True), follow_up=text(), rationale=text(nonempty=True),
)
NO_ACTION_PLAN = obj(
    proposed_action=enum("none"), target_resource_id={"type": "null"},
    source_runbook={"type": "null"}, classification=enum("none"),
    blast_radius=obj(
        scope=enum("none"), estimated_affected={"type": "integer", "enum": [0]},
        degradation=enum("none"),
    ),
    preconditions={"type": "array", "items": text(), "maxItems": 0},
    rollback=enum("not_applicable"), expected_outcome=text(nonempty=True),
    follow_up=text(), rationale=text(nonempty=True),
)


def stage(**properties: dict) -> dict:
    return obj(
        status=enum("completed", "insufficient_evidence"),
        citations=array(CITATION), evidence_gaps=STRINGS, **properties,
    )


_SCHEMAS = {
    "ops-orchestrator": stage(
        incident_fingerprint=text(nonempty=True), summary=text(nonempty=True),
        hypotheses=array(HYPOTHESIS), proposed_action=text(nonempty=True),
        gate_decision=enum("allow", "deny", "escalate", "not_evaluated"),
        handoff=text(),
    ),
    "triage-dedup": stage(
        incident_fingerprint=text(nonempty=True),
        adjusted_severity=enum("critical", "high", "medium", "low", "info"),
        severity_changed_because=text(nonempty=True), suppress={"type": "boolean"},
        reasoning=text(nonempty=True),
    ),
    "rca-investigator": stage(
        hypotheses=array(HYPOTHESIS), timeline=TIMELINE, notable_absence=text(),
    ),
    "runbook-memory": stage(
        runbooks=array(obj(
            doc_id=text(nonempty=True), revision=text(nonempty=True), title=text(nonempty=True),
            relevance=enum("strong", "partial", "weak"), documented_actions=STRINGS,
            preconditions=text(), prohibitions=STRINGS, excerpt=text(),
        )),
        postmortems=array(obj(
            doc_id=text(nonempty=True), revision=text(nonempty=True), title=text(nonempty=True),
            what_happened=text(), what_it_cost=text(), lesson=text(),
        )),
        coverage_gap=text(),
    ),
    "remediation-planner": stage(plan={"anyOf": [ACTION_PLAN, NO_ACTION_PLAN]}),
    "incident-comms": stage(
        engineer_update=text(nonempty=True), executive_update=text(nonempty=True),
        customer_update=text(), withheld=STRINGS,
    ),
    "postmortem-drafter": stage(
        summary=text(nonempty=True), timeline=TIMELINE, contributing_factors=STRINGS,
        what_made_it_slow=text(),
        actions=array(obj(action=text(nonempty=True), owner=text(nullable=True),
                          status=enum("proposed", "unknown"))),
        lesson=text(), executed_action=enum("none"),
        closure_basis=enum("observed_recovery", "administrative_closure", "unknown"),
    ),
    "alert-hygiene": stage(
        executive_summary=text(nonempty=True),
        estimated_volume_reduction_pct={"type": ["number", "null"], "minimum": 0, "maximum": 100},
        recommendations=array(obj(
            rule_name=text(nonempty=True), current_behavior=text(nonempty=True),
            proposed_change=text(nonempty=True), evidence=array(text(nonempty=True), 1),
            expected_effect=text(nonempty=True),
        )),
        do_not_touch=array(obj(rule_name=text(nonempty=True), reason=text(nonempty=True))),
        limitations=text(nonempty=True),
    ),
    "industry-signal-pack": stage(
        industry=enum(*INDUSTRIES), criticality_adjustment=text(nonempty=True),
        control_frameworks=STRINGS, safety_critical_asset_classes=STRINGS,
        additional_prohibitions=STRINGS,
    ),
}


def output_schema(agent_name: str) -> dict:
    """Return an independent strict JSON schema; unknown agents are errors."""
    if agent_name not in _SCHEMAS:
        raise PortfolioError(f"unknown agent: {agent_name}")
    return deepcopy(_SCHEMAS[agent_name])


def validate_schema(schema: dict) -> None:
    """Validate the bounded JSON Schema subset accepted by this compiler.

    Reject unknown keywords rather than silently ignoring a misspelled constraint.
    All objects are closed and all properties required, including nullable fields.
    """
    from jsonschema import Draft202012Validator

    allowed = {
        "type", "properties", "required", "additionalProperties", "items", "anyOf",
        "enum", "minLength", "minimum", "maximum", "minItems", "maxItems", "pattern",
    }

    def visit(node: Any, where: str) -> None:
        if not isinstance(node, dict) or not node or set(node) - allowed:
            raise PortfolioError(f"{where}: invalid or unknown schema fields")
        if "anyOf" in node:
            if set(node) != {"anyOf"} or not isinstance(node["anyOf"], list) or len(node["anyOf"]) < 2:
                raise PortfolioError(f"{where}: invalid union schema")
            for index, branch in enumerate(node["anyOf"]):
                visit(branch, f"{where}.anyOf[{index}]")
            return
        kind = node.get("type")
        common = {"type", "enum"}
        by_kind = {
            "object": {"properties", "required", "additionalProperties"},
            "array": {"items", "minItems", "maxItems"},
            "string": {"minLength", "pattern"},
            "number": {"minimum", "maximum"},
            "integer": {"minimum", "maximum"},
            "boolean": set(), "null": set(),
        }
        base_kind = kind[0] if isinstance(kind, list) and kind else kind
        if not isinstance(base_kind, str) or set(node) - common - by_kind.get(base_kind, set()):
            raise PortfolioError(f"{where}: unsupported keywords for schema type")
        if "pattern" in node:
            _reject_regex_lookaround(node["pattern"], where)
        if kind == "object":
            props = node.get("properties")
            if (not isinstance(props, dict) or not props or node.get("additionalProperties") is not False
                    or not isinstance(node.get("required"), list)
                    or len(node["required"]) != len(props) or set(node["required"]) != set(props)):
                raise PortfolioError(f"{where}: objects must be closed with every property required")
            for key, value in props.items():
                visit(value, f"{where}.{key}")
        elif kind == "array":
            if "items" not in node:
                raise PortfolioError(f"{where}: array items schema required")
            visit(node["items"], f"{where}.items")
        elif kind not in ("string", "number", "integer", "boolean", "null") and kind not in (
            ["string", "null"], ["number", "null"],
        ):
            raise PortfolioError(f"{where}: unsupported schema type")

    try:
        Draft202012Validator.check_schema(schema)
        visit(schema, "$")
        if schema.get("type") != "object":
            raise PortfolioError("stage schema must have an object root")
    except PortfolioError:
        raise
    except Exception as exc:
        raise PortfolioError(f"invalid JSON schema: {exc}") from exc


def _reject_regex_lookaround(pattern: str, where: str) -> None:
    escaped, in_class, class_can_close = False, False, False
    class_start = -1
    for index, character in enumerate(pattern):
        if escaped:
            escaped = False
            class_can_close = True
        elif character == "\\":
            escaped = True
        elif in_class:
            if character == "]" and class_can_close:
                in_class = False
            elif character != "^" or index != class_start + 1:
                class_can_close = True
        elif character == "[":
            in_class, class_can_close = True, False
            class_start = index
        elif any(pattern.startswith(token, index) for token in ("(?=", "(?!", "(?<=", "(?<!")):
            raise PortfolioError(f"{where}: provider output schemas do not support regex lookaround")


@dataclass(frozen=True)
class ActionPlan:
    proposed_action: str
    target_resource_id: str
    classification: str
    estimated_affected: int
    rollback: str


@dataclass(frozen=True)
class NoActionPlan:
    rationale: str
    proposed_action: str = "none"
    target_resource_id: None = None
    classification: str = "none"
    estimated_affected: int = 0


@dataclass(frozen=True)
class ValidatedStage:
    agent_name: str
    status: str
    canonical_json: str
    cited_source_ids: tuple[str, ...]
    citation_membership_checked: bool
    plan: ActionPlan | NoActionPlan | None = None
    authorized_for_execution: bool = False

    def as_dict(self) -> dict:
        return json.loads(self.canonical_json)


def validate_stage_output(
    agent_name: str, value: dict, *, known_citations: Collection[str] | None = None,
) -> ValidatedStage:
    """Validate a candidate, optionally checking citation IDs against a trusted set.

    Membership is not semantic support or authentication. Gate receipts, target
    authorization, suppression prohibitions, document eligibility/revisions and
    actual workflow order remain the trusted runner's responsibility.
    """
    from jsonschema import Draft202012Validator

    schema = output_schema(agent_name)
    try:
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        Draft202012Validator(schema).validate(value)
    except Exception as exc:
        raise PortfolioError(f"{agent_name}: invalid stage output: {exc}") from exc
    ids = [citation["source_id"] for citation in value["citations"]]
    references = list(ids)
    for hypothesis in value.get("hypotheses", []):
        references.extend(hypothesis["evidence"])
        references.extend(hypothesis["contradicting_evidence"])
    for recommendation in value.get("recommendations", []):
        references.extend(recommendation["evidence"])
    for key in ("runbooks", "postmortems"):
        for document in value.get(key, []):
            references.append(document["doc_id"])
            if not any(citation["source_id"] == document["doc_id"]
                       and citation["revision"] == document["revision"]
                       for citation in value["citations"]):
                raise PortfolioError(f"{agent_name}: document revision must match its citation")
    if value["status"] == "completed" and not ids:
        raise PortfolioError(f"{agent_name}: completed draft requires citations")
    if value["status"] == "insufficient_evidence" and not value["evidence_gaps"]:
        raise PortfolioError(f"{agent_name}: insufficient evidence requires explicit gaps")
    if set(references) - set(ids):
        raise PortfolioError(f"{agent_name}: nested evidence must appear in citations")
    if known_citations is not None and set(references) - set(known_citations):
        raise PortfolioError(f"{agent_name}: citation is not in trusted evidence")
    plan = None
    if agent_name == "remediation-planner":
        proposal = value["plan"]
        if proposal["proposed_action"] == "none":
            plan = NoActionPlan(proposal["rationale"])
        else:
            if value["status"] == "insufficient_evidence":
                raise PortfolioError("insufficient evidence cannot produce an action plan")
            if proposal["classification"] == "mitigation" and not proposal["follow_up"].strip():
                raise PortfolioError("mitigation requires an explicit follow-up")
            if (proposal["source_runbook"] not in ids
                    and not (proposal["proposed_action"] == "annotate_incident"
                             and proposal["source_runbook"] is None)):
                raise PortfolioError("non-annotation action plan requires a cited source runbook")
            plan = ActionPlan(
                proposal["proposed_action"], proposal["target_resource_id"],
                proposal["classification"], proposal["blast_radius"]["estimated_affected"],
                proposal["rollback"],
            )
    if agent_name == "ops-orchestrator":
        if value["proposed_action"] == "none" and value["gate_decision"] != "not_evaluated":
            raise PortfolioError("no action must not claim a gate decision")
        if value["status"] == "insufficient_evidence" and value["proposed_action"] != "none":
            raise PortfolioError("insufficient evidence cannot propose an operation")
    if agent_name == "triage-dedup" and value["status"] == "insufficient_evidence" and value["suppress"]:
        raise PortfolioError("insufficient evidence cannot recommend suppression")
    return ValidatedStage(
        agent_name, value["status"], canonical, tuple(sorted(set(ids))),
        known_citations is not None, plan,
    )
