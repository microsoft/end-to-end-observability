"""Ops Orchestrator -- agent #0, plus the local demo harness.

Runs the full incident loop:

    raw alerts -> normalize -> correlate -> retrieve knowledge
               -> investigate -> plan -> SAFETY GATE -> comms

The code skills (normalizer, safety gate) always execute for real. The
reasoning steps go through a pluggable ``Reasoner``:

* ``StubReasoner``    -- deterministic, no model, no network. Default.
                         Makes the demo runnable before Foundry exists.
* ``FoundryReasoner`` -- Azure AI Foundry Agent Service.

The point of the seam is that the governance behaviour is identical
either way. The gate is not part of the reasoning.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, Sequence

from .envelope import utcnow_iso
from .normalizer import Incident, normalize_and_correlate
from .safety_gate import ChangeRequest, ChangeSafetyGate, Decision
from .tools import get_recent_changes, get_resource_context, run_kql, search_knowledge

AGENTS_DIR = Path(__file__).resolve().parents[1] / "agents"


# --- reasoning seam ---------------------------------------------------


class Reasoner(Protocol):
    """Anything that can execute a named prompt-hosted agent."""

    def run(self, agent: str, context: str, payload: dict[str, Any]) -> dict[str, Any]:
        ...


class StubReasoner:
    """Deterministic stand-in for the prompt-hosted agents.

    Produces structurally correct output with no model call, so the loop
    and the governance behaviour can be demonstrated and tested offline.
    It does *not* pretend to be intelligent -- hypotheses come from the
    evidence actually retrieved, not from invention.
    """

    name = "stub"

    def run(self, agent: str, context: str, payload: dict[str, Any]) -> dict[str, Any]:
        handler = getattr(self, f"_{agent.replace('-', '_')}", None)
        if handler is None:
            return {"agent": agent, "output": f"[stub] no handler for agent '{agent}'"}
        return handler(context, payload)

    def _rca_investigator(self, context: str, payload: dict[str, Any]) -> dict[str, Any]:
        evidence: list[dict[str, Any]] = payload.get("evidence", [])
        changes = payload.get("changes", {}).get("changes", [])
        knowledge = payload.get("knowledge", {}).get("results", [])
        symptom = payload.get("symptom_class", "unknown")

        hypotheses: list[dict[str, Any]] = []

        if changes:
            top = changes[0]
            hypotheses.append(
                {
                    "hypothesis": (
                        f"Recent change '{top.get('operation', 'unknown')}' at "
                        f"{top.get('timestamp', 'unknown')} altered behaviour"
                    ),
                    "confidence": "high",
                    "evidence": [
                        f"change by {top.get('caller', 'unknown')}",
                        top.get("correlation_hint", ""),
                    ],
                    "next_check": "compare error rate before and after the change timestamp",
                }
            )

        if knowledge:
            best = knowledge[0]
            hypotheses.append(
                {
                    "hypothesis": f"Known pattern documented in '{best.get('title')}'",
                    "confidence": "medium" if best.get("score", 0) > 3 else "low",
                    "evidence": [f"{best.get('kind')} {best.get('doc_id')} matched the symptom"],
                    "next_check": "confirm the documented preconditions hold",
                }
            )

        for item in evidence:
            if item.get("row_count"):
                hypotheses.append(
                    {
                        "hypothesis": f"Telemetry supports a {symptom} condition",
                        "confidence": "medium",
                        "evidence": [f"{item['row_count']} rows from the {item.get('intent')} query"],
                        "next_check": "widen the window to establish whether this is novel",
                    }
                )
                break

        if not hypotheses:
            hypotheses.append(
                {
                    "hypothesis": "Insufficient evidence retrieved to form a hypothesis",
                    "confidence": "none",
                    "evidence": [],
                    "next_check": "verify tool connectivity and widen the query timespan",
                }
            )

        return {"agent": "rca-investigator", "reasoner": self.name, "hypotheses": hypotheses}

    def _remediation_planner(self, context: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Propose the action the highest-ranked runbook actually prescribes.

        Postmortems are skipped: they describe what someone did once, not
        what the current documented procedure is.
        """
        knowledge = payload.get("knowledge", {}).get("results", [])
        for result in knowledge:
            if result.get("kind") != "runbook":
                continue
            actions = result.get("documented_actions") or []
            if actions:
                return {
                    "agent": "remediation-planner",
                    "reasoner": self.name,
                    "proposed_action": actions[0],
                    "rationale": (
                        f"prescribed by {result.get('doc_id')} "
                        f"(match score {result.get('score')})"
                    ),
                    "source_runbook": result.get("doc_id"),
                    "rollback": "see the rollback section of the source runbook",
                }
        return {
            "agent": "remediation-planner",
            "reasoner": self.name,
            "proposed_action": "annotate_incident",
            "rationale": "no runbook prescribed an action; defaulting to the lowest-impact option",
            "source_runbook": "",
            "rollback": "not applicable",
        }

    def _incident_comms(self, context: str, payload: dict[str, Any]) -> dict[str, Any]:
        incident = payload.get("incident", {})
        decision: Decision | None = payload.get("decision")
        status = decision.effect.value if decision else "pending"
        return {
            "agent": "incident-comms",
            "reasoner": self.name,
            "engineer_update": (
                f"{incident.get('severity', 'unknown').upper()} on "
                f"{incident.get('service', 'unknown')}: {incident.get('title', '')}. "
                f"{incident.get('signal_count', 0)} signal(s) from "
                f"{', '.join(incident.get('sources', []))}. "
                f"Remediation status: {status}."
            ),
            "executive_update": (
                f"We are investigating a {incident.get('severity', 'unknown')} issue "
                f"affecting {incident.get('service', 'unknown')}. "
                f"No customer-facing impact confirmed yet. Next update in 30 minutes."
            ),
        }


