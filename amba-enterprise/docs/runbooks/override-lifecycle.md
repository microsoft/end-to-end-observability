# Override Lifecycle Runbook

Procedures for managing threshold overrides and policy exclusions under centralized governance.

## Override Types

| Type | Mechanism | Scope | Use Case |
|---|---|---|---|
| Threshold Override | Resource tag | Per-resource | Adjust alert threshold for specific resources |
| Policy Exclusion | `policyAssignmentExclusionList` | Per-subscription or MG | Exclude entire scope from specific initiative |
| Resource Disable | `MonitorDisable` tag | Per-resource | Disable all AMBA monitoring on a resource |

## Threshold Override Procedure

### Tag Convention

AMBA threshold overrides use tags with this pattern:
```
_amba-<metricname>-threshold-Override_ = "<new-value>"
```

Example overrides:
```
_amba-PercentageCPU-threshold-Override_ = "90"        # Override CPU alert threshold to 90%
_amba-AvailableMemoryBytes-threshold-Override_ = "500000000"  # Override memory to 500MB
_amba-OSDiskBandwidthConsumedPercentage-threshold-Override_ = "95"
```

### Request Process

1. **Request**: Team submits override request via change management
   - Resource ID(s) affected
   - Metric name and desired threshold
   - Business justification
   - Requested duration (max 90 days, renewable)

2. **Approval**: Governance team reviews against baseline policy
   - Is the new threshold within acceptable range?
   - Is there an underlying issue that should be fixed instead?
   - Does this create an unacceptable monitoring gap?

3. **Implementation**: Platform engineering applies the tag
   ```powershell
   az tag update --resource-id "<resource-id>" \
     --operation merge \
     --tags "_amba-PercentageCPU-threshold-Override_=90"
   ```

4. **Remediation**: Trigger policy remediation to apply new threshold
   ```powershell
   az policy remediation create \
     --name "override-cpu-$(Get-Date -Format 'yyyyMMdd')" \
     --policy-assignment "<assignment-id>" \
     --resource-discovery-mode ReEvaluateCompliance
   ```

5. **Verification**: Confirm alert threshold updated
   ```powershell
   az monitor metrics alert show --name "<alert-name>" --resource-group "<rg>" \
     --query "criteria.allOf[0].threshold"
   ```

### Expiry and Review

- **Expiry tracking**: Add a companion tag `_amba-override-expires_ = "2026-08-01"`
- **Quarterly review**: Script scans for overrides approaching expiry
- **Auto-removal**: Expired overrides are removed and remediation re-run to restore baseline

```powershell
# Find expired overrides
$resources = az resource list --query "[?tags._amba_override_expires != null]" | ConvertFrom-Json
$today = Get-Date
foreach ($resource in $resources) {
    $expires = [DateTime]$resource.tags.'_amba-override-expires_'
    if ($expires -lt $today) {
        Write-Host "EXPIRED override on: $($resource.name) - expired $expires"
    }
}
```

## Policy Exclusion Procedure

### Request Process

1. **Request**: Team submits exclusion request
   - Subscription or MG ID to exclude
   - Initiative(s) to exclude from
   - Justification (e.g., "subscription runs third-party monitoring")
   - Duration (max 180 days, renewable)

2. **Approval**: Governance team + ops leader sign-off required

3. **Implementation**: Add to tfvars exclusion list
   ```hcl
   policy_assignment_exclusion_list = [
     "/subscriptions/00000000-0000-0000-0000-000000000000"  # Ticket: CHG-12345
   ]
   ```

4. **Deploy**: Apply via CI/CD pipeline

5. **Document**: Record in exclusion registry (see below)

### Exclusion Registry

Maintain a tracking file:

| Subscription/MG | Initiative | Ticket | Owner | Approved | Expires | Review Date |
|---|---|---|---|---|---|---|
| sub-legacy-001 | All | CHG-12345 | j.smith | 2026-05-01 | 2026-10-01 | 2026-08-01 |
| mg-sandbox | VM, VMSS | CHG-12400 | k.jones | 2026-05-15 | 2026-08-15 | 2026-07-15 |

## Resource Disable Procedure

For permanently excluding individual resources from all AMBA monitoring:

```powershell
# Apply disable tag
az tag update --resource-id "<resource-id>" \
  --operation merge \
  --tags "MonitorDisable=true"
```

**Governance**:
- Must be approved via change management
- Must have documented justification
- Subject to quarterly audit
- Alternative monitoring must be documented (e.g., "uses Datadog")

## Quarterly Audit Script

```powershell
# Audit all overrides, exclusions, and disabled resources
Write-Host "=== AMBA Override Audit ===" -ForegroundColor Cyan

# 1. Threshold overrides
$overrides = az resource list --query "[?contains(keys(tags), '_amba') && contains(keys(tags), 'Override')]" | ConvertFrom-Json
Write-Host "Threshold overrides: $($overrides.Count)"

# 2. Disabled resources
$disabled = az resource list --query "[?tags.MonitorDisable == 'true']" | ConvertFrom-Json
Write-Host "Disabled resources: $($disabled.Count)"

# 3. Policy exclusions (from tfvars)
$tfvars = Get-Content "./terraform/environments/prod.tfvars" -Raw
$exclusions = [regex]::Matches($tfvars, '/subscriptions/[^"]+')
Write-Host "Policy exclusions: $($exclusions.Count)"
```
