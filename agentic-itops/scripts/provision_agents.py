"""Provision the prompt-hosted agents into an Azure AI Foundry project.

    python scripts/provision_agents.py --dry-run     # show what would happen
    python scripts/provision_agents.py               # create or update
    python scripts/provision_agents.py --agent 04-runbook-memory

Reads each ``agents/*/agent.yaml`` plus its ``instructions.md`` and creates
the agent in the configured project. Prints the environment variable
assignments to export afterwards.

Requires:
    AZURE_AI_FOUNDRY_PROJECT_ENDPOINT
    pip install -r requirements-live.txt
and an identity with Azure AI Developer on the project.

Connected agents are created in dependency order: specialists first, then
the orchestrator that references them.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "agents"

#: Specialists before the orchestrator that binds them.
ORDER = [
    "02-triage-dedup",
    "03-rca-investigator",
    "04-runbook-memory",
    "05-remediation-planner",
    "07-incident-comms",
    "08-postmortem-drafter",
    "09-alert-hygiene",
    "10-industry-signal-pack",
    "00-ops-orchestrator",
]


def load_definition(folder: str) -> tuple[dict, str]:
    directory = AGENTS / folder
    manifest = yaml.safe_load((directory / "agent.yaml").read_text(encoding="utf-8"))
    instructions_file = manifest.get("instructions_file", "instructions.md")
    instructions = (directory / instructions_file).read_text(encoding="utf-8")
    return manifest, instructions


def build_tools(manifest: dict, created: dict[str, str]) -> list[dict]:
    """Translate the manifest's tool block into Foundry tool definitions.

    Kept explicit rather than clever: the mapping between our manifest
    vocabulary and the SDK's is exactly the kind of thing that should be
    readable when it breaks.
    """
    tools: list[dict] = []

    for tool in manifest.get("tools") or []:
        kind = tool.get("type")
        if kind == "openapi":
            spec_path = (AGENTS / "_" / tool["spec"]).resolve()
            if not spec_path.exists():
                spec_path = (ROOT / tool["spec"].replace("../../", "")).resolve()
            tools.append({
                "type": "openapi",
                "openapi": {
                    "name": tool["name"],
                    "spec": yaml.safe_load(spec_path.read_text(encoding="utf-8")),
                    "auth": {"type": tool.get("auth", "managed_identity")},
                },
            })
        elif kind == "azure_ai_search":
            tools.append({
                "type": "azure_ai_search",
                "azure_ai_search": {
                    "index_name": tool.get("index"),
                    "query_type": tool.get("query_type", "vector_semantic_hybrid"),
                    "top_k": tool.get("top_k", 3),
                },
            })
        elif kind == "mcp":
            url = os.path.expandvars(str(tool.get("server_url", "")))
            if not url or url.startswith("${"):
                print(f"    skipping MCP tool '{tool.get('name')}': server_url not set")
                continue
            tools.append({
                "type": "mcp",
                "mcp": {"server_label": tool["server_label"], "server_url": url},
            })

    for name in manifest.get("connected_agents") or []:
        agent_id = created.get(name)
        if not agent_id:
            print(f"    warning: connected agent '{name}' not created yet; skipping binding")
            continue
        tools.append({
            "type": "connected_agent",
            "connected_agent": {"id": agent_id, "name": name},
        })

    return tools


def main() -> int:
    parser = argparse.ArgumentParser(description="Provision Foundry agents")
    parser.add_argument("--agent", help="provision one agent folder only")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    folders = [args.agent] if args.agent else ORDER
    created: dict[str, str] = {}

    if args.dry_run:
        for folder in folders:
            manifest, instructions = load_definition(folder)
            tool_types = [t.get("type") for t in (manifest.get("tools") or [])]
            print(f"\n{folder}")
            print(f"  name:         {manifest['name']}")
            print(f"  model:        {manifest['model']}")
            print(f"  temperature:  {manifest.get('temperature')}")
            print(f"  tools:        {tool_types or '(none)'}")
            print(f"  connected:    {manifest.get('connected_agents') or '(none)'}")
            print(f"  instructions: {len(instructions)} chars")
            print(f"  roles:        {manifest.get('identity', {}).get('required_roles')}")
        print("\nDry run only. Nothing was created.")
        return 0

    endpoint = os.environ.get("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        print("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT is not set. See .env.example.")
        return 1

    try:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential
    except ImportError:
        print("Missing SDKs. Run: pip install -r requirements-live.txt")
        return 1

    client = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())

    for folder in folders:
        manifest, instructions = load_definition(folder)
        print(f"\n{folder} -> {manifest['name']}")
        agent = client.agents.create_agent(
            model=manifest["model"],
            name=manifest["name"],
            description=manifest.get("description", "").strip(),
            instructions=instructions,
            tools=build_tools(manifest, created),
            temperature=manifest.get("temperature", 0.2),
            top_p=manifest.get("top_p", 0.95),
        )
        created[manifest["name"]] = agent.id
        print(f"  created {agent.id}")

    print("\nExport these:")
    for name, agent_id in created.items():
        print(f"  export AGENT_ID_{name.replace('-', '_').upper()}={agent_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
