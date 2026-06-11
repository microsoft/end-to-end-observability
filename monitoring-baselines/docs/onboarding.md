# Monitoring Baselines Onboarding

How to roll out the Monitoring Baselines initiative (AMA, diagnostic settings, private endpoints) to greenfield and brownfield subscriptions.

## Greenfield (new subscriptions)

When a subscription is created and placed under the Landing Zones management group, it automatically inherits the Monitoring Baselines assignment. No manual steps are required for new resources:

1. Subscription is vended into the Landing Zones MG → inherits the assignment.
2. A workload resource is created (VM, Key Vault, Storage, etc.).
3. Azure Policy evaluates the resource (~15 minutes).
4. `DeployIfNotExists` automatically:
   - Installs the Azure Monitor Agent on VMs / VMSS / Arc machines.
   - Creates diagnostic settings routing logs to the central Log Analytics workspace.
5. Private endpoint policies evaluate and flag (Audit) any resource without private link.
6. Verify in the posture workbook after ~1 hour.

> Add a check to the subscription vending pipeline to confirm the assignment is inherited and the remediation identity has the required roles.

## Brownfield (existing subscriptions)

Existing resources are not auto-remediated until a remediation task runs. Follow the waves below.

### 1. Confirm prerequisites
- The Landing Zones management group contains the target subscription(s).
- The remediation UAMI (reused from AMBA) exists and has:
  - Monitoring Contributor + Log Analytics Contributor at the assignment scope.
  - Virtual Machine Contributor (required to install the AMA extension).
- The central Log Analytics workspace ID is set in the environment tfvars.

### 2. Deploy the initiative
```bash
cd monitoring-baselines/terraform
terraform plan  -var-file="environments/<env>.tfvars" -out=tfplan
terraform apply tfplan
```

### 3. Remediate in waves
Order matters — install the agent before expecting log-based signals.

```bash
# Wave 2: AMA
az policy remediation create \
  --name "mb-ama-wave" \
  --management-group "<landing-zone-mg>" \
  --policy-assignment "mon-baselines-<env>" \
  --definition-reference-id "ama_linux_vm" \
  --resource-discovery-mode ReEvaluateCompliance

# Repeat for ama_windows_vm, ama_linux_vmss, ama_windows_vmss, ama_arc_linux, ama_arc_windows

# Wave 3: Diagnostic settings
az policy remediation create \
  --name "mb-diag-wave" \
  --management-group "<landing-zone-mg>" \
  --policy-assignment "mon-baselines-<env>" \
  --definition-reference-id "diag_keyvault" \
  --resource-discovery-mode ReEvaluateCompliance

# Repeat for each diag_* reference id
```

> AMA installation can take 5–10 minutes per VM. Allow the remediation task to complete before validating compliance.

### 4. Validate
- Target ≥95% compliance within 48 hours.
- Open `visualizations/workbook-monitoring-posture.json` and confirm the non-compliant drill-down tables shrink as remediation completes.
- Spot-check a VM: confirm the `AzureMonitorLinuxAgent` / `AzureMonitorWindowsAgent` extension is present.
- Spot-check a Key Vault / Storage account: confirm diagnostic settings point to the central workspace.

### 5. Review private endpoint findings
- Private endpoint policies start in **Audit** mode and only flag non-compliant resources.
- Review flagged resources with the network team.
- Escalate to **Deny** (via `private_endpoint_effect = "Deny"`) only after pre-prod soak confirms no legitimate resources break, and with change management approval.

### 6. Document and close
- Record any exclusions in the override lifecycle registry (see `runbooks/override-lifecycle.md`).
- Schedule a 7-day post-deployment review.
