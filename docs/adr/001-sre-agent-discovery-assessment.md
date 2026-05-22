# ADR-001: SRE Agent as Discovery Engine — Feasibility Assessment

| Field | Value |
|-------|-------|
| **Status** | Proposed |
| **Date** | 2026-05-22 |
| **Author** | Andrew Delosky |
| **Deciders** | Andrew Delosky, James Hunter, Wilkin Shum, Gaurav Bhardwaj, Brad Allen |
| **Related** | User Story #37 (Discovery-Driven Monitoring Baseline), Task #53 (SRE Agent Use Case Mapping) |

## Context

The OGE Observability Solution Build originally designed a standalone **Discovery CLI module** (`discovery/`) to scan Azure estates and identify monitoring gaps. Since that work began, the team agreed to leverage **Azure SRE Agent** (GA March 2026) as the core engine behind the observability solution.

This ADR evaluates whether SRE Agent's built-in capabilities can replace the custom discovery module, and recommends an architecture path forward.

## Current Discovery Module

The `discovery/` module is a deterministic, CLI-driven Azure Resource Graph (ARG) scanner:

- **8 YAML rules** across 6 resource types (App Service, AKS, SQL, Storage, Key Vault)
- **Subscription + Management Group scope** via CLI flags
- **Structured output**: JSON manifest + Markdown report
- **Rules engine** evaluates: missing diagnostic settings, missing workspace links, missing tags
- **CI/CD-friendly**: runs in pipelines, produces deterministic output
- **Reader-only** permissions (no write operations)

### Acceptance Criteria (Story #37)

| Criterion | Status |
|-----------|--------|
| Discovery CLI scans one subscription and outputs resource inventory + observability flags | ✅ Implemented |
| Generates valid Terraform config inputs for monitored resources | 🚧 Partial (JSON schema ready; Terraform generation not yet implemented) |
| Supports single/multi-subscription modes | ✅ Implemented |

## SRE Agent Discovery Capabilities

Azure SRE Agent uses ARG as its primary discovery engine — the same foundation as our module. Key capabilities:

### What SRE Agent Handles Well

| Capability | Detail |
|---|---|
| **Resource enumeration via ARG** | First-class built-in; same engine as our module |
| **Diagnostic settings checks** | Can run `az monitor diagnostic-settings list` on any resource |
| **Missing alert identification** | Prompt-driven gap analysis via scheduled tasks |
| **Multi-subscription** | With per-subscription RBAC on managed identity |
| **Deep investigation** | Hypothesis-driven root cause analysis — far beyond our module |
| **Remediation** | Can create alerts, diagnostic settings, with approval workflow |
| **Extensibility** | Skills (Markdown procedures), sub-agents (YAML), MCP connectors, Python tools |
| **Memory & learning** | Persistent knowledge graph, synthesized architecture docs |

### Where SRE Agent Falls Short (Out-of-the-Box)

| Gap | Closeable via Extensibility? | Mechanism |
|---|---|---|
| **No AMBA awareness** | ✅ Yes | Upload AMBA docs to Knowledge Base; create a Skill that references them during evaluation |
| **No Management Group scope** | ✅ Yes | Python tool uses `azure-mgmt-resourcegraph` (preinstalled) + managed identity ARM scope to query ARG with MG scoping — same query as our module |
| **No structured JSON export** | ✅ Yes | Python tools **must** return JSON-serializable output; the discovery schema becomes the tool's return value — deterministic Python code, not LLM text |
| **Non-deterministic output** | ✅ Yes | Python tool output is deterministic code execution; the LLM decides *when* to call the tool, but the tool's output is identical for the same inputs |
| **No declarative rules engine** | ✅ Yes | Embed the YAML rules engine inside the Python tool; same logic, different host |
| **No programmatic API (CI/CD)** | ⚠️ Partial | Scheduled tasks (cron-based) cover recurring scans; Python tool can POST results to webhook/Storage Account/Event Grid; no on-demand REST API trigger from a pipeline |

**Key finding:** Five of six gaps are fully closeable using SRE Agent's existing extensibility (Python tools + Skills + Knowledge Base). The Python execution environment includes `azure-identity`, `azure-mgmt-resourcegraph`, `pandas`, and 700+ packages — the same libraries our standalone module depends on. The remaining gap (CI/CD pipeline trigger) has viable workarounds.

## Analysis

The two tools solve different problems on the same spectrum:

```
Discovery Module                     SRE Agent
───────────────────────────────────────────────────────────────
SCAN → REPORT → (hand off)    →    INVESTIGATE → REMEDIATE → LEARN
deterministic                       intelligent
batch / CI-CD                       interactive / event-driven
structured JSON                     natural language + actions
```

However, SRE Agent's extensibility model (Python tools, Skills, Knowledge Base) can close most of the gap between these two approaches. A Python tool running inside SRE Agent executes deterministic code with the same libraries and APIs as the standalone module, while gaining access to the agent's investigation, remediation, and memory capabilities.

