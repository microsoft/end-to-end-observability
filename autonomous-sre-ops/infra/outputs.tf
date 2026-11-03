output "resource_groups" {
  value = { for name, group in azurerm_resource_group.demo : name => group.name }
}

output "targets" {
  description = "Private addresses are not SSH trust anchors. Pin each host key through an authenticated bootstrap."
  value = {
    for name, vm in azurerm_linux_virtual_machine.target : name => {
      resource_id         = vm.id
      address             = local.targets[name].address
      runner_username     = "asrerunner"
      deployment_username = vm.admin_username
    }
  }
}

output "log_analytics_workspace_id" {
  value = azurerm_log_analytics_workspace.demo.id
}

output "registry_name" {
  value = azurerm_container_registry.demo.name
}

output "registry_server" {
  value = azurerm_container_registry.demo.login_server
}

output "key_vault_uri" {
  value = azurerm_key_vault.operations.vault_uri
}

output "storage_account_name" {
  value = azurerm_storage_account.operations.name
}

output "managed_identities" {
  value = {
    for name, identity in azurerm_user_assigned_identity.demo : name => {
      resource_id  = identity.id
      client_id    = identity.client_id
      principal_id = identity.principal_id
    }
  }
}

output "agent_resource_id" {
  value = var.deploy_managed_agent ? azapi_resource.agent[0].id : null
}

output "agent_endpoint" {
  value = var.deploy_managed_agent ? try(azapi_resource.agent[0].output.properties.agentEndpoint, null) : null
}

output "agent_network_subnet_id" {
  description = "Dedicated managed-agent egress subnet; verify effective routing from an actual workspace tool."
  value       = azurerm_subnet.demo["agent"].id
}

output "faults_enabled" {
  value = false
}

output "runtime_jobs" {
  value = { for name, job in azurerm_container_app_job.operations : name => job.name }
}

output "runtime_bootstrap_client_id" {
  value = var.deploy_runtime ? azurerm_user_assigned_identity.bootstrap[0].client_id : null
}

output "dashboard" {
  description = "Nonsecret dashboard metadata for protected delivery and the owned Entra bootstrap."
  value = local.dashboard_foundation ? {
    phase              = var.dashboard.phase
    resource_group     = azurerm_resource_group.dashboard[0].name
    environment_name   = azurerm_container_app_environment.dashboard[0].name
    app_name           = local.dashboard_name
    public_origin      = local.dashboard_origin
    resource_id        = local.dashboard_deployed ? azapi_resource.dashboard[0].id : null
    identity_client_id = azurerm_user_assigned_identity.dashboard[0].client_id
    identity_id        = azurerm_user_assigned_identity.dashboard[0].id
    vault_uri          = azurerm_key_vault.dashboard[0].vault_uri
    viewer_group_id    = local.dashboard_group_id
  } : null
}
