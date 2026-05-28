# Release Upgrade Runbook

Procedures for consuming new upstream AMBA releases safely.

## Upgrade Cadence

- **Monthly**: Check AMBA GitHub releases for new versions
- **Quarterly**: Plan and execute version upgrades
- **As-needed**: Security or critical bug fixes

## Pre-Upgrade Checklist

- [ ] Review AMBA release notes for breaking changes
- [ ] Check "Update to new releases" docs for version-specific migration steps
- [ ] Diff policy set definitions between current and new version
- [ ] Diff parameter names between versions (look for removals/renames)
- [ ] Verify new module versions are available in Terraform registry
- [ ] Schedule maintenance window for upgrade

## Upgrade Procedure

### Step 1: Analyze Breaking Changes

```bash
# Clone/pull latest AMBA repo
cd /path/to/azure-monitor-baseline-alerts
git fetch --tags
git diff <current-tag>..<new-tag> -- patterns/alz/alzArm.param.json

# Check for parameter removals
git diff <current-tag>..<new-tag> -- patterns/alz/policySetDefinitions/
```

Look for:
- Removed parameters → will cause `InvalidPolicyParameterUpdate`
- Renamed parameters → update tfvars
- New required parameters → add to tfvars
- Changed default values → verify enterprise alignment

### Step 2: Update Terraform Module Versions

```hcl
# In versions.tf or main.tf, update pinned versions:
module "amba_alz" {
  source  = "Azure/avm-ptn-alz/azurerm"
  version = "<new-version>"  # Was 0.15.0
}

module "amba_monitoring" {
  source  = "Azure/avm-ptn-monitoring-amba-alz/azurerm"
  version = "<new-version>"  # Was 0.3.0
}
```

### Step 3: Update Parameter Files

If parameters were renamed or removed:
1. Update all `environments/*.tfvars` files
2. Run `./scripts/validate-parameters.ps1 -All`
3. Commit changes

### Step 4: Deploy to Sandbox

```bash
terraform -chdir=terraform plan -var-file=environments/sandbox.tfvars
terraform -chdir=terraform apply -var-file=environments/sandbox.tfvars
```

### Step 5: Validate in Sandbox

- Check policy assignments exist
- Trigger remediation on test resources
- Verify alerts are created correctly
- Validate APR routing behavior

### Step 6: Handle Breaking Changes

If `InvalidPolicyParameterUpdate` occurs:

1. Run cleanup in sandbox scope:
   ```powershell
   ./Start-AMBA-ALZ-Maintenance.ps1 -pseudoRootManagementGroup "contoso-sandbox" -cleanItems All
   ```
2. Re-deploy with new version
3. Re-run remediation

### Step 7: Promote to Pre-Prod

```bash
terraform -chdir=terraform apply -var-file=environments/pre-prod.tfvars
```

Validate the same criteria as sandbox, plus:
- Compliance rates return to >95% within 48 hours
- No alert noise increase
- Existing overrides still function

### Step 8: Promote to Production

After pre-prod validation (minimum 7-day soak):
```bash
terraform -chdir=terraform apply -var-file=environments/prod.tfvars
```

### Step 9: Post-Upgrade Validation

- [ ] All policy assignments active
- [ ] Compliance rates stable
- [ ] APR routing functional
- [ ] No orphaned alerts from previous version
- [ ] Role assignments use Monitoring Policy Contributor (not Contributor)
- [ ] Deployment count budget checked

## Rollback Procedure

If critical issues found after upgrade:

1. **Immediate**: Revert Terraform module version in code
2. **If policy conflicts**: Run cleanup, then deploy previous version
3. **If partial**: Use policy exclusion list to exclude affected subscriptions temporarily

```bash
# Revert module versions
git revert <upgrade-commit>
terraform -chdir=terraform apply -var-file=environments/<ring>.tfvars
```

## Version History Tracking

Maintain a version log:

| Date | AMBA Version | AVM ALZ Module | AVM Monitoring Module | Notes |
|---|---|---|---|---|
| 2026-05-26 | 2025-10-01 | 0.15.0 | 0.3.0 | Initial deployment |