The real question for OGE customers is whether they need:

1. **A compliance report** — run in a pipeline, hand to an ops team (discovery module strength)
2. **An interactive advisor** — finds gaps AND proposes/executes fixes (SRE Agent strength)
3. **Both** — scheduled scan for visibility, agent for remediation

### Key Insight

The discovery module's **rules, resource coverage, and structured output schema** remain valuable regardless of the engine. The question is *where* that logic lives — in standalone Python, or as SRE Agent Skills + Knowledge Base + Python tools.

## Options

### Option A: Keep Discovery Module Standalone, Use SRE Agent Downstream

- Discovery CLI runs in pipeline → produces JSON manifest
- SRE Agent consumes the manifest as context for investigation and remediation
- **Pro**: Deterministic, CI/CD-friendly, clear separation of concerns
- **Con**: Two systems to maintain; discovery rules duplicated if SRE Agent also needs them

### Option B: Migrate Discovery Logic Into SRE Agent (Naive)

- Convert YAML rules → SRE Agent Skills + Knowledge Base
- Rely on the LLM to perform discovery via natural language prompts
- Use scheduled tasks for recurring scans
- **Pro**: Single platform; agent can scan AND act on findings
- **Con**: Non-deterministic output, no structured JSON, no Management Group scope — gaps remain open
- **Assessment**: Not recommended. Relies on LLM-generated discovery rather than deterministic code.

### Option C: Hybrid — Discovery Module as SRE Agent Skill Input

- **Retain the discovery module** for structured, deterministic estate scanning
- **Publish discovery rules as SRE Agent Knowledge Base content** so the agent understands OGE monitoring standards
- **Build SRE Agent Skills** that reference discovery output and can investigate + remediate gaps
- **Use the discovery JSON manifest** as a scheduled task input for the agent
- **Pro**: Best of both — deterministic scanning + intelligent remediation; discovery module remains pipeline-friendly
- **Con**: Two systems to maintain; integration design required between discovery output and SRE Agent consumption

### Option D: SRE Agent-Native Discovery via Python Tool + Skill (Recommended alongside Option C for discussion)

Migrate the discovery logic into SRE Agent as a **Python tool** + **Skill** + **Knowledge Base**, eliminating the standalone module:

**Architecture:**

```
┌─────────────────────────────────────────────────────────────┐
│                    Azure SRE Agent                           │
│                                                              │
│  ┌───────────────────┐    ┌──────────────────────────────┐  │
│  │  Discovery Skill   │    │  Knowledge Base               │  │
│  │  (SKILL.md)        │    │  - AMBA baseline standards    │  │
│  │  - When to scan    │    │  - OGE monitoring thresholds  │  │
│  │  - Procedure steps │    │  - Rule definitions (YAML)    │  │
│  │  - How to report   │    │  - Customer-specific policies │  │
│  └────────┬──────────┘    └──────────────────────────────┘  │
│           │                                                  │
│  ┌────────▼──────────┐    ┌──────────────────────────────┐  │
│  │  Python Tool       │    │  Built-in Agent Capabilities  │  │
│  │  - ARG queries     │    │  - az CLI commands            │  │
│  │  - Rules engine    │    │  - Log Analytics / KQL        │  │
│  │  - JSON output     │    │  - Deep Investigation         │  │
│  │  - MG scope        │    │  - Remediation (with approval)│  │
│  │  (deterministic)   │    │  - Memory & Knowledge Graph   │  │
│  └───────────────────┘    └──────────────────────────────┘  │
│                                                              │
│  SCAN ──→ EVALUATE ──→ REPORT ──→ INVESTIGATE ──→ REMEDIATE │
│  (all in one platform, unified workflow)                     │
└─────────────────────────────────────────────────────────────┘
```

**How it works:**

1. **Python Tool** (`observability-discovery-scan`): Contains the ARG query logic, rules engine, and JSON output generation — ported from the existing `discovery/` module. Uses `azure-mgmt-resourcegraph` + managed identity. Returns the same structured JSON schema. This is deterministic code, not LLM-generated text.

2. **Skill** (`SKILL.md`): Defines the procedure — when to trigger the discovery scan, how to interpret results, what follow-up actions to take (investigate gaps, propose remediation, send reports).

3. **Knowledge Base**: AMBA standards, OGE-specific thresholds, customer monitoring policies. The agent references these when evaluating findings and proposing remediation.

4. **Scheduled Task**: Runs the discovery scan on a cron schedule (daily/weekly). The agent executes the Python tool, evaluates findings against the Knowledge Base, and can automatically investigate and remediate gaps — all in one execution flow.

5. **Output Delivery**: Python tool can POST structured JSON to a Storage Account, Event Grid, or webhook for external consumption. Scheduled task can also send summaries via Teams/Outlook connector.

