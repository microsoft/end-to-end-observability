# SRE EA Governance & Drift

The governance watch. Daily, it measures compliance, tagging, ownership, exposure,
configuration drift, and DR coverage across the tenant, and raises owner-routed
findings that keep the estate clean and auditable.

## Contents

| File | Purpose |
| --- | --- |
| `sre-ea-governance-drift.agent.md` | The scheduled-task definition (daily). Paste the body into the agent's **Scheduled task** form, run mode **Autonomous**. |

## What it does each run

1. Inventory + tagging gaps (unowned/untagged production workloads).
2. Top non-compliant policy initiatives by scope.
3. Posture/exposure (public resources, broad RBAC, Advisor items).
4. Drift vs the last `HealthModelSnapshot_CL`.
5. DR coverage gaps for critical workloads.
6. Write owner-routed governance findings to `SREAgentRecommendations_CL` (#7);
   refresh the drift snapshot.

## Recommend-only

Proposes remediation/exemption with owner routing; never changes policy, RBAC,
tags, or deletes resources.

## Register it (manual, portal)

1. `sre.azure.com` → **Scheduled tasks** → New, name `sre-ea-governance-drift`,
   cadence **daily**, run mode **Autonomous**.
2. Paste the markdown **body** of `sre-ea-governance-drift.agent.md`.
3. Leave **disabled**; enable after review.
4. Verify: workbook **#7 Enterprise Architecture & Governance** Top 10 shows rows
   with `CreatedBy = sre-agent/ea-governance-drift`.

See the [Operating Pack catalog](../README.md).
