# -----------------------------------------------------------------------------
# Production Environment - AMBA Enterprise Parameters
# Purpose: Full enterprise production deployment
# Controls: All initiatives enabled, strict governance, BYON where approved
# -----------------------------------------------------------------------------

environment                    = "prod"
enterprise_scale_company_prefix = "contoso"
deployment_location            = "eastus"

# Management group topology
platform_management_group     = "contoso-platform"
identity_management_group     = "contoso-platform-identity"
management_management_group   = "contoso-platform-management"
connectivity_management_group = "contoso-platform-connectivity"
landing_zone_management_group = "contoso-landingzones"

# Management subscription for UAMI and monitoring resources
management_subscription_id = "" # Set via CI/CD variable or override

# Resource group configuration
alz_monitor_resource_group_name     = "rg-amba-monitoring-001"
alz_monitor_resource_group_location = "eastus"
alz_monitor_resource_group_tags = {
  _deployed_by_amba = "True"
  environment       = "production"
  cost_center       = "cloud-operations"
  compliance        = "required"
}

# Identity - BYO UAMI for production (enterprise-managed)
bring_your_own_user_assigned_managed_identity             = "Yes"
bring_your_own_user_assigned_managed_identity_resource_id = "" # Set via CI/CD variable
user_assigned_managed_identity_name                       = "id-amba-prod-001"

# Initiative flags - all initiatives enabled for production
enable_amba_connectivity       = "Yes"
enable_amba_identity           = "Yes"
enable_amba_management         = "Yes"
enable_amba_service_health     = "Yes"
enable_amba_notification_assets = "Yes"
enable_amba_hybrid_vm          = "Yes"
enable_amba_key_management     = "Yes"
enable_amba_load_balancing     = "Yes"
enable_amba_network_changes    = "Yes"
enable_amba_recovery_services  = "Yes"
enable_amba_storage            = "Yes"
enable_amba_vm                 = "Yes"
enable_amba_vmss               = "Yes"
enable_amba_web                = "Yes"

# Disable tag configuration - production uses strict values
alz_monitor_disable_tag_name   = "MonitorDisable"
alz_monitor_disable_tag_values = ["true"]

# Alert routing - only route AMBA alerts (prevents noise from non-AMBA alerts)
include_alz_alerts_only = "true"

# Notification - full routing: email + Logic App + ITSM
alz_monitor_action_group_email = ["cloudops-oncall@contoso.com", "sre-team@contoso.com"]
alz_logic_app_resource_id      = "" # Set via CI/CD variable
alz_logic_app_callback_url     = "" # Set via CI/CD variable

# BYON - production may have pre-existing action groups for specific subscriptions
# Add resource IDs of approved BYO action groups here
bring_your_own_action_group          = []
bring_your_own_alert_processing_rule = []

# Telemetry - opt out in production per enterprise policy
telemetry_opt_out = "Yes"

# Production exclusions - approved via change management
# Add management group or subscription IDs that are approved for exclusion
policy_assignment_exclusion_list = []
