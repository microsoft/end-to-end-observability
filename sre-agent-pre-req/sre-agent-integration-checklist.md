# Azure SRE Agent — Integration Checklist for OilCo

**Audience:** Cloud / Platform Architects

**Purpose:** Provide a documented, repeatable checklist for onboarding Azure SRE Agent into OilCo workloads so that AI-assisted operations integrate cleanly with the existing observability stack, follow least-privilege, and stay within Microsoft's officially supported behaviors.

**Reference:** Microsoft Learn — [Azure SRE Agent docs](https://learn.microsoft.com/en-us/azure/sre-agent/overview)

**Doc-verification stamp:** All guidance below cross-checked against Microsoft Learn on **2026-05-26**.

---

## 1. Pre-flight checklist (before you click "Create agent")

### 1.1 Subscription & region

- [ ] Target Azure subscription identified and tagged for SRE Agent workloads.

- [ ] Subscription is in a **supported region**: Sweden Central, East US 2, Australia East, or UK South. (See [Supported regions](https://learn.microsoft.com/en-us/azure/sre-agent/supported-regions) — list is authoritative and may expand.)

- [ ] Resource provider registered:

  ```bash

  az provider register --namespace "Microsoft.App"

  ```

- [ ] Decide on a dedicated resource group for the agent (recommended — separates agent telemetry from the workloads it monitors).

### 1.2 Identity & permissions

- [ ] Operator deploying the agent has **Owner** or **User Access Administrator** on the subscription (per [Create an agent](https://learn.microsoft.com/en-us/azure/sre-agent/create-agent) — needed to assign RBAC to the agent's managed identity). **Role Based Access Control Administrator** is also acceptable per [Network requirements](https://learn.microsoft.com/en-us/azure/sre-agent/network-requirements) — the underlying permission required is `Microsoft.Authorization/roleAssignments/write`.

- [ ] Identified the **Entra ID group** that should hold each SRE Agent user role (Reader / Standard User / Administrator) — avoid assigning to individuals.

- [ ] Confirmed all approver accounts are **work/school (Entra ID)** accounts — personal MSAs cannot authorize OBO.

### 1.3 Networking / firewall

Allow-list the following domains for both **HTTP and WebSocket** traffic on egress proxies (Zscaler, Palo Alto, etc.) — per [Network requirements](https://learn.microsoft.com/en-us/azure/sre-agent/network-requirements):

- [ ] `*.azuresre.ai` — agent portal, API, real-time chat

- [ ] `sre.azure.com` — management portal

- [ ] `portal.azure.com` — Azure portal access

- [ ] `api.applicationinsights.io` — App Insights query API

- [ ] Validated proxy does **not** strip / block WebSockets (common Zscaler failure mode).

### 1.4 Observability prerequisites

- [ ] Workloads under scope already emit logs to **Log Analytics** (diagnostic settings configured on App Service, AKS, Container Apps, SQL, etc.).

- [ ] Application Insights is enabled on app-tier components — the agent consumes `requests`, `dependencies`, `exceptions`, `traces`, `customEvents`.

- [ ] Azure Monitor alerts exist for production services (agent reads/acks/closes via Monitoring Contributor at subscription scope).

- [ ] Decide whether to **reuse an existing App Insights** or let the wizard create a new one. *Recommendation:* let it create a new one — keeps agent telemetry isolated from app telemetry and avoids retention/quota contention.

---

## 2. Agent deployment checklist

### 2.1 Provisioning (sre.azure.com → Create agent)

- [ ] Subscription, RG, name, region populated.

- [ ] **Permission level chosen and documented** (per [Permissions](https://learn.microsoft.com/en-us/azure/sre-agent/permissions)):

  - **Reader (recommended start, per Microsoft)** — agent reads only; write actions require Admin approval via OBO.

  - **Privileged** — adds resource-type Contributor roles (e.g., Container App Contributor) on detected resource types so the agent can execute approved actions directly. Adopt only after a Review-mode observation period.

- [ ] Managed resource groups limited to the **smallest blast radius** that satisfies the use case (start with one non-prod RG).

- [ ] Deployment verified by asking the agent: *"What Azure resources can you see?"* and confirming the inventory matches expectations.

### 2.2 RBAC verification (post-deploy)

Confirm the auto-assigned roles on the managed identity:

| Scope | Role | Why |

|---|---|---|

| Subscription | Monitoring Contributor | Ack/close Azure Monitor alerts |

| RG (each managed) | Reader | View resources & properties |

| RG (each managed) | Log Analytics Reader | Query workspaces |

| RG (each managed) | Monitoring Reader | Metrics & monitoring data |

| RG (each managed, if Privileged) | Resource-type Contributor (e.g., Container App Contributor) | Take action |

- [ ] Listed the UAMI's role assignments and stored a snapshot in the change record:

  ```bash

  az role assignment list --assignee <UAMI_OBJECT_ID> --all -o table

  ```

### 2.3 User-role assignments (on the agent resource)

- [ ] `SRE Agent Reader` → auditors, compliance, stakeholders.

- [ ] `SRE Agent Standard User` → L1/L2 on-call engineers.

- [ ] `SRE Agent Administrator` → SRE leads / incident commanders (these are the **only** users who can approve OBO and write actions).

- [ ] Assigned via Entra groups, not user objects.

---

## 3. Run-mode & response-plan checklist

Run modes are configured **per response plan and per scheduled task**, not at the agent level.

| Use case | Run mode | Notes |

|---|---|---|

| Production incident remediation | **Review** | Admin must approve each write action |

| Staging / dev incidents | Autonomous | Trusted blast radius |

| Daily health checks | Autonomous | Read-heavy, safe |

| Security alerts | **Review** | Always require human gate |

| Cost / usage reports | Autonomous | Read-only |

- [ ] Each response plan documented with: trigger, run mode, who can approve.

- [ ] Default agent autonomy left at **Review** during the first 2–4 weeks.

---

## 4. Integrations checklist (only what's officially supported)

Only enable connectors that are documented as supported — do not script around unsupported integrations:

- [ ] **Monitoring/Observability** — Azure Monitor, App Insights, Log Analytics, Grafana

- [ ] **Incident management** — Azure Monitor Alerts, PagerDuty, ServiceNow

- [ ] **Source / CI/CD** — GitHub, Azure DevOps (repos + work items)

- [ ] **Data sources** — Azure Data Explorer (Kusto), MCP servers

- [ ] **Knowledge base** — OilCo runbooks, architecture diagrams, escalation matrices uploaded (Administrator only).

---

## 5. Sample KQL — proving SRE Agent diagnosis capability

These queries target the agent's own App Insights `customEvents` table (auto-created at provisioning). Run them from the agent portal under **Monitor → Logs**. They demonstrate that the agent's investigation, tool use, model usage, and approvals are fully observable.

### 5.1 Event-type overview

```kusto

customEvents

| summarize Count = count() by name

| sort by Count desc

```

### 5.2 What tools did the agent invoke in the last 7 days?

```kusto

customEvents

| where name == "AgentToolExecution"

| where customDimensions.EventType == "ToolStart"

| where timestamp > ago(7d)

| project timestamp,

    Tool     = tostring(customDimensions.ToolName),

    SubAgent = tostring(customDimensions.SubAgentName),

    Input    = tostring(customDimensions.ToolInput)

| sort by timestamp desc

```

### 5.3 Every Azure CLI command the agent has executed

```kusto

customEvents

| where name == "AgentAzCliExecution"

| where timestamp > ago(30d)

| project timestamp,

    Command = tostring(customDimensions.Command),

    SubAgent = tostring(customDimensions.SubAgentName)

| sort by timestamp desc

```

### 5.4 Incident outcomes — was the agent effective?

```kusto

customEvents

| where name == "IncidentActivitySnapshot"

| where timestamp > ago(30d)

| project timestamp,

    IncidentId       = tostring(customDimensions.IncidentId),

    Title            = tostring(customDimensions.IncidentTitle),

    Platform         = tostring(customDimensions.IncidentPlatform),

    Severity         = tostring(customDimensions.IncidentSeverity),

    MitigatedByAgent = tostring(customDimensions.IncidentMitigatedByAgent),

    AssistedByAgent  = tostring(customDimensions.IncidentAssistedByAgent),

    Autonomy         = tostring(customDimensions.AgentAutonomyLevel),

    ResponsePlan     = tostring(customDimensions.ResponsePlanId)

| sort by timestamp desc

```

### 5.5 Approvals & denials (human-in-the-loop audit)

Microsoft's published `ApprovalDecision` query projects the full `customDimensions` payload (the field schema isn't pinned in docs, so don't hard-code field names):

```kusto

// All approval decisions — official MS Learn pattern

customEvents

| where name == "ApprovalDecision"

| where timestamp > ago(30d)

| project timestamp, customDimensions

```

### 5.6 LLM token & cost telemetry

```kusto

customEvents

| where name == "ModelGeneration"

| where customDimensions.EventType == "ModelGenerationEnd"

| where timestamp > ago(7d)

| extend InputTokens  = toint(customDimensions.InputTokens),

         OutputTokens = toint(customDimensions.OutputTokens),

         Model        = tostring(customDimensions.ModelId),

         Agent        = tostring(customDimensions.AgentName)

| summarize Calls = count(),

            TotalInput  = sum(InputTokens),

            TotalOutput = sum(OutputTokens)

            by bin(timestamp, 1d), Model, Agent

| sort by timestamp desc

```

### 5.6b Daily token consumption trend (MS Learn sample)

```kusto

customEvents

| where name == "ModelGeneration"

| where customDimensions.EventType == "ModelGenerationEnd"

| where timestamp > ago(30d)

| extend InputTokens  = toint(customDimensions.InputTokens),

         OutputTokens = toint(customDimensions.OutputTokens)

| summarize TotalTokens = sum(InputTokens) + sum(OutputTokens) by bin(timestamp, 1d)

| render timechart

```

### 5.6c Trace a single thread end-to-end (correlation)

Every event includes `TraceId`, `SpanId`, `ParentSpanId`, `ThreadId`, `CorrelationId`, `gen_ai.agent.id`, `gen_ai.agent.name`. Use `ThreadId` to reconstruct a conversation:

```kusto

customEvents

| where timestamp > ago(7d)

| where tostring(customDimensions.ThreadId) == "<YOUR_THREAD_ID>"

| project timestamp,

    Event     = name,

    EventType = tostring(customDimensions.EventType),

    Tool      = tostring(customDimensions.ToolName),

    Agent     = tostring(customDimensions.SubAgentName)

| sort by timestamp asc

```

### 5.7 Application diagnosis — exception heat map (workload-side App Insights)

Run against the **workload's** App Insights (the agent's typical first diagnostic pass):

```kusto

exceptions

| where timestamp > ago(24h)

| summarize Count = count() by problemId, cloud_RoleName

| sort by Count desc

| take 20

```

### 5.8 Dependency failures — what's calling what and failing

```kusto

dependencies

| where timestamp > ago(1h)

| where success == false

| summarize Failures = count(), AvgDuration = avg(duration)

    by target, type, resultCode

| sort by Failures desc

```

### 5.9 AKS / Container Apps — pod restart spikes (Log Analytics)

```kusto

KubePodInventory

| where TimeGenerated > ago(6h)

| summarize Restarts = sum(PodRestartCount) by Namespace, Name, bin(TimeGenerated, 15m)

| where Restarts > 0

| sort by TimeGenerated desc

```

---

## 6. Stay within documented behavior — guardrails

- [ ] Only deploy in officially supported regions (no workarounds). Agent region is **set at creation and cannot be changed** — to move regions, create a new agent ([Supported regions FAQ](https://learn.microsoft.com/en-us/azure/sre-agent/supported-regions)).

- [ ] Each agent is single-region / single-subscription, but its managed identity **can manage resources across any region or subscription** where you grant RBAC.

- [ ] For ARM-level operations on the agent itself (create/update/delete), audit via the **Azure Activity Log** on the agent's resource group — not via App Insights.

- [ ] Do not bypass the OBO flow with shared service principals — keep the managed identity → OBO model intact.

- [ ] Do not lower the user-role bar (`SRE Agent Administrator` is the *only* role that should approve write actions).

- [ ] Do not flip production response plans to **Autonomous** until you have ≥ 2 weeks of clean Review-mode approvals on the same trigger.

- [ ] Do not embed customer data or secrets in custom runbooks or knowledge-base uploads — runbooks are versioned and visible to all SRE Agent users.

- [ ] Use only documented connector types (Azure Monitor, PagerDuty, ServiceNow, GitHub, ADO, Grafana, ADX, MCP). Custom REST integrations should be wrapped as **subagents** with a published runbook, not hand-rolled webhooks.

- [ ] Keep the App Insights resource created by the agent — it is the system of record for audit (Section 5).

- [ ] Apply Azure Policy to prevent deletion of the agent's App Insights and Log Analytics workspaces (audit retention).

---

## 7. Acceptance-criteria mapping

| Acceptance Criterion | Where it's covered |

|---|---|

| Checklist defines required workspace setup | Sections 1.1 – 1.4, 2.1 – 2.3 |

| Sample KQL queries demonstrate SRE Agent diagnosis capabilities | Section 5 (5.1 – 5.9) |

| SRE Agent deployment aligns with official documentation and avoids untested behaviors | Section 6 + every step links back to Microsoft Learn |

---

## 8. References (verified 2026-05-26)

- [Azure SRE Agent — Overview](https://learn.microsoft.com/en-us/azure/sre-agent/overview)

- [Create an agent](https://learn.microsoft.com/en-us/azure/sre-agent/create-agent)

- [Network requirements](https://learn.microsoft.com/en-us/azure/sre-agent/network-requirements)

- [Supported regions](https://learn.microsoft.com/en-us/azure/sre-agent/supported-regions)

- [Agent permissions (RBAC)](https://learn.microsoft.com/en-us/azure/sre-agent/permissions)

- [User roles](https://learn.microsoft.com/en-us/azure/sre-agent/user-roles)

- [Run modes](https://learn.microsoft.com/en-us/azure/sre-agent/run-modes)

- [Audit agent actions (KQL)](https://learn.microsoft.com/en-us/azure/sre-agent/audit-agent-actions)
