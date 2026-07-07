# SRE Agent Operating Pack

The portable set of **Azure SRE Agent skills / scheduled tasks** that make the SRE
Agent the *brain* of end-to-end observability. Each task is a **portal-pasteable
prompt** (`*.agent.md`) the agent runs on a schedule or on a trigger; together they
implement the full agent loop and feed the central
[`SREAgentRecommendations_CL`](../docs/recommendation-schema.md) table that every
page of the [10-workbook suite](../README.md) reads.

> **Why prompts, not API calls.** The SRE Agent's *Agent Extensions* (scheduled
> tasks / skills) API is restricted to internal Microsoft tenants, so tasks are
> registered **manually in the portal** (`sre.azure.com`). This pack is therefore
> deliverable to any client: an operator pastes each prompt into the agent's
> *Create scheduled task* / *Response plan* form. See [Manual setup](#manual-setup-registering-a-task-in-the-portal).

## The agent loop

```
Detect ──► Triage / RCA ──► Recommend ──► Act (gated) ──► Verify ──► Learn / Maintain
   ▲                                                                      │
   └──────────────────────── continuous, scored, evidence-backed ────────┘
```

Every task is **recommend-first**: it writes scored, evidence-backed rows to
`SREAgentRecommendations_CL`; risk-bearing changes carry `ApprovalRequired = true`
and are executed only by the gated Act/Verify tasks under the
[safe-update model](../docs/operating-model.md).

## Task catalog

| # | Task (skill) | Loop stage | Cadence | Default run mode | Feeds workbook | Status |
| --- | --- | --- | --- | --- | --- | --- |
| T1 | [detect-health](./detect-health/) | Detect | every 15 min | Autonomous (read-only) | #1 Command Center, #2 Tenant Health, #5 Operations | **built** |
| T2 | [coverage-onboarding](./coverage-onboarding/) | Detect / Onboard | daily | Autonomous (read-only) | #7 EA & Governance, #8 Quality & Cost | **built** |
| T3 | [incident-triage-rca](./incident-triage-rca/) | Triage / RCA | on incident | Autonomous (read-only) | #3 Incident Triage & RCA | **built** |
| T4 | [remediation-act](./remediation-act/) | Act | on approval | **Review** | #4 Automation & Remediation | **built** |
| T5 | [verify-validate](./verify-validate/) | Verify | post-action | Autonomous (read-only) | #6 Application Reliability | **built** |
| T6 | [app-reliability-slo](./app-reliability-slo/) | Detect | hourly | Autonomous (read-only) | #6 Application Reliability | **built** |
| T7 | [ea-governance-drift](./ea-governance-drift/) | Detect | daily | Autonomous (read-only) | #7 EA & Governance | **built** |
| T8 | [observability-quality-cost](./observability-quality-cost/) | Detect | daily | Autonomous (read-only) | #8 Quality & Cost | **built** |
| T9 | [security-identity-posture](./security-identity-posture/) | Detect | daily | **Review** | #9 Security & Identity | **built** |
| T10 | [weekly-review](./weekly-review/) | Learn / Maintain | weekly | Review | #10 Workbook Maintenance | **built** |

Each task folder holds the `*.agent.md` prompt (portal body), a short `README.md`,
and any optional runnable scaffold (e.g.
[`detect-health/Invoke-DetectHealth.ps1`](./detect-health/Invoke-DetectHealth.ps1))
for local/CI use where the portal scheduler isn't available.

## Shared contract (every task)

1. **Write to the central table.** One row per finding in
   `SREAgentRecommendations_CL` with `WorkbookName` + `WorkbookPage` matching
   [`workbooks/manifest.json`](../workbooks/manifest.json), a `RecommendationScore`
   ([scoring model](../docs/scoring-model.md)), `AgentActionType`,
   `ApprovalRequired`, `RemediationRisk`, `RollbackAvailable`, and evidence.
2. **Stable IDs / upsert.** `RecommendationId` is a stable hash of the signal so
   re-runs update (not duplicate) a finding; the Top 10 query dedupes with
   `arg_max(LastUpdated)`.
3. **Unknown is not Healthy.** Missing health, no telemetry, or an empty result on
   a scope that should report is itself a finding.
4. **Audit.** Actions and approvals are recorded in `SREAgentActions_CL` and in the
   agent's own App Insights `customEvents` (see
   [pre-req checklist §5](../../sre-agent-pre-req/sre-agent-integration-checklist.md)).
