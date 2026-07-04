# SRE Agent Observability Workbooks

A suite of **10 Azure Workbooks** for end-to-end, tenant-wide observability, with
the **Azure SRE Agent at the center**. The workbooks are operating surfaces for
both the agent and humans: every page opens with a reusable **Top 10 SRE Agent
Recommendations** section sourced from a central recommendation table.

> Built as code. The deployable `workbooks/*.workbook.json` are **assembled** by a
> Python builder from shared libraries plus one compact spec per workbook — so the
> Top 10 block and the 13 shared parameters are byte-identical on every page.

## The 10 workbooks

| # | Workbook | Purpose |
| --- | --- | --- |
| 1 | **SRE Agent Command Center** | Agent-detected tenant health, incidents, confidence, automation state. |
| 2 | **Tenant Health Model** | Health hierarchy and the agent's health interpretation (Unknown ≠ Healthy). |
| 3 | **Incident Triage & RCA** | Correlated evidence, blast radius, root-cause candidates, remediation. |
| 4 | **Automation & Remediation** | Automation catalog, runs, approvals, guardrails, rollback. |
| 5 | **Operations Command Center** | NOC/SRE infra view: alerts, Service/Resource Health, compute/network/DR. |
| 6 | **Application Reliability** | SLOs, error budget, journeys, latency, dependencies, releases. |
| 7 | **Enterprise Architecture & Governance** | Inventory, policy/tag compliance, posture, drift, DR coverage. |
| 8 | **Observability Quality & Cost** | Telemetry completeness, workbook quality, alert hygiene, LA cost. |
| 9 | **Security & Identity Observability** | Defender posture, exposure, Key Vault, RBAC, identity. |
| 10 | **SRE Agent Workbook Maintenance** | Weekly self-review of workbook health and improvements. |

## Repository layout

```
sre-agent-observability-workbooks/
  builder/
    build_workbooks.py        # assembles workbooks/*.workbook.json + manifest.json
    shared/                   # parameters, Top 10 pattern, section/tile helpers
    specs/                    # one spec per workbook (its sections)
  workbooks/                  # GENERATED deployable artifacts (+ manifest.json)
  infra/
    terraform/                # production path
    bicep/                    # ARM/CLI path
  scripts/                    # Deploy-Workbooks.ps1, New-BicepParam.ps1, New-SampleRecommendations.ps1
  parameters/                 # sample.parameters.json
  queries/
    kql/                      # top10-recommendations.kql, recommendation-score.kql
    resource-graph/           # inventory.kql, governance.kql
  automation/weekly-review/   # weekly self-maintenance agent + scaffold
  tests/validate_workbooks.py # schema/param/Top-10/scoping validator
  docs/                       # schema, scoring, deployment, operating model, data sources
```

## Quick start

```powershell
cd sre-agent-observability-workbooks

# 1. Build + validate
python builder/build_workbooks.py
python tests/validate_workbooks.py        # -> "All 10 workbook(s) valid."

# 2. Deploy (fast test path; run in-session with &)
cd scripts
& ./Deploy-Workbooks.ps1 -WorkspaceResourceId "/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.OperationalInsights/workspaces/<ws>"

# 3. Seed sample recommendations so the Top 10 panels render
& ./New-SampleRecommendations.ps1 -Count 30 -OutFile sample-recommendations.json
```

Production deploys use Terraform (`infra/terraform`) or Bicep (`infra/bicep`); all
three paths produce identical workbooks with the same deterministic names. See
[docs/deployment.md](./docs/deployment.md).

## How the SRE Agent writes recommendations

The agent writes rows to the central custom table **`SREAgentRecommendations_CL`**
via the Logs Ingestion API. Each row sets `WorkbookName` + `WorkbookPage` (matching
`workbooks/manifest.json`), a `RecommendationScore`, an `AgentActionType`,
`ApprovalRequired`, `RemediationRisk`, and `RollbackAvailable`. Each page's Top 10
query filters to that page, keeps active non-expired statuses, dedupes to the
latest revision, and ranks by score. Full contract:
[docs/recommendation-schema.md](./docs/recommendation-schema.md) and
[docs/scoring-model.md](./docs/scoring-model.md).

The agent's **Detect** stage is implemented as a runnable task,
[`automation/detect-health/Invoke-DetectHealth.ps1`](./automation/detect-health/Invoke-DetectHealth.ps1):
it polls live Azure signals (policy compliance, fired alerts, resource health,
recent changes, Application Insights latency), scores each with the documented
model, maps it to the owning workbook page, and ingests real rows (tagged
`CreatedBy = sre-agent/detect-health`). Run it to populate the Top 10 panels with
genuine findings instead of the sample seed
([`scripts/New-SampleRecommendations.ps1`](./scripts/New-SampleRecommendations.ps1),
`CreatedBy = sre-agent`).

## Editing the workbooks

Do **not** hand-edit `workbooks/*.workbook.json`. Edit `builder/specs/*.py` (or the
shared libraries under `builder/shared/`) and rebuild. The validator enforces the
13 shared parameters, the Top 10 tile, correct scoping, and that no build-time
placeholders leak. `command_center.py` is the reference spec for the others.

## Safe update / operating model

The agent auto-applies only low-risk metadata; medium/high-risk definition changes
go through a PR against `builder/specs/*`; and SLO/alert/health-scoring/
remediation/access/panel-deletion changes are **mandatory approval**. Every change
is versioned, validated, auditable, and reversible. See
[docs/operating-model.md](./docs/operating-model.md).

## Before production

- Create `SREAgentRecommendations_CL` + its DCE/DCR Logs Ingestion pipeline and
  grant the agent's identity **Monitoring Metrics Publisher**
  ([docs/data-sources.md](./docs/data-sources.md)).
- Supply tenant-specific placeholder tables where panels reference them
  (`SREAgentActions_CL`, `HealthModelSnapshot_CL`, workbook-QA tables, etc.).
- Set the **LogAnalyticsWorkspaceId** parameter (and the optional scope params) for
  each workbook, or template them in your IaC parameters.
- Replace runbook / action-group / CMDB placeholders with real targets.

## Docs

| Doc | Contents |
| --- | --- |
| [recommendation-schema.md](./docs/recommendation-schema.md) | `SREAgentRecommendations_CL` schema, status lifecycle, action types. |
| [scoring-model.md](./docs/scoring-model.md) | `RecommendationScore` formula and term bands. |
| [deployment.md](./docs/deployment.md) | Build → deploy via PowerShell / Terraform / Bicep. |
| [operating-model.md](./docs/operating-model.md) | Roles, safe-update model, guardrails, invariants. |
| [data-sources.md](./docs/data-sources.md) | ARG / LA / App Insights / Metrics sources; ingestion setup. |
| [automation/weekly-review](./automation/weekly-review/README.md) | Weekly self-maintenance. |
