# -----------------------------------------------------------------------------
# Outputs
# -----------------------------------------------------------------------------

output "monitoring_resource_group_id" {
  description = "Resource ID of the AMBA monitoring resource group"
  value       = module.monitoring_resource_group.id
}

output "user_assigned_identity_id" {
  description = "Resource ID of the UAMI used by AMBA policies"
  value       = var.bring_your_own_user_assigned_managed_identity == "No" ? module.user_assigned_identity[0].id : var.bring_your_own_user_assigned_managed_identity_resource_id
}

output "deployment_location" {
  description = "Region used for this deployment (locked after first deploy)"
  value       = var.deployment_location
}

output "enabled_initiatives" {
  description = "Map of initiative enable/disable state"
  value       = local.initiative_flags
}

output "environment" {
  description = "Current deployment environment ring"
  value       = var.environment
}
