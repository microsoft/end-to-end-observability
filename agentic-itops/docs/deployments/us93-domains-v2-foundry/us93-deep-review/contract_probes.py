"""Offline review probes; does not modify repository or call Azure."""
import contextlib
import copy
import importlib.util
import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ["AGENTIC_ITOPS_LIVE_AZURE"] = "0"
os.environ["AGENTIC_ITOPS_LIVE_SEARCH"] = "0"
sys.dont_write_bytecode = True

from agentic_itops import cli
from agentic_itops.envelope import (
    IncidentEnvelope, MetricRef, MonitorCondition, ResourceRef, Severity,
    SymptomClass, compute_fingerprint,
)
from agentic_itops.hygiene import analyze
from agentic_itops.normalizer import Incident, correlate, normalize_many, normalize_one
from agentic_itops.orchestrator import FoundryReasoner, OpsOrchestrator, run_demo
from agentic_itops.safety_gate import ChangeRequest, ChangeSafetyGate

results = []


def record(name, expected, observed, defect):
    results.append({"probe": name, "expected": expected, "observed": observed, "defectReproduced": defect})


payload = {
    "data": {
        "essentials": {
            "alertRule": "CPU High", "severity": "Sev2", "monitorCondition": "Fired",
            "alertTargetIDs": ["/subscriptions/test/resourceGroups/rg-app-demo/providers/Microsoft.Compute/virtualMachines/vm-api"],
        },
        "alertContext": {"condition": {"allOf": [
            {"metricName": "Percentage CPU", "metricValue": 95, "threshold": 80, "operator": "GreaterThan"}
        ]}},
    }
}
metric = normalize_one(payload).metric
record("azure-common-alert-array", {"name": "Percentage CPU", "value": 95, "threshold": 80},
       metric.__dict__, metric.value is None and metric.threshold is None)

batch = {"alerts": [
    {"labels": {"alertname": "CPU High", "instance": "host-a", "metric": "cpu"}, "status": "firing"},
    {"labels": {"alertname": "CPU High", "instance": "host-b", "metric": "cpu"}, "status": "firing"},
]}
normalized, failures = normalize_many([batch])
record("raw-alertmanager-batch", 2, {"normalized": len(normalized), "failures": len(failures),
       "warnings": normalized[0].normalization.warnings}, len(normalized) == 1)

primary = normalize_one(payload)
primary.environment = "test"
incident = Incident(primary.fingerprint, [primary])


class ProposalReasoner:
    def run(self, agent, context, payload):
        if agent == "remediation-planner":
            return {"proposed_action": "scale_out",
                    "target_resource_id": "/subscriptions/test/resourceGroups/rg-app-demo/providers/Microsoft.Compute/virtualMachines/vm-scada-01",
                    "blast_radius": {"scope": "tenant", "estimated_affected": 40},
                    "rationale": "offline contract probe"}
        return {}


with patch("agentic_itops.orchestrator.search_knowledge", return_value={"results": []}), \
     patch("agentic_itops.orchestrator.run_kql", return_value={"rows": [], "row_count": 0}), \
     patch("agentic_itops.orchestrator.get_resource_context", return_value={"found": False}), \
     patch("agentic_itops.orchestrator.get_recent_changes", return_value={"changes": []}):
    result = OpsOrchestrator(reasoner=ProposalReasoner()).handle(incident)
record("gate-evaluates-wrong-target-and-radius", "deny or reject proposal/incident mismatch",
       {"proposal": result.plan, "decision": result.decision, "executed": result.executed},
       result.decision["effect"] == "allow")

capture = io.StringIO()
with contextlib.redirect_stdout(capture):
    exit_code = cli.main(["gate", "--action", "scale_out", "--resource", primary.resource.id,
                          "--env", "test", "--policy", str(OUT / "empty-policy.yaml"), "--json"])
decision = json.loads(capture.getvalue())
record("empty-policy-cli-fallback", "deny with explicit empty configuration",
       {"exit": exit_code, "effect": decision["effect"], "policy": decision["policy_version"]},
       decision["effect"] == "allow" and decision["policy_version"] == "1.0-sample")

