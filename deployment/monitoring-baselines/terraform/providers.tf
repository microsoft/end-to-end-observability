provider "azurerm" {
  features {}

  # Use the management subscription for state and the remediation identity
  subscription_id = var.management_subscription_id
}

provider "azapi" {}
