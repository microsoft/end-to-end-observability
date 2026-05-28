# Cleanup Runbook

Procedures for cleaning up AMBA deployments using the official maintenance script.

## Prerequisites

- PowerShell 7+
- Azure PowerShell module (Az)
- Contributor or higher access at target management group scope

## Cleanup Script Location

The official AMBA cleanup script is at:
```
https://github.com/Azure/azure-monitor-baseline-alerts/blob/main/patterns/alz/scripts/Start-AMBA-ALZ-Maintenance.ps1
```

## Cleanup Ordering

The script removes resources in this order (dependencies respected):
1. Alert rules (metric, log, activity)
2. Policy Assignments
3. Policy Set Definitions (Initiatives)
4. Policy Definitions
5. Role Assignments
6. User Assigned Managed Identity
7. Action Groups
8. Alert Processing Rules
9. Resource Groups (only if empty after above removals)

## Full Cleanup (All Resources)

```powershell
./Start-AMBA-ALZ-Maintenance.ps1 `
  -pseudoRootManagementGroup "contoso" `
  -cleanItems All
```

## Selective Cleanup Options

### Role Assignments Only (for least-privilege migration)
```powershell
./Start-AMBA-ALZ-Maintenance.ps1 `
  -pseudoRootManagementGroup "contoso" `
  -cleanItems RoleAssignments
```

### Alerts Only (orphaned alerts for deleted resources)
```powershell
./Start-AMBA-ALZ-Maintenance.ps1 `
  -pseudoRootManagementGroup "contoso" `
  -cleanItems Alerts
```

### Notification Assets Only
```powershell
./Start-AMBA-ALZ-Maintenance.ps1 `
  -pseudoRootManagementGroup "contoso" `
  -cleanItems NotificationAssets
```

## Critical Warnings

### Tag Protection
- **NEVER** remove the `_deployed_by_amba = "True"` tag from resources
- The cleanup script identifies AMBA resources by this tag
- If tags are removed, resources become invisible to the cleanup process and must be manually deleted

### Terraform State Considerations
After running the cleanup script on Terraform-managed deployments:
1. Run `terraform plan` to detect state drift
2. Either `terraform apply` to re-deploy or `terraform state rm` for removed resources
3. Update `terraform.tfstate` to reflect cleanup

### Post-Cleanup Steps
1. Verify no orphaned resources remain:
   ```powershell
   az resource list --query "[?tags._deployed_by_amba == 'True']" --subscription "<sub-id>"
   ```
2. Clear deployment entries at management group if needed for region change
3. Update compliance dashboard to reflect cleanup
4. Document cleanup in change management system

## Rollback Procedure

If cleanup was unintentional or needs to be reversed:
1. Re-run Terraform apply with the same parameters:
   ```bash
   terraform -chdir=terraform apply -var-file=environments/<ring>.tfvars
   ```
2. Trigger policy remediation for all initiatives
3. Verify alert rules and APR are recreated
4. Validate action group routing
