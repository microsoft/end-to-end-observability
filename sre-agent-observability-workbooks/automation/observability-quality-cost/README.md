# SRE Observability Quality & Cost

Watches the monitoring estate itself. Daily, it checks telemetry completeness,
alert hygiene, workbook/panel quality, and Log Analytics ingestion cost — keeping
observability trustworthy *and* affordable.

## Contents

| File | Purpose |
| --- | --- |
| `sre-observability-quality-cost.agent.md` | The scheduled-task definition (daily). Paste the body into the agent's **Scheduled task** form, run mode **Autonomous**. |

## What it does each run

1. Telemetry completeness (missing/stale `Heartbeat`, empty-but-expected tables).
2. Alert hygiene (noisy / never-firing / action-group-less / flapping rules).
3. Workbook quality (empty/broken/slow panels, placeholder tables).
4. Ingestion & cost (volume by table/sub, commitment-tier fit, retention).
5. Write quality + cost findings to `SREAgentRecommendations_CL` (workbook #8).

## Recommend-only

Proposes hygiene and cost actions; never changes alert rules, thresholds,
retention, or pricing.

## Register it (manual, portal)

1. `sre.azure.com` → **Scheduled tasks** → New, name
   `sre-observability-quality-cost`, cadence **daily**, run mode **Autonomous**.
2. Paste the markdown **body** of `sre-observability-quality-cost.agent.md`.
3. Leave **disabled**; enable after review.
4. Verify: workbook **#8 Observability Quality & Cost** Top 10 shows rows with
   `CreatedBy = sre-agent/observability-quality-cost`.

See the [Operating Pack catalog](../README.md).
