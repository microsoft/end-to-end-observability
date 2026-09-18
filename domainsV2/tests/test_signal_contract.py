"""Producer contract checks run offline, without starting a demo or contacting Azure."""

from copy import deepcopy
import ast
import json
from pathlib import Path
import re
import runpy
import unittest
from unittest.mock import MagicMock

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from energy_domains_v2 import events
from energy_domains_v2 import replay
from energy_domains_v2.contracts import domain_for
from energy_domains_v2.transport import IngestionSender


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "contracts" / "energy-observations.v1.schema.json"
EXAMPLES = ROOT / "contracts" / "energy-observations.v1.examples.jsonl"


class SignalContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(cls.schema)
        if "date-time" not in FormatChecker.checkers:
            raise RuntimeError("Install domainsV2[validation] for date-time validation")
        cls.validator = Draft202012Validator(cls.schema, format_checker=FormatChecker())
        cls.exporter = runpy.run_path(str(ROOT / "scripts" / "export_signal_examples.py"))
        cls.examples = [json.loads(line) for line in EXAMPLES.read_text(encoding="utf-8").splitlines()]

    def test_exporter_and_checks_support_python311(self):
        for path in (ROOT / "scripts" / "export_signal_examples.py", Path(__file__)):
            with self.subTest(path=path.name):
                ast.parse(path.read_text(encoding="utf-8"), feature_version=(3, 11))

    def test_bicep_configures_hosted_data_endpoints(self):
        text = (ROOT / "infra" / "main.bicep").read_text(encoding="utf-8")
        settings = {
            "V2_STORAGE_ACCOUNT_URL": (
                "!empty(resolvedRuntimeEndpoints) ? resolvedRuntimeEndpoints.storageAccountUrl"
                " : storage.properties.primaryEndpoints.blob"),
            "V2_STORAGE_CONTAINER": "stateContainer.name",
            "V2_LOGS_ENDPOINT": (
                "!empty(resolvedRuntimeEndpoints) ? resolvedRuntimeEndpoints.logsEndpoint"
                " : dcr.properties.endpoints.logsIngestion"),
            "V2_DCR_IMMUTABLE_ID": (
                "!empty(resolvedRuntimeEndpoints) ? resolvedRuntimeEndpoints.dcrImmutableId"
                " : dcr.properties.immutableId"),
            "V2_LOG_ANALYTICS_WORKSPACE_ID": (
                "!empty(resolvedRuntimeEndpoints) ? resolvedRuntimeEndpoints.workspaceId"
                " : workspace.properties.customerId"),
        }
        for name, value in settings.items():
            with self.subTest(setting=name):
                self.assertRegex(
                    text,
                    rf"\{{\s*name:\s*'{name}',\s*value:\s*{re.escape(value)}\s*\}}",
                )

    def test_bicep_table_and_stream_columns_match_wire_schema(self):
        text = (ROOT / "infra" / "main.bicep").read_text(encoding="utf-8")
        declaration = re.search(r"var observationsColumns = \[(.*?)\n\]", text, re.S)
        self.assertIsNotNone(declaration)
        columns = dict(re.findall(r"\{ name: '([^']+)', type: '([^']+)' \}", declaration[1]))
        expected = {key: value["x-log-analytics-type"]
                    for key, value in self.schema["properties"].items()}
        self.assertEqual(columns, expected)
        self.assertEqual(set(self.schema["required"]), set(expected))
        self.assertIn("'Custom-EnergyObservations_CL': { columns: observationsColumns }", text)
        self.assertIn("schema: { name: 'EnergyObservations_CL', columns: observationsColumns }", text)
        self.assertRegex(text, r"streams: \['Custom-EnergyObservations_CL'\]\s+"
                         r"destinations: \['v2Workspace'\]\s+transformKql: 'source'\s+"
                         r"outputStream: 'Custom-EnergyObservations_CL'")

    def test_all_replay_frames_conform_without_wire_changes(self):
        covered = set()
        cases = set()
        ids = set()
        for surface, scenario, phase, records in self.exporter["contract_frames"]():
            covered.add(surface)
            cases.add((surface, scenario, phase))
            self.assertTrue(records)
            for row in records:
                with self.subTest(surface=surface, scenario=scenario, phase=phase,
                                  event_id=row["EventId"]):
                    self.validator.validate(row)
                    events.encoded(row)
                    self.assertNotIn(row["EventId"], ids)
                    ids.add(row["EventId"])
        self.assertEqual(covered, set(self.schema["properties"]["Surface"]["enum"]))
        self.assertEqual(len(cases), 162)  # Nine normal sets and 45 interventions, three phases each.
        self.assertGreaterEqual(len(ids), len(cases))

    def test_examples_are_generated_deterministically_and_cover_surfaces(self):
        expected = self.exporter["example_bytes"]()
        self.assertEqual(EXAMPLES.read_bytes(), expected)
        self.assertEqual(expected, self.exporter["example_bytes"]())
        self.assertNotIn(b"\r", expected)
        self.assertTrue(expected.endswith(b"\n"))
        self.assertEqual({row["Surface"] for row in self.examples},
                         set(self.schema["properties"]["Surface"]["enum"]))
        for row in self.examples:
            self.validator.validate(row)

    def test_enriched_frames_declare_profile_and_keep_context_neutral(self):
        for surface, scenario, phase, records in self.exporter["contract_frames"]():
            with self.subTest(surface=surface, scenario=scenario, phase=phase):
                for row in records:
                    self.assertEqual(row["Context"]["details_profile"], "domain-details-v1")
                    self.assertLess(events.request_size(row["Context"]), events.MAX_FIELD_BYTES)
                    self.assertEqual(row["IsSynthetic"], True)
                    self.assertEqual(row["OperationalUse"], "prohibited")
                    events._neutral(row["Context"])

    def test_profile_marker_is_versioned_but_legacy_envelopes_remain_valid(self):
        row = deepcopy(self.examples[0])
        row["Context"].pop("details_profile", None)
        self.validator.validate(row)
        row["Context"]["details_profile"] = "domain-details-v1"
        self.validator.validate(row)
        for invalid in (1, None, "unknown-profile"):
            row["Context"]["details_profile"] = invalid
            with self.subTest(marker=invalid), self.assertRaises(ValidationError):
                self.validator.validate(row)

    def test_static_details_do_not_change_with_interventions(self):
        at = "2026-09-12T16:00:00Z"
        for surface in replay.SURFACES:
            model = replay.initial_model(surface)
            for phase in range(3):
                normal = replay.sample(surface, model, phase, at)["observations"]
                expected = {replay._identity(row): row["context"] for row in normal}
                for choice in replay.scenarios(surface):
                    changed = replay.apply_action(surface, model, "inject", choice["id"])
                    rows = replay.sample(surface, changed, phase, at)["observations"]
                    with self.subTest(surface=surface, phase=phase, choice=choice["id"]):
                        self.assertEqual({replay._identity(row): row["context"] for row in rows},
                                         expected)

    def test_recovery_preserves_normal_payload_and_observation_contract(self):
        at = "2026-01-01T12:00:00Z"
        for surface in replay.SURFACES:
            for scenario in replay.scenarios(surface):
                for phase in range(replay._spec(surface)["phase_count"]):
                    with self.subTest(surface=surface, scenario=scenario["id"], phase=phase):
                        normal = replay.initial_model(surface)
                        injected = replay.apply_action(surface, normal, "inject", scenario["id"])
                        abnormal = replay.sample(surface, injected, phase, at)
                        recovered = replay.apply_action(surface, abnormal["model"], "recover")
                        frame = replay.sample(surface, recovered, phase, at)
                        self.assertEqual(frame["observations"],
                                         replay.sample(surface, normal, phase, at)["observations"])
                        state = {"run_id": "recovery-example", "surface": surface, "sequence": phase}
                        for row in events.observations(frame, state, domain_for(surface), at):
                            self.validator.validate(row)

    def test_examples_preserve_null_zero_and_stale_values(self):
        for portal, value_key in (("monitoring", "value"), ("inventory", "quantity")):
            payloads = [row["Payload"] for row in self.examples if row["Portal"] == portal]
            self.assertTrue(any(payload.get(value_key) is None for payload in payloads))
            self.assertTrue(any(payload.get(value_key) == 0 for payload in payloads))
        self.assertTrue(any(row["Payload"].get("quality") == "stale" for row in self.examples))
        for row in self.examples:
            self.assertEqual(json.loads(events.encoded(row)), row)

    def test_schema_rejects_incompatible_envelopes(self):
        changes = {
            "SchemaVersion": 1, "EventId": "not-an-event-id", "RunId": "",
            "Domain": "downstream-portfolio", "Surface": "unknown", "RecordType": "alert",
            "Portal": "alerts", "Payload": "{}", "Context": [],
            "IsSynthetic": False, "OperationalUse": "allowed",
            "ObservedAt": "2026-01-01T00:00:00",
            "TimeGenerated": "not-a-time", "SimulatedAt": "2026-02-30T12:00:00Z",
        }
        for key, value in changes.items():
            row = deepcopy(self.examples[0])
            row[key] = value
            with self.subTest(field=key), self.assertRaises(ValidationError):
                self.validator.validate(row)
        row = deepcopy(self.examples[0])
        row["Portal"] = "inventory" if row["Portal"] == "monitoring" else "monitoring"
        with self.assertRaises(ValidationError):
            self.validator.validate(row)
        for extra in ("Scenario", "TenantId"):
            row = dict(self.examples[0], **{extra: "not-in-the-wire-envelope"})
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                self.validator.validate(row)
        for key in self.schema["required"]:
            row = deepcopy(self.examples[0])
            del row[key]
            with self.subTest(missing=key), self.assertRaises(ValidationError):
                self.validator.validate(row)

    def test_payload_profile_rejects_coercions(self):
        for portal, field in (("monitoring", "value"), ("inventory", "quantity")):
            example = next(row for row in self.examples if row["Portal"] == portal)
            for bad_value in ("0", True, [], {}):
                row = deepcopy(example)
                row["Payload"][field] = bad_value
                with self.subTest(portal=portal, value=bad_value), self.assertRaises(ValidationError):
                    self.validator.validate(row)
        telemetry = deepcopy(next(row for row in self.examples if row["Portal"] == "monitoring"))
        telemetry["Payload"]["source_time_utc"] = None
        self.validator.validate(telemetry)
        telemetry["Payload"]["source_time_utc"] = "not-a-time"
        with self.assertRaises(ValidationError):
            self.validator.validate(telemetry)

    def test_nested_control_labels_are_rejected_by_existing_producer(self):
        for field in ("payload", "context"):
            for label in ("scenario", "SCENARIO_NAME", "ground_truth", "is_Anomaly",
                          "detector_verdict", "expected_outcome", "fault_injected"):
                raw = {"simulated_at": "2026-01-01T00:00:00Z", "record_type": "telemetry",
                       "entity_id": "fictional", "location_id": "fictional",
                       "payload": {}, "context": {}}
                raw[field] = {"nested": [{label: "not-a-feature"}]}
                state = {"run_id": "contract-example", "sequence": 1, "surface": "power-grid"}
                with self.subTest(field=field, label=label), self.assertRaises(ValueError):
                    events.observations({"observations": [raw]}, state, "power-grid",
                                        "2026-01-01T00:00:00Z")

    def test_existing_sender_preserves_envelopes_and_retry_identity(self):
        client = MagicMock()
        client.upload.return_value = None
        sender = IngestionSender("https://contract.centralus-1.ingest.monitor.azure.com",
                                 "dcr-" + "a" * 32, client=client)
        before = deepcopy(self.examples)
        sender.send(events.OBSERVATIONS, self.examples)
        sender.send(events.OBSERVATIONS, self.examples)
        self.assertEqual(self.examples, before)
        for call in client.upload.call_args_list:
            self.assertEqual(call.kwargs["stream_name"], "Custom-EnergyObservations_CL")
            self.assertEqual(call.kwargs["logs"], before)
        self.assertEqual(client.upload.call_count, 2)

    def test_owner_queries_keep_identity_and_exclude_audit(self):
        query = (ROOT / "docs" / "signal-processing.kql").read_text(encoding="utf-8")
        self.assertNotIn("EnergyInjectionAudit_CL", query)
        self.assertNotRegex(query, r"(?im)^\s*\|\s*take\b")
        self.assertEqual(query.count("summarize arg_max(TimeGenerated, *) by EventId"), 3)
        self.assertEqual(query.count("where RunId == SelectedRunId"), 2)
        for key in self.schema["required"]:
            self.assertIn(key, query)


if __name__ == "__main__":
    unittest.main()
