# Brownfield Onboarding Workflow

Step-by-step procedure for onboarding existing Azure subscriptions to AMBA enterprise baselines.

## Prerequisites

- [ ] Subscription naming validated (alphanumeric + underscore + hyphen only)
- [ ] VM Insights DCR deployed on target VMs (required for VM/HybridVM log alerts)
- [ ] AMBA enterprise Terraform wrapper deployed to target management group
- [ ] Change management ticket approved for onboarding
- [ ] Notification mode decision made (standard AMBA assets vs BYON)

## Procedure

### Step 1: Run Preflight Inventory

```powershell
./scripts/preflight-inventory.ps1 -SubscriptionId "<subscription-id>"
```

Review the generated JSON report. Key decision points:
- If **existing action groups exist**: evaluate BYON path
- If **existing APRs exist**: evaluate for conflict with AMBA APR
- If **AMBA tags found**: previous deployment exists — run cleanup first
- If **VM Insights missing**: deploy DCR before VM alert remediation

### Step 2: Resolve Blockers

| Blocker | Resolution |
|---|---|
| Invalid subscription name | Rename subscription, then re-run preflight |
| Previous AMBA deployment detected | Run `Start-AMBA-ALZ-Maintenance.ps1` cleanup |
| Orphaned role assignments | Remove via Azure Portal > MG > IAM > Identity not found |
| VM Insights missing | Deploy Azure Monitor Agent + DCR via policy or manual |

### Step 3: Configure Notification Mode

**Standard path** (no existing AG/APR):
- No additional configuration needed
- AMBA Notification Assets initiative creates AG and APR automatically

**BYON Action Group** (existing AG, no APR):
```hcl
# In environment tfvars or subscription-specific override:
bring_your_own_action_group = ["/subscriptions/<id>/resourceGroups/<rg>/providers/microsoft.insights/actionGroups/<ag-name>"]
```

**Full BYON** (existing AG + existing APR):
```hcl
bring_your_own_action_group          = ["/subscriptions/<id>/resourceGroups/<rg>/providers/microsoft.insights/actionGroups/<ag-name>"]
bring_your_own_alert_processing_rule = ["/subscriptions/<id>/resourceGroups/<rg>/providers/Microsoft.AlertsManagement/actionRules/<apr-name>"]
```

### Step 4: Deploy Policy Assignments

Policy assignments are deployed at the management group level via Terraform.
For subscription-specific exclusions, add to the exclusion list:

```hcl
policy_assignment_exclusion_list = ["/subscriptions/<id-to-exclude>"]
```

### Step 5: Execute Remediation Waves

Remediation must follow the initiative sequencing order:

**Wave 1: Notification Assets + Service Health**
```powershell
# Trigger remediation for Notification Assets
az policy remediation create --name "amba-notification-wave1" \
  --policy-assignment "/providers/Microsoft.Management/managementGroups/<mg>/providers/Microsoft.Authorization/policyAssignments/<assignment-id>" \
  --resource-discovery-mode ReEvaluateCompliance
```

**Wave 2: Platform initiatives** (Identity, Management, Connectivity)

**Wave 3: Compute** (VM, VMSS, HybridVM) — only after VM Insights confirmed

**Wave 4: Landing Zone services** (Key Management, Load Balancing, Network Changes, Recovery Services, Storage, Web)

### Step 6: Validate Compliance

```powershell
# Check compliance state for the subscription
az policy state summarize --subscription "<subscription-id>" \
  --filter "policySetDefinitionName eq 'Alerting-*'"
```

**Target**: >95% compliance within 48 hours of remediation.

### Step 7: Validate Alert Routing

```powershell
# Verify APR exists and is correctly configured
az monitor alert-processing-rule list --subscription "<subscription-id>" \
  --query "[?tags._deployed_by_amba == 'True']"
```

Confirm:
- [ ] APR exists and is enabled
- [ ] APR action type is `AddActionGroups`
- [ ] APR points to correct action group(s)
- [ ] APR severity filter aligns with ops policy

### Step 8: Document and Close

- Record onboarding in change management system
- Update coverage tracking dashboard
- Schedule 7-day post-deployment review for alert noise evaluation
