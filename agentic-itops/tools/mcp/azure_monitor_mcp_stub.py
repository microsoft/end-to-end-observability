"""Minimal MCP server exposing the read-only observability tools.

Why an MCP stub exists alongside the OpenAPI specs
--------------------------------------------------
Foundry can bind tools either way, and the choice is not arbitrary:

* **OpenAPI** for the code-hosted skills we own (normalizer, safety
  gate, hygiene). They are our endpoints, the contract is ours, and an
  OpenAPI spec is the most direct binding.
* **MCP** for read-only access to someone else's system -- Azure
  Monitor, a customer's ITSM, a vendor platform. MCP is where the
  ecosystem is converging for that, and a customer who already runs an
  MCP server for their observability stack can point us at it instead of
  us writing another adapter.

This file is a **stub**. It speaks MCP's shape over stdio and answers
from the same fixtures as the rest of the offline path, so agents can be
wired and tested before any Azure resource exists. It is not a
production server and deliberately exposes nothing that mutates.

Run:  python tools/mcp/azure_monitor_mcp_stub.py
Then bind in agent.yaml as:

    - type: mcp
      server_label: azure-monitor
      server_url: <your transport endpoint>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agentic_itops.tools import (  # noqa: E402
    get_recent_changes,
    get_resource_context,
    run_kql,
    search_knowledge,
)

PROTOCOL_VERSION = "2024-11-05"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "run_kql",
        "description": (
            "Run a read-only KQL query against Log Analytics. Returns columns "
            "and rows. An empty result is a real answer -- report it rather "
            "than inventing rows."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "timespan": {"type": "string", "default": "PT1H"},
            },
        },
    },
    {
        "name": "get_resource_context",
        "description": (
            "Resource metadata, dependencies, ownership and criticality. Check "
            "criticality before proposing any action."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["resource_id"],
            "properties": {"resource_id": {"type": "string"}},
        },
    },
    {
        "name": "get_recent_changes",
        "description": (
            "Deployments and configuration changes touching a resource or "
            "service. Call this before querying telemetry."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "resource_id": {"type": "string"},
                "service": {"type": "string"},
                "hours": {"type": "integer", "default": 24},
            },
        },
    },
    {
        "name": "search_knowledge",
        "description": (
            "Search runbooks and postmortems. Returns documented_actions for "
            "runbooks. If nothing matches, that is the answer -- do not invent "
            "a runbook."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer", "default": 3},
                "kind": {"type": "string", "enum": ["runbook", "postmortem", ""]},
            },
        },
    },
]

HANDLERS = {
    "run_kql": lambda a: run_kql(a["query"], a.get("timespan", "PT1H")),
    "get_resource_context": lambda a: get_resource_context(a["resource_id"]),
    "get_recent_changes": lambda a: get_recent_changes(
        a.get("resource_id", ""), a.get("service", ""), a.get("hours", 24)
    ),
    "search_knowledge": lambda a: search_knowledge(
        a["query"], a.get("top_k", 3), a.get("kind", "")
    ),
}


def handle(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")

    if method == "initialize":
        return _ok(request_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "agentic-itops-azure-monitor-stub", "version": "0.1.0"},
        })

    if method == "tools/list":
        return _ok(request_id, {"tools": TOOLS})

    if method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name", "")
        handler = HANDLERS.get(name)
        if handler is None:
            return _err(request_id, -32601, f"unknown tool: {name}")
        try:
            result = handler(params.get("arguments") or {})
        except Exception as exc:  # surface tool failure as content, not transport error
            return _ok(request_id, {
                "content": [{"type": "text", "text": f"tool error: {exc}"}],
                "isError": True,
            })
        return _ok(request_id, {
            "content": [{"type": "text", "text": json.dumps(result, default=str)}],
            "isError": False,
        })

    if method and method.startswith("notifications/"):
        return None  # notifications take no response

    return _err(request_id, -32601, f"unknown method: {method}")


def _ok(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _err(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            sys.stdout.write(json.dumps(_err(None, -32700, "parse error")) + "\n")
            sys.stdout.flush()
            continue
        response = handle(request)
        if response is not None:
            sys.stdout.write(json.dumps(response, default=str) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
