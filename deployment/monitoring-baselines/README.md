# Vendored `monitoring-baselines` (Phase 1) — fixed copy

This is a **self-contained, fixed copy** of the repository's `monitoring-baselines`
Terraform module, kept under `deployment/` so the end-to-end orchestrator
(`Deploy-EndToEnd.ps1`, Phase 1) never modifies the shared subproject. The shared
`../../monitoring-baselines/` is left untouched.

It carries the E2E test fixes for User Story **#65** (see
`../checklists/e2e-test-results.md`). Everything Phase 1 needs is here:

```
monitoring-baselines/
  terraform/            # full module (main.tf carries the fixes)
    *.tf
    environments/*.tfvars
  scripts/
    validate-parameters.ps1
```

## Fixes applied here (not in the shared module)

| # | Fix |
| --- | --- |
| **F1** | All AMA / diagnostic / private-endpoint built-in policies are resolved by **immutable GUID** (`data.azurerm_policy_definition.name`) instead of display name, so Microsoft renames no longer break `terraform plan`. The current display name of each GUID is a trailing comment in `terraform/main.tf` `locals`. Two references were also corrected: `diag_nsg` (now the NSG *category-group-to-Log-Analytics* policy, not traffic analytics) and `diag_storage` (now "Configure diagnostic settings for Storage Accounts to Log Analytics workspace"). |
| **F2** | The remediation UAMI resource ID is **casing-normalized once** in `locals` (`uami_id_normalized`) and reused everywhere, so a lower-cased `resourcegroups` segment (as returned by `az identity list`) no longer breaks our own ID parsing *or* the azurerm provider's strict `identity_ids` validation. |
| **F5** | AMA policy parameters are conditional: only the VM/VMSS user-assigned policies receive `userAssignedIdentityResourceId`; the Arc-enabled policies (`ama_arc_*`), which expose only `effect`, receive no parameters. |
| **F6** *(follow-up, non-blocking)* | `terraform plan` warns that `management_group_id` on `azurerm_policy_set_definition` is deprecated (removed in azurerm v5.0). Migrate to `azurerm_management_group_policy_set_definition` before pinning azurerm >= 5.0. |

## Refreshing a policy GUID

A GUID only needs updating if Microsoft **retires** a built-in definition. Find the
current one and update the matching entry in `terraform/main.tf` `locals`:

```powershell
az policy definition list --query "[?policyType=='BuiltIn' && contains(displayName,'<text>')].{name:name,display:displayName}" -o table
```

## Validation

```powershell
# From this folder
./scripts/validate-parameters.ps1 -All
terraform -chdir=terraform init -backend=false
terraform -chdir=terraform validate
```

The orchestrator runs `validate-parameters.ps1` then `terraform init/plan/apply`
against `terraform/` automatically in Phase 1.
