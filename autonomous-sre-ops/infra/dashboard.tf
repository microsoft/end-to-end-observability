locals {
  dashboard_foundation = var.dashboard.phase != "disabled"
  dashboard_deployed   = contains(["private", "public"], var.dashboard.phase)
  dashboard_group_id   = var.dashboard.viewer_group_id
  dashboard_name       = "ca-asre-dash-${local.suffix}"
  dashboard_tags       = merge(local.tags, { component = "dashboard" })
  dashboard_origin     = local.dashboard_foundation ? "https://${local.dashboard_name}.${azurerm_container_app_environment.dashboard[0].default_domain}" : null
}

resource "azurerm_resource_group" "dashboard" {
  count    = local.dashboard_foundation ? 1 : 0
  name     = "rg-asre-dash-${local.suffix}"
  location = var.location
  tags     = local.dashboard_tags
}

resource "azurerm_container_app_environment" "dashboard" {
  count                      = local.dashboard_foundation ? 1 : 0
  name                       = "cae-asre-dash-${local.suffix}"
  location                   = var.location
  resource_group_name        = azurerm_resource_group.dashboard[0].name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.demo.id
  tags                       = local.dashboard_tags

  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
    minimum_count         = 0
    maximum_count         = 0
  }
}

resource "azurerm_user_assigned_identity" "dashboard" {
  count               = local.dashboard_foundation ? 1 : 0
  name                = "id-asre-dash-${local.suffix}"
  location            = var.location
  resource_group_name = azurerm_resource_group.dashboard[0].name
  tags                = local.dashboard_tags
}

resource "azurerm_role_assignment" "dashboard_pull" {
  count                = local.dashboard_foundation ? 1 : 0
  scope                = azurerm_container_registry.demo.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.dashboard[0].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_key_vault" "dashboard" {
  count                         = local.dashboard_foundation ? 1 : 0
  name                          = "kv-asre-dash-${local.suffix}"
  location                      = var.location
  resource_group_name           = azurerm_resource_group.dashboard[0].name
  tenant_id                     = var.tenant_id
  sku_name                      = "standard"
  rbac_authorization_enabled    = true
  purge_protection_enabled      = true
  soft_delete_retention_days    = 7
  public_network_access_enabled = false
  tags                          = local.dashboard_tags
}

resource "azurerm_role_definition" "dashboard_credential_setter" {
  count       = local.dashboard_foundation ? 1 : 0
  name        = "ASRE dashboard owned credential setter"
  scope       = azurerm_resource_group.dashboard[0].id
  description = "Set the new dashboard sign-in credential; no secret get, delete or purge."
  permissions {
    actions      = []
    data_actions = ["Microsoft.KeyVault/vaults/secrets/setSecret/action"]
  }
  assignable_scopes = [azurerm_resource_group.dashboard[0].id]
}

resource "azurerm_role_assignment" "dashboard_credential_setter" {
  count              = local.dashboard_foundation ? 1 : 0
  scope              = azurerm_key_vault.dashboard[0].id
  role_definition_id = azurerm_role_definition.dashboard_credential_setter[0].role_definition_resource_id
  principal_id       = var.operator_object_id
  principal_type     = "User"
}

resource "azurerm_role_assignment" "dashboard_credential_reader" {
  count                = local.dashboard_foundation ? 1 : 0
  scope                = azurerm_key_vault.dashboard[0].id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.dashboard[0].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_definition" "dashboard_native_reader" {
  count       = local.dashboard_foundation ? 1 : 0
  name        = "ASRE dashboard native viewer"
  scope       = azurerm_resource_group.demo["control"].id
  description = "Read the fixed SRE agent's incidents, threads, schedule and connector state; no writes or approvals."
  permissions {
    actions = ["Microsoft.App/agents/read"]
    data_actions = [
      "Microsoft.App/agents/threads/read",
      "Microsoft.App/agents/incidentManagement/read",
      "Microsoft.App/agents/extendedAgents/read",
      "Microsoft.App/agents/scheduledtasks/read",
    ]
  }
  assignable_scopes = [azurerm_resource_group.demo["control"].id]
}

resource "azurerm_role_assignment" "dashboard_native_reader" {
  count              = local.dashboard_foundation ? 1 : 0
  scope              = azapi_resource.agent[0].id
  role_definition_id = azurerm_role_definition.dashboard_native_reader[0].role_definition_resource_id
  principal_id       = azurerm_user_assigned_identity.dashboard[0].principal_id
  principal_type     = "ServicePrincipal"
}

resource "azurerm_role_definition" "dashboard_logs_reader" {
  count       = local.dashboard_foundation ? 1 : 0
  name        = "ASRE dashboard fixed log query reader"
  scope       = azurerm_resource_group.demo["control"].id
  description = "Read workspace metadata and query Perf/provider data; no shared keys, export jobs or support writes."
  permissions {
    actions = [
      "Microsoft.OperationalInsights/workspaces/read",
      "Microsoft.OperationalInsights/workspaces/query/read",
      "Microsoft.OperationalInsights/workspaces/query/Perf/read",
      "Microsoft.OperationalInsights/workspaces/analytics/query/action",
      "Microsoft.OperationalInsights/workspaces/search/action",
    ]
  }
  assignable_scopes = [azurerm_resource_group.demo["control"].id]
}

resource "azurerm_role_assignment" "dashboard_logs_reader" {
  count              = local.dashboard_foundation ? 1 : 0
  scope              = azurerm_log_analytics_workspace.demo.id
  role_definition_id = azurerm_role_definition.dashboard_logs_reader[0].role_definition_resource_id
  principal_id       = azurerm_user_assigned_identity.dashboard[0].principal_id
  principal_type     = "ServicePrincipal"
}

