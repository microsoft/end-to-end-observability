# SRE Detect-Health Heartbeat

The always-on **Detect** stage of the SRE Agent loop for the *SRE Agent
Observability Workbooks* suite. On a short schedule it converts raw platform
state into scored, evidence-backed **recommendations** — it never remediates.

## Contents

| File | Purpose |
| --- | --- |
| `sre-detect-health.agent.md` | The scheduled-task definition (frontmatter schedule + `enabled: false`, operating principles, detect steps, gating defaults, guardrails). |
| `Invoke-DetectHealth.ps1` | **Runnable** implementation of the same Detect logic for local/CI use or a scheduled runner — queries real signals, scores them, and ingests rows to `SREAgentRecommendations_CL` via the Logs Ingestion API. |

## Run it now (local / CI)

Use the runnable script to produce real, scored recommendations from live signals
without waiting on the agent's in-portal scheduler. It maps every finding to the
owning workbook page, so the Top 10 panels fill with genuine agent data:

```powershell
cd automation/detect-health
# Dry run — print scored recommendations, do not ingest:
& ./Invoke-DetectHealth.ps1 -WorkspaceResourceId "<law-resource-id>" -WhatIf
# Ingest to SREAgentRecommendations_CL via the DCR:
& ./Invoke-DetectHealth.ps1 -WorkspaceResourceId "<law-resource-id>" `
    -DcrEndpoint "https://<dce>.<region>.ingest.monitor.azure.com" `
    -DcrImmutableId "dcr-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

`RecommendationId` is a stable hash of each signal, so re-running upserts the same
findings (the Top 10 query dedupes with `arg_max(LastUpdated)`). `CreatedBy` is
`sre-agent/detect-health`, distinguishing real findings from sample seed rows.

## What it does each run

1. **Health** — `healthresources` for Unavailable/Degraded/Unknown resources.
2. **Alerts** — fired, unresolved `alertsmanagementresources`; detects alert storms.
3. **Change correlation** — `resourcechanges` correlated with new unhealthy scopes.
4. **Service Health** — tags scopes touched by active platform issues.
5. **Score** — severity × confidence × blast radius → `RecommendationScore`.
6. **Upsert findings** — deduped rows to `SREAgentRecommendations_CL`.
7. **Narrate** — per-page `SREAgentNarratives_CL` (state + top 3 actions).

## Recommend-only

This task **proposes**; it does not execute. Medium/high-risk proposals carry
`ApprovalRequired = true` and are handled by the gated Act/Verify tasks, not here.

## Deploy (disabled, review one at a time)

Scheduled tasks on Azure SRE Agent (preview) are created in the **sre.azure.com
portal** — there is no ARM resource or `az` command for them. Paste this task's
prompt body into the agent's *Create scheduled task* form:

1. Open the agent: `https://sre.azure.com/agents/subscriptions/832c2234-b1ac-4faf-af30-9525e7ac4a9b/resourceGroups/rg-sre-lab/providers/Microsoft.App/agents/sre-agent-f7gvab4i42zky`
2. New scheduled task → name `sre-detect-health`, cadence **every 15 minutes**.
3. Paste the markdown body of `sre-detect-health.agent.md` as the prompt.
4. Leave the task **disabled** — enable only after you review and test it.

## Schedule & cost

Highest-frequency task in the suite (every 15 min). Per **Story #59**, record
per-run agent-unit consumption and duration so the cost reference model can
compute `reference_cost = units_per_run × runs_per_day × days`, then tune cadence
to budget before enabling in production.
