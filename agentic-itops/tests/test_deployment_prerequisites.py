from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentic_itops import cli
from agentic_itops.normalizer import load_directory, normalize_and_correlate
from agentic_itops.orchestrator import FoundryReasoner, OpsOrchestrator, StubReasoner, run_demo
from agentic_itops.safety_gate import ChangeRequest, ChangeSafetyGate, Effect, load_policy

AT = datetime(2026, 9, 14, 13, 42, tzinfo=timezone.utc)
RESOURCE = "/subscriptions/test/resourceGroups/rg-app-test/providers/example/items/one"
DATA = Path(__file__).resolve().parents[1] / "data" / "alerts"


def request(**changes):
    return ChangeRequest(**{
        "action": "scale_out", "resource_id": RESOURCE, "environment": "prod",
        "estimated_affected": 1, "at": AT, **changes,
    })


def test_blank_cli_policy_never_loads_sample(tmp_path, capsys):
    policy = tmp_path / "empty.yaml"
    policy.write_text("", encoding="utf-8")
    result = cli.main(["gate", "--action", "scale_out", "--resource", RESOURCE,
                       "--policy", str(policy), "--at", AT.isoformat(), "--json"])
    assert result == 2
    assert json.loads(capsys.readouterr().out)["effect"] == "deny"


@pytest.mark.parametrize("scopes", [None, {}, {"allow": []}])
def test_missing_resource_allowlist_denies(scopes):
    policy = load_policy()
    if scopes is None:
        policy.pop("resource_scopes")
    else:
        policy["resource_scopes"] = scopes
    decision = ChangeSafetyGate(policy).evaluate(request())
    assert decision.effect is Effect.DENY
    assert "resource_scopes.allow" in decision.matched_rules


@pytest.mark.parametrize("count", [-1, 0, True, "1", 1.5, None])
def test_invalid_affected_count_denies(count):
    decision = ChangeSafetyGate().evaluate(request(estimated_affected=count))
    assert decision.effect is Effect.DENY
    assert decision.matched_rules == ["invalid_request"]


@pytest.mark.parametrize("provenance", [
    {"is_synthetic": True}, {"operational_use": "prohibited"},
    {"is_synthetic": True, "dry_run": True},
])
def test_synthetic_prohibition_precedes_policy_and_dry_run(provenance):
    decision = ChangeSafetyGate().evaluate(request(**provenance))
    assert decision.effect is Effect.DENY
    assert decision.matched_rules == ["synthetic_non_operation"]


@pytest.mark.parametrize("policy", [
    [], False, {"read_only": "false"}, {"allowed_actions": "scale_out"},
    {"resource_scopes": {"allow": "*"}}, {"blast_radius": {"hard_deny_above": True}},
    {"blast_radius": {"max_affected_auto": 10, "hard_deny_above": 5}},
    {"change_freezes": [{"start": "bad", "end": "also bad"}]},
    {"change_freezes": [{"start": "2026-01-01T00:00:00+00:00"}]},
    {"change_freezes": [{"days": ["noday"]}]},
    {"change_freezes": [{"start_time": "bad", "end_time": "20:00"}]},
])
def test_invalid_policy_rejected_explicitly(policy):
    with pytest.raises(ValueError):
        ChangeSafetyGate(policy)


def test_audit_binds_policy_and_timestamp():
    policy = load_policy()
    changed = deepcopy(policy)
    changed["version"] = "new"
    original = ChangeSafetyGate(policy).evaluate(request()).audit_id
    assert original != ChangeSafetyGate(changed).evaluate(request()).audit_id
    assert original != ChangeSafetyGate(policy).evaluate(
        request(at=AT.replace(minute=43))).audit_id


@pytest.fixture
def incident():
    incidents, failures = normalize_and_correlate(load_directory(DATA))
    assert not failures
    return next(item for item in incidents if item.primary.resource.name == "vm-pump-ctrl-07")


class ChangedPlanner(StubReasoner):
    def __init__(self, changes):
        self.changes = changes
        self.comms_called = False

    def _remediation_planner(self, context, payload):
        return {**super()._remediation_planner(context, payload), **self.changes}

    def _incident_comms(self, context, payload):
        self.comms_called = True
        return super()._incident_comms(context, payload)


