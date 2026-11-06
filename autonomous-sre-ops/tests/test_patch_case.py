"""Offline native dimension, immutable proof and installation ownership tests."""

import copy
import hashlib
import http.client
import contextlib
import errno
import io
import json
import ssl
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.error import HTTPError, URLError
from unittest.mock import patch

from operations import agent_case as ac, patch_provider as pp, servicenow as sn
from tests.test_agent_case import ALERT, COMMISSIONING, INVESTIGATION, NOW, Facade, Reader, Store, evidence
from tests.test_agent_case import RetryClock, RetryCredential, RetryOpener, RetryResponse
from tests.test_patch_provider import INSTALLATION, RECORDED_INSTALLATION_SUMMARY, snapshot

NOTE = "Offline terminal installation fixture. No repair or recovery claimed."
FAKE_CURRENT_CREDENTIAL = "offline-current-credential-fixture"
SENSITIVE_CURRENT_ERRORS = (
    "https://fixture.invalid/packages?access_token=" + FAKE_CURRENT_CREDENTIAL,
    "Authorization: Bearer " + FAKE_CURRENT_CREDENTIAL,
)
# Exact query as first released and independently read from a frozen incident
# episode by the coordinator job. Keep this independent
# of the runtime allowlist so a query change cannot silently redefine it.
OCT22_PATCH_QUERY = (
    'arg("").PatchInstallationResources '
    "| where type =~ 'microsoft.compute/virtualmachines/patchinstallationresults' "
    f"| where id startswith '{ac.PATCH_RESOURCE}/patchInstallationResults/' "
    "| extend observed_at=todatetime(properties.lastModifiedDateTime) "
    "| where observed_at > ago(10m) "
    "| where tostring(properties.status) in ('Failed', 'CompletedWithWarnings') "
    "| where toint(properties.failedPatchCount) > 0 "
    f"| project resource_id='{ac.PATCH_RESOURCE}', observed_at, installation_id=id, "
    "status=tostring(properties.status), failed_patch_count=toint(properties.failedPatchCount), "
    "error_details=substring(tostring(properties.errorDetails), 0, 1600)"
)


def sample():
    value = snapshot()
    return pp.sample(value, pp.digest_for(pp.encode(value)))


def patch_evidence():
    value = evidence()
    value.update(resource_id=ac.PATCH_RESOURCE, signal_family="patch-installation")
    value["evidence"][1] = {
        "source": "UpdateManager", "observed_at": sample()["observed_at"],
        "reference": pp.COMPUTE_URL, "finding": "Offline native terminal installation failure.",
    }
    return value


def native_fixture():
    return {
        "id": "88888888-8888-4888-8888-888888888888", "source": "Incident",
        "threadOrigin": "Trigger", "startMessageIsHuman": None, "type": "Prod", "agentMode": "Review",
        "createdTimestamp": "2026-10-18T22:03:01Z",
        "incidentSource": {"incidentType": "AzMonitor", "incidentId": ac.PATCH_PROFILE.rule_id,
                           "canonicalIncidentId": "azmon:v2:arm-rule:" + "A" * 64},
        "incidentDetails": {"filterId": "asre-azure-monitor-review", "incidentPriority": "Sev1",
                            "incidentCreatedTime": "2026-10-18T22:03:00Z"},
        "status": {"incidentStatus": {"incidentId": ALERT.rsplit("/", 1)[1]}},
        "startMessage": {"author": {"role": "SREAgent", "userId": "agent-default"}},
    }


def arm_alert():
    return {"id": ALERT, "properties": {
        "essentials": {"alertRule": ac.PATCH_PROFILE.rule_id, "targetResource": ac.WORKSPACE,
                       "severity": "Sev1", "monitorService": "Log Alerts V2", "signalType": "Log",
                       "startDateTime": "2026-10-18T22:03:00Z", "monitorCondition": "Fired"},
        "context": {"context": {"condition": {
            "windowStartTime": "2026-10-18T21:52:00Z", "windowEndTime": "2026-10-18T22:02:00Z",
            "windowSize": "PT10M", "allOf": [{
                "searchQuery": ac.PATCH_PROFILE.query, "operator": "GreaterThan", "threshold": "0",
                "timeAggregation": "Count", "metricValue": 1,
                "dimensions": [{"name": "installation_id", "value": INSTALLATION}],
            }],
        }}},
    }}


def console_table(values=None):
    rows = []
    for value in [snapshot()] if values is None else values:
        digest = pp.digest_for(pp.encode(value))
        event = pp.event(value, digest)
        rows.append([event["provider_time"], pp.TARGET, event["provider_time"], event["installation_id"],
                     event["status"], event["failed_patch_count"], digest, pp.encode(event).decode()])
    return {"tables": [{"columns": [{"name": name} for name in (
        "TimeGenerated", "resource_id", "observed_at", "installation_id", "status",
        "failed_patch_count", "digest", "event_json")], "rows": rows}]}


class PatchReader(Reader):
    def __init__(self):
        super().__init__()
        self.thread["resource_id"] = ac.PATCH_RESOURCE
        self.alerts[ALERT].update(rule_id=ac.PATCH_PROFILE.rule_id, installation_id=INSTALLATION)
        self.signal_data = {
            "alert_id": ALERT, "rule_id": ac.PATCH_PROFILE.rule_id, "resource_id": ac.PATCH_RESOURCE,
            "query": ac.PATCH_PROFILE.query, "samples": [sample()],
        }
        self.current_summary = copy.deepcopy(snapshot()["summary"])

    def signal(self, identity):
        value = copy.deepcopy(self.signal_data)
        value["current_provider_observation"] = self.patch_current(value["samples"])
        return value

    def patch_current(self, samples):
        try:
            current = pp.observation(pp.encode({
                "patchStatus": {"lastPatchInstallationSummary": self.current_summary}}), NOW)
            pp.corroborate(current, [row["snapshot"] for row in samples])
            return current
        except pp.ProviderError:
            raise ac.CaseToolError("Offline current corroboration rejected.") from None

    def disk(self, identity):
        raise AssertionError("Patch evidence must not use the filesystem reader.")


class PatchFacade(Facade):
    def __call__(self, method, path, body, headers, **options):
        if options:
            assert method == "POST" and 0 < options["timeout"] <= 45
        return super().__call__(method, path, body, headers)

    def context(self, episode):
        value = super().context(episode)
        value.update(resource_id=ac.PATCH_RESOURCE, signal_family="patch-installation")
        value["ci"]["sys_id"] = sn.CIS["patch"]
        return value


