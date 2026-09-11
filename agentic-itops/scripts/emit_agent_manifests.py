"""Emit agent.yaml for the prompt-hosted agents.

Keeping these generated from one place means the identity block, model
settings and tool bindings stay consistent. Divergence between agent
definitions is how a portfolio ends up with one agent quietly holding
more permission than anyone intended.

Run:  python scripts/emit_agent_manifests.py
"""

from __future__ import annotations

from pathlib import Path

import yaml

AGENTS = Path(__file__).resolve().parents[1] / "agents"

COMMON = {
    "model": "gpt-4o",
    "temperature": 0.2,
    "top_p": 0.95,
    "instructions_file": "instructions.md",
}

READER_IDENTITY = {
    "type": "user_assigned_managed_identity",
    "required_roles": ["Monitoring Reader", "Reader"],
}

TOOL = {
    "log_analytics": {
        "type": "openapi",
        "name": "log_analytics",
        "spec": "../../tools/openapi/log-analytics.yaml",
        "auth": "managed_identity",
    },
    "resource_graph": {
        "type": "openapi",
        "name": "resource_graph",
        "spec": "../../tools/openapi/resource-graph.yaml",
        "auth": "managed_identity",
    },
    "change_history": {
        "type": "openapi",
        "name": "change_history",
        "spec": "../../tools/openapi/change-history.yaml",
        "auth": "managed_identity",
    },
    "knowledge": {
        "type": "azure_ai_search",
        "name": "search_knowledge",
        "index": "agentic-itops-knowledge",
        "query_type": "vector_semantic_hybrid",
        "top_k": 3,
    },
    "azure_monitor_mcp": {
        "type": "mcp",
        "name": "azure_monitor",
        "server_label": "azure-monitor",
        "server_url": "${AZURE_MONITOR_MCP_URL}",
        "auth": "managed_identity",
        "note": "stub server in tools/mcp/azure_monitor_mcp_stub.py",
    },
}

