"""Generate the sample alert corpus and tool fixtures.

Run:  python scripts/generate_sample_data.py

Two scenarios are built deliberately:

1. **Cross-source correlation.** Azure Monitor, Datadog and Prometheus
   each alert on the same CPU saturation on ``vm-pump-ctrl-07``, and a
   human opens a ServiceNow ticket about it. Four signals, four tools,
   one fingerprint. This is the demo moment.

2. **Governance denial.** An availability alert on ``vm-scada-01``
   where the obvious remediation is a restart -- and the Change-Safety
   Gate refuses, because OT assets are outside the agent's authority.
   A demo that only shows success proves nothing about governance.

The bulk file for hygiene analysis is derived from this repo's existing
``test-data/output/amba-alerts.jsonl`` so the numbers relate to data the
team already recognises.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALERTS = ROOT / "data" / "alerts"
FIXTURES = ROOT / "data" / "fixtures"
HISTORY = ROOT / "data" / "alert-history"
REPO_TEST_DATA = ROOT.parent / "test-data" / "output" / "amba-alerts.jsonl"

SUB = "55555555-5555-5555-5555-555555555555"
PUMP_ARM = (
    f"/subscriptions/{SUB}/resourceGroups/rg-refinery-midstream"
    f"/providers/Microsoft.Compute/virtualMachines/vm-pump-ctrl-07"
)
SCADA_ARM = (
    f"/subscriptions/{SUB}/resourceGroups/rg-refinery-midstream"
    f"/providers/Microsoft.Compute/virtualMachines/vm-scada-01"
)
API_ARM = (
    f"/subscriptions/{SUB}/resourceGroups/rg-app-portal"
    f"/providers/Microsoft.Web/sites/app-customer-portal"
)

T0 = datetime(2026, 9, 14, 13, 42, 0, tzinfo=timezone.utc)


def iso(offset_minutes: int = 0) -> str:
    return (T0 + timedelta(minutes=offset_minutes)).isoformat()


def write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"  wrote {path.relative_to(ROOT)}")


# --- scenario 1: four tools, one problem ------------------------------


def scenario_cross_source() -> None:
    write(
        ALERTS / "01-azure-monitor-pump-cpu.json",
        {
            "schemaId": "azureMonitorCommonAlertSchema",
            "data": {
                "essentials": {
                    "alertId": "/subscriptions/.../alerts/aaaa1111",
                    "alertRule": "Percentage CPU - AMBA",
                    "severity": "Sev1",
                    "signalType": "Metric",
                    "monitorCondition": "Fired",
                    "monitorService": "Platform",
                    "alertTargetIDs": [PUMP_ARM],
                    "targetResource": PUMP_ARM,
                    "targetResourceType": "Microsoft.Compute/virtualMachines",
                    "targetResourceRegion": "centralus",
                    "alertState": "New",
                    "startDateTime": iso(0),
                    "description": "Sustained CPU above threshold on midstream pump controller",
                },
                "alertContext": {
                    "condition": {
                        "allOf": [
                            {
                                "metricName": "Percentage CPU",
                                "metricValue": 94.2,
                                "threshold": 85.0,
                                "operator": "GreaterThan",
                            }
                        ]
                    }
                },
            },
        },
    )

    write(
        ALERTS / "02-datadog-pump-cpu.json",
        {
            "source": "datadog",
            "alert_id": "dd-88213",
            "event_title": "[Triggered] High CPU on vm-pump-ctrl-07",
            "event_msg": "system.cpu.user over 90% for 10 minutes",
            "alert_type": "error",
            "alert_transition": "Triggered",
            "date": iso(2),
            "hostname": "vm-pump-ctrl-07",
            "metric": "system.cpu.user",
            "value": 92.7,
            "tags": [
                "env:prod",
                "service:rg-refinery-midstream",
                f"arm_id:{PUMP_ARM}",
                "region:centralus",
                "team:midstream-ops",
            ],
        },
    )

    write(
        ALERTS / "03-prometheus-pump-cpu.json",
        {
            "version": "4",
            "status": "firing",
            "receiver": "agentic-itops",
            "alerts": [
                {
                    "status": "firing",
                    "fingerprint": "9f2c1ab44de0",
                    "startsAt": iso(3),
                    "labels": {
                        "alertname": "HighCpuUsage",
                        "severity": "critical",
                        "instance": "vm-pump-ctrl-07",
                        "service": "rg-refinery-midstream",
                        "env": "prod",
                        "metric": "node_cpu_seconds_total",
                        "arm_id": PUMP_ARM,
                    },
                    "annotations": {
                        "summary": "CPU saturation on midstream pump controller",
                        "description": "Node CPU above 90% for 5m",
                    },
                }
            ],
        },
    )

    write(
        ALERTS / "04-servicenow-pump-ticket.json",
        {
            "sys_id": "a1b2c3d4e5f6",
            "number": "INC0042817",
            "short_description": "Pump controller 07 CPU saturation - setpoint changes lagging",
            "description": (
                "Midstream control room reports lag on pump 07 setpoint changes. "
                "Suspect CPU saturation on the controller VM."
            ),
            "state": "2",
            "impact": "2",
            "priority": "2",
            "cmdb_ci": "vm-pump-ctrl-07",
            "business_service": "rg-refinery-midstream",
            "environment": "prod",
            "arm_id": PUMP_ARM,
            "opened_at": iso(11),
            "assignment_group": "Midstream Operations",
            "caller_id": "control.room.operator",
        },
    )


# --- scenario 2: the gate says no -------------------------------------


def scenario_governance_denial() -> None:
    write(
        ALERTS / "05-azure-monitor-scada-availability.json",
        {
            "schemaId": "azureMonitorCommonAlertSchema",
            "data": {
                "essentials": {
                    "alertId": "/subscriptions/.../alerts/bbbb2222",
                    "alertRule": "Heartbeat missing - SCADA historian",
                    "severity": "Sev0",
                    "signalType": "Log",
                    "monitorCondition": "Fired",
                    "monitorService": "Log Analytics",
                    "alertTargetIDs": [SCADA_ARM],
                    "targetResource": SCADA_ARM,
                    "targetResourceType": "Microsoft.Compute/virtualMachines",
                    "targetResourceRegion": "centralus",
                    "alertState": "New",
                    "startDateTime": iso(5),
                    "description": (
                        "No heartbeat from SCADA historian for 10 minutes. "
                        "Restarting the collector service is the documented fix."
                    ),
                }
            },
        },
    )


# --- scenario 3: an ordinary app incident -----------------------------


def scenario_app_errors() -> None:
    write(
        ALERTS / "06-splunk-portal-5xx.json",
        {
            "sid": "splunk-scheduler-9912",
            "search_name": "Customer portal 5xx error rate above baseline",
            "owner": "sre",
            "app": "observability",
            "result": {
                "host": "app-customer-portal",
                "service": "rg-app-portal",
                "env": "prod",
                "metric": "http_5xx",
                "count": "418",
                "severity": "2",
                "arm_id": API_ARM,
                "_time": iso(8),
                "_raw": (
                    "418 HTTP 500 responses in 5 minutes from app-customer-portal "
                    "following deployment rel-2026.09.14.3"
                ),
            },
        },
    )


# --- bulk corpus for hygiene analysis ---------------------------------


def bulk_corpus() -> None:
    """Build a realistic alert-history file for the hygiene analyst.

    Seeded from the repo's own AMBA sample when available so the output
    is recognisable to anyone who has looked at ``test-data``.
    """
    random.seed(20260914)
    base_rules = [
        ("Network In - AMBA", "Sev3", 0.93),
        ("Percentage CPU - AMBA", "Sev2", 0.55),
        ("Available Memory Bytes - AMBA", "Sev2", 0.88),
        ("Disk Read Bytes - AMBA", "Sev4", 0.97),
        ("Heartbeat missing - AMBA", "Sev1", 0.10),
        ("Data Disk IOPS Consumed Percentage - AMBA", "Sev3", 0.91),
        ("HTTP 5xx - portal", "Sev1", 0.22),
        ("Certificate expiring - AMBA", "Sev2", 0.0),
        ("VM availability - AMBA", "Sev1", 0.15),
        ("Flow rate deviation - midstream", "Sev2", 0.35),
    ]
    weights = [140, 62, 58, 47, 9, 41, 18, 3, 7, 12]

    seed_payload = None
    if REPO_TEST_DATA.exists():
        first = REPO_TEST_DATA.read_text(encoding="utf-8").splitlines()[0]
        seed_payload = json.loads(first)
        print(f"  seeded shape from {REPO_TEST_DATA.name}")

    lines: list[str] = []
    for (rule, severity, resolve_rate), count in zip(base_rules, weights):
        for _ in range(count):
            resource = (
                f"/subscriptions/{SUB}/resourceGroups/"
                f"{random.choice(['rg-data-01', 'rg-app-portal', 'rg-refinery-midstream'])}"
                f"/providers/microsoft.compute/virtualmachines/"
                f"vm-{random.choice(['ff093f', 'a21b7c', 'c8810d', 'pump-ctrl-07'])}"
            )
            resolved = random.random() < resolve_rate
            started = T0 - timedelta(days=random.randint(0, 29), minutes=random.randint(0, 1439))
            record = {
                "type": "microsoft.alertsmanagement/alerts",
                "properties": {
                    "essentials": {
                        "severity": severity,
                        "alertRule": rule,
                        "monitorCondition": "Resolved" if resolved else "Fired",
                        "alertState": "Closed" if resolved else "New",
                        "targetResource": resource,
                        "startDateTime": started.isoformat(),
                        "monitorService": "Platform",
                        "signalType": "Metric",
                    }
                },
            }
            if seed_payload:
                record["type"] = seed_payload.get("type", record["type"])
            lines.append(json.dumps(record))

    random.shuffle(lines)
    path = HISTORY / "bulk-amba-sample.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  wrote {path.relative_to(ROOT)} ({len(lines)} records)")


# --- tool fixtures ----------------------------------------------------


def fixtures() -> None:
    write(
        FIXTURES / "kql_cpu.json",
        {
            "columns": ["TimeGenerated", "Computer", "AvgCpu"],
            "rows": [
                [iso(-40), "vm-pump-ctrl-07", 41.2],
                [iso(-30), "vm-pump-ctrl-07", 44.8],
                [iso(-20), "vm-pump-ctrl-07", 78.5],
                [iso(-10), "vm-pump-ctrl-07", 91.3],
                [iso(0), "vm-pump-ctrl-07", 94.2],
            ],
            "note": "step change between T-30 and T-20 correlates with the config deployment",
        },
    )
    write(
        FIXTURES / "kql_deployments.json",
        {
            "columns": ["TimeGenerated", "OperationName", "Caller", "Properties"],
            "rows": [
                [
                    iso(-26),
                    "Microsoft.Compute/virtualMachines/extensions/write",
                    "svc-deploy@contoso.com",
                    "CustomScriptExtension v1.10.21 -> v1.10.24",
                ],
            ],
        },
    )
    write(
        FIXTURES / "kql_restarts.json",
        {
            "columns": ["TimeGenerated", "Computer", "RestartCount", "Reason"],
            "rows": [
                [iso(-18), "vm-pump-ctrl-07", 3, "polling service watchdog"],
                [iso(-9), "vm-pump-ctrl-07", 4, "polling service watchdog"],
            ],
        },
    )
    write(
        FIXTURES / "kql_requests.json",
        {
            "columns": ["TimeGenerated", "ResultCode", "Count"],
            "rows": [
                [iso(-15), "200", 8421],
                [iso(-10), "500", 96],
                [iso(-5), "500", 204],
                [iso(0), "500", 418],
            ],
        },
    )
    write(
        FIXTURES / "kql_generic.json",
        {
            "columns": ["TimeGenerated", "Message"],
            "rows": [[iso(-5), "no specific fixture matched this query intent"]],
        },
    )
    write(
        FIXTURES / "kql_iot.json",
        {
            "columns": ["TimeGenerated", "DeviceId", "FlowRate", "SuctionPressure"],
            "rows": [
                [iso(-25), "pump-07", 812.4, 41.2],
                [iso(-15), "pump-07", 640.1, 22.8],
                [iso(-5), "pump-07", 588.7, 18.4],
            ],
            "note": "falling suction pressure alongside flow drop is the cavitation signature",
        },
    )
    write(
        FIXTURES / "kql_availability.json",
        {
            "columns": ["TimeGenerated", "Computer", "LastSeen"],
            "rows": [[iso(-10), "vm-scada-01", iso(-15)]],
            "note": "historian stopped reporting 15 minutes before the alert fired",
        },
    )

    write(
        FIXTURES / "resource_graph.json",
        {
            "resources": [
                {
                    "id": PUMP_ARM,
                    "name": "vm-pump-ctrl-07",
                    "type": "Microsoft.Compute/virtualMachines",
                    "location": "centralus",
                    "resourceGroup": "rg-refinery-midstream",
                    "subscriptionId": SUB,
                    "tags": {"env": "prod", "criticality": "high", "owner": "midstream-ops"},
                    "owner": "midstream-ops@contoso.com",
                    "dependencies": {
                        "upstream": ["vm-scada-01 (historian)", "iot-hub-midstream"],
                        "downstream": ["app-flow-dashboard", "adx-refinery-telemetry"],
                    },
                    "criticality": "high",
                },
                {
                    "id": SCADA_ARM,
                    "name": "vm-scada-01",
                    "type": "Microsoft.Compute/virtualMachines",
                    "location": "centralus",
                    "resourceGroup": "rg-refinery-midstream",
                    "subscriptionId": SUB,
                    "tags": {"env": "prod", "criticality": "safety", "zone": "ot"},
                    "owner": "ot-engineering@contoso.com",
                    "dependencies": {
                        "upstream": ["plc-network-segment-a"],
                        "downstream": ["vm-pump-ctrl-07", "vm-pump-ctrl-08"],
                    },
                    "criticality": "safety-critical",
                    "note": "OT zone. Agent action is denied by policy regardless of severity.",
                },
                {
                    "id": API_ARM,
                    "name": "app-customer-portal",
                    "type": "Microsoft.Web/sites",
                    "location": "eastus2",
                    "resourceGroup": "rg-app-portal",
                    "subscriptionId": SUB,
                    "tags": {"env": "prod", "criticality": "high"},
                    "owner": "portal-team@contoso.com",
                    "dependencies": {
                        "upstream": ["sql-portal-prod", "redis-portal-prod"],
                        "downstream": ["external customers"],
                    },
                    "criticality": "high",
                },
            ]
        },
    )

    write(
        FIXTURES / "change_history.json",
        {
            "changes": [
                {
                    "timestamp": iso(-26),
                    "operation": "Microsoft.Compute/virtualMachines/extensions/write",
                    "status": "Succeeded",
                    "caller": "svc-deploy@contoso.com",
                    "target_resource_id": PUMP_ARM,
                    "service": "rg-refinery-midstream",
                    "change_ref": "CHG0031204",
                    "summary": "CustomScriptExtension 1.10.21 -> 1.10.24",
                    "correlation_hint": "landed 6 minutes before CPU stepped from ~44% to ~78%",
                },
                {
                    "timestamp": iso(-190),
                    "operation": "Microsoft.Compute/virtualMachines/write",
                    "status": "Succeeded",
                    "caller": "ops.engineer@contoso.com",
                    "target_resource_id": PUMP_ARM,
                    "service": "rg-refinery-midstream",
                    "change_ref": "CHG0031180",
                    "summary": "resize Standard_D2s_v5 (no effective change)",
                    "correlation_hint": "",
                },
                {
                    "timestamp": iso(-12),
                    "operation": "Microsoft.Web/sites/deployments/write",
                    "status": "Succeeded",
                    "caller": "github-actions@contoso.com",
                    "target_resource_id": API_ARM,
                    "service": "rg-app-portal",
                    "change_ref": "rel-2026.09.14.3",
                    "summary": "portal release 2026.09.14.3",
                    "correlation_hint": "5xx rate began climbing 2 minutes after this release",
                },
            ]
        },
    )


def main() -> None:
    print("Generating sample data...")
    scenario_cross_source()
    scenario_governance_denial()
    scenario_app_errors()
    bulk_corpus()
    fixtures()
    print("Done.")


if __name__ == "__main__":
    main()