resource "azurerm_role_assignment" "dashboard_resource_reader" {
  for_each = local.dashboard_foundation ? merge(
    { for name, rule in azurerm_monitor_scheduled_query_rules_alert_v2.guest : "rule-${name}" => rule.id
    if contains(["disk", "patch-installation", "noise-overlap", "capacity-growth", "backup-job"], name) },
    {
      patch = azurerm_linux_virtual_machine.target["patch"].id
      vault = azurerm_recovery_services_vault.demo.id
    },
  ) : {}
  scope                = each.value
  role_definition_name = "Reader"
  principal_id         = azurerm_user_assigned_identity.dashboard[0].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_definition" "dashboard_alert_reader" {
  count       = local.dashboard_foundation ? 1 : 0
  name        = "ASRE dashboard alert instance reader"
  scope       = "/subscriptions/${var.subscription_id}"
  description = "Read subscription-level alert instances through the dashboard's fixed ARG query; no alert changes."
  permissions {
    actions = [
      "Microsoft.AlertsManagement/alerts/read",
      "Microsoft.ResourceGraph/resources/read",
    ]
  }
  assignable_scopes = ["/subscriptions/${var.subscription_id}"]
}

resource "azurerm_role_assignment" "dashboard_alert_reader" {
  count              = local.dashboard_foundation ? 1 : 0
  scope              = "/subscriptions/${var.subscription_id}"
  role_definition_id = azurerm_role_definition.dashboard_alert_reader[0].role_definition_resource_id
  principal_id       = azurerm_user_assigned_identity.dashboard[0].principal_id
  principal_type     = "ServicePrincipal"
}

# AzAPI GET retains references, without requiring Plan to list secret values.
resource "azapi_resource" "dashboard" {
  count     = local.dashboard_deployed ? 1 : 0
  type      = "Microsoft.App/containerApps@2024-03-01"
  name      = local.dashboard_name
  parent_id = azurerm_resource_group.dashboard[0].id
  location  = var.location
  tags      = local.dashboard_tags
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.dashboard[0].id]
  }
  body = {
    properties = {
      managedEnvironmentId = azurerm_container_app_environment.dashboard[0].id
      workloadProfileName  = "Consumption"
      configuration = {
        activeRevisionsMode = "Single"
        ingress = {
          external      = var.dashboard.phase == "public"
          allowInsecure = false
          targetPort    = 8765
          transport     = "http"
          traffic       = [{ latestRevision = true, weight = 100 }]
        }
        registries = [{
          server   = azurerm_container_registry.demo.login_server
          identity = azurerm_user_assigned_identity.dashboard[0].id
        }]
        secrets = [{
          name        = "entra-client-secret"
          keyVaultUrl = var.dashboard.secret_version_uri
          identity    = azurerm_user_assigned_identity.dashboard[0].id
        }]
      }
      template = {
        containers = [{
          name  = "dashboard"
          image = "${azurerm_container_registry.demo.login_server}/asre-dashboard@${var.dashboard.image_digest}"
          args  = var.dashboard.phase == "private" ? ["--probe-sources"] : []
          resources = {
            cpu    = 0.25
            memory = "0.5Gi"
          }
          env = [
            { name = "ASRE_CONTEXT", value = jsonencode(local.context_core) },
            { name = "ASRE_DASHBOARD_HOST", value = "0.0.0.0" },
            { name = "ASRE_DASHBOARD_PUBLIC_ORIGIN", value = local.dashboard_origin },
            { name = "ASRE_DASHBOARD_MANAGED_IDENTITY_CLIENT_ID", value = azurerm_user_assigned_identity.dashboard[0].client_id },
          ]
          probes = [
            for kind in ["Liveness", "Readiness", "Startup"] : {
              type = kind
              httpGet = {
                path        = "/healthz"
                port        = 8765
                httpHeaders = [{ name = "Host", value = trimprefix(local.dashboard_origin, "https://") }]
              }
              initialDelaySeconds = 5
              periodSeconds       = 10
              timeoutSeconds      = 3
              failureThreshold    = kind == "Startup" ? 30 : 3
            }
          ]
        }]
        scale = {
          minReplicas = 0
          maxReplicas = 1
          rules = [{
            name = "dashboard-http"
            http = { metadata = { concurrentRequests = "10" } }
          }]
        }
      }
    }
  }
  depends_on = [
    azurerm_role_assignment.dashboard_pull,
    azurerm_role_assignment.dashboard_credential_reader,
  ]
}

resource "azapi_resource" "dashboard_auth" {
  count     = local.dashboard_deployed ? 1 : 0
  type      = "Microsoft.App/containerApps/authConfigs@2024-03-01"
  name      = "current"
  parent_id = azapi_resource.dashboard[0].id
  body = {
    properties = {
      platform = { enabled = true }
      globalValidation = {
        unauthenticatedClientAction = "RedirectToLoginPage"
        redirectToProvider          = "azureActiveDirectory"
        excludedPaths               = []
      }
      httpSettings = { requireHttps = true }
      identityProviders = {
        azureActiveDirectory = {
          enabled = true
          registration = {
            clientId                = var.dashboard.entra_client_id
            clientSecretSettingName = "entra-client-secret"
            openIdIssuer            = "https://login.microsoftonline.com/${var.tenant_id}/v2.0"
          }
          validation = {
            allowedAudiences = [var.dashboard.entra_client_id]
            defaultAuthorizationPolicy = {
              allowedPrincipals = { groups = [local.dashboard_group_id] }
            }
          }
        }
      }
    }
  }
}
