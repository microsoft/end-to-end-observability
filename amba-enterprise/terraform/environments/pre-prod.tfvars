# -----------------------------------------------------------------------------
# Pre-Production Environment - AMBA Enterprise Parameters
# Purpose: Validation before production rollout, mirrors prod config
# Restrictions: Full initiative scope, production-like alerting
# -----------------------------------------------------------------------------

environment                    = "pre-prod"
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
alz_monitor_resource_group_name     = "rg-amba-monitoring-preprod-001"
alz_monitor_resource_group_location = "eastus"
alz_monitor_resource_group_tags = {
  _deployed_by_amba = "True"
  environment       = "pre-prod"
  cost_center       = "platform-engineering"
}

# Identity - use AMBA-created UAMI for pre-prod
bring_your_own_user_assigned_managed_identity = "No"
user_assigned_managed_identity_name           = "id-amba-preprod-001"

# Initiative flags - full scope mirrors production
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

# Disable tag configuration
alz_monitor_disable_tag_name   = "MonitorDisable"
alz_monitor_disable_tag_values = ["true", "Test", "Dev", "Sandbox"]

# Alert routing - only route AMBA alerts
include_alz_alerts_only = "true"

# Notification - email + Logic App for pre-prod
alz_monitor_action_group_email = ["amba-preprod-alerts@contoso.com", "cloudops-oncall@contoso.com"]
alz_logic_app_resource_id      = ""
alz_logic_app_callback_url     = ""

# No BYON in pre-prod - validate standard path
bring_your_own_action_group          = []
bring_your_own_alert_processing_rule = []

# Telemetry
telemetry_opt_out = "No"

# No exclusions in pre-prod
policy_assignment_exclusion_list = []