policy = {"allowed_actions": ["scale_out"], "approval_tiers": {"test": {"*": "none"}}}
decision = ChangeSafetyGate(policy).evaluate(
    ChangeRequest(action="scale_out", resource_id="outside-every-approved-scope", environment="test"))
record("policy-without-resource-scope", "reject missing required scope or deny",
       decision.to_dict(), decision.allowed)


def envelope(metric_name, state=MonitorCondition.FIRED, stamp="2026-09-14T13:00:00Z"):
    return IncidentEnvelope(
        fingerprint=compute_fingerprint("svc", "host", "saturation", metric_name),
        source="test", title="Same rule", service="svc", resource=ResourceRef(id="host"),
        severity=Severity.MEDIUM, symptom_class=SymptomClass.SATURATION,
        metric=MetricRef(name=metric_name), monitor_condition=state, first_seen_at=stamp)


def attached_to(order):
    groups = correlate([envelope(name) for name in order] + [envelope("")])
    return next(group.envelopes[0].metric.name for group in groups if len(group.envelopes) == 2)


attachments = [attached_to(["cpu", "memory"]), attached_to(["memory", "cpu"])]
record("metric-free-correlation-order", "stable/explicit ambiguity handling", attachments,
       attachments[0] != attachments[1])

events = [
    envelope("cpu", MonitorCondition.FIRED, "2026-09-14T12:00:00-04:00"),
    envelope("cpu", MonitorCondition.RESOLVED, "2026-09-14T15:30:00Z"),
]
resolved = Incident(events[0].fingerprint, events).is_resolved
record("resolution-utc-order", False, resolved, resolved is True)
empty = analyze([])
record("empty-hygiene-volume", 0, empty["total_firings"], empty["total_firings"] == 1)
history = analyze([envelope("cpu"), envelope("cpu", MonitorCondition.RESOLVED)])
record("hygiene-firing-lifecycle", "one firing plus one resolution, not two firings",
       history["findings"][0], history["findings"][0]["fired"] == 2)

capture = io.StringIO()
with contextlib.redirect_stdout(capture):
    rejected = run_demo([{"unsupported_shape": True}])
record("demo-drops-normalization-failures", "visible failed/incomplete batch", rejected, rejected == [])

captured = {}


def response_create(**kwargs):
    captured.update(kwargs)
    return SimpleNamespace(output_text="not-json")


reasoner = FoundryReasoner(endpoint="https://unused.invalid", state_path=OUT / "nonexistent-state.json")
reasoner._client = SimpleNamespace(responses=SimpleNamespace(create=response_create))
answer = reasoner.run("remediation-planner", "", {"large_evidence": "x" * 7000})
body = captured["input"].split("Structured payload (JSON):\n", 1)[1]
try:
    json.loads(body)
    valid = True
except json.JSONDecodeError:
    valid = False
record("foundry-truncates-json", "valid bounded structured evidence", {"valid_json": valid, "chars": len(body)}, not valid)
record("foundry-unstructured-response", "explicit schema failure", answer, "error" not in answer)

arguments = cli.build_parser().parse_args(["demo", "--foundry"])
record("live-demo-pins-historical-time", "live acceptance uses current trusted time",
       arguments.at, arguments.at == cli.DEFAULT_DEMO_AT)

spec = importlib.util.spec_from_file_location("review_openapi_generator", ROOT / "scripts" / "emit_openapi.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
import yaml
parity = {}
for name, builder in module.SPECS.items():
    actual = yaml.safe_load((ROOT / "tools" / "openapi" / name).read_text(encoding="utf-8"))
    parity[name] = actual == builder()
results.append({"probe": "openapi-generator-parity", "observed": parity, "allMatch": all(parity.values())})

destination = OUT / "contract-probe-results.json"
destination.write_text(json.dumps(results, indent=2, default=str) + "\n", encoding="utf-8")
for item in results:
    print(item["probe"], "DEFECT_REPRODUCED" if item.get("defectReproduced") else
          "PASS" if item.get("allMatch") else "NOT_REPRODUCED")
assert all(item.get("defectReproduced", item.get("allMatch", False)) for item in results)
print(f"{len(results)} offline checks recorded in {destination.name}")
