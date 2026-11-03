locals {
  tags = {
    project     = "autonomous-sre-ops"
    environment = "demonstration"
    managed-by  = "terraform"
    fault-state = "disabled"
  }
  groups = toset(["control", "linux", "patch", "backup"])
  suffix = substr(sha256("${var.subscription_id}:asre-demo"), 0, 10)
  # The context loaders fix this name; the agent resource and the context document share it.
  agent_name = "asre-demo-cus"
  targets = {
    linux  = { name = "vm-asre-linux-cus", address = "10.42.0.10" }
    patch  = { name = "vm-asre-patch-cus", address = "10.42.0.11" }
    backup = { name = "vm-asre-backup-cus", address = "10.42.0.12" }
  }
  subnets = {
    workloads = "10.42.0.0/24"
    runner    = "10.42.1.0/26"
    endpoints = "10.42.2.0/27"
    agent     = "10.42.3.0/27"
  }

  # Deployment context (schema asre-context/v1) without the two values that cannot be static:
  # the agent endpoint (known only after the agent exists) and the operator object id. It must
  # never reference azapi_resource.agent; the runtime jobs and the dashboard receive it as the
  # static ASRE_CONTEXT environment value, and the agent depends on neither.
  context_core = {
    schema          = "asre-context/v1"
    subscription_id = var.subscription_id
    tenant_id       = var.tenant_id
    location        = var.location
    name_suffix     = local.suffix
    resource_groups = { for key, group in azurerm_resource_group.demo : key => group.name }
    log_analytics = {
      name        = azurerm_log_analytics_workspace.demo.name
      customer_id = lower(azurerm_log_analytics_workspace.demo.workspace_id)
    }
    registry = {
      name         = azurerm_container_registry.demo.name
      login_server = azurerm_container_registry.demo.login_server
    }
    key_vault = {
      name = azurerm_key_vault.operations.name
      uri  = azurerm_key_vault.operations.vault_uri
    }
    storage = {
      account_name   = azurerm_storage_account.operations.name
      blob_endpoint  = trimsuffix(azurerm_storage_account.operations.primary_blob_endpoint, "/")
      table_endpoint = trimsuffix(azurerm_storage_account.operations.primary_table_endpoint, "/")
    }
    recovery_vault = {
      name           = azurerm_recovery_services_vault.demo.name
      resource_group = azurerm_recovery_services_vault.demo.resource_group_name
    }
    identities = merge(
      { for key, identity in azurerm_user_assigned_identity.demo : key => {
        client_id    = lower(identity.client_id)
        principal_id = lower(identity.principal_id)
      } },
      { for identity in azurerm_user_assigned_identity.bootstrap : "bootstrap" => {
        client_id    = lower(identity.client_id)
        principal_id = lower(identity.principal_id)
      } },
    )
    targets = { for key, vm in azurerm_linux_virtual_machine.target : key => {
      name           = vm.name
      resource_group = vm.resource_group_name
      address        = local.targets[key].address
    } }
    agent      = { name = local.agent_name }
    servicenow = var.servicenow
  }
}

resource "azurerm_resource_group" "demo" {
  for_each = local.groups
  name     = "rg-asre-${each.key}-cus"
  location = var.location
  tags     = local.tags
}

resource "azurerm_user_assigned_identity" "demo" {
  for_each            = toset(["agent", "runner", "collector", "pull"])
  name                = "id-asre-${each.key}-cus"
  location            = var.location
  resource_group_name = azurerm_resource_group.demo["control"].name
  tags                = local.tags
}

resource "azurerm_log_analytics_workspace" "demo" {
  name                       = "law-asre-cus"
  location                   = var.location
  resource_group_name        = azurerm_resource_group.demo["control"].name
  sku                        = "PerGB2018"
  retention_in_days          = 30
  daily_quota_gb             = 1
  internet_ingestion_enabled = true
  internet_query_enabled     = true
  tags                       = local.tags
}

resource "azurerm_application_insights" "demo" {
  name                = "appi-asre-cus"
  location            = var.location
  resource_group_name = azurerm_resource_group.demo["control"].name
  workspace_id        = azurerm_log_analytics_workspace.demo.id
  application_type    = "web"
  retention_in_days   = 30
  tags                = local.tags
}

resource "azurerm_container_registry" "demo" {
  name                = "acrasre${local.suffix}"
  resource_group_name = azurerm_resource_group.demo["control"].name
  location            = var.location
  sku                 = "Basic"
  admin_enabled       = false
  tags                = local.tags
}

resource "azurerm_role_assignment" "pull" {
  scope                = azurerm_container_registry.demo.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.demo["pull"].principal_id
  principal_type       = "ServicePrincipal"
}
