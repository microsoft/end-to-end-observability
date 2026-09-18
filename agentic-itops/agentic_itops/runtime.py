"""Version-bound portfolio orchestration; callers supply authenticated backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Callable

from .consumer.budget import BudgetLedger
from .consumer.contracts import ContractError, canonical, digest, iso, strict_json, utc
from .consumer.responses import AgentEngine, AgentFailure, ResponsesTransport, RuntimeAgent
from .consumer.storage_contract import EvidenceRef, EvidenceStore
from .portfolio_schemas import ActionPlan, NoActionPlan, output_schema, validate_stage_output
from .model_context import model_candidate
from .safety_gate import ChangeRequest, ChangeSafetyGate


AGENTS = (
    "ops-orchestrator", "triage-dedup", "rca-investigator", "runbook-memory",
    "remediation-planner", "incident-comms", "postmortem-drafter",
    "alert-hygiene", "industry-signal-pack",
)
SPECIALIST_ORDER = ("runbook-memory", "rca-investigator", "remediation-planner", "incident-comms")
DELEGATIONS = {"delegate_" + name.replace("-", "_"): name for name in SPECIALIST_ORDER}


def bind_runtime_agents(portfolio, versions: dict[str, str]) -> dict[str, RuntimeAgent]:
    if not portfolio.bindings_complete:
        raise ContractError("portfolio bindings are incomplete: " + "; ".join(portfolio.blockers))
    if portfolio.mode != "trusted_dispatch" or set(versions) != set(AGENTS):
        raise ContractError("runtime requires the approved transport and all nine exact versions")
    agents = {}
    for compiled in portfolio.agents:
        functions = portfolio.functions_for(compiled.name)
        represented = {function.source_tool for function in functions}
        if set(compiled.tool_names) - represented:
            raise ContractError("a declared tool has no budgeted trusted-dispatch implementation")
        if set(compiled.connected_agents) - {function.target_agent for function in functions}:
            raise ContractError("a connected agent was omitted from runtime dispatch")
        agents[compiled.name] = RuntimeAgent.create(
            compiled.name, versions[compiled.name], compiled.output_schema,
            portfolio.function_schemas(compiled.name),
            {"portfolio_sha256": portfolio.content_sha256, "agent_content_sha256": compiled.content_sha256},
            function_outputs={function.name: function.output_schema for function in functions},
        )
    return agents


@dataclass
class WorkflowContext:
    workflow_id: str
    candidate: dict
    stages: dict = field(default_factory=dict)
    knowledge: dict = field(default_factory=dict)
    gate_receipt: dict | None = None
    trigger_payload: dict | None = None

    @property
    def citation_ids(self) -> set[str]:
        return {item["event_id"] for item in self.candidate["evidence_refs"]} | set(self.knowledge)

    def coordinator_progress(self) -> dict:
        remaining = [name for name in SPECIALIST_ORDER if name not in self.stages]
        return {
            "validated_specialist_stages": [name for name in SPECIALIST_ORDER if name in self.stages],
            "remaining_specialist_stages": remaining,
            "next_required_delegate": ("delegate_" + remaining[0].replace("-", "_") if remaining else None),
            "gate_receipt_present": self.gate_receipt is not None,
            "final_output_allowed": not remaining and self.gate_receipt is not None,
            "rule": (
                "For ops-orchestrator only: do not return final JSON while final_output_allowed is false. "
                "Call next_required_delegate with empty arguments when provided. A bound gate receipt is "
                "also required. Final output remains subject to all contract and evidence validation."),
        }

    def model_input(self) -> dict:
        return {
            "industry": "energy_utilities", "incident": model_candidate(self.candidate),
            "validated_stages": self.stages, "gate_receipt": self.gate_receipt,
            "required_specialist_order": list(SPECIALIST_ORDER),
            "coordinator_progress": self.coordinator_progress(),
            "no_action_gate_result": "not_evaluated; execution remains prohibited",
            "root_hypotheses_rule": "Copy validated RCA hypotheses exactly, without additions or rewriting.",
            "trigger_provenance": self.candidate.get("trigger", {"kind": "detector_episode"}),
            "knowledge_scope_rule": ("Search results are additionally restricted to this source domain/surfaces. "
                                     "An empty bounded retrieval does not prove that no approved knowledge exists."),
            "investigation_rule": (
                "An operator request is not detection. Analyze the explicit question using retained observations. "
                "A complete descriptive assessment may report no anomaly or causal premise. Do not invent "
                "thresholds, onset, recovery or human authorship. Report notable absences."
                if "trigger" in self.candidate else "Preserve the bound deterministic detector episode."),
            "IsSynthetic": True, "OperationalUse": "prohibited", "execution_allowed": False,
        }


class PortfolioRuntime:
    def __init__(
        self, agents: dict[str, RuntimeAgent], transport: ResponsesTransport,
        ledger: BudgetLedger, evidence: EvidenceStore, gate_policy: dict,
        backend: Callable[[str, str, dict, WorkflowContext], dict],
        *, knowledge_guard: Callable[[WorkflowContext], None] | None = None,
    ):
        if set(agents) != set(AGENTS):
            raise ContractError("all nine exact portfolio agents must be version-bound")
        for name, agent in agents.items():
            if agent.definition["output_schema"] != output_schema(name):
                raise ContractError("runtime output contract differs from the portfolio compiler")
        hashes = [agent.definition["provenance"].get("portfolio_sha256") for agent in agents.values()]
        if (not isinstance(hashes[0], str) or not re.fullmatch(r"[a-f0-9]{64}", hashes[0])
                or any(value != hashes[0] for value in hashes)):
            raise ContractError("all agent versions must share one explicit compiled portfolio digest")
        self.portfolio_hash = hashes[0]
        if not set(DELEGATIONS).issubset(agents["ops-orchestrator"].definition["functions"]):
            raise ContractError("the actual coordinator must expose all four delegation functions")
        self.agents, self.transport, self.ledger, self.evidence = dict(agents), transport, ledger, evidence
        self.gate = ChangeSafetyGate(strict_json(canonical(gate_policy)))
        self.policy_hash = digest(self.gate.policy)
        self.backend = backend
        self.knowledge_guard = knowledge_guard

    def _guard_knowledge(self, context: WorkflowContext) -> None:
        if context.knowledge:
            if {item["event_id"] for item in context.candidate["evidence_refs"]} & set(context.knowledge):
                raise AgentFailure("knowledge document IDs cannot shadow source observation IDs")
            if self.knowledge_guard is None:
                raise AgentFailure("retrieved knowledge has no bound eligibility/revocation guard")
            if any(utc(item["retained_until_utc"]) <= datetime.now(timezone.utc)
                   for item in context.knowledge.values()):
                raise AgentFailure("retrieved knowledge support has expired")
            self.knowledge_guard(context)

    def _validate(self, name: str, value: dict, context: WorkflowContext) -> None:
        validated = validate_stage_output(name, value, known_citations=context.citation_ids)
        candidate = context.candidate
        events = {item["event_id"]: item for item in candidate["evidence_refs"]}
        for citation in value["citations"]:
            document = context.knowledge.get(citation["source_id"])
            if document is not None and citation["revision"] != document["revision"]:
                raise AgentFailure("knowledge citation revision does not match the approved retrieval")
            if (document is None and citation["revision"] is not None
                    and citation["revision"] != events[citation["source_id"]].get("source_sha256")):
                raise AgentFailure("observation citation cannot claim a knowledge document revision")
        for collection in ("runbooks", "postmortems"):
            for document in value.get(collection, []):
                retrieved = context.knowledge.get(document["doc_id"])
                if retrieved is None or document["revision"] != retrieved["revision"]:
                    raise AgentFailure("knowledge document was not retrieved at the declared approved revision")
        if name in ("ops-orchestrator", "triage-dedup"):
            if value["incident_fingerprint"] != candidate["incident_fingerprint"]:
                raise AgentFailure("agent substituted another incident fingerprint")
        if name == "industry-signal-pack" and value["industry"] != "energy_utilities":
            raise AgentFailure("agent substituted the trusted producer industry")
        if (name == "postmortem-drafter" and context.trigger_payload is not None
                and context.trigger_payload.get("closure_basis") is not None
                and value["closure_basis"] != context.trigger_payload["closure_basis"]):
            raise AgentFailure("postmortem cannot replace administrative closure with observed recovery")
        if name == "rca-investigator" and not value["hypotheses"] and not value["notable_absence"].strip():
            raise AgentFailure("an empty RCA requires explicit notable absence")
        if isinstance(validated.plan, ActionPlan):
            if (validated.plan.target_resource_id != candidate["target"]["id"]
                    or validated.plan.estimated_affected != 1):
                raise AgentFailure("planner target/radius differs from the single authorized synthetic entity")
            runbook = value["plan"]["source_runbook"]
            if runbook is not None and runbook not in context.knowledge:
                raise AgentFailure("planner runbook was not returned by approved retrieval")
        if name == "ops-orchestrator":
            progress = context.coordinator_progress()
            if not progress["final_output_allowed"]:
                raise AgentFailure(
                    "coordinator finished before all specialist/gate stages: "
                    f"missing_stages={','.join(progress['remaining_specialist_stages']) or 'none'}; "
                    f"gate_receipt_present={progress['gate_receipt_present']}")
            proposal = context.stages["remediation-planner"]["plan"]
            if (value["proposed_action"] != proposal["proposed_action"]
                    or value["gate_decision"] != context.gate_receipt["effect"]
                    or value["hypotheses"] != context.stages["rca-investigator"]["hypotheses"]):
                raise AgentFailure("coordinator rewrote the validated proposal, gate or RCA")

    def _gate(self, context: WorkflowContext) -> dict:
        if digest(self.gate.policy) != self.policy_hash:
            raise AgentFailure("gate policy changed within a version-bound runtime")
        output = context.stages["remediation-planner"]
        validated = validate_stage_output("remediation-planner", output, known_citations=context.citation_ids)
        proposal = output["plan"]
        if isinstance(validated.plan, NoActionPlan):
            decision = {
                "effect": "not_evaluated", "reason": "no_action_proposed",
                "evaluated_at": iso(datetime.now(timezone.utc)),
            }
        else:
            decision = self.gate.evaluate(ChangeRequest(
                action=proposal["proposed_action"], resource_id=context.candidate["target"]["id"],
                environment="unknown", service=context.candidate["source"]["domain"],
                blast_radius_scope=proposal["blast_radius"]["scope"],
                estimated_affected=proposal["blast_radius"]["estimated_affected"],
                incident_fingerprint=context.candidate["incident_fingerprint"],
                justification=proposal["rationale"], is_synthetic=True, operational_use="prohibited",
            )).to_dict()
            if decision["effect"] != "deny":
                raise AgentFailure("synthetic proposal unexpectedly received operational authority")
        receipt = {
            **decision, "workflow_id": context.workflow_id,
            "proposal_sha256": digest(proposal), "evidence_sha256": digest(context.candidate["evidence_refs"]),
            "policy_sha256": self.policy_hash, "target": context.candidate["target"]["id"],
            "IsSynthetic": True, "OperationalUse": "prohibited", "execution_allowed": False,
        }
        receipt["receipt_sha256"] = digest(receipt)
        return receipt

    def analyze(
        self, workflow_id: str, candidate_reference: EvidenceRef, *,
        producer_key: str, run_id: str, analysis_revision: int,
        evidence_retained_until: datetime, guard: Callable[[], None],
    ) -> dict:
        if type(analysis_revision) is not int or analysis_revision < 1:
            raise ContractError("an explicitly allocated positive analysis revision is required")
        retained_until = utc(evidence_retained_until)
        if retained_until <= datetime.now(timezone.utc):
            raise ContractError("source evidence retention has expired")
        candidate = strict_json(self.evidence.read(candidate_reference))
        if "trigger" in candidate:
            from .investigations import validate_investigation
            validate_investigation(candidate)
        if (candidate.get("IsSynthetic") is not True or candidate.get("OperationalUse") != "prohibited"
                or candidate.get("execution_allowed") is not False
                or candidate["source"]["producer_key"] != producer_key
                or candidate["source"]["run_id"] != run_id):
            raise ContractError("candidate does not match its trusted non-operational workflow scope")
        by_id = {item["event_id"]: item for item in candidate["evidence_refs"]}
        if not by_id or len(by_id) != len(candidate["evidence_refs"]):
            raise ContractError("candidate evidence membership is empty or ambiguous")
        observed_ids = set()
        for observation in candidate["observations"]:
            event_id = observation["EventId"]
            if (event_id not in by_id or by_id[event_id]["source_sha256"] != digest(observation)
                    or observation["RunId"] != run_id
                    or observation["Domain"] != candidate["source"]["domain"]
                    or observation["Surface"] not in candidate["source"]["surfaces"]
                    or observation["EntityId"] != candidate["target"]["entity_id"]
                    or observation["LocationId"] != candidate["target"]["location_id"]):
                raise ContractError("candidate source projection does not match its evidence/scope")
            observed_ids.add(event_id)
        if observed_ids != set(by_id):
            raise ContractError("candidate has missing source projections")
        context = WorkflowContext(workflow_id, candidate)
        self.ledger.open_workflow(workflow_id)
        engine: AgentEngine

        def guarded():
            guard()
            if datetime.now(timezone.utc) >= retained_until:
                raise AgentFailure("source evidence expired during reasoning")
            self._guard_knowledge(context)

        def dispatch(agent_name, function_name, arguments):
            if function_name in DELEGATIONS:
                if agent_name != "ops-orchestrator" or arguments:
                    raise AgentFailure("delegation is restricted to the actual coordinator")
                completed = [name for name in SPECIALIST_ORDER if name in context.stages]
                expected = SPECIALIST_ORDER[len(completed)] if len(completed) < 4 else None
                target = DELEGATIONS[function_name]
                if target != expected:
                    raise AgentFailure("coordinator violated the required specialist order")
                if target == "incident-comms" and context.gate_receipt is None:
                    raise AgentFailure("communications require the exact finalized planner gate receipt")
                output = engine.invoke(target, context.model_input())
                context.stages[target] = output
                if target == "remediation-planner":
                    guarded()
                    context.gate_receipt = self._gate(context)
                return output
            return self.backend(agent_name, function_name, arguments, context)

        engine = AgentEngine(self.agents, self.transport, self.ledger, workflow_id,
                             dispatch, lambda name, value: self._validate(name, value, context), guarded,
                             continuation_context=lambda: {
                                 "gate_receipt": context.gate_receipt,
                                 "coordinator_progress": context.coordinator_progress(),
                             },
                             citation_scope=lambda: context.citation_ids)
        for name in ("industry-signal-pack", "triage-dedup"):
            context.stages[name] = engine.invoke(name, context.model_input())
        context.stages["ops-orchestrator"] = engine.invoke("ops-orchestrator", context.model_input())
        guarded()
        completed = all(value["status"] == "completed" for value in context.stages.values())
        header = {
            "schema_version": "1", "result_id": workflow_id, "workflow_id": workflow_id,
            "incident_id": candidate["incident_id"], "analysis_revision": analysis_revision,
            "status": "completed" if completed else "incomplete", "source": candidate["source"],
            "evidence_refs": [{
                "evidence_id": item["event_id"], "sha256": item["source_sha256"],
                "retained_until_utc": iso(retained_until), "verified": True,
            } for item in candidate["evidence_refs"]],
            "stage_results": {
                name: {"status": ("completed" if output["status"] == "completed" else "incomplete"),
                       "output": output, "agent_version": self.agents[name].reference.version,
                       "evidence_refs": [ref["source_id"] for ref in output["citations"]]}
                for name, output in context.stages.items()
            },
            "versions": {**{name: agent.reference.version for name, agent in self.agents.items()},
                         "gate_policy_sha256": self.policy_hash,
                         "budget_policy_sha256": self.ledger.limits.sha256,
                         "portfolio_sha256": self.portfolio_hash},
            "created_at_utc": iso(datetime.now(timezone.utc)),
            "observed_lifecycle": candidate["observed_lifecycle"],
            "summary": context.stages["ops-orchestrator"]["summary"],
            "proposal": context.stages["remediation-planner"]["plan"], "gate_receipt": context.gate_receipt,
            "coverage": {"candidate_evidence": {
                "key": candidate_reference.key, "sha256": candidate_reference.sha256,
                "byte_length": candidate_reference.byte_length},
                "provider_receipts": engine.receipts, "suppression_applied": False},
            "knowledge_dependencies": sorted({item["publication_id"] for item in context.knowledge.values()}),
            "IsSynthetic": True, "OperationalUse": "prohibited", "execution_allowed": False,
        }
        if "trigger" in candidate:
            header["coverage"]["trigger"] = candidate["trigger"]
            header["coverage"]["analysis_authorship"] = {"kind": "ai"}
            header["coverage"]["detected_incident"] = False
        for name in ("postmortem-drafter", "alert-hygiene"):
            header["stage_results"][name] = {"status": "not-triggered", "triggered": False}
        for document_id, document in context.knowledge.items():
            expiry = min(retained_until, utc(document["retained_until_utc"]))
            header["evidence_refs"].append({
                "evidence_id": document_id, "sha256": document["sha256"],
                "retained_until_utc": iso(expiry), "verified": True,
            })
        return header

    def run_lifecycle_stage(
        self, name: str, workflow_id: str, payload: dict, known_citations: set[str],
        *, guard: Callable[[], None], candidate: dict | None = None,
        receipt_sink: Callable[[dict], None] | None = None,
    ) -> dict:
        if name not in ("postmortem-drafter", "alert-hygiene"):
            raise ContractError("only the two separately triggered lifecycle agents belong here")
        if (payload.get("IsSynthetic") is not True or payload.get("OperationalUse") != "prohibited"
                or payload.get("execution_allowed") is not False):
            raise ContractError("lifecycle work must preserve its trusted non-operation overlay")
        source = payload.get("source")
        if (not isinstance(source, dict) or not isinstance(source.get("producer_key"), str)
                or not source["producer_key"] or not isinstance(source.get("run_id"), str)
                or not source["run_id"] or not known_citations):
            raise ContractError("lifecycle work requires ledger-bound source scope and evidence")
        if name == "postmortem-drafter" and payload.get("review_state") != "closed":
            raise ContractError("postmortem drafting requires a verified closed review revision")
        self.ledger.open_workflow(workflow_id)
        if candidate is not None and (
                candidate.get("source") != source
                or {item["event_id"] for item in candidate["evidence_refs"]} - known_citations):
            raise ContractError("lifecycle candidate differs from its authoritative source/evidence")
        context = WorkflowContext(
            workflow_id, candidate or {"source": source, "evidence_refs": [{"event_id": item} for item in known_citations]},
            trigger_payload=payload,
        )

        def validate(agent_name, value):
            self._validate(agent_name, value, context)
            if (agent_name == "postmortem-drafter"
                    and value["closure_basis"] != payload["closure_basis"]):
                raise AgentFailure("postmortem changed the trusted closure basis")
            if (agent_name == "alert-hygiene" and not payload.get("savings_estimate_supported", False)
                    and value["estimated_volume_reduction_pct"] is not None):
                raise AgentFailure("hygiene invented a quantitative savings estimate")

        def guarded():
            guard()
            self._guard_knowledge(context)

        engine = AgentEngine(
            self.agents, self.transport, self.ledger, workflow_id,
            lambda agent, function, arguments: self.backend(agent, function, arguments, context),
            validate, guarded, citation_scope=lambda: context.citation_ids,
        )
        output = engine.invoke(name, payload)
        guarded()
        if receipt_sink is not None:
            receipt_sink({"provider_receipts": engine.receipts, "knowledge": context.knowledge})
        return output
