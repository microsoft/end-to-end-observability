# SRE Verify & Validate

The **Verify** stage of the SRE Agent loop. After a remediation is Applied, it
proves the fix worked (signal cleared, health recovered, SLO met) and that nothing
else regressed — then advances the recommendation to `Validated`, or requests
rollback on failure.

## Contents

| File | Purpose |
| --- | --- |
| `sre-verify-validate.agent.md` | The trigger-driven task definition (runs after an action reaches Applied). Paste the body into a **Response plan**, run mode **Autonomous** (read-only). |

## What it does per applied action

1. Recompute the original trigger signal over a post-action window.
2. Confirm the SLO is met; record the error-budget delta.
3. Scan for regressions (new alerts/exceptions/latency).
4. Decide: `Validated`, request rollback (`Failed → Rolled back`), or inconclusive.
5. Write the verification summary to `SREAgentRecommendations_CL` (workbook #6).

## Verify-only

Confirms or escalates; it never remediates. Rollback is **requested** and executed
by the gated `sre-remediation-act` task.

## Register it (manual, portal)

1. `sre.azure.com` → your agent → **Response plans** → New, trigger *action
   applied*, name `sre-verify-validate`, run mode **Autonomous**.
2. Paste the markdown **body** of `sre-verify-validate.agent.md`.
3. Leave **disabled**; enable after review.
4. Verify: workbooks **#6 Application Reliability** / **#4 Automation & Remediation**
   show validation rows (`CreatedBy = sre-agent/verify-validate`).

See the [Operating Pack catalog](../README.md).
