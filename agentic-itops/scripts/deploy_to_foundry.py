"""Deploy the Agentic ITOps agents into a live Azure AI Foundry project.

Two deliberate differences from ``provision_agents.py``:

**1. Knowledge uses Foundry's built-in vector store**, bound via
``file_search``, rather than Azure AI Search. The corpus is eleven
markdown documents. A dedicated Search service is a separate resource, a
separate bill, a separate identity grant and -- as it turned out in
eastus2 -- a separate capacity constraint. Foundry's own vector store is
co-located and free with the account. Swap to Azure AI Search when the
corpus is a customer's real runbook estate and hybrid semantic ranking
starts to earn its cost.

**2. It talks to the OpenAI-compatible surface** via
``AIProjectClient.get_openai_client()`` rather than the preview
``azure-ai-agents`` operations. That preview package's surface has moved
between releases; the OpenAI-compatible API is stable and is what the
Foundry portal shows.

    python scripts/deploy_to_foundry.py --endpoint <project-endpoint>
    python scripts/deploy_to_foundry.py --endpoint <...> --smoke-test
    python scripts/deploy_to_foundry.py --endpoint <...> --cleanup
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "agents"
DATA = ROOT / "data"
STATE = ROOT / ".foundry-agents.json"

# Specialists first; the orchestrator binds them afterwards.
ORDER = [
    "04-runbook-memory",
    "03-rca-investigator",
    "05-remediation-planner",
    "07-incident-comms",
    "00-ops-orchestrator",
]


def load(folder: str) -> tuple[dict, str]:
    directory = AGENTS / folder
    manifest = yaml.safe_load((directory / "agent.yaml").read_text(encoding="utf-8"))
    instructions = (directory / manifest.get("instructions_file", "instructions.md")).read_text(
        encoding="utf-8"
    )
    return manifest, instructions


def get_clients(endpoint: str):
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    project = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential())
    return project, project.get_openai_client()


def upload_knowledge(openai_client, reuse: str = "") -> str:
    """Index the runbook and postmortem corpus into a Foundry vector store."""
    if reuse:
        print(f"  reusing vector store {reuse}")
        return reuse

    paths = sorted(DATA.glob("runbooks/*.md")) + sorted(DATA.glob("postmortems/*.md"))
    if not paths:
        raise SystemExit("no knowledge documents under data/")

    file_ids = []
    for path in paths:
        with path.open("rb") as handle:
            uploaded = openai_client.files.create(file=handle, purpose="assistants")
        file_ids.append(uploaded.id)
        print(f"    {path.name}")

    store = openai_client.vector_stores.create(
        name="agentic-itops-knowledge", file_ids=file_ids
    )

    # Indexing is asynchronous. Binding a store before it is ready gives
    # an agent that silently returns nothing and looks broken.
    for _ in range(60):
        status = openai_client.vector_stores.retrieve(store.id)
        counts = status.file_counts
        if counts.in_progress == 0:
            print(
                f"  vector store {store.id}: {counts.completed} indexed, {counts.failed} failed"
            )
            break
        time.sleep(2)
    return store.id


def deploy(project, oai, model: str, endpoint: str) -> int:
    from azure.ai.projects.models import FileSearchTool, PromptAgentDefinition

    print("Uploading knowledge corpus...")
    vector_store_id = upload_knowledge(oai, os.getenv("REUSE_VECTOR_STORE", ""))

    print("\nCreating agents...")
    created: dict[str, str] = {}
    for folder in ORDER:
        manifest, instructions = load(folder)
        tools = []

        # Only agent #4 gets the knowledge store. Giving every agent
        # search access would let the planner quietly re-derive its own
        # view of the runbooks instead of using the retriever's output.
        if any(t.get("type") == "azure_ai_search" for t in (manifest.get("tools") or [])):
            tools.append(FileSearchTool(vector_store_ids=[vector_store_id], max_num_results=5))

        version = project.agents.create_version(
            agent_name=manifest["name"],
            definition=PromptAgentDefinition(
                model=model,
                instructions=instructions,
                temperature=manifest.get("temperature", 0.2),
                tools=tools or None,
            ),
            description=(manifest.get("description") or "").strip()[:512],
        )
        created[manifest["name"]] = version.name
        print(f"  {manifest['name']:<22} v{version.version}  ({len(tools)} tool(s))")

    STATE.write_text(
        json.dumps(
            {"endpoint": endpoint, "vector_store": vector_store_id, "agents": created}, indent=2
        ),
        encoding="utf-8",
    )
    print(f"\nwrote {STATE.name}")
    return 0


def cleanup(project, oai) -> int:
    if not STATE.exists():
        print("nothing to clean up")
        return 0
    state = json.loads(STATE.read_text(encoding="utf-8"))
    for name in (state.get("agents") or {}):
        try:
            project.agents.delete(name)
            print(f"  deleted agent {name}")
        except Exception as exc:
            print(f"  could not delete {name}: {exc}")
    if state.get("vector_store"):
        try:
            oai.vector_stores.delete(state["vector_store"])
            print("  deleted vector store")
        except Exception as exc:
            print(f"  could not delete vector store: {exc}")
    STATE.unlink(missing_ok=True)
    return 0


def smoke_test(oai) -> int:
    """Ask the memory agent a real question and print what it says."""
    if not STATE.exists():
        print("no .foundry-agents.json; deploy first")
        return 1
    state = json.loads(STATE.read_text(encoding="utf-8"))
    agent_name = state["agents"]["runbook-memory"]

    question = (
        "Incident: sustained CPU saturation on vm-pump-ctrl-07, service "
        "rg-refinery-midstream, symptom_class saturation, environment prod. "
        "Have we seen this before, and what does the documentation prescribe?"
    )
    print(f"Q: {question}\n")

    response = oai.responses.create(
        input=question,
        extra_body={"agent_reference": {"type": "agent_reference", "name": agent_name}},
    )
    print("A:", response.output_text)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default=os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT"))
    parser.add_argument("--model", default="gpt-4o")
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--smoke-test", action="store_true")
    args = parser.parse_args()

    if not args.endpoint:
        print("--endpoint or AZURE_AI_FOUNDRY_PROJECT_ENDPOINT required")
        return 1

    _project, oai = get_clients(args.endpoint)

    if args.cleanup:
        return cleanup(_project, oai)
    if args.smoke_test:
        return smoke_test(oai)
    return deploy(_project, oai, args.model, args.endpoint)


if __name__ == "__main__":
    sys.exit(main())

