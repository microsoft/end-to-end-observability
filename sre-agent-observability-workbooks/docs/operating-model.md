# Operating Model

The SRE Agent is the **center** of this observability solution. The 10 workbooks
are not just dashboards for humans — they are **operating surfaces** the agent
writes to and reads from. This document defines how the agent and humans share
control safely.

## Roles

- **SRE Agent** — observes the tenant, scores findings, writes recommendations to
  `SREAgentRecommendations_CL`, executes approved automations, and maintains the
  workbooks themselves (weekly review).
- **Humans (SRE / app teams / platform)** — set policy, approve risk-bearing
  changes, and act on routed recommendations.

## The recommendation loop

1. The agent detects an issue (or improvement) and computes a
   `RecommendationScore` (see [scoring-model.md](./scoring-model.md)).
2. It writes a row to `SREAgentRecommendations_CL` with an `AgentActionType`,
   `ApprovalRequired`, `RemediationRisk`, and `RollbackAvailable`.
3. The relevant workbook page surfaces it in **Top 10 SRE Agent Recommendations**.
4. A human approves (where required); the agent acts and records the outcome in
   `SREAgentActions_CL`.
5. Status advances through the lifecycle to `Validated` (or `Rolled back` on
   failure).

## Safe update model

| Change class | Who | Approval | Mechanism |
| --- | --- | --- | --- |
| Live data refresh | agent | none | automatic |
| Low-risk workbook metadata (titles, descriptions, links, formatting) | agent | none | auto-apply, versioned, logged |
| Medium/high-risk workbook definition (panels, query logic) | agent proposes | human review | **PR** against `builder/specs/*` |
| SLO thresholds | agent proposes | **mandatory** | PR + approval |
| Alert thresholds | agent proposes | **mandatory** | PR + approval |
| Health scoring / model | agent proposes | **mandatory** | PR + approval |
| Remediation logic | agent proposes | **mandatory** | PR + approval |
| Access scope (RBAC) | agent proposes | **mandatory** | PR + approval |
| Panel deletion | agent proposes | **mandatory** | PR + approval |

**The agent must never silently change** health scoring, SLO thresholds, alert
thresholds, remediation logic, access scope, or delete a workbook panel. These
are always `ApprovalRequired = true`.

## Guardrails

The agent blocks an action (and records it in `SREAgentActions_CL` with a
`BlockedReason`) when any guardrail trips:

- Blocked by policy
- Blocked by RBAC
- Blocked by business hours
- Blocked for production
- Blocked due to missing owner
- Blocked due to insufficient confidence
- Blocked due to missing rollback
- Blocked because Service Health indicates a platform issue

## Invariants

Every agent change must be:

- **Versioned** — the workbook is built from `builder/`; changes are commits.
- **Validated** — `tests/validate_workbooks.py` must pass before deploy.
- **Auditable** — recorded in `SREAgentActions_CL` with actor, time, and target.
- **Reversible** — a rollback exists (prior workbook version or remediation undo).

## Weekly self-maintenance

The agent reviews its own surfaces weekly (see
[`automation/weekly-review`](../automation/weekly-review/README.md)): freshness,
broken/empty/slow panels, new resources not represented, coverage gaps, drift,
and alert quality. It auto-applies only low-risk fixes and opens PRs for
everything else — the same safe update model, applied to the workbooks
themselves.
