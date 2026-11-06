"""Offline strict Compute-summary and immutable archive protocol regressions."""

import copy
import io
import http.client
import errno
import ssl
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from operations import patch_provider as pp

INSTALLATION = "99999999-9999-4999-8999-999999999999"
NOW = datetime(2026, 10, 18, 22, 2, tzinfo=timezone.utc)

# Verbatim lastPatchInstallationSummary subtree from the coordinator's recorded
# patch-window-fix/research/patch-instance-view.json (2026-10-27).
# Original file SHA256:
# 000000000000000000000000000000000000000000000000000000000000002a
# Historical-clock protocol regression only, never fresh/live failure proof.
RECORDED_INSTALLATION_SUMMARY = {
    "error": {
        "code": "1",
        "details": [
            {
                "code": "OPERATION_FAILED",
                "message": " Operation status was marked as failed because: [X] a failure occurred during the operation ",
            },
            {
                "code": "PACKAGE_MANAGER_FAILURE",
                "message": "Customer environment error: Investigate and resolve unexpected return code (100) from package manager on command: sudo DEBIAN...",
            },
        ],
        "message": (
            "2 error/s reported. The latest 2 error/s are shared in detail. To view all errors, "
            "review this log file on the machine: /var/log/azure/Microsoft.CPlat.Core.LinuxPatchExtension/13.core.log"
        ),
    },
    "excludedPatchCount": 0,
    "failedPatchCount": 1,
    "installationActivityId": "00000055-000a-4000-9000-000000000055",
    "installedPatchCount": 0,
    "lastModifiedTime": "2026-10-27T15:15:46+00:00",
    "maintenanceRunId": "",
    "maintenanceWindowExceeded": False,
    "notSelectedPatchCount": 135,
    "pendingPatchCount": 0,
    "startTime": "2026-10-27T15:09:29.3859232+00:00",
    "status": "Failed",
}


def summary():
    return {
        "installationActivityId": INSTALLATION, "status": "Failed",
        "lastModifiedTime": "2026-10-18T22:01:30.1234567Z", "startTime": "2026-10-18T22:01:00Z",
        "installedPatchCount": 0, "failedPatchCount": 1, "pendingPatchCount": 0,
        "excludedPatchCount": 0, "notSelectedPatchCount": 0, "maintenanceWindowExceeded": False,
        "error": {"code": "UnitFixture", "message": "Offline package failure, not live proof."},
    }


def snapshot():
    return pp.snapshot(pp.encode({"patchStatus": {"lastPatchInstallationSummary": summary()}}), NOW)


class Credential:
    def get_token(self, scope):
        return type("Token", (), {"token": "offline-only"})()


class Http:
    def __init__(self):
        self.requests, self.objects = [], {}
        self.put_error, self.mutate, self.missing, self.oversized = None, False, False, False

    def open(self, request, timeout):
        self.requests.append(request)
        assert timeout == 5
        url = request.full_url
        if request.get_method() == "PUT":
            assert request.get_header("If-none-match") == "*"
            self.objects.setdefault(url, request.data)
            if self.put_error:
                raise self.put_error
            status, data = 201, b""
        else:
            if self.missing:
                raise HTTPError(url, 404, "fixture", {}, io.BytesIO())
            status, data = 200, self.objects[url]
            if self.mutate:
                data = data.replace(b'"failedPatchCount":1', b'"failedPatchCount":2')
            if self.oversized:
                data = b"x" * (pp.ARCHIVE_LIMIT + 1)
        result = io.BytesIO(data)
        result.status = status
        return result


