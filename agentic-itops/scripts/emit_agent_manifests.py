"""Emit exactly eight prompt manifests; 00 remains separately authored.

Metadata/tools live here; all nine output contracts live in portfolio_schemas.
Run ``python scripts\\emit_agent_manifests.py --check`` to detect source drift.
Rebuild with no flags, then run the all-nine compiler and its targeted tests.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentic_itops.portfolio_schemas import INDUSTRIES, output_schema

AGENTS = ROOT / "agents"
COMMON = {
    "model": "gpt-4o", "temperature": 0.2, "top_p": 0.95,
    "instructions_file": "instructions.md",
}
READER_IDENTITY = {
    "type": "user_assigned_managed_identity",
    "required_roles": ["Monitoring Reader", "Reader"],
}
TOOL = {
    name: {
        "type": "openapi", "name": name,
        "spec": f"../../tools/openapi/{filename}.yaml", "auth": "managed_identity",
    }
    for name, filename in (
        ("log_analytics", "log-analytics"), ("resource_graph", "resource-graph"),
        ("change_history", "change-history"), ("alert_hygiene", "alert-hygiene"),
    )
}
TOOL.update({
    "knowledge": {
        "type": "azure_ai_search", "name": "search_knowledge",
        "index": "agentic-itops-knowledge", "query_type": "vector_semantic_hybrid", "top_k": 3,
    },
    "azure_monitor_mcp": {
        "type": "mcp", "name": "azure_monitor", "server_label": "azure-monitor",
        "server_url": "${AZURE_MONITOR_MCP_URL}", "auth": "managed_identity",
        "note": "stub server in tools/mcp/azure_monitor_mcp_stub.py",
    },
})
AGENT_SPECS = {
    "02-triage-dedup": {
        "name": "triage-dedup", "display_name": "Triage and Deduplication",
        "description": (
            "Clusters normalized signals into a single incident, applies "
            "business-context severity, and suppresses known-noise patterns."
        ),
        "tools": [TOOL["resource_graph"]],
    },
    "03-rca-investigator": {
        "name": "rca-investigator", "display_name": "RCA Investigator",
        "description": (
            "Forms ranked, evidence-backed root cause hypotheses using change "
            "history, telemetry and resource topology."
        ),
        "tools": [TOOL["change_history"], TOOL["log_analytics"], TOOL["resource_graph"],
                  TOOL["azure_monitor_mcp"]],
    },
    "04-runbook-memory": {
        "name": "runbook-memory", "display_name": "Runbook and Institutional Memory",
        "description": (
            "Answers whether this has been seen before, what was done about it, "
            "and what the documentation prohibits."
        ),
        "tools": [TOOL["knowledge"]],
    },
    "05-remediation-planner": {
        "name": "remediation-planner", "display_name": "Remediation Planner",
        "description": (
            "Turns a diagnosis into a concrete, reviewable plan with blast radius "
            "and rollback. Proposes only; never authorizes."
        ),
        "tools": [TOOL["knowledge"], TOOL["resource_graph"]],
    },
    "07-incident-comms": {
        "name": "incident-comms", "display_name": "Incident Communications",
        "description": "Drafts audience-appropriate incident updates. Drafts only; never sends.",
        "tools": [], "temperature": 0.4,
    },
    "08-postmortem-drafter": {
        "name": "postmortem-drafter", "display_name": "Postmortem Drafter",
        "description": (
            "Drafts a blameless postmortem from the incident record. Publication "
            "requires separate review and a dedicated publisher identity."
        ),
        "tools": [TOOL["knowledge"], TOOL["change_history"]], "temperature": 0.3,
    },
    "09-alert-hygiene": {
        "name": "alert-hygiene", "display_name": "Alert Hygiene Analyst",
        "description": (
            "Turns the computed noise backlog into customer-readable "
            "recommendations. Deliverable inside an ADR, before any security gate."
        ),
        "tools": [TOOL["alert_hygiene"]],
    },
    "10-industry-signal-pack": {
        "name": "industry-signal-pack", "display_name": "Industry Signal Pack",
        "description": (
            "Parameterized vertical context: what critical means, which control "
            "framework applies, which assets are safety-critical. One skill, many configs."
        ),
        "tools": [TOOL["knowledge"]],
        "parameters": {
            "industry": {"type": "string", "enum": list(INDUSTRIES), "default": "energy_utilities"},
        },
    },
}


def generated_manifest(folder: str) -> dict:
    """Return the source-authoritative manifest for one of the eight generated agents."""
    spec = AGENT_SPECS[folder]
    manifest = deepcopy({
        **COMMON, **spec, "identity": READER_IDENTITY, "output_schema": output_schema(spec["name"]),
    })
    ordered = {key: manifest[key] for key in
               ("name", "display_name", "model", "temperature", "top_p", "description")}
    ordered.update({key: value for key, value in manifest.items() if key not in ordered})
    return ordered


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="check eight generated manifests without writing")
    args = parser.parse_args()
    for folder in AGENT_SPECS:
        manifest = generated_manifest(folder)
        path = AGENTS / folder / "agent.yaml"
        if args.check:
            if not path.exists() or yaml.safe_load(path.read_text(encoding="utf-8")) != manifest:
                raise SystemExit(f"manifest drift: {path.relative_to(ROOT)}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(manifest, sort_keys=False, width=88), encoding="utf-8")
        print(f"  wrote {path.relative_to(ROOT)}")
    if args.check:
        print(f"All {len(AGENT_SPECS)} generated manifests match; 00 is separately authored.")


if __name__ == "__main__":
    main()