def _first_action_in(text: str) -> str:
    """Deprecated. Superseded by ``documented_actions`` from the knowledge
    tool, which reads prescribed commands from code blocks rather than
    guessing from prose. Retained only so external callers do not break.
    """
    verbs = (
        "restart", "scale_out", "scale_in", "failover", "drain_node",
        "clear_cache", "recycle_app_pool", "reprocess_queue",
    )
    lowered = (text or "").lower()
    for verb in verbs:
        if verb in lowered:
            return verb
    return ""


class FoundryReasoner:
    """Calls agents deployed in an Azure AI Foundry project.

    Uses the **Responses API** with an ``agent_reference``, which is the
    surface the current Foundry project resource exposes. The older
    Assistants/threads API returns 404 against these projects, and the
    preview ``azure-ai-agents`` operations have moved between releases --
    the OpenAI-compatible Responses path is the stable one.

    Agent names come from ``.foundry-agents.json``, written by
    ``scripts/deploy_to_foundry.py``.
    """

    name = "foundry"

    def __init__(self, endpoint: str | None = None, state_path: Path | None = None) -> None:
        state_file = state_path or (Path(__file__).resolve().parents[1] / ".foundry-agents.json")
        state: dict[str, Any] = {}
        if state_file.exists():
            state = json.loads(state_file.read_text(encoding="utf-8"))

        self.endpoint = (
            endpoint
            or state.get("endpoint")
            or os.environ.get("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")
        )
        if not self.endpoint:
            raise RuntimeError(
                "no Foundry endpoint. Deploy with scripts/deploy_to_foundry.py or set "
                "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT."
            )
        self.agents: dict[str, str] = state.get("agents", {})
        self._client = None

    def _get_client(self):
        if self._client is None:
            from azure.ai.projects import AIProjectClient  # noqa: PLC0415
            from azure.identity import DefaultAzureCredential  # noqa: PLC0415

            project = AIProjectClient(endpoint=self.endpoint, credential=DefaultAzureCredential())
            self._client = project.get_openai_client()
        return self._client

    def run(self, agent: str, context: str, payload: dict[str, Any]) -> dict[str, Any]:
        agent_name = self.agents.get(agent, agent)
        client = self._get_client()

        message = (
            f"{context}\n\nStructured payload (JSON):\n"
            f"{json.dumps(payload, default=str)[:6000]}"
        )
        try:
            response = client.responses.create(
                input=message,
                extra_body={"agent_reference": {"type": "agent_reference", "name": agent_name}},
            )
        except Exception as exc:
            return {"agent": agent, "reasoner": self.name, "error": str(exc)[:400]}

        text = response.output_text or ""
        return {"agent": agent, "reasoner": self.name, "output": text, **_try_json(text)}


def _try_json(text: str) -> dict[str, Any]:
    """Agents are instructed to emit JSON; tolerate it when they do not."""
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        stripped = stripped.split("\n", 1)[-1] if "\n" in stripped else stripped
    try:
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, AttributeError):
        return {}


# --- the loop ---------------------------------------------------------


@dataclass
class LoopResult:
    incident: dict[str, Any]
    knowledge: dict[str, Any] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    changes: dict[str, Any] = field(default_factory=dict)
    rca: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)
    decision: dict[str, Any] = field(default_factory=dict)
    comms: dict[str, Any] = field(default_factory=dict)
    executed: bool = False
    completed_at: str = field(default_factory=utcnow_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident": self.incident,
            "knowledge": self.knowledge,
            "evidence": self.evidence,
            "changes": self.changes,
            "rca": self.rca,
            "plan": self.plan,
            "decision": self.decision,
            "comms": self.comms,
            "executed": self.executed,
            "completed_at": self.completed_at,
        }


