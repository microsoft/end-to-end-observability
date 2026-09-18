# Current Foundry platform compatibility research

Independent public-source review, 2026-09-14. Researcher used 15 calls; no repository/tenant inspection, installation, model calls or deployment. This file preserves the returned findings; no SDK compatibility was tested locally.

## Principal conclusions

**Classic connected_agent tool descriptors are unsupported in new Foundry.** Preserve the Ops Orchestrator's four-specialist logical topology, but migrate the transport explicitly. Current migration guidance recommends A2A and specifies `azure-ai-projects>=2.3.0`; pin an exact tested compatible release rather than only "2.x".

**Azure AI Search documentation conflicts on the runtime identity.** Its prerequisites prescribe the account system-assigned MI and Search Index Data Contributor/Search Service Contributor, while private-network guidance specifies project MI in the Search connection. These contributor roles are documented setup prerequisites, not verified least-privilege read-only minima. Resolve the exact principal/setup experimentally before assigning roles. No arbitrary per-agent Azure UAMI support was established.

## Compatibility matrix

| Requirement | Evidence-backed status | Required deployment verification |
|---|---|---|
| `create_version` / PromptAgentDefinition / Responses | Supported in current migration guidance, Python projects >=2.3.0 | Exact SDK/dependency pin and supported model/settings |
| Literal classic `connected_agent` in modern tools | Unsupported according to migration table | Replace descriptor, not logical specialist relationships |
| A2A specialists | Supported GA `A2ATool`, `a2a`, protocol 1.0; preview `A2APreviewTool` uses 0.3 | Enable incoming A2A, configure target base path/connections, caller Foundry Agent Consumer or higher on target project; Foundry targets text-only/no streaming |
| Workflow alternative | `WorkflowAgentDefinition(workflow=...)` accepts CSDL YAML | Exact action/reference syntax and topology not verified; distinct definition, not drop-in prompt tool |
| Search binding | `AzureAISearchTool` -> `AzureAISearchToolResource(indexes=[AISearchIndexResource(...)])`; fields include project_connection_id, index_name, query_type, top_k, filter | Actual account/project identity, index/vectorization/semantic setup, embedding authorization, compatible text/vector/citation fields |
| Hybrid Search | `vector_semantic_hybrid`, top_k=3 supported; one index per Search tool; same tenant required | Do not substitute local function/BM25 or FileSearch and claim parity |
| OpenAPI direct MI | `type: managed_identity`, `security_scheme.audience`; project MI documented as caller | Host endpoint exists, target audience and caller authorization; project connection required by separate key/token variant, not necessarily direct MI |
| Remote MCP | MCPTool includes server_label, server_url, project_connection_id, require_approval | Reachable remote server, supported transport/authentication and approval continuation; stdio JSON-RPC stub not equivalent |
| Structured outputs | PromptAgentDefinition.text -> PromptAgentDefinitionTextOptions.format -> TextResponseFormat; json_schema supported, concrete TextResponseFormatJsonSchema identified | Exact constructor fields/strict-schema subset not verified; JSON instructions/json_object do not ensure required fields |
| Agent-version invocation | Name-based invocation samples verified | Exact supported name-plus-version request field remains unverified; no invented field allowed |

The documented A2A binding uses `A2ATool(a2a_version=A2AProtocolVersion.V1_0, project_connection_id=a2a_connection.id)`. SDK also exposes base_url, agent_card_path and send_credentials_for_agent_card. Default card path is `/.well-known/agent-card.json`; card credentials are not sent by default. This does not automatically expose existing specialists as endpoints.

The Search binding uses `project.connections.get(connection_name).id` with a Cognitive Services account/project connection, not a classic Azure ML workspace connection. Private Search guidance describes standard setup with VNet injection; confirm that setup before choosing network infrastructure.

## Identity and approval chain

Application DefaultAzureCredential authenticates the application to Foundry. Foundry outbound tool identity is a separate hop. OpenAPI uses project MI in the documented direct flow; its backend may use another MI for downstream Logs/Graph calls. MCP can use project MI, agent identity, shared credentials or OAuth passthrough depending on connection. "Agent identity" is not proof of an arbitrary manifest-declared per-agent Azure UAMI.