def test_gate_uses_proposed_radius_not_original_incident(incident):
    result = OpsOrchestrator(
        reasoner=ChangedPlanner({
            "blast_radius": {"scope": "service", "estimated_affected": 40},
        }), evaluate_at=AT,
    ).handle(incident)
    assert result.decision["effect"] == "deny"
    assert any("40" in reason or "10 or more" in reason for reason in result.decision["reasons"])
    assert not result.executed


def test_different_planner_target_is_not_gated_as_original(incident):
    reasoner = ChangedPlanner({"target_resource_id": RESOURCE})
    with pytest.raises(ValueError, match="authorized target"):
        OpsOrchestrator(reasoner=reasoner, evaluate_at=AT).handle(incident)
    assert not reasoner.comms_called


@pytest.mark.parametrize("changes", [
    {"proposed_action": None}, {"target_resource_id": ""},
    {"blast_radius": {}}, {"blast_radius": {"scope": "resource", "estimated_affected": -1}},
    {"error": "model unavailable"},
])
def test_bad_plans_cannot_become_annotations_or_comms(incident, changes):
    reasoner = ChangedPlanner(changes)
    with pytest.raises((ValueError, RuntimeError)):
        OpsOrchestrator(reasoner=reasoner, evaluate_at=AT).handle(incident)
    assert not reasoner.comms_called


def test_demo_rejects_partial_normalization_and_empty_input():
    with pytest.raises(ValueError, match="failed normalization"):
        run_demo([{"unsupported": True}])
    with pytest.raises(ValueError, match="no incidents"):
        run_demo([])


@pytest.mark.parametrize("foundry,expected", [(False, AT), (True, None)])
def test_default_demo_clock_depends_on_mode(monkeypatch, capsys, foundry, expected):
    captured = []
    monkeypatch.setattr(cli, "FoundryReasoner", StubReasoner)
    monkeypatch.setattr(cli, "run_demo",
                        lambda *args, **kwargs: captured.append(kwargs["evaluate_at"]) or [])
    argv = ["demo", "--json"] + (["--foundry"] if foundry else [])
    assert cli.main(argv) == 0
    assert captured == [expected]


def test_foundry_cli_rejects_caller_selected_gate_time(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["demo", "--foundry", "--at", AT.isoformat()])
    assert exc.value.code == 2
    assert "offline-only" in capsys.readouterr().err


def fake_foundry(monkeypatch, text, status="completed"):
    reasoner = FoundryReasoner(endpoint="https://project.invalid")
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status=status, output_text=text)

    monkeypatch.setattr(reasoner, "_get_client",
                        lambda: SimpleNamespace(responses=SimpleNamespace(create=create)))
    return reasoner, calls


def test_model_payload_not_truncated_and_metadata_not_overwritten(monkeypatch):
    reasoner, calls = fake_foundry(monkeypatch, '{"agent":"forged","reasoner":"forged"}')
    payload = {"evidence": "x" * 9000}
    result = reasoner.run("rca-investigator", "context", payload)
    assert calls[0]["input"].endswith(json.dumps(payload))
    assert result["agent"] == "rca-investigator"
    assert result["reasoner"] == "foundry"


@pytest.mark.parametrize("text,status", [("not json", "completed"), ("[]", "completed"),
                                        ("{}", "incomplete"), ("{}", "failed")])
def test_failed_or_invalid_model_result_raises(monkeypatch, text, status):
    reasoner, _ = fake_foundry(monkeypatch, text, status)
    with pytest.raises((ValueError, RuntimeError)):
        reasoner.run("rca-investigator", "context", {})


def test_oversized_payload_is_rejected_before_model_call(monkeypatch):
    reasoner, calls = fake_foundry(monkeypatch, "{}")
    with pytest.raises(ValueError, match="60000-byte"):
        reasoner.run("rca-investigator", "context", {"evidence": "x" * 60001})
    assert not calls


@pytest.mark.parametrize("rule", [
    {}, {"id": "missing-predicate"}, {"resource_patterns": ["*"]},
    {"resource_pattern": ""}, {"resource_pattern": "   "},
    {"action": "restart", "resource_patterns": ["*"]},
    {"action": "restart "},
])
def test_malformed_deny_rules_cannot_be_silently_ignored(rule):
    policy = load_policy()
    policy["deny"] = [rule]
    with pytest.raises(ValueError, match="deny rule"):
        ChangeSafetyGate(policy)