class PatchCaseTests(unittest.TestCase):
    def setUp(self):
        self.reader, self.store = PatchReader(), Store()
        self.facade = PatchFacade(self.store)
        self.client = sn.CaseClient(lambda: "offline-fixture", transport=self.facade)
        self.configuration = {**copy.deepcopy(COMMISSIONING), "profiles": ["disk", "patch-installation"]}
        self.tool = self.make()

    def make(self, reader=None, store=None):
        return ac.CaseTool(reader or self.reader, self.configuration, store or self.store, self.client, lambda: NOW,
                           policy={"version": "1.0", "minimum_not_before": "2026-10-18T21:15:23.835Z"},
                           profile="patch-installation")

    def create(self, value=None, tool=None, investigation=INVESTIGATION, note=NOTE):
        return (tool or self.tool).run("create", investigation, ALERT,
                                      json.dumps(patch_evidence() if value is None else value), note)

    def frozen(self):
        return self.tool.load(pp.episode(INSTALLATION))

    def legacy(self, investigation=INVESTIGATION, query=ac.LEGACY_PATCH_ALERT_QUERY,
               installation_id=INSTALLATION):
        binding = self.tool.authority(INVESTIGATION, ALERT, writing=False)
        del binding["installation_id"]
        binding["investigation_id"] = investigation
        episode = ac.episode_for(investigation)
        value = patch_evidence()
        value["episode_id"] = episode
        value["evidence"][1]["reference"] = ac.GRAPH_QUERY_URL
        row = {k: v for k, v in sample().items() if k not in {"snapshot", "digest"}}
        row["installation_id"] = pp.TARGET + "/patchInstallationResults/" + installation_id
        signal = {**self.reader.signal_data, "query": query, "samples": [row]}
        payloads = self.tool.payloads(episode, binding, value, NOTE)
        plan = {
            "version": 1, "episode_id": episode, "recurrence_id": "", "binding": binding,
            "proof": {"signal": signal, "receipts": {}}, "payloads": payloads,
            "fingerprints": {name: hashlib.sha256(ac.wire(payload)).hexdigest() for name, payload in payloads.items()},
        }
        self.store.create(ac.intent_path(episode), ac.wire(plan))
        return plan

    def invoke_main(self, action="read", fail=None, failures=2, error=None, auth=False,
                    discovery=False, thread_list=None):
        clock, credential = RetryClock(), RetryCredential()
        context = clock.context(action)
        counters = {"log-query": 0, "archive": 0, "compute": 0, "put": 0, "facade": 0, "secret": 0, "oauth": 0}
        counters.update({"sre-threads": 0, "sre-thread": 0, "alert": 0})
        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return NOW
        def transport(method, path, body, headers, **options):
            if path == "/oauth_token.do":
                counters["oauth"] += 1
                if fail == "oauth":
                    raise sn.ServiceNowError("offline-private-canary")
                return sn.HttpResponse(200, json.dumps({
                    "access_token": "offline-facade-token", "token_type": "Bearer",
                    "expires_in": 1800, "scope": sn.SCOPE}).encode())
            counters["facade"] += 1
            if fail == "facade":
                raise sn.ServiceNowError("offline-private-canary")
            return self.facade(method, path, body, headers, **options)
        provider = sn.ClientCredentials(lambda: ac.exact_secret(ac._MeteredCredential(credential, context), context=context),
                                        transport=transport)
        client = sn.CaseClient(provider.token if auth else lambda: "offline-fixture", transport=transport)
        class Opener:
            def open(inner, request, timeout):
                url, method = request.full_url, request.get_method()
                stage = ("log-query" if url == ac.LOG_QUERY_URL else "compute" if url == pp.COMPUTE_URL
                         else "archive" if pp.ARCHIVE_PREFIX in url else "secret" if url == ac.SECRET_URL
                         else "sre-threads" if url == ac.SRE_ORIGIN + "/api/v1/threads"
                         else "sre-thread" if "/api/v1/threads/" in url
                         else "alert" if "/providers/Microsoft.AlertsManagement/alerts/" in url else None)
                if stage:
                    counters[stage] += 1
                    if stage == fail and counters[stage] <= failures:
                        raise error or TimeoutError("offline-private-canary")
                if method == "PUT":
                    counters["put"] += 1
                    name = url.split("/evidence/", 1)[1]
                    self.store.create(name, request.data)
                    return RetryResponse(b"", 201)
                if url == ac.LOG_QUERY_URL:
                    return RetryResponse(ac.wire(console_table()))
                if url == ac.SECRET_URL:
                    return RetryResponse(b'{"value":"offline-only-facade-secret"}')
                if url == pp.COMPUTE_URL:
                    return RetryResponse(pp.encode({"patchStatus": {"lastPatchInstallationSummary": snapshot()["summary"]}}))
                if pp.ARCHIVE_PREFIX in url:
                    return RetryResponse(pp.encode(snapshot()))
                if url == ac.SRE_ORIGIN + "/api/v1/threads":
                    collection = {"value": [native_fixture()]} if thread_list is None else thread_list
                    return RetryResponse(collection if isinstance(collection, bytes) else json.dumps(collection).encode())
                if "/api/v1/threads/" in url:
                    return RetryResponse(ac.wire(native_fixture()))
                if "/providers/Microsoft.AlertsManagement/alerts/" in url:
                    return RetryResponse(ac.wire(arm_alert()))
                if "?restype=container" in url:
                    return RetryResponse(b"<EnumerationResults><Blobs/><NextMarker/></EnumerationResults>")
                if "/case-intents/v1/" in url:
                    name = url.split("/evidence/", 1)[1]
                    if name in self.store.data:
                        return RetryResponse(self.store.data[name])
                    raise HTTPError(url, 404, "offline-private-canary", {}, None)
                raise AssertionError("Unexpected offline route.")
        output, stderr = io.StringIO(), io.StringIO()
        with patch.object(ac, "_configuration_frozen", True), \
                patch.object(ac, "_frozen_configuration", self.configuration), \
                patch.object(ac, "commissioning_policy", return_value=self.tool.policy), \
                patch.object(ac, "managed_identity", return_value=credential), \
                patch.object(ac, "_patch_context", return_value=context), \
                patch.object(ac, "datetime", FixedDatetime), \
                patch.object(ac, "CaseClient", return_value=client), \
                patch.object(ac, "build_opener", return_value=Opener()), \
                patch.object(pp, "build_opener", return_value=Opener()), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(stderr):
            result = ac.main(action, "" if discovery else native_fixture()["id"], "" if discovery else ALERT,
                             json.dumps(patch_evidence()) if action == "create" else "",
                             NOTE if action == "create" else "", "", "patch-installation")
        self.assertNotIn("offline-private-canary", json.dumps(result) + output.getvalue() + stderr.getvalue())
        self.assertEqual(credential.closed, 1)
        return result, counters, context, clock

    def test_main_ids_omitted_uses_production_discovery_and_retries(self):
        for stage, failures in ((None, 0), ("sre-threads", 1), ("sre-thread", 2)):
            with self.subTest(stage=stage):
                result, counts, context, clock = self.invoke_main(discovery=True, fail=stage, failures=failures)
                self.assertEqual((counts["put"], counts["facade"], self.store.data), (0, 0, {}))
                if stage == "sre-thread":
                    self.assertEqual((result["stage"], result["reason"], result["attempts"], result["retryable"]),
                                     ("sre-thread", "timeout", 2, True))
                    self.assertEqual(counts["log-query"], 0)
                else:
                    self.assertEqual(result["status"], "native_evidence")
                    self.assertEqual(counts["sre-threads"], 2 if stage else 1)
                    self.assertEqual((counts["sre-thread"], counts["alert"], counts["log-query"]), (3, 3, 1))
                self.assertEqual(clock.sleeps, [2] if stage else [])

    def test_main_discovery_count_and_byte_residuals_are_distinct(self):
        for collection, diagnostic in (({"value": [native_fixture()] * 201}, False),
                                       (b"x" * (ac._NATIVE_COLLECTION_LIMIT + 1), True)):
            result, counts, context, clock = self.invoke_main(discovery=True, thread_list=collection)
            self.assertEqual(result["status"], "blocked")
            if diagnostic:
                self.assertEqual((result["stage"], result["reason"], result["retryable"], result["attempts"]),
                                 ("sre-threads", "invalid", False, 1))
            else:
                self.assertEqual(set(result), {"status", "message"})
            self.assertEqual((counts["sre-threads"], counts["sre-thread"], counts["put"], counts["facade"]), (1, 0, 0, 0))
            self.assertEqual(self.store.data, {})
            self.assertEqual(clock.sleeps, [])

    def test_main_enhanced_production_reads_retry_and_keep_success_shape(self):
        for stage in ("log-query", "archive", "compute"):
            with self.subTest(stage=stage):
                self.setUp()
                result, counts, context, clock = self.invoke_main(fail=stage, failures=1)
                self.assertEqual(result["status"], "native_evidence")
                self.assertNotIn("attempts", result)
                self.assertEqual(counts[stage], 2)
                self.assertEqual(clock.sleeps, [2])
                self.assertEqual((counts["put"], counts["facade"], self.store.creations), (0, 0, 0))
                self.assertTrue(context.active)

    def test_main_prewrite_exhaustion_preserves_zero_persistence_and_facade_calls(self):
        for action in ("read", "create"):
            for stage in ("log-query", "archive", "compute"):
                with self.subTest(action=action, stage=stage):
                    self.setUp()
                    result, counts, context, clock = self.invoke_main(action, stage)
                    self.assertEqual((result["status"], result["stage"], result["reason"], result["retryable"], result["attempts"]),
                                     ("blocked", stage, "timeout", action == "read", 2))
                    self.assertEqual((counts[stage], counts["put"], counts["facade"], self.store.creations), (2, 0, 0, 0))
                    self.assertEqual(clock.sleeps, [2])
                    self.assertTrue(context.active)

    def test_main_create_latches_once_and_original_owner_reconciles(self):
        result, counts, context, clock = self.invoke_main("create", "log-query", failures=1)
        self.assertEqual(result["status"], "persisted")
        self.assertEqual((counts["put"], self.store.creations), (1, 1))
        self.assertFalse(context.active)
        frozen = copy.deepcopy(self.store.data)
        self.facade.posts.clear()
        result, counts, context, clock = self.invoke_main("create")
        self.assertEqual(result["status"], "persisted")
        self.assertEqual((counts["put"], self.store.data, self.facade.posts), (0, frozen, []))
        self.assertFalse(context.active)

    def test_owned_read_secret_is_metered_but_oauth_and_facade_failures_are_generic(self):
        self.create()
        original = copy.deepcopy(self.store.data)
        for failures in (1, 2):
            result, counts, context, clock = self.invoke_main(fail="secret", failures=failures, auth=True)
            self.assertEqual(counts["secret"], 2)
            if failures == 1:
                self.assertIn(result["status"], {"persisted", "owned"})
                self.assertGreater(counts["facade"], 0)
            else:
                self.assertEqual((result["stage"], result["reason"], result["retryable"], result["attempts"]),
                                 ("secret", "timeout", True, 2))
                self.assertEqual((counts["oauth"], counts["facade"]), (0, 0))
            self.assertEqual(self.store.data, original)
            self.assertTrue(context.active)
        for stage in ("oauth", "facade"):
            result, counts, context, clock = self.invoke_main(fail=stage, auth=True)
            self.assertEqual(set(result), {"status", "message"})
            self.assertEqual((counts["secret"], counts["oauth"]), (1, 1))
            self.assertEqual(self.store.data, original)
            self.assertTrue(context.active)

    def test_owned_read_secret_retry_matrix(self):
        self.create()
        original = copy.deepcopy(self.store.data)
        cases = [(TimeoutError(), "timeout", True),
                 (URLError(ConnectionResetError(errno.ECONNRESET, "canary")), "transport", True),
                 (http.client.IncompleteRead(b"offline-private-canary"), "transport", True),
                 (ssl.SSLError(), "tls", False), (ac._InvalidRead(), "invalid", False)]
        for status in (408, 429, 500, 502, 503, 504, 401, 403, 400, 302, 501):
            cases.append((HTTPError(ac.SECRET_URL, status, "offline-private-canary", {}, None),
                          "timeout" if status == 408 else "throttled" if status == 429 else
                          "server-error" if status >= 500 else "auth" if status in (401, 403) else "rejected",
                          status in (408, 429, 500, 502, 503, 504)))
        for error, reason, retryable in cases:
            with self.subTest(error=type(error).__name__, reason=reason):
                result, counts, context, clock = self.invoke_main(fail="secret", error=error, auth=True)
                self.assertEqual((result["stage"], result["reason"], result["retryable"], result["attempts"]),
                                 ("secret", reason, retryable, 2 if retryable else 1))
                self.assertEqual((counts["oauth"], counts["facade"], counts["put"]), (0, 0, 0))
                self.assertEqual(counts["secret"], 2 if retryable else 1)
                self.assertEqual(self.store.data, original)

    def test_partial_log_table_and_provider_validation_are_not_retried(self):
        clock = RetryClock()
        context = clock.context()
        reader = ac.AzureReader(ac._MeteredCredential(RetryCredential(), context),
                                profile=ac.PATCH_PROFILE, context=context)
        for raw in ({"partialError": {}}, {"tables": [{"columns": [], "rows": []}]},
                    {"tables": [{"columns": [{"name": "TimeGenerated"}], "rows": [[]]}]}):
            with patch.object(reader, "alert", return_value=self.reader.alert(ALERT)), \
                    patch.object(ac, "_azure_attempt", return_value=raw) as attempt, \
                    self.assertRaises(ac.CaseDiagnostic) as raised:
                reader.patch_signal(ALERT)
            self.assertEqual((raised.exception.stage, raised.exception.reason, raised.exception.attempts),
                             ("log-query", "invalid", 1))
            self.assertEqual(attempt.call_count, 1)
        for error, reason in ((pp.ProviderError("offline-private-canary"), "invalid"),
                              (pp.ProviderReadError("auth"), "auth")):
            with patch.object(pp, "read_compute", side_effect=error) as attempt, \
                    self.assertRaises(ac.CaseDiagnostic) as raised:
                reader.patch_current([sample()])
            self.assertEqual((raised.exception.stage, raised.exception.reason), ("compute", reason))
            self.assertEqual(attempt.call_count, 1)

    def test_freeze_expired_create_entry_surfaces_direct_budget_without_put_or_token(self):
        clock = RetryClock()
        context = clock.context("create")
        credential, opener = RetryCredential(), RetryOpener()
        intents = ac.BlobIntents(ac._MeteredCredential(credential, context), opener, context=context)
        tool = self.make(store=intents)
        binding = tool.authority(INVESTIGATION, ALERT, writing=True)
        clock.value = 90
        with patch.object(intents, "read", side_effect=AssertionError("Diagnostic must not be swallowed.")), \
                self.assertRaises(ac.CaseDiagnostic) as raised:
            tool.freeze(pp.episode(INSTALLATION), binding, json.dumps(patch_evidence()), NOTE, "")
        self.assertEqual((raised.exception.stage, raised.exception.reason, raised.exception.retryable,
                          raised.exception.attempts), ("intent-read", "budget", False, 0))
        self.assertEqual((opener.calls, credential.calls, self.facade.posts), ([], [], []))
        self.assertTrue(context.active)

    def test_blob_write_latch_precedes_token_and_never_reopens_or_retries(self):
        for failure in (False, True):
            clock = RetryClock()
            context = clock.context("create")
            credential = RetryCredential()
            def token(scope):
                self.assertFalse(context.active)
                return RetryCredential().get_token(scope)
            credential.get_token = token
            opener = RetryOpener(TimeoutError() if failure else RetryResponse(b"", 201), RetryResponse(b"{}"))
            blobs = ac.BlobIntents(ac._MeteredCredential(credential, context), opener, context=context)
            name = ac.intent_path(pp.episode(INSTALLATION))
            if failure:
                with self.assertRaises(ac.CaseToolError) as raised:
                    blobs.create(name, b"{}")
                self.assertNotIsInstance(raised.exception, ac.CaseDiagnostic)
            else:
                blobs.create(name, b"{}")
            self.assertEqual(len(opener.calls), 1)
            self.assertEqual(opener.calls[0][0:1] + opener.calls[0][3:4], ("PUT", 20))
            self.assertFalse(context.active)
            context.close("secret")
            self.assertFalse(context.active)
            self.assertEqual(blobs.read(name), b"{}")
            self.assertEqual(opener.calls[-1][3], 20)
            self.assertEqual(clock.sleeps, [])

    def test_facade_all_entries_latch_even_cached_token_and_rejection(self):
        for name in ac._PrewriteClient.METHODS:
            context = RetryClock().context("create")
            class Client:
                def __getattr__(inner, method):
                    def call(*args, **kwargs):
                        self.assertFalse(context.active)
                        raise TimeoutError("offline-private-canary")
                    return call
            client = ac._PrewriteClient(Client(), context)
            with self.subTest(method=name), self.assertRaises(TimeoutError):
                getattr(client, name)()
            self.assertFalse(context.active)
            with self.assertRaises(TimeoutError):
                getattr(client, name)()
            self.assertFalse(context.active)
        with self.assertRaises(ac.CaseToolError):
            ac._PrewriteClient(object(), RetryClock().context("create")).unlisted_method()

    def test_legacy_listing_uses_raw_credential_and_elapsed_time_blocks_new_write(self):
        clock = RetryClock()
        context = clock.context("create")
        credential = RetryCredential(clock, delay=91)
        opener = RetryOpener(RetryResponse(b"<EnumerationResults><Blobs/><NextMarker/></EnumerationResults>"))
        blobs = ac.BlobIntents(ac._MeteredCredential(credential, context), opener, context=context)
        self.assertEqual(blobs.initial_paths(), [])
        self.assertEqual(opener.calls[0][3], 20)
        with self.assertRaises(ac.CaseDiagnostic) as raised:
            blobs.create(ac.intent_path(pp.episode(INSTALLATION)), b"{}")
        self.assertEqual((raised.exception.reason, raised.exception.attempts), ("budget", 0))
        self.assertEqual((len(credential.calls), len(opener.calls), clock.sleeps), (1, 1, []))
        self.assertTrue(context.active)

    def display_error(self, error):
        value = snapshot()
        if error is None:
            del value["summary"]["error"]
        else:
            value["summary"]["error"] = copy.deepcopy(error)
        self.reader.signal_data["samples"] = [pp.sample(value, pp.digest_for(pp.encode(value)))]
        result = self.tool.run("read")
        self.assertEqual((self.store.creations, self.facade.posts), (0, []))
        return result

    def test_read_discovers_only_native_evidence_without_ownership_or_writes(self):
        result = self.tool.run("read")
        self.assertEqual(result["status"], "native_evidence")
        self.assertEqual(result["binding"], self.tool.authority(INVESTIGATION, ALERT, writing=False))
        self.assertEqual(result["binding"]["installation_id"], INSTALLATION)
        expected = {key: sample()[key] for key in (
            "resource_id", "installation_id", "observed_at", "status", "failed_patch_count", "digest",
        )}
        expected["provider_error"] = {
            **snapshot()["summary"]["error"], "detail_count": 0, "details": [], "truncated": False,
        }
        self.assertEqual(result["signal"]["samples"], [expected])
        self.assertEqual(result["signal"]["sample_count"], 1)
        self.assertEqual(result["signal"]["reference"], pp.COMPUTE_URL)
        self.assertEqual(result["current_provider_observation"], {
            "installation_id": INSTALLATION, "status": "Failed", "failed_patch_count": 1,
            "provider_time": self.reader.current_summary["lastModifiedTime"],
            "collection_time": NOW.isoformat(),
            "relationship": "current summary identifies the archived installation",
        })
        self.assertLessEqual(len(ac.wire(result)), 3072)
        self.assertEqual(self.store.creations, 0)
        self.assertEqual(self.facade.posts, [])

    def test_patch_provider_error_recorded_shape_and_absence(self):
        recorded = RECORDED_INSTALLATION_SUMMARY["error"]
        result = self.display_error(recorded)
        expected = {
            "code": "1", "message": recorded["message"], "detail_count": 2,
            "details": [{"code": detail["code"], "message": detail["message"].strip()}
                        for detail in recorded["details"]], "truncated": False,
        }
        shown = result["signal"]["samples"][0]
        self.assertEqual(shown["provider_error"], expected)
        self.assertEqual(shown["provider_error"]["details"][1]["code"], "PACKAGE_MANAGER_FAILURE")
        self.assertIn("(100)", shown["provider_error"]["details"][1]["message"])
        self.assertLessEqual(len(ac.wire(result)), 2600)
        absent = self.display_error(None)
        expected_display = copy.deepcopy(result)
        expected_display["signal"]["samples"][0].update(
            provider_error=None, digest=self.reader.signal_data["samples"][0]["digest"])
        self.assertEqual(absent, expected_display)
        self.assertIsNone(absent["signal"]["samples"][0]["provider_error"])

    def test_patch_provider_error_escaped_byte_boundaries_and_normalization(self):
        for field, cap in (("code", 48), ("message", 192), ("detail_code", 48), ("detail_message", 160)):
            for ch, cost in (("a", 1), ("é", 6), ("🚀", 12), ('"', 2), ("\\", 2)):
                for extra in (0, 1):
                    with self.subTest(field=field, ch=ch, extra=extra):
                        count = cap // cost
                        error = {"code": "Unit", "message": "Root", "details": [{"code": "Detail", "message": "Body"}]}
                        target, key = (error["details"][0], field[7:]) if field.startswith("detail_") else (error, field)
                        target[key] = ch * (count + extra)
                        result = self.display_error(error)
                        projected = result["signal"]["samples"][0]["provider_error"]
                        actual = projected["details"][0][key] if field.startswith("detail_") else projected[key]
                        self.assertEqual(actual, ch * count)
                        self.assertLessEqual(len(json.dumps(actual, ensure_ascii=True)) - 2, cap)
                        self.assertEqual(projected["truncated"], bool(extra))
                        self.assertEqual(json.loads(ac.wire(result)), result)
            for text, expected in (
                (' \tquoted "message"\nC:\\logs\x00fail ', 'quoted "message" C:\\logs fail'),
                (" a\u2003b\r\n c ", "a b c"),
            ):
                with self.subTest(field=field, normalized=text):
                    error = {"code": "Unit", "message": "Root", "details": [{"code": "Detail", "message": "Body"}]}
                    target, key = (error["details"][0], field[7:]) if field.startswith("detail_") else (error, field)
                    target[key] = text
                    projected = self.display_error(error)["signal"]["samples"][0]["provider_error"]
                    actual = projected["details"][0][key] if field.startswith("detail_") else projected[key]
                    self.assertEqual(actual, expected)
                    self.assertFalse(projected["truncated"])
        projected = self.display_error({"code": "Unit", "message": "a" * 190 + "🚀"})["signal"]["samples"][0]["provider_error"]
        self.assertEqual(projected["message"], "a" * 190)
        self.assertTrue(projected["truncated"])
        projected = self.display_error({"code": "Unit", "message": "a" * 191 + " " + "🚀"})["signal"]["samples"][0]["provider_error"]
        self.assertEqual(projected["message"], "a" * 191 + " ")
        self.assertTrue(projected["truncated"])

    def test_patch_provider_error_rejects_lone_surrogate_without_writes(self):
        for field in ("code", "message", "detail_code", "detail_message"):
            with self.subTest(field=field), self.assertRaises(ac.CaseToolError):
                error = {"code": "Unit", "message": "Root", "details": [{"code": "Detail", "message": "Body"}]}
                target, key = (error["details"][0], field[7:]) if field.startswith("detail_") else (error, field)
                target[key] = "valid prefix " + "\ud800"
                self.display_error(error)
            self.assertEqual((self.store.creations, self.facade.posts), (0, []))

    def test_patch_provider_error_detail_counts_order_and_omitted_fields(self):
        for count in (0, 3, 5):
            with self.subTest(count=count):
                details = [{"code": str(index), "message": f"Detail {index}"} for index in range(count)]
                error = {"code": "Unit", "message": "Root", "details": details}
                projected = self.display_error(error)["signal"]["samples"][0]["provider_error"]
                self.assertEqual(projected, {**error, "details": details[:3], "detail_count": count, "truncated": count > 3})
        inner = {"exceptiontype": "UnitException", "errordetail": "Offline detail"}
        for location, key, value in (
            ("root", "target", ""), ("root", "innererror", inner),
            ("detail", "target", ""), ("detail", "innererror", inner), ("detail", "details", []),
        ):
            with self.subTest(location=location, omitted=key):
                error = {"code": "Unit", "message": "Root", "details": [{"code": "Detail", "message": "Body"}]}
                target = error if location == "root" else error["details"][0]
                target[key] = value
                saved = copy.deepcopy(error)
                projected = self.display_error(error)["signal"]["samples"][0]["provider_error"]
                self.assertEqual(projected, {
                    "code": "Unit", "message": "Root", "detail_count": 1,
                    "details": [{"code": "Detail", "message": "Body"}], "truncated": True,
                })
                self.assertEqual(error, saved)

    def test_patch_provider_error_maximum_display_byte_budgets(self):
        recorded = self.display_error(RECORDED_INSTALLATION_SUMMARY["error"])
        sizes = {"recorded": len(ac.wire(recorded))}
        base = copy.deepcopy(recorded)
        del base["signal"]["samples"][0]["provider_error"]
        sizes["without_provider_error"] = len(ac.wire(base))
        sizes["no_error"] = len(ac.wire(self.display_error(None)))
        mixed = '🚀é"\\' * 100
        adversarial = {"code": mixed, "message": mixed,
                       "details": [{"code": mixed, "message": mixed} for _ in range(4)]}
        at_cap = {"code": "🚀" * 4, "message": "é" * 32,
                  "details": [{"code": "é" * 8, "message": '"\\' * 40} for _ in range(3)]}
        for name, error in (("adversarial", adversarial), ("at_cap", at_cap)):
            with self.subTest(shape=name):
                result = self.display_error(error)
                summary = self.reader.signal_data["samples"][0]["snapshot"]["summary"]
                self.assertLessEqual(len(pp.encode(summary, pp.SUMMARY_LIMIT)), pp.SUMMARY_LIMIT)
                projected = result["signal"]["samples"][0]["provider_error"]
                self.assertEqual(projected["detail_count"], len(error["details"]))
                self.assertEqual(len(projected["details"]), 3)
                self.assertEqual(projected["truncated"], name == "adversarial")
                fields = [(projected["code"], 48), (projected["message"], 192)]
                fields += [(detail[key], cap) for detail in projected["details"] for key, cap in (("code", 48), ("message", 160))]
                for text, cap in fields:
                    size = len(json.dumps(text, ensure_ascii=True)) - 2
                    self.assertLessEqual(size, cap)
                    if name == "at_cap":
                        self.assertEqual(size, cap)
                sizes[name] = len(ac.wire(result))
                self.assertLessEqual(sizes[name], 2900)
        print("PATCH_PROVIDER_ERROR_SOURCE_BYTES " + json.dumps(sizes, sort_keys=True))

    def test_patch_display_canonical_budget_includes_escaping_and_fails_closed(self):
        self.assertEqual(ac.PATCH_DISPLAY_LIMIT, 3072)
        self.assertEqual((ac.PATCH_ERROR_CODE_LIMIT, ac.PATCH_ERROR_MESSAGE_LIMIT,
                          ac.PATCH_DETAIL_MESSAGE_LIMIT, ac.PATCH_DETAIL_LIMIT), (48, 192, 160, 3))
        oversized = self.tool.run("read")
        identity = "é" * 300
        oversized["binding"]["investigation_id"] = identity
        self.assertLess(len(json.dumps(oversized, separators=(",", ":"), ensure_ascii=False).encode("utf-8")), 3072)
        self.assertGreater(len(ac.wire(oversized)), 3072)
        self.reader.thread["id"] = identity
        with patch.object(ac, "_patch_native_display", wraps=ac._patch_native_display) as display, \
                self.assertRaises(ac.CaseToolError):
            self.tool.run("read", identity, ALERT)
        display.assert_called_once()
        self.assertEqual((self.store.creations, self.facade.posts), (0, []))

    def test_patch_display_current_metadata_does_not_infer_recovery_or_ordering(self):
        latest = self.reader.current_summary
        latest.update(installationActivityId="11111111-1111-4111-8111-111111111111",
                      status="Succeeded", failedPatchCount=0, startTime="2026-10-18T20:00:00Z",
                      lastModifiedTime="2026-10-18T20:01:00Z")
        result = self.tool.run("read")
        self.assertEqual(result["current_provider_observation"], {
            "installation_id": latest["installationActivityId"], "status": "Succeeded", "failed_patch_count": 0,
            "provider_time": "2026-10-18T20:01:00Z", "collection_time": NOW.isoformat(),
            "relationship": "current summary identifies another installation",
        })
        self.assertEqual(result["signal"]["samples"][0]["installation_id"], INSTALLATION)
        self.assertEqual(result["signal"]["samples"][0]["observed_at"], sample()["observed_at"])
        self.assertEqual((self.store.creations, self.facade.posts), (0, []))

    def test_patch_display_cannot_hide_sensitive_or_malformed_non_displayed_evidence(self):
        for kind in ("hidden-sensitive", "hidden-count", "hidden-time", "hidden-installation", "current-sensitive",
                     "current-invalid", "hidden-detail-fourth", "hidden-detail-tail"):
            with self.subTest(kind=kind):
                self.setUp()
                hidden = sample()
                if kind == "hidden-sensitive":
                    value = snapshot()
                    value["summary"]["error"]["message"] = "benign prefix " * 20 + SENSITIVE_CURRENT_ERRORS[0]
                    hidden = pp.sample(value, pp.digest_for(pp.encode(value)))
                elif kind == "hidden-count":
                    hidden["failed_patch_count"] = True
                elif kind == "hidden-time":
                    hidden["observed_at"] = "2026-10-18T22:04:00Z"
                elif kind == "hidden-installation":
                    hidden["installation_id"] = "11111111-1111-4111-8111-111111111111"
                elif kind.startswith("hidden-detail"):
                    value = snapshot()
                    details = [{"code": "Shown", "message": "Benign"} for _ in range(3)] if kind == "hidden-detail-fourth" else []
                    details.append({"code": "Hidden", "message": "x" * 200 + SENSITIVE_CURRENT_ERRORS[0]})
                    value["summary"]["error"]["details"] = details
                    hidden = pp.sample(value, pp.digest_for(pp.encode(value)))
                elif kind == "current-sensitive":
                    self.reader.current_summary["error"]["details"] = [
                        {"code": "Hidden", "message": "benign prefix " * 20 + SENSITIVE_CURRENT_ERRORS[1]},
                    ]
                else:
                    self.reader.current_summary["failedPatchCount"] = True
                self.reader.signal_data["samples"] = [hidden] if kind.startswith("hidden-detail") else [sample(), hidden]
                with patch.object(ac, "_patch_native_display", wraps=ac._patch_native_display) as display, \
                        self.assertRaises(ac.CaseToolError):
                    self.tool.run("read")
                display.assert_not_called()
                self.assertEqual((self.store.creations, self.facade.posts), (0, []))

    def test_patch_display_is_after_all_archive_and_current_compute_reads(self):
        first, second = snapshot(), snapshot()
        second["summary"].update(lastModifiedTime="2026-10-18T22:01:45Z", failedPatchCount=2,
                                  status="CompletedWithWarnings")
        values = {pp.digest_for(pp.encode(value)): value for value in (first, second)}
        reader = ac.AzureReader(object(), profile=ac.PATCH_PROFILE)
        self.reader.signal = reader.signal
        with patch.object(reader, "alert", return_value=self.reader.alert(ALERT)), \
                patch.object(ac, "_azure_json", return_value=console_table([first, second])), \
                patch.object(pp, "read_archive", side_effect=lambda credential, digest: copy.deepcopy(values[digest])) as archive, \
                patch.object(pp, "read_compute", return_value=pp.encode({
                    "patchStatus": {"lastPatchInstallationSummary": self.reader.current_summary}})) as compute:
            result = self.tool.run("read")
        self.assertEqual([call.args[1] for call in archive.call_args_list], list(values))
        compute.assert_called_once_with(reader.credential)
        self.assertEqual(result["signal"]["sample_count"], 2)
        self.assertEqual(len(result["signal"]["samples"]), 1)
        self.assertEqual(result["signal"]["samples"][0]["observed_at"], first["summary"]["lastModifiedTime"])
        self.assertEqual((self.store.creations, self.facade.posts), (0, []))

    def test_patch_display_is_fresh_and_does_not_replace_complete_frozen_proof(self):
        first, second = snapshot(), snapshot()
        first["summary"]["error"] = copy.deepcopy(RECORDED_INSTALLATION_SUMMARY["error"])
        first["summary"]["error"]["message"] = "complete archived error " * 100
        second["summary"].update(lastModifiedTime="2026-10-18T22:01:45Z", failedPatchCount=2,
                                  status="CompletedWithWarnings")
        self.reader.signal_data["samples"] = [pp.sample(value, pp.digest_for(pp.encode(value))) for value in (first, second)]
        self.reader.current_summary = copy.deepcopy(first["summary"])
        original = copy.deepcopy(self.reader.signal_data)
        original_bytes = pp.encode(original)
        binding = self.tool.authority(INVESTIGATION, ALERT, writing=False)
        saved_binding = copy.deepcopy(binding)
        current = copy.deepcopy(self.reader.current_summary)
        value = patch_evidence()
        value["episode_id"] = pp.episode(INSTALLATION)
        payloads = self.tool.payloads(value["episode_id"], binding, value, NOTE)
        fingerprints = {name: hashlib.sha256(ac.wire(payload)).hexdigest() for name, payload in payloads.items()}
        result = self.tool.native_context(binding)
        self.assertEqual(result["binding"], saved_binding)
        self.assertEqual((self.store.creations, self.facade.posts), (0, []))
        result["binding"]["investigation_id"] = "changed-display"
        result["signal"]["samples"][0]["status"] = "changed-display"
        result["signal"]["samples"][0]["provider_error"]["code"] = "changed-display"
        result["signal"]["samples"][0]["provider_error"]["details"][0]["message"] = "changed-display"
        result["current_provider_observation"]["status"] = "changed-display"
        self.assertEqual(binding, saved_binding)
        self.assertEqual(self.reader.signal_data, original)
        self.assertEqual(pp.encode(self.reader.signal_data), original_bytes)
        self.assertEqual(self.reader.current_summary, current)
        self.assertEqual(self.tool._patch_observation["summary"], current)
        self.create()
        frozen = self.frozen()
        self.assertEqual(frozen["payloads"], payloads)
        self.assertEqual(frozen["fingerprints"], fingerprints)
        proof = frozen["proof"]["signal"]
        self.assertEqual(proof, original)
        self.assertEqual(pp.encode(proof), original_bytes)
        for row in proof["samples"]:
            self.assertEqual(pp.archived(pp.encode(row["snapshot"]), row["digest"]), row["snapshot"])
            self.assertEqual(pp.encode(row), pp.encode(pp.sample(row["snapshot"], row["digest"])))
            self.assertEqual(row["error_details"], pp.encode(row["snapshot"]["summary"]["error"]).decode("ascii")[:1600])
            self.assertNotIn("provider_error", row)
        self.assertEqual(len(proof["samples"][0]["error_details"]), 1600)
        self.assertEqual(proof["samples"][0]["snapshot"]["summary"]["error"]["message"],
                         first["summary"]["error"]["message"])

    def test_sensitive_current_error_is_guarded_before_initial_read_output(self):
        for message in SENSITIVE_CURRENT_ERRORS:
            with self.subTest(kind=message.split(":", 1)[0]):
                self.setUp()
                self.reader.current_summary["error"] = {"code": "ChangedCurrent", "message": message}
                pp.validate_summary(self.reader.current_summary)  # Schema-valid is not content-safe.
                with self.assertRaises(ac.CaseToolError) as raised:
                    self.tool.run("read")
                self.assertEqual(str(raised.exception),
                                 "Runtime content exceeds its bound or contains suspected credentials.")
                self.assertNotIn(FAKE_CURRENT_CREDENTIAL, str(raised.exception))
                self.assertEqual((self.store.creations, self.facade.posts), (0, []))

    def sensitive_current_resume(self, action):
        for message in SENSITIVE_CURRENT_ERRORS:
            with self.subTest(action=action, kind=message.split(":", 1)[0]):
                self.setUp()
                binding = self.tool.authority(INVESTIGATION, ALERT, writing=True)
                plan = self.tool.freeze(pp.episode(INSTALLATION), binding, json.dumps(patch_evidence()), NOTE, "")
                if action == "note":
                    self.facade.unknown.add(plan["payloads"]["note"]["write_request_id"])
                    self.assertEqual(self.tool.run("create", INVESTIGATION, ALERT)["status"], "pending")
                    self.facade.unknown.clear()
                raw = self.store.read(ac.intent_path(plan["episode_id"]))
                posts = len(self.facade.posts)
                self.reader.current_summary["error"] = {"code": "ChangedCurrent", "message": message}
                pp.validate_summary(self.reader.current_summary)
                restarted = self.make()
                with self.assertRaises(ac.CaseToolError) as raised:
                    restarted.run(action, INVESTIGATION, ALERT)
                self.assertEqual(str(raised.exception),
                                 "Runtime content exceeds its bound or contains suspected credentials.")
                self.assertNotIn(FAKE_CURRENT_CREDENTIAL, str(raised.exception))
                self.assertEqual(len(self.facade.posts), posts)
                self.assertEqual(self.store.read(ac.intent_path(plan["episode_id"])), raw)
                result = restarted.run("reconcile", INVESTIGATION, ALERT)
                self.assertNotIn("current_provider_observation", result)
                self.assertNotIn(FAKE_CURRENT_CREDENTIAL, json.dumps(result))
                self.assertEqual(result[action]["write_request_id"], plan["payloads"][action]["write_request_id"])
                self.reader.current_summary["error"] = {"code": "ChangedCurrent", "message": "Benign current update."}
                result = restarted.run(action, INVESTIGATION, ALERT)
                self.assertEqual(result["status"], "persisted")
                self.assertEqual(result[action]["write_request_id"], plan["payloads"][action]["write_request_id"])
                self.assertEqual(self.store.read(ac.intent_path(plan["episode_id"])), raw)
                self.assertEqual(self.store.creations, 1)

    def test_sensitive_current_error_blocks_resumed_pending_create(self):
        self.sensitive_current_resume("create")

    def test_sensitive_current_error_blocks_resumed_pending_note(self):
        self.sensitive_current_resume("note")

    def test_native_decoder_preserves_rule_review_author_provenance(self):
        raw = native_fixture()
        self.assertEqual(ac.decode_native_incident(raw, ac.PATCH_PROFILE)["alert_ids"], [ALERT])
        for mutate in (
            lambda x: x["incidentSource"].update(incidentId=ac.RULE),
            lambda x: x["incidentSource"].update(canonicalIncidentId=None),
            lambda x: x.update(startMessageIsHuman=True),
            lambda x: x.update(agentMode="Autonomous"),
            lambda x: x["startMessage"]["author"].update(role="User"),
        ):
            wrong = copy.deepcopy(raw)
            mutate(wrong)
            with self.assertRaises(ac.CaseToolError):
                ac.decode_native_incident(wrong, ac.PATCH_PROFILE)

    def test_arm_requires_exact_query_target_and_single_canonical_dimension(self):
        reader = ac.AzureReader(object(), profile=ac.PATCH_PROFILE)
        with patch.object(ac, "_azure_json", return_value=arm_alert()):
            self.assertEqual(reader.alert(ALERT)["installation_id"], INSTALLATION)
        for dimensions in (None, [], [{"name": "installation_id", "value": INSTALLATION}] * 2,
                           [{"name": "installation_id", "value": "malformed"}],
                           [{"name": "installation_id", "value": [INSTALLATION]}],
                           [{"name": "other", "value": INSTALLATION}],
                           [{"name": "installation_id", "value": INSTALLATION, "extra": True}],
                           [{"name": "installation_id", "value": INSTALLATION},
                            {"name": "installation_id", "value": "11111111-1111-4111-8111-111111111111"}]):
            raw = arm_alert()
            raw["properties"]["context"]["context"]["condition"]["allOf"][0]["dimensions"] = dimensions
            with self.subTest(dimensions=dimensions), patch.object(ac, "_azure_json", return_value=raw), self.assertRaises(ac.CaseToolError):
                reader.alert(ALERT)
        for mutate in (
            lambda x: x["properties"]["essentials"].update(alertRule=ac.RULE),
            lambda x: x["properties"]["essentials"].update(targetResource=pp.TARGET),
            lambda x: x["properties"]["context"]["context"]["condition"]["allOf"][0].update(
                searchQuery=ac.PATCH_PROFILE.query.replace(" and provider_time <= now()", "")),
        ):
            raw = arm_alert()
            mutate(raw)
            with patch.object(ac, "_azure_json", return_value=raw), self.assertRaises(ac.CaseToolError):
                reader.alert(ALERT)

    def test_legacy_query_is_recognized_only_undimensioned_never_fresh_creation(self):
        raw = arm_alert()
        clause = raw["properties"]["context"]["context"]["condition"]["allOf"][0]
        clause.update(searchQuery=ac.LEGACY_PATCH_ALERT_QUERY, dimensions=[])
        reader = ac.AzureReader(object(), profile=ac.PATCH_PROFILE)
        with patch.object(ac, "_azure_json", return_value=raw):
            self.assertTrue(reader.alert(ALERT)["legacy_patch"])
            with self.assertRaises(ac.CaseToolError):
                reader.signal(ALERT)
        del self.reader.alerts[ALERT]["installation_id"]
        self.reader.alerts[ALERT]["legacy_patch"] = True
        with self.assertRaises(ac.CaseToolError):
            self.create()
        self.assertEqual(self.store.creations, 0)

    def test_discovery_skips_historical_undimensioned_series_not_eligible_ambiguity(self):
        reader = ac.AzureReader(object(), profile=ac.PATCH_PROFILE)
        old, fresh = native_fixture(), native_fixture()
        old["id"] = "77777777-7777-4777-8777-777777777777"
        historical = arm_alert()
        original_alert = reader.alert
        def alert(identity):
            return original_alert(ALERT) if identity == "old" else self.reader.alert(ALERT)
        def investigation(identity):
            return {**self.reader.thread, "id": identity, "alert_ids": ["old" if identity == old["id"] else ALERT]}
        def read(_credential, _scope, url):
            if url == ac.SRE_ORIGIN + "/api/v1/threads":
                return {"value": [old, fresh]}
            self.assertTrue(url.startswith("https://management.azure.com" + ALERT + "?"))
            return historical
        # Exercise the actual ARM recognizer for both exact historical queries.
        for query in (OCT22_PATCH_QUERY, ac.LEGACY_PATCH_ALERT_QUERY):
            historical["properties"]["context"]["context"]["condition"]["allOf"][0].update(
                searchQuery=query, dimensions=[])
            with self.subTest(query=query), patch.object(ac, "_azure_json", side_effect=read), \
                    patch.object(reader, "investigation", side_effect=investigation), \
                    patch.object(reader, "alert", side_effect=alert):
                self.assertEqual(reader.current_investigation(ac.stamp(COMMISSIONING["not_before"]), NOW, []), fresh["id"])
        with patch.object(ac, "_azure_json", return_value={"value": [fresh, {**fresh, "id": old["id"]}]}), \
                patch.object(reader, "investigation", side_effect=lambda identity: {**self.reader.thread, "id": identity}), \
                patch.object(reader, "alert", return_value=self.reader.alert(ALERT)), self.assertRaises(ac.CaseToolError):
            reader.current_investigation(ac.stamp(COMMISSIONING["not_before"]), NOW, [])

    def test_reader_uses_only_constructed_authenticated_digest_snapshots_and_captured_window(self):
        reader = ac.AzureReader(object(), profile=ac.PATCH_PROFILE)
        with patch.object(reader, "alert", return_value=self.reader.alert(ALERT)), \
                patch.object(ac, "_azure_json", return_value=console_table()) as query, \
                patch.object(pp, "read_archive", return_value=snapshot()) as archive, \
                patch.object(pp, "read_compute", return_value=pp.encode({
                    "patchStatus": {"lastPatchInstallationSummary": snapshot()["summary"]}})) as compute:
            result = reader.signal(ALERT)
        self.assertEqual(result["samples"], [sample()])
        self.assertEqual(query.call_args.args[2], ac.LOG_QUERY_URL)
        text = query.call_args.args[3]["query"]
        self.assertIn("provider_time > datetime(2026-10-18T21:52:00Z)", text)
        self.assertIn("provider_time <= datetime(2026-10-18T22:02:00Z)", text)
        self.assertIn("installation_id == '" + INSTALLATION + "'", text)
        self.assertNotIn("ago(", text)
        self.assertNotIn("arg(", text)
        archive.assert_called_once_with(reader.credential, sample()["digest"])
        compute.assert_called_once_with(reader.credential)
        self.assertEqual(result["current_provider_observation"]["summary"], snapshot()["summary"])
        self.assertIsNone(ac.PATCH_PROFILE.graph_query)

    def test_actual_compute_failure_malformed_or_same_guid_start_conflict_blocks_admission(self):
        good = pp.encode({"patchStatus": {"lastPatchInstallationSummary": snapshot()["summary"]}})
        wrong = copy.deepcopy(snapshot()["summary"])
        wrong["startTime"] = "2026-10-18T22:00:00.0000001Z"
        invalid_count = copy.deepcopy(snapshot()["summary"])
        invalid_count["failedPatchCount"] = True
        for response in (
            pp.ProviderError("Offline transport/auth failure"),
            good.replace(b'"patchStatus":', b'"patchStatus":{},"patchStatus":'),
            pp.encode({"patchStatus": {}}),
            pp.encode({"partialError": {}, "patchStatus": {"lastPatchInstallationSummary": snapshot()["summary"]}}),
            pp.encode({"patchStatus": {"lastPatchInstallationSummary": invalid_count}}),
            pp.encode({"patchStatus": {"lastPatchInstallationSummary": wrong}}),
        ):
            self.setUp()
            reader = ac.AzureReader(object(), profile=ac.PATCH_PROFILE)
            self.reader.signal = reader.signal
            with self.subTest(response=str(response)[:100]), \
                    patch.object(reader, "alert", return_value=self.reader.alert(ALERT)), \
                    patch.object(ac, "_azure_json", return_value=console_table()), \
                    patch.object(pp, "read_archive", return_value=snapshot()), \
                    patch.object(pp, "read_compute",
                                 **({"side_effect": response} if isinstance(response, Exception)
                                    else {"return_value": response})) as compute, \
                    self.assertRaises(ac.CaseToolError):
                self.create()
            compute.assert_called_once_with(reader.credential)
            self.assertEqual((self.store.creations, self.facade.posts), (0, []))

    def test_current_state_changes_never_replace_original_failure_or_claim_supersession(self):
        for updates in (
            {"status": "Succeeded", "failedPatchCount": 0, "lastModifiedTime": "2026-10-18T22:01:10Z",
             "startTime": "2026-10-18T22:01:00+00:00"},
            {"status": "CompletedWithWarnings", "failedPatchCount": 2,
             "error": {"code": "Changed", "message": "Same timestamp update."}},
            {"installationActivityId": "11111111-1111-4111-8111-111111111111", "status": "Unknown",
             "failedPatchCount": 0, "startTime": "2026-10-18T20:00:00Z",
             "lastModifiedTime": "2026-10-18T20:01:00Z"},
        ):
            self.setUp()
            self.reader.current_summary.update(updates)
            with self.subTest(updates=updates):
                result = self.create()
                frozen = self.frozen()
                self.assertEqual(frozen["proof"]["signal"]["samples"], [sample()])
                self.assertNotIn("current_provider_observation", frozen["proof"]["signal"])
                self.assertNotIn("current_provider_observation", frozen)
                self.assertEqual(result["current_provider_observation"]["summary"], self.reader.current_summary)
                self.assertEqual(frozen["payloads"]["create"]["write_request_id"], result["create"]["write_request_id"])
                if self.reader.current_summary["installationActivityId"] != INSTALLATION:
                    self.assertEqual(result["current_provider_observation"]["relationship"],
                                     "current summary identifies another installation")

    def test_pending_owner_restart_requires_real_compute_before_original_not_applied_submissions(self):
        binding = self.tool.authority(INVESTIGATION, ALERT, writing=True)
        plan = self.tool.freeze(pp.episode(INSTALLATION), binding, json.dumps(patch_evidence()), NOTE, "")
        raw = self.store.read(ac.intent_path(plan["episode_id"]))
        reader = ac.AzureReader(object(), profile=ac.PATCH_PROFILE)
        self.reader.patch_current = reader.patch_current
        restarted = self.make()
        with patch.object(pp, "read_compute", side_effect=pp.ProviderError("Offline unavailable")) as compute:
            with self.assertRaises(ac.CaseToolError):
                restarted.run("create", INVESTIGATION, ALERT)
            compute.assert_called_once_with(reader.credential)
            self.assertEqual(self.facade.posts, [])
            result = restarted.run("reconcile", INVESTIGATION, ALERT)
            self.assertEqual(result["create"]["outcome"], "not_applied")
            self.assertEqual(result["create"]["write_request_id"], plan["payloads"]["create"]["write_request_id"])
            self.assertEqual(compute.call_count, 1)
        self.assertEqual(self.store.read(ac.intent_path(plan["episode_id"])), raw)
        latest = {**snapshot()["summary"], "status": "Succeeded", "failedPatchCount": 0}
        with patch.object(pp, "read_compute", return_value=pp.encode({
                "patchStatus": {"lastPatchInstallationSummary": latest}})) as compute:
            result = restarted.run("create", INVESTIGATION, ALERT)
        compute.assert_called_once_with(reader.credential)  # Reused for create/note in this invocation only.
        self.assertEqual(result["status"], "persisted")
        for name in ("create", "note"):
            self.assertEqual(result[name]["write_request_id"], plan["payloads"][name]["write_request_id"])
        self.assertEqual(self.store.read(ac.intent_path(plan["episode_id"])), raw)
        self.assertEqual((self.store.creations, self.facade.state["case_count"], self.facade.state["note_count"]), (1, 1, 1))

    def test_get_only_owned_and_legacy_operations_do_not_require_compute(self):
        self.create()
        reader = ac.AzureReader(object(), profile=ac.PATCH_PROFILE)
        self.reader.patch_current = reader.patch_current
        with patch.object(pp, "read_compute", side_effect=pp.ProviderError("Offline unavailable")) as compute:
            for action in ("read", "reconcile", "create"):
                self.assertEqual(self.tool.run(action, INVESTIGATION, ALERT)["status"], "persisted")
            self.assertEqual(self.create(note=NOTE + " changed")["status"], "owned")
        compute.assert_not_called()
        self.setUp()
        plan = self.legacy(query=OCT22_PATCH_QUERY)
        self.reader.patch_current = reader.patch_current
        self.reader.alerts[ALERT].pop("installation_id")
        self.reader.alerts[ALERT]["legacy_patch"] = True
        with patch.object(pp, "read_compute", side_effect=pp.ProviderError("Offline unavailable")) as compute:
            for action in ("read", "reconcile", "create"):
                self.assertEqual(self.tool.run(action, INVESTIGATION, ALERT)["episode_id"], plan["episode_id"])
        compute.assert_not_called()
        self.assertEqual(self.facade.posts, [])

    def test_resumed_compute_latency_cannot_extend_original_submission_freshness(self):
        binding = self.tool.authority(INVESTIGATION, ALERT, writing=True)
        plan = self.tool.freeze(pp.episode(INSTALLATION), binding, json.dumps(patch_evidence()), NOTE, "")
        raw = self.store.read(ac.intent_path(plan["episode_id"]))
        reader = ac.AzureReader(object(), profile=ac.PATCH_PROFILE)
        self.reader.patch_current = reader.patch_current
        restarted, current = self.make(), [NOW]
        restarted.clock = lambda: current[0]
        def delayed_compute(_credential):
            current[0] += timedelta(minutes=20)
            return pp.encode({"patchStatus": {"lastPatchInstallationSummary": snapshot()["summary"]}})
        with patch.object(pp, "read_compute", side_effect=delayed_compute) as compute, self.assertRaises(ac.CaseToolError):
            restarted.run("create", INVESTIGATION, ALERT)
        compute.assert_called_once()
        self.assertEqual(self.facade.posts, [])
        self.assertEqual(self.store.read(ac.intent_path(plan["episode_id"])), raw)

    def test_existing_case_pending_note_requires_compute_and_retains_original_note_identity(self):
        binding = self.tool.authority(INVESTIGATION, ALERT, writing=True)
        plan = self.tool.freeze(pp.episode(INSTALLATION), binding, json.dumps(patch_evidence()), NOTE, "")
        raw = self.store.read(ac.intent_path(plan["episode_id"]))
        note_id = plan["payloads"]["note"]["write_request_id"]
        self.facade.unknown.add(note_id)
        self.assertEqual(self.tool.run("create", INVESTIGATION, ALERT)["status"], "pending")
        self.assertEqual(len(self.facade.posts), 1)
        self.facade.unknown.clear()
        reader = ac.AzureReader(object(), profile=ac.PATCH_PROFILE)
        self.reader.patch_current = reader.patch_current
        restarted = self.make()
        with patch.object(pp, "read_compute", side_effect=pp.ProviderError("Offline unavailable")) as compute:
            with self.assertRaises(ac.CaseToolError):
                restarted.run("note", INVESTIGATION, ALERT)
            self.assertEqual(restarted.run("reconcile", INVESTIGATION, ALERT)["note"]["outcome"], "not_applied")
        compute.assert_called_once_with(reader.credential)
        self.assertEqual(len(self.facade.posts), 1)
        self.assertEqual(self.store.read(ac.intent_path(plan["episode_id"])), raw)
        latest = {**snapshot()["summary"], "installationActivityId": "11111111-1111-4111-8111-111111111111"}
        with patch.object(pp, "read_compute", return_value=pp.encode({
                "patchStatus": {"lastPatchInstallationSummary": latest}})) as compute:
            result = restarted.run("note", INVESTIGATION, ALERT)
        compute.assert_called_once_with(reader.credential)
        self.assertEqual(result["status"], "persisted")
        self.assertEqual(result["note"]["write_request_id"], note_id)
        self.assertEqual(result["current_provider_observation"]["relationship"],
                         "current summary identifies another installation")
        self.assertEqual((self.facade.state["case_count"], self.facade.state["note_count"]), (1, 1))
        self.assertEqual(self.store.read(ac.intent_path(plan["episode_id"])), raw)

    def test_reader_rejects_empty_partial_malformed_mutated_or_different_installation(self):
        reader = ac.AzureReader(object(), profile=ac.PATCH_PROFILE)
        raw = console_table()
        bad = [{"error": "partial"}, console_table([]), {**raw, "tables": raw["tables"] * 2}]
        for index, value in ((1, ac.RESOURCE), (3, "11111111-1111-4111-8111-111111111111"),
                             (5, True), (6, "https://untrusted.invalid/archive"),
                             (7, raw["tables"][0]["rows"][0][7].replace('"version":"1.0"', '"version":"1.0","version":"1.0"'))):
            wrong = copy.deepcopy(raw)
            wrong["tables"][0]["rows"][0][index] = value
            bad.append(wrong)
        wrong = copy.deepcopy(raw)
        wrong["tables"][0]["rows"] *= 101
        bad.append(wrong)
        for value in bad:
            with self.subTest(value=str(value)[:100]), \
                    patch.object(reader, "alert", return_value=self.reader.alert(ALERT)), \
                    patch.object(ac, "_azure_json", return_value=value), \
                    patch.object(pp, "read_archive", return_value=snapshot()), \
                    patch.object(pp, "read_compute", return_value=pp.encode({
                        "patchStatus": {"lastPatchInstallationSummary": snapshot()["summary"]}})), \
                    self.assertRaises(ac.CaseToolError):
                reader.signal(ALERT)
        with patch.object(reader, "alert", return_value=self.reader.alert(ALERT)), \
                patch.object(ac, "_azure_json", return_value=raw), \
                patch.object(pp, "read_archive", side_effect=pp.ProviderError("missing/mutated")), self.assertRaises(ac.CaseToolError):
            reader.signal(ALERT)

    def test_every_sample_same_installation_and_actual_native_ten_minute_window(self):
        for mutate in (
            lambda x: x.update(resource_id=ac.RESOURCE),
            lambda x: x.update(installation_id="11111111-1111-4111-8111-111111111111"),
            lambda x: x.update(observed_at="2026-10-18T22:04:00Z"),
            lambda x: x.update(failed_patch_count=True), lambda x: x.update(status="Succeeded"),
            lambda x: x["snapshot"]["summary"].update(failedPatchCount=2),
        ):
            row = sample()
            mutate(row)
            self.reader.signal_data["samples"] = [sample(), row]
            with self.assertRaises(ac.CaseToolError):
                self.create()
            self.assertEqual(self.store.creations, 0)
        self.reader.signal_data["samples"] = [sample()] * 100
        self.assertEqual(self.create()["status"], "persisted")

    def test_old_stale_failure_or_later_provider_time_cannot_fill_captured_window(self):
        for modified in ("2026-10-18T22:02:30Z", "2026-10-18T21:50:00Z"):
            value = snapshot()
            value["summary"].update(lastModifiedTime=modified, startTime="2026-10-18T21:45:00Z")
            value["collection_time"] = modified
            self.reader.signal_data["samples"] = [pp.sample(value, pp.digest_for(pp.encode(value)))]
            with self.assertRaises(ac.CaseToolError):
                self.create()
            self.assertEqual(self.facade.posts, [])

    def test_one_complete_version_two_intent_owns_case_and_findings(self):
        result = self.create()
        self.assertEqual(result["status"], "persisted")
        self.assertIn("journal_sys_id", result["note"])
        self.assertEqual(self.frozen()["version"], 2)
        self.assertNotEqual(result["episode_id"], ac.episode_for(INVESTIGATION))
        self.assertEqual(self.create()["status"], "persisted")
        self.assertEqual((self.facade.state["case_count"], self.facade.state["note_count"]), (1, 1))

    def test_changed_payload_or_competing_investigation_is_owned_get_only(self):
        self.create()
        original, posts = self.store.read(ac.intent_path(pp.episode(INSTALLATION))), len(self.facade.posts)
        self.assertEqual(self.create(note=NOTE + " changed")["status"], "owned")
        self.reader.thread["id"] = "other-native-investigation"
        result = self.create(investigation="other-native-investigation")
        self.assertEqual(result["status"], "owned")
        self.assertEqual(result["binding"]["investigation_id"], INVESTIGATION)
        self.assertEqual(len(self.facade.posts), posts)
        self.assertEqual(self.store.read(ac.intent_path(pp.episode(INSTALLATION))), original)

    def test_terminal_update_same_installation_keeps_original_proof_payload_and_ids(self):
        result = self.create()
        original = self.store.read(ac.intent_path(pp.episode(INSTALLATION)))
        updated = snapshot()
        updated["summary"].update(lastModifiedTime="2026-10-18T22:01:45Z", failedPatchCount=2,
                                  status="CompletedWithWarnings", error={"code": "Updated", "message": "Offline"})
        self.reader.signal_data["samples"] = [pp.sample(updated, pp.digest_for(pp.encode(updated)))]
        again = self.tool.run("create", INVESTIGATION, ALERT)
        self.assertEqual(again["create"]["write_request_id"], result["create"]["write_request_id"])
        self.assertEqual(self.store.read(ac.intent_path(pp.episode(INSTALLATION))), original)
        self.assertEqual(self.facade.state["case_count"], 1)

    def test_different_installations_same_merged_investigation_get_distinct_episodes(self):
        first = self.create()
        second_id = "11111111-1111-4111-8111-111111111111"
        updated = snapshot()
        updated["summary"]["installationActivityId"] = second_id
        self.reader.alerts[ALERT]["installation_id"] = second_id
        self.reader.signal_data["samples"] = [pp.sample(updated, pp.digest_for(pp.encode(updated)))]
        second = self.create()
        self.assertNotEqual(first["episode_id"], second["episode_id"])
        self.assertEqual(self.facade.state["case_count"], 2)

    def test_distinct_investigation_race_retains_atomic_winner_and_one_case(self):
        second_reader = PatchReader()
        second_reader.thread["id"] = "second-native-investigation"
        second = self.make(second_reader)
        self.store.barrier = threading.Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.create), pool.submit(self.create, tool=second, investigation=second_reader.thread["id"])]
            results = [future.result(timeout=15) for future in futures]
        self.assertEqual(sorted(r["status"] for r in results), ["owned", "persisted"])
        self.assertEqual(self.store.creations, 1)
        self.assertEqual((self.facade.state["case_count"], self.facade.state["note_count"]), (1, 1))
        self.assertEqual({r["binding"]["investigation_id"] for r in results},
                         {self.frozen()["binding"]["investigation_id"]})

    def test_lost_blob_and_facade_responses_reconcile_original_never_new_episode(self):
        self.store.uncertain = True
        self.facade.loss = {"create", "note"}
        self.assertEqual(self.create()["status"], "persisted")
        self.assertEqual(self.make().run("reconcile", INVESTIGATION, ALERT)["status"], "persisted")
        self.assertEqual((self.store.creations, self.facade.state["case_count"], self.facade.state["note_count"]), (1, 1, 1))
        self.facade.unknown.add(self.frozen()["payloads"]["create"]["write_request_id"])
        posts = len(self.facade.posts)
        self.assertEqual(self.create()["status"], "pending")
        self.assertEqual(len(self.facade.posts), posts)

    def test_lost_create_without_object_blocks_no_facade_and_restart_uses_same_identity(self):
        self.store.lose_create = True
        with self.assertRaises(ac.CaseToolError):
            self.create()
        self.assertEqual(self.facade.posts, [])
        self.store.lose_create = False
        self.assertEqual(self.create(tool=self.make())["episode_id"], pp.episode(INSTALLATION))

    def test_persistent_store_reopens_after_restart_with_identical_original_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            self.store = Store(directory)
            self.facade.store = self.store
            self.tool = self.make()
            self.create()
            before = self.store.read(ac.intent_path(pp.episode(INSTALLATION)))
            reopened = self.make(store=Store(directory))
            self.assertEqual(reopened.run("reconcile", INVESTIGATION, ALERT)["status"], "persisted")
            self.assertEqual(reopened.intents.read(ac.intent_path(pp.episode(INSTALLATION))), before)
            self.assertEqual(self.facade.state["case_count"], 1)

    def test_matching_legacy_v1_blocks_new_owner_and_still_reconciles(self):
        plan = self.legacy()
        self.assertEqual(self.create()["status"], "owned")
        self.assertEqual(self.store.creations, 1)
        self.assertEqual(self.facade.posts, [])
        self.reader.alerts[ALERT].pop("installation_id")
        self.reader.alerts[ALERT]["legacy_patch"] = True
        result = self.tool.run("reconcile", INVESTIGATION, ALERT)
        self.assertEqual(result["episode_id"], plan["episode_id"])
        self.assertEqual(result["create"]["outcome"], "not_applied")
        self.assertEqual(self.create()["status"], "owned")
        self.assertEqual(self.facade.posts, [])

    def test_oct22_legacy_query_is_exact_frozen_v1_ownership_not_fresh_authority(self):
        self.assertEqual(ac.LEGACY_PATCH_OCT22_QUERY, OCT22_PATCH_QUERY)
        self.assertEqual(set(ac.LEGACY_PATCH_ALERT_QUERIES), {OCT22_PATCH_QUERY, ac.LEGACY_PATCH_ALERT_QUERY})
        plan = self.legacy(query=OCT22_PATCH_QUERY)
        raw = self.store.read(ac.intent_path(plan["episode_id"]))
        self.assertEqual(self.tool.load(plan["episode_id"]), plan)
        self.assertEqual(self.tool.legacy_patch_owner(INSTALLATION), plan)
        # Matching historical ownership blocks a new v2 episode, GET-only.
        self.assertEqual(self.create()["status"], "owned")
        self.assertEqual(self.store.creations, 1)
        self.reader.alerts[ALERT].pop("installation_id")
        self.reader.alerts[ALERT]["legacy_patch"] = True
        for action in ("read", "reconcile", "create"):
            result = (self.create() if action == "create"
                      else self.tool.run(action, INVESTIGATION, ALERT))
            self.assertEqual(result["episode_id"], plan["episode_id"])
            self.assertEqual(result["create"]["write_request_id"], plan["payloads"]["create"]["write_request_id"])
        self.assertEqual(self.facade.posts, [])
        self.assertEqual(self.store.read(ac.intent_path(plan["episode_id"])), raw)

    def test_oct22_legacy_owner_of_different_installation_does_not_block_new_v2_admission(self):
        plan = self.legacy(query=OCT22_PATCH_QUERY,
                           installation_id="22222222-2222-4222-8222-222222222222")
        raw = self.store.read(ac.intent_path(plan["episode_id"]))
        self.assertIsNone(self.tool.legacy_patch_owner(INSTALLATION))
        result = self.create()
        self.assertEqual(result["status"], "persisted")
        self.assertEqual(result["episode_id"], pp.episode(INSTALLATION))
        self.assertNotEqual(result["episode_id"], plan["episode_id"])
        self.assertEqual(self.store.creations, 2)
        self.assertEqual(len(self.facade.posts), 2)
        self.assertEqual(self.facade.state["case_count"], 1)
        self.assertEqual(self.store.read(ac.intent_path(plan["episode_id"])), raw)

    def test_legacy_allowlist_does_not_admit_hybrid_changed_or_unbounded_queries(self):
        unknown = (
            OCT22_PATCH_QUERY.replace("ago(10m)", "ago(20m)"),
            OCT22_PATCH_QUERY.replace(" > 0", " >= 0"),
            OCT22_PATCH_QUERY.replace(ac.PATCH_RESOURCE, ac.RESOURCE),
            OCT22_PATCH_QUERY + " | take 1",
            OCT22_PATCH_QUERY.replace("| project resource_id=", "| project TimeGenerated=observed_at, resource_id="),
            OCT22_PATCH_QUERY.replace("observed_at > ago(10m)", "observed_at > ago(10m) and observed_at <= now()"),
        )
        for query in unknown:
            self.setUp()
            plan = self.legacy(query=query)
            with self.subTest(query=query), self.assertRaises(ac.CaseToolError):
                self.tool.load(plan["episode_id"])
            with self.assertRaises(ac.CaseToolError):
                self.create()
            raw = arm_alert()
            clause = raw["properties"]["context"]["context"]["condition"]["allOf"][0]
            clause.update(searchQuery=query, dimensions=[])
            with patch.object(ac, "_azure_json", return_value=raw), self.assertRaises(ac.CaseToolError):
                ac.AzureReader(object(), profile=ac.PATCH_PROFILE).alert(ALERT)
            self.assertEqual(self.facade.posts, [])

    def test_each_recognized_legacy_arm_series_is_undimensioned_and_cannot_create_new_case(self):
        for query in (OCT22_PATCH_QUERY, ac.LEGACY_PATCH_ALERT_QUERY):
            self.setUp()
            raw = arm_alert()
            clause = raw["properties"]["context"]["context"]["condition"]["allOf"][0]
            clause.update(searchQuery=query, dimensions=[])
            reader = ac.AzureReader(object(), profile=ac.PATCH_PROFILE)
            with self.subTest(query=query), patch.object(ac, "_azure_json", return_value=raw):
                self.assertTrue(reader.alert(ALERT)["legacy_patch"])
                with self.assertRaises(ac.CaseToolError):
                    reader.signal(ALERT)
                clause["dimensions"] = [{"name": "installation_id", "value": INSTALLATION}]
                with self.assertRaises(ac.CaseToolError):
                    reader.alert(ALERT)
            self.reader.alerts[ALERT].pop("installation_id")
            self.reader.alerts[ALERT]["legacy_patch"] = True
            with self.assertRaises(ac.CaseToolError):
                self.create()
            self.assertEqual(self.store.creations, 0)
            self.assertEqual(self.facade.posts, [])

    def test_legacy_query_never_validates_v2_proof_or_fresh_dimensioned_evidence(self):
        self.create()
        for query in (OCT22_PATCH_QUERY, ac.LEGACY_PATCH_ALERT_QUERY):
            value = copy.deepcopy(self.frozen())
            value["proof"]["signal"]["query"] = query
            with self.subTest(query=query), self.assertRaises(ac.CaseToolError):
                self.tool.validate_intent(value, value["episode_id"], "")
        self.setUp()
        for query in (OCT22_PATCH_QUERY, ac.LEGACY_PATCH_ALERT_QUERY):
            self.reader.signal_data["query"] = query
            with self.subTest(query=query), self.assertRaises(ac.CaseToolError):
                self.create()
            self.assertEqual(self.store.creations, 0)
            self.assertEqual(self.facade.posts, [])

    def test_legacy_multiowner_errors_caps_or_corruption_fail_closed(self):
        for mode in ("multiowner", "error", "truncated", "corrupt"):
            self.setUp()
            if mode == "multiowner":
                self.legacy()
                self.legacy("other-legacy-investigation")
            elif mode == "error":
                self.store.initial_paths = lambda: (_ for _ in ()).throw(OSError("fixture"))
            elif mode == "truncated":
                self.store.initial_paths = lambda: ["invalid"] * 33
            else:
                self.store.create(ac.intent_path(ac.episode_for("corrupt")), b"{}")
            with self.subTest(mode=mode), self.assertRaises(ac.CaseToolError):
                self.create()
            self.assertEqual(self.facade.posts, [])

    def test_other_family_repair_recovery_foreign_source_or_native_target_cannot_write(self):
        for mutate in (
            lambda x: x.update(signal_family="filesystem"), lambda x: x.update(mode="proactive"),
            lambda x: x["verification"].update(recovery_owner="agent"),
            lambda x: x["evidence"][1].update(source="AzureMonitor"),
        ):
            value = patch_evidence()
            mutate(value)
            with self.assertRaises(ac.CaseToolError):
                self.create(value)
            self.assertEqual(self.store.creations, 0)
        self.reader.thread["resource_id"] = ac.RESOURCE
        with self.assertRaises(ac.CaseToolError):
            self.create()

    def test_incomplete_or_failed_provider_read_is_explicit(self):
        self.reader.signal_data["samples"] = []
        with self.assertRaises(ac.CaseToolError):
            self.create()
        with patch.object(self.reader, "signal", side_effect=ac.CaseToolError("read failed")), self.assertRaises(ac.CaseToolError):
            self.create()
        self.assertEqual(self.facade.posts, [])

    def test_slow_evidence_and_legacy_scan_cannot_renew_admission_freshness(self):
        for operation in ("signal", "initial_paths"):
            self.setUp()
            clock = [NOW]
            self.tool.clock = lambda: clock[0]
            target = self.reader if operation == "signal" else self.store
            original = getattr(target, operation)
            def slow(*args):
                result = original(*args)
                clock[0] += timedelta(minutes=20)
                return result
            with self.subTest(operation=operation), patch.object(target, operation, side_effect=slow), \
                    self.assertRaises(ac.CaseToolError):
                self.create()
            self.assertEqual(self.store.creations, 0)
            self.assertEqual(self.facade.posts, [])

    def test_authentication_latency_cannot_submit_after_freshness_expires(self):
        clock = [NOW]
        self.tool.clock = lambda: clock[0]
        def delayed_token():
            clock[0] += timedelta(minutes=20)
            return "offline-fixture"
        self.tool.client = sn.CaseClient(delayed_token, transport=self.facade)
        with self.assertRaises((ac.CaseToolError, sn.ServiceNowError)):
            self.create()
        self.assertEqual(self.store.creations, 1)
        self.assertEqual(self.facade.posts, [])


if __name__ == "__main__":
    unittest.main()
