resource "azurerm_recovery_services_vault" "demo" {
  name                = "rsv-asre-cus"
  location            = var.location
  resource_group_name = azurerm_resource_group.demo["backup"].name
  sku                 = "Standard"
  storage_mode_type   = "LocallyRedundant"
  soft_delete_enabled = true
  tags                = local.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_backup_policy_vm" "demo" {
  name                = "asre-daily"
  resource_group_name = azurerm_resource_group.demo["backup"].name
  recovery_vault_name = azurerm_recovery_services_vault.demo.name
  timezone            = "UTC"
  policy_type         = "V1"

  backup {
    frequency = "Daily"
    time      = "02:00"
  }
  retention_daily {
    count = 7
  }
}

resource "azurerm_backup_protected_vm" "demo" {
  count               = var.backup_protection_enabled ? 1 : 0
  resource_group_name = azurerm_resource_group.demo["backup"].name
  recovery_vault_name = azurerm_recovery_services_vault.demo.name
  source_vm_id        = azurerm_linux_virtual_machine.target["backup"].id
  backup_policy_id    = azurerm_backup_policy_vm.demo.id

  lifecycle {
    prevent_destroy = true
  }
}
