"""Readiness proofs use mocked HTTP/pipeline responses, never live Azure."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import uuid

from azure.core.credentials import AccessToken
from azure.core.exceptions import ClientAuthenticationError, ServiceRequestError, ServiceResponseError
from azure.core.pipeline.transport import HttpTransport

from energy_domains_v2 import readiness as r
from energy_domains_v2.contracts import SURFACES
from energy_domains_v2.transport import LocalCaptureSender


WORKSPACE = "7129bc4b-a4be-4361-bd58-ce401b955f76"
SESSION = "bd1bd1ca-876d-4f4d-825b-5d41fc112c71"
START = datetime(2026, 9, 10, 16, 0, tzinfo=timezone.utc)
END = START + timedelta(seconds=600)
NOW = END + timedelta(seconds=60)
HOST_MARKERS = {name: "" for name in (
    "WEBSITE_SITE_NAME", "WEBSITE_INSTANCE_ID", "IDENTITY_ENDPOINT", "MSI_ENDPOINT",
)}


def _body(surfaces=SURFACES, distinct=10, span=540, count=100):
    rows = [
        [surface, count, distinct, r._iso(START), r._iso(START + timedelta(seconds=span)), r._iso(END)]
        for surface in surfaces
    ]
    return {"tables": [{
        "name": "PrimaryResult",
        "columns": [{"name": name, "type": kind} for name, kind in r._COLUMNS.items()],
        "rows": rows,
    }]}


def _response(body, status=200):
    return SimpleNamespace(status_code=status, headers={}, content=json.dumps(body).encode(), json=lambda: body)


def _probe_body():
    return {"tables": [{
        "name": "PrimaryResult", "columns": [{"name": "EventId", "type": "string"}], "rows": [],
    }]}


class _MemoryTransport(HttpTransport):
    def __init__(self, response):
        self.response = response
        self.requests = []

    def open(self):
        pass

    def close(self):
        pass

    def __exit__(self, *args):
        self.close()

    def send(self, request, **kwargs):
        self.requests.append(request)
        return self.response


class _MemoryCredential:
    def __init__(self):
        self.scopes = []

    def get_token(self, *scopes, **kwargs):
        self.scopes.append(scopes)
        return AccessToken("nonsecret-test-token", int(time.time()) + 3600)


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.client.send_request.return_value = _response(_body())
        self.checker = r.AzureReadinessChecker(WORKSPACE, client=self.client, clock=lambda: NOW)

    def check(self, **kwargs):
        values = dict(session_id=SESSION, started_at=r._iso(START), warmup_until=r._iso(END),
                      surfaces=list(SURFACES))
        values.update(kwargs)
        return self.checker.check(**values)

    def test_all_nine_surfaces_need_distinct_spanning_baseline(self):
        result = self.check()
        self.assertTrue(result["ready"])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["checked_at"], r._iso(NOW))
        self.assertEqual(set(result["surfaces"]), set(SURFACES))
        for evidence in result["surfaces"].values():
            self.assertTrue(evidence["ready"])
            self.assertEqual(evidence["distinct_observed_at"], 10)
            self.assertEqual(evidence["sample_count"], 100)
            self.assertEqual(evidence["span_seconds"], 540)
            self.assertEqual(evidence["age_seconds"], 120)
            self.assertEqual(evidence["baseline_end_gap_seconds"], 60)

    def test_minimum_nine_timestamps_and_480_seconds(self):
        self.client.send_request.return_value = _response(_body(distinct=9, span=480, count=9))
        self.assertTrue(self.check()["ready"])
        for distinct, span in ((8, 540), (9, 479), (1, 0)):
            self.client.send_request.return_value = _response(_body(distinct=distinct, span=span))
            with self.subTest(distinct=distinct, span=span):
                self.assertFalse(self.check()["ready"])

    def test_latest_locked_window_includes_exact_warmup_endpoint(self):
        self.client.send_request.return_value = _response(_body(distinct=11, span=600, count=110))
        result = self.check()
        self.assertTrue(result["ready"])
        self.assertEqual(result["surfaces"]["power-grid"]["last_observed_at"], r._iso(END))
        self.assertEqual(result["surfaces"]["power-grid"]["baseline_end_gap_seconds"], 0)

    def test_subset_selection_does_not_require_unselected_surfaces(self):
        self.client.send_request.return_value = _response(_body(["power-grid"]))
        self.assertTrue(self.check(surfaces=["power-grid"])["ready"])

    def test_missing_or_empty_surfaces_are_pending_not_accepted(self):
        for selected in (SURFACES[:-1], ()):
            self.client.send_request.return_value = _response(_body(selected))
            result = self.check()
            self.assertFalse(result["ready"])
            self.assertEqual(result["status"], "pending")
            self.assertEqual(result["surfaces"][SURFACES[-1]]["sample_count"], 0)
            self.assertIsNone(result["surfaces"][SURFACES[-1]]["first_observed_at"])

    def test_a_single_bad_surface_blocks_global_readiness(self):
        body = _body()
        body["tables"][0]["rows"][-1][2] = 8
        self.client.send_request.return_value = _response(body)
        result = self.check()
        self.assertFalse(result["ready"])
        self.assertTrue(result["surfaces"][SURFACES[0]]["ready"])
        self.assertFalse(result["surfaces"][SURFACES[-1]]["ready"])

    def test_baseline_is_immutable_and_does_not_expire_when_now_advances(self):
        first = self.check()
        first_query = json.loads(self.client.send_request.call_args.args[0].content)
        self.checker.clock = lambda: END + timedelta(hours=1)
        second = self.check()
        second_query = json.loads(self.client.send_request.call_args.args[0].content)
        self.assertTrue(first["ready"] and second["ready"])
        self.assertEqual(first_query, second_query)
        self.assertGreater(second["surfaces"]["power-grid"]["age_seconds"],
                           first["surfaces"]["power-grid"]["age_seconds"])

    def test_no_query_before_warmup_elapsed(self):
        self.checker.clock = lambda: END - timedelta(seconds=1)
        result = self.check()
        self.assertFalse(result["ready"])
        self.assertEqual(result["status"], "pending")
        self.client.send_request.assert_not_called()

    def test_query_has_exact_session_allowlist_windows_and_dedup_before_counts(self):
        self.check()
        request = self.client.send_request.call_args.args[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.url, f"{r.QUERY_ENDPOINT}/v1/workspaces/{WORKSPACE}/query")
        self.assertEqual(request.headers["Prefer"], "wait=15")
        payload = json.loads(request.content)
        query = payload["query"]
        self.assertEqual(payload["timespan"], f"{r._iso(START)}/{r._iso(END)}")
        self.assertTrue(query.startswith("EnergyObservations_CL\n"))
        self.assertIn(f'RunId == "{SESSION}"', query)
        self.assertIn(f"ObservedAt <= datetime({r._iso(END)})", query)
        self.assertIn(f"TimeGenerated <= datetime({r._iso(END)})", query)
        self.assertIn("by Surface, EventId", query)
        self.assertLess(query.index("by Surface, EventId"), query.index("by Surface, ObservedAt"))
        self.assertIn("DistinctObservedAt=count()", query)
        for forbidden in ("EnergyInjectionAudit", "union", "search", "ago(", "dcount", "workspace("):
            self.assertNotIn(forbidden, query)
        self.assertEqual(self.client.send_request.call_args.kwargs,
                         {"connection_timeout": 5, "read_timeout": 20})

    def test_http_200_partial_error_never_counts_as_ready(self):
        body = _body()
        body["error"] = {"code": "PartialError", "message": "secret-access-token"}
        self.client.send_request.return_value = _response(body)
        with self.assertRaisesRegex(r.ReadinessError, "^PartialQueryFailure$"):
            self.check()

    def test_http_errors_empty_success_redirects_and_transport_are_sanitized(self):
        for status in (204, 302, 400, 401, 403, 429, 500, 503):
            self.client.send_request.return_value = _response({"message": "secret"}, status)
            with self.subTest(status=status), self.assertRaises(r.ReadinessError) as failure:
                self.check()
            self.assertEqual(failure.exception.code, f"QueryHttp{status}")
        for error, code in (
            (ServiceRequestError("secret"), "QueryTransportFailed"),
            (ServiceResponseError("secret"), "QueryTransportFailed"),
            (ClientAuthenticationError("secret"), "QueryAuthenticationFailed"),
        ):
            self.client.send_request.side_effect = error
            with self.assertRaises(r.ReadinessError) as failure:
                self.check()
            self.assertEqual(failure.exception.code, code)
            self.assertNotIn("secret", str(failure.exception))

    def test_response_shape_columns_rows_and_counts_are_validated(self):
        candidates = [None, {}, {"tables": []}, {"tables": [None]}, {"tables": [_body()["tables"][0]] * 2}]
        for mutate in (
            lambda table: table.update(name="WrongResult"),
            lambda table: table["columns"][0].update(type="long"),
            lambda table: table["columns"].append({"name": "extra", "type": "string"}),
            lambda table: table["columns"][1].update(name="Surface"),
            lambda table: table.update(rows=None),
            lambda table: table["rows"][0].append("extra"),
            lambda table: table["rows"][0].__setitem__(1, True),
            lambda table: table["rows"][0].__setitem__(1, 1.5),
            lambda table: table["rows"][0].__setitem__(1, 1001),
            lambda table: table["rows"][0].__setitem__(2, 101),
            lambda table: table["rows"][0].__setitem__(2, 1),
            lambda table: table["rows"][0].__setitem__(2, 0),
        ):
            body = _body()
            mutate(body["tables"][0])
            candidates.append(body)
        for index, body in enumerate(candidates):
            self.client.send_request.return_value = _response(body)
            with self.subTest(index=index), self.assertRaises(r.ReadinessError):
                self.check()

    def test_unselected_duplicate_or_unknown_surface_is_not_trusted(self):
        for surface in ("other", SURFACES[1]):
            body = _body()
            body["tables"][0]["rows"][0][0] = surface
            self.client.send_request.return_value = _response(body)
            with self.assertRaises(r.ReadinessError):
                self.check()
        self.client.send_request.return_value = _response(_body(["upstream"]))
        with self.assertRaises(r.ReadinessError):
            self.check(surfaces=["power-grid"])

    def test_timestamps_outside_baseline_or_in_future_are_not_trusted(self):
        for column, value in (
            (3, r._iso(START - timedelta(seconds=1))),
            (4, r._iso(END + timedelta(microseconds=1))),
            (5, r._iso(NOW + timedelta(seconds=61))),
            (5, r._iso(START - timedelta(seconds=1))),
            (3, None), (4, "bad"), (5, "2026-09-10T16:00:00"),
        ):
            body = _body()
            body["tables"][0]["rows"][0][column] = value
            self.client.send_request.return_value = _response(body)
            with self.subTest(column=column, value=value), self.assertRaises(r.ReadinessError):
                self.check()

    def test_response_bound_and_json_failure_are_explicit(self):
        self.client.send_request.return_value = SimpleNamespace(
            status_code=200, content=b"x" * (r.MAX_RESPONSE_BYTES + 1))
        with self.assertRaisesRegex(r.ReadinessError, "^QueryResponseTooLarge$"):
            self.check()
        response = MagicMock(status_code=200, content=b"invalid")
        response.json.side_effect = ValueError("secret raw response")
        self.client.send_request.return_value = response
        with self.assertRaisesRegex(r.ReadinessError, "^MalformedQueryResponse$"):
            self.check()

    def test_untrusted_inputs_are_rejected_before_network(self):
        for changes in (
            {"session_id": '"; union EnergyInjectionAudit_CL'},
            {"session_id": None}, {"started_at": "2026-09-10T16:00:00"},
            {"warmup_until": r._iso(END + timedelta(seconds=1))},
            {"surfaces": []}, {"surfaces": ["power-grid", "power-grid"]},
            {"surfaces": ["power-grid; union *"]}, {"surfaces": "power-grid"},
            {"surfaces": [None]}, {"surfaces": ["power-grid"] * 10},
        ):
            with self.subTest(changes=changes), self.assertRaises(r.ReadinessError):
                self.check(**changes)
        self.client.send_request.assert_not_called()
        with self.assertRaises(r.ReadinessError):
            r.AzureReadinessChecker("../workspace", client=self.client)

    def test_response_column_order_is_not_assumed(self):
        body = _body()
        body["tables"][0]["columns"].reverse()
        for row in body["tables"][0]["rows"]:
            row.reverse()
        self.client.send_request.return_value = _response(body)
        self.assertTrue(self.check()["ready"])

    def test_checker_construction_is_offline_and_identity_policy_is_bounded(self):
        with patch.object(r, "_credential") as credential, patch.object(r, "PipelineClient") as client:
            checker = r.AzureReadinessChecker(WORKSPACE)
            client.return_value.send_request.assert_not_called()
            kwargs = client.call_args.kwargs
            self.assertEqual(kwargs["base_url"], r.QUERY_ENDPOINT)
            retry = next(policy for policy in kwargs["policies"] if isinstance(policy, r._BoundedRetryPolicy))
            self.assertEqual(retry.total_retries, 1)
            auth = next(policy for policy in kwargs["policies"] if isinstance(
                policy, r.BearerTokenCredentialPolicy))
            self.assertEqual(auth._scopes, (r.QUERY_TOKEN_SCOPE,))
            self.assertEqual(kwargs["connection_timeout"], 5)
            self.assertEqual(kwargs["read_timeout"], 20)
            self.assertIs(checker.client, client.return_value)
            credential.assert_called_once_with(None)

    def test_real_azure_core_pipeline_uses_scoped_token_without_any_network(self):
        transport, credential = _MemoryTransport(_response(_body())), _MemoryCredential()
        original = r.PipelineClient

        def memory_client(**kwargs):
            return original(**kwargs, transport=transport)

        with patch.dict(os.environ, HOST_MARKERS), patch.object(
                r, "PipelineClient", side_effect=memory_client):
            checker = r.AzureReadinessChecker(WORKSPACE, credential=credential, clock=lambda: NOW)
            self.assertEqual(transport.requests, [])
            self.assertTrue(checker.check(SESSION, START, END, SURFACES)["ready"])
        self.assertEqual(credential.scopes, [(r.QUERY_TOKEN_SCOPE,)])
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(transport.requests[0].headers["Authorization"], "Bearer nonsecret-test-token")
        self.assertEqual(transport.requests[0].url,
                         f"{r.QUERY_ENDPOINT}/v1/workspaces/{WORKSPACE}/query")

    def test_local_checker_cannot_claim_azure_or_run_hosted(self):
        with patch.dict(os.environ, HOST_MARKERS):
            checker = r.LocalReadinessChecker(clock=lambda: START)
            result = checker.check(SESSION, START, END, SURFACES)
            self.assertFalse(result["ready"])
            self.assertEqual(result["status"], "local")
            checker.clock = lambda: NOW
            with self.assertRaisesRegex(r.ReadinessError, "^LocalCaptureNotConfigured$"):
                checker.check(SESSION, START, END, SURFACES)
        with patch.dict(os.environ, {**HOST_MARKERS, "WEBSITE_SITE_NAME": "v2-app"}):
            with self.assertRaises(r.ReadinessError):
                r.LocalReadinessChecker()

    def test_configured_checker_factory_is_offline_and_matches_v2_mode(self):
        with patch.dict(os.environ, {**HOST_MARKERS, "V2_MODE": "local"}):
            self.assertIsInstance(r.configured_checker(), r.LocalReadinessChecker)
        with patch.dict(os.environ, {**HOST_MARKERS, "V2_MODE": "azure",
                                     "V2_LOG_ANALYTICS_WORKSPACE_ID": WORKSPACE}), patch.object(
                                         r, "AzureReadinessChecker") as checker:
            self.assertIs(r.configured_checker(), checker.return_value)
            checker.assert_called_once_with(WORKSPACE)
        for environment, code in (
            ({"V2_MODE": "azure", "V2_LOG_ANALYTICS_WORKSPACE_ID": ""}, "MissingWorkspaceId"),
            ({"V2_MODE": "invalid"}, "InvalidReadinessMode"),
            ({"V2_MODE": "local", "WEBSITE_SITE_NAME": "v2-app"}, "HostedLocalReadinessProhibited"),
        ):
            with patch.dict(os.environ, {**HOST_MARKERS, **environment}):
                with self.assertRaisesRegex(r.ReadinessError, f"^{code}$"):
                    r.configured_checker()

    def test_errors_never_echo_arbitrary_diagnostics(self):
        self.assertEqual(str(r.ReadinessError("secret-token-with-spaces and/path")), "ReadinessFailed")


class ReadinessInfrastructureTests(unittest.TestCase):
    def test_incremental_module_has_only_conditioned_observation_access(self):
        source = (Path(__file__).resolve().parents[1] / "infra" / "readiness.bicep").read_text()
        self.assertIn("targetScope = 'resourceGroup'", source)
        self.assertIn("conditionVersion: '2.0'", source)
        self.assertIn("StringEquals 'EnergyObservations_CL'", source)
        self.assertIn("principalId: app.identity.principalId", source)
        self.assertIn("scope: workspace", source)
        self.assertIn("'Microsoft.OperationalInsights/workspaces/query/read'", source)
        self.assertIn("'Microsoft.OperationalInsights/workspaces/tables/data/read'", source)
        for forbidden in ("EnergyInjectionAudit_CL", "Tables.Custom", "query/*",
                          "workspaces/read'", "'*/read'", "sharedKeys", "appsettings"):
            self.assertNotIn(forbidden, source)
        resources = [line for line in source.splitlines() if line.startswith("resource ")]
        self.assertEqual(len(resources), 4)
        self.assertEqual(sum(" existing =" in line for line in resources), 2)


class LocalCaptureReadinessTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent / f".readiness-tests-{uuid.uuid4().hex}"
        self.root.mkdir()
        environment = patch.dict(os.environ, HOST_MARKERS)
        environment.start()
        self.addCleanup(environment.stop)
        self.addCleanup(lambda: shutil.rmtree(self.root))
        self.sender = LocalCaptureSender(self.root)
        self.checker = r.LocalReadinessChecker(self.root, clock=lambda: NOW)

    def capture(self, surfaces=SURFACES, *, session_id=SESSION, frames=10):
        rows = []
        for surface in surfaces:
            for frame in range(frames):
                for entity in range(2):
                    at = r._iso(START + timedelta(seconds=frame * 60))
                    rows.append({
                        "RunId": session_id, "Surface": surface, "ObservedAt": at, "TimeGenerated": at,
                        "EventId": f"{surface}-{frame}-{entity}", "IsSynthetic": True,
                        "OperationalUse": "prohibited", "Payload": {"value": entity},
                    })
        self.sender.send("EnergyObservations_CL", rows)
        return rows

    def check(self, surfaces=SURFACES):
        return self.checker.check(SESSION, START, END, surfaces)

    def test_actual_captures_allow_local_baseline_but_never_claim_azure(self):
        self.capture()
        result = self.check()
        self.assertTrue(result["ready"])
        self.assertEqual(result["status"], "local")
        for evidence in result["surfaces"].values():
            self.assertTrue(evidence["ready"])
            self.assertEqual(evidence["sample_count"], 20)
            self.assertEqual(evidence["distinct_observed_at"], 10)
            self.assertEqual(evidence["span_seconds"], 540)
            self.assertIsNone(evidence["last_ingested_at"])
            self.assertIsNotNone(evidence["last_captured_at"])
        self.assertFalse(self.checker.access_probe()["verified"])
        self.assertEqual(self.checker.access_probe()["code"], "LocalOnly")

    def test_retries_and_fresh_checker_do_not_inflate_counts(self):
        rows = self.capture()
        self.sender.send("EnergyObservations_CL", rows)
        restarted = r.LocalReadinessChecker(self.root, clock=lambda: NOW)
        result = restarted.check(SESSION, START, END, SURFACES)
        self.assertTrue(result["ready"])
        self.assertTrue(all(row["sample_count"] == 20 for row in result["surfaces"].values()))

    def test_elapsed_time_missing_surface_and_few_frames_do_not_make_ready(self):
        self.assertFalse(self.check()["ready"])
        self.capture(SURFACES[:-1])
        self.assertFalse(self.check()["ready"])
        self.capture([SURFACES[-1]], frames=8)
        self.assertFalse(self.check()["ready"])
        self.assertFalse(self.check()["surfaces"][SURFACES[-1]]["ready"])

    def test_other_sessions_audit_rows_and_post_baseline_observations_cannot_prove_ready(self):
        rows = self.capture(session_id="4e7d4c13-4b19-4fc7-ad1e-f334ef565fe7")
        for row in rows:
            row["RunId"] = SESSION
        self.sender.send("EnergyInjectionAudit_CL", rows)
        for row in rows:
            row["ObservedAt"] = row["TimeGenerated"] = r._iso(END + timedelta(seconds=1))
        self.sender.send("EnergyObservations_CL", rows)
        self.assertFalse(self.check()["ready"])

    def test_no_capture_read_before_warmup(self):
        self.checker.clock = lambda: END - timedelta(seconds=1)
        with patch.object(Path, "glob", side_effect=AssertionError("No early file scan")):
            self.assertFalse(self.check()["ready"])

    def test_capture_corruption_and_io_errors_fail_explicitly(self):
        path = self.root / f"{uuid.uuid4().hex}.json"
        for contents in ('{', '{"delivery_mode":"azure","table":"EnergyObservations_CL","records":[]}',
                         '{"delivery_mode":"local_capture","table":"EnergyObservations_CL","records":[NaN]}'):
            path.write_text(contents, encoding="utf-8")
            with self.assertRaises(r.ReadinessError):
                self.check()
        path.unlink()
        self.capture()
        with patch.object(Path, "open", side_effect=OSError("secret-path")):
            with self.assertRaisesRegex(r.ReadinessError, "^LocalCaptureUnavailable$"):
                self.check()

    def test_scan_file_byte_and_event_limits_fail_closed(self):
        self.capture()
        for name, value in (
            ("MAX_CAPTURE_FILES", 0), ("MAX_CAPTURE_FILE_BYTES", 100),
            ("MAX_CAPTURE_SCAN_BYTES", 100), ("MAX_CAPTURE_EVENTS", 1),
        ):
            with patch.object(r, name, value):
                with self.assertRaisesRegex(r.ReadinessError, "^LocalCaptureScanLimit$"):
                    self.check()

    def test_conflicting_event_identity_cannot_fabricate_frames(self):
        rows = self.capture(["power-grid"])
        rows[0]["ObservedAt"] = r._iso(START + timedelta(seconds=1))
        self.sender.send("EnergyObservations_CL", rows)
        with self.assertRaisesRegex(r.ReadinessError, "^ConflictingLocalCaptureEvent$"):
            self.check(["power-grid"])

    def test_factory_reads_actual_app_capture_directory(self):
        with patch.dict(os.environ, {"V2_MODE": "local", "V2_LOCAL_STATE": str(self.root)}):
            checker = r.configured_checker()
        self.assertEqual(checker.directory, self.root / "capture")


class AccessProbeTests(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.checker = r.AzureReadinessChecker(WORKSPACE, client=self.client, clock=lambda: NOW)
        self.denied = _response({"error": {"code": "InsufficientAccessError", "message": "secret"}}, 403)

    def test_positive_and_explicit_negative_are_both_required(self):
        self.client.send_request.side_effect = [_response(_probe_body()), self.denied]
        self.assertEqual(self.checker.access_probe(), {
            "verified": True, "observations_allowed": True, "audit_denied": True, "code": "AccessVerified",
        })
        requests = [call.args[0] for call in self.client.send_request.call_args_list]
        self.assertEqual(len(requests), 2)
        for request in requests:
            self.assertEqual(request.url, f"{r.QUERY_ENDPOINT}/v1/workspaces/{WORKSPACE}/query")
        positive, negative = [json.loads(request.content) for request in requests]
        self.assertTrue(positive["query"].startswith("EnergyObservations_CL "))
        self.assertIn("| take 0 | project EventId", positive["query"])
        self.assertEqual(positive["timespan"], "PT5M")
        self.assertEqual(negative, {
            "query": "EnergyInjectionAudit_CL | where TimeGenerated >= datetime(2026-09-10T15:00:00Z)"
                     " and TimeGenerated < datetime(2026-09-10T16:00:00Z)"
                     " | summarize HasAny=count() > 0",
            "timespan": "2026-09-10T15:00:00Z/2026-09-10T16:00:00Z",
        })
        for forbidden in ("EventId", "RunId", "Action", "Scenario", "project", "take ", "ago("):
            self.assertNotIn(forbidden, negative["query"])

    def test_no_arbitrary_query_argument_is_accepted(self):
        with self.assertRaises(TypeError):
            self.checker.access_probe("union *")
        self.client.send_request.assert_not_called()

    def test_audit_boolean_visibility_never_automatically_verifies_denial(self):
        for visible, code in ((True, "AuditRowsVisible"), (False, "AuditNoVisibleRows")):
            body = {"tables": [{
                "name": "PrimaryResult", "columns": [{"name": "HasAny", "type": "bool"}],
                "rows": [[visible]],
            }]}
            self.client.send_request.side_effect = [_response(_probe_body()), _response(body)]
            self.assertEqual(self.checker.access_probe(), {
                "verified": False, "observations_allowed": True, "audit_denied": False, "code": code,
            })

    def test_audit_aggregate_requires_exact_schema_one_row_and_boolean_not_integer(self):
        bodies = [None, {}, {"tables": []}, {"tables": [None]},
                  {"tables": [_probe_body()["tables"][0]]}]
        for name, columns, rows in (
            ("Other", [{"name": "HasAny", "type": "bool"}], [[False]]),
            ("PrimaryResult", [{"name": "HasAny", "type": "long"}], [[0]]),
            ("PrimaryResult", [{"name": "HasAny", "type": "bool"}], []),
            ("PrimaryResult", [{"name": "HasAny", "type": "bool"}], [[False], [False]]),
            ("PrimaryResult", [{"name": "HasAny", "type": "bool"}], [[0]]),
            ("PrimaryResult", [{"name": "HasAny", "type": "bool"}], [[1]]),
            ("PrimaryResult", [{"name": "HasAny", "type": "bool"}], [["false"]]),
            ("PrimaryResult", [{"name": "HasAny", "type": "bool"}], [[None]]),
            ("PrimaryResult", [{"name": "HasAny", "type": "bool"}], [[False, "secret"]]),
            ("PrimaryResult", [{"name": "HasAny", "type": "bool"},
                              {"name": "EventId", "type": "string"}], [[False, "secret"]]),
        ):
            bodies.append({"tables": [{"name": name, "columns": columns, "rows": rows}]})
        bodies.append({"tables": [{"name": "PrimaryResult",
                                  "columns": [{"name": "HasAny", "type": "bool"}],
                                  "rows": [[False]]}] * 2})
        for body in bodies:
            self.client.send_request.side_effect = [_response(_probe_body()), _response(body)]
            result = self.checker.access_probe()
            self.assertEqual(result["code"], "MalformedAuditProbe")
            self.assertFalse(result["verified"])
            self.assertFalse(result["audit_denied"])
            self.assertNotIn("secret", json.dumps(result))

    def test_completed_hour_is_utc_and_handles_midnight_and_year_rollover(self):
        for now, expected in (
            (datetime(2026, 9, 10, 12, 55, tzinfo=timezone(timedelta(hours=-4))),
             "2026-09-10T15:00:00Z/2026-09-10T16:00:00Z"),
            (datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
             "2025-12-31T23:00:00Z/2026-01-01T00:00:00Z"),
        ):
            self.checker.clock = lambda: now
            self.client.send_request.side_effect = [_response(_probe_body()), self.denied]
            self.assertTrue(self.checker.access_probe()["verified"])
            self.assertEqual(json.loads(self.client.send_request.call_args.args[0].content)["timespan"],
                             expected)

    def test_hour_rollover_during_audit_cannot_report_misleading_control_window(self):
        times = iter((END, END + timedelta(hours=1)))
        self.checker.clock = lambda: next(times)
        body = {"tables": [{
            "name": "PrimaryResult", "columns": [{"name": "HasAny", "type": "bool"}], "rows": [[False]],
        }]}
        self.client.send_request.side_effect = [_response(_probe_body()), _response(body)]
        result = self.checker.access_probe()
        self.assertFalse(result["verified"])
        self.assertFalse(result["audit_denied"])
        self.assertEqual(result["code"], "AuditWindowChanged")

    def test_authentication_missing_table_transient_or_partial_failure_do_not_prove_denial(self):
        for status, body, code in (
            (401, {"error": {"code": "AuthenticationFailed"}}, "AuditProbeHttp401"),
            (403, {"error": {"code": "InvalidAuthenticationToken"}}, "AuditDenialUnverified"),
            (403, {}, "AuditDenialUnverified"),
            (404, {"error": {"code": "PathNotFoundError"}}, "AuditProbeHttp404"),
            (400, {"error": {"code": "SemanticError"}}, "AuditProbeHttp400"),
            (429, {}, "AuditProbeHttp429"),
            (503, {}, "AuditProbeHttp503"),
            (200, {"error": {"code": "PartialError"}}, "AuditProbePartial"),
        ):
            self.client.send_request.side_effect = [_response(_probe_body()), _response(body, status)]
            with self.subTest(status=status, code=code):
                result = self.checker.access_probe()
                self.assertFalse(result["verified"])
                self.assertFalse(result["audit_denied"])
                self.assertEqual(result["code"], code)

    def test_failed_positive_does_not_attempt_audit_probe(self):
        for response in (
            _response({}, 403), _response({}, 429), _response({}, 500),
            _response({"error": {"code": "PartialError"}}, 200),
            _response({"tables": []}), _response(_body(["power-grid"])),
        ):
            self.client.send_request.reset_mock()
            self.client.send_request.side_effect = None
            self.client.send_request.return_value = response
            result = self.checker.access_probe()
            self.assertFalse(result["verified"])
            self.assertFalse(result["observations_allowed"])
            self.client.send_request.assert_called_once()

    def test_transport_failure_returns_only_safe_booleans_and_code(self):
        self.client.send_request.side_effect = [_response(_probe_body()), ServiceRequestError("secret")]
        result = self.checker.access_probe()
        self.assertEqual(result["code"], "QueryTransportFailed")
        self.assertFalse(result["verified"])
        self.assertEqual(set(result), {"verified", "observations_allowed", "audit_denied", "code"})
        self.assertNotIn("secret", json.dumps(result))

    def test_local_probe_is_never_live_identity_evidence(self):
        with patch.dict(os.environ, HOST_MARKERS):
            result = r.LocalReadinessChecker().access_probe()
        self.assertFalse(result["verified"])
        self.assertEqual(result["code"], "LocalOnly")


if __name__ == "__main__":
    unittest.main()
