# SRE Application Reliability & SLO

The reliability watch for the application tier. Hourly, it measures user-facing
SLIs (failure rate, p95/p99 latency, availability, dependency health) against SLOs
and the error budget, and raises scored findings before a slow burn becomes an
incident.

## Contents

| File | Purpose |
| --- | --- |
| `sre-app-reliability-slo.agent.md` | The scheduled-task definition (hourly). Paste the body into the agent's **Scheduled task** form, run mode **Autonomous**. |

## What it does each run

1. Measure SLIs per workload/journey over the SLO window.
2. Compute remaining error budget and burn rate.
3. Identify failing/slow dependencies driving breaches.
4. Correlate regressions with recent releases.
5. Write scored reliability findings to `SREAgentRecommendations_CL` (workbook #6).

## Recommend-only

Proposes investigation or a gated remediation; never executes a fix.

## Register it (manual, portal)

1. `sre.azure.com` → **Scheduled tasks** → New, name `sre-app-reliability-slo`,
   cadence **hourly**, run mode **Autonomous**.
2. Paste the markdown **body** of `sre-app-reliability-slo.agent.md`.
3. Leave **disabled**; enable after review.
4. Verify: workbook **#6 Application Reliability** Top 10 shows rows with
   `CreatedBy = sre-agent/app-reliability-slo`.

See the [Operating Pack catalog](../README.md).
