import re
import unittest
from pathlib import Path

from operations import agent_case as ac

ROOT = Path(__file__).resolve().parents[1]


class RuntimeConfigurationTests(unittest.TestCase):
    def test_complete_terraform_patch_alert_query_matches_the_fixed_profile(self):
        monitoring = (ROOT / "infra" / "monitoring.tf").read_text()
        heredocs = re.findall(
            r'(?ms)^    patch-installation = \{\n\s+severity\s*=\s*1\n'
            r'\s+query\s*=\s*<<-KQL\n(.*?)^      KQL\n    \}',
            monitoring,
        )
        self.assertEqual(len(heredocs), 1)
        query = heredocs[0]
        vm = '${azurerm_linux_virtual_machine.target["patch"].id}'
        self.assertEqual(re.findall(r"\$\{[^}]*\}", query), [vm, vm])
        rendered = query.replace(vm, ac.PATCH_RESOURCE)
        self.assertNotRegex(rendered, r"\$\{|%\{")
        # Match the actual native-rule criteria expression, not generic split().
        self.assertIn(
            'query                   = contains(local.native_created_rules, each.key) ? '
            'join(" ", [for line in split("\\n", trimspace(each.value.query)) : trimspace(line)]) '
            ': each.value.query',
            monitoring,
        )
        flattened = " ".join(line.strip() for line in rendered.strip().split("\n"))
        self.assertEqual(flattened, ac.PATCH_PROFILE.query)
        self.assertIn('for_each = each.key == "patch-installation" ? [true] : []', monitoring)
        self.assertRegex(monitoring, r'name\s*=\s*"installation_id"\s+operator\s*=\s*"Include"\s+values\s*=\s*\["\*"\]')
        self.assertNotIn('arg("")', flattened)
        self.assertIn("TimeGenerated=provider_time", flattened)

    def test_runtime_release_does_not_reset_qualified_guest_or_alert_settings(self):
        compute = (ROOT / "infra" / "compute.tf").read_text()
        self.assertRegex(compute, r"ignore_changes\s*=\s*\[custom_data\]")
        self.assertRegex(compute, r'patch_mode\s*=\s*"AutomaticByPlatform"')
        self.assertRegex(compute, r"bypass_platform_safety_checks_on_user_schedule_enabled\s*=\s*true")
        monitoring = (ROOT / "infra" / "monitoring.tf").read_text()
        self.assertIn("contains(var.enabled_native_alert_rules, each.key)", monitoring)
        self.assertIn("lower(azurerm_log_analytics_workspace.demo.id)", monitoring)
        imports = ROOT / "infra" / "native-alert-imports.tf"
        self.assertFalse(imports.exists())
        self.assertNotIn("import {", monitoring)
        self.assertNotIn("adopt_existing_native_alerts", monitoring)

    def test_correlated_feedback_is_opt_in_and_has_no_approval_or_configuration_role(self):
        variables = (ROOT / "infra" / "variables.tf").read_text()
        feedback = variables.split('variable "enable_servicenow_feedback"', 1)[1]
        self.assertRegex(feedback, r"default\s*=\s*false")
        self.assertIn("var.deploy_runtime && var.deploy_managed_agent", feedback)
        runtime = (ROOT / "infra" / "runtime.tf").read_text()
        self.assertIn('feedback = { mode = "feedback", cron = "* * * * *" }', runtime)
        self.assertIn('ASRE_SERVICENOW_FEEDBACK_ENABLED = "true"', runtime)
        role = (ROOT / "infra" / "feedback.tf").read_text()
        self.assertIn('"Microsoft.App/agents/threads/read"', role)
        self.assertIn('"Microsoft.App/agents/threads/write"', role)
        self.assertIn('azurerm_user_assigned_identity.demo["runner"].principal_id', role)
        self.assertRegex(role, r"scope\s*=\s*azapi_resource\.agent\[0\]\.id")
        for denied in ("/approve/", "/delete", 'agents/write', 'demo["agent"]', "*"):
            self.assertNotIn(denied, role)
        self.assertNotIn("feedback_job_refresh", role)
        self.assertNotIn('azurerm_container_app_job.operations["feedback"]', role)
        self.assertNotRegex(role, r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

    def test_review_jobs_are_private_digest_pinned_and_bootstrap_is_separate(self):
        source = (ROOT / "infra" / "runtime.tf").read_text()
        self.assertRegex(source, r"internal_load_balancer_enabled\s*=\s*true")
        self.assertIn("infrastructure_subnet_id           = local.runner_subnet_id", source)
        self.assertNotIn('azurerm_subnet.demo["runner"]', source)
        self.assertIn('/asre-operations@${var.runtime_image_digest}', source)
        self.assertIn('ASRE_EXECUTION_MODE      = "Review"', source)
        self.assertIn('each.key == "bootstrap" ? [azurerm_user_assigned_identity.bootstrap[0].id]', source)
        self.assertIn('each.key == "bootstrap" ? [true] : []', source)
        self.assertNotIn('role_definition_name = "Contributor"', source)
        self.assertNotIn("password_secret_name", source)
        self.assertNotIn("secret {", source)
        self.assertNotIn("latest", source)

    def test_runtime_refresh_preserves_the_existing_platform_group_and_profile(self):
        source = (ROOT / "infra" / "runtime.tf").read_text()
        environment = source.split('resource "azapi_resource" "agent_requests"', 1)[0]
        self.assertIn(
            '"ME_cae-asre-operations-cus_${azurerm_resource_group.demo["control"].name}_${local.runner_location}"',
            environment,
        )
        self.assertRegex(environment, r"minimum_count\s*=\s*0")
        self.assertRegex(environment, r"maximum_count\s*=\s*0")
        self.assertNotIn("ignore_changes", environment)

    def test_storage_preserves_only_the_existing_defender_scanner_instance_rule(self):
        source = (ROOT / "infra" / "data.tf").read_text().split(
            'resource "azapi_resource" "evidence_container"', 1
        )[0]
        self.assertIn(
            '"/subscriptions/${var.subscription_id}/providers/Microsoft.Security/datascanners/StorageDataScanner"',
            source,
        )
        self.assertRegex(source, r"endpoint_tenant_id\s*=\s*var.tenant_id")
        self.assertRegex(source, r"public_network_access_enabled\s*=\s*false")
        self.assertRegex(source, r'default_action\s*=\s*"Deny"')
        self.assertRegex(source, r'bypass\s*=\s*\["None"\]')
        self.assertEqual(source.count("private_link_access {"), 1)
        self.assertNotIn("ignore_changes", source)

    def test_credential_bootstrap_cannot_delete_or_purge_secrets(self):
        source = (ROOT / "infra" / "runtime.tf").read_text()
        definition = source.split('resource "azurerm_role_definition" "credential_bootstrap"', 1)[1].split(
            'resource "azurerm_role_assignment" "credential_bootstrap"', 1
        )[0]
        self.assertIn("Microsoft.KeyVault/vaults/secrets/getSecret/action", definition)
        self.assertIn("Microsoft.KeyVault/vaults/secrets/setSecret/action", definition)
        self.assertNotIn("/secrets/*", definition)
        self.assertNotIn("delete/action", definition)
        self.assertNotIn("purge/action", definition)


def _block(source, header):
    """Return one top-level Terraform block, from its header line to the closing brace at column 0."""
    start = source.index(header)
    return source[start : source.index("\n}\n", start) + 3]


class SatelliteRunnerRegionTests(unittest.TestCase):
    """Static pins for the optional runner-only satellite network (var.runner_location)."""

    @classmethod
    def setUpClass(cls):
        infra = ROOT / "infra"
        cls.variables = (infra / "variables.tf").read_text()
        cls.main = (infra / "main.tf").read_text()
        cls.network = (infra / "network.tf").read_text()
        cls.data = (infra / "data.tf").read_text()
        cls.runtime = (infra / "runtime.tf").read_text()

    def test_variable_defaults_to_null_and_allows_only_four_regions(self):
        variable = _block(self.variables, 'variable "runner_location"')
        self.assertIn("type        = string", variable)
        self.assertRegex(variable, r"default\s*=\s*null\n")
        self.assertIn(
            'condition     = var.runner_location == null ? true : contains(["centralus", "westcentralus", '
            '"southcentralus", "westus2"], var.runner_location)',
            variable,
        )
        self.assertIn("only when deploy_runtime is true", variable)
        self.assertIn("before the first runtime apply", variable)
        # The primary region and the context location are not relaxed.
        location = _block(self.variables, 'variable "location"')
        self.assertIn('condition     = var.location == "centralus"', location)
        self.assertIn("location        = var.location", self.main.split("context_core = {", 1)[1])

    def test_locals_gate_the_satellite_on_the_runtime_and_a_different_region(self):
        self.assertIn(
            "runner_satellite    = var.deploy_runtime && var.runner_location != null && var.runner_location != var.location",
            self.main,
        )
        self.assertIn("runner_location     = local.runner_satellite ? var.runner_location : var.location", self.main)
        self.assertIn(
            'runner_region_codes = { westcentralus = "wcus", southcentralus = "scus", westus2 = "wus2" }', self.main
        )
        self.assertIn('runner_cidr      = local.runner_satellite ? local.satellite_network.runner : local.subnets.runner', self.main)
        self.assertIn(
            "runner_subnet_id = local.runner_satellite ? azurerm_subnet.satellite_runner[0].id "
            ': azurerm_subnet.demo["runner"].id',
            self.main,
        )
        # 10.43.0.0/24 sits outside the primary 10.42.0.0/16 so the two VNets can peer.
        self.assertRegex(self.main, r'vnet\s*=\s*"10\.43\.0\.0/24"\s+runner\s*=\s*"10\.43\.0\.0/26"')
        self.assertIn('address_space       = ["10.42.0.0/16"]', self.network)

    def test_every_satellite_resource_is_gated_and_lives_in_the_control_group(self):
        names = [
            "azurerm_virtual_network.satellite",
            "azurerm_subnet.satellite_runner",
            "azurerm_network_security_group.satellite_runner",
            "azurerm_subnet_network_security_group_association.satellite_runner",
            "azurerm_public_ip.satellite_egress",
            "azurerm_nat_gateway.satellite",
            "azurerm_nat_gateway_public_ip_association.satellite",
            "azurerm_subnet_nat_gateway_association.satellite_runner",
            "azurerm_virtual_network_peering.primary_to_satellite",
            "azurerm_virtual_network_peering.satellite_to_primary",
        ]
        for name in names:
            kind, label = name.split(".")
            block = _block(self.network, f'resource "{kind}" "{label}"')
            self.assertRegex(block, r"\n  count\s*=\s*local\.runner_satellite \? 1 : 0\n", name)
            if "resource_group_name" in block:
                self.assertRegex(block, r'resource_group_name\s*=\s*azurerm_resource_group\.demo\["control"\]\.name', name)
        for kind, label in (
            ("azurerm_virtual_network", "satellite"),
            ("azurerm_network_security_group", "satellite_runner"),
            ("azurerm_public_ip", "satellite_egress"),
            ("azurerm_nat_gateway", "satellite"),
        ):
            block = _block(self.network, f'resource "{kind}" "{label}"')
            self.assertRegex(block, r"location\s*=\s*local\.runner_location", label)
            self.assertRegex(block, r"tags\s*=\s*local\.tags", label)
        link = _block(self.data, 'resource "azurerm_private_dns_zone_virtual_network_link" "satellite"')
        self.assertRegex(link, r"for_each\s*=\s*local\.runner_satellite \? local\.private_services : \{\}")

    def test_satellite_names_use_the_region_code_and_the_primary_names_are_unchanged(self):
        for expected in (
            'name                = "vnet-asre-runner-${local.runner_region_code}"',
            'name                = "nsg-asre-runner-${local.runner_region_code}"',
            'name                = "pip-asre-egress-${local.runner_region_code}"',
            'name                    = "nat-asre-${local.runner_region_code}"',
            'name                         = "peer-asre-cus-to-${local.runner_region_code}"',
            'name                         = "peer-asre-${local.runner_region_code}-to-cus"',
        ):
            self.assertIn(expected, self.network)
        self.assertIn('runner_region_code = local.runner_satellite ? local.runner_region_codes[local.runner_location] : "cus"', self.main)
        for primary in ('"vnet-asre-cus"', '"pip-asre-egress-cus"', '"nsg-asre-${each.key}-cus"'):
            self.assertIn(primary, self.network)
        self.assertEqual(self.network.count('resource "azurerm_virtual_network" '), 2)

    def test_peering_is_a_pair_between_the_two_vnets_only_without_transit(self):
        forward = _block(self.network, 'resource "azurerm_virtual_network_peering" "primary_to_satellite"')
        reverse = _block(self.network, 'resource "azurerm_virtual_network_peering" "satellite_to_primary"')
        self.assertRegex(forward, r"virtual_network_name\s*=\s*azurerm_virtual_network\.demo\.name")
        self.assertRegex(forward, r"remote_virtual_network_id\s*=\s*azurerm_virtual_network\.satellite\[0\]\.id")
        self.assertRegex(reverse, r"virtual_network_name\s*=\s*azurerm_virtual_network\.satellite\[0\]\.name")
        self.assertRegex(reverse, r"remote_virtual_network_id\s*=\s*azurerm_virtual_network\.demo\.id")
        for peering in (forward, reverse):
            self.assertRegex(peering, r"allow_virtual_network_access\s*=\s*true")
            self.assertRegex(peering, r"allow_forwarded_traffic\s*=\s*false")
            self.assertRegex(peering, r"allow_gateway_transit\s*=\s*false")
            self.assertRegex(peering, r"use_remote_gateways\s*=\s*false")
        self.assertRegex(reverse, r"depends_on\s*=\s*\[azurerm_virtual_network_peering\.primary_to_satellite\]")
        self.assertNotIn("depends_on", forward)
        self.assertEqual(self.network.count('resource "azurerm_virtual_network_peering" '), 2)

    def test_satellite_subnet_is_delegated_without_default_outbound_and_has_nat_and_an_empty_nsg(self):
        subnet = _block(self.network, 'resource "azurerm_subnet" "satellite_runner"')
        self.assertRegex(subnet, r'name\s*=\s*"snet-runner"')
        self.assertRegex(subnet, r"address_prefixes\s*=\s*\[local\.satellite_network\.runner\]")
        self.assertRegex(subnet, r"virtual_network_name\s*=\s*azurerm_virtual_network\.satellite\[0\]\.name")
        self.assertRegex(subnet, r"default_outbound_access_enabled\s*=\s*false")
        self.assertRegex(subnet, r'private_endpoint_network_policies\s*=\s*"Disabled"')
        self.assertRegex(subnet, r'name\s*=\s*"Microsoft\.App/environments"')
        self.assertRegex(subnet, r'actions\s*=\s*\["Microsoft\.Network/virtualNetworks/subnets/join/action"\]')
        nsg = _block(self.network, 'resource "azurerm_network_security_group" "satellite_runner"')
        self.assertNotIn("security_rule", nsg)
        self.assertNotRegex(self.network, r"network_security_group_name\s*=\s*azurerm_network_security_group\.satellite")
        association = _block(self.network, 'resource "azurerm_subnet_network_security_group_association" "satellite_runner"')
        self.assertRegex(association, r"subnet_id\s*=\s*azurerm_subnet\.satellite_runner\[0\]\.id")
        self.assertRegex(association, r"network_security_group_id\s*=\s*azurerm_network_security_group\.satellite_runner\[0\]\.id")
        nat = _block(self.network, 'resource "azurerm_nat_gateway" "satellite"')
        self.assertRegex(nat, r'sku_name\s*=\s*"Standard"')
        self.assertRegex(nat, r"idle_timeout_in_minutes\s*=\s*10")
        ip = _block(self.network, 'resource "azurerm_public_ip" "satellite_egress"')
        self.assertRegex(ip, r'allocation_method\s*=\s*"Static"')
        self.assertRegex(ip, r'sku\s*=\s*"Standard"')
        self.assertIn('ignore_changes = [ip_tags["FirstPartyUsage"]]', ip)
        ip_association = _block(self.network, 'resource "azurerm_nat_gateway_public_ip_association" "satellite"')
        self.assertRegex(ip_association, r"nat_gateway_id\s*=\s*azurerm_nat_gateway\.satellite\[0\]\.id")
        self.assertRegex(ip_association, r"public_ip_address_id\s*=\s*azurerm_public_ip\.satellite_egress\[0\]\.id")
        subnet_nat = _block(self.network, 'resource "azurerm_subnet_nat_gateway_association" "satellite_runner"')
        self.assertRegex(subnet_nat, r"subnet_id\s*=\s*azurerm_subnet\.satellite_runner\[0\]\.id")
        self.assertRegex(subnet_nat, r"nat_gateway_id\s*=\s*azurerm_nat_gateway\.satellite\[0\]\.id")
        # The primary runner subnet, its NSG and its NAT association stay (the wrapper refuses deletions).
        self.assertIn('for_each       = toset(["workloads", "runner", "agent"])', self.network)
        self.assertRegex(self.network, r'resource "azurerm_subnet" "demo" \{\n\s*for_each\s*=\s*local\.subnets\n')

    def test_all_four_private_zones_are_linked_to_the_satellite_vnet(self):
        for zone in (
            "privatelink.blob.core.windows.net",
            "privatelink.table.core.windows.net",
            "privatelink.queue.core.windows.net",
            "privatelink.vaultcore.azure.net",
        ):
            self.assertEqual(self.data.count(f'zone = "{zone}"'), 1, zone)
        link = _block(self.data, 'resource "azurerm_private_dns_zone_virtual_network_link" "satellite"')
        self.assertRegex(link, r'name\s*=\s*"asre-runner"')
        self.assertRegex(link, r"private_dns_zone_name\s*=\s*azurerm_private_dns_zone\.service\[each\.key\]\.name")
        self.assertRegex(link, r"virtual_network_id\s*=\s*azurerm_virtual_network\.satellite\[0\]\.id")
        self.assertRegex(link, r"registration_enabled\s*=\s*false")
        self.assertRegex(link, r"tags\s*=\s*local\.tags")
        primary = _block(self.data, 'resource "azurerm_private_dns_zone_virtual_network_link" "service"')
        self.assertRegex(primary, r'name\s*=\s*"asre"\n')
        self.assertRegex(primary, r"virtual_network_id\s*=\s*azurerm_virtual_network\.demo\.id")

    def test_workloads_nsg_allows_only_the_active_runner_cidr(self):
        ssh = _block(self.network, 'resource "azurerm_network_security_rule" "runner_ssh"')
        self.assertRegex(ssh, r"source_address_prefix\s*=\s*local\.runner_cidr\n")
        probe = _block(self.network, 'resource "azurerm_network_security_rule" "workload_probe"')
        self.assertRegex(probe, r"source_address_prefixes\s*=\s*\[local\.runner_cidr, local\.subnets\.agent\]")
        self.assertNotIn("local.subnets.runner", self.network)

    def test_environment_and_jobs_follow_the_runner_region_and_keep_their_names(self):
        environment = _block(self.runtime, 'resource "azurerm_container_app_environment" "operations"')
        self.assertRegex(environment, r'name\s*=\s*"cae-asre-operations-cus"')
        self.assertRegex(environment, r"location\s*=\s*local\.runner_location\n")
        self.assertRegex(environment, r"infrastructure_subnet_id\s*=\s*local\.runner_subnet_id\n")
        self.assertRegex(environment, r"log_analytics_workspace_id\s*=\s*azurerm_log_analytics_workspace\.demo\.id")
        for dependency in (
            "azurerm_subnet_nat_gateway_association.satellite_runner",
            "azurerm_subnet_network_security_group_association.satellite_runner",
            "azurerm_virtual_network_peering.primary_to_satellite",
            "azurerm_virtual_network_peering.satellite_to_primary",
            "azurerm_private_dns_zone_virtual_network_link.satellite",
        ):
            self.assertIn(f"    {dependency},\n", environment.split("depends_on = [", 1)[1])
        job = _block(self.runtime, 'resource "azurerm_container_app_job" "operations"')
        self.assertRegex(job, r'name\s*=\s*"job-asre-\$\{each\.key\}-cus"')
        self.assertRegex(job, r"location\s*=\s*local\.runner_location\n")
        self.assertNotRegex(job, r"location\s*=\s*var\.location")
        # Identities and the registry stay in the primary region, so the job identities never move.
        self.assertRegex(_block(self.runtime, 'resource "azurerm_user_assigned_identity" "bootstrap"'), r"location\s*=\s*var\.location")
