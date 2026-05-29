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
    enableAMBAConnectivity      = var.enable_amba_connectivity
    enableAMBAIdentity          = var.enable_amba_identity
    enableAMBAManagement        = var.enable_amba_management
    enableAMBAServiceHealth     = var.enable_amba_service_health
    enableAMBANotificationAssets = var.enable_amba_notification_assets
    enableAMBAHybridVM          = var.enable_amba_hybrid_vm
    enableAMBAKeyManagement     = var.enable_amba_key_management
    enableAMBALoadBalancing     = var.enable_amba_load_balancing
    enableAMBANetworkChanges    = var.enable_amba_network_changes
    enableAMBARecoveryServices  = var.enable_amba_recovery_services
    enableAMBAStorage           = var.enable_amba_storage
    enableAMBAVM                = var.enable_amba_vm
    enableAMBAVMSS              = var.enable_amba_vmss
    enableAMBAWeb               = var.enable_amba_web
  }
}

# -----------------------------------------------------------------------------
# Module: ALZ Policy Assets and Role Assignments
# Deploys AMBA policy definitions, policy set definitions, and assignments
# at the management group hierarchy
# -----------------------------------------------------------------------------
module "amba_alz" {
  source  = "Azure/avm-ptn-alz/azurerm"
  version = "0.15.0"

  management_group_hierarchy = {
    root = {
      id          = var.enterprise_scale_company_prefix
      displayName = var.enterprise_scale_company_prefix
    }
  }

  location = var.deployment_location
}

# -----------------------------------------------------------------------------
# Module: AMBA Monitoring Resources
# Deploys monitoring resource groups, UAMI, alert processing rules,
# action groups, and policy assignments
# -----------------------------------------------------------------------------
module "amba_monitoring" {
  source  = "Azure/avm-ptn-monitoring-amba-alz/azurerm"
  version = "0.3.0"

  company_prefix              = var.enterprise_scale_company_prefix
  platform_management_group   = var.platform_management_group
  identity_management_group   = var.identity_management_group
  management_management_group = var.management_management_group
  connectivity_management_group = var.connectivity_management_group
  landing_zone_management_group = var.landing_zone_management_group

  location = var.deployment_location

  # Initiative enable flags
  enable_amba_connectivity       = var.enable_amba_connectivity
  enable_amba_identity           = var.enable_amba_identity
  enable_amba_management         = var.enable_amba_management
  enable_amba_service_health     = var.enable_amba_service_health
  enable_amba_notification_assets = var.enable_amba_notification_assets
  enable_amba_hybrid_vm          = var.enable_amba_hybrid_vm
  enable_amba_key_management     = var.enable_amba_key_management
  enable_amba_load_balancing     = var.enable_amba_load_balancing
  enable_amba_network_changes    = var.enable_amba_network_changes
  enable_amba_recovery_services  = var.enable_amba_recovery_services
  enable_amba_storage            = var.enable_amba_storage
  enable_amba_vm                 = var.enable_amba_vm
  enable_amba_vmss               = var.enable_amba_vmss
  enable_amba_web                = var.enable_amba_web

  # Identity configuration
  bring_your_own_user_assigned_managed_identity             = var.bring_your_own_user_assigned_managed_identity
  bring_your_own_user_assigned_managed_identity_resource_id = var.bring_your_own_user_assigned_managed_identity_resource_id
  user_assigned_managed_identity_name                       = var.user_assigned_managed_identity_name
  management_subscription_id                                = var.management_subscription_id

  # Monitoring resource group
  alz_monitor_resource_group_name     = var.alz_monitor_resource_group_name
  alz_monitor_resource_group_location = var.alz_monitor_resource_group_location
  alz_monitor_resource_group_tags     = local.common_tags

  # Disable tag configuration
  alz_monitor_disable_tag_name   = var.alz_monitor_disable_tag_name
  alz_monitor_disable_tag_values = var.alz_monitor_disable_tag_values

  # Alert routing
  include_alz_alerts_only = var.include_alz_alerts_only

  # Notification configuration
  alz_monitor_action_group_email = var.alz_monitor_action_group_email
  alz_logic_app_resource_id      = var.alz_logic_app_resource_id
  alz_logic_app_callback_url     = var.alz_logic_app_callback_url

  # BYON configuration
  bring_your_own_action_group          = var.bring_your_own_action_group
  bring_your_own_alert_processing_rule = var.bring_your_own_alert_processing_rule

  # Telemetry
  telemetry_opt_out = var.telemetry_opt_out

  # Exclusions
  policy_assignment_exclusion_list = var.policy_assignment_exclusion_list

  tags = local.common_tags
}

# -----------------------------------------------------------------------------
# Module: Resource Group (for monitoring resources in management subscription)
# -----------------------------------------------------------------------------
module "monitoring_resource_group" {
  source  = "Azure/avm-res-resources-resourcegroup/azurerm"
  version = "0.2.1"

  name     = var.alz_monitor_resource_group_name
  location = var.alz_monitor_resource_group_location
  tags     = local.common_tags
}

# -----------------------------------------------------------------------------
# Module: User Assigned Managed Identity
# Only created when not using BYO UAMI
# -----------------------------------------------------------------------------
module "user_assigned_identity" {
  source  = "Azure/avm-res-managedidentity-userassignedidentity/azurerm"
  version = "0.3.4"

  count = var.bring_your_own_user_assigned_managed_identity == "No" ? 1 : 0

  name                = var.user_assigned_managed_identity_name
  resource_group_name = module.monitoring_resource_group.name
  location            = var.alz_monitor_resource_group_location
  tags                = local.common_tags
}
