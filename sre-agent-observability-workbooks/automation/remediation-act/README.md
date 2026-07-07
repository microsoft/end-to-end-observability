# SRE Remediation & Act (gated)

The **Act** stage of the SRE Agent loop — the *only* task that mutates resources,
and only after explicit human approval (OBO). It executes approved recommendations
within the documented blast radius, records every action with a rollback handle,
and advances the recommendation status.

## Contents

| File | Purpose |
| --- | --- |
| `sre-remediation-act.agent.md` | The gated, trigger-driven task definition. Paste the body into a **Response plan** with run mode **Review**. |

## What it does per approved recommendation

1. Re-validate the approval (Approved + Admin approver + fresh).
2. Run guardrail checks (policy/RBAC/hours/prod/owner/confidence/Service Health).
3. Capture a rollback snapshot (or block if none).
4. Execute the single approved action on the single target.
5. Record the outcome in `SREAgentActions_CL`; advance the recommendation status.
6. Hand the result to `sre-verify-validate`.

## Always Review mode

This task is **never** Autonomous. Every write goes through OBO approval by an
`SRE Agent Administrator`. It never widens scope and never silently changes SLOs,
alert thresholds, health scoring, access scope, or deletes panels.

## Register it (manual, portal)

1. `sre.azure.com` → your agent → **Response plans** → New, trigger
   *recommendation approved* (or your incident/approval trigger), name
   `sre-remediation-act`, run mode **Review**.
2. Paste the markdown **body** of `sre-remediation-act.agent.md`.
3. Leave **disabled**; enable only after ≥ 2 weeks of clean Review-mode approvals.
4. Verify: workbook **#4 Automation & Remediation** shows the action, approver,
   guardrails, and rollback state.

See the [Operating Pack catalog](../README.md) and
[operating model](../../docs/operating-model.md).
