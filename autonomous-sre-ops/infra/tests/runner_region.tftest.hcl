# Offline proof of the optional satellite runner region (var.runner_location). Nothing here contacts Azure:
# both providers are mocked, every run only plans, and all identifiers are synthetic. The SSH public keys
# are assembled at run time from the OpenSSH wire format, so no key material is stored in this file.
#
# Default (runner_location unset): zero satellite resources and the runtime stays on the primary runner
# subnet in centralus. runner_location = "southcentralus": the runner-only satellite network, both
# peerings and the four DNS links exist, and only the environment and its jobs move region.

mock_provider "azurerm" {}
mock_provider "azapi" {}

variables {
  subscription_id       = "00000001-000a-4000-a000-000000000001"
  tenant_id             = "00000002-000a-4000-9000-000000000002"
  admin_ssh_public_key  = "ssh-ed25519 ${base64encode("\u0000\u0000\u0000\u000bssh-ed25519\u0000\u0000\u0000 ${format("%032d", 1)}")} offline-admin"
  runner_ssh_public_key = "ssh-ed25519 ${base64encode("\u0000\u0000\u0000\u000bssh-ed25519\u0000\u0000\u0000 ${format("%032d", 2)}")} offline-runner"
  operator_object_id    = "00000003-000a-4000-9000-000000000003"
  deploy_runtime        = true
  runtime_image_digest  = "sha256:${format("%064d", 7)}"
}

# Subnet ids are computed by the provider; fixing them lets the runs compare the environment subnet exactly.
override_resource {
  target          = azurerm_subnet.demo["runner"]
  override_during = plan
  values = {
    id = "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.Network/virtualNetworks/vnet-asre-cus/subnets/snet-runner"
  }
}

override_resource {
  target          = azurerm_subnet.satellite_runner[0]
  override_during = plan
  values = {
    id = "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.Network/virtualNetworks/vnet-asre-runner-scus/subnets/snet-runner"
  }
}

run "default_keeps_the_runtime_on_the_primary_runner_subnet" {
  command = plan

  assert {
    condition = (
      length(azurerm_virtual_network.satellite) == 0 &&
      length(azurerm_subnet.satellite_runner) == 0 &&
      length(azurerm_network_security_group.satellite_runner) == 0 &&
      length(azurerm_subnet_network_security_group_association.satellite_runner) == 0 &&
      length(azurerm_public_ip.satellite_egress) == 0 &&
      length(azurerm_nat_gateway.satellite) == 0 &&
      length(azurerm_nat_gateway_public_ip_association.satellite) == 0 &&
      length(azurerm_subnet_nat_gateway_association.satellite_runner) == 0 &&
      length(azurerm_virtual_network_peering.primary_to_satellite) == 0 &&
      length(azurerm_virtual_network_peering.satellite_to_primary) == 0 &&
      length(azurerm_private_dns_zone_virtual_network_link.satellite) == 0
    )
    error_message = "Without runner_location no satellite resource may exist."
  }

  assert {
    condition     = local.runner_satellite == false && local.runner_location == "centralus" && local.runner_cidr == "10.42.1.0/26"
    error_message = "Without runner_location the runner region is centralus and the runner CIDR is the primary runner subnet."
  }

  assert {
    condition = (
      azurerm_container_app_environment.operations[0].name == "cae-asre-operations-cus" &&
      azurerm_container_app_environment.operations[0].location == "centralus" &&
      azurerm_container_app_environment.operations[0].infrastructure_resource_group_name == "ME_cae-asre-operations-cus_rg-asre-control-cus_centralus"
    )
    error_message = "By default the environment keeps its name, stays in centralus and keeps its original ME_ resource group name."
  }

  assert {
    condition     = azurerm_container_app_environment.operations[0].infrastructure_subnet_id == azurerm_subnet.demo["runner"].id
    error_message = "By default the environment must use the primary runner subnet."
  }

  assert {
    condition = (
      join(",", sort(keys(azurerm_container_app_job.operations))) == "bootstrap,collector,worker" &&
      alltrue([for key, job in azurerm_container_app_job.operations : job.location == "centralus" && job.name == "job-asre-${key}-cus"])
    )
    error_message = "By default every runtime job keeps its name and stays in centralus."
  }

  assert {
    condition = (
      azurerm_network_security_rule.runner_ssh.source_address_prefix == "10.42.1.0/26" &&
      join(",", sort(azurerm_network_security_rule.workload_probe.source_address_prefixes)) == "10.42.1.0/26,10.42.3.0/27"
    )
    error_message = "By default the workloads NSG rules must name the primary runner subnet (and the agent subnet for the probe)."
  }
}

