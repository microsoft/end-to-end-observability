"""Offline checks of the root opt-in and its closed release trust boundary."""
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = yaml.safe_load((ROOT / "azure-pipelines.yml").read_text(encoding="utf-8"))
RUNNER = (ROOT / "scripts/Invoke-IncidentRelease.ps1").read_text(encoding="utf-8")


class IncidentReleaseTests(unittest.TestCase):
    def test_root_preserves_generic_defaults_and_configuration_resources_are_conditional(self):
        self.assertEqual(PIPELINE["extends"]["template"], ".pipelines/cd-template.yaml@templates")
        self.assertEqual(PIPELINE["lockBehavior"], "sequential")
        opt_in = "${{ if eq(parameters.configuration_only, true) }}"
        self.assertNotIn("pool", PIPELINE)
        self.assertEqual(PIPELINE["variables"], [{opt_in: [{"group": "alz-mgmt"}]}])
        self.assertEqual(PIPELINE["trigger"]["branches"]["include"], ["main"])
        self.assertNotIn("pr", PIPELINE)
        parameters = {p["name"]: p for p in PIPELINE["parameters"]}
        self.assertEqual(parameters["terraform_action"]["default"], "apply")
        self.assertEqual(parameters["terraform_action"]["values"], ["apply", "destroy", "plan"])
        for name in ("configuration_only", "publish_disabled_configuration"):
            self.assertIs(parameters[name]["default"], False)
        self.assertEqual(parameters["validated_configuration_run_id"]["default"], "0")

    def test_configuration_profile_only_selected_by_explicit_opt_in(self):
        parent = PIPELINE["extends"]["parameters"]
        self.assertEqual(parent["${{ if eq(parameters.configuration_only, true) }}"],
                         {"delivery_profile": "asre-configuration"})
        self.assertNotIn("${{ else }}", parent)
        self.assertNotIn("delivery_profile", parent)  # Retain the parent's standard default.
        for name in ("configuration_only", "publish_disabled_configuration", "validated_configuration_run_id"):
            self.assertEqual(parent[name], "${{ parameters." + name + " }}")
        self.assertNotIn("stages", PIPELINE)
        self.assertNotIn("jobs", PIPELINE)
        self.assertNotIn("steps", PIPELINE)

    def test_template_identity_and_existing_arguments_stay_bound(self):
        self.assertEqual(PIPELINE["resources"]["repositories"], [{
            "repository": "templates", "type": "git",
            "name": "alz-landingzone/alz-mgmt-templates",
        }])
        parent = PIPELINE["extends"]["parameters"]
        self.assertEqual(parent["terraform_cli_version"],
                         "${{ coalesce(parameters.terraform_cli_version, 'latest') }}")
        self.assertEqual(parent["root_module_folder_relative_path"], ".")
        for name in ("terraform_action", "configuration_only", "publish_disabled_configuration",
                     "validated_configuration_run_id"):
            self.assertEqual(parent[name], "${{ parameters." + name + " }}")

    def test_release_actions_exclude_activation_and_infrastructure(self):
        self.assertIn("[ValidateSet('Validate', 'Verify', 'PublishDisabled')]", RUNNER)
        self.assertNotIn("[scriptblock]", RUNNER)
        self.assertNotIn("Invoke-Expression", RUNNER)
        self.assertNotRegex(RUNNER, r"(?im)^\s*&\s+(terraform|az)\b")
        self.assertEqual(len(re.findall(r"-Action PublishDisabled\b", RUNNER)), 1)
        self.assertIn("'source/scripts/Publish-IncidentWorkflow.ps1'", RUNNER)
        self.assertNotRegex(RUNNER, r"-Action (Cutover|Enable|Apply|Rollback)\b")

    def test_trusted_source_gate_precedes_any_dependency_execution(self):
        builder = RUNNER.index("$generated = &")
        for gate in ("$env:BUILD_SOURCEBRANCH -cne 'refs/heads/master'",
                     "$env:BUILD_REASON -cne 'Manual'",
                     "$env:BUILD_REPOSITORY_PROVIDER -cne 'TfsGit'",
                     "Assert-Checkout $SourceDirectory", "Assert-Checkout $TemplateDirectory",
                     "Configuration source hash mismatch", "Configuration artifact hash mismatch"):
            self.assertLess(RUNNER.index(gate), builder)
        self.assertIn("ls-files --error-unmatch", RUNNER)
        self.assertIn("diff --quiet HEAD", RUNNER)

    def test_manifest_is_distinct_closed_explicit_and_bounds_age(self):
        self.assertIn("'configuration-manifest.json'", RUNNER)
        self.assertIn("artifact_kind = 'asre-configuration/v1'", RUNNER)
        self.assertIn("publication_action = 'PublishDisabled'; runtime_accepted = $false", RUNNER)
        for gate in ("$manifest.Count -ne $keys.Count", "$manifest.files.Count -ne $expected.Count + 1",
                     "$manifest.validation_run_id -cne $env:CONFIGURATION_VALIDATION_RUN_ID",
                     "$created -lt $start", "$created -gt $finish", "$finish -gt $now",
                     "[TimeSpan]::FromHours(4)", "Unexpected configuration artifact path",
                     "ReparsePoint", "'generated/case-tool.py'"):
            self.assertIn(gate, RUNNER)

    def test_fixed_agent_endpoint_context_and_retained_oidc_connection(self):
        for value in ("00000001-000a-4000-a000-000000000001",
                      "00000002-000a-4000-9000-000000000002",
                      "00000018-000a-4000-b000-000000000018",
                      "asre-demo-cus--0a1b2c3d.4e5f6a7b.centralus.azuresre.ai"):
            self.assertIn(value, RUNNER)
        for key in ("collection_uri", "project_id", "definition_id", "repository_id",
                    "source_branch", "source_commit", "template_repository", "template_commit",
                    "subscription_id", "tenant_id", "agent_resource_id", "agent_endpoint"):
            self.assertRegex(RUNNER, rf"\b{key}\s*=")

    def test_frozen_a_b_closure_is_explicit_not_a_folder_glob(self):
        closure = RUNNER.split("$SourceFiles = @(", 1)[1].split("\n)", 1)[0]
        for path in ("config/native-incident-runtime.json",
                     "scripts/Publish-IncidentWorkflow.ps1", "scripts/ReviewAgent.Common.ps1",
                     "scripts/Initialize-ReviewAgent.ps1", "scripts/Build-CaseTool.ps1",
                     "agent/workflows/azure-monitor-review.json", "agent/tools/servicenow_case.json",
                     "agent/tools/servicenow_case.py", "operations/agent_case.py", "operations/servicenow.py",
                     "operations/patch_provider.py", "operations/patch_sensor.py",
                     "operations/worker.py", "operations/feedback.py", "infra/monitoring.tf"):
            self.assertIn("'" + path + "'", closure)
        self.assertEqual(closure.count("'operations/contracts/"), 6)
        self.assertNotIn("*", closure)
        self.assertIn("-PassThru", RUNNER)
        self.assertIn("Generated case-tool package hash mismatch.", RUNNER)

    def test_publication_requires_strict_disabled_readback_and_no_runtime_claim(self):
        publication = RUNNER.split("$result = &", 1)[1]
        self.assertIn("-Action PublishDisabled", publication)
        self.assertIn("$result.status -cne 'published-disabled'", publication)
        self.assertIn("$result.runtime_accepted -isnot [bool]", publication)
        self.assertIn("-or $result.runtime_accepted", publication)
        self.assertNotIn("-Transport", publication)


if __name__ == "__main__":
    unittest.main()
