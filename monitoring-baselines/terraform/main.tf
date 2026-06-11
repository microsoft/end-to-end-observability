# -----------------------------------------------------------------------------
# Monitoring Baselines - Main Configuration
#
# Builds a single custom policy initiative ("Monitoring-Baselines-<env>") that
# bundles Azure built-in policy definitions for:
#   1. Azure Monitor Agent (AMA) deployment        -> DeployIfNotExists
#   2. Diagnostic settings to Log Analytics         -> DeployIfNotExists
#   3. Private endpoint posture                     -> Audit / Deny (parameterized)
#
# The initiative is assigned to the Landing Zones management group and remediated
# by a User Assigned Managed Identity (reused from the AMBA deployment).
# -----------------------------------------------------------------------------

locals {
  initiative_name       = "Monitoring-Baselines-${var.environment}"
  landing_zone_mg_scope = "/providers/Microsoft.Management/managementGroups/${var.landing_zone_management_group}"
  remediation_location  = var.deployment_location

  common_tags = merge(var.resource_tags, {
    _deployed_by_monitoring_baselines = "True"
    environment                       = var.environment
    managed_by                        = "terraform"
  })

  # --- Built-in policy definitions referenced by display name ---
  # Display names resolve to immutable built-in definition IDs via data sources.

  ama_policy_display_names = var.enable_ama_policies == "Yes" ? {
    ama_linux_vm     = "Configure Linux virtual machines to run Azure Monitor Agent with user-assigned managed identity-based authentication"
    ama_windows_vm   = "Configure Windows virtual machines to run Azure Monitor Agent with user-assigned managed identity-based authentication"
    ama_linux_vmss   = "Configure Linux virtual machine scale sets to run Azure Monitor Agent with user-assigned managed identity-based authentication"
    ama_windows_vmss = "Configure Windows virtual machine scale sets to run Azure Monitor Agent with user-assigned managed identity-based authentication"
    ama_arc_linux    = "Configure Linux Arc-enabled machines to run Azure Monitor Agent"
    ama_arc_windows  = "Configure Windows Arc-enabled machines to run Azure Monitor Agent"
  } : {}

  diagnostic_policy_display_names = var.enable_diagnostic_settings_policies == "Yes" ? {
    diag_keyvault       = "Enable logging by category group for Key Vault (microsoft.keyvault/vaults) to Log Analytics"
    diag_nsg            = "Configure Network Security Groups to use specific workspace for traffic analytics"
    diag_storage        = "Enable logging by category group for Storage Accounts (microsoft.storage/storageaccounts) to Log Analytics"
    diag_sql_db         = "Enable logging by category group for SQL Databases (microsoft.sql/servers/databases) to Log Analytics"
    diag_app_service    = "Enable logging by category group for App Services (microsoft.web/sites) to Log Analytics"
    diag_event_hub      = "Enable logging by category group for Event Hubs Namespaces (microsoft.eventhub/namespaces) to Log Analytics"
    diag_service_bus    = "Enable logging by category group for Service Bus Namespaces (microsoft.servicebus/namespaces) to Log Analytics"
    diag_recovery_vault = "Enable logging by category group for Recovery Services vaults (microsoft.recoveryservices/vaults) to Log Analytics"
  } : {}

  private_endpoint_policy_display_names = var.enable_private_endpoint_policies == "Yes" ? {
    pe_storage       = "Storage accounts should use private link"
    pe_keyvault      = "Azure Key Vaults should use private link"
    pe_log_analytics = "Azure Monitor Private Link Scope should block access to non private link resources"
  } : {}
}

# -----------------------------------------------------------------------------
# Data sources: resolve built-in policy definitions by display name
# -----------------------------------------------------------------------------
data "azurerm_policy_definition" "ama" {
  for_each     = local.ama_policy_display_names
  display_name = each.value
}

data "azurerm_policy_definition" "diagnostic" {
  for_each     = local.diagnostic_policy_display_names
  display_name = each.value
}

data "azurerm_policy_definition" "private_endpoint" {
  for_each     = local.private_endpoint_policy_display_names
  display_name = each.value
}

