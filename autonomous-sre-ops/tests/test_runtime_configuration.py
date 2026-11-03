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
        imports = (ROOT / "infra" / "native-alert-imports.tf").read_text()
        self.assertIn('toset(["patch-installation", "capacity-growth", "backup-job"])', imports)
        self.assertIn("var.adopt_existing_native_alerts", imports)
        self.assertIn("azurerm_monitor_scheduled_query_rules_alert_v2.guest[each.key]", imports)

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
        self.assertIn('azurerm_container_app_job.operations["feedback"].id', role)
        self.assertIn('00000041-000a-4000-8000-000000000041', role)
        self.assertIn('00000043-000a-4000-a000-000000000043', role)

    def test_feedback_delivery_is_manual_private_and_runtime_only(self):
        import yaml
        source = yaml.safe_load((ROOT / ".pipelines" / "servicenow-feedback.yml").read_text())
        self.assertEqual(source["trigger"], "none")
        self.assertEqual(source["pr"], "none")
        self.assertEqual(source["pool"]["name"], "alz-mgmt")
        self.assertEqual(source["resources"]["repositories"][0]["ref"], "refs/heads/main")
        parent = source["extends"]
        self.assertEqual(parent["template"], ".pipelines/cd-template.yaml@templates")
        self.assertEqual(parent["parameters"]["delivery_profile"], "asre-saved-plan")
        self.assertIs(parent["parameters"]["runtime_only"], True)
        parameters = {item["name"]: item for item in source["parameters"]}
        self.assertEqual(parameters["terraform_action"]["default"], "plan")
        self.assertIs(parameters["build_runtime_images"]["default"], False)
        self.assertEqual(parameters["validated_plan_run_id"]["default"], "0")

    def test_review_jobs_are_private_digest_pinned_and_bootstrap_is_separate(self):
        source = (ROOT / "infra" / "runtime.tf").read_text()
        self.assertRegex(source, r"internal_load_balancer_enabled\s*=\s*true")
        self.assertIn('azurerm_subnet.demo["runner"]', source)
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
            '"ME_cae-asre-operations-cus_${azurerm_resource_group.demo["control"].name}_${var.location}"',
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

    def test_protected_image_build_checks_source_inputs_and_locks_the_actual_digest(self):
        source = (ROOT / "scripts" / "Build-RuntimeImages.ps1").read_text()
        self.assertIn("$Manifest.source_commit -ne $Commit", source)
        self.assertIn("$Manifest.template_commit -ne $env:TEMPLATE_SOURCE_COMMIT", source)
        self.assertIn("'Dockerfile', '.dockerignore'", source)
        self.assertIn("Get-FileHash -LiteralPath $InputFile.FullName", source)
        self.assertIn("'--write-enabled', 'false', '--delete-enabled', 'false'", source)
        self.assertIn("image-provenance.json", source)
        self.assertNotIn("terraform apply", source)
        self.assertNotIn("admin-enabled", source)

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
