# MCP stub

`azure_monitor_mcp_stub.py` speaks the Model Context Protocol shape over
stdio and answers from `data/fixtures`. It exists so Foundry agents can be
wired and tested against an MCP binding before any Azure resource exists.

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python azure_monitor_mcp_stub.py
```

Four read-only tools: `run_kql`, `get_resource_context`,
`get_recent_changes`, `search_knowledge`. **Nothing here mutates.**

## Why MCP and OpenAPI both

- **OpenAPI** for the skills we own — normalizer, safety gate, hygiene. Our
  endpoints, our contract, generated from `scripts/emit_openapi.py`.
- **MCP** for read-only access to systems we do not own. It is where the
  ecosystem is converging, and a customer already running an MCP server for
  their observability stack can point us at it rather than having us write
  another adapter.

Bind in `agent.yaml`:

```yaml
tools:
  - type: mcp
    name: azure_monitor
    server_label: azure-monitor
    server_url: ${AZURE_MONITOR_MCP_URL}
    auth: managed_identity
```

## Not a production server

No transport beyond stdio, no auth, no rate limiting, no concurrency. For a
real deployment, use the Azure Monitor MCP server or host these tools behind
a proper MCP implementation with managed identity.
