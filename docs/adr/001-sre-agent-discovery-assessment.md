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

### Where SRE Agent Falls Short

| Gap | Impact | Workaround |
|---|---|---|
| **No AMBA awareness** | Cannot evaluate Azure Monitor Baseline Alerts compliance out-of-box | Load AMBA docs into Knowledge Base + custom skill |
| **No Management Group scope** | Cannot sweep an entire MG hierarchy in one pass | Per-subscription RBAC grants |
| **No structured JSON export** | Output is chat/natural language, not a deterministic manifest | Custom Python tool to enforce JSON schema |
| **Non-deterministic output** | Same prompt may yield different formats/detail between runs | Python tool enforces output schema |
| **No programmatic API** | Cannot trigger from CI/CD pipeline like a CLI | Scheduled tasks + webhook/email output |
| **No declarative rules engine** | Rules aren't YAML-based, versioned, or customer-extensible | Skills approximate this, but less rigorous |

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

**SRE Agent cannot fully replace the discovery module as a standalone batch scanner.** However, the real question for OGE customers is whether they need:

1. **A compliance report** — run in a pipeline, hand to an ops team (discovery module strength)
2. **An interactive advisor** — finds gaps AND proposes/executes fixes (SRE Agent strength)
3. **Both** — scheduled scan for visibility, agent for remediation

### Key Insight

The discovery module's **rules, resource coverage, and structured output schema** remain valuable regardless of the engine. The question is *where* that logic lives — in standalone Python, or as SRE Agent Skills + Knowledge Base + Python tools.

## Options

### Option A: Keep Discovery Module Standalone, Use SRE Agent Downstream

- Discovery CLI runs in pipeline → produces JSON manifest
- SRE Agent consumes the manifest as context for investigation and remediation
- **Pro**: Deterministic, CI/CD-friendly, clear separation
- **Con**: Two systems to maintain; discovery rules duplicated if SRE Agent also needs them

### Option B: Migrate Discovery Logic Into SRE Agent

- Convert YAML rules → SRE Agent Skills + Knowledge Base
- Build a Python tool for structured JSON output
- Use scheduled tasks for recurring scans
- **Pro**: Single platform; agent can scan AND act on findings
- **Con**: Loses CI/CD integration, deterministic output, Management Group scope

### Option C: Hybrid — Discovery Module as SRE Agent Skill Input (Recommended)

- **Retain the discovery module** for structured, deterministic estate scanning
- **Publish discovery rules as SRE Agent Knowledge Base content** so the agent understands OGE monitoring standards
- **Build SRE Agent Skills** that reference discovery output and can investigate + remediate gaps
- **Use the discovery JSON manifest** as a scheduled task input for the agent
- **Pro**: Best of both — deterministic scanning + intelligent remediation
- **Con**: Requires integration design between the two systems

## Recommendation

**Option C (Hybrid)** — pending team discussion.

The discovery module remains the deterministic foundation. SRE Agent becomes the intelligent layer that acts on discovery findings. The rules and resource coverage we've built become shared assets used by both systems.

### Proposed Next Steps

1. **Team discussion** — Review this ADR at the May 26 OGE Observability Sync
2. **Customer validation** — Determine whether OGE customers need pipeline-integrated reports, interactive remediation, or both
3. **Prototype** — Build one SRE Agent Skill that consumes `discovery-report.json` and demonstrates gap remediation
4. **Update Story #37** — Adjust acceptance criteria based on the agreed architecture

## Decision

**Pending** — to be decided at OGE Observability Sync (May 26, 2026).
