"""Offline SDK serialization probe; never authenticates or calls Azure."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentic_itops.foundry_contracts import AgentVersionReference


def probe() -> dict:
    import httpx
    from openai import OpenAI
    from azure.ai.projects.models import (
        A2ATool,
        AISearchIndexResource,
        AzureAISearchTool,
        AzureAISearchToolResource,
        MCPTool,
        OpenApiFunctionDefinition,
        OpenApiManagedAuthDetails,
        OpenApiManagedSecurityScheme,
        OpenApiTool,
        PromptAgentDefinition,
        PromptAgentDefinitionTextOptions,
        TextResponseFormatJsonSchema,
    )

    versions = {name: importlib.metadata.version(name) for name in
                ("azure-ai-projects", "openai", "azure-identity")}
    expected = {"azure-ai-projects": "2.6.1", "openai": "3.8.0", "azure-identity": "1.25.3"}
    if versions != expected:
        raise RuntimeError(f"SDK lock mismatch: expected {expected}, got {versions}")
    schema = {
        "type": "object",
        "properties": {"status": {"type": "string", "enum": ["insufficient_evidence"]}},
        "required": ["status"],
        "additionalProperties": False,
    }
    tools = [
        A2ATool(base_url="https://gateway.invalid/a2a/memory",
                project_connection_id="probe-a2a", a2a_version="1.0"),
        AzureAISearchTool(azure_ai_search=AzureAISearchToolResource(indexes=[
            AISearchIndexResource(project_connection_id="probe-search",
                                 index_name="agentic-itops-knowledge",
                                 query_type="vector_semantic_hybrid", top_k=3,
                                 filter="synthetic eq true and approved eq true")
        ])),
        OpenApiTool(openapi=OpenApiFunctionDefinition(
            name="probe_read",
            spec={
                "openapi": "3.0.3", "info": {"title": "Read-only probe", "version": "1"},
                "servers": [{"url": "https://tools.invalid"}],
                "paths": {"/status": {"get": {"operationId": "getStatus",
                          "responses": {"200": {"description": "Status"}}}}},
            },
            auth=OpenApiManagedAuthDetails(
                security_scheme=OpenApiManagedSecurityScheme(audience="api://probe-tools")),
        )),
        MCPTool(server_label="probe", server_url="https://tools.invalid/mcp",
                project_connection_id="probe-mcp", allowed_tools=["query_observations"],
                require_approval="always"),
    ]
    definition = PromptAgentDefinition(
        model="gpt-4o", instructions="Offline schema probe; no operation is authorized.",
        temperature=0.2, top_p=0.95, tools=tools,
        text=PromptAgentDefinitionTextOptions(format=TextResponseFormatJsonSchema(
            name="probe_result", schema=schema, strict=True)),
    ).as_dict()
    assert [tool["type"] for tool in definition["tools"]] == [
        "a2a", "azure_ai_search", "openapi", "mcp"]
    assert definition["text"]["format"]["schema"] == schema
    assert definition["top_p"] == 0.95
    captured = []

    def handle(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "resp_offline_probe", "object": "response", "created_at": 0,
            "model": "gpt-4o", "status": "completed", "output": [],
        })

    with httpx.Client(transport=httpx.MockTransport(handle)) as transport:
        with OpenAI(api_key="offline-probe-not-a-credential", base_url="https://probe.invalid/openai/v1",
                    http_client=transport, max_retries=0) as client:
            client.responses.create(
                input="Offline transport probe",
                extra_body=AgentVersionReference("ops-orchestrator", "1").extra_body(),
                max_output_tokens=128, max_tool_calls=1, parallel_tool_calls=False,
            )
    assert len(captured) == 1
    assert captured[0]["agent_reference"]["version"] == "1"
    assert captured[0]["agent_reference"]["name"] == "ops-orchestrator"
    assert captured[0]["max_tool_calls"] == 1
    return {
        "scope": "offline SDK construction and HTTP serialization only",
        "versions": versions,
        "tool_types": [tool["type"] for tool in definition["tools"]],
        "strict_schema_serialized": True,
        "version_reference_serialized": True,
        "network_requests": 0,
        "azure_authentication": False,
        "live_acceptance": "not performed",
        "remaining_gates": [
            "all-nine manifest contracts and source authority",
            "server-side version selection and structured-output acceptance",
            "authenticated A2A intermediate validation, correlation and nested budgets",
            "Search identity, applicable snapshot/filter and revocation enforcement",
            "MCP/OpenAPI authentication and real tool behavior",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = probe()
    text = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
