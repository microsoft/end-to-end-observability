# SRE Coverage & Onboarding

The **Coverage / Onboarding** stage of the SRE Agent loop — the "are we watching
*everything*?" task. Daily, it reconciles the managed scope against what is actually
emitting telemetry and alerts, and proposes the baseline changes that close the gaps.

## Contents

| File | Purpose |
| --- | --- |
| `sre-coverage-onboarding.agent.md` | The scheduled-task definition (daily). Paste the body into the agent's **Scheduled task** form. |

## What it does each run

1. Reconcile inventory; find new resources / subscriptions not yet in scope.
2. Find resources missing diagnostic settings to the central workspace.
3. Find VMs/VMSS/Arc without the Azure Monitor Agent (or DCR association).
4. Find app-tier components without Application Insights.
5. Compare critical resource types against AMBA alert baselines.
6. Write owner-routed, scored coverage findings to `SREAgentRecommendations_CL`
   (workbooks #8 Quality & Cost and #7 EA & Governance).

## Recommend-only

Proposes baseline changes (diagnostics, AMA, AMBA, App Insights) with gating; the
gated remediation task deploys them, the verify task confirms.

## Register it (manual, portal)

1. `sre.azure.com` → your agent → **Scheduled tasks** → New, name
   `sre-coverage-onboarding`, cadence **daily**, run mode **Autonomous** (read-only).
2. Paste the markdown **body** of `sre-coverage-onboarding.agent.md`.
3. Leave **disabled**; enable after review.
4. Verify: workbooks #7/#8 Top 10 panels show rows with
   `CreatedBy = sre-agent/coverage-onboarding`.

See the [Operating Pack catalog](../README.md).
