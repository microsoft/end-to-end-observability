"""Log Analytics query tool (stubbed).

Bound to the RCA Investigator agent. In stub mode, queries are matched
against fixtures by intent keyword rather than executed -- enough to
drive a believable hypothesis loop offline.

Live mode requires:
    AGENTIC_ITOPS_LIVE_AZURE=1
    AZURE_LOG_ANALYTICS_WORKSPACE_ID=<guid>
plus an identity with Log Analytics Reader. See .env.example.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parents[2] / "data" / "fixtures"

#: Ordered intent patterns. First match wins, so specific before general.
_INTENTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("exceptions", (r"exception", r"\berror\b", r"traces\b.*severitylevel", r"stacktrace")),
    ("restarts", (r"containerrestart", r"restart", r"kubepodinventory", r"oomkilled")),
    ("deployments", (r"deployment", r"releaseannotation", r"azureactivity.*write")),
    ("cpu", (r"percentage cpu", r"cpu", r"processor")),
    ("memory", (r"memory", r"availablemb", r"workingset")),
    ("disk", (r"disk", r"freespace", r"logicaldisk")),
    ("network", (r"network", r"bytesreceived", r"bytessent", r"tcp")),
    ("requests", (r"requests\b", r"resultcode", r"httprequest", r"5\d\d")),
    ("dependencies", (r"dependenc", r"downstream", r"outbound call")),
    ("iot", (r"iot", r"telemetry", r"device", r"sensor", r"pump", r"flow")),
)


def run_kql(query: str, timespan: str = "PT1H", workspace_id: str | None = None) -> dict[str, Any]:
    """Execute a KQL query against Log Analytics.

    Args:
        query: The KQL text.
        timespan: ISO-8601 duration, e.g. ``PT1H`` or ``P1D``.
        workspace_id: Overrides the configured workspace.

    Returns:
        ``{"columns": [...], "rows": [[...]], "row_count": n, "mode": "stub"|"live"}``
    """
    if _live():
        return _run_live(query, timespan, workspace_id)
    return _run_stub(query, timespan)


def _live() -> bool:
    return os.getenv("AGENTIC_ITOPS_LIVE_AZURE", "").strip() in ("1", "true", "yes")


def _run_stub(query: str, timespan: str) -> dict[str, Any]:
    intent = _classify_query(query)
    fixture = FIXTURES / f"kql_{intent}.json"
    if not fixture.exists():
        return {
            "columns": [],
            "rows": [],
            "row_count": 0,
            "mode": "stub",
            "intent": intent,
            "note": (
                f"no fixture for intent '{intent}'. Add data/fixtures/kql_{intent}.json "
                f"to extend the offline demo."
            ),
            "query": query,
            "timespan": timespan,
        }
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    payload.update({"mode": "stub", "intent": intent, "query": query, "timespan": timespan})
    payload["row_count"] = len(payload.get("rows", []))
    return payload


def _classify_query(query: str) -> str:
    haystack = (query or "").lower()
    for intent, patterns in _INTENTS:
        for pattern in patterns:
            if re.search(pattern, haystack):
                return intent
    return "generic"


def _run_live(query: str, timespan: str, workspace_id: str | None) -> dict[str, Any]:
    """Real Log Analytics call.

    Kept import-local so the stub path has no Azure SDK dependency --
    the repo must stay installable with nothing but PyYAML.
    """
    from azure.identity import DefaultAzureCredential  # noqa: PLC0415
    from azure.monitor.query import LogsQueryClient, LogsQueryStatus  # noqa: PLC0415

    workspace = workspace_id or os.environ["AZURE_LOG_ANALYTICS_WORKSPACE_ID"]
    client = LogsQueryClient(DefaultAzureCredential())
    response = client.query_workspace(workspace_id=workspace, query=query, timespan=timespan)

    if response.status == LogsQueryStatus.FAILURE:
        return {"columns": [], "rows": [], "row_count": 0, "mode": "live", "error": str(response)}

    table = response.tables[0] if response.tables else None
    if table is None:
        return {"columns": [], "rows": [], "row_count": 0, "mode": "live"}
    return {
        "columns": list(table.columns),
        "rows": [list(row) for row in table.rows],
        "row_count": len(table.rows),
        "mode": "live",
        "query": query,
        "timespan": timespan,
    }
