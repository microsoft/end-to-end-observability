import copy
import json
import unittest
from pathlib import Path
from uuid import uuid4

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

    def test_vm_disks_stay_standard_lrs(self):
        compute = (ROOT / "infra" / "compute.tf").read_text(encoding="utf-8")
        self.assertEqual(compute.count('storage_account_type = "Standard_LRS"'), 2)
        self.assertNotIn('storage_account_type = "StandardSSD_LRS"', compute)


if __name__ == "__main__":
    unittest.main()
