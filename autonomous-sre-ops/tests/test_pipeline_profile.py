"""Offline seal of the retained generic root plus its configuration opt-in."""

import hashlib
from pathlib import Path
import unittest

import yaml


PIPELINE = Path(__file__).resolve().parents[1] / "azure-pipelines.yml"
# Candidate corrected within architect turn61-62 scope, not a claim of
# fresh integrated-source approval. Root frozen before resealing these tests.
# Keep this literal independent of the file under test; never auto-recompute it.
ACCEPTED_PROFILE_SHA256 = (
    "000000000000000000000000000000000000000000000000000000000000011a"
)

# Exact remote990 root, blob 000000000000000000000000000000000000004a.
# main/apply/destroy/root '.' are approved foreign intent, not regressions.
# This unadapted fixture lacks the integrated configuration-only definition.
UNADAPTED_GENERIC_PROFILE = """\
---
trigger:
  branches:
    include:
      - main

resources:
  repositories:
    - repository: templates
      type: git
      name: alz-landingzone/alz-mgmt-templates

parameters:
  - name: terraform_action
    displayName: Terraform Action to perform
    type: string
    default: 'apply'
    values:
      - 'apply'
      - 'destroy'
  - name: terraform_cli_version
    displayName: Terraform CLI Version
    type: string
    default: 'latest'

lockBehavior: sequential

extends:
  template: .pipelines/cd-template.yaml@templates
  parameters:
    terraform_action: ${{ parameters.terraform_action }}
    root_module_folder_relative_path: .
    terraform_cli_version: ${{ coalesce(parameters.terraform_cli_version, 'latest') }}
"""


class PipelineProfileTests(unittest.TestCase):
    def assert_accepted_profile(self, content):
        # Permit Windows checkout line endings, but no semantic/content changes.
        normalized = content.replace("\r\n", "\n")
        self.assertEqual(
            hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            ACCEPTED_PROFILE_SHA256,
            "Pipeline must exactly retain the integrated configuration opt-in definition.",
        )

    def test_pipeline_retains_exact_accepted_profile(self):
        self.assert_accepted_profile(PIPELINE.read_text(encoding="utf-8"))

    def test_unadapted_generic_definition_is_rejected_for_missing_integration(self):
        with self.assertRaisesRegex(AssertionError, "integrated configuration opt-in definition"):
            self.assert_accepted_profile(UNADAPTED_GENERIC_PROFILE)

    def test_generic_definition_is_preserved_except_explicit_additions(self):
        baseline = yaml.safe_load(UNADAPTED_GENERIC_PROFILE)
        integrated = yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))
        opt_in = "${{ if eq(parameters.configuration_only, true) }}"
        # Static conditional-shape checks, NOT an Azure expression evaluator.
        # Root has no pool, exactly as remote990. Configuration deployment jobs
        # own their static pools; only the variable group remains conditional.
        self.assertNotIn("pool", integrated)
        self.assertEqual(integrated.pop("variables"), [{opt_in: [{"group": "alz-mgmt"}]}])
        additions = {"configuration_only", "publish_disabled_configuration",
                     "validated_configuration_run_id"}
        declared = integrated["parameters"]
        self.assertEqual({p["name"] for p in declared[2:]}, additions)
        integrated["parameters"] = declared[:2]
        self.assertEqual(integrated["parameters"][0]["values"].pop(), "plan")
        parent = integrated["extends"]["parameters"]
        self.assertEqual(parent.pop(opt_in), {"delivery_profile": "asre-configuration"})
        for name in additions:
            self.assertEqual(parent.pop(name), "${{ parameters." + name + " }}")
        self.assertEqual(integrated, baseline)

    def test_complete_seal_rejects_executable_root_injection(self):
        content = PIPELINE.read_text(encoding="utf-8")
        for key in ("stages", "jobs", "steps"):
            with self.subTest(key=key), self.assertRaises(AssertionError):
                self.assert_accepted_profile(content + f"\n{key}:\n  - script: echo forbidden\n")


if __name__ == "__main__":
    unittest.main()
