# SRE Agent Permissions and Safe-Deployment Guide for OGE Customers

| Field | Value |
|-------|-------|
| **Story** | #55 — SRE Agent permissions and safe-deployment guide for OGE customers |
| **Author** | Andrew Delosky |
| **Status** | Draft |
| **Date** | 2026-06-11 |
| **Parent Epic** | #31 — Agentic and Scale-Ready Ops Layer |
| **Sources** | [Microsoft Learn](https://learn.microsoft.com/en-us/azure/sre-agent/), [sre.azure.com/docs](https://sre.azure.com/docs/), [github.com/microsoft/sre-agent](https://github.com/microsoft/sre-agent) |

> **Methodology**: This guide is built exclusively from verified Microsoft documentation. Items flagged as **UNDOCUMENTED** have no official source and represent risks or blockers that must be validated with the PG before customer deployment.

---

## 1. Identity and RBAC Model

### 1.1 Agent Identity Architecture

Each SRE Agent creates **two managed identities**:

| Identity | Managed By | Purpose |
|----------|-----------|---------|
| **User-Assigned MI (UAMI)** | You — assign RBAC, select in connector dropdowns | All Azure resource operations, connectors, data sources |
| **System-Assigned MI** | Azure (internal, not user-configurable) | Internal agent infrastructure only |

Always select the **UAMI** (named `id-*`) in connector and integration dropdowns. The system-assigned identity is not exposed for user configuration.

> **Source**: [agent-identity](https://sre.azure.com/docs/concepts/agent-identity)

### 1.2 RBAC Roles — Minimum Required

Regardless of permission level selected, these four roles are **always assigned**:

| Role | Scope | Purpose |
|------|-------|---------|
| Reader | Resource Group | View resources and properties |
| Log Analytics Reader | Resource Group | Query logs and workspaces |
| Monitoring Reader | Resource Group | Access metrics and monitoring data |
| **Monitoring Contributor** | **Subscription** | Acknowledge/close Azure Monitor alerts, update monitoring settings |

> ⚠️ **OGE Note**: `Monitoring Contributor` is assigned at **subscription scope**, not resource group. This is a broader grant than the others. OGE security teams should be made aware during deployment planning.

> **Source**: [permissions](https://sre.azure.com/docs/concepts/permissions)

### 1.3 Permission Levels (Two Tiers)

| Level | Grants | OGE Recommendation |
|-------|--------|---------------------|
| **Reader** | Core monitoring roles + resource-type-specific **Reader** roles. Agent requests temporary OBO elevation when action is needed. | **Recommended for initial deployments** — least privilege |
| **Privileged** | Core monitoring roles + resource-type-specific **Contributor** roles based on detected resource types | Use only for mature deployments with established guardrails |

**Default state**: If no resource groups are assigned during creation, the UAMI has **zero permissions**. Explicit access must be granted.

> **Source**: [permissions](https://learn.microsoft.com/en-us/azure/sre-agent/permissions)

### 1.4 RBAC Scope Model

| Scope | Supported | Notes |
|-------|-----------|-------|
| Per resource group | ✅ Yes | Primary model — Reader, Contributor, resource-specific roles |
| Per subscription | ✅ Yes | Reader at subscription level for broad visibility |
| Per management group | **UNDOCUMENTED** | Not mentioned in any official SRE Agent documentation |
| Cross-subscription | ✅ Yes | Agent can manage resources across subscriptions if RBAC is granted |

CLI patterns for RBAC assignment:

```bash
# Resource group scope (recommended starting point)
az role assignment create \
  --assignee <AGENT_UAMI_PRINCIPAL_ID> \
  --role Reader \
  --scope /subscriptions/<SUB_ID>/resourceGroups/<RG_NAME>

# Subscription scope (broad read-only visibility)
az role assignment create \
  --assignee <AGENT_UAMI_PRINCIPAL_ID> \
  --role Reader \
  --scope /subscriptions/<SUB_ID>
```

> **Source**: [permissions](https://learn.microsoft.com/en-us/azure/sre-agent/permissions)

### 1.5 Sub-Agent RBAC Isolation

> **UNDOCUMENTED**: The official documentation does **not** describe any mechanism to assign distinct RBAC roles or different managed identities to individual custom (sub) agents. Custom agents share the parent agent's UAMI and its associated RBAC permissions.

**OGE Risk**: If a customer needs different sub-agents to have different access levels (e.g., a security scanner with Reader-only vs. a remediation agent with Contributor), this cannot be achieved within a single SRE Agent instance. The workaround is to deploy **separate SRE Agent instances** with distinct UAMIs — but this increases always-on costs.

> **Source**: [sub-agents](https://learn.microsoft.com/en-us/azure/sre-agent/sub-agents)

### 1.6 Deployer Prerequisites

The person deploying the agent must hold **Owner** or **User Access Administrator** on the subscription to assign RBAC roles to the UAMI at creation time. The `Microsoft.App` resource provider must be registered.

```bash
az provider register -n Microsoft.App --wait
```

> **Source**: [create-agent](https://learn.microsoft.com/en-us/azure/sre-agent/create-agent)

### 1.7 Auditing RBAC Assignments

Three methods to find the UAMI and audit its role assignments:

| Method | Steps |
|--------|-------|
| **Agent Portal** | Settings → Azure settings → "Managed Identity" → "Go to Identity" |
| **Azure Portal** | Navigate to agent RG → find `id-*` identity → copy Object ID → review IAM tab |
| **Azure CLI** | `az role assignment list --assignee <principal-id> --all` |

```bash
# Find the UAMI principal ID
az resource show \
  --resource-group <RG_NAME> \
  --name <AGENT_NAME> \
  --resource-type Microsoft.App/containerApps \
  --query identity.userAssignedIdentities

# List all role assignments
az role assignment list --assignee <PRINCIPAL_ID> --all -o table
```

> **Source**: [agent-identity](https://sre.azure.com/docs/concepts/agent-identity), [permissions](https://sre.azure.com/docs/concepts/permissions)

---

## 2. User Roles and Access Control

### 2.1 Four Data-Plane Roles

SRE Agent defines **four** distinct data-plane roles (assigned via Azure IAM on the agent resource):

| Role | Key Capabilities | Cannot Do |
|------|-----------------|-----------|
| **SRE Agent Reader** | View threads, logs, incidents (read-only) | Chat, request actions, modify anything |
| **SRE Agent Standard User** | Chat, run diagnostics, request actions, manage scheduled tasks | Approve actions, upload KB docs, create custom agents, modify connectors |
| **SRE Agent Author** | Create custom agents, upload KB docs, author response plans, add/edit connectors | Approve actions, create scheduled tasks, delete resources |
| **SRE Agent Administrator** | Full access — approve actions, manage connectors, delete resources | — |

> **OGE Recommendation**: Use **Author** role for CSA engineers building sub-agents and skills, reserving **Administrator** for the customer's designated approvers. This enforces separation of duties — builders cannot approve their own automations.

The user who creates the agent automatically receives SRE Agent Administrator role. Backend enforces 403 errors on role violations even if the UI shows buttons.

```bash
# Assign Author role to a CSA engineer
az role assignment create \
  --role "SRE Agent Author" \
  --assignee <USER_PRINCIPAL_ID> \
  --scope <AGENT_RESOURCE_ID>
```

> **Source**: [user-roles](https://sre.azure.com/docs/concepts/user-roles)

### 2.2 Three-Layer Access Control Model

```
Layer 1: User Roles       → who can do what with the agent (Azure IAM on agent resource)
Layer 2: Run Modes         → whether agent asks before acting (per response plan / scheduled task)
Layer 3: Agent Permissions → what the agent can access on Azure (RBAC on resource groups)
```

> **Source**: [user-roles](https://sre.azure.com/docs/concepts/user-roles)

---

## 3. Run Modes and Approval Workflows

### 3.1 Available Run Modes

There are exactly **two run modes**:

| Mode | Behavior | Default For |
|------|----------|-------------|
| **Review** | Agent proposes action; Administrator approves or denies before execution | Agent-level global setting |
| **Autonomous** | Agent executes immediately and reports what it did | Incident response plans; Scheduled tasks |

> ⚠️ **OGE CRITICAL**: The default for incident response plans and scheduled tasks is **Autonomous**, not Review. **Enterprise deployments must explicitly override to Review for production plans** until the operations team has validated the agent's behavior.

> **Source**: [run-modes](https://sre.azure.com/docs/concepts/run-modes)

### 3.2 What Review Mode Does and Does NOT Gate

**Review mode shows Approve/Deny buttons ONLY for Azure infrastructure operations:**
- Azure CLI commands
- ARM operations
- Kubernetes commands (kubectl)

**Review mode does NOT gate:**
- Sending emails (Outlook connector)
- Posting to Teams
- Querying external data sources
- Any non-Azure-infrastructure tool

> ⚠️ **OGE CRITICAL**: To govern non-Azure actions, use **Tool Access Policies** (Section 4) or **Agent Hooks** (Section 5). Review mode alone is insufficient for full governance.

> **Source**: [run-modes](https://sre.azure.com/docs/concepts/run-modes)

### 3.3 Run Mode Scope

| Level | Supported | Detail |
|-------|-----------|--------|
| Per response plan | ✅ Yes | Set `Agent autonomy level` when creating/editing incident response plans |
| Per scheduled task | ✅ Yes | Set `Agent autonomy level` when creating/editing scheduled tasks |
| Per custom agent | ❌ No | Modes are NOT set in custom agent YAML definitions |
| Agent global fallback | ✅ Yes | Settings → Basics → global mode (defaults to Review) |

> **Source**: [run-modes](https://learn.microsoft.com/en-us/azure/sre-agent/run-modes)

### 3.4 Approval Workflow Details

| Aspect | Detail |
|--------|--------|
| Who can approve | **SRE Agent Administrators only** — Standard Users cannot approve |
| How | Agent presents proposed action text with `[Approve]` and `[Deny]` buttons in chat UI |
| OBO escalation | If managed identity lacks permissions even after approval, agent prompts for on-behalf-of (OBO) user credential authorization |
| OBO token lifecycle | Encrypted, stored on approval record, reused within a single task, NOT reused across tasks |
| OBO restriction | **Personal Microsoft accounts (MSA) cannot authorize OBO** — work/school (Entra ID) accounts only |
| UI distinction | "Grant permissions" = OBO needed; "Approve action" = agent has sufficient permissions |

> **Source**: [run-modes](https://learn.microsoft.com/en-us/azure/sre-agent/run-modes), [permissions](https://sre.azure.com/docs/concepts/permissions)

### 3.5 Management Lock Interaction

> **UNDOCUMENTED**: How Azure Resource Manager management locks (`CanNotDelete`, `ReadOnly`) interact with SRE Agent operations is **not mentioned in any official documentation**. This is a significant gap for OGE production environments where critical infrastructure is typically lock-protected.

**OGE Risk**: Until this is clarified by the PG, assume that management locks follow standard ARM behavior — the agent's operations will be blocked by locks just as any other ARM client would be. However, this has not been verified.

---

## 4. Tool Access Policies

> **Note**: This capability is currently in **Preview**.

Tool Access Policies provide a granular allow/deny system for controlling which tools the agent can use.

### 4.1 Three Policy Scopes

| Scope | Who Sets It | Rules Available | Covers |
|-------|-------------|-----------------|--------|
| **Global** | Admin only | Allow, Ask, **Deny** | All custom agents + conversations |
| **Custom Agent** | Admin or Author | Allow only | All conversations for one agent |
| **Thread** | Any user | Allow only | One conversation |

**Only the global scope can Deny.** Custom agent and thread scopes can only widen access within global boundaries.

### 4.2 Rule Evaluation Order

1. **Deny** (global) → blocks tool immediately
2. **Allow** (any scope) → executes immediately, even in Review mode
3. **Ask** (global) → pauses in Review mode, auto-approves in Autonomous
4. **No match** → default behavior (run mode applies)

### 4.3 Pattern Syntax

| Pattern | Example | Matches |
|---------|---------|---------|
| Tool name | `RunAzCliWriteCommands` | All Azure CLI write operations |
| Argument matching | `bash(az * delete *)` | Any bash command with `az` and `delete` |
| Wildcard | `*` | All tools |

### 4.4 Named Runtime Tool Names

| Tool | Operations |
|------|-----------|
| `RunAzCliReadCommands` | Azure CLI read operations |
| `RunAzCliWriteCommands` | Azure CLI write operations |
| `RunKubectlReadCommand` | kubectl read operations |
| `RunKubectlWriteCommand` | kubectl write operations |
| `RunInTerminal` | Terminal commands |
| `RunShellCommand` | Shell commands |
| `ExecutePythonCode` | Python script execution |
| `RunPsqlReadCommand` | PostgreSQL read queries |
| `GeneratePdfReport` | PDF report generation |

### 4.5 Recommended OGE Baseline Global Deny Policy

Based on PG-documented dangerous command patterns:

```json
{
  "permissions": {
    "deny": [
      "bash(az * delete *)",
      "RunKubectlWriteCommand(kubectl delete *)",
      "bash(rm -rf *)",
      "bash(sudo *)",
      "RunPsqlReadCommand(DROP TABLE *)",
      "RunPsqlReadCommand(DROP DATABASE *)",
      "RunPsqlReadCommand(TRUNCATE TABLE *)",
      "RunPsqlReadCommand(DELETE FROM *)"
    ]
  }
}
```

Configuration via API:
```bash
curl -X PUT "https://<agent>/api/v2/agent/settings/global" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"permissions": {"deny": ["bash(az * delete *)", ...]}}'
```

Or via portal: **Settings → Permissions**

**Limit**: 1,000 patterns per scope.

> **Source**: [tool-access-policies](https://sre.azure.com/docs/concepts/tool-access-policies), [Agent Hooks blog](https://techcommunity.microsoft.com/blog/appsonazureblog/agent-hooks-production-grade-governance-for-azure-sre-agent/4500292)

---

## 5. Agent Hooks — Production Governance Framework

Agent Hooks provide programmatic guardrails that execute before or after agent actions.

### 5.1 Four Hook Events

| Event | Triggers When | Can Do |
|-------|--------------|--------|
| **Start** | New thread begins (first message) | Inject context, filter by thread source |
| **PreToolUse** | Agent about to execute a tool | Allow, deny, ask; inject context; override tool access policies |
| **PostToolUse** | Tool finishes executing | Audit, block results, inject context |
| **Stop** | Agent about to return final response | Validate completeness, reject and force continuation |

### 5.2 Two Execution Types

| Type | How It Works | Best For |
|------|-------------|---------|
| **Prompt** | LLM evaluates a prompt, returns JSON `{"ok": true/false}` | Nuanced validation, quality gates |
| **Command** | Bash or Python script in sandboxed environment | Deterministic checks, blocklists, audit logging |

### 5.3 Hook Configuration Parameters

| Option | Default | Range/Notes |
|--------|---------|-------------|
| `timeout` | 30s | 1–300 seconds |
| `failMode` | `allow` | `allow` or `block` — **use `block` for security-critical hooks** |
| Script size | — | 64 KB maximum |
| `maxRejections` | 3 | 1–25 (prompt Stop hooks only) |

> ⚠️ **OGE CRITICAL**: Set `failMode: block` for all security-relevant hooks. The default `allow` means a crashed hook script permits the action to proceed.

### 5.4 Command Hook Exit Codes

| Exit Code | Behavior |
|-----------|---------|
| `0` with no output | Allow |
| `0` with JSON | Parse JSON for decision |
| `2` | Block — stderr becomes reason |
| Other | Falls back to `failMode` |

> ⚠️ A rejection without a `reason` field is treated as **approval**. Always include `reason` when rejecting.

### 5.5 Two Hook Levels

| Level | Configured By | Scope |
|-------|--------------|-------|
| Agent level | SRE Agent Administrator | All threads + all custom agents |
| Custom agent level | Administrator | Only that specific custom agent |

Both levels can coexist. When both match same event, **both run**. A **deny** short-circuits remaining hooks.

### 5.6 PreToolUse Security Note

PreToolUse hooks can return `permissionDecision: "allow"` to **override tool access policies** (including global deny). This is audit-logged but represents a security-sensitive capability.

> **OGE Recommendation**: Restrict hook authoring to trusted Administrators only. Document all hooks that override deny policies.

> **Source**: [agent-hooks](https://sre.azure.com/docs/capabilities/agent-hooks), [hooks tutorial](https://sre.azure.com/docs/tutorials/agent-config/agent-hooks)

---

## 6. Security at Scale — Multi-Subscription Deployment

### 6.1 Cross-Subscription Access

The agent deploys to a single region in a single subscription. With appropriate RBAC on its UAMI, it can manage resources across any region or subscription.

> **Source**: [supported-regions](https://learn.microsoft.com/en-us/azure/sre-agent/supported-regions)

### 6.2 RBAC Automation for Multi-Subscription Estates

For OGE customers with many subscriptions, automate RBAC assignments:

```bash
# Script to grant Reader across all subscriptions
AGENT_MI_PRINCIPAL_ID="<UAMI_OBJECT_ID>"
for SUB_ID in $(az account list --query "[].id" -o tsv); do
  az role assignment create \
    --assignee $AGENT_MI_PRINCIPAL_ID \
    --role Reader \
    --scope /subscriptions/$SUB_ID
done
```

> **UNDOCUMENTED**: Management Group-level RBAC for the agent UAMI is not documented. For 50+ subscription estates, per-subscription automation is the supported path.

### 6.3 Multi-Agent Isolation Pattern

Each SRE Agent instance gets its own distinct UAMI. Deploy **separate agents** for:
- Different business units requiring RBAC isolation
- Different security zones (production vs. non-production)
- Different compliance boundaries

> **Trade-off**: Each additional agent incurs the always-on cost (~$292/month baseline). Microsoft documents consolidation for cost efficiency but supports the isolation pattern.

> **Source**: [agent-identity](https://sre.azure.com/docs/concepts/agent-identity), [pricing-billing](https://learn.microsoft.com/en-us/azure/sre-agent/pricing-billing)

### 6.4 Multi-Tenant Pattern (Azure Lighthouse)

For MSP or multi-tenant OGE scenarios:

| Component | Detail |
|-----------|--------|
| Architecture | Single SRE Agent in managing tenant → Lighthouse delegation → customer tenant subscriptions |
| Required role | `User Access Administrator` (Lighthouse doesn't support Owner delegation) |
| Minimum customer roles | Reader + Monitoring Reader + Log Analytics Reader |

> **Source**: [Multi-tenant blog](https://techcommunity.microsoft.com/blog/appsonazureblog/managing-multi-tenant-azure-resource-with-sre-agent-and-lighthouse/4511789)

### 6.5 Network Security — Three Modes

| Mode | Description | OGE Recommendation |
|------|-------------|---------------------|
| **Unrestricted** (default) | No restrictions; agent reaches any internet endpoint | Dev/test only |
| **Limited** | Wildcard URL allowlist restricts endpoints | Partial control without VNet overhead |
| **Full VNet** | All outbound routes through customer VNet with custom DNS, firewall, and audit trail | **Required for OGE production** |

**Full VNet integration** (GA as of June 2026):
- Agent traffic appears in network logs
- Passes through L4 and L7 firewalls
- Respects custom DNS and egress rules
- **Egress-only** — inbound connections to the agent from inside a private network are not supported

> ⚠️ **Known Preview Limitation**: Connector traffic (PagerDuty, ServiceNow, Teams, Outlook, etc.) **routes over the public internet** regardless of VNet mode. This is a significant gap for regulated OGE environments.

> **Source**: [network-integration](https://learn.microsoft.com/en-us/azure/sre-agent/network-integration)

### 6.6 VNet Bypass Controls

When Full VNet is enabled, specific traffic can be routed outside the VNet (bypass). Scoped to **Administrators only**:

| Bypass | Traffic Type |
|--------|-------------|
| MCP server access | MCP server traffic |
| Package manager access | PyPI, npm, NuGet |
| Code repositories | GitHub, GitHub Enterprise, Azure DevOps |
| Additional hosts | Custom hostnames/wildcard patterns |

These are documented as **"transitional aids, not permanent substitutes"** for hostname-aware egress filtering.

> **Source**: [network-integration](https://learn.microsoft.com/en-us/azure/sre-agent/network-integration)

### 6.7 Private AKS Cluster Access

When using Full VNet with private AKS clusters:
- kubectl commands run through **AKS command invoke flow**
- Supports agent's managed identity but does **NOT** propagate OBO user credentials
- AKS command invoke limits: **60-second ARM API timeout** and **512 KB output size limit**

> **Source**: [network-integration](https://learn.microsoft.com/en-us/azure/sre-agent/network-integration)

### 6.8 Required Firewall Domains

| Domain | Purpose |
|--------|---------|
| `*.azuresre.ai` | Agent portal, API, and real-time chat (WebSocket) |
| `sre.azure.com` | Agent management portal |
| `portal.azure.com` | Azure portal (Monitor, Logs, managed identity) |
| `api.applicationinsights.io` | Application Insights query API |

> ⚠️ Zscaler and some corporate proxies block `*.azuresre.ai` by default. WebSocket connections must be allowed.

> **Source**: [network-requirements](https://learn.microsoft.com/en-us/azure/sre-agent/network-requirements)

### 6.9 Supported Deployment Regions

| Region | Geography |
|--------|-----------|
| East US 2 | United States |
| Canada Central | Canada |
| Sweden Central | Europe |
| France Central | Europe |
| UK South | Europe |
| Australia East | Asia Pacific |
| Korea Central | Asia Pacific |

> Agent is locked to a single region at creation. Region cannot be changed post-deployment.

> **Source**: [supported-regions](https://learn.microsoft.com/en-us/azure/sre-agent/supported-regions)

---

## 7. Audit and Compliance

### 7.1 Application Insights Audit Trail

All agent actions are logged to **Azure Application Insights** (`customEvents` table). Nine event types:

| Event | What It Captures | Compliance Relevance |
|-------|-----------------|---------------------|
| `AgentResponse` | Chat responses sent to users | High |
| `ModelGeneration` | Every LLM call (input/output tokens, model ID) | High |
| `AgentToolExecution` | Every tool call (name, input, output) | **Critical** |
| `AgentAzCliExecution` | Azure CLI commands run by the agent | **Critical** |
| `ApprovalDecision` | Approval or rejection of proposed actions | **Critical** |
| `AgentExecution` | Agent session start/end lifecycle | Medium |
| `MetaAgent` | Agent routing and orchestration decisions | Medium |
| `AgentHandoff` | Cross-agent handoffs | Medium |
| `IncidentActivitySnapshot` | Full incident lifecycle (severity, status, mitigation) | **Critical** |

All events include OpenTelemetry correlation fields: `TraceId`, `SpanId`, `ParentSpanId`, `ThreadId`, `gen_ai.agent.id`, `gen_ai.agent.name`.

Example KQL to audit agent actions in a specific thread:
```kql
customEvents
| where tostring(customDimensions.ThreadId) == "<THREAD_ID>"
| project timestamp, Event = name,
    EventType = tostring(customDimensions.EventType),
    Tool = tostring(customDimensions.ToolName),
    Agent = tostring(customDimensions.SubAgentName)
| sort by timestamp asc
```

> **Source**: [audit-agent-actions](https://sre.azure.com/docs/capabilities/audit-agent-actions)

### 7.2 Azure Activity Log

ARM-level resource operations (creating, updating, deleting the agent resource, managed identity, Application Insights resources) are captured in the standard Azure Activity Log.

> **Source**: [audit-agent-actions](https://learn.microsoft.com/en-us/azure/sre-agent/audit-agent-actions)

### 7.3 Data Storage and Residency

| Data | Stored In | Residency |
|------|-----------|-----------|
| Conversations, memories, knowledge files | Agent deployment region | ✅ In-region |
| Session insights, synthesized knowledge | Agent deployment region | ✅ In-region |
| Application Insights telemetry | Log Analytics workspace in agent RG | ✅ In-region |

**Critical exception — AI model provider:**

| Deployment Region | Default Model | Data Residency |
|-------------------|--------------|----------------|
| EU, EFTA, UK | Azure OpenAI | ✅ Covered by EU Data Boundary |
| US, APAC, others | **Anthropic** | ⚠️ Data **may be processed in the United States** |

> ⚠️ **OGE CRITICAL**: For US-deployed agents, Anthropic is the default model. Prompts, responses, and resource analysis may be processed in the US by Anthropic. To use Azure OpenAI instead, **explicitly switch the model in Settings**. OGE customers in regulated sectors should be informed of this default.

**Microsoft and Anthropic do NOT use customer data to train AI models.**

> **Source**: [data-privacy](https://learn.microsoft.com/en-us/azure/sre-agent/data-privacy)

### 7.4 Data Isolation

Data is isolated by **tenant and Azure subscription boundaries**.

> **Source**: [data-privacy](https://learn.microsoft.com/en-us/azure/sre-agent/data-privacy)

### 7.5 Compliance Certifications

> **UNDOCUMENTED**: No compliance certifications specific to Azure SRE Agent (ISO 27001, SOC 2 Type II, FedRAMP, IEC 62443, NERC CIP) are published in official documentation. Enterprise compliance teams should query their Microsoft account team for current certification status.

Referenced frameworks:
- [Microsoft Product Terms](https://www.microsoft.com/licensing/terms)
- [Microsoft DPA](https://www.microsoft.com/licensing/docs/view/Microsoft-Products-and-Services-Data-Protection-Addendum-DPA)
- [Service Trust Portal](https://aka.ms/subprocessor)

> **Source**: [data-privacy](https://learn.microsoft.com/en-us/azure/sre-agent/data-privacy)

### 7.6 Export for Compliance

| Method | Covers | Format |
|--------|--------|--------|
| Settings → Agent consumption → Download | Thread-level usage data (title, type, AAU cost, status) | CSV |
| Application Insights KQL | Full action telemetry, tool calls, CLI commands, approvals | Query results (exportable) |
| Azure Activity Log | ARM-level resource operations | Portal export / Event Hub / Log Analytics |

> **UNCLEAR**: Whether full conversation content (message text, LLM prompt text, tool outputs) can be exported in bulk for compliance archival is not explicitly documented as a supported workflow.

---

## 8. Deployment Automation

### 8.1 Agent Resource Deployment

The SRE Agent deploys via **Bicep + Azure Developer CLI (`azd up`)**:

- Resource type: `Microsoft.App/agents` (API version `2025-05-01-preview`)
- Provisioning time: ~8–12 minutes
- Required role: **Owner** on subscription
- Official lab: [github.com/microsoft/sre-agent/tree/main/labs](https://github.com/microsoft/sre-agent/tree/main/labs)

### 8.2 IaC Limitation — Skills and Sub-Agents

> **CONFIRMED LIMITATION**: SRE Agent Skills, sub-agents, and scheduled tasks **cannot be deployed via Terraform, Azure CLI, or direct API**. These must be configured manually through the portal or via the data-plane REST API (`/api/v2/`).

**OGE Impact**: Enterprise customers requiring repeatable, auditable deployments across multiple subscriptions must account for manual portal configuration labor in the delivery model.

**Workarounds**:
- Data-plane REST API (`/api/v2/`) for custom agent YAML and hook configuration
- Agent can potentially import Skills from a connected GitHub repo
- Manual deployment templates (team is building these)

> **Source**: Wilkin Shum team testing (June 11, 2026), [sre.azure.com/docs API reference](https://sre.azure.com/docs/)

---

## 9. Risk Register — UNDOCUMENTED Items

| # | Area | Finding | Risk Level | Action |
|---|------|---------|------------|--------|
| U1 | RBAC | Management Group-level RBAC for UAMI — not documented | 🟡 Medium | Validate with PG |
| U2 | RBAC | Per-sub-agent distinct RBAC isolation — not supported | 🟡 Medium | Use multi-agent pattern |
| U4 | Run Mode | Management lock interaction — not documented | 🔴 Critical | Validate with PG |
| U5 | Run Mode | Autonomous mode acknowledgment text not published | 🟡 Medium | Request from PG |
| U8 | Network | Connector traffic bypasses VNet (preview limitation) | 🔴 Critical | Monitor for GA fix |
| U9 | Compliance | No SRE Agent-specific certifications documented | 🔴 Critical | Query account team |
| U10 | Compliance | Bulk conversation export for archival — unclear | 🟠 High | Validate with PG |
| U11 | Data | Anthropic default in US regions — data processed in US | 🔴 Critical | Explicitly switch to Azure OpenAI |
| U12 | Regions | No Middle East region available | 🟠 High | Monitor region rollout |
| U13 | Deployment | Skills/sub-agents cannot be deployed via IaC | 🟠 High | Raise with PG |

---

## 10. OGE Deployment Checklist

### Pre-Deployment

- [ ] Determine permission level (Reader recommended for initial deployment)
- [ ] Identify resource groups to manage — assign RBAC per RG
- [ ] Verify deployer has Owner or User Access Administrator on subscription
- [ ] Register `Microsoft.App` resource provider
- [ ] Define user role assignments (Author for CSA builders, Administrator for approvers)
- [ ] Decide network mode (Full VNet recommended for production)
- [ ] Configure firewall rules for `*.azuresre.ai`, `sre.azure.com`, `portal.azure.com`

### Post-Deployment

- [ ] Set agent global run mode to **Review** (verify it is not Autonomous)
- [ ] Configure Tool Access Policies — deploy recommended global deny policy
- [ ] Set up Agent Hooks for security-critical operations with `failMode: block`
- [ ] Explicitly set AI model to **Azure OpenAI** if data residency is a concern
- [ ] Override incident response plan and scheduled task defaults from Autonomous to Review
- [ ] Verify Application Insights is collecting audit events (`customEvents` table)
- [ ] Document all RBAC assignments and communicate to customer security team
- [ ] Test approval workflow with a non-critical action

### Ongoing Operations

- [ ] Periodic RBAC audit (`az role assignment list --assignee <MI> --all`)
- [ ] Monitor Application Insights for `AgentAzCliExecution` and `ApprovalDecision` events
- [ ] Review and update Tool Access Policies as new sub-agents are added
- [ ] Monitor VNet bypass list — remove transitional entries as egress filtering matures
