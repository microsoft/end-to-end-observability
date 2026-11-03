import copy
import json
import unittest
from pathlib import Path
from uuid import uuid4

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.schema = json.loads((ROOT / "contracts" / "servicenow-case.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(self.schema)
        self.validator = Draft202012Validator(self.schema, format_checker=FormatChecker())
        self.case = {
            "contract_version": "1.0", "episode_id": str(uuid4()), "source_event_ids": ["fixture-alert-1"],
            "resource_id": self.schema["properties"]["resource_id"]["enum"][0],
            "mode": "reactive", "signal_family": "filesystem", "detected_at": "2026-10-14T12:00:00Z",
            "summary": "Fixture-only disk utilization evidence",
            "evidence": [{"source": "AzureMonitor", "observed_at": "2026-10-14T12:00:00Z", "reference": "fixture-only", "finding": "Synthetic unit-test data, not a real incident"}],
            "diagnosis": {"finding": "Unit-test fixture", "confidence": 0.5, "gaps": ["No live evidence"]},
            "actions": [], "verification": {"status": "not_started", "recovery_owner": "none"},
        }

    def test_case_contract_and_invalid_scope(self):
        self.validator.validate(self.case)
        invalid = copy.deepcopy(self.case)
        invalid["resource_id"] = invalid["resource_id"].replace("rg-asre-linux-cus", "rg-asre-backup-cus")
        self.assertTrue(list(self.validator.iter_errors(invalid)))

    def test_verified_action_requires_real_operation_and_evidence_fields(self):
        case = copy.deepcopy(self.case)
        case["actions"] = [{"request_id": str(uuid4()), "name": "recover_workload", "status": "verified"}]
        self.assertTrue(list(self.validator.iter_errors(case)))

    def test_duplicate_events_and_invalid_timestamp_are_rejected(self):
        case = copy.deepcopy(self.case)
        case["source_event_ids"] *= 2
        case["detected_at"] = "not-a-date"
        self.assertGreaterEqual(len(list(self.validator.iter_errors(case))), 2)

    def test_plans_and_schedules_are_disabled_and_not_fake_api_payloads(self):
        for folder, name, key in (("response-plans", "plans.json", "plans"), ("scheduled-tasks", "tasks.json", "tasks")):
            config = json.loads((ROOT / "agent" / folder / name).read_text(encoding="utf-8"))
            self.assertEqual(config["artifact_kind"], "portal-configuration-checklist")
            self.assertFalse(config["deployed"])
            for entry in config[key]:
                self.assertFalse(entry["enabled"])
                self.assertLessEqual(len(entry["skills"]), 5)

    def assert_retained_pipeline(self, pipeline):
        """Check wiring only, not Azure's server-side expression expansion."""
        self.assertEqual(set(pipeline), {
            "trigger", "resources", "parameters", "variables", "lockBehavior", "extends",
        })
        self.assertEqual(pipeline["trigger"], {"branches": {"include": ["main"]}})
        self.assertEqual(pipeline["extends"]["template"], ".pipelines/cd-template.yaml@templates")
        self.assertNotIn("stages", pipeline)
        self.assertNotIn("jobs", pipeline)
        self.assertNotIn("steps", pipeline)
        declared = {entry["name"]: entry for entry in pipeline["parameters"]}
        self.assertEqual(len(pipeline["parameters"]), 5)
        self.assertEqual(set(declared), {
            "terraform_action", "terraform_cli_version", "configuration_only",
            "publish_disabled_configuration", "validated_configuration_run_id",
        })
        action = declared["terraform_action"]
        self.assertEqual(action["type"], "string")
        self.assertEqual(action["default"], "apply")
        self.assertEqual(action["values"], ["apply", "destroy", "plan"])
        self.assertEqual(declared["terraform_cli_version"]["type"], "string")
        self.assertEqual(declared["terraform_cli_version"]["default"], "latest")
        self.assertEqual(pipeline["resources"]["repositories"], [{
            "repository": "templates", "type": "git",
            "name": "alz-landingzone/alz-mgmt-templates",
        }])
        parameters = pipeline["extends"]["parameters"]
        self.assertEqual(declared["configuration_only"]["type"], "boolean")
        self.assertIs(declared["configuration_only"]["default"], False)
        opt_in = "${{ if eq(parameters.configuration_only, true) }}"
        self.assertEqual(parameters, {
            opt_in: {"delivery_profile": "asre-configuration"},
            "terraform_action": "${{ parameters.terraform_action }}",
            "root_module_folder_relative_path": ".",
            "terraform_cli_version": "${{ coalesce(parameters.terraform_cli_version, 'latest') }}",
            "configuration_only": "${{ parameters.configuration_only }}",
            "publish_disabled_configuration": "${{ parameters.publish_disabled_configuration }}",
            "validated_configuration_run_id": "${{ parameters.validated_configuration_run_id }}",
        })
        self.assertEqual(declared["publish_disabled_configuration"]["type"], "boolean")
        self.assertIs(declared["publish_disabled_configuration"]["default"], False)
        self.assertEqual(declared["validated_configuration_run_id"]["type"], "string")
        self.assertEqual(declared["validated_configuration_run_id"]["default"], "0")
        self.assertEqual(pipeline["variables"], [{opt_in: [{"group": "alz-mgmt"}]}])
        self.assertNotIn("pool", pipeline)
        self.assertEqual(pipeline["lockBehavior"], "sequential")

    def test_retained_generic_pipeline_has_isolated_configuration_opt_in(self):
        pipeline = yaml.safe_load((ROOT / "azure-pipelines.yml").read_text(encoding="utf-8"))
        self.assert_retained_pipeline(pipeline)

    def test_parent_receives_generic_action_cli_and_root_unchanged(self):
        pipeline = yaml.safe_load((ROOT / "azure-pipelines.yml").read_text(encoding="utf-8"))
        parameters = pipeline["extends"]["parameters"]
        self.assertEqual(parameters["terraform_action"], "${{ parameters.terraform_action }}")
        self.assertEqual(parameters["terraform_cli_version"],
                         "${{ coalesce(parameters.terraform_cli_version, 'latest') }}")
        self.assertEqual(parameters["root_module_folder_relative_path"], ".")
        self.assertNotIn("delivery_profile", parameters)
        self.assertNotIn("${{ else }}", parameters)

    def test_old_authority_inputs_are_not_public_and_compute_is_unchanged(self):
        pipeline = yaml.safe_load((ROOT / "azure-pipelines.yml").read_text(encoding="utf-8"))
        declared = {entry["name"]: entry for entry in pipeline["parameters"]}
        for name in ("validated_plan_run_id", "build_runtime_images", "prepare_plan_job_reads",
                     "resume_demo_targets", "enable_review_pilot", "delivery_profile"):
            self.assertNotIn(name, declared)
            self.assertNotIn(name, pipeline["extends"]["parameters"])
        compute = (ROOT / "infra" / "compute.tf").read_text(encoding="utf-8")
        self.assertEqual(compute.count('storage_account_type = "Standard_LRS"'), 2)
        self.assertNotIn('storage_account_type = "StandardSSD_LRS"', compute)

    def test_focused_wiring_mutations_are_rejected_without_relying_on_seal(self):
        pipeline = yaml.safe_load((ROOT / "azure-pipelines.yml").read_text(encoding="utf-8"))
        opt_in = "${{ if eq(parameters.configuration_only, true) }}"
        mutations = []
        inverted = copy.deepcopy(pipeline)
        inverted["extends"]["parameters"]["${{ if eq(parameters.configuration_only, false) }}"] = (
            inverted["extends"]["parameters"].pop(opt_in)
        )
        mutations.append(("inverted routing", inverted))
        forced = copy.deepcopy(pipeline)
        forced["extends"]["parameters"]["terraform_action"] = "plan"
        mutations.append(("forced plan substitution", forced))
        for name in ("configuration_only", "publish_disabled_configuration",
                     "validated_configuration_run_id"):
            dropped = copy.deepcopy(pipeline)
            del dropped["extends"]["parameters"][name]
            mutations.append(("dropped forwarding " + name, dropped))
        for key, value in (("pool", {"name": "alz-mgmt"}),
                           ("variables", [{"group": "alz-mgmt"}])):
            unconditional = copy.deepcopy(pipeline)
            unconditional[key] = value
            mutations.append(("unconditional " + key, unconditional))
        for name, value in (("empty", {}), ("conditional", {opt_in: {"name": "alz-mgmt"}})):
            root_pool = copy.deepcopy(pipeline)
            root_pool["pool"] = value
            mutations.append(("reintroduced " + name + " root pool", root_pool))
        for name, value in (("configuration_only", True), ("publish_disabled_configuration", True),
                            ("validated_configuration_run_id", "1")):
            unsafe = copy.deepcopy(pipeline)
            next(p for p in unsafe["parameters"] if p["name"] == name)["default"] = value
            mutations.append(("changed safe default " + name, unsafe))
        for key in ("stages", "jobs", "steps"):
            executable = copy.deepcopy(pipeline)
            executable[key] = [{"script": "echo forbidden-root-execution"}]
            mutations.append(("injected root " + key, executable))
        self.assertEqual(len(mutations), 15)
        for name, mutated in mutations:
            with self.subTest(mutation=name), self.assertRaises(AssertionError):
                self.assert_retained_pipeline(mutated)


if __name__ == "__main__":
    unittest.main()
