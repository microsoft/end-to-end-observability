import copy
import io
import json
import subprocess
import sys
import textwrap
import unittest
from unittest.mock import patch
from pathlib import Path
from urllib.parse import parse_qs

from operations.servicenow import (
    BASE, CIS, CLIENT_ID, GROUP, SCHEMAS, SCOPE, AmbiguousWriteError, CaseClient,
    ClientCredentials, Contracts, HttpResponse, OwnedCase, ServiceNowError,
)

ROOT = Path(__file__).resolve().parents[1]
EPISODE = "00000025-000a-4000-8000-000000000025"
WRITE = "00000026-000a-4000-8000-000000000026"
JOURNAL = "0000000000000000000000000000011a"
REFERENCE = {"sys_id": "0000000000000000000000000000010a", "number": "INC0900001", "record_type": "incident"}
RESOURCE = "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-linux-cus/providers/Microsoft.Compute/virtualMachines/vm-asre-linux-cus"


def response(result, status=200):
    return HttpResponse(status, json.dumps({"result": result}).encode())


def context():
    evidence = {
        "contract_version": "1.0", "episode_id": EPISODE, "source_event_ids": ["unit-fixture-only"],
        "resource_id": RESOURCE, "mode": "reactive", "signal_family": "filesystem",
        "detected_at": "2026-10-16T02:00:00Z", "summary": "Unit fixture, not a real alert",
        "evidence": [{"source": "ServiceNow", "observed_at": "2026-10-16T02:00:00Z", "reference": "fixture", "finding": "Unit fixture only"}],
        "diagnosis": {"finding": "Unit test", "confidence": 0.5, "gaps": ["No live evidence"]},
        "actions": [], "verification": {"status": "not_started", "recovery_owner": "none"},
    }
    return {
        "episode_id": EPISODE, "case_reference": dict(REFERENCE), "resource_id": RESOURCE,
        "mode": "reactive", "signal_family": "filesystem", "state": {"value": "1", "label": "New"},
        "assignment_group": {"sys_id": GROUP, "label": "ASRE Demo Operations"},
        "ci": {"sys_id": CIS["linux"], "label": "ASRE Demo - Linux"},
        "priority": {"value": "4", "label": "Low"}, "short_description": "Unit fixture",
        "description": "Synthetic unit fixture, not a real incident.", "revision": "1",
        "updated_at": "2026-10-16 02:00:00", "investigation_reference": None,
        "evidence": evidence, "instance_url": "https://example-instance.service-now.com/incident.do?sys_id=" + REFERENCE["sys_id"],
    }