def test_mutated_policy_is_revalidated():
    gate = ChangeSafetyGate()
    gate.policy["deny"] = [{}]
    with pytest.raises(ValueError, match="recognized predicate"):
        gate.evaluate(request())


@pytest.mark.parametrize("location,markers", [
    ("raw", {"IsSynthetic": True}),
    ("labels", {"is_synthetic": "true"}),
    ("labels", {"operational_use": "prohibited"}),
    ("raw", {"nested": [{"OperationalUse": "prohibited"}]}),
])
def test_nonprimary_source_prohibition_reaches_actual_gate(incident, location, markers):
    policy = load_policy()
    policy["approval_tiers"]["prod"]["restart"] = "none"
    policy["change_freezes"] = []
    gate = ChangeSafetyGate(policy)
    reasoner = ChangedPlanner({"is_synthetic": False, "operational_use": "unspecified"})
    orchestrator = OpsOrchestrator(reasoner=reasoner, gate=gate, evaluate_at=AT)
    assert orchestrator.handle(incident).decision["effect"] == "allow"
    secondary = next(item for item in incident.envelopes if item is not incident.primary)
    getattr(secondary, location).update(markers)
    result = orchestrator.handle(incident)
    assert result.decision["effect"] == "deny"
    assert result.decision["matched_rules"] == ["synthetic_non_operation"]
    assert "deny" in result.comms["engineer_update"]
    assert not result.executed


class ChangedStage(StubReasoner):
    def __init__(self, stage, output):
        self.stage = stage
        self.output = output
        self.calls = []

    def run(self, agent, context, payload):
        self.calls.append(agent)
        if agent == self.stage:
            return self.output
        return super().run(agent, context, payload)


@pytest.mark.parametrize("output", [
    {}, {"hypotheses": "none"}, {"hypotheses": []}, {"hypotheses": [{}]},
    {"hypotheses": [{"hypothesis": "cause", "confidence": "high",
                     "evidence": [], "next_check": "check"}]},
    {"hypotheses": [], "notable_absence": 0},
    {"hypotheses": [], "notable_absence": "unknown", "error": None},
])
def test_malformed_rca_blocks_planning(incident, output):
    reasoner = ChangedStage("rca-investigator", output)
    with pytest.raises((ValueError, RuntimeError)):
        OpsOrchestrator(reasoner=reasoner, evaluate_at=AT).handle(incident)
    assert reasoner.calls == ["rca-investigator"]


def test_completed_empty_foundry_rca_blocks_pipeline(monkeypatch, incident):
    reasoner, calls = fake_foundry(monkeypatch, "{}", status="completed")
    with pytest.raises(ValueError, match="hypotheses"):
        OpsOrchestrator(reasoner=reasoner).handle(incident)
    assert len(calls) == 1


@pytest.mark.parametrize("output", [
    {}, {"engineer_update": "draft"}, {"engineer_update": "", "executive_update": "draft"},
    {"engineer_update": "draft", "executive_update": False},
    {"engineer_update": "draft", "executive_update": "draft", "withheld": {}},
])
def test_malformed_comms_never_returns_success(incident, output):
    reasoner = ChangedStage("incident-comms", output)
    with pytest.raises((ValueError, RuntimeError)):
        OpsOrchestrator(reasoner=reasoner, evaluate_at=AT).handle(incident)
    assert reasoner.calls == ["rca-investigator", "remediation-planner", "incident-comms"]


def test_explicit_insufficient_evidence_is_not_an_empty_stage(incident):
    reasoner = ChangedStage("rca-investigator", {
        "hypotheses": [], "notable_absence": "No usable source evidence is available.",
    })
    result = OpsOrchestrator(reasoner=reasoner, evaluate_at=AT).handle(incident)
    assert result.rca["hypotheses"] == []
    assert result.comms["engineer_update"]
    assert not result.executed


def test_malformed_provenance_blocks_before_reasoning(incident):
    incident.primary.raw["IsSynthetic"] = "unknown"
    reasoner = ChangedStage("rca-investigator", {})
    with pytest.raises(ValueError, match="source synthetic provenance"):
        OpsOrchestrator(reasoner=reasoner, evaluate_at=AT).handle(incident)
    assert not reasoner.calls
