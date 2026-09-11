"""Azure Resource Graph context tool (stubbed).

Answers "what is this thing, what does it depend on, and who owns it?"
That context is what separates a useful root-cause hypothesis from a
restatement of the alert.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parents[2] / "data" / "fixtures"


def get_resource_context(resource_id: str) -> dict[str, Any]:
    """Return metadata, tags, dependencies and ownership for a resource.

    Args:
        resource_id: ARM resource id, or a bare resource name.

    Returns:
        Resource context including ``dependencies`` (upstream and
        downstream) and ``owner``. ``found`` is False when unknown.
    """
    if _live():
        return _get_live(resource_id)
    return _get_stub(resource_id)


def _live() -> bool:
    return os.getenv("AGENTIC_ITOPS_LIVE_AZURE", "").strip() in ("1", "true", "yes")


def _get_stub(resource_id: str) -> dict[str, Any]:
    fixture = FIXTURES / "resource_graph.json"
    if not fixture.exists():
        return {"found": False, "resource_id": resource_id, "mode": "stub"}
    catalog = json.loads(fixture.read_text(encoding="utf-8"))

    needle = (resource_id or "").strip().lower()
    for entry in catalog.get("resources", []):
        entry_id = str(entry.get("id", "")).lower()
        entry_name = str(entry.get("name", "")).lower()
        if needle and (needle == entry_id or needle == entry_name or needle.endswith("/" + entry_name)):
            return {**entry, "found": True, "mode": "stub"}

    return {
        "found": False,
        "resource_id": resource_id,
        "mode": "stub",
        "note": "not in the sample catalog; add it to data/fixtures/resource_graph.json",
    }


def _get_live(resource_id: str) -> dict[str, Any]:
    from azure.identity import DefaultAzureCredential  # noqa: PLC0415
    from azure.mgmt.resourcegraph import ResourceGraphClient  # noqa: PLC0415
    from azure.mgmt.resourcegraph.models import QueryRequest  # noqa: PLC0415

    client = ResourceGraphClient(DefaultAzureCredential())
    query = (
        "Resources | where id =~ '{rid}' "
        "| project id, name, type, location, resourceGroup, subscriptionId, tags"
    ).format(rid=resource_id.replace("'", "''"))
    result = client.resources(QueryRequest(query=query))
    rows = result.data or []
    if not rows:
        return {"found": False, "resource_id": resource_id, "mode": "live"}
    return {**rows[0], "found": True, "mode": "live"}
