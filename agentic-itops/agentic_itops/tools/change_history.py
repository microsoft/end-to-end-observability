"""Change history tool (stubbed).

"What changed?" is the single highest-yield question in incident
response, and the one most often unanswered because the deployment
system and the monitoring system belong to different teams.

Live implementations typically union Azure Activity Log, Azure DevOps or
GitHub deployments, and the customer's change-management record.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parents[2] / "data" / "fixtures"


def get_recent_changes(
    resource_id: str = "",
    service: str = "",
    hours: int = 24,
) -> dict[str, Any]:
    """Return changes touching a resource or service in the recent window.

    Args:
        resource_id: ARM id to filter on. Optional.
        service: Logical service name to filter on. Optional.
        hours: Lookback window. Defaults to 24.

    Returns:
        ``{"changes": [...], "count": n, "window_hours": h}`` ordered
        most-recent first. Each change carries ``correlation_hint``,
        which is what makes it useful to an agent rather than a log dump.
    """
    if _live():
        return _get_live(resource_id, service, hours)
    return _get_stub(resource_id, service, hours)


def _live() -> bool:
    return os.getenv("AGENTIC_ITOPS_LIVE_AZURE", "").strip() in ("1", "true", "yes")


def _get_stub(resource_id: str, service: str, hours: int) -> dict[str, Any]:
    fixture = FIXTURES / "change_history.json"
    if not fixture.exists():
        return {"changes": [], "count": 0, "window_hours": hours, "mode": "stub"}

    data = json.loads(fixture.read_text(encoding="utf-8"))
    changes = data.get("changes", [])

    resource_needle = (resource_id or "").strip().lower()
    service_needle = (service or "").strip().lower()

    matched = []
    for change in changes:
        target = str(change.get("target_resource_id", "")).lower()
        target_service = str(change.get("service", "")).lower()
        if resource_needle and resource_needle in target:
            matched.append(change)
        elif service_needle and service_needle == target_service:
            matched.append(change)
        elif not resource_needle and not service_needle:
            matched.append(change)

    matched.sort(key=lambda c: str(c.get("timestamp", "")), reverse=True)
    return {
        "changes": matched,
        "count": len(matched),
        "window_hours": hours,
        "mode": "stub",
        "queried_at": datetime.now(timezone.utc).isoformat(),
    }


def _get_live(resource_id: str, service: str, hours: int) -> dict[str, Any]:
    from azure.identity import DefaultAzureCredential  # noqa: PLC0415
    from azure.mgmt.monitor import MonitorManagementClient  # noqa: PLC0415

    subscription_id = os.environ["AZURE_SUBSCRIPTION_ID"]
    client = MonitorManagementClient(DefaultAzureCredential(), subscription_id)
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    query = f"eventTimestamp ge '{since}' and resourceUri eq '{resource_id}'"

    changes = []
    for entry in client.activity_logs.list(filter=query):
        changes.append(
            {
                "timestamp": str(entry.event_timestamp),
                "operation": getattr(entry.operation_name, "value", ""),
                "status": getattr(entry.status, "value", ""),
                "caller": entry.caller,
                "target_resource_id": entry.resource_id,
                "correlation_hint": "",
            }
        )
    return {"changes": changes, "count": len(changes), "window_hours": hours, "mode": "live"}
