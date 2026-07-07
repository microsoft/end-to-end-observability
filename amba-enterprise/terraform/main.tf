# -----------------------------------------------------------------------------
# Enterprise AMBA Hybrid Rollout - Main Module
# Orchestrates AMBA deployment via AVM modules
# -----------------------------------------------------------------------------

locals {
  # Tag set applied to all AMBA resources - DO NOT REMOVE _deployed_by_amba
  common_tags = merge(var.alz_monitor_resource_group_tags, {
    _deployed_by_amba = "True"
    environment       = var.environment
    managed_by        = "terraform"
  })

  # Initiative enable flags mapped for the ALZ module
  initiative_flags = {
    enableAMBAConnectivity       = var.enable_amba_connectivity
    enableAMBAIdentity           = var.enable_amba_identity
    enableAMBAManagement         = var.enable_amba_management
    enableAMBAServiceHealth      = var.enable_amba_service_health
    enableAMBANotificationAssets = var.enable_amba_notification_assets
    enableAMBAHybridVM           = var.enable_amba_hybrid_vm
    enableAMBAKeyManagement      = var.enable_amba_key_management
    enableAMBALoadBalancing      = var.enable_amba_load_balancing
    enableAMBANetworkChanges     = var.enable_amba_network_changes
    enableAMBARecoveryServices   = var.enable_amba_recovery_services
    enableAMBAStorage            = var.enable_amba_storage
    enableAMBAVM                 = var.enable_amba_vm
    enableAMBAVMSS               = var.enable_amba_vmss
    enableAMBAWeb                = var.enable_amba_web
  }
}

# -----------------------------------------------------------------------------
# Module: ALZ Policy Assets and Role Assignments
# Deploys AMBA policy definitions, policy set definitions, and assignments
# at the management group hierarchy.
#
# The avm-ptn-alz module deploys an ALZ architecture (policy definitions, set
# definitions, and assignments) under a parent management group. The specific
# initiatives that get deployed are described by the named architecture
# definition rather than by individual boolean flags.
# -----------------------------------------------------------------------------
module "amba_alz" {
  source  = "Azure/avm-ptn-alz/azurerm"
  version = "0.15.0"

  architecture_name  = var.alz_architecture_name
  parent_resource_id = "/providers/Microsoft.Management/managementGroups/${var.enterprise_scale_company_prefix}"
  location           = var.deployment_location
}

# -----------------------------------------------------------------------------
# Module: AMBA Monitoring Resources
# Deploys the monitoring resource group, the User Assigned Managed Identity used
# by AMBA policy remediation, and the role assignment at the root management
# group. This is the purpose-built AMBA monitoring pattern module.
# -----------------------------------------------------------------------------
module "amba_monitoring" {
  source  = "Azure/avm-ptn-monitoring-amba-alz/azurerm"
  version = "0.3.0"

  location                            = var.deployment_location
  root_management_group_name          = var.enterprise_scale_company_prefix
  resource_group_name                 = var.alz_monitor_resource_group_name
  user_assigned_managed_identity_name = var.user_assigned_managed_identity_name
  role_definition_id                  = var.monitoring_role_definition_id
  tags                                = local.common_tags
  enable_telemetry                    = var.telemetry_opt_out == "Yes" ? false : true
}
