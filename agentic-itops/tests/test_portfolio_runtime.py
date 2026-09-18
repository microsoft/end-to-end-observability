from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

pytest.importorskip("jsonschema", reason="install requirements-consumer.txt")
pytest.importorskip("httpx", reason="install requirements-consumer.txt")
pytest.importorskip("flask", reason="install requirements-consumer.txt")

from agentic_itops.consumer.budget import BudgetLedger
from agentic_itops.consumer.contracts import ContractError
from agentic_itops.consumer.responses import AgentFailure, RuntimeAgent
from agentic_itops.consumer.storage_contract import EvidenceRef
from agentic_itops.consumer.processing import process_snapshot
from agentic_itops.portfolio_schemas import output_schema
from agentic_itops.review import SQLiteReviewRepository
from agentic_itops.runtime import AGENTS, DELEGATIONS, PortfolioRuntime, SPECIALIST_ORDER, WorkflowContext
from tests.test_consumer_budget import LIMITS
from tests.test_consumer_processing import setup_batches
from tests.test_consumer_responses import EMPTY


def example(schema):
    if "enum" in schema:
        return schema["enum"][0]
    if "anyOf" in schema:
        return example(schema["anyOf"][-1])
    kind = schema["type"]
    if isinstance(kind, list):
        return None
    if kind == "object":
        return {key: example(value) for key, value in schema["properties"].items()}
    if kind == "array":
        return [example(schema["items"]) for _ in range(schema.get("minItems", 0))]
    return {"string": "Evidence-backed test draft", "integer": 1, "number": 0,
            "boolean": False, "null": None}[kind]


