# -----------------------------------------------------------------------------
# Outputs
# -----------------------------------------------------------------------------

output "initiative_id" {
  description = "Resource ID of the custom Monitoring Baselines policy set definition (initiative)"
  value       = azurerm_policy_set_definition.monitoring_baselines.id
}

output "assignment_id" {
  description = "Resource ID of the Monitoring Baselines policy assignment"
  value       = azurerm_management_group_policy_assignment.monitoring_baselines.id
}

output "assignment_principal_id" {
  description = "Principal ID of the remediation identity used by the assignment"
  value       = data.azurerm_user_assigned_identity.remediation.principal_id
}

output "enabled_policy_categories" {
  description = "Map of policy category enablement state"
  value = {
    ama                 = var.enable_ama_policies
    diagnostic_settings = var.enable_diagnostic_settings_policies
    private_endpoints   = var.enable_private_endpoint_policies
  }
}

output "private_endpoint_effect" {
  description = "Effect applied to private endpoint posture policies"
  value       = var.private_endpoint_effect
}

output "environment" {
  description = "Current deployment environment ring"
  value       = var.environment
}