class Wire:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class ServiceNowTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.getaddrinfo", "socket.socket.connect", "socket.socket.connect_ex"):
            self.enterContext(patch(target, side_effect=AssertionError("Networking is forbidden in source tests.")))

    def test_bundled_contracts_are_identical_and_docker_image_hashes_include_them(self):
        for name in SCHEMAS:
            relative = name + ".schema.json"
            self.assertEqual((ROOT / "contracts" / relative).read_bytes(), (ROOT / "operations" / "contracts" / relative).read_bytes())
        ignore = (ROOT / "operations" / ".dockerignore").read_text()
        self.assertIn("!contracts/*.json", ignore)
        builder = (ROOT / "scripts" / "Build-RuntimeImages.ps1").read_text()
        self.assertIn("-Recurse -File", builder)
        self.assertIn("@('.py', '.json')", builder)
        runtime = set((ROOT / "operations" / "requirements.txt").read_text().splitlines())
        validators = [
            line for line in (ROOT / "tests" / "requirements.txt").read_text().splitlines()
            if line.startswith(("jsonschema==", "rfc3339-validator=="))
        ]
        self.assertEqual(len(validators), 2)
        self.assertTrue(set(validators).issubset(runtime), "Runtime and CI must use identical validator pins.")

    def test_packageable_transport_keeps_fixed_allowlist_without_repair_ledger_import(self):
        from operations.servicenow import TARGETS, canonical_uuid, normalized_target
        self.assertEqual(len(TARGETS), 3)
        self.assertEqual(normalized_target(RESOURCE), RESOURCE.lower())
        self.assertEqual(canonical_uuid(EPISODE), EPISODE)
        with self.assertRaises(PermissionError):
            normalized_target(RESOURCE + "-other")
        with self.assertRaises(ValueError):
            canonical_uuid(EPISODE.upper())
        source = (ROOT / "operations" / "servicenow.py").read_text()
        self.assertNotIn("from operations.lifecycle", source)

    def test_oauth_requires_exact_scope_caches_and_refreshes_with_margin(self):
        clock = [100.0]
        grant = HttpResponse(200, json.dumps({"access_token": "unit-token", "token_type": "Bearer", "expires_in": 1799, "scope": SCOPE}).encode())
        wire = Wire(grant, grant)
        provider = ClientCredentials(lambda: "unit-secret-not-live", wire, lambda: clock[0])
        self.assertEqual(provider.token(), "unit-token")
        self.assertEqual(provider.token(), "unit-token")
        self.assertEqual(len(wire.calls), 1)
        body = parse_qs(wire.calls[0][2].decode())
        self.assertEqual(body["scope"], [SCOPE])
        self.assertEqual(body["client_id"], [CLIENT_ID])
        clock[0] = 1840
        self.assertEqual(provider.token(), "unit-token")
        self.assertEqual(len(wire.calls), 2)

    def test_keyword_client_override_keeps_endpoint_scope_and_positional_compatibility(self):
        override = "a" * 32
        grant = HttpResponse(200, json.dumps({
            "access_token": "unit-token", "token_type": "Bearer", "expires_in": 1800, "scope": SCOPE,
        }).encode())
        wire = Wire(grant)
        self.assertEqual(ClientCredentials(lambda: "unit-only", wire, lambda: 0, client_id=override).token(), "unit-token")
        self.assertEqual(wire.calls[0][0:2], ("POST", "/oauth_token.do"))
        body = parse_qs(wire.calls[0][2].decode())
        self.assertEqual(body["client_id"], [override])
        self.assertEqual(body["scope"], [SCOPE])
        with self.assertRaises(TypeError):
            ClientCredentials(lambda: "unit-only", wire, lambda: 0, override)

    def test_client_override_is_validated_before_secret_or_transport_access(self):
        for value in (None, "", "A" * 32, "a" * 31, "a" * 33, "https://other.invalid", "a" * 31 + "\n"):
            wire = Wire()
            with self.subTest(value=value), self.assertRaises(ServiceNowError):
                ClientCredentials(lambda: self.fail("Secret must not be read."), wire, client_id=value)
            self.assertEqual(wire.calls, [])

    def test_oauth_rejects_missing_scope_invalid_expiry_and_bad_token_type(self):
        for patch in ({"scope": ""}, {"expires_in": True}, {"expires_in": 99999}, {"token_type": []}, {"access_token": "bad\r\nheader"}):
            grant = {"access_token": "unit-token", "token_type": "Bearer", "expires_in": 1799, "scope": SCOPE, **patch}
            with self.subTest(patch=patch), self.assertRaises(ServiceNowError):
                ClientCredentials(lambda: "unit-only", Wire(HttpResponse(200, json.dumps(grant).encode()))).token()

    def test_read_preserves_exact_owned_case_and_rejects_group_ci_target_or_reference_changes(self):
        for field in ("assignment_group", "ci", "resource_id", "case_reference"):
            value = context()
            if field in {"assignment_group", "ci", "case_reference"}:
                value[field]["sys_id"] = "f" * 32
            else:
                value[field] = RESOURCE.replace("-linux-", "-patch-")
            client = CaseClient(lambda: "unit-token", Wire(response(value)))
            with self.subTest(field=field), self.assertRaises(ServiceNowError):
                client.read_case(EPISODE, RESOURCE, REFERENCE)
        case = CaseClient(lambda: "unit-token", Wire(response(context()))).read_case(EPISODE, RESOURCE, REFERENCE)
        self.assertEqual(case.reference, REFERENCE)

    def test_note_replay_and_unknown_write_keep_original_ids(self):
        receipt = {"outcome": "already_applied", "episode_id": EPISODE, "write_request_id": WRITE, "case_reference": REFERENCE, "journal_sys_id": JOURNAL}
        case = OwnedCase(EPISODE, RESOURCE.lower(), REFERENCE, "1")
        body = {"transport_version": "1.0", "write_request_id": WRITE, "note": "Synthetic unit fixture only."}
        wire = Wire(response(receipt), ServiceNowError("Timeout"))
        client = CaseClient(lambda: "unit-token", wire)
        self.assertEqual(client.append_note(case, body)["journal_sys_id"], JOURNAL)
        with self.assertRaises(AmbiguousWriteError) as caught:
            client.append_note(case, body)
        self.assertEqual(caught.exception.episode_id, EPISODE)
        self.assertEqual(caught.exception.write_request_id, WRITE)
        self.assertEqual(wire.calls[0][2], wire.calls[1][2])

    def test_202_unknown_and_reconciliation_do_not_send_get_body(self):
        unknown = {"outcome": "unknown", "episode_id": EPISODE, "write_request_id": WRITE, "message": "Reconcile original write."}
        wire = Wire(response(unknown, 202), response(unknown, 202))
        client = CaseClient(lambda: "unit-token", wire)
        case = OwnedCase(EPISODE, RESOURCE.lower(), REFERENCE, "1")
        self.assertEqual(client.append_note(case, {"transport_version": "1.0", "write_request_id": WRITE, "note": "Unit fixture"})["outcome"], "unknown")
        self.assertEqual(client.reconcile(EPISODE, WRITE)["outcome"], "unknown")
        self.assertEqual(wire.calls[1][0], "GET")
        self.assertIsNone(wire.calls[1][2])

    def test_reconcile_exact_authenticated_missing_write_normalizes_server_404(self):
        wire = Wire(response({"error": {
            "code": "write_not_found", "message": "No write belongs to this owned episode.", "write_request_id": None,
        }}, 404))
        result = CaseClient(lambda: "unit-token", wire).reconcile(EPISODE, WRITE)
        self.assertEqual(result, {"outcome": "not_applied", "episode_id": EPISODE,
                                  "write_request_id": WRITE, "error_code": "write_not_found"})
        self.assertEqual(wire.calls[0][0:2], ("GET", BASE + "/cases/" + EPISODE + "/writes/" + WRITE))
        self.assertIsNone(wire.calls[0][2])
        self.assertEqual(wire.calls[0][3]["Authorization"], "Bearer unit-token")

    def test_missing_write_normalization_never_accepts_generic_errors_other_paths_or_denials(self):
        missing = {"error": {"code": "write_not_found", "message": "Unit fixture.", "write_request_id": None}}
        for status, result in (
            (401, missing), (403, missing), (500, missing),
            (404, {"error": {**missing["error"], "code": "episode_not_found"}}),
            (404, {"error": {**missing["error"], "write_request_id": WRITE}}),
            (404, {"error": {**missing["error"], "extra": "invalid"}}),
        ):
            with self.subTest(status=status, result=result), self.assertRaises((ServiceNowError, PermissionError)):
                CaseClient(lambda: "unit-token", Wire(response(result, status))).reconcile(EPISODE, WRITE)
        with self.assertRaises(ServiceNowError):
            CaseClient(lambda: "unit-token", Wire(response(missing, 404))).read_case(EPISODE, RESOURCE)
        with self.assertRaises(ServiceNowError):
            CaseClient(lambda: "unit-token", Wire(HttpResponse(404, b"not a facade error"))).reconcile(EPISODE, WRITE)

    def test_platform_auth_denial_is_not_ambiguous_or_success(self):
        wire = Wire(HttpResponse(403, b'{"error":{"message":"User Not Authorized"}}'))
        client = CaseClient(lambda: "unit-token", wire)
        with self.assertRaises(PermissionError):
            client.append_note(OwnedCase(EPISODE, RESOURCE.lower(), REFERENCE, "1"), {"transport_version": "1.0", "write_request_id": WRITE, "note": "Unit fixture"})

    def test_sensitive_or_oversized_notes_fail_before_transport(self):
        for note in ("x" * 3201, "Authorization: Bearer unit-example", "https://example.test?sig=not-a-real-secret"):
            wire = Wire()
            client = CaseClient(lambda: "unit-token", wire)
            with self.subTest(note=note[:20]), self.assertRaises(ServiceNowError):
                client.append_note(OwnedCase(EPISODE, RESOURCE.lower(), REFERENCE, "1"), {"transport_version": "1.0", "write_request_id": WRITE, "note": note})
            self.assertEqual(wire.calls, [])

    def test_gateway_failure_is_an_explicit_unknown_with_original_write_identity(self):
        wire = Wire(HttpResponse(502, b"gateway unavailable"))
        with self.assertRaises(AmbiguousWriteError) as caught:
            CaseClient(lambda: "unit-token", wire).append_note(
                OwnedCase(EPISODE, RESOURCE.lower(), REFERENCE, "1"),
                {"transport_version": "1.0", "write_request_id": WRITE, "note": "Unit fixture"},
            )
        self.assertEqual(caught.exception.write_request_id, WRITE)

    def test_invalid_calendar_date_is_checked_by_the_runtime_format_validator(self):
        value = context()
        value["evidence"]["detected_at"] = "2026-02-30T00:00:00Z"
        with self.assertRaises(ServiceNowError):
            CaseClient(lambda: "unit-token", Wire(response(value))).read_case(EPISODE, RESOURCE, REFERENCE)

    def test_contracts_own_date_time_checkers_without_changing_global_registries(self):
        from jsonschema import FormatChecker, _format
        registries = [FormatChecker.checkers, *(checker.checkers for checker in _format._draft_checkers.values())]
        before = [dict(registry) for registry in registries]
        existing = FormatChecker()
        original = dict(existing.checkers)
        first, second = Contracts(), Contracts()
        first_checker = next(iter(first.validators.values())).format_checker
        second_checker = next(iter(second.validators.values())).format_checker
        self.assertIsNot(first_checker, second_checker)
        for contracts, checker in ((first, first_checker), (second, second_checker)):
            self.assertEqual(checker.checkers["date-time"][0].__module__, "operations.servicenow")
            self.assertEqual(
                {name: check for name, check in checker.checkers.items() if name != "date-time"},
                {name: check for name, check in original.items() if name != "date-time"},
            )
            self.assertIn("uuid", checker.checkers)
            for validator in contracts.validators.values():
                self.assertIs(validator.format_checker, checker)
        self.assertEqual(existing.checkers, original)
        self.assertEqual([dict(registry) for registry in registries], before)

    def test_contracts_accept_pinned_date_time_forms(self):
        contracts = Contracts()
        for timestamp in (
            "2026-10-16T02:00:00Z", "2024-02-29T23:59:59Z", "2000-02-29T00:00:00Z",
            "0001-01-01T00:00:00Z", "9999-12-31T23:59:59Z",
            "2026-10-16t02:00:00z", "2026-10-16t02:00:00Z", "2026-10-16T02:00:00z",
            "2026-10-16T02:00:00.123456789Z", "2026-10-16t02:00:00.0+05:30",
            "2026-10-16T02:00:00-07:00", "2026-10-16T02:00:00+23:59",
            "2026-10-16T02:00:00-23:59", "2026-10-16T02:00:00-00:00",
        ):
            value = context()["evidence"]
            value["detected_at"] = timestamp
            with self.subTest(timestamp=timestamp):
                contracts.validate("servicenow-case", value)

    def test_contracts_reject_invalid_date_times_and_non_string_values(self):
        contracts = Contracts()
        for timestamp in (
            "", "2026-10-16", "2026-10-16T02:00:00", "2026-10-16T02:00:00.123",
            "2026-02-29T00:00:00Z", "1900-02-29T00:00:00Z", "2024-04-31T00:00:00Z",
            "0000-01-01T00:00:00Z", "2026-00-16T02:00:00Z", "2026-13-16T02:00:00Z",
            "2026-10-00T02:00:00Z", "2026-10-32T02:00:00Z", "2026-10-16T24:00:00Z",
            "2026-10-16T02:60:00Z", "2026-10-16T02:00:60Z", "2016-12-31T23:59:60Z",
            "2026-10-16T02:00:00.Z", "2026-10-16T02:00:00,1Z",
            "2026-10-16T02:00:00+24:00", "2026-10-16T02:00:00+00:60",
            "2026-10-16T02:00:00+0530", "2026-10-16 02:00:00Z",
            "２０２６-10-16T02:00:00Z", "2026-10-16T02:00:٠٠Z",
            "2026-10-16T02:00:00.１Z", "2026-10-16T02:00:00+٠٠:00",
            " 2026-10-16T02:00:00Z", "2026-10-16T02:00:00Z ", "2026-10-16T02:00:00Z\t",
            "2026-10-16T02:00:00Z\n",  # Explicit tightening of upstream's $ acceptance.
            "2026-10-16T02:00:00Z\r\n", "2026-10-16T02:00:00Zjunk",
            "2026-10-16T02:\n00:00Z", None, True, 20261016, 1.5, [], {},
        ):
            value = context()["evidence"]
            value["detected_at"] = timestamp
            with self.subTest(timestamp=timestamp), self.assertRaises(ServiceNowError):
                contracts.validate("servicenow-case", value)

    def test_contracts_keep_all_six_schemas_cross_references_and_uuid_checks(self):
        contracts = Contracts()
        fixtures = {
            "servicenow-case": context()["evidence"],
            "servicenow-case-write": {
                "transport_version": "1.0", "write_request_id": WRITE, "evidence": context()["evidence"],
            },
            "servicenow-note-write": {
                "transport_version": "1.0", "write_request_id": WRITE, "note": "Unit fixture",
                "evidence": context()["evidence"],
            },
            "servicenow-feedback-ack": {
                "transport_version": "1.0", "write_request_id": WRITE, "event_id": JOURNAL,
            },
            "servicenow-action-audit": {
                "transport_version": "1.0", "write_request_id": WRITE, "action_request_id": EPISODE,
                "action_name": "recover_workload", "proposal_fingerprint": "a" * 64,
            },
            "servicenow-adapter-response": {"result": context()},
        }
        self.assertEqual(set(fixtures), set(SCHEMAS))
        self.assertEqual(set(contracts.validators), {"urn:asre-demo:" + name + ":1" for name in SCHEMAS})
        for name, value in fixtures.items():
            with self.subTest(schema=name):
                contracts.validate(name, value)
                invalid = copy.deepcopy(value)
                if name == "servicenow-adapter-response":
                    invalid["result"]["episode_id"] = "not-a-uuid"
                else:
                    invalid["episode_id" if name == "servicenow-case" else "write_request_id"] = "not-a-uuid"
                with self.assertRaises(ServiceNowError):
                    contracts.validate(name, invalid)
        for name in ("servicenow-case-write", "servicenow-note-write", "servicenow-adapter-response"):
            invalid = copy.deepcopy(fixtures[name])
            evidence = invalid["result"]["evidence"] if name == "servicenow-adapter-response" else invalid["evidence"]
            evidence["evidence"][0]["observed_at"] = "2026-02-30T00:00:00Z"
            with self.subTest(reference=name), self.assertRaises(ServiceNowError):
                contracts.validate(name, invalid)

    def test_contracts_check_nested_evidence_verification_and_audit_dates(self):
        contracts = Contracts()
        value = context()["evidence"]
        value["verification"]["observed_at"] = "2024-02-29t00:00:00z"
        contracts.validate("servicenow-case", value)
        value["verification"]["observed_at"] = "2026-02-29T00:00:00Z"
        with self.assertRaises(ServiceNowError):
            contracts.validate("servicenow-case", value)
        audit = {
            "receipt_version": "1.0", "receipt_id": "c" * 32, "namespace": "asre-demo-v1",
            "episode_id": EPISODE, "case_reference": REFERENCE, "resource_id": RESOURCE,
            "action_name": "recover_workload", "action_request_id": EPISODE,
            "proposal_fingerprint": "a" * 64, "journal_sys_id": JOURNAL,
            "audited_at": "2024-02-29t00:00:00.123z", "expires_at": "2024-03-01T00:00:00+05:30",
            "human_approval": "not_recorded", "execution_authorized": False,
        }
        contracts.validate("servicenow-adapter-response", audit, definition="auditReceipt")
        for field in ("audited_at", "expires_at"):
            for timestamp in ("2026-02-30T00:00:00Z", "2026-10-16T02:00:00Z\n"):
                invalid = {**audit, field: timestamp}
                with self.subTest(field=field, timestamp=timestamp), self.assertRaises(ServiceNowError):
                    contracts.validate("servicenow-adapter-response", invalid, definition="auditReceipt")

    def test_contracts_keep_native_servicenow_timestamp_patterns(self):
        contracts = Contracts()
        contracts.validate("servicenow-adapter-response", context(), definition="context")
        for timestamp in ("2026-10-16T02:00:00Z", "２０２６-10-16 02:00:00", "2026-10-16 02:00", "not-a-date"):
            value = context()
            value["updated_at"] = timestamp
            with self.subTest(timestamp=timestamp), self.assertRaises(ServiceNowError):
                contracts.validate("servicenow-adapter-response", value, definition="context")

    def _run_isolated_date_time_contracts(self, mode):
        script = textwrap.dedent(r"""
            import importlib.abc
            import sys
            import unittest
            from pathlib import Path

            def deny_network(event, args):
                if event.startswith("socket.") or event == "urllib.Request":
                    raise AssertionError("Networking is forbidden in isolated source tests.")

            sys.addaudithook(deny_network)
            assert sys.flags.isolated and sys.dont_write_bytecode
            assert "jsonschema" not in sys.modules
            blocked = []

            class BlockRFC3339(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname.split(".", 1)[0] in {"rfc3339_validator", "rfc3339", "strict_rfc3339"}:
                        blocked.append(fullname)
                        raise ModuleNotFoundError("Optional RFC3339 import blocked.", name=fullname)

            blocker = BlockRFC3339()
            sys.meta_path.insert(0, blocker)
            from jsonschema import FormatChecker, _format

            assert "rfc3339_validator" in blocked
            assert "rfc3339_validator" not in sys.modules
            assert "date-time" not in FormatChecker().checkers
            modules = {name: sys.modules[name] for name in ("jsonschema", "jsonschema._format")}

            def registries():
                return [
                    dict(FormatChecker.checkers),
                    *(dict(checker.checkers) for checker in _format._draft_checkers.values()),
                ]

            before = registries()
            existing = FormatChecker()
            if sys.argv[2] == "late":
                sys.meta_path.remove(blocker)
                import rfc3339_validator
                assert rfc3339_validator.__version__ == "0.1.4"
                assert rfc3339_validator.validate_rfc3339("2024-02-29T00:00:00Z")
                assert "date-time" not in FormatChecker().checkers

            root = Path(sys.argv[1])
            sys.path[:0] = [str(root), str(root / "tests")]
            from test_servicenow import ServiceNowTests

            names = (
                "test_contracts_own_date_time_checkers_without_changing_global_registries",
                "test_contracts_accept_pinned_date_time_forms",
                "test_contracts_reject_invalid_date_times_and_non_string_values",
                "test_contracts_keep_all_six_schemas_cross_references_and_uuid_checks",
                "test_contracts_check_nested_evidence_verification_and_audit_dates",
                "test_contracts_keep_native_servicenow_timestamp_patterns",
            )
            suite = unittest.TestSuite(ServiceNowTests(name) for name in names)
            result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
            assert result.wasSuccessful()
            assert result.testsRun == 6
            assert registries() == before
            assert "date-time" not in existing.checkers
            assert all(sys.modules[name] is module for name, module in modules.items())
            if sys.argv[2] == "absent":
                assert "rfc3339_validator" not in sys.modules
            print("isolated " + sys.argv[2] + ": 6 source regressions passed")
        """)
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-c", script, str(ROOT), mode],
            cwd=ROOT, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("isolated " + mode + ": 6 source regressions passed", completed.stdout)

    def test_contracts_work_with_optional_rfc3339_imports_absent_before_jsonschema(self):
        self._run_isolated_date_time_contracts("absent")

    def test_contracts_work_with_optional_rfc3339_available_late_without_jsonschema_reload(self):
        self._run_isolated_date_time_contracts("late")

    def test_empty_more_page_is_exhausted_and_echo_is_rejected(self):
        page = {"feedback_version": "1.0", "context": context(), "events": [], "next_cursor": "a" * 32, "has_more": True, "next_poll": "Restart without cursor"}
        final = {**page, "next_cursor": None, "has_more": False}
        wire = Wire(response(page), response(final))
        client = CaseClient(lambda: "unit-token", wire)
        case = OwnedCase(EPISODE, RESOURCE.lower(), REFERENCE, "1")
        self.assertEqual(len(list(client.feedback_pages(case))), 2)
        self.assertTrue(wire.calls[1][1].endswith("?cursor=" + "a" * 32))
        echo = {**final, "events": [{"event_id": JOURNAL, "kind": "work_note", "actor": "copilot.integration", "observed_at": "2026-10-16 02:00:00", "content": "Unit echo", "disposition": "pending_delivery"}]}
        with self.assertRaises(ServiceNowError):
            list(CaseClient(lambda: "unit-token", Wire(response(echo))).feedback_pages(case))

    def test_wrong_write_receipt_and_missing_journal_are_rejected(self):
        for patch in ({"episode_id": "11111111-1111-4111-8111-111111111111"}, {"journal_sys_id": None}):
            receipt = {"outcome": "applied", "episode_id": EPISODE, "write_request_id": WRITE, "case_reference": REFERENCE, "journal_sys_id": JOURNAL, **patch}
            with self.subTest(patch=patch), self.assertRaises(ServiceNowError):
                CaseClient(lambda: "unit-token", Wire(response(receipt))).append_note(
                    OwnedCase(EPISODE, RESOURCE.lower(), REFERENCE, "1"),
                    {"transport_version": "1.0", "write_request_id": WRITE, "note": "Unit fixture"},
                )

    def test_receipt_error_never_reports_response_content(self):
        client = CaseClient(lambda: "unit-token", Wire(HttpResponse(200, b'{"result":{"unexpected":"private-fixture"}}')))
        with self.assertRaises(ServiceNowError) as caught:
            client.health()
        self.assertNotIn("private-fixture", str(caught.exception))

    def test_valid_but_wrong_endpoint_response_is_rejected_before_context_access(self):
        receipt = {"outcome": "applied", "episode_id": EPISODE, "write_request_id": WRITE, "case_reference": REFERENCE}
        with self.assertRaises(ServiceNowError):
            CaseClient(lambda: "unit-token", Wire(response(receipt))).read_case(EPISODE, RESOURCE)
        with self.assertRaises(AmbiguousWriteError):
            CaseClient(lambda: "unit-token", Wire(response(context()))).append_note(
                OwnedCase(EPISODE, RESOURCE.lower(), REFERENCE, "1"),
                {"transport_version": "1.0", "write_request_id": WRITE, "note": "Unit fixture"},
            )

    def test_create_requires_independent_exact_owned_context(self):
        payload = {"transport_version": "1.0", "write_request_id": WRITE, "evidence": context()["evidence"]}
        receipt = {"outcome": "already_applied", "episode_id": EPISODE, "write_request_id": WRITE, "case_reference": REFERENCE}
        wire = Wire(response(receipt), response(context()))
        self.assertEqual(CaseClient(lambda: "unit-token", wire).create_case(payload), receipt)
        self.assertEqual(wire.calls[1][0], "GET")
        invalid = context()
        invalid["assignment_group"]["sys_id"] = "f" * 32
        with self.assertRaises(AmbiguousWriteError) as caught:
            CaseClient(lambda: "unit-token", Wire(response(receipt), response(invalid))).create_case(payload)
        self.assertEqual(caught.exception.write_request_id, WRITE)

    def test_ack_confirms_received_not_answered_and_exact_event(self):
        payload = {"transport_version": "1.0", "write_request_id": WRITE, "event_id": JOURNAL}
        receipt = {
            "outcome": "applied", "episode_id": EPISODE, "write_request_id": WRITE,
            "case_reference": REFERENCE, "event_id": JOURNAL, "delivery": "received_not_answered",
        }
        case = OwnedCase(EPISODE, RESOURCE.lower(), REFERENCE, "1")
        self.assertEqual(CaseClient(lambda: "unit-token", Wire(response(receipt))).acknowledge(case, payload), receipt)
        with self.assertRaises(AmbiguousWriteError):
            CaseClient(lambda: "unit-token", Wire(response({**receipt, "event_id": "f" * 32}))).acknowledge(case, payload)

    def test_audit_receipt_is_correlated_and_never_authorizes_execution(self):
        payload = {
            "transport_version": "1.0", "write_request_id": WRITE, "action_request_id": EPISODE,
            "action_name": "recover_workload", "proposal_fingerprint": "a" * 64,
        }
        audit = {
            "receipt_version": "1.0", "receipt_id": "c" * 32, "namespace": "asre-demo-v1",
            "episode_id": EPISODE, "case_reference": REFERENCE, "resource_id": RESOURCE,
            "action_name": "recover_workload", "action_request_id": EPISODE,
            "proposal_fingerprint": "a" * 64, "journal_sys_id": JOURNAL,
            "audited_at": "2026-10-16T02:00:00Z", "expires_at": "2026-10-16T02:15:00Z",
            "human_approval": "not_recorded", "execution_authorized": False,
        }
        receipt = {
            "outcome": "applied", "episode_id": EPISODE, "write_request_id": WRITE,
            "case_reference": REFERENCE, "journal_sys_id": JOURNAL, "audit_receipt": audit,
        }
        case = OwnedCase(EPISODE, RESOURCE.lower(), REFERENCE, "1")
        self.assertEqual(CaseClient(lambda: "unit-token", Wire(response(receipt))).audit(case, payload), receipt)
        for patch in ({"execution_authorized": True}, {"proposal_fingerprint": "f" * 64}, {"journal_sys_id": "f" * 32}):
            with self.subTest(patch=patch), self.assertRaises(AmbiguousWriteError):
                CaseClient(lambda: "unit-token", Wire(response({**receipt, "audit_receipt": {**audit, **patch}}))).audit(case, payload)

    def test_cursor_cycles_are_rejected_without_an_unbounded_request_loop(self):
        page = {
            "feedback_version": "1.0", "context": context(), "events": [],
            "next_cursor": "a" * 32, "has_more": True, "next_poll": "Restart without cursor",
        }
        wire = Wire(response(page), response(page))
        with self.assertRaises(ServiceNowError):
            list(CaseClient(lambda: "unit-token", wire).feedback_pages(OwnedCase(EPISODE, RESOURCE.lower(), REFERENCE, "1")))
        self.assertEqual(len(wire.calls), 2)


class FiniteDispatchTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.create_connection", "socket.getaddrinfo", "socket.socket.connect", "socket.socket.connect_ex"):
            self.enterContext(patch(target, side_effect=AssertionError("Finite transport tests are offline only.")))

    def test_real_https_dispatch_fractional_timeout_after_token_and_serialization(self):
        from operations import servicenow as sn
        events, calls = [], []
        case = OwnedCase(EPISODE, RESOURCE.lower(), REFERENCE, "1")
        payload = {"transport_version": "1.0", "write_request_id": WRITE, "note": "Offline finite fixture."}
        result = {"outcome": "applied", "episode_id": EPISODE, "write_request_id": WRITE,
                  "case_reference": REFERENCE, "journal_sys_id": JOURNAL}
        class Response(io.BytesIO):
            status = 200
        class Opener:
            def open(self, request, timeout):
                events.append("POST")
                calls.append((request, timeout))
                return Response(json.dumps({"result": result}).encode())
        def token():
            events.append("token")
            return "unit-token"
        def guard():
            events.append("guard")
            return 0.123456
        original = sn.json.dumps
        def dumps(value, *args, **kwargs):
            if value is payload:
                events.append("serialize")
            return original(value, *args, **kwargs)
        with patch.object(sn, "build_opener", return_value=Opener()), patch.object(sn.json, "dumps", side_effect=dumps):
            receipt = CaseClient(token).append_note(case, payload, before_submit=guard)
        self.assertEqual(receipt["journal_sys_id"], JOURNAL)
        self.assertEqual(events, ["token", "serialize", "guard", "guard", "POST"])
        self.assertEqual(calls[0][1], 0.123456)
        self.assertEqual(json.loads(calls[0][0].data), payload)
        self.assertEqual(calls[0][0].full_url, sn.ORIGIN + BASE + "/cases/" + EPISODE + "/notes")

    def test_guard_rejection_or_sub_floor_never_reaches_real_https(self):
        from operations import servicenow as sn
        case = OwnedCase(EPISODE, RESOURCE.lower(), REFERENCE, "1")
        payload = {"transport_version": "1.0", "write_request_id": WRITE, "note": "Offline fixture."}
        for remaining in (0, -1, 0.000999, float("nan"), float("inf"), True, "1"):
            with self.subTest(remaining=remaining), patch.object(sn, "build_opener") as opener:
                with self.assertRaises(ServiceNowError) as caught:
                    CaseClient(lambda: "unit").append_note(case, payload, before_submit=lambda: remaining)
                self.assertNotIsInstance(caught.exception, AmbiguousWriteError)
                opener.assert_not_called()

    def test_expiry_during_real_https_preparation_cannot_dispatch(self):
        from operations import servicenow as sn
        remaining = [1.0]
        opener = unittest.mock.Mock()
        def build(*args):
            remaining[0] = 0
            return opener
        case = OwnedCase(EPISODE, RESOURCE.lower(), REFERENCE, "1")
        payload = {"transport_version": "1.0", "write_request_id": WRITE, "note": "Offline fixture."}
        with patch.object(sn, "build_opener", side_effect=build), self.assertRaises(PermissionError):
            CaseClient(lambda: "unit").append_note(case, payload, before_submit=lambda: remaining[0])
        opener.open.assert_not_called()
        with patch.object(sn, "build_opener") as opener:
            def expired():
                raise PermissionError("Unit authority expired after token acquisition.")
            with self.assertRaises(PermissionError):
                CaseClient(lambda: "unit").append_note(case, payload, before_submit=expired)
            opener.assert_not_called()

    def test_default_four_argument_transport_and_default_real_timeout_unchanged(self):
        from operations import servicenow as sn
        health = {"version": "0.1.0", "instance": "example-instance", "scope": "x_0000000_asre",
                  "execution_mode": "Review", "azure_mutations_exposed": False, "human_approval_enabled": False,
                  "trusted_verifier_bound": False, "feedback": "fixture", "agent_continuation": "fixture"}
        wire = Wire(response(health))
        CaseClient(lambda: "unit", wire).health()
        self.assertEqual(len(wire.calls[0]), 4)
        class Response(io.BytesIO):
            status = 200
        opener = unittest.mock.Mock()
        opener.open.side_effect = lambda *a, **kw: Response(json.dumps({"result": health}).encode())
        with patch.object(sn, "build_opener", return_value=opener):
            CaseClient(lambda: "unit").health()
            self.assertEqual(opener.open.call_args.kwargs["timeout"], 45)
            sn.https_transport("GET", BASE + "/health", None, {}, timeout=0.001)
            self.assertEqual(opener.open.call_args.kwargs["timeout"], 0.001)
            for value in (0, 46, True, float("nan")):
                with self.assertRaises(ServiceNowError):
                    sn.https_transport("POST", BASE + "/cases", b"{}", {}, timeout=value)


if __name__ == "__main__":
    unittest.main()