run "runner_location_builds_the_satellite_and_moves_only_the_runtime" {
  command = plan

  variables {
    runner_location = "southcentralus"
  }

  assert {
    condition = (
      length(azurerm_virtual_network.satellite) == 1 &&
      length(azurerm_subnet.satellite_runner) == 1 &&
      length(azurerm_network_security_group.satellite_runner) == 1 &&
      length(azurerm_subnet_network_security_group_association.satellite_runner) == 1 &&
      length(azurerm_public_ip.satellite_egress) == 1 &&
      length(azurerm_nat_gateway.satellite) == 1 &&
      length(azurerm_nat_gateway_public_ip_association.satellite) == 1 &&
      length(azurerm_subnet_nat_gateway_association.satellite_runner) == 1 &&
      length(azurerm_virtual_network_peering.primary_to_satellite) == 1 &&
      length(azurerm_virtual_network_peering.satellite_to_primary) == 1 &&
      length(azurerm_private_dns_zone_virtual_network_link.satellite) == 4
    )
    error_message = "runner_location = southcentralus must create exactly one of each satellite resource, both peerings and four DNS links."
  }

  assert {
    condition = (
      azurerm_virtual_network.satellite[0].name == "vnet-asre-runner-scus" &&
      azurerm_virtual_network.satellite[0].location == "southcentralus" &&
      join(",", azurerm_virtual_network.satellite[0].address_space) == "10.43.0.0/24" &&
      azurerm_subnet.satellite_runner[0].name == "snet-runner" &&
      join(",", azurerm_subnet.satellite_runner[0].address_prefixes) == "10.43.0.0/26" &&
      azurerm_subnet.satellite_runner[0].default_outbound_access_enabled == false &&
      azurerm_subnet.satellite_runner[0].delegation[0].service_delegation[0].name == "Microsoft.App/environments" &&
      azurerm_network_security_group.satellite_runner[0].name == "nsg-asre-runner-scus" &&
      azurerm_public_ip.satellite_egress[0].name == "pip-asre-egress-scus" &&
      azurerm_public_ip.satellite_egress[0].location == "southcentralus" &&
      azurerm_public_ip.satellite_egress[0].allocation_method == "Static" &&
      azurerm_public_ip.satellite_egress[0].sku == "Standard" &&
      azurerm_nat_gateway.satellite[0].name == "nat-asre-scus" &&
      azurerm_nat_gateway.satellite[0].location == "southcentralus" &&
      azurerm_nat_gateway.satellite[0].sku_name == "Standard"
    )
    error_message = "The satellite network must use the scus names, the 10.43.0.0/24 and 10.43.0.0/26 ranges, a delegated runner subnet without default outbound access, an NSG without rules, and a Standard static NAT egress."
  }

  assert {
    condition = (
      azurerm_virtual_network_peering.primary_to_satellite[0].name == "peer-asre-cus-to-scus" &&
      azurerm_virtual_network_peering.primary_to_satellite[0].virtual_network_name == "vnet-asre-cus" &&
      azurerm_virtual_network_peering.satellite_to_primary[0].name == "peer-asre-scus-to-cus" &&
      azurerm_virtual_network_peering.satellite_to_primary[0].virtual_network_name == "vnet-asre-runner-scus" &&
      alltrue([
        for peering in concat(azurerm_virtual_network_peering.primary_to_satellite, azurerm_virtual_network_peering.satellite_to_primary) :
        peering.allow_virtual_network_access == true && peering.allow_forwarded_traffic == false && peering.allow_gateway_transit == false && peering.use_remote_gateways == false
      ])
    )
    error_message = "Both peerings must exist with virtual network access only: no forwarded traffic, no gateway transit, no remote gateways."
  }

  assert {
    condition = (
      join(",", sort(keys(azurerm_private_dns_zone_virtual_network_link.satellite))) == join(",", sort(keys(azurerm_private_dns_zone_virtual_network_link.service))) &&
      alltrue([for link in azurerm_private_dns_zone_virtual_network_link.satellite : link.name == "asre-runner" && link.registration_enabled == false])
    )
    error_message = "The satellite VNet must be linked, without registration, to every private zone the primary VNet is linked to."
  }

  assert {
    condition = (
      azurerm_container_app_environment.operations[0].name == "cae-asre-operations-cus" &&
      azurerm_container_app_environment.operations[0].location == "southcentralus" &&
      endswith(azurerm_container_app_environment.operations[0].infrastructure_resource_group_name, "_southcentralus") &&
      azurerm_container_app_environment.operations[0].infrastructure_resource_group_name == "ME_cae-asre-operations-cus_rg-asre-control-cus_southcentralus" &&
      azurerm_container_app_environment.operations[0].infrastructure_subnet_id == azurerm_subnet.satellite_runner[0].id
    )
    error_message = "The environment keeps its name but must run in southcentralus on the satellite runner subnet with the matching ME_ resource group name."
  }

  assert {
    condition = (
      join(",", sort(keys(azurerm_container_app_job.operations))) == "bootstrap,collector,worker" &&
      alltrue([for key, job in azurerm_container_app_job.operations : job.location == "southcentralus" && job.name == "job-asre-${key}-cus"])
    )
    error_message = "Every runtime job keeps its name and must run in southcentralus."
  }

  assert {
    condition = (
      azurerm_network_security_rule.runner_ssh.source_address_prefix == "10.43.0.0/26" &&
      join(",", sort(azurerm_network_security_rule.workload_probe.source_address_prefixes)) == "10.42.3.0/27,10.43.0.0/26"
    )
    error_message = "In satellite mode the workloads NSG rules must name only the satellite runner subnet (and the agent subnet for the probe)."
  }

  assert {
    condition = (
      azurerm_virtual_network.demo.location == "centralus" &&
      azurerm_log_analytics_workspace.demo.location == "centralus" &&
      azurerm_user_assigned_identity.bootstrap[0].location == "centralus" &&
      azurerm_subnet.demo["runner"].name == "snet-runner" &&
      output.context.location == "centralus"
    )
    error_message = "Only the environment and its jobs may move: the primary VNet, the workspace, the identities and the context location stay in centralus."
  }
}

