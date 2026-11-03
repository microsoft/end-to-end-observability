resource "azurerm_virtual_network" "demo" {
  name                = "vnet-asre-cus"
  address_space       = ["10.42.0.0/16"]
  location            = var.location
  resource_group_name = azurerm_resource_group.demo["control"].name
  tags                = local.tags
}

resource "azurerm_subnet" "demo" {
  for_each                          = local.subnets
  name                              = "snet-${each.key}"
  resource_group_name               = azurerm_resource_group.demo["control"].name
  virtual_network_name              = azurerm_virtual_network.demo.name
  address_prefixes                  = [each.value]
  default_outbound_access_enabled   = false
  private_endpoint_network_policies = each.key == "endpoints" ? "Enabled" : "Disabled"

  dynamic "delegation" {
    for_each = contains(["runner", "agent"], each.key) ? [each.key] : []
    content {
      name = "app-environments"
      service_delegation {
        name    = "Microsoft.App/environments"
        actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
      }
    }
  }
}

resource "azurerm_network_security_group" "demo" {
  for_each            = local.subnets
  name                = "nsg-asre-${each.key}-cus"
  location            = var.location
  resource_group_name = azurerm_resource_group.demo["control"].name
  tags                = local.tags
}

resource "azurerm_subnet_network_security_group_association" "demo" {
  for_each                  = local.subnets
  subnet_id                 = azurerm_subnet.demo[each.key].id
  network_security_group_id = azurerm_network_security_group.demo[each.key].id
}

resource "azurerm_network_security_rule" "runner_ssh" {
  name                        = "AllowRunnerSSH"
  priority                    = 100
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "22"
  source_address_prefix       = local.subnets.runner
  destination_address_prefix  = local.subnets.workloads
  resource_group_name         = azurerm_resource_group.demo["control"].name
  network_security_group_name = azurerm_network_security_group.demo["workloads"].name
}

resource "azurerm_network_security_rule" "workload_probe" {
  name                        = "AllowInternalWorkloadProbe"
  priority                    = 110
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "8080"
  source_address_prefixes     = [local.subnets.runner, local.subnets.agent]
  destination_address_prefix  = local.subnets.workloads
  resource_group_name         = azurerm_resource_group.demo["control"].name
  network_security_group_name = azurerm_network_security_group.demo["workloads"].name
}

resource "azurerm_network_security_rule" "deny_workload_inbound" {
  name                        = "DenyOtherInbound"
  priority                    = 4000
  direction                   = "Inbound"
  access                      = "Deny"
  protocol                    = "*"
  source_port_range           = "*"
  destination_port_range      = "*"
  source_address_prefix       = "*"
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.demo["control"].name
  network_security_group_name = azurerm_network_security_group.demo["workloads"].name
}

resource "azurerm_public_ip" "egress" {
  name                = "pip-asre-egress-cus"
  resource_group_name = azurerm_resource_group.demo["control"].name
  location            = var.location
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = local.tags

  lifecycle {
    # Azure injects this platform-owned IP tag; removing it would replace live egress.
    ignore_changes = [ip_tags["FirstPartyUsage"]]
  }
}

resource "azurerm_nat_gateway" "demo" {
  name                    = "nat-asre-cus"
  location                = var.location
  resource_group_name     = azurerm_resource_group.demo["control"].name
  sku_name                = "Standard"
  idle_timeout_in_minutes = 10
  tags                    = local.tags
}

resource "azurerm_nat_gateway_public_ip_association" "demo" {
  nat_gateway_id       = azurerm_nat_gateway.demo.id
  public_ip_address_id = azurerm_public_ip.egress.id
}

resource "azurerm_subnet_nat_gateway_association" "demo" {
  for_each       = toset(["workloads", "runner", "agent"])
  subnet_id      = azurerm_subnet.demo[each.key].id
  nat_gateway_id = azurerm_nat_gateway.demo.id
}
