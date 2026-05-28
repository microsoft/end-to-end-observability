# Greenfield Onboarding Workflow

Step-by-step procedure for onboarding new Azure subscriptions to AMBA enterprise baselines.

## Key Principle

For greenfield subscriptions, policy assignments are enforced **before** workload deployment. This ensures `DeployIfNotExists` policies create alert resources automatically when monitored resources are created — no manual remediation required.

## Prerequisites

- [ ] Management group hierarchy deployed
- [ ] AMBA enterprise Terraform wrapper applied to target management groups
- [ ] All 17 initiative policy assignments active at correct scopes
- [ ] Notification Assets initiative deployed (APR and AG ready)
- [ ] UAMI provisioned with Monitoring Reader at pseudo-root MG
- [ ] Subscription vending pipeline configured

## Subscription Vending Integration

Add these steps to your subscription vending automation:

### Step 1: Create Subscription

```hcl
# Standard subscription creation via vending module
resource "azurerm_subscription" "workload" {
  subscription_name = var.subscription_name  # Must be alphanumeric + _ + - only
  billing_scope_id  = var.billing_scope
}
```

### Step 2: Place in Correct Management Group

```hcl
resource "azurerm_management_group_subscription_association" "workload" {
  management_group_id = var.target_management_group_id  # Landing zone MG
  subscription_id     = azurerm_subscription.workload.id
}
```

Once placed in the correct MG, the subscription inherits all AMBA policy assignments automatically.

### Step 3: Deploy VM Insights Prerequisites (if VM workloads)

```hcl
# Deploy Azure Monitor Agent and Data Collection Rule
# Required BEFORE VMs are created for immediate log alert coverage
resource "azurerm_monitor_data_collection_rule" "vm_insights" {
  name                = "dcr-vminsights-${var.subscription_name}"
  resource_group_name = azurerm_resource_group.monitoring.name
  location            = var.location

  destinations {
    log_analytics {
      workspace_resource_id = var.log_analytics_workspace_id
      name                  = "VMInsightsPerf-Logs-Dest"
    }
  }

  data_flow {
    streams      = ["Microsoft-InsightsMetrics", "Microsoft-Perf"]
    destinations = ["VMInsightsPerf-Logs-Dest"]
  }

  data_sources {
    performance_counter {
      streams                       = ["Microsoft-InsightsMetrics", "Microsoft-Perf"]
      sampling_frequency_in_seconds = 60
      counter_specifiers            = ["\\VmInsights\\DetailedMapEnabled"]
      name                          = "VMInsightsPerfCounters"
    }
  }
}
```

### Step 4: Deploy Workload Resources

When workload resources (VMs, Storage Accounts, etc.) are created:

1. Azure Policy evaluates compliance (within ~15 minutes)
2. `DeployIfNotExists` policies automatically create alert rules
3. APR automatically routes alerts to the configured action group
4. No manual remediation required

### Step 5: Verify Automatic Alert Creation

After workload deployment (allow 30-60 minutes for policy evaluation):

```powershell
# Check that alerts were auto-created
az monitor metrics alert list --subscription "<subscription-id>" \
  --query "[?tags._deployed_by_amba == 'True']" \
  --output table

# Verify compliance
az policy state summarize --subscription "<subscription-id>" \
  --filter "contains(policySetDefinitionName, 'Alerting')"
```

**Expected**: All deployed resources should have corresponding AMBA alert rules within 1 hour.

## Subscription Naming Validation

Add this validation to your vending pipeline:

```powershell
function Test-AmbaCompatibleName {
    param([string]$Name)
    return $Name -match '^[a-zA-Z0-9_-]+$'
}

$proposedName = "contoso-workload-prod-001"
if (-not (Test-AmbaCompatibleName -Name $proposedName)) {
    throw "Subscription name '$proposedName' contains invalid characters for AMBA"
}
```

## Post-Onboarding Checklist

- [ ] Subscription placed in correct management group
- [ ] Policy assignments inherited (check with `az policy assignment list`)
- [ ] VM Insights DCR deployed (if VM workloads)
- [ ] First workload deployed and alerts auto-created
- [ ] APR routing validated (correct AG, severity filters)
- [ ] Subscription added to compliance tracking dashboard
- [ ] 7-day review scheduled to evaluate alert noise
