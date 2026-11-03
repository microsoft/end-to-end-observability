import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra"
GUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def read(path):
    return path.read_text(encoding="utf-8")


def string_leaves(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from string_leaves(item)
    elif isinstance(value, list):
        for item in value:
            yield from string_leaves(item)


def infra_sources():
    """Every text source of the root module, skipping the local provider cache."""
    for path in sorted(INFRA.rglob("*")):
        relative = path.relative_to(INFRA)
        if ".terraform" in relative.parts or not path.is_file():
            continue
        if path.suffix in {".tf", ".hcl", ".example", ".tfvars", ".json", ".md"}:
            yield path


class TerraformContextTests(unittest.TestCase):
    def test_offline_terraform_test_asserts_every_synthetic_context_value(self):
        fixture = json.loads(read(ROOT / "tests" / "fixtures" / "context.synthetic.json"))
        text = read(INFRA / "tests" / "context.tftest.hcl")
        missing = sorted({leaf for leaf in string_leaves(fixture) if leaf not in text})
        self.assertEqual(missing, [])
        self.assertIn('mock_provider "azurerm" {}', text)
        self.assertIn('mock_provider "azapi" {}', text)
        self.assertNotIn("command = apply", text)
        for rejected in (
            "var.subscription_id",
            "var.tenant_id",
            "var.operator_object_id",
            "var.servicenow",
            "var.dashboard",
        ):
            self.assertIn(f"expect_failures = [{rejected}]", text)

    def test_every_job_and_the_dashboard_receive_the_same_static_context(self):
        runtime = read(INFRA / "runtime.tf")
        # One entry for the bootstrap job and one for every other job.
        self.assertEqual(len(re.findall(r"ASRE_CONTEXT\s*=\s*jsonencode\(local\.context_core\)", runtime)), 2)
        dashboard = read(INFRA / "dashboard.tf")
        self.assertRegex(dashboard, r'name\s*=\s*"ASRE_CONTEXT",\s*value\s*=\s*jsonencode\(local\.context_core\)')

    def test_static_context_cannot_depend_on_the_agent_resource(self):
        main = read(INFRA / "main.tf")
        core = re.search(r"\n  context_core = \{\n(.*?)\n  \}\n\}", main, re.S)
        self.assertIsNotNone(core)
        for forbidden in ("azapi_resource.agent", "operator_object_id", "agentEndpoint"):
            self.assertNotIn(forbidden, core.group(1))
        outputs = read(INFRA / "outputs.tf")
        document = outputs.split('output "context"', 1)[1].split("\n}\n", 1)[0]
        self.assertIn("local.context_core", document)
        self.assertIn("var.operator_object_id", document)
        self.assertIn("azapi_resource.agent[0].output.properties.agentEndpoint", document)

    def test_replaced_resources_files_and_variables_are_gone(self):
        self.assertFalse((INFRA / "native-alert-imports.tf").exists())
        self.assertFalse((INFRA / "environments" / "demo.tfvars").exists())
        gone = (
            "adopt_existing_native_alerts",
            "agent_delivery_administrator",
            "feedback_job_refresh",
            "agent_administrator_object_id",
        )
        for path in infra_sources():
            text = read(path)
            for name in gone:
                self.assertNotIn(name, text, f"{name} found in {path.relative_to(ROOT)}")

    def test_root_module_holds_no_guid_literal(self):
        for path in sorted(INFRA.glob("*.tf")):
            self.assertIsNone(re.search(GUID, read(path)), path.name)

    def test_subscription_and_tenant_have_no_default(self):
        variables = read(INFRA / "variables.tf")
        for name in ("subscription_id", "tenant_id"):
            block = variables.split(f'variable "{name}"', 1)[1].split("\nvariable ", 1)[0]
            self.assertNotRegex(block, r"\n\s*default\s*=")
            self.assertIn("validation {", block)

    def test_backend_example_has_exactly_the_documented_keys(self):
        backend = read(INFRA / "backend.tf")
        self.assertRegex(backend, r'backend\s+"azurerm"\s*\{\s*\}')
        lines = [
            line.strip()
            for line in read(INFRA / "environments" / "backend.hcl.example").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        pairs = {}
        for line in lines:
            key, separator, value = line.partition("=")
            self.assertEqual(separator, "=", line)
            pairs[key.strip()] = value.strip()
        self.assertEqual(
            sorted(pairs),
            sorted(
                [
                    "resource_group_name",
                    "storage_account_name",
                    "container_name",
                    "key",
                    "use_azuread_auth",
                    "subscription_id",
                    "tenant_id",
                ]
            ),
        )
        self.assertEqual(pairs["key"], '"autonomous-sre-ops/demo.tfstate"')
        self.assertEqual(pairs["use_azuread_auth"], "true")
        for name in (
            "resource_group_name",
            "storage_account_name",
            "container_name",
            "subscription_id",
            "tenant_id",
        ):
            self.assertRegex(pairs[name], r'^"<[a-z-]+>"$', name)

    def test_example_values_carry_no_identity_and_keep_the_safe_first_apply(self):
        values = read(INFRA / "environments" / "demo.tfvars.example")
        assignments = re.findall(r"(?m)^([a-z_]+)\s*=", values)
        for supplied_by_script in ("subscription_id", "tenant_id", "admin_ssh_public_key", "runner_ssh_public_key"):
            self.assertNotIn(supplied_by_script, assignments)
        self.assertIsNone(re.search(GUID, values))
        self.assertRegex(values, r"(?m)^deploy_managed_agent = false$")
        self.assertRegex(values, r"(?m)^backup_protection_enabled = false$")
        self.assertRegex(values, r"(?m)^operator_object_id = null$")
        self.assertRegex(values, r'(?m)^dashboard = \{ phase = "disabled" \}$')


if __name__ == "__main__":
    unittest.main()
