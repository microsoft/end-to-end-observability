import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class AgentConfigurationTests(unittest.TestCase):
    def test_agent_uses_dedicated_private_egress_without_bypass(self):
        source = (ROOT / "infra" / "agent.tf").read_text(encoding="utf-8")
        normalized = " ".join(source.split())
        for setting in (
            'subnetResourceId = azurerm_subnet.demo["agent"].id',
            'mode                           = "AzureVNet"',
            "allowHttpMcpServerNetworkAccess = false",
            "usePrivateDnsResolution = true",
            'mode        = "Review"',
            'accessLevel = "Low"',
        ):
            self.assertIn(" ".join(setting.split()), normalized)
        self.assertNotIn('role_definition_name = "Contributor"', source)
        self.assertNotIn("agentSpaceId", source)
        self.assertNotIn("Microsoft.App/agentSpaces", source)

    def test_model_matches_published_configuration_and_has_no_hardcoded_administrator(self):
        values = (ROOT / "infra" / "environments" / "demo.tfvars").read_text(encoding="utf-8")
        self.assertIn('name     = "Automatic"', values)
        self.assertIn('provider = "MicrosoftFoundry"', values)
        self.assertNotIn("agent_administrator_object_id", values)

    def test_delivery_administrator_is_only_the_actual_apply_identity_at_the_agent(self):
        source = (ROOT / "infra" / "agent.tf").read_text(encoding="utf-8")
        role = source.split('resource "azurerm_role_assignment" "agent_delivery_administrator"', 1)[1]
        self.assertIn("var.deploy_managed_agent && var.deploy_runtime", role)
        self.assertIn("scope                = azapi_resource.agent[0].id", role)
        self.assertIn('principal_id         = "00000051-000a-4000-a000-000000000051"', role)
        self.assertIn('principal_type       = "ServicePrincipal"', role)
        self.assertIn('role_definition_name = "SRE Agent Administrator"', role)
        self.assertNotIn('azurerm_resource_group', role)


if __name__ == "__main__":
    unittest.main()
