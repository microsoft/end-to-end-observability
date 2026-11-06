resource "azurerm_storage_account" "operations" {
  name                            = "stasre${local.suffix}"
  resource_group_name             = azurerm_resource_group.demo["control"].name
  location                        = var.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  shared_access_key_enabled       = false
  default_to_oauth_authentication = true
  public_network_access_enabled   = false
  allow_nested_items_to_be_public = false
  tags                            = local.tags

  blob_properties {
    versioning_enabled = true
    delete_retention_policy {
      days = 7
    }
    container_delete_retention_policy {
      days = 7
    }
  }

  network_rules {
    default_action = "Deny"
    bypass         = ["None"]

    # Preserve Defender's exact service-owned scanner rule without broad trusted-service bypass.
    private_link_access {
      endpoint_resource_id = "/subscriptions/${var.subscription_id}/providers/Microsoft.Security/datascanners/StorageDataScanner"
      endpoint_tenant_id   = var.tenant_id
    }
  }
}

# ARM child resources avoid requiring the delivery agent to reach the new data plane.
resource "azapi_resource" "evidence_container" {
  type      = "Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01"
  name      = "evidence"
  parent_id = "${azurerm_storage_account.operations.id}/blobServices/default"
  body = {
    properties = {
      publicAccess = "None"
    }
  }
}

resource "azapi_resource" "ledger" {
  for_each  = toset(["Episodes", "Operations", "TargetLocks", "PendingWrites"])
  type      = "Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01"
  name      = each.key
  parent_id = "${azurerm_storage_account.operations.id}/tableServices/default"
  body      = { properties = {} }
}

resource "azapi_resource" "pending_queue" {
  type      = "Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01"
  name      = "pending-writes"
  parent_id = "${azurerm_storage_account.operations.id}/queueServices/default"
  body      = { properties = {} }
}

resource "azurerm_key_vault" "operations" {
  name                          = "kv-asre-${local.suffix}"
  location                      = var.location
  resource_group_name           = azurerm_resource_group.demo["control"].name
  tenant_id                     = var.tenant_id
  sku_name                      = "standard"
  rbac_authorization_enabled    = true
  purge_protection_enabled      = true
  soft_delete_retention_days    = 7
  public_network_access_enabled = false
  tags                          = local.tags

  network_acls {
    default_action = "Deny"
    bypass         = "None"
  }

  lifecycle {
    prevent_destroy = true
  }
}

locals {
  private_services = {
    blob  = { resource = azurerm_storage_account.operations.id, zone = "privatelink.blob.core.windows.net" }
    table = { resource = azurerm_storage_account.operations.id, zone = "privatelink.table.core.windows.net" }
    queue = { resource = azurerm_storage_account.operations.id, zone = "privatelink.queue.core.windows.net" }
    vault = { resource = azurerm_key_vault.operations.id, zone = "privatelink.vaultcore.azure.net" }
  }
}

resource "azurerm_private_dns_zone" "service" {
  for_each            = local.private_services
  name                = each.value.zone
  resource_group_name = azurerm_resource_group.demo["control"].name
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "service" {
  for_each              = local.private_services
  name                  = "asre"
  resource_group_name   = azurerm_resource_group.demo["control"].name
  private_dns_zone_name = azurerm_private_dns_zone.service[each.key].name
  virtual_network_id    = azurerm_virtual_network.demo.id
  registration_enabled  = false
  tags                  = local.tags
}

# The satellite runner VNet resolves the same four zones to the primary-region private endpoints.
resource "azurerm_private_dns_zone_virtual_network_link" "satellite" {
  for_each              = local.runner_satellite ? local.private_services : {}
  name                  = "asre-runner"
  resource_group_name   = azurerm_resource_group.demo["control"].name
  private_dns_zone_name = azurerm_private_dns_zone.service[each.key].name
  virtual_network_id    = azurerm_virtual_network.satellite[0].id
  registration_enabled  = false
  tags                  = local.tags
}

resource "azurerm_private_endpoint" "service" {
  for_each            = local.private_services
  name                = "pe-asre-${each.key}-cus"
  location            = var.location
  resource_group_name = azurerm_resource_group.demo["control"].name
  subnet_id           = azurerm_subnet.demo["endpoints"].id
  tags                = local.tags

  private_service_connection {
    name                           = "asre-${each.key}"
    private_connection_resource_id = each.value.resource
    subresource_names              = [each.key]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "asre"
    private_dns_zone_ids = [azurerm_private_dns_zone.service[each.key].id]
  }
}

resource "azurerm_role_assignment" "table_writer" {
  for_each             = toset(["runner", "collector"])
  scope                = azurerm_storage_account.operations.id
  role_definition_name = "Storage Table Data Contributor"
  principal_id         = azurerm_user_assigned_identity.demo[each.key].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "blob_writer" {
  for_each             = toset(["runner", "collector"])
  scope                = azurerm_storage_account.operations.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.demo[each.key].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "runner_secret" {
  scope                = azurerm_key_vault.operations.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.demo["runner"].principal_id
  principal_type       = "ServicePrincipal"
}
