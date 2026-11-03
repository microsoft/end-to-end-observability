terraform {
  backend "azurerm" {
    resource_group_name  = "rg-example-state-001"
    storage_account_name = "stexamplestate001"
    container_name       = "tfstate"
    key                  = "autonomous-sre-ops"
    subscription_id      = "00000003-000a-4000-8000-000000000003"
    tenant_id            = "00000002-000a-4000-9000-000000000002"
    use_azuread_auth     = true
  }
}
