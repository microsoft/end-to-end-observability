# E2E test results — Story #65 (End-to-End SRE Agent Testing)

Deliverable for User Story **#65**. Record pass/fail **per component** and note any
**OGE-specific** gaps. Validate against `sre-agent-pre-req/sre-agent-integration-checklist.md`.

- **Ring:** `sandbox`
- **Run date:** `2026-07-06`
- **Operator:** `@wilkinshum`
- **Environment:** lab `rg-sre-lab` (sub `832c2234…`, LAW `law-f7gvab4i42zky`, agent `sre-agent-f7gvab4i42zky`)
- **Config:** `deployment/config/deployment.parameters.json`
- **Command:** `./Deploy-EndToEnd.ps1 [-WhatIf] -FromPhase N -ToPhase N`

## Results per component

| Phase | Component | Method | Result | Notes / OGE-specific gaps |
| --- | --- | --- | --- | --- |
| 0 | Providers registered (`Microsoft.App`, `AlertsManagement`, `PolicyInsights`) | CLI | ⚠️ Partial | `Microsoft.AlertsManagement` = **NotRegistered** — pre-req gap (F3). Others Registered. |
| 0 | Central LAW resolvable | CLI | ✅ Pass | |
| 0 | Remediation UAMI resolvable | CLI | ✅ Pass | |
| 1 | monitoring-baselines — validate-parameters | PS | ✅ Pass | 3/3 tfvars pass governance. |
| 1 | monitoring-baselines — terraform init | Terraform | ✅ Pass | Requires backend config (F4) — used local override for test. |
| 1 | monitoring-baselines — terraform plan | Terraform | ✅ Pass | **Re-run 2026-07-07 after F1/F2/F5 fixes: `Plan: 5 to add, 0 to change, 0 to destroy` (exit 0).** All 20 built-ins resolved by GUID (F1); UAMI data source resolved principal `1ef8ee9a-…` from a lowercase-`resourcegroups` ID (F2); Arc AMA policies get empty params (F5). One non-blocking deprecation warning → F6. |
| 1 | AMA / diagnostic settings remediation | Portal/CLI | ⬜ Not reached | Plan is green; not applied to lab MG yet (`contoso-landingzones` MG + backend needed for apply). |
| 2 | amba-enterprise — validate-naming / parameters | PS | ⬜ Not run | Same backend/MG prereqs as P1. |
| 2 | amba-enterprise — terraform apply | Terraform | ⬜ Not run | |
| 3 | Workbooks — build | Python | ✅ Pass | 10 workbooks assembled. |
| 3 | Workbooks — validate ("All 10 valid") | Python | ✅ Pass | "All 10 workbook(s) valid." |
| 3 | Workbooks — deploy (PowerShell) | PS | ✅ Pass | All 10 deployed to lab LAW (idempotent). |
| 3 | Workbooks — Terraform parity path | Terraform | ⬜ Not tested | Parity path exists; not exercised this run. |
| 3 | Workbooks — Bicep parity path | Bicep | ⬜ Not tested | |
| 3 | Auto-generated workbooks (discovery) | Python | ⬜ N/A | Disabled in config (no discovery input). |
| 4 | SRE Agent create — Bicep (`Microsoft.App/agents`) | Bicep | ⚠️ N/A | Agent already exists; template builds (BCP081 only — verify api/region). |
| 4 | SRE Agent create — Marketplace/portal fallback | Portal | ⬜ N/A | |
| 4 | Agent managed-identity RBAC verified | CLI | ⬜ Not run | |
| 4 | Agent inventory ("what can you see?") | Portal | ⬜ Not run | |
| 5 | Scheduled tasks deployed PAUSED | PS | ✅ Pass | Endpoint resolved; 10 tasks present (skip-if-exists), all Paused. |
| 5 | Task enablement (one at a time) | Portal/PS | ⬜ Manual | Enable individually after review. |

## Orchestrator (`deployment/Deploy-EndToEnd.ps1`) — verdict

