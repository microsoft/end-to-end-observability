# `deployment/` — End-to-End SRE Agent Deployment

One folder that packages the **entire onboarding pipeline** into a single, gated,
repeatable run. It does **not** replace each subproject's tooling — it *orchestrates*
it in the order the repo requires, so a new client is onboarded the same way every
time with minimal manual steps.

> **Work item:** User Story **#65 — End-to-End SRE Agent Testing**
> (`https://dev.azure.com/jahunte/End-to-End%20Observability`, project *End-to-End Observability*).

## Contents

| Path | What it is |
| --- | --- |
| `Deploy-EndToEnd.ps1` | Master orchestrator. Runs Phases 0–5 in order with validation **gates**; `-WhatIf`-safe and idempotent. |
| `config/deployment.parameters.example.json` | Central config (IDs, ring, region). Copy to `deployment.parameters.json` and fill in. |
| `templates/sre-agent.bicep` | Phase 4 IaC to create the agent (`Microsoft.App/agents@2026-01-01`). |
| `checklists/manual-gates.md` | The steps that are **inherently manual** (portal / RBAC / network / approvals). |
| `checklists/e2e-test-results.md` | Pass/fail matrix per component — the deliverable for Story #65. |

## Prerequisites

- Azure CLI authenticated (`az login`) with rights to the target subscriptions.
- Terraform `>= 1.9.0`, Python 3.11+, PowerShell 7+.
- Foundation from `sre-agent-pre-req` in place (MG hierarchy, central LAW, shared
  remediation UAMI, RBAC, egress allow-list).

## Quick start

```powershell
cd deployment
Copy-Item config/deployment.parameters.example.json config/deployment.parameters.json
# ...edit config/deployment.parameters.json...

# 1) Dry run everything first (no changes made)
./Deploy-EndToEnd.ps1 -WhatIf

# 2) Deploy telemetry -> alerting -> dashboards
./Deploy-EndToEnd.ps1 -FromPhase 1 -ToPhase 3

# 3) Create the agent (opt-in) and deploy tasks PAUSED
./Deploy-EndToEnd.ps1 -FromPhase 4 -ToPhase 5 -DeployAgent
```

Run in-session with the call operator (never through a nested `powershell -File`
subprocess — that hangs on the first `az` call).

## The gated sequence

Order matters; each phase **gates** the next. Running out of order surfaces the very
findings the SRE Agent later reports.

| Phase | Subproject | Does | Gate |
| --- | --- | --- | --- |
| **0** | `sre-agent-pre-req` | Verify providers registered, LAW + UAMI resolvable | Foundation present |
| **1** | `monitoring-baselines` *(vendored)* | **Telemetry first**: AMA + diagnostic settings (Terraform) | Telemetry flowing to LAW |
| **2** | `amba-enterprise` | Alerting on top of telemetry (Terraform, ring-by-ring) | Alerts + action groups deployed |
| **3** | workbooks | Build + validate + deploy dashboards (Python + PS/TF/Bicep) | All workbooks valid + deployed |
| **4** | sre-agent | Create agent `Microsoft.App/agents`, `mode=Review` | Agent sees expected inventory |
| **5** | scheduled-tasks | Deploy tasks **PAUSED** | Tasks created, disabled |

**Why this order:** telemetry (P1) must exist before alerting (P2) — VM/VMSS/HybridVM
log alerts fail to remediate without AMA present. Dashboards (P3) must exist before
tasks (P5) write `SREAgentRecommendations_CL` rows. The agent (P4) is created in
Review mode; its tasks (P5) ship disabled and are enabled one at a time.

> **Phase 1 is self-contained.** It runs a **vendored, fixed copy** of
> `monitoring-baselines` under `deployment/monitoring-baselines/` (E2E fixes F1/F2/F5 for
> Story #65), so the shared subproject is never modified. See
> `monitoring-baselines/README.md`. Phases 2–5 invoke their shared subprojects in place,
> unmodified.

## Orchestrator parameters

| Parameter | Purpose |
| --- | --- |
| `-ConfigFile` | Path to config JSON (default `config/deployment.parameters.json`). |
| `-FromPhase` / `-ToPhase` | Run a subset of phases (0–5). |
| `-WhatIf` | Dry run: Terraform stops after `plan`; scripts pass through their own `-WhatIf`. |
| `-DeployAgent` | Opt-in: Phase 4 actually creates the agent (otherwise prints the command). |
| `-ActivateTasks` | Opt-in: Phase 5 enables tasks (default leaves them PAUSED). |

## What stays manual

Full automation is intentionally **not** the goal for a few steps — see
[`checklists/manual-gates.md`](checklists/manual-gates.md). These include MG hierarchy
+ RBAC creation, egress allow-listing, moving `private_endpoint_effect` to Deny,
Entra group/user-role administration, and any change-management approval gates.

## Notes

- `config/deployment.parameters.json` (the filled-in copy) is **git-ignored** — it
  holds real tenant/subscription IDs. Commit only the `.example.json`.
- The orchestrator is idempotent: re-running updates in place (Terraform apply,
  create-or-update workbooks, task-by-name skip).
