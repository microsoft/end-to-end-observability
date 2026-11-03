locals {
  tags = {
    project     = "asre-demo"
    environment = "demonstration"
    managed-by  = "terraform"
    fault-state = "disabled"
  }
  groups = toset(["control", "linux", "patch", "backup"])
  suffix = substr(sha256("${var.subscription_id}:asre-demo"), 0, 10)
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
