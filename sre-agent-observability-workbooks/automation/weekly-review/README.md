# Weekly SRE Agent Workbook Review

Scheduled review that keeps the **SRE Agent Observability Workbooks** suite
correct, complete, fast, and trustworthy. This is the automation behind workbook
#10, *SRE Agent Workbook Maintenance*, and it enforces the suite's **safe update
model**.

## Contents

| File | Purpose |
| --- | --- |
| `sre-workbook-maintenance.agent.md` | The agent definition (operating principles, review steps, change classification, guardrails). |
| `Invoke-WeeklyWorkbookReview.ps1` | Mechanical harness: runs every panel query, flags empty/slow/failed panels, checks coverage, writes findings JSON. |

## What it does each week

1. **Freshness** — confirms each page's primary tables have recent data.
2. **Panel health** — runs every Log Analytics panel; flags *Empty panel*,
   *Query failure*, *Slow query*.
3. **Coverage** — new subscriptions / resource types not represented; resources
   missing telemetry.
4. **Drift** — health-model drift vs the last snapshot.
5. **Alert quality** — noisy rules, rules without action groups, AMBA gaps.
6. **Usage & RCA** — prioritizes by adoption and recent incident learnings.
7. **Writes findings** to `SREAgentRecommendations_CL`
   (`WorkbookName = "SRE Agent Workbook Maintenance"`).

## Safe update model (enforced)

| Change | Action | Approval |
| --- | --- | --- |
| Live data refresh | automatic | none |
| Low-risk metadata (titles, descriptions, links, formatting) | auto-apply, versioned, logged | none |
| Medium/high-risk definition (panels, query logic) | open PR against `builder/specs/*` | human review |
| SLO / alert thresholds, health scoring, remediation, access scope, panel deletion | propose only | **mandatory** |

The workbooks are **built from `builder/`** — the agent proposes changes as edits
to `builder/specs/*.py` (or shared libraries) and rebuilds. It never hand-edits
`workbooks/*.workbook.json`.

## Run it

```powershell
cd sre-agent-observability-workbooks/automation/weekly-review
# In-session (nested powershell -File hangs on the first az call):
& ./Invoke-WeeklyWorkbookReview.ps1 -WorkspaceResourceId "<workspace-resource-id>"
```

Findings are written to `review-findings.json` in the
`SREAgentRecommendations_CL` shape. Ingest them via your Logs Ingestion pipeline
(see `../../scripts/New-SampleRecommendations.ps1` and `../../docs/data-sources.md`).

## Schedule

Run weekly (e.g. an Azure Automation runbook, a scheduled pipeline, or the SRE
Agent's own scheduler). After a major incident or a large tenant change, run it
on demand.
