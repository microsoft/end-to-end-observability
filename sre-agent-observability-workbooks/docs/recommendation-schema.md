# SREAgentRecommendations_CL — Central Recommendation Schema

Every page of every workbook in this suite opens with a **Top 10 SRE Agent
Recommendations** section. All of them read from a single central custom log
table, **`SREAgentRecommendations_CL`**, in the suite's Log Analytics workspace.
This table is the contract between the SRE Agent (the writer) and the workbooks
(the readers).

## Table assumptions

- A **DCR-based custom table** (created via a Data Collection Rule), so field
  names are clean (`Severity`, not `Severity_s`). If you instead use the legacy
  HTTP Data Collector API, Log Analytics appends type suffixes (`_s`, `_d`, `_b`,
  `_t`) and you must update the workbook/query column names accordingly.
- Written by the SRE Agent via the **Logs Ingestion API** (DCE + DCR + custom
  table). See [data-sources.md](./data-sources.md) for setup.

## Schema

| Field | Type | Notes |
| --- | --- | --- |
| `TimeGenerated` | datetime | Ingestion timestamp (required by LA). |
| `RecommendationId` | string (guid) | Stable ID across revisions; `arg_max` dedupes on it. |
| `WorkbookName` | string | **Must equal** the workbook display name (see `workbooks/manifest.json`). |
| `WorkbookPage` | string | **Must equal** the page name (manifest `page`). |
| `Rank` | int | Optional pre-computed rank; the query re-ranks by score. |
| `Severity` | string | `Sev0`..`Sev4`. |
| `Category` | string | e.g. Reliability, Telemetry coverage, Security, Cost, Governance, Root cause, Post-incident. |
| `RecommendationText` | string | The action, human-readable. |
| `Reason` | string | Why the agent raised it. |
| `EvidenceSummary` | string | One-line evidence summary. |
| `EvidenceLinks` | string (url) | Drilldown to evidence (rendered as a link). |
| `AffectedScope` | string | Scope string (sub/RG/MG/workload). |
| `AffectedResourceId` | string | Specific resource, if any. |
| `ApplicationName` | string | Workload/app association. |
| `BusinessCriticality` | string | Mission critical / High / Medium / Low. |
| `OwnerTeam` | string | Routing target. |
| `ConfidenceScore` | real | 0..1. |
| `RecommendationScore` | real | Ranking score (see [scoring-model.md](./scoring-model.md)). |
| `AgentActionType` | string | See action types below. |
| `ApprovalRequired` | bool | True for all risk-bearing changes. |
| `RemediationRisk` | string | Low / Medium / High. |
| `RollbackAvailable` | bool | Whether a rollback exists. |
| `Status` | string | See lifecycle below. |
| `CreatedBy` | string | Agent identity. |
| `LastUpdated` | datetime | Latest revision time; ordering + `arg_max` key. |
| `ExpiresOn` | datetime | Excluded when in the past. |
| `RelatedIncidentId` | string | Incident correlation. |
| `RelatedAlertId` | string | Alert correlation. |
| `RelatedPullRequest` | string (url) | PR for medium/high-risk changes. |
| `RelatedWorkItem` | string | ADO/ITSM work item (used as the Runbook/work-item link). |

## Status lifecycle

```
New → Pending approval → Approved → In progress → Applied → Validated
                      ↘ Rejected
                      ↘ Suppressed
                      ↘ Expired
In progress → Failed → Rolled back
```

Full set: `New`, `Pending approval`, `Approved`, `In progress`, `Applied`,
`Validated`, `Rejected`, `Suppressed`, `Expired`, `Failed`, `Rolled back`.

**The Top 10 query surfaces only active statuses:** `New`, `Pending approval`,
`Approved`, `In progress`, `Failed` — and excludes anything past `ExpiresOn`.

## Agent action types

`Inform`, `Recommend`, `Open ticket`, `Notify owner`, `Open PR`,
`Auto-update workbook`, `Auto-remediate`, `Request approval`, `Suppress`,
`Escalate`.

## How the Top 10 query uses these fields

For each page the query (`queries/kql/top10-recommendations.kql`):

1. Filters `WorkbookName` + `WorkbookPage` to the page's identity.
2. Keeps only active statuses; excludes expired.
3. Optionally narrows by the `Severity`, `BusinessCriticality`, `OwnerTeam`, and
   `RecommendationStatus` workbook parameters.
4. `summarize arg_max(LastUpdated, *) by RecommendationId` — latest revision only.
5. `order by RecommendationScore desc, Severity asc, LastUpdated desc` → `take 10`.
6. Projects rank, severity, recommendation, scope, owner, confidence, agent
   action, approval required, status, last updated, and the
   Evidence/Incident/PR/Runbook drilldown links.

## Companion tables (placeholders, documented)

These are referenced by some panels and are tenant-specific:

- `SREAgentActions_CL` — automation/audit log (ActionId, ActionType,
  TargetResourceId, Outcome [Running/Succeeded/Failed/Blocked], BlockedReason,
  Approver, RollbackAvailable, StartedOn, CompletedOn).
- `SREAgentAutomationCatalog_CL` — automation catalog.
- `HealthModelSnapshot_CL` — last modeled topology (drift detection).
- `WorkbookQA_CL` / `WorkbookReviewRuns_CL` / `WorkbookVersions_CL` — weekly-review
  outputs and version registry.

See [data-sources.md](./data-sources.md) for the full placeholder inventory.