class PatchProviderTests(unittest.TestCase):
    def test_typed_single_attempt_get_classification_and_timeout_override(self):
        from unittest.mock import Mock
        cases = [(TimeoutError("canary"), "timeout", True),
                 (URLError(ConnectionResetError(errno.ECONNRESET, "canary")), "transport", True),
                 (URLError(ssl.SSLError("canary")), "tls", False),
                 (URLError(URLError(ssl.SSLError("canary"))), "tls", False)]
        for status in (301, 401, 403, 404, 408, 429, 500, 501, 502, 503, 504):
            cases.append((HTTPError("https://canary.invalid", status, "canary",
                                    {"Retry-After": "7"}, io.BytesIO()),
                          "timeout" if status == 408 else "throttled" if status == 429 else
                          "auth" if status in {401, 403} else "server-error" if status >= 500 else "rejected",
                          status in {408, 429, 500, 502, 503, 504}))
        for error, reason, transient in cases:
            opener = Mock()
            opener.open.side_effect = error
            with self.subTest(reason=reason, error=type(error).__name__), \
                    patch.object(pp, "build_opener", return_value=opener), \
                    self.assertRaises(pp.ProviderReadError) as raised:
                pp.read_compute(Credential(), timeout=12.5)
            self.assertEqual(raised.exception.reason, reason)
            self.assertEqual(isinstance(raised.exception, pp.TransientProviderError), transient)
            opener.open.assert_called_once()
            self.assertEqual(opener.open.call_args.kwargs, {"timeout": 12.5})
            if transient and isinstance(error, HTTPError):
                self.assertEqual(raised.exception.retry_after, 7)
        for value, expected in ((None, (None, True)), ("0", (0, True)), ("30", (30, True)),
                                ("31", (None, False)), ("-1", (None, False)),
                                ("Wed, 21 Oct 2026 07:28:00 GMT", (None, False))):
            self.assertEqual(pp.retry_after(value), expected)
        opener = Mock()
        opener.open.side_effect = http.client.IncompleteRead(b"canary")
        with patch.object(pp, "build_opener", return_value=opener), self.assertRaises(pp.TransientProviderError):
            pp.read_compute(Credential())
        self.assertEqual(opener.open.call_count, 1)

    def test_sensor_defaults_and_put_failures_have_no_retry_or_new_classification(self):
        from unittest.mock import Mock
        opener = Mock()
        opener.open.side_effect = TimeoutError("canary")
        with patch.object(pp, "build_opener", return_value=opener), self.assertRaises(pp.TransientProviderError):
            pp.read_compute(Credential())
        self.assertEqual(opener.open.call_count, 1)
        self.assertEqual(opener.open.call_args.kwargs, {"timeout": 5})
        value = snapshot()
        transport = Http()
        transport.put_error = TimeoutError("canary")
        with patch.object(pp, "build_opener", return_value=transport):
            result = pp.archive(Credential(), value)
        self.assertEqual(result, pp.digest_for(pp.encode(value)))
        self.assertEqual([request.get_method() for request in transport.requests], ["PUT", "GET"])
        self.assertEqual(transport.requests[0].get_header("If-none-match"), "*")
        for error in (HTTPError("https://canary.invalid", 503, "canary", {}, io.BytesIO()), OSError("canary")):
            opener = Mock()
            opener.open.side_effect = error
            with patch.object(pp, "build_opener", return_value=opener), self.assertRaises(pp.ProviderError) as raised:
                pp._request(Credential(), "https://storage.azure.com/.default", pp.STORAGE,
                            data=b"{}", limit=1024)
            self.assertIs(type(raised.exception), pp.ProviderError)
            self.assertEqual(opener.open.call_count, 1)

    def test_non_network_get_errors_preserve_plain_provider_error_and_programming_errors(self):
        from unittest.mock import Mock
        for error in (PermissionError(errno.EACCES, "canary"), FileNotFoundError(errno.ENOENT, "canary"),
                      URLError("unknown url type"), http.client.InvalidURL("canary"),
                      http.client.NotConnected("canary"), ValueError("canary"), UnicodeError("canary")):
            opener = Mock()
            opener.open.side_effect = error
            with patch.object(pp, "build_opener", return_value=opener), self.assertRaises(pp.ProviderError) as raised:
                pp.read_compute(Credential())
            self.assertIs(type(raised.exception), pp.ProviderError)
            self.assertEqual(str(raised.exception), "The fixed provider/archive transport did not complete.")
            self.assertEqual(opener.open.call_count, 1)
        for kind in (TypeError, AttributeError, KeyError):
            opener = Mock()
            error = kind("canary")
            opener.open.side_effect = error
            with patch.object(pp, "build_opener", return_value=opener), self.assertRaises(kind) as raised:
                pp.read_compute(Credential())
            self.assertIs(raised.exception, error)

    def test_mixed_http_chains_keep_provider_get_non_transient(self):
        import contextlib
        from tests.test_agent_case import mixed_http_failures, RetryOpener
        with contextlib.ExitStack() as guards:
            for target in ("socket.socket.connect", "socket.create_connection", "socket.getaddrinfo"):
                guards.enter_context(patch(target, side_effect=AssertionError("Sockets forbidden.")))
            for name, error, classification in mixed_http_failures():
                with self.subTest(name=name):
                    opener = RetryOpener(error)
                    stdout, stderr = io.StringIO(), io.StringIO()
                    expected = type(error) if isinstance(error, TypeError) else pp.ProviderError
                    with patch.object(pp, "build_opener", return_value=opener), \
                            contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
                            self.assertRaises(expected) as raised:
                        pp.read_compute(Credential())
                    failure = raised.exception
                    if classification:
                        self.assertIs(type(failure), pp.ProviderReadError)
                        self.assertEqual(failure.reason, "tls")
                        self.assertNotIsInstance(failure, pp.TransientProviderError)
                    elif isinstance(error, TypeError):
                        self.assertIs(failure, error)
                    else:
                        self.assertIs(type(failure), pp.ProviderError)
                        self.assertEqual(str(failure), "The fixed provider/archive transport did not complete.")
                    self.assertFalse(hasattr(failure, "retry_after"))
                    self.assertEqual(len(opener.calls), 1)
                    self.assertEqual((stdout.getvalue(), stderr.getvalue()), ("", ""))

    def test_current_observation_is_separate_from_archived_failure_freshness(self):
        raw = pp.encode({"patchStatus": {"lastPatchInstallationSummary": RECORDED_INSTALLATION_SUMMARY}})
        current = pp.observation(raw, datetime(2026, 10, 27, 19, 33, tzinfo=timezone.utc))
        self.assertEqual(current["summary"], RECORDED_INSTALLATION_SUMMARY)
        with self.assertRaises(pp.ProviderError):
            pp.validate_snapshot(current)
        # A current observation cannot manufacture a fresh failure archive.
        with self.assertRaises(pp.ProviderError):
            pp.event(current, pp.digest_for(pp.encode(current)))

    def test_corroboration_uses_parsed_start_identity_not_state_or_modification_order(self):
        original = snapshot()
        for updates in (
            {"status": "Succeeded", "failedPatchCount": 0, "lastModifiedTime": "2026-10-18T22:01:10Z"},
            {"status": "CompletedWithWarnings", "failedPatchCount": 2,
             "error": {"code": "Changed", "message": "Same provider time, different summary."}},
            {"status": "Unknown", "failedPatchCount": 0, "startTime": "2026-10-18T20:00:00Z",
             "lastModifiedTime": "2026-10-18T20:01:00Z",
             "installationActivityId": "11111111-1111-4111-8111-111111111111"},
        ):
            latest = {**summary(), **updates}
            if latest["installationActivityId"] == INSTALLATION:
                latest["startTime"] = latest["startTime"].replace("Z", "+00:00")
            current = pp.observation(pp.encode({"patchStatus": {"lastPatchInstallationSummary": latest}}), NOW)
            with self.subTest(updates=updates):
                relationship = pp.corroborate(current, [original])
                expected = ("current summary identifies the archived installation"
                            if latest["installationActivityId"] == INSTALLATION
                            else "current summary identifies another installation")
                self.assertEqual(relationship, expected)
                self.assertEqual(original, snapshot())
        latest = {**summary(), "startTime": "2026-10-18T22:01:00.0000001Z"}
        current = pp.observation(pp.encode({"patchStatus": {"lastPatchInstallationSummary": latest}}), NOW)
        with self.assertRaises(pp.ProviderError):
            pp.corroborate(current, [original])
        healthy = snapshot()
        healthy["summary"].update(status="Succeeded", failedPatchCount=0)
        with self.assertRaises(pp.ProviderError):
            pp.corroborate(pp.observation(pp.encode({
                "patchStatus": {"lastPatchInstallationSummary": summary()}}), NOW), [healthy])

    def test_optional_maintenance_run_id_is_bounded_typed_preserved_and_not_authority(self):
        for value in ("", " Raw-Maintenance-Run-ID ", "x" * pp.MAINTENANCE_RUN_ID_LIMIT):
            candidate = {**summary(), "maintenanceRunId": value}
            self.assertEqual(pp.validate_summary(candidate)["maintenanceRunId"], value)
            self.assertEqual(candidate["installationActivityId"], INSTALLATION)
        for value in (None, True, 0, [], {}, "x" * (pp.MAINTENANCE_RUN_ID_LIMIT + 1)):
            with self.subTest(value=str(value)[:50]), self.assertRaises(pp.ProviderError):
                pp.validate_summary({**summary(), "maintenanceRunId": value})
        for field in ("maintenanceRunID", "unknownProviderField"):
            with self.subTest(field=field), self.assertRaises(pp.ProviderError):
                pp.validate_summary({**summary(), field: ""})
        # Absence stays admitted; this optional field is not an installation ID.
        self.assertNotIn("maintenanceRunId", pp.validate_summary(summary()))

    def test_recorded_compute_summary_with_empty_maintenance_run_id_round_trips(self):
        recorded = copy.deepcopy(RECORDED_INSTALLATION_SUMMARY)
        self.assertEqual(pp.validate_summary(recorded), RECORDED_INSTALLATION_SUMMARY)
        raw = pp.encode({"patchStatus": {"lastPatchInstallationSummary": recorded}})
        value = pp.snapshot(raw, datetime(2026, 10, 27, 15, 16, tzinfo=timezone.utc))
        self.assertEqual(value["summary"], RECORDED_INSTALLATION_SUMMARY)
        archived = pp.archived(pp.encode(value), pp.digest_for(pp.encode(value)))
        self.assertEqual(archived["summary"]["maintenanceRunId"], "")
        self.assertEqual(archived["summary"]["lastModifiedTime"], "2026-10-27T15:15:46+00:00")
        # Accepting the real shape must not refresh its stale provider time.
        with self.assertRaises(pp.ProviderError):
            pp.snapshot(raw, datetime(2026, 10, 27, 19, 33, tzinfo=timezone.utc))

    def test_exact_timestamp_and_collection_time_are_separate(self):
        value = snapshot()
        self.assertEqual(value["summary"], summary())
        self.assertEqual(value["summary"]["lastModifiedTime"], "2026-10-18T22:01:30.1234567Z")
        self.assertNotEqual(value["collection_time"], value["summary"]["lastModifiedTime"])
        self.assertTrue(pp.qualifying(value))

    def test_all_counts_are_typed_bounded_and_required(self):
        for field in pp.COUNTS:
            for value in (True, False, -1, 1.0, "1", None, 1000001, []):
                wrong = summary()
                wrong[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(pp.ProviderError):
                    pp.validate_summary(wrong)
            wrong = summary()
            del wrong[field]
            with self.assertRaises(pp.ProviderError):
                pp.validate_summary(wrong)

    def test_invalid_guid_time_status_and_incomplete_summary_fail_closed(self):
        for field, value in (
            ("installationActivityId", INSTALLATION.upper()), ("installationActivityId", "not-a-guid"),
            ("installationActivityId", "00000000-0000-0000-0000-000000000000"),
            ("lastModifiedTime", None), ("lastModifiedTime", "2026-10-18"),
            ("lastModifiedTime", "2026-02-30T00:00:00Z"),
            ("lastModifiedTime", "2026-10-18T22:01:30+01:00"),
            ("status", "Completed"), ("status", []), ("maintenanceWindowExceeded", 0),
            ("startTime", "2026-10-18T22:02:00Z"), ("error", {"code": "partial"}),
        ):
            wrong = summary()
            wrong[field] = value
            # This all-digit fixture's upper case is unchanged; use a real
            # alphabetic GUID to exercise canonical case rejection.
            if field == "installationActivityId" and value == INSTALLATION.upper():
                wrong[field] = "00000055-000A-4000-9000-000000000055"
            with self.subTest(field=field, value=value), self.assertRaises(pp.ProviderError):
                pp.validate_summary(wrong)

    def test_provider_time_never_replaced_by_poll_time(self):
        for modified in ("2026-10-18T22:02:01Z", "2026-10-18T22:02:00.0000001Z",
                         "2026-10-18T21:52:00Z", "2026-10-18T15:15:46Z"):
            raw = summary()
            raw.update(lastModifiedTime=modified, startTime="2026-10-18T15:00:00Z")
            with self.subTest(modified=modified), self.assertRaises(pp.ProviderError):
                pp.snapshot(pp.encode({"patchStatus": {"lastPatchInstallationSummary": raw}}), NOW)

    def test_observed_old_failure_remains_stale_at_later_poll(self):
        value = summary()
        value.update(installationActivityId="00000055-000a-4000-9000-000000000055",
                     lastModifiedTime="2026-10-27T15:15:46+00:00", startTime="2026-10-27T15:00:00Z")
        raw = pp.encode({"patchStatus": {"lastPatchInstallationSummary": value}})
        with self.assertRaises(pp.ProviderError):
            pp.snapshot(raw, datetime(2026, 10, 27, 19, 33, tzinfo=timezone.utc))

    def test_duplicate_json_nan_incomplete_assessment_and_outer_success_are_not_failure(self):
        for raw in (
            b'{"patchStatus":{},"patchStatus":{}}',
            b'{"patchStatus":{"lastPatchInstallationSummary":{"status":"Failed","status":"Succeeded"}}}',
            b'{"value":NaN}', b"[]", b"{", b"x" * (pp.SOURCE_LIMIT + 1),
            pp.encode({"status": "Succeeded"}), pp.encode({"patchStatus": {"assessmentStatus": "Failed"}}),
        ):
            with self.subTest(raw=raw[:90]), self.assertRaises(pp.ProviderError):
                pp.snapshot(raw, NOW)

    def test_valid_no_failure_is_distinct_from_failed_read(self):
        for status, failed in (("Succeeded", 0), ("InProgress", 1), ("Unknown", 0), ("Failed", 0)):
            value = snapshot()
            value["summary"].update(status=status, failedPatchCount=failed)
            self.assertFalse(pp.qualifying(value))
        value = snapshot()
        value["summary"]["status"] = "CompletedWithWarnings"
        self.assertTrue(pp.qualifying(value))

    def test_canonical_snapshot_provenance_digest_and_byte_bounds(self):
        raw = pp.encode(snapshot())
        digest = pp.digest_for(raw)
        self.assertEqual(pp.archived(raw, digest), snapshot())
        for wrong in (
            {**snapshot(), "resource_id": pp.TARGET.lower()},
            {**snapshot(), "source": "ARG"}, {**snapshot(), "api_version": "other"},
            {**snapshot(), "extra": True},
        ):
            with self.assertRaises(pp.ProviderError):
                pp.archived(pp.encode(wrong), pp.digest_for(pp.encode(wrong)))
        for value in (raw + b" ", raw.replace(b'"failedPatchCount":1', b'"failedPatchCount":2')):
            with self.assertRaises(pp.ProviderError):
                pp.archived(value, digest)
        with self.assertRaises(pp.ProviderError):
            pp.archived(b" " + raw, pp.digest_for(b" " + raw))
        for digest in ("https://untrusted.invalid/a", "../a", "a" * 63, "A" * 64):
            with self.assertRaises(pp.ProviderError):
                pp.archive_path(digest)
        value = summary()
        value["error"]["message"] = "x" * 25000
        with self.assertRaises(pp.ProviderError):
            pp.validate_summary(value)

    def test_archive_one_create_and_one_authenticated_actual_readback(self):
        http = Http()
        with patch.object(pp, "build_opener", return_value=http):
            digest = pp.archive(Credential(), snapshot())
        self.assertEqual([r.get_method() for r in http.requests], ["PUT", "GET"])
        self.assertTrue(all(r.full_url == pp.STORAGE + pp.archive_path(digest) for r in http.requests))
        self.assertTrue(all(r.get_header("Authorization") == "Bearer offline-only" for r in http.requests))

    def test_uncertain_or_conflicting_put_never_blindly_rewrites(self):
        for error in (URLError("lost fixture response"), HTTPError("fixture", 412, "exists", {}, io.BytesIO())):
            http = Http()
            http.put_error = error
            with patch.object(pp, "build_opener", return_value=http):
                digest = pp.archive(Credential(), snapshot())
            self.assertEqual([r.get_method() for r in http.requests], ["PUT", "GET"])
            self.assertEqual(digest, pp.digest_for(pp.encode(snapshot())))

    def test_missing_mutated_and_oversized_archive_cannot_emit(self):
        for field in ("missing", "mutate", "oversized"):
            http = Http()
            setattr(http, field, True)
            with self.subTest(field=field), patch.object(pp, "build_opener", return_value=http), self.assertRaises(pp.ProviderError):
                pp.archive(Credential(), snapshot())
            self.assertEqual([r.get_method() for r in http.requests], ["PUT", "GET"])

    def test_terminal_update_changes_digest_not_dimension_or_episode(self):
        first, updated = snapshot(), snapshot()
        updated["summary"].update(lastModifiedTime="2026-10-18T22:01:45Z", failedPatchCount=2, status="CompletedWithWarnings")
        events = [pp.event(v, pp.digest_for(pp.encode(v))) for v in (first, updated)]
        self.assertNotEqual(events[0]["digest"], events[1]["digest"])
        self.assertEqual(events[0]["installation_id"], events[1]["installation_id"])
        self.assertEqual(pp.episode(events[0]["installation_id"]), pp.episode(events[1]["installation_id"]))
        self.assertNotEqual(pp.episode(INSTALLATION), pp.episode("11111111-1111-4111-8111-111111111111"))

    def test_exact_closed_event_schema_and_emission_freshness(self):
        value = pp.event(snapshot(), pp.digest_for(pp.encode(snapshot())))
        self.assertEqual(set(value), pp.EVENT_FIELDS)
        self.assertEqual(pp.validate_event(value, NOW), value)
        for wrong in ({**value, "extra": True}, {**value, "version": "2.0"},
                      {**value, "failed_patch_count": True}, {**value, "resource_id": "foreign"}):
            with self.assertRaises(pp.ProviderError):
                pp.validate_event(wrong, NOW)
        with self.assertRaises(pp.ProviderError):
            pp.validate_event(value, NOW + timedelta(minutes=10))

    def test_compute_transport_fixed_get_no_retry_no_redirect(self):
        http = Http()
        http.objects[pp.COMPUTE_URL] = pp.encode({"patchStatus": {"lastPatchInstallationSummary": summary()}})
        with patch.object(pp, "build_opener", return_value=http):
            self.assertEqual(pp.decode(pp.read_compute(Credential()))["patchStatus"]["lastPatchInstallationSummary"], summary())
        self.assertEqual(len(http.requests), 1)
        self.assertEqual(http.requests[0].get_method(), "GET")
        self.assertEqual(http.requests[0].full_url, pp.COMPUTE_URL)
        self.assertIsNone(pp.NoRedirect().redirect_request(None, None, 302, "", {}, "https://untrusted.invalid"))
