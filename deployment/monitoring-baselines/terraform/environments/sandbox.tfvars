# -----------------------------------------------------------------------------
# Sandbox Environment - Monitoring Baselines Parameters
# Purpose: Initial testing and validation of AMA/diagnostic/private-endpoint policies
# Restrictions: Private endpoints audited only (never deny), narrower scope
# -----------------------------------------------------------------------------

environment                     = "sandbox"
enterprise_scale_company_prefix = "contoso"
deployment_location             = "eastus"

# Scope - assign to landing zones management group
landing_zone_management_group = "contoso-landingzones"

# Management subscription hosting the remediation identity and state
management_subscription_id = "" # Set via CI/CD variable or override

# Remediation identity - reuse the AMBA UAMI (sandbox instance)
# Format: /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-amba-sandbox-001
user_assigned_managed_identity_resource_id = "" # Set via CI/CD variable

# Diagnostic settings destination - central Log Analytics workspace
log_analytics_workspace_id = "" # Set via CI/CD variable

# Policy category flags - AMA and diagnostics on, private endpoints audit-only
enable_ama_policies                 = "Yes"
enable_diagnostic_settings_policies = "Yes"
enable_private_endpoint_policies    = "Yes"

# Private endpoints flagged only in sandbox (never block)
private_endpoint_effect = "Audit"

# No exclusions in sandbox
policy_assignment_exclusion_list = []

# Tags
resource_tags = {
  _deployed_by_monitoring_baselines = "True"
  environment                       = "sandbox"
  cost_center                       = "platform-engineering"
}
