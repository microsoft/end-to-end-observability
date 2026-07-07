# -----------------------------------------------------------------------------
# Production Environment - Monitoring Baselines Parameters
# Purpose: Full enterprise production enforcement
# Controls: All categories enabled. Private endpoints default to Audit; flip to
#           Deny only after pre-prod soak confirms no legitimate resources break.
# -----------------------------------------------------------------------------

environment                     = "prod"
enterprise_scale_company_prefix = "contoso"
deployment_location             = "eastus"

# Scope - assign to landing zones management group
landing_zone_management_group = "contoso-landingzones"

# Management subscription hosting the remediation identity and state
management_subscription_id = "" # Set via CI/CD variable or override

# Remediation identity - reuse the enterprise-managed AMBA UAMI (BYO)
user_assigned_managed_identity_resource_id = "" # Set via CI/CD variable

# Diagnostic settings destination - central Log Analytics workspace
log_analytics_workspace_id = "" # Set via CI/CD variable

# Policy category flags - all enforced in production
enable_ama_policies                 = "Yes"
enable_diagnostic_settings_policies = "Yes"
enable_private_endpoint_policies    = "Yes"

# Private endpoint effect - default Audit. Change to "Deny" via change management
# once pre-prod confirms no legitimate resources are blocked.
private_endpoint_effect = "Audit"

# Production exclusions - approved via change management
# Add management group or subscription IDs that are approved for exclusion
policy_assignment_exclusion_list = []

# Tags
resource_tags = {
  _deployed_by_monitoring_baselines = "True"
  environment                       = "production"
  cost_center                       = "cloud-operations"
  compliance                        = "required"
}