def setup(tmp_path, *, wrong_order=False, bad_target=False, action_plan=False,
          fabricated_knowledge=False, fabricated_event_revision=False, coordinator_stages=4):
    evidence, refs, _, scope, policy = setup_batches(tmp_path)
    report = process_snapshot(scope, refs, evidence, [policy])
    candidate_ref = EvidenceRef(**report["candidates"][0]["evidence"])
    candidate = json.loads(evidence.read(candidate_ref))
    citation = candidate["evidence_refs"][0]["event_id"]
    agents = {
        name: RuntimeAgent.create(
            name, str(index + 1), output_schema(name),
            {function: EMPTY for function in DELEGATIONS} if name == "ops-orchestrator" else {},
            {"portfolio_sha256": "a" * 64},
            function_outputs={function: output_schema(target) for function, target in DELEGATIONS.items()}
            if name == "ops-orchestrator" else {},
        )
        for index, name in enumerate(AGENTS)
    }
    path = tmp_path / "runtime-budget.db"
    ledger = BudgetLedger(lambda: sqlite3.connect(path),
                          replace(LIMITS, monthly_nanodollars=100000, api_calls=32, tool_calls=32),
                          "unit-project", dialect="sqlite-local-test")
    ledger.migrate()

    class Transport:
        def __init__(self):
            self.calls = []
            self.request_schemas = []
            self.citation_scopes = []
            self.coordinator_states = []
            self.root_turn = 0
        def respond(self, agent, history, max_output_tokens, timeout_seconds):
            name = agent.reference.name
            self.calls.append((name, agent.reference.version))
            self.request_schemas.append(agent.definition["output_schema"])
            contexts = [json.loads(item["content"]) for item in history if item.get("role") == "user"]
            if name == "ops-orchestrator":
                state = next((item["trusted_runtime_state"] for item in reversed(contexts)
                              if "trusted_runtime_state" in item), contexts[0]["context"])
                self.coordinator_states.append(state)
            self.citation_scopes.append(next(item["trusted_citation_ids"] for item in reversed(contexts)
                                            if "trusted_citation_ids" in item))
            response = {"id": f"resp-{len(self.calls)}", "status": "completed",
                        "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20}}
            if name == "ops-orchestrator" and self.root_turn < coordinator_stages:
                target = ("rca-investigator" if wrong_order and self.root_turn == 0
                          else SPECIALIST_ORDER[self.root_turn])
                self.root_turn += 1
                response["output"] = [{
                    "type": "function_call", "name": "delegate_" + target.replace("-", "_"),
                    "call_id": f"delegate_{self.root_turn}", "arguments": "{}",
                }]
            else:
                value = example(output_schema(name))
                value["citations"] = [{"source_id": citation, "revision": None, "locator": None}]
                if name == "runbook-memory" and fabricated_knowledge:
                    document = example(output_schema(name)["properties"]["runbooks"]["items"])
                    document.update(doc_id=citation, revision=candidate["evidence_refs"][0]["source_sha256"])
                    value["runbooks"] = [document]
                    value["citations"][0]["revision"] = document["revision"]
                if name == "runbook-memory" and fabricated_event_revision:
                    value["citations"][0]["revision"] = "fabricated-approved-revision"
                if name in ("ops-orchestrator", "triage-dedup"):
                    value["incident_fingerprint"] = candidate["incident_fingerprint"]
                if name == "ops-orchestrator":
                    value.update(proposed_action="annotate_incident" if action_plan else "none",
                                 gate_decision="deny" if action_plan else "not_evaluated")
                if name == "remediation-planner" and (bad_target or action_plan):
                    value["plan"] = example(output_schema(name)["properties"]["plan"]["anyOf"][0])
                    value["plan"].update(proposed_action="annotate_incident", source_runbook=None,
                                         target_resource_id=("/subscriptions/invented/resource" if bad_target
                                                             else candidate["target"]["id"]))
                if name == "postmortem-drafter":
                    value["closure_basis"] = "administrative_closure"
                response["output"] = [{"type": "message", "role": "assistant",
                                       "content": [{"type": "output_text", "text": json.dumps(value)}]}]
            return response

    transport = Transport()
    def no_unbound_backend(*args):
        raise AssertionError("test agents must not request unbound backend operations")
    runtime = PortfolioRuntime(agents, transport, ledger, evidence, {}, no_unbound_backend)
    return runtime, transport, candidate_ref, scope, citation


def analyze(runtime, reference, scope, **changes):
    arguments = {
        "producer_key": scope.producer.key, "run_id": scope.run_id, "analysis_revision": 1,
        "evidence_retained_until": datetime.now(timezone.utc) + timedelta(days=1),
        "guard": lambda: None,
    }
    arguments.update(changes)
    return runtime.analyze("workflow-incident", reference, **arguments)


def test_actual_coordinator_drives_four_specialists_and_registers_reviewable_header(tmp_path):
    runtime, transport, reference, scope, _ = setup(tmp_path)
    header = analyze(runtime, reference, scope)
    names = [name for name, _ in transport.calls]
    assert names[:2] == ["industry-signal-pack", "triage-dedup"]
    assert [name for name in names if name in SPECIALIST_ORDER] == list(SPECIALIST_ORDER)
    assert names.count("ops-orchestrator") == 5
    assert header["gate_receipt"]["effect"] == "not_evaluated"
    assert header["gate_receipt"]["reason"] == "no_action_proposed"
    assert header["execution_allowed"] is False
    assert header["stage_results"]["postmortem-drafter"]["status"] == "not-triggered"
    candidate = json.loads(runtime.evidence.read(reference))
    expected = sorted(item["event_id"] for item in candidate["evidence_refs"])
    assert all(members == expected for members in transport.citation_scopes)
    assert all("enum" not in schema["properties"]["citations"]["items"]["properties"]["source_id"]
               for schema in transport.request_schemas)
    assert all("enum" not in agent.definition["output_schema"]["properties"]["citations"]["items"]["properties"]["source_id"]
               for agent in runtime.agents.values())
    repository = SQLiteReviewRepository()
    registered = repository.register_result(header)
    assert registered["result"] == header
    repository.close()


@pytest.mark.parametrize("action_plan", [False, True])
def test_coordinator_receives_current_required_delegate_and_finalization_state(tmp_path, action_plan):
    runtime, transport, reference, scope, _ = setup(tmp_path, action_plan=action_plan)
    header = analyze(runtime, reference, scope)
    assert len(transport.coordinator_states) == 5
    for index, state in enumerate(transport.coordinator_states):
        progress = state["coordinator_progress"]
        remaining = list(SPECIALIST_ORDER[index:])
        assert progress["validated_specialist_stages"] == list(SPECIALIST_ORDER[:index])
        assert progress["remaining_specialist_stages"] == remaining
        assert progress["next_required_delegate"] == (
            "delegate_" + remaining[0].replace("-", "_") if remaining else None)
        assert progress["gate_receipt_present"] is (index >= 3)
        assert progress["final_output_allowed"] is (index == 4)
        assert state["gate_receipt"] == (header["gate_receipt"] if index >= 3 else None)


@pytest.mark.parametrize("coordinator_stages", range(4))
def test_premature_coordinator_finalization_still_fails_without_retry(tmp_path, coordinator_stages):
    runtime, transport, reference, scope, _ = setup(tmp_path, coordinator_stages=coordinator_stages)
    missing = ",".join(SPECIALIST_ORDER[coordinator_stages:])
    with pytest.raises(AgentFailure, match=(
        f"coordinator finished before all specialist/gate stages: missing_stages={missing}; "
        f"gate_receipt_present={coordinator_stages >= 3}"
    )):
        analyze(runtime, reference, scope)
    assert transport.root_turn == coordinator_stages
    assert sum(name == "ops-orchestrator" for name, _ in transport.calls) == coordinator_stages + 1


def test_coordinator_progress_requires_gate_even_when_all_specialists_are_present():
    context = WorkflowContext("unit-progress", {}, stages={name: {} for name in SPECIALIST_ORDER})
    progress = context.coordinator_progress()
    assert progress["remaining_specialist_stages"] == []
    assert progress["next_required_delegate"] is None
    assert progress["gate_receipt_present"] is False
    assert progress["final_output_allowed"] is False


def test_all_nine_roles_run_only_across_their_distinct_lifecycle_triggers(tmp_path):
    runtime, transport, reference, scope, citation = setup(tmp_path)
    header = analyze(runtime, reference, scope)
    safety = {"IsSynthetic": True, "OperationalUse": "prohibited", "execution_allowed": False,
              "source": header["source"]}
    postmortem = runtime.run_lifecycle_stage(
        "postmortem-drafter", "workflow-postmortem",
        {**safety, "review_state": "closed", "closure_basis": "administrative_closure"},
        {citation}, guard=lambda: None)
    hygiene = runtime.run_lifecycle_stage(
        "alert-hygiene", "workflow-hygiene", {**safety, "savings_estimate_supported": False},
        {citation}, guard=lambda: None)
    assert set(name for name, _ in transport.calls) == set(AGENTS)
    assert postmortem["executed_action"] == "none"
    assert hygiene["estimated_volume_reduction_pct"] is None
    assert all(version == runtime.agents[name].reference.version for name, version in transport.calls)
    assert transport.citation_scopes[-2:] == [[citation], [citation]]


@pytest.mark.parametrize("option", ["wrong_order", "bad_target"])
def test_coordinator_order_and_planner_target_cannot_be_substituted(tmp_path, option):
    runtime, _, reference, scope, _ = setup(tmp_path, **{option: True})
    with pytest.raises(AgentFailure):
        analyze(runtime, reference, scope)


def test_wrong_source_or_expired_evidence_never_invokes_a_model(tmp_path):
    runtime, transport, reference, scope, _ = setup(tmp_path)
    with pytest.raises(ContractError):
        analyze(runtime, reference, scope, run_id="someone-else")
    with pytest.raises(ContractError):
        analyze(runtime, reference, scope,
                evidence_retained_until=datetime.now(timezone.utc) - timedelta(seconds=1))
    assert transport.calls == []


def test_exact_finalized_proposal_is_denied_and_bound_before_communications(tmp_path):
    from agentic_itops.consumer.contracts import digest
    runtime, _, reference, scope, _ = setup(tmp_path, action_plan=True)
    header = analyze(runtime, reference, scope)
    receipt = header["gate_receipt"]
    assert receipt["effect"] == "deny"
    assert receipt["proposal_sha256"] == digest(header["proposal"])
    assert receipt["policy_sha256"] == runtime.policy_hash
    assert receipt["target"] == header["proposal"]["target_resource_id"]
    assert receipt["execution_allowed"] is False
    assert "synthetic_non_operation" in receipt["matched_rules"]


@pytest.mark.parametrize("option,message", [
    ("fabricated_knowledge", "not retrieved"),
    ("fabricated_event_revision", "observation citation"),
])
def test_observation_ids_cannot_masquerade_as_retrieved_knowledge(tmp_path, option, message):
    runtime, transport, reference, scope, _ = setup(tmp_path, **{option: True})
    with pytest.raises(AgentFailure, match=message):
        analyze(runtime, reference, scope)
    assert "rca-investigator" not in [name for name, _ in transport.calls]


def test_retrieved_document_identity_requires_and_accepts_its_eligibility_guard(tmp_path):
    runtime, _, reference, _, _ = setup(tmp_path)
    context = WorkflowContext("workflow", json.loads(runtime.evidence.read(reference)))
    context.knowledge["reviewed-book"] = {
        "revision": "v1", "publication_id": "publication-1", "sha256": "b" * 64,
        "retained_until_utc": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    }
    with pytest.raises(AgentFailure, match="eligibility"):
        runtime._guard_knowledge(context)
    checked = []
    runtime.knowledge_guard = lambda value: checked.append(value.workflow_id)
    runtime._guard_knowledge(context)
    value = example(output_schema("runbook-memory"))
    document = example(output_schema("runbook-memory")["properties"]["runbooks"]["items"])
    document.update(doc_id="reviewed-book", revision="v1")
    value["runbooks"] = [document]
    value["citations"] = [{"source_id": "reviewed-book", "revision": "v1", "locator": None}]
    runtime._validate("runbook-memory", value, context)
    assert checked == ["workflow"]


def test_retrieval_cannot_reclassify_a_raw_event_as_a_document(tmp_path):
    runtime, _, reference, _, citation = setup(tmp_path)
    context = WorkflowContext("workflow", json.loads(runtime.evidence.read(reference)))
    context.knowledge[citation] = {"revision": "v1"}
    with pytest.raises(AgentFailure, match="shadow"):
        runtime._guard_knowledge(context)
