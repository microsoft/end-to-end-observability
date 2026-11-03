"""Offline checks of the closed release trust boundary (runner script)."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = (ROOT / "scripts/Invoke-IncidentRelease.ps1").read_text(encoding="utf-8")


class IncidentReleaseTests(unittest.TestCase):
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
