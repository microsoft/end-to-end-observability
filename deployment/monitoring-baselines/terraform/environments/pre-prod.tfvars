# -----------------------------------------------------------------------------
# Pre-Production Environment - Monitoring Baselines Parameters
# Purpose: Validation before production rollout, mirrors prod config
# Restrictions: Full policy scope, private endpoints audited (validate before deny)
# -----------------------------------------------------------------------------

environment                     = "pre-prod"
enterprise_scale_company_prefix = "contoso"
deployment_location             = "eastus"

# Scope - assign to landing zones management group
landing_zone_management_group = "contoso-landingzones"

# Management subscription hosting the remediation identity and state
management_subscription_id = "" # Set via CI/CD variable or override

# Remediation identity - reuse the AMBA UAMI (pre-prod instance)
user_assigned_managed_identity_resource_id = "" # Set via CI/CD variable

# Diagnostic settings destination - central Log Analytics workspace
log_analytics_workspace_id = "" # Set via CI/CD variable

# Policy category flags - full scope mirrors production
enable_ama_policies                 = "Yes"
enable_diagnostic_settings_policies = "Yes"
enable_private_endpoint_policies    = "Yes"

# Private endpoints audited in pre-prod to validate impact before enabling deny in prod
private_endpoint_effect = "Audit"

# No exclusions in pre-prod
policy_assignment_exclusion_list = []

# Tags
resource_tags = {
  _deployed_by_monitoring_baselines = "True"
  environment                       = "pre-prod"
  cost_center                       = "platform-engineering"
}
