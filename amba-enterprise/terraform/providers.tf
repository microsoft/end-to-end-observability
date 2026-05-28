provider "azurerm" {
  features {}

  # Use environment-specific subscription for state and UAMI
  subscription_id = var.management_subscription_id
}

provider "azapi" {}
