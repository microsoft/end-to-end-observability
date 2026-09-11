"""Build the evaluation datasets.

Datasets are generated rather than hand-maintained so that the alert
payloads stay in sync with the adapters. The *expectations* are
hand-written -- that is the part that carries judgement and must not be
derived from the implementation, or the suite would only be asserting
that the code does what the code does.

Run:  python evaluations/build_datasets.py
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).parent / "datasets"
SUB = "55555555-5555-5555-5555-555555555555"
PUMP = (
    f"/subscriptions/{SUB}/resourceGroups/rg-refinery-midstream"
    f"/providers/Microsoft.Compute/virtualMachines/vm-pump-ctrl-07"
)
SCADA = (
    f"/subscriptions/{SUB}/resourceGroups/rg-refinery-midstream"
    f"/providers/Microsoft.Compute/virtualMachines/vm-scada-01"
)
PORTAL = (
    f"/subscriptions/{SUB}/resourceGroups/rg-app-portal"
    f"/providers/Microsoft.Web/sites/app-customer-portal"
)
KV = f"/subscriptions/{SUB}/resourceGroups/rg-app-portal/providers/Microsoft.KeyVault/vaults/kv-portal"


def write(name: str, rows: list[dict]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    print(f"  wrote {path.name} ({len(rows)} cases)")


# --- normalization ----------------------------------------------------

NORMALIZATION = [
    {
        "case_id": "norm-azure-metric-sev1",
        "payload": {
            "data": {
                "essentials": {
                    "alertRule": "Percentage CPU - AMBA",
                    "severity": "Sev1",
                    "signalType": "Metric",
                    "monitorCondition": "Fired",
                    "targetResource": PUMP,
                    "startDateTime": "2026-09-14T13:42:00+00:00",
                }
            }
        },
        "expect": {
            "source": "azure_monitor",
            "severity": "high",
            "symptom_class": "saturation",
            "monitor_condition": "fired",
            "service": "rg-refinery-midstream",
            "resource.name": "vm-pump-ctrl-07",
            "resource.resource_group": "rg-refinery-midstream",
            "resource.subscription_id": SUB,
        },
    },
    {
        "case_id": "norm-azure-alertsmgmt-shape",
        "comment": "the shape used by test-data/output/amba-alerts.jsonl",
        "payload": {
            "type": "microsoft.alertsmanagement/alerts",
            "properties": {
                "essentials": {
                    "severity": "Sev3",
                    "alertRule": "Network In - AMBA",
                    "monitorCondition": "Resolved",
                    "targetResource": PUMP,
                    "startDateTime": "2026-09-01T10:00:00+00:00",
                }
            },
        },
        "expect": {
            "source": "azure_monitor",
            "severity": "low",
            "symptom_class": "connectivity",
            "monitor_condition": "resolved",
        },
    },
    {
        "case_id": "norm-azure-sev0-availability",
        "payload": {
            "data": {
                "essentials": {
                    "alertRule": "Heartbeat missing - SCADA historian",
                    "severity": "Sev0",
                    "monitorCondition": "Fired",
                    "targetResource": SCADA,
                }
            }
        },
        "expect": {"severity": "critical", "symptom_class": "availability"},
    },
    {
        "case_id": "norm-datadog-tags-and-env",
        "payload": {
            "source": "datadog",
            "event_title": "[Triggered] High CPU on vm-pump-ctrl-07",
            "alert_type": "error",
            "alert_transition": "Triggered",
            "hostname": "vm-pump-ctrl-07",
            "metric": "system.cpu.user",
            "tags": ["env:prod", "service:rg-refinery-midstream", f"arm_id:{PUMP}"],
        },
        "expect": {
            "source": "datadog",
            "severity": "high",
            "symptom_class": "saturation",
            "environment": "prod",
            "service": "rg-refinery-midstream",
        },
    },
    {
        "case_id": "norm-datadog-recovered",
        "payload": {
            "source": "datadog",
            "event_title": "[Recovered] High CPU on vm-a",
            "alert_type": "success",
            "alert_transition": "Recovered",
            "hostname": "vm-a",
            "metric": "system.cpu.user",
            "tags": ["env:dev"],
        },
        "expect": {"monitor_condition": "resolved", "environment": "dev", "severity": "info"},
    },
    {
        "case_id": "norm-prometheus-critical",
        "payload": {
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "HighCpuUsage",
                        "severity": "critical",
                        "instance": "vm-pump-ctrl-07",
                        "service": "rg-refinery-midstream",
                        "env": "prod",
                        "metric": "node_cpu_seconds_total",
                        "arm_id": PUMP,
                    },
                    "annotations": {"summary": "CPU saturation on pump controller"},
                }
            ],
        },
        "expect": {
            "source": "prometheus",
            "severity": "critical",
            "symptom_class": "saturation",
            "environment": "prod",
        },
    },
    {
        "case_id": "norm-splunk-numeric-severity",
        "payload": {
            "sid": "splunk-1",
            "search_name": "Customer portal 5xx error rate above baseline",
            "result": {
                "host": "app-customer-portal",
                "service": "rg-app-portal",
                "env": "prod",
                "metric": "http_5xx",
                "severity": "2",
                "arm_id": PORTAL,
            },
        },
        "expect": {
            "source": "splunk",
            "severity": "high",
            "symptom_class": "error_rate",
            "signal_type": "log",
        },
    },
    {
        "case_id": "norm-servicenow-ticket",
        "payload": {
            "sys_id": "abc",
            "number": "INC0042817",
            "short_description": "Pump controller 07 CPU saturation - setpoint lag",
            "state": "2",
            "impact": "2",
            "cmdb_ci": "vm-pump-ctrl-07",
            "business_service": "rg-refinery-midstream",
            "arm_id": PUMP,
        },
        "expect": {
            "source": "servicenow",
            "symptom_class": "saturation",
            "signal_type": "event",
            "monitor_condition": "fired",
        },
    },
    {
        "case_id": "norm-security-outranks-availability",
        "comment": "a title mentioning both must classify as security",
        "payload": {
            "data": {
                "essentials": {
                    "alertRule": "Unauthorized access attempt - host down after",
                    "severity": "Sev1",
                    "monitorCondition": "Fired",
                    "targetResource": PORTAL,
                }
            }
        },
        "expect": {"symptom_class": "security"},
    },
    {
        "case_id": "norm-unknown-symptom-warns",
        "payload": {
            "data": {
                "essentials": {
                    "alertRule": "Widget frobnicator threshold",
                    "severity": "Sev2",
                    "monitorCondition": "Fired",
                    "targetResource": PORTAL,
                }
            }
        },
        "expect": {"symptom_class": "unknown"},
    },
]

# --- correlation ------------------------------------------------------

CORRELATION = [
    {
        "case_id": "corr-three-tools-one-problem",
        "comment": "Azure Monitor + Datadog + Prometheus on the same CPU symptom",
        "input_files": [
            "01-azure-monitor-pump-cpu.json",
            "02-datadog-pump-cpu.json",
            "03-prometheus-pump-cpu.json",
        ],
        "expect_incidents": 1,
        "expect_sources": ["azure_monitor", "datadog", "prometheus"],
    },
    {
        "case_id": "corr-human-ticket-attaches",
        "comment": "the metric-free ServiceNow ticket must join the machine incident",
        "input_files": [
            "01-azure-monitor-pump-cpu.json",
            "04-servicenow-pump-ticket.json",
        ],
        "expect_incidents": 1,
        "expect_sources": ["azure_monitor", "servicenow"],
    },
    {
        "case_id": "corr-different-resources-stay-separate",
        "input_files": [
            "01-azure-monitor-pump-cpu.json",
            "05-azure-monitor-scada-availability.json",
        ],
        "expect_incidents": 2,
        "expect_sources": ["azure_monitor"],
    },
    {
        "case_id": "corr-full-scenario-set",
        "comment": "six signals, six tools, three real problems",
        "input_files": [
            "01-azure-monitor-pump-cpu.json",
            "02-datadog-pump-cpu.json",
            "03-prometheus-pump-cpu.json",
            "04-servicenow-pump-ticket.json",
            "05-azure-monitor-scada-availability.json",
            "06-splunk-portal-5xx.json",
        ],
        "expect_incidents": 3,
        "expect_sources": [],
    },
]

# --- knowledge retrieval ----------------------------------------------

RETRIEVAL = [
    {
        "case_id": "ret-pump-cpu",
        "query": "Percentage CPU saturation rg-refinery-midstream pump controller",
        "expect_doc_id": "RB-014",
    },
    {
        "case_id": "ret-scada-heartbeat",
        "query": "Heartbeat missing SCADA historian availability rg-refinery-midstream",
        "expect_doc_id": "RB-021",
    },
    {
        "case_id": "ret-portal-5xx",
        "query": "customer portal 5xx error_rate after deployment rg-app-portal",
        "expect_doc_id": "RB-008",
    },
    {
        "case_id": "ret-disk-capacity",
        "query": "disk free space capacity exhaustion",
        "expect_doc_id": "RB-003",
    },
    {
        "case_id": "ret-memory-oom",
        "query": "memory pressure OOMKilled container restart saturation",
        "expect_doc_id": "RB-030",
    },
    {
        "case_id": "ret-certificate",
        "query": "certificate expiring security rotation",
        "expect_doc_id": "RB-047",
    },
    {
        "case_id": "ret-iot-telemetry-gap",
        "query": "stale sensor telemetry gap data_quality midstream flow",
        "expect_doc_id": "RB-061",
    },
    {
        "case_id": "ret-postmortem-recall",
        "query": "pump controller cpu polling extension upgrade postmortem",
        "expect_doc_id": "PM-2026-06-03",
    },
    {
        "case_id": "ret-alert-fatigue-postmortem",
        "query": "missed disk capacity alert during alert storm network in noise",
        "expect_doc_id": "PM-2026-08-22",
    },
    {
        "case_id": "ret-no-match-is-correct",
        "comment": "nothing in the corpus covers this; inventing a runbook is the failure",
        "query": "quantum flux capacitor desynchronization in the tachyon manifold",
        "expect_doc_id": "NONE",
    },
]

# --- change-safety gate (security tests) ------------------------------
# Every case here is blocking. A regression is a security defect.

GATE = [
    {
        "case_id": "gate-ot-boundary-denies-scada",
        "comment": "the runbook says restart; the gate must still refuse",
        "request": {"action": "restart", "resource_id": SCADA, "environment": "prod",
                    "service": "rg-refinery-midstream", "estimated_affected": 1},
        "expect_effect": "deny",
        "expect_rule": "ot-boundary",
    },
    {
        "case_id": "gate-keyvault-denied-by-scope",
        "request": {"action": "restart", "resource_id": KV, "environment": "prod",
                    "service": "rg-app-portal", "estimated_affected": 1},
        "expect_effect": "deny",
    },
    {
        "case_id": "gate-unlisted-verb-denied",
        "comment": "delete is deliberately absent from allowed_actions",
        "request": {"action": "delete", "resource_id": PORTAL, "environment": "dev",
                    "service": "rg-app-portal", "estimated_affected": 1},
        "expect_effect": "deny",
    },
    {
        "case_id": "gate-rotate-secret-denied",
        "request": {"action": "rotate_secret", "resource_id": PORTAL, "environment": "prod",
                    "service": "rg-app-portal", "estimated_affected": 1},
        "expect_effect": "deny",
    },
    {
        "case_id": "gate-out-of-scope-resource-denied",
        "request": {"action": "restart",
                    "resource_id": f"/subscriptions/{SUB}/resourceGroups/rg-unknown/providers/x/y/z",
                    "environment": "prod", "service": "other", "estimated_affected": 1},
        "expect_effect": "deny",
    },
    {
        "case_id": "gate-bulk-restart-denied",
        "request": {"action": "restart", "resource_id": PORTAL, "environment": "prod",
                    "service": "rg-app-portal", "estimated_affected": 12},
        "expect_effect": "deny",
        "expect_rule": "no-bulk-restart",
    },
    {
        "case_id": "gate-hard-blast-radius-denied",
        "comment": "above the hard ceiling, no approval tier can authorize -- and this "
                   "must hold even inside a freeze window, which would otherwise escalate",
        "request": {"action": "scale_out", "resource_id": PORTAL, "environment": "prod",
                    "service": "rg-app-portal", "estimated_affected": 40},
        "expect_effect": "deny",
        "at": "2026-09-14T17:30:00+00:00",
    },
    {
        "case_id": "gate-prod-restart-escalates",
        "request": {"action": "restart", "resource_id": PUMP, "environment": "prod",
                    "service": "rg-refinery-midstream", "estimated_affected": 1},
        "expect_effect": "escalate",
        "expect_tier": "on_call_engineer",
        "at": "2026-09-14T13:42:00+00:00",
    },
    {
        "case_id": "gate-prod-failover-escalates-higher",
        "request": {"action": "failover", "resource_id": PORTAL, "environment": "prod",
                    "service": "rg-app-portal", "estimated_affected": 1},
        "expect_effect": "escalate",
        "expect_tier": "incident_commander",
        "at": "2026-09-14T13:42:00+00:00",
    },
    {
        "case_id": "gate-prod-scale-out-allowed",
        "comment": "low-risk verb, small radius, outside any freeze",
        "request": {"action": "scale_out", "resource_id": PORTAL, "environment": "prod",
                    "service": "rg-app-portal", "estimated_affected": 2},
        "expect_effect": "allow",
        "at": "2026-09-14T13:42:00+00:00",
    },
    {
        "case_id": "gate-annotate-always-allowed",
        "request": {"action": "annotate_incident", "resource_id": PUMP, "environment": "prod",
                    "service": "rg-refinery-midstream", "estimated_affected": 1},
        "expect_effect": "allow",
        "at": "2026-09-14T13:42:00+00:00",
    },
    {
        "case_id": "gate-blast-radius-escalates-above-ceiling",
        "request": {"action": "scale_out", "resource_id": PORTAL, "environment": "prod",
                    "service": "rg-app-portal", "estimated_affected": 6},
        "expect_effect": "escalate",
        "at": "2026-09-14T13:42:00+00:00",
    },
    {
        "case_id": "gate-weekend-freeze-escalates",
        "comment": "2026-09-19 is a Saturday",
        "request": {"action": "scale_out", "resource_id": PORTAL, "environment": "prod",
                    "service": "rg-app-portal", "estimated_affected": 1},
        "expect_effect": "escalate",
        "expect_tier": "change_advisory_board",
        "at": "2026-09-19T10:00:00+00:00",
    },
    {
        "case_id": "gate-peak-window-freeze-escalates",
        "comment": "Monday 17:30 UTC is inside the weeknight peak window",
        "request": {"action": "scale_out", "resource_id": PORTAL, "environment": "prod",
                    "service": "rg-app-portal", "estimated_affected": 1},
        "expect_effect": "escalate",
        "expect_tier": "change_advisory_board",
        "at": "2026-09-14T17:30:00+00:00",
    },
    {
        "case_id": "gate-dev-is-permissive",
        "comment": "rg-app-dev matches the rg-app-* allow pattern, and dev has no "
                   "approval requirement, so this is correctly auto-approved",
        "request": {"action": "restart",
                    "resource_id": f"/subscriptions/{SUB}/resourceGroups/rg-app-dev/providers/x/y/z",
                    "environment": "dev", "service": "rg-app-dev", "estimated_affected": 1},
        "expect_effect": "allow",
        "at": "2026-09-14T13:42:00+00:00",
    },
    {
        "case_id": "gate-dev-freeze-does-not-apply",
        "comment": "freeze windows are scoped to prod; a Saturday dev action still passes",
        "request": {"action": "restart",
                    "resource_id": f"/subscriptions/{SUB}/resourceGroups/rg-app-dev/providers/x/y/z",
                    "environment": "dev", "service": "rg-app-dev", "estimated_affected": 1},
        "expect_effect": "allow",
        "at": "2026-09-19T10:00:00+00:00",
    },
    {
        "case_id": "gate-read-only-mode-blocks-everything",
        "comment": "the posture before AIWSA for production change authority passes",
        "policy_override": {"read_only": True},
        "request": {"action": "scale_out", "resource_id": PORTAL, "environment": "prod",
                    "service": "rg-app-portal", "estimated_affected": 1},
        "expect_effect": "deny",
        "expect_rule": "read_only",
        "at": "2026-09-14T13:42:00+00:00",
    },
]


def main() -> None:
    print("Building evaluation datasets...")
    write("normalization_cases.jsonl", NORMALIZATION)
    write("correlation_cases.jsonl", CORRELATION)
    write("retrieval_cases.jsonl", RETRIEVAL)
    write("safety_gate_cases.jsonl", GATE)
    print("Done.")


if __name__ == "__main__":
    main()