MCP sample explicitly handles `mcp_approval_request` with `McpApprovalResponse(approval_request_id=..., approve=...)` and resumes with previous_response_id. Add an approval-handling component; do not silently disable all approvals to simplify the demo.

Current tool prerequisites mention Foundry User for creating/running agents and Foundry Project Manager for relevant connection creation. A2A target role requirement does not prove application Responses or deployment permissions. Record principal, operation, audience, role definition, scope and effective inherited grants for every hop.

## Model lifecycle, not quota proof

Official retirement schedule updated 2026-09-14 17:12 UTC, content SHA `a1838d34e12c68a709ac084c2bceb01bbe522666`:

| Model/version | Published status | Published retirement | Replacement listed |
|---|---|---|---|
| gpt-4o / 2024-11-20 | Legacy | 2027-04-14 | gpt-5.1 |
| gpt-4.1-mini / 2025-04-14 | Legacy | 2027-04-14 | None |

These are base-model rows, not fine-tuning dates. This supports an explicit lifecycle constraint, not availability in a chosen region/deployment type, tenant quota or tool compatibility. Recheck schedule at deployment.

## Cost meter bases

Dedicated Search uses provisioned Search Units billed hourly; premium features such as semantic ranker add charges. Current cost guide describes Serverless Preview separately in compute units/indexed GB-month, not an automatic substitute for the proposed dedicated setup. OpenAI embedding table lists per-1,000-token rates. Optional Container Apps hosting uses vCPU-seconds, GiB-seconds and requests; these are not Foundry prompt-agent hosting prices. No verified dollar total supplied.

## Authoritative references

1. [Migration](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/migrate), updated 2026-09-14.
2. [Search tool](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/ai-search), updated 2026-09-04.
3. [A2A](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/agent-to-agent), updated 2026-09-14.
4. [WorkflowAgentDefinition](https://learn.microsoft.com/en-us/python/api/azure-ai-projects/azure.ai.projects.models.workflowagentdefinition?view=azure-python), updated 2026-08-25.
5. [AISearchIndexResource](https://learn.microsoft.com/en-us/python/api/azure-ai-projects/azure.ai.projects.models.aisearchindexresource?view=azure-python), updated 2026-08-25.
6. [OpenAPI](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/openapi), updated 2026-08-24.
7. [MCP](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/model-context-protocol), updated 2026-09-04.
8. [PromptAgentDefinition](https://learn.microsoft.com/en-us/python/api/azure-ai-projects/azure.ai.projects.models.promptagentdefinition?view=azure-python), [text options](https://learn.microsoft.com/en-us/python/api/azure-ai-projects/azure.ai.projects.models.promptagentdefinitiontextoptions?view=azure-python), [TextResponseFormat](https://learn.microsoft.com/en-us/python/api/azure-ai-projects/azure.ai.projects.models.textresponseformat?view=azure-python), updated 2026-08-25.
9. [A2ATool](https://learn.microsoft.com/en-us/python/api/azure-ai-projects/azure.ai.projects.models.a2atool?view=azure-python), updated 2026-08-25.
10. [MCP auth](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/mcp-authentication), updated 2026-09-11.
11. [Retirement schedule](https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-retirement-schedule), updated 2026-09-14.
12. [Lifecycle policy](https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-retirements), updated 2026-07-24.
13. [Search cost management](https://learn.microsoft.com/en-us/azure/search/search-sku-manage-costs), updated 2026-07-02.
14. [OpenAI pricing](https://azure.microsoft.com/en-us/pricing/details/cognitive-services/openai-service/), accessed 2026-09-14.
15. [Container Apps pricing](https://azure.microsoft.com/en-us/pricing/details/container-apps/), accessed 2026-09-14.

Remaining open compatibility work: version-pinned request schema, exact strict-output constructor/subset, workflow YAML, effective Search/vectorization identity, per-agent UAMI feasibility and chosen region/model/tool intersection. These are explicit implementation gates, not grounds for claiming platform-wide impossibility.