5. **CreatedBy.** Real agent findings use `CreatedBy = sre-agent/<task-name>`,
   distinct from sample seed (`sre-agent`).

## Deploying the tasks

There are two surfaces; this pack supports both.

### A. Programmatic (data-plane API) — recommended, repeatable

> The ARM "Agent Extensions" surface (`Microsoft.App/agents/scheduledtasks`) is
> restricted to internal Microsoft tenants. The agent's **data-plane** endpoint
> (`https://<agent>.<region>.azuresre.ai/api/v1/scheduledtasks`) accepts the same
> create/pause operations for **any** tenant when the caller holds the **SRE Agent
> Administrator** (or Author) RBAC role on the agent resource.

[`Deploy-AgentTasks.ps1`](./Deploy-AgentTasks.ps1) registers the whole pack and
leaves every task **Paused** (safe by default):

```powershell
cd automation
& ./Deploy-AgentTasks.ps1 `
    -AgentResourceId "/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.App/agents/<agent>" `
    -Tenant "<tenant-guid>"
# tasks are created Paused; review in the portal, then enable one at a time.
```

Prerequisite RBAC (one-time, by an Owner / User Access Administrator):

```powershell
az role assignment create --assignee-object-id <your-object-id> --assignee-principal-type User `
    --role "SRE Agent Administrator" --scope "<AgentResourceId>"
```

The script strips each `*.agent.md` frontmatter, sanitizes non-ASCII (the API's JSON
parser rejects raw em-dashes/smart quotes), `POST`s a CreateScheduledTask request,
then pauses each task. Re-runnable: existing tasks (by name) are skipped.

### B. Manual (portal)

> Full prerequisites (region, identity, RBAC, networking, run modes) are in
> [`sre-agent-pre-req/sre-agent-integration-checklist.md`](../../sre-agent-pre-req/sre-agent-integration-checklist.md).

Per task:

1. **Open the agent** at `sre.azure.com` → your agent → **Scheduled tasks** (or
   **Response plans** for trigger-driven tasks like T3/T4). Switch the portal
   **Directory** to the tenant that owns the agent if needed.
2. **New scheduled task** → set **name** = the task's frontmatter `name`, and the
   **cadence** from the catalog above.
3. **Paste the prompt**: copy the markdown **body** of the task's `*.agent.md`
   (everything below the YAML frontmatter) into the task prompt.
4. **Set the run mode** from the catalog (Review for anything that can act; T9 and
   T4 are always **Review**).
5. **Leave it disabled**, then enable one task at a time after a Review-mode
   observation window (≥ 2 weeks for anything that can write — per the pre-req
   guardrails).
6. **Confirm the wiring**: after the first run, check the matching workbook page's
   **Top 10 SRE Agent Recommendations** panel for rows with
   `CreatedBy = sre-agent/<task-name>`.

## Prerequisite: the ingestion pipeline

The tasks write through the Azure Monitor **Logs Ingestion API** (DCE + DCR +
custom table). Create `SREAgentRecommendations_CL` (and `SREAgentActions_CL`) and
grant the agent's managed identity **Monitoring Metrics Publisher** on the DCR —
see [docs/data-sources.md](../docs/data-sources.md). Until the portal scheduler is
enabled, the runnable scaffolds (e.g. `Invoke-DetectHealth.ps1`) produce the same
rows through the same pipeline.

## Build order

All ten tasks now ship in this pack (T1–T10), each as its own `automation/<task>/`
folder with a portal-pasteable `*.agent.md` and a register-it `README.md`. T1
additionally ships a runnable scaffold (`Invoke-DetectHealth.ps1`) for local/CI use
where the portal scheduler isn't available. Enable them one at a time, in loop
order (Detect → Triage → Act → Verify → posture), after a Review-mode window.