**PASS.** Drives every phase in gated order, `-WhatIf`-safe, idempotent, and correctly
**surfaces** downstream defects at the right gate. Two orchestrator bugs found and fixed
during this run (scriptblock scope in `Invoke-Native`; terraform backend handling for
local/dry-run via `*_override.tf`).

## Findings (error separated from fix — root-cause triage)

| # | Error observed | Root-cause class | Home for the fix |
| --- | --- | --- | --- |
| **F1** ✅ *fixed* | `terraform plan` (monitoring-baselines): built-in policy **not found by display name** — diag_storage, diag_app_service, diag_nsg, diag_keyvault | Defect (display-name drift; documented risk) | **Fixed** in `deployment/monitoring-baselines/terraform/main.tf` — all AMA/diagnostic/PE built-ins now resolved by **immutable GUID** (`data.azurerm_policy_definition.name`), not display name. GUIDs verified against live built-ins 2026-07-07. |
| **F2** ✅ *fixed* | `index(split("/", uami_id), "resourceGroups")` → *item not found* when the ARM ID is lower-cased `resourcegroups` (as `az identity list` returns) | Defect (case-sensitive ID parse; hits any client) | **Fixed** in `deployment/monitoring-baselines/terraform/main.tf` — RG parsed case-insensitively via `regex("(?i)/resourceGroups/([^/]+)/", ...)`. |
| **F3** | `Microsoft.AlertsManagement` NotRegistered | Pre-req / onboarding | `sre-agent-pre-req` — already in provider-registration step; orchestrator auto-registers in non-WhatIf Phase 0. |
| **F4** | monitoring-baselines & amba `versions.tf` declare an `azurerm` backend → `plan` refuses without backend config | Onboarding / config | Populate `terraformBackend` in the deployment config (CI injects), or use the orchestrator's local-override test mode. |
| **F5** ✅ *fixed* | Coupled to F1: the Arc-enabled AMA built-ins (`ama_arc_*`) expose only `effect`, but the initiative passed `userAssignedIdentityResourceId` to **all** AMA policies → would fail at assign/remediate. F1's earlier plan failure masked it. | Defect (parameter mismatch) | **Fixed** in `deployment/monitoring-baselines/terraform/main.tf` — AMA parameter block now conditional: identity param only for VM/VMSS policies, empty params for `ama_arc_*`. |
| **F6** ⬜ *follow-up* | Plan warns: `management_group_id` on `azurerm_policy_set_definition` is deprecated, removed in azurerm v5.0 | Hygiene (provider upgrade) | Non-blocking. Migrate `azurerm_policy_set_definition` to `azurerm_management_group_policy_set_definition` before pinning azurerm >= 5.0. |

## Marketplace / Bicep / Terraform coverage (acceptance criterion)

- **Marketplace:** not applicable to this repo path — SRE Agent created via portal/IaC.
- **Bicep:** `sre-agent.bicep` builds (BCP081 only). Workbook Bicep parity path not exercised this run.
- **Terraform:** monitoring-baselines init + plan exercised (surfaced F1/F2/F4); amba not run; workbook TF parity not run.

## OGE-specific solutions needed

1. Built-in policy display-name mapping (F1) will drift per Microsoft renames — needs a resilient lookup (by policy name/GUID) rather than display name for OGE.
2. UAMI/resource-ID parsing must be case-insensitive (F2) to survive real client ARM ID casing.

## Go / No-Go

- **Recommendation:** ✅ **Go for the telemetry phase (plan-verified)** — monitoring-baselines Terraform F1/F2 (+ coupled F5) fixed and **`terraform plan` succeeds live against the lab (2026-07-07): `Plan: 5 to add, 0 to change, 0 to destroy`**. Remaining step before client rollout: `apply` against a real landing-zone MG with backend configured (F4), then remediate (Waves 2–4).
- **Rationale:** Pipeline structure, ordering, gates, and now Phase 1 plan validated live. The blockers were localized `monitoring-baselines` defects, now addressed at the root cause (GUID-based resolution + UAMI casing normalization + conditional AMA params). Only a non-blocking provider-deprecation warning (F6) remains.
