resource "azapi_resource" "agent" {
  count = var.deploy_managed_agent ? 1 : 0
  # The pinned embedded schema omits the networking fields in Microsoft's VNet example.
  schema_validation_enabled = false
  type                      = "Microsoft.App/agents@2026-01-01"
  name                      = "asre-demo-cus"
  parent_id                 = azurerm_resource_group.demo["control"].id
  location                  = var.location
  tags                      = local.tags
  response_export_values    = ["properties.agentEndpoint"]

  identity {
    type         = "SystemAssigned, UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.demo["agent"].id]
  }

  body = {
    properties = {
      actionConfiguration = {
        mode        = "Review"
        accessLevel = "Low"
        identity    = azurerm_user_assigned_identity.demo["agent"].id
      }
      defaultModel = var.agent_model
      vnetConfiguration = {
        subnetResourceId = azurerm_subnet.demo["agent"].id
      }
      sandboxConfiguration = {
        egress = {
          mode                            = "AzureVNet"
          allowedHosts                    = []
          allowedRegistries               = []
          allowedCodeRepositories         = []
          allowHttpMcpServerNetworkAccess = false
          vnetConfiguration = {
            usePrivateDnsResolution = true
          }
        }
      }
      knowledgeGraphConfiguration = {
        identity         = azurerm_user_assigned_identity.demo["agent"].id
        managedResources = [for group in azurerm_resource_group.demo : group.id]
      }
      logConfiguration = {
        applicationInsightsConfiguration = {
          appId            = azurerm_application_insights.demo.app_id
          connectionString = azurerm_application_insights.demo.connection_string
        }
      }
      upgradeChannel = "Stable"
    }
  }

  depends_on = [
    azurerm_role_assignment.agent_reader,
    azurerm_role_assignment.agent_logs,
    azurerm_role_assignment.agent_monitoring,
    azurerm_subnet_network_security_group_association.demo,
    azurerm_subnet_nat_gateway_association.demo,
    azurerm_private_dns_zone_virtual_network_link.service,
  ]
}

resource "azurerm_role_assignment" "agent_reader" {
  for_each             = azurerm_resource_group.demo
  scope                = each.value.id
  role_definition_name = "Reader"
  principal_id         = azurerm_user_assigned_identity.demo["agent"].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "agent_logs" {
  scope                = azurerm_log_analytics_workspace.demo.id
  role_definition_name = "Log Analytics Reader"
  principal_id         = azurerm_user_assigned_identity.demo["agent"].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "agent_monitoring" {
  for_each             = azurerm_resource_group.demo
  scope                = each.value.id
  role_definition_name = "Monitoring Reader"
  principal_id         = azurerm_user_assigned_identity.demo["agent"].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "agent_administrator" {
  count                = var.deploy_managed_agent ? 1 : 0
  scope                = azapi_resource.agent[0].id
  role_definition_name = "SRE Agent Administrator"
  principal_id         = var.agent_administrator_object_id
  principal_type       = "User"
}

resource "azurerm_role_assignment" "agent_delivery_administrator" {
  count                = var.deploy_managed_agent && var.deploy_runtime ? 1 : 0
  scope                = azapi_resource.agent[0].id
  role_definition_name = "SRE Agent Administrator"
  principal_id         = "00000051-000a-4000-a000-000000000051"
  principal_type       = "ServicePrincipal"
}