class OpsOrchestrator:
    """Agent #0. Delegates to specialists and enforces the human gate."""

    def __init__(
        self,
        reasoner: Reasoner | None = None,
        gate: ChangeSafetyGate | None = None,
        evaluate_at: "datetime | None" = None,
    ) -> None:
        self.reasoner = reasoner or StubReasoner()
        self.gate = gate or ChangeSafetyGate()
        # Pinning evaluation time makes demos and tests reproducible.
        # In production this stays None so the gate sees wall clock --
        # a change freeze has to be evaluated at execution time, not at
        # the time the alert happened to fire.
        self.evaluate_at = evaluate_at

    def handle(self, incident: Incident) -> LoopResult:
        primary = incident.primary
        context = incident.agent_context()
        result = LoopResult(incident=incident.to_dict())

        # 1. institutional memory first. Cheapest call, and it often
        #    short-circuits the investigation entirely.
        result.knowledge = search_knowledge(
            f"{primary.title} {primary.symptom_class.value} {primary.service} "
            f"{primary.resource.type}",
            top_k=3,
        )

        # 2. telemetry and topology
        result.evidence = [
            run_kql(_kql_for(primary.symptom_class.value, primary.resource.name)),
        ]
        resource_context = get_resource_context(primary.resource.id or primary.resource.name)
        if resource_context.get("found"):
            result.evidence.append({"resource_context": resource_context, "row_count": 1})

        # 3. what changed
        result.changes = get_recent_changes(
            resource_id=primary.resource.id, service=primary.service, hours=24
        )

        # 4. investigate
        result.rca = self.reasoner.run(
            "rca-investigator",
            context,
            {
                "symptom_class": primary.symptom_class.value,
                "evidence": result.evidence,
                "changes": result.changes,
                "knowledge": result.knowledge,
            },
        )

        # 5. plan
        result.plan = self.reasoner.run(
            "remediation-planner",
            context,
            {"knowledge": result.knowledge, "rca": result.rca},
        )

        # 6. THE GATE. Code, always. The planner does not get a vote.
        #
        # Coerce a missing or null action to the lowest-impact verb. A
        # live agent can return the key absent or explicitly null, and
        # letting that reach the gate as the string "None" would produce
        # a denial for the wrong reason -- which reads as the gate
        # working when actually the planner failed.
        proposed = result.plan.get("proposed_action") or "annotate_incident"
        if result.plan.get("proposed_action") != proposed:
            # Record the coercion so the console, the gate and the audit
            # record all agree on what was actually submitted. Leaving
            # the plan showing null while the gate evaluated something
            # else is exactly the kind of divergence that makes an
            # incident record untrustworthy after the fact.
            result.plan["proposed_action"] = proposed
            result.plan["coerced"] = (
                "planner returned no action; defaulted to the lowest-impact verb"
            )
        request = ChangeRequest(
            action=str(proposed),
            resource_id=primary.resource.id,
            environment=primary.environment,
            service=primary.service,
            blast_radius_scope=primary.blast_radius.scope,
            estimated_affected=primary.blast_radius.estimated_affected,
            incident_fingerprint=incident.fingerprint,
            justification=str(result.plan.get("rationale", "")),
            at=self.evaluate_at,
        )
        decision = self.gate.evaluate(request)
        result.decision = decision.to_dict()

        # 7. communicate
        result.comms = self.reasoner.run(
            "incident-comms",
            context,
            {"incident": result.incident, "decision": decision},
        )

        # Execution is never automatic in this harness. Even an ALLOW
        # decision only marks the action as ready to hand to Azure SRE
        # Agent; a human still initiates. See docs/governance.md.
        result.executed = False
        return result


def _kql_for(symptom_class: str, resource_name: str) -> str:
    """Pick a starting query for the symptom. The agent refines from here."""
    templates = {
        "saturation": (
            'InsightsMetrics | where Computer has "{r}" | where Name == "Percentage CPU" '
            "| summarize avg(Val) by bin(TimeGenerated, 5m)"
        ),
        "availability": (
            'Heartbeat | where Computer has "{r}" '
            "| summarize LastSeen = max(TimeGenerated) by Computer"
        ),
        "error_rate": (
            'AppRequests | where Name has "{r}" | where ResultCode startswith "5" '
            "| summarize count() by bin(TimeGenerated, 5m), ResultCode"
        ),
        "latency": (
            'AppRequests | where Name has "{r}" '
            "| summarize percentile(DurationMs, 95) by bin(TimeGenerated, 5m)"
        ),
        "connectivity": (
            'NetworkMonitoring | where Computer has "{r}" '
            "| summarize avg(ResponseTimeMs) by bin(TimeGenerated, 5m)"
        ),
        "capacity": (
            'Perf | where Computer has "{r}" | where CounterName == "% Free Space" '
            "| summarize min(CounterValue) by bin(TimeGenerated, 15m)"
        ),
        "data_quality": (
            'IoTTelemetry | where DeviceId has "{r}" '
            "| summarize count() by bin(TimeGenerated, 5m)"
        ),
    }
    template = templates.get(symptom_class, 'search "{r}" | take 50')
    return template.format(r=resource_name or "*")


def run_demo(
    payloads: Sequence[dict[str, Any]],
    reasoner: Reasoner | None = None,
    gate: ChangeSafetyGate | None = None,
    limit: int = 1,
    evaluate_at: datetime | None = None,
) -> list[LoopResult]:
    """Normalize a batch and run the loop over the top incidents."""
    incidents, _failures = normalize_and_correlate(payloads)
    orchestrator = OpsOrchestrator(reasoner=reasoner, gate=gate, evaluate_at=evaluate_at)
    return [orchestrator.handle(incident) for incident in incidents[:limit]]