AGENT_SPECS: dict[str, dict] = {
    "02-triage-dedup": {
        "name": "triage-dedup",
        "display_name": "Triage and Deduplication",
        "description": (
            "Clusters normalized signals into a single incident, applies "
            "business-context severity, and suppresses known-noise patterns."
        ),
        "tools": [TOOL["resource_graph"]],
        "output_schema": {
            "type": "object",
            "required": ["incident_fingerprint", "adjusted_severity", "suppress", "reasoning"],
            "properties": {
                "incident_fingerprint": {"type": "string"},
                "adjusted_severity": {
                    "type": "string",
                    "enum": ["critical", "high", "medium", "low", "info"],
                },
                "severity_changed_because": {"type": "string"},
                "suppress": {"type": "boolean"},
                "reasoning": {"type": "string"},
            },
        },
    },
    "03-rca-investigator": {
        "name": "rca-investigator",
        "display_name": "RCA Investigator",
        "description": (
            "Forms ranked, evidence-backed root cause hypotheses using change "
            "history, telemetry and resource topology."
        ),
        "tools": [
            TOOL["change_history"],
            TOOL["log_analytics"],
            TOOL["resource_graph"],
            TOOL["azure_monitor_mcp"],
        ],
        "output_schema": {
            "type": "object",
            "required": ["hypotheses"],
            "properties": {
                "hypotheses": {"type": "array"},
                "timeline": {"type": "array"},
                "notable_absence": {"type": "string"},
            },
        },
    },
    "04-runbook-memory": {
        "name": "runbook-memory",
        "display_name": "Runbook and Institutional Memory",
        "description": (
            "Answers whether this has been seen before, what was done about it, "
            "and what the documentation prohibits."
        ),
        "tools": [TOOL["knowledge"]],
        "output_schema": {
            "type": "object",
            "required": ["runbooks", "postmortems"],
            "properties": {
                "runbooks": {"type": "array"},
                "postmortems": {"type": "array"},
                "coverage_gap": {"type": "string"},
            },
        },
    },
    "05-remediation-planner": {
        "name": "remediation-planner",
        "display_name": "Remediation Planner",
        "description": (
            "Turns a diagnosis into a concrete, reviewable plan with blast radius "
            "and rollback. Proposes only; never authorizes."
        ),
        "tools": [TOOL["knowledge"], TOOL["resource_graph"]],
        "output_schema": {
            "type": "object",
            "required": ["proposed_action", "target_resource_id", "classification", "rollback"],
            "properties": {
                "proposed_action": {"type": "string"},
                "target_resource_id": {"type": "string"},
                "source_runbook": {"type": "string"},
                "classification": {"type": "string", "enum": ["mitigation", "fix"]},
                "blast_radius": {"type": "object"},
                "preconditions": {"type": "array"},
                "rollback": {"type": "string"},
                "expected_outcome": {"type": "string"},
                "follow_up": {"type": "string"},
                "rationale": {"type": "string"},
            },
        },
    },
    "07-incident-comms": {
        "name": "incident-comms",
        "display_name": "Incident Communications",
        "description": "Drafts audience-appropriate incident updates. Drafts only; never sends.",
        "tools": [],
        "temperature": 0.4,
        "output_schema": {
            "type": "object",
            "required": ["engineer_update", "executive_update"],
            "properties": {
                "engineer_update": {"type": "string"},
                "executive_update": {"type": "string"},
                "customer_update": {"type": "string"},
                "withheld": {"type": "array"},
            },
        },
    },
    "08-postmortem-drafter": {
        "name": "postmortem-drafter",
        "display_name": "Postmortem Drafter",
        "description": (
            "Drafts a blameless postmortem from the incident record and writes it "
            "back to the knowledge index, closing the learning loop."
        ),
        "tools": [TOOL["knowledge"], TOOL["change_history"]],
        "temperature": 0.3,
        "output_schema": {
            "type": "object",
            "required": ["summary", "timeline", "contributing_factors", "actions"],
            "properties": {
                "summary": {"type": "string"},
                "timeline": {"type": "array"},
                "contributing_factors": {"type": "array"},
                "what_made_it_slow": {"type": "string"},
                "actions": {"type": "array"},
                "lesson": {"type": "string"},
            },
        },
    },
    "09-alert-hygiene": {
        "name": "alert-hygiene",
        "display_name": "Alert Hygiene Analyst",
        "description": (
            "Turns the computed noise backlog into customer-readable "
            "recommendations. Deliverable inside an ADR, before any security gate."
        ),
        "tools": [
            {
                "type": "openapi",
                "name": "alert_hygiene",
                "spec": "../../tools/openapi/alert-hygiene.yaml",
                "auth": "managed_identity",
            }
        ],
        "output_schema": {
            "type": "object",
            "required": ["executive_summary", "recommendations"],
            "properties": {
                "executive_summary": {"type": "string"},
                "estimated_volume_reduction_pct": {"type": "number"},
                "recommendations": {"type": "array"},
                "do_not_touch": {"type": "array"},
            },
        },
    },
    "10-industry-signal-pack": {
        "name": "industry-signal-pack",
        "display_name": "Industry Signal Pack",
        "description": (
            "Parameterized vertical context: what critical means, which control "
            "framework applies, which assets are safety-critical. One skill, many configs."
        ),
        "tools": [TOOL["knowledge"]],
        "parameters": {
            "industry": {
                "type": "string",
                "enum": [
                    "energy_utilities",
                    "manufacturing",
                    "financial_services",
                    "healthcare",
                    "retail_supply_chain",
                    "public_sector",
                ],
                "default": "energy_utilities",
            }
        },
        "output_schema": {
            "type": "object",
            "required": ["industry", "criticality_adjustment", "control_frameworks"],
            "properties": {
                "industry": {"type": "string"},
                "criticality_adjustment": {"type": "string"},
                "control_frameworks": {"type": "array"},
                "safety_critical_asset_classes": {"type": "array"},
                "additional_prohibitions": {"type": "array"},
            },
        },
    },
}


def main() -> None:
    for folder, spec in AGENT_SPECS.items():
        manifest = {**COMMON, **spec, "identity": READER_IDENTITY}
        # Put the descriptive keys first for readability.
        ordered = {
            k: manifest[k]
            for k in ("name", "display_name", "model", "temperature", "top_p", "description")
            if k in manifest
        }
        ordered.update({k: v for k, v in manifest.items() if k not in ordered})

        path = AGENTS / folder / "agent.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(ordered, sort_keys=False, width=88, default_flow_style=False),
            encoding="utf-8",
        )
        print(f"  wrote {path.relative_to(AGENTS.parent)}")


if __name__ == "__main__":
    main()