# -----------------------------------------------------------------------------
# Custom policy set definition (initiative) bundling all baseline policies
# -----------------------------------------------------------------------------
resource "azurerm_policy_set_definition" "monitoring_baselines" {
  name                = local.initiative_name
  policy_type         = "Custom"
  display_name        = "Monitoring Baselines (${var.environment})"
  description         = "Enforces Azure Monitor Agent deployment, diagnostic settings, and private endpoint posture on supported resources. Separate from AMBA alerting initiatives."
  management_group_id = local.landing_zone_mg_scope

  metadata = jsonencode({
    category = "Monitoring"
    version  = "1.0.0"
    source   = "monitoring-baselines"
  })

  # AMA deployment policies (DeployIfNotExists)
  dynamic "policy_definition_reference" {
    for_each = data.azurerm_policy_definition.ama
    content {
      policy_definition_id = policy_definition_reference.value.id
      reference_id         = policy_definition_reference.key
      parameter_values = jsonencode({
        userAssignedIdentityResourceId = { value = var.user_assigned_managed_identity_resource_id }
      })
      policy_group_names = ["azure-monitor-agent"]
    }
  }

  # Diagnostic settings policies (DeployIfNotExists -> Log Analytics)
  dynamic "policy_definition_reference" {
    for_each = data.azurerm_policy_definition.diagnostic
    content {
      policy_definition_id = policy_definition_reference.value.id
      reference_id         = policy_definition_reference.key
      parameter_values = jsonencode({
        logAnalytics = { value = var.log_analytics_workspace_id }
      })
      policy_group_names = ["diagnostic-settings"]
    }
  }

  # Private endpoint posture policies (Audit / Deny)
  dynamic "policy_definition_reference" {
    for_each = data.azurerm_policy_definition.private_endpoint
    content {
      policy_definition_id = policy_definition_reference.value.id
      reference_id         = policy_definition_reference.key
      parameter_values = jsonencode({
        effect = { value = var.private_endpoint_effect }
      })
      policy_group_names = ["private-endpoints"]
    }
  }

  policy_definition_group {
    name         = "azure-monitor-agent"
    display_name = "Azure Monitor Agent Deployment"
    description  = "Deploys and configures the Azure Monitor Agent on VMs, scale sets, and Arc-enabled machines."
  }

  policy_definition_group {
    name         = "diagnostic-settings"
    display_name = "Diagnostic Settings"
    description  = "Routes platform logs and metrics to the central Log Analytics workspace."
  }

  policy_definition_group {
    name         = "private-endpoints"
    display_name = "Private Endpoint Posture"
    description  = "Flags or blocks monitoring-adjacent resources that do not use private link."
  }
}

# -----------------------------------------------------------------------------
# Assignment of the initiative to the Landing Zones management group
# -----------------------------------------------------------------------------
resource "azurerm_management_group_policy_assignment" "monitoring_baselines" {
  name                 = "mon-baselines-${var.environment}"
  display_name         = "Monitoring Baselines (${var.environment})"
  description          = "Auto-enforces AMA, diagnostic settings, and private endpoint posture on supported resources."
  policy_definition_id = azurerm_policy_set_definition.monitoring_baselines.id
  management_group_id  = local.landing_zone_mg_scope
  location             = local.remediation_location

  identity {
    type         = "UserAssigned"
    identity_ids = [var.user_assigned_managed_identity_resource_id]
  }

  not_scopes = [for id in var.policy_assignment_exclusion_list : id]

  non_compliance_message {
    content = "Resource is missing a required monitoring baseline (Azure Monitor Agent, diagnostic settings, or private endpoint). Trigger remediation or apply an approved exception."
  }

  metadata = jsonencode({
    category   = "Monitoring"
    assignedBy = "monitoring-baselines"
  })
}

# -----------------------------------------------------------------------------
# Role assignments for the remediation identity
# DeployIfNotExists remediation requires permission to deploy the AMA extension,
# create diagnostic settings, and write to the Log Analytics workspace.
# -----------------------------------------------------------------------------
resource "azurerm_role_assignment" "monitoring_contributor" {
  scope                = local.landing_zone_mg_scope
  role_definition_name = "Monitoring Contributor"
  principal_id         = data.azurerm_user_assigned_identity.remediation.principal_id
}

resource "azurerm_role_assignment" "log_analytics_contributor" {
  scope                = local.landing_zone_mg_scope
  role_definition_name = "Log Analytics Contributor"
  principal_id         = data.azurerm_user_assigned_identity.remediation.principal_id
}

# Virtual Machine Contributor is required to install the AMA extension on VMs/VMSS
resource "azurerm_role_assignment" "vm_contributor" {
  count                = var.enable_ama_policies == "Yes" ? 1 : 0
  scope                = local.landing_zone_mg_scope
  role_definition_name = "Virtual Machine Contributor"
  principal_id         = data.azurerm_user_assigned_identity.remediation.principal_id
}

# Resolve the remediation identity to obtain its principal (object) ID
data "azurerm_user_assigned_identity" "remediation" {
  name                = element(split("/", var.user_assigned_managed_identity_resource_id), length(split("/", var.user_assigned_managed_identity_resource_id)) - 1)
  resource_group_name = element(split("/", var.user_assigned_managed_identity_resource_id), index(split("/", var.user_assigned_managed_identity_resource_id), "resourceGroups") + 1)
}
