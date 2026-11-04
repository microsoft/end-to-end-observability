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
  source_address_prefix       = local.runner_cidr
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
  source_address_prefixes     = [local.runner_cidr, local.subnets.agent]
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

# Optional runner-only satellite network (var.runner_location, only with deploy_runtime). Every resource below has
# count 0 by default. The primary runner subnet, its NSG and its NAT association stay in place and unused.
resource "azurerm_virtual_network" "satellite" {
  count               = local.runner_satellite ? 1 : 0
  name                = "vnet-asre-runner-${local.runner_region_code}"
  address_space       = [local.satellite_network.vnet]
  location            = local.runner_location
  resource_group_name = azurerm_resource_group.demo["control"].name
  tags                = local.tags
}

resource "azurerm_subnet" "satellite_runner" {
  count                             = local.runner_satellite ? 1 : 0
  name                              = "snet-runner"
  resource_group_name               = azurerm_resource_group.demo["control"].name
  virtual_network_name              = azurerm_virtual_network.satellite[0].name
  address_prefixes                  = [local.satellite_network.runner]
  default_outbound_access_enabled   = false
  private_endpoint_network_policies = "Disabled"

  delegation {
    name = "app-environments"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_network_security_group" "satellite_runner" {
  count               = local.runner_satellite ? 1 : 0
  name                = "nsg-asre-runner-${local.runner_region_code}"
  location            = local.runner_location
  resource_group_name = azurerm_resource_group.demo["control"].name
  tags                = local.tags
}

resource "azurerm_subnet_network_security_group_association" "satellite_runner" {
  count                     = local.runner_satellite ? 1 : 0
  subnet_id                 = azurerm_subnet.satellite_runner[0].id
  network_security_group_id = azurerm_network_security_group.satellite_runner[0].id
}

resource "azurerm_public_ip" "satellite_egress" {
  count               = local.runner_satellite ? 1 : 0
  name                = "pip-asre-egress-${local.runner_region_code}"
  resource_group_name = azurerm_resource_group.demo["control"].name
  location            = local.runner_location
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = local.tags

  lifecycle {
    # Azure injects this platform-owned IP tag; removing it would replace live egress.
    ignore_changes = [ip_tags["FirstPartyUsage"]]
  }
}

resource "azurerm_nat_gateway" "satellite" {
  count                   = local.runner_satellite ? 1 : 0
  name                    = "nat-asre-${local.runner_region_code}"
  location                = local.runner_location
  resource_group_name     = azurerm_resource_group.demo["control"].name
  sku_name                = "Standard"
  idle_timeout_in_minutes = 10
  tags                    = local.tags
}

resource "azurerm_nat_gateway_public_ip_association" "satellite" {
  count                = local.runner_satellite ? 1 : 0
  nat_gateway_id       = azurerm_nat_gateway.satellite[0].id
  public_ip_address_id = azurerm_public_ip.satellite_egress[0].id
}

resource "azurerm_subnet_nat_gateway_association" "satellite_runner" {
  count          = local.runner_satellite ? 1 : 0
  subnet_id      = azurerm_subnet.satellite_runner[0].id
  nat_gateway_id = azurerm_nat_gateway.satellite[0].id
}

# Global peering, both directions, between the two VNets only (no hub, no gateway, no transit).
resource "azurerm_virtual_network_peering" "primary_to_satellite" {
  count                        = local.runner_satellite ? 1 : 0
  name                         = "peer-asre-cus-to-${local.runner_region_code}"
  resource_group_name          = azurerm_resource_group.demo["control"].name
  virtual_network_name         = azurerm_virtual_network.demo.name
  remote_virtual_network_id    = azurerm_virtual_network.satellite[0].id
  allow_virtual_network_access = true
  allow_forwarded_traffic      = false
  allow_gateway_transit        = false
  use_remote_gateways          = false
}

resource "azurerm_virtual_network_peering" "satellite_to_primary" {
  count                        = local.runner_satellite ? 1 : 0
  name                         = "peer-asre-${local.runner_region_code}-to-cus"
  resource_group_name          = azurerm_resource_group.demo["control"].name
  virtual_network_name         = azurerm_virtual_network.satellite[0].name
  remote_virtual_network_id    = azurerm_virtual_network.demo.id
  allow_virtual_network_access = true
  allow_forwarded_traffic      = false
  allow_gateway_transit        = false
  use_remote_gateways          = false

  # The provider cannot create two peerings at the same time (azurerm docs: "Virtual Network
  # peerings cannot be created, updated or deleted concurrently").
  depends_on = [azurerm_virtual_network_peering.primary_to_satellite]
}
