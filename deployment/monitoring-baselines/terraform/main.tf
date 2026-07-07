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

  # Normalize the remediation UAMI resource ID to canonical ARM casing. Client-supplied
  # IDs (e.g. from `az identity list`) often lower-case the "resourceGroups" segment, which
  # both our own ID parsing and the azurerm provider's strict resource-ID validation reject
  # ("the segment ... didn't match") — E2E finding F2. Fixing casing once here keeps every
  # downstream consumer (identity_ids, the identity data source, the AMA policy parameter)
  # working regardless of how the ID was cased on input. Empty input passes through unchanged.
  uami_id_normalized = replace(var.user_assigned_managed_identity_resource_id, "//[Rr]esource[Gg]roups//", "/resourceGroups/")

  common_tags = merge(var.resource_tags, {
    _deployed_by_monitoring_baselines = "True"
    environment                       = var.environment
    managed_by                        = "terraform"
  })

  # --- Built-in policy definitions referenced by immutable name (GUID) ---
  # Resolving by GUID (not display name) avoids ParserFailure-style plan errors when
  # Microsoft renames a built-in definition (E2E finding F1). The trailing comment on
  # each line records the current display name. To refresh a GUID (only needed if a
  # definition is retired), run:
  #   az policy definition list --query "[?policyType=='BuiltIn' && contains(displayName,'<text>')].{name:name,display:displayName}"

  ama_policy_names = var.enable_ama_policies == "Yes" ? {
    ama_linux_vm     = "ae8a10e6-19d6-44a3-a02d-a2bdfc707742" # Configure Linux virtual machines to run Azure Monitor Agent with user-assigned managed identity-based authentication
    ama_windows_vm   = "637125fd-7c39-4b94-bb0a-d331faf333a9" # Configure Windows virtual machines to run Azure Monitor Agent with user-assigned managed identity-based authentication
    ama_linux_vmss   = "59c3d93f-900b-4827-a8bd-562e7b956e7c" # Configure Linux virtual machine scale sets to run Azure Monitor Agent with user-assigned managed identity-based authentication
    ama_windows_vmss = "98569e20-8f32-4f31-bf34-0e91590ae9d3" # Configure Windows virtual machine scale sets to run Azure Monitor Agent with user-assigned managed identity-based authentication
    ama_arc_linux    = "845857af-0333-4c5d-bbbc-6076697da122" # Configure Linux Arc-enabled machines to run Azure Monitor Agent
    ama_arc_windows  = "94f686d6-9a24-4e19-91f1-de937dc171a4" # Configure Windows Arc-enabled machines to run Azure Monitor Agent
  } : {}

  diagnostic_policy_names = var.enable_diagnostic_settings_policies == "Yes" ? {
    diag_keyvault       = "6b359d8f-f88d-4052-aa7c-32015963ecc1" # Enable logging by category group for Key vaults (microsoft.keyvault/vaults) to Log Analytics
    diag_nsg            = "baa4c6de-b7cf-4b12-b436-6e40ef44c8cb" # Enable logging by category group for Network security groups (microsoft.network/networksecuritygroups) to Log Analytics
    diag_storage        = "59759c62-9a22-4cdf-ae64-074495983fef" # Configure diagnostic settings for Storage Accounts to Log Analytics workspace
    diag_sql_db         = "6567d3f3-42d0-4cfb-9606-9741ba60fa07" # Enable logging by category group for SQL databases (microsoft.sql/servers/databases) to Log Analytics
    diag_app_service    = "c0d8e23a-47be-4032-961f-8b0ff3957061" # Enable logging by category group for App Service (microsoft.web/sites) to Log Analytics
    diag_event_hub      = "441af8bf-7c88-4efc-bd24-b7be28d4acce" # Enable logging by category group for Event Hubs Namespaces (microsoft.eventhub/namespaces) to Log Analytics
    diag_service_bus    = "0277b2d5-6e6f-4d97-9929-a5c4eab56fd7" # Enable logging by category group for Service Bus Namespaces (microsoft.servicebus/namespaces) to Log Analytics
    diag_recovery_vault = "2f4d1c08-3695-41a7-a0a0-8db4a0e25233" # Enable logging by category group for Recovery Services vaults (microsoft.recoveryservices/vaults) to Log Analytics
  } : {}

  private_endpoint_policy_names = var.enable_private_endpoint_policies == "Yes" ? {
    pe_storage       = "6edd7eda-6dd8-40f7-810d-67160c639cd9" # Storage accounts should use private link
    pe_keyvault      = "a6abeaec-4d90-4a02-805f-6b26c4d3fbe9" # Azure Key Vaults should use private link
    pe_log_analytics = "a499fed8-bcc8-4195-b154-641f14743757" # Azure Monitor Private Link Scope should block access to non private link resources
  } : {}
}

# -----------------------------------------------------------------------------
# Data sources: resolve built-in policy definitions by immutable name (GUID)
# -----------------------------------------------------------------------------
data "azurerm_policy_definition" "ama" {
  for_each = local.ama_policy_names
  name     = each.value
}

data "azurerm_policy_definition" "diagnostic" {
  for_each = local.diagnostic_policy_names
  name     = each.value
}

data "azurerm_policy_definition" "private_endpoint" {
  for_each = local.private_endpoint_policy_names
  name     = each.value
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
      # Only the VM/VMSS user-assigned-identity policies expose userAssignedIdentityResourceId.
      # The Arc-enabled AMA policies (ama_arc_*) expose only `effect`, so passing the identity
      # parameter to them fails assignment/remediation — send no parameters for those.
      parameter_values = startswith(policy_definition_reference.key, "ama_arc_") ? jsonencode({}) : jsonencode({
        userAssignedIdentityResourceId = { value = local.uami_id_normalized }
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
    identity_ids = [local.uami_id_normalized]
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

# Resolve the remediation identity to obtain its principal (object) ID. Both the name and
# resource group are parsed from the casing-normalized ID (local.uami_id_normalized), so a
# lower-cased "resourcegroups" segment on input no longer breaks the index() lookup (F2).
data "azurerm_user_assigned_identity" "remediation" {
  name                = element(split("/", local.uami_id_normalized), length(split("/", local.uami_id_normalized)) - 1)
  resource_group_name = element(split("/", local.uami_id_normalized), index(split("/", local.uami_id_normalized), "resourceGroups") + 1)
}