**Pro:**
- Single platform for the entire scan → evaluate → investigate → remediate → learn lifecycle
- No separate CLI to maintain, deploy, or version independently
- Agent learns from discovery findings over time (persistent memory)
- Customer-extensible via Knowledge Base updates (no code changes)
- Leverages SRE Agent as the team's agreed-upon core engine

**Con:**
- No on-demand CI/CD pipeline trigger (see Production Considerations below)
- 900-second Python tool timeout ceiling (see Production Considerations below)
- Locked into SRE Agent platform; discovery logic is less portable
- No persistent Python state between executions (results must be stored externally)

## Production Considerations

### Timeout Ceiling (900 seconds)

The Python tool execution environment has a maximum timeout of **900 seconds (15 minutes)**. Impact analysis for discovery workloads:

| Estate Size | Estimated Scan Time | Within Limit? |
|---|---|---|
| **Small** (1 subscription, <500 resources) | ~5–10 seconds | ✅ Yes |
| **Medium** (5–10 subscriptions, 1K–10K resources) | ~15–45 seconds | ✅ Yes |
| **Large** (20–50 subscriptions, 10K–50K resources) | ~1–3 minutes | ✅ Yes |
| **Enterprise** (50+ subscriptions, 100K+ resources) | ~3–8 minutes | ✅ Yes (with pagination) |
| **Extreme** (200+ subscriptions, 500K+ resources) | ~8–15 minutes | ⚠️ At the boundary |

**Why this is manageable:** Azure Resource Graph returns results in sub-seconds even for 100K+ resource estates. The discovery module already pages at 1,000 rows via `skip_token`. The bulk of execution time is ARG query + rules evaluation + JSON serialization — all CPU-bound Python, not waiting on network. The 900-second ceiling is generous for this workload.

**Mitigation for extreme estates:** Partition scans by subscription or management group across multiple scheduled task runs. Each task covers a subset of the estate, with results aggregated in a Storage Account.

### CI/CD Pipeline Integration

SRE Agent has **no REST API** for on-demand programmatic invocation. The closest alternatives:

| Approach | How It Works | Trade-off |
|---|---|---|
| **Scheduled task (cron)** | Runs discovery on a defined schedule (hourly, daily, weekly) | Not triggered by code changes; covers most operational use cases |
| **Python tool → webhook** | Tool POSTs JSON results to an Azure Function or Logic App endpoint | Pipeline can be triggered by the webhook; adds one integration hop |
| **Python tool → Storage Account** | Tool writes JSON to blob storage; Event Grid triggers downstream pipeline | Pipeline reacts to blob creation; standard Azure event-driven pattern |
| **Dual-deploy shared code** | Same Python discovery logic packaged as both an SRE Agent Python tool AND an Azure Function | Pipeline calls the Function directly; SRE Agent uses the same logic internally. Requires maintaining shared code in two deployment targets. |

**Assessment:** For most OGE customer scenarios, a **daily or weekly scheduled scan** with results pushed to Storage Account or Teams covers the operational need. True CI/CD integration (trigger discovery on every PR or deployment) would require the dual-deploy pattern or a webhook intermediary. This should be validated against actual customer requirements.

### Scale and Multi-Tenancy

- **Per-subscription RBAC**: Each target subscription requires explicit Reader role assignment on the agent's managed identity. For large estates, automate this via a script or Azure Policy.
- **No Management Group RBAC in SRE Agent docs**: However, the Python tool can pass Management Group scope to the ARG SDK directly, since it's executing Python code with ARM-scoped managed identity. This is the same approach as the standalone module.
- **Multiple agents**: For very large enterprises, consider one SRE Agent per business unit or region, each with its own scheduled discovery task.

## Recommendation

**Present Options C and D for team discussion** at the May 26 OGE Observability Sync.

Both options are viable. The choice depends on the team's answers to these questions:

| Question | If Yes → Option C | If Yes → Option D |
|---|---|---|
| Do OGE customers need discovery results in CI/CD pipelines? | ✅ Standalone CLI is pipeline-native | ⚠️ Requires webhook/storage workaround |
| Is minimizing the number of systems to maintain a priority? | | ✅ Single platform |
| Will the team build investigation + remediation workflows on top of discovery? | ⚠️ Requires integration between CLI output and agent | ✅ Native — agent acts on its own findings |
| Do we want discovery to improve over time via agent memory? | | ✅ Agent learns from every scan |
| Is portability important (run discovery outside of SRE Agent)? | ✅ CLI runs anywhere Python runs | ⚠️ Logic is tied to SRE Agent platform |

### Proposed Next Steps

1. **Team discussion** — Review Options C and D at the May 26 OGE Observability Sync
2. **Customer validation** — Determine whether OGE customers need pipeline-integrated reports, interactive remediation, or both
3. **Prototype** — Regardless of option chosen, build one SRE Agent Skill + Python tool that demonstrates discovery-to-remediation in a single agent flow
4. **Update Story #37** — Adjust acceptance criteria based on the agreed architecture

## Decision

**Pending** — to be decided at OGE Observability Sync (May 26, 2026).
