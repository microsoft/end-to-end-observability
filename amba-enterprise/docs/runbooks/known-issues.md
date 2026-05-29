# Known Issues Mitigation Runbook

Documented resolutions for all 9 known AMBA deployment issues.

## Issue 1: VM Log Alert Policies Fail to Remediate

**Error**: `failed to resolve table or column expression named` or `DraftClientException`

**Cause**: VM Insights not enabled — no data in InsightsMetrics/Heartbeat tables.

**Resolution**:
1. Deploy Azure Monitor Agent on target VMs
2. Associate VMs with VM Insights Data Collection Rule
3. Wait 15 minutes for data ingestion to begin
4. Re-run remediation task

**Prevention**: Include VM Insights DCR deployment in subscription vending pipeline and brownfield preflight inventory.

---

## Issue 2: Role Assignment Update Not Permitted

**Error**: `RoleAssignmentUpdateNotPermitted - Tenant ID, application ID, principal ID, and scope are not allowed to be updated.`

**Cause**: Orphaned role assignments from deleted identities blocking new assignments.

**Resolution**:
1. Navigate to Azure Portal → Management Groups → Target MG → Access Control (IAM)
2. Under Contributor role, find entries showing "Identity not found"
3. Select all orphaned entries and click Remove
4. Re-run deployment

**Prevention**: Schedule quarterly role assignment hygiene reviews. Use the cleanup script:
```powershell
./Start-AMBA-ALZ-Maintenance.ps1 -pseudoRootManagementGroup $mgId -cleanItems RoleAssignments
```

---

## Issue 3: Invalid Deployment Location

**Error**: `InvalidDeploymentLocation - Invalid deployment location 'westeurope'. The deployment 'ALZARM' already exists in location 'uksouth'.`

**Cause**: Attempting to deploy to a different region after first deployment. Deployment entries persist with original region.

**Resolution**:
- **If intentional same-scope deployment**: No action needed — definitions/assignments are not region-specific.
- **If changing region after cleanup**:
  1. Navigate to Management Groups → Target MG → Deployments
  2. Delete all AMBA deployment entries (prefix: `amba-`, `pid-`, `alzArm`, `ambaPreparingToLaunch`)
  3. Re-deploy to new region

**Prevention**: Document deployment region in parameter governance profile. Lock region choice at Phase 1.

---

## Issue 4: 800 Deployment Limit Exceeded

**Error**: `MultipleErrorsOccurred - Conflict,Conflict,Conflict...`

**Cause**: Management group has reached 800 deployment entry limit.

**Resolution**:
1. Navigate to Management Groups → Target MG → Deployments
2. Delete stale/old deployment entries (AMBA prefixes: `amba-`, `pid-`, `alzArm`, `ambaPreparingToLaunch`)
3. Re-run deployment

**Prevention**: Run `./scripts/deployment-budget.ps1` in CI pipeline. Schedule monthly cleanup of deployment entries older than 90 days.

---

## Issue 5: Location Property Not Specified

**Error**: `InvalidDeployment - The 'location' property must be specified for 'amba-id-amba-prod-001'`

**Cause**: BYO UAMI misconfiguration — management subscription ID not correctly specified when creating new UAMI.

**Resolution**:
1. Verify `management_subscription_id` is correctly set in tfvars
2. If using BYO UAMI, ensure `bring_your_own_user_assigned_managed_identity_resource_id` points to valid resource
3. Re-run deployment

**Prevention**: Validate parameter file before deployment with `./scripts/validate-parameters.ps1`.

---

## Issue 6: Action Group/APR Deployment Failure (Invalid Resource Name)

**Error**: `InvalidResourceNameFormat - At least one resource name segment is invalid`

**Cause**: Subscription name contains characters not allowed in AG/APR resource names.

**Resolution**:
1. Identify the subscription with invalid name
2. Rename subscription to use only: a-z, A-Z, 0-9, underscore, hyphen
3. Re-run remediation

**Prevention**: Run `./scripts/validate-naming.ps1` before any AMBA deployment. Add naming validation to subscription vending pipeline.

---

## Issue 7: Failed to Edit Action Groups

**Error**: `MissingApiVersionParameter - The api-version query parameter is required`

**Cause**: Same subscription naming issue — invalid characters in AG name derived from subscription name.

**Resolution**:
1. Rename subscription to remove invalid characters
2. Delete existing action groups (names starting with `ag-AMBA-` or `ag-AMBA-SH-`)
3. Re-run remediation

---

## Issue 8: InvalidPolicyParameterUpdate

**Error**: `InvalidPolicyParameterUpdate - The existing policy parameter(s) 'xxx' were not found in the policy being updated`

**Cause**: Breaking change between AMBA versions — a parameter was renamed or removed.

**Resolution**:
1. Run cleanup: `./Start-AMBA-ALZ-Maintenance.ps1 -pseudoRootManagementGroup $mgId -cleanItems All`
2. Update tfvars to align with new parameter names (check AMBA release notes)
3. Re-deploy with new version

**Prevention**: Always follow the release upgrade runbook. Check diff between versions before deploying.

---

## Issue 9: PolicyDefinitionNotFound

**Error**: `PolicyDefinitionNotFound - The policy set definition 'Alerting-xxx' request is invalid. The following policy definition could not be found...`

**Cause**: Transient timing issue — policy definition deployment hasn't propagated yet.

**Resolution**: Simply re-run the deployment. This is a race condition that resolves on retry.

**Prevention**: Add retry logic in deployment pipeline (max 2 retries with 60-second delay).
