# SRE Incident Triage & RCA

The **Triage / RCA** stage of the SRE Agent loop. On an Azure Monitor incident (or
on demand) it correlates evidence across the affected scope, estimates blast
radius, ranks root-cause candidates, and proposes — never executes — a remediation.

## Contents

| File | Purpose |
| --- | --- |
| `sre-incident-triage-rca.agent.md` | The trigger-driven task definition (response-plan body, operating principles, triage steps, gating, guardrails). Paste the body into the agent's **Response plan** for Azure Monitor incidents. |

## What it does per incident

1. Frame the incident → scope, owner, criticality, time window.
2. Correlate fired alerts; collapse alert storms to a single root cause.
3. Estimate blast radius (resources, apps, regions, downstream dependencies).
4. Correlate recent changes/deployments (`resourcechanges`, `AzureActivity`).
5. Analyze symptoms (App Insights failures/latency/exceptions/dependencies; metrics).
6. Tag Service Health platform overlap.
7. Rank root-cause candidates with evidence + confidence.
8. Write one scored triage finding to `SREAgentRecommendations_CL`
   (`WorkbookName = "Incident Triage & RCA"`), upserting on the incident ID.

## Recommend-only

This task **proposes**; it never mutates a resource. Risk-bearing proposals carry
`ApprovalRequired = true` and are handed to the gated `sre-remediation-act` task
with their evidence attached.

## Register it (manual, portal)

1. Open the agent at `sre.azure.com` → **Response plans**.
2. New response plan → trigger **Azure Monitor incident**, name
   `sre-incident-triage-rca`, run mode **Autonomous** (read-only triage).
3. Paste the markdown **body** of `sre-incident-triage-rca.agent.md` as the prompt.
4. Leave **disabled**; enable after a review window.
5. Verify: after a test incident, check workbook **#3 Incident Triage & RCA** →
   Top 10 panel for the row with `CreatedBy = sre-agent/incident-triage-rca` and a
   matching `RelatedIncidentId`.

See the [Operating Pack catalog](../README.md) and the
[pre-req checklist](../../../sre-agent-pre-req/sre-agent-integration-checklist.md).