run "westus2_uses_its_own_region_code" {
  command = plan

  variables {
    runner_location = "westus2"
  }

  assert {
    condition = (
      azurerm_virtual_network.satellite[0].name == "vnet-asre-runner-wus2" &&
      azurerm_virtual_network_peering.primary_to_satellite[0].name == "peer-asre-cus-to-wus2" &&
      azurerm_virtual_network_peering.satellite_to_primary[0].name == "peer-asre-wus2-to-cus" &&
      azurerm_container_app_environment.operations[0].location == "westus2"
    )
    error_message = "westus2 must use the wus2 region code in every satellite name."
  }
}

run "centralus_runner_location_is_the_primary_network" {
  command = plan

  variables {
    runner_location = "centralus"
  }

  assert {
    condition     = local.runner_satellite == false && length(azurerm_virtual_network.satellite) == 0 && azurerm_container_app_environment.operations[0].location == "centralus"
    error_message = "runner_location = centralus must behave exactly like the default."
  }
}

run "runner_location_is_ignored_without_the_runtime" {
  command = plan

  variables {
    deploy_runtime       = false
    runtime_image_digest = ""
    runner_location      = "southcentralus"
  }

  assert {
    condition     = local.runner_satellite == false && length(azurerm_virtual_network.satellite) == 0 && length(azurerm_container_app_environment.operations) == 0
    error_message = "runner_location is effective only when deploy_runtime is true."
  }
}

run "unlisted_runner_location_is_rejected" {
  command = plan

  variables {
    runner_location = "eastus"
  }

  expect_failures = [var.runner_location]
}
