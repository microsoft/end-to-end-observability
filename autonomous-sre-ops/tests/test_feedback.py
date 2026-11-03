"""Offline feedback tests; fake notes are not live engineer-update acceptance."""

import copy
import hashlib
import io
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from unittest.mock import patch
from uuid import uuid4

from operations import agent_case as ac
from operations.feedback import (
    ACTIVE, Bridge, Conflict, FeedbackError, RejectedDelivery, UnknownDelivery, inspect_intent,
)
from operations.feedback_runtime import AGENT_ID, Native
from operations.servicenow import AmbiguousWriteError, CaseClient, OwnedCase
from test_agent_case import ALERT, NOTE, REFERENCE, evidence, make

THREAD = "12345678-1234-4234-8234-abcdef012345"
EVENT = "b" * 32
# The shipped agent_case.NATIVE_RULE_ID is None until commissioned (fail closed), so a disk-profile
# native incident carries this canonical id and the module patches NATIVE_RULE_ID to it.
DISK_CANONICAL_ID = "azmon:v2:arm-rule:" + "B" * 64
_native_rule_id_patch = None


def setUpModule():
    global _native_rule_id_patch
    _native_rule_id_patch = patch.object(ac, "NATIVE_RULE_ID", DISK_CANONICAL_ID)
    _native_rule_id_patch.start()


def tearDownModule():
    _native_rule_id_patch.stop()


def intent_fixture():
    with patch("test_agent_case.INVESTIGATION", THREAD):
        tool, _, store, _ = make()
        tool.run("create", THREAD, ALERT, json.dumps(evidence()), NOTE)
        return ac.decode(store.read(ac.intent_path(ac.episode_for(THREAD))))


def native_fixture():
    return {
        "id": THREAD, "source": "Incident", "threadOrigin": "Trigger",
        "startMessageIsHuman": None, "type": "Prod", "agentMode": "Review",
        "startMessage": {"author": {"role": "SREAgent", "userId": "agent-default"}},
        "incidentSource": {"incidentType": "AzMonitor", "incidentId": ac.RULE,
                           "canonicalIncidentId": DISK_CANONICAL_ID},
        "incidentDetails": {"filterId": "asre-azure-monitor-review", "incidentPriority": "Sev1"},
        "status": {"incidentStatus": {"incidentId": ALERT.split("/")[-1], "status": "resolved"}},
    }


class Store:
    def __init__(self):
        self.rows, self.lock, self.version = {}, threading.RLock(), 0

    def get(self, episode, key):
        with self.lock:
            return copy.deepcopy(self.rows.get((episode, key)))

    def create(self, episode, key, values):
        with self.lock:
            if (episode, key) not in self.rows:
                self.version += 1
                self.rows[episode, key] = {**copy.deepcopy(values), "_etag": self.version}
            return self.get(episode, key)

    def change(self, episode, key, previous, values):
        with self.lock:
            if self.rows[episode, key]["_etag"] != previous["_etag"]:
                raise Conflict("Fixture conditional write conflict.")
            self.version += 1
            self.rows[episode, key].update(copy.deepcopy(values), _etag=self.version)
            return self.get(episode, key)

    def pending(self, episode):
        with self.lock:
            return [copy.deepcopy(row) for (ep, _), row in self.rows.items()
                    if ep == episode and row.get("status") in ACTIVE]

    def transition(self, episode, previous, current, event):
        with self.lock:
            if (
                self.rows[episode, "_state"]["_etag"] != previous["_etag"]
                or (episode, event["event_id"]) in self.rows
            ):
                raise Conflict("Fixture transaction conflict.")
            self.change(episode, "_state", previous, current)
            self.create(episode, event["event_id"], event)


class NativeFake:
    def __init__(self):
        self.detail, self.value, self.state = native_fixture(), [], "Idle"
        self.posts, self.mode, self.barrier = 0, "normal", None

    def thread(self, thread):
        assert thread == THREAD
        return copy.deepcopy(self.detail)

    def messages(self, thread):
        assert thread == THREAD
        return {"value": copy.deepcopy(self.value), "state": self.state}

    def prepare(self, thread, message):
        assert thread == THREAD
        if self.barrier:
            self.barrier.wait(timeout=10)

        def submit():
            self.posts += 1
            if self.mode == "rejected":
                raise RejectedDelivery("Fixture HTTP 403.")
            if self.mode == "timeout_before":
                raise UnknownDelivery("Fixture timeout before native persistence.")
            if self.mode == "crash_before":
                raise KeyboardInterrupt()
            if self.mode != "success_without_receipt":
                self.value.append({"id": str(uuid4()), "text": message, "author": {"role": "User"}})
            if self.mode == "timeout_after":
                raise UnknownDelivery("Fixture timeout after native persistence.")
            if self.mode == "crash_after":
                raise KeyboardInterrupt()
        return submit


class Client:
    def __init__(self):
        self.context = {
            "investigation_reference": THREAD, "state": {"value": "1", "label": "New"},
            "revision": "1", "updated_at": "2026-10-22 17:00:00",
        }
        self.events = [{
            "event_id": EVENT, "kind": "work_note", "actor": "unit.fixture.operator",
            "observed_at": "2026-10-22 17:00:00", "content": "Unit fixture: please reassess this symptom.",
            "disposition": "pending_delivery",
        }]
        self.acks, self.receipts, self.lose_ack = 0, {}, False

    def reconcile(self, episode, write):
        return copy.deepcopy(self.receipts.get(write, {"outcome": "not_applied"}))

    def read_case(self, episode, resource, reference):
        assert reference == REFERENCE
        return OwnedCase(episode, resource.lower(), copy.deepcopy(reference), self.context["state"]["value"])

    def feedback_pages(self, case):
        yield {"context": copy.deepcopy(self.context), "events": copy.deepcopy(self.events)}

    def acknowledge(self, case, payload):
        self.acks += 1
        result = {"outcome": "applied", "case_reference": copy.deepcopy(case.reference),
                  "event_id": payload["event_id"], "delivery": "received_not_answered"}
        self.receipts[payload["write_request_id"]] = result
        self.events = [event for event in self.events if event["event_id"] != payload["event_id"]]
        if self.lose_ack:
            raise AmbiguousWriteError(case.episode_id, payload["write_request_id"])
        return result


class PatchFeedbackIntentTests(unittest.TestCase):
    def setUp(self):
        from tests.test_agent_case import COMMISSIONING, NOW, Store as IntentStore
        from tests.test_patch_case import NOTE as PATCH_NOTE, PatchFacade, PatchReader, patch_evidence
        self.reader, self.store = PatchReader(), IntentStore()
        self.reader.thread["id"] = THREAD
        client = CaseClient(lambda: "offline-fixture", transport=PatchFacade(self.store))
        self.tool = ac.CaseTool(
            self.reader, {**copy.deepcopy(COMMISSIONING), "profiles": ["disk", "patch-installation"]},
            self.store, client, lambda: NOW,
            policy={"version": "1.0", "minimum_not_before": "2026-10-18T21:15:23.835Z"},
            profile="patch-installation",
        )
        self.tool.run("create", THREAD, ALERT, json.dumps(patch_evidence()), PATCH_NOTE)
        self.intent = self.tool.load(ac.patch.episode(self.reader.alerts[ALERT]["installation_id"]))

    def test_v2_feedback_binds_original_canonical_thread_and_installation(self):
        result = inspect_intent(self.intent)
        self.assertEqual(result.thread_id, THREAD)
        self.assertEqual(result.profile, ac.PATCH_PROFILE)
        self.assertEqual(result.episode_id, self.intent["episode_id"])
        self.assertNotEqual(result.episode_id, ac.episode_for(THREAD))
        native, client = NativeFake(), Client()
        native.detail["incidentSource"]["incidentId"] = ac.PATCH_PROFILE.rule_id
        client.receipts[result.create_write_id] = {"outcome": "applied", "case_reference": copy.deepcopy(REFERENCE)}
        binding = Bridge(client, native, Store()).bind(self.intent)
        self.assertEqual(binding.thread_id, THREAD)
        self.assertEqual(binding.episode_id, result.episode_id)

    def test_v2_wrong_dimension_mutated_snapshot_or_unknown_format_cannot_bind(self):
        for mutate in (
            lambda x: x["binding"].update(installation_id="11111111-1111-4111-8111-111111111111"),
            lambda x: x["proof"]["signal"]["samples"][0]["snapshot"]["summary"].update(failedPatchCount=2),
            lambda x: x.update(version=3),
            lambda x: x["binding"].update(resource_id=ac.RESOURCE, rule_id=ac.RULE),
        ):
            value = copy.deepcopy(self.intent)
            mutate(value)
            with self.assertRaises((FeedbackError, ac.CaseToolError)):
                inspect_intent(value)

    def test_legacy_patch_v1_keeps_original_thread_episode_and_payload_semantics(self):
        from tests.test_patch_case import NOTE as PATCH_NOTE
        binding = {k: v for k, v in self.intent["binding"].items() if k != "installation_id"}
        episode = ac.episode_for(THREAD)
        evidence = copy.deepcopy(self.intent["payloads"]["create"]["evidence"])
        evidence["episode_id"] = episode
        evidence["evidence"][1]["reference"] = ac.GRAPH_QUERY_URL
        payloads = self.tool.payloads(episode, binding, evidence, PATCH_NOTE)
        legacy = {
            **copy.deepcopy(self.intent), "version": 1, "episode_id": episode, "binding": binding,
            "payloads": payloads,
            "fingerprints": {name: hashlib.sha256(ac.wire(payload)).hexdigest() for name, payload in payloads.items()},
        }
        signal = legacy["proof"]["signal"]
        signal["samples"] = [{k: v for k, v in row.items() if k not in {"snapshot", "digest"}}
                             for row in signal["samples"]]
        for row in signal["samples"]:
            row["installation_id"] = ac.PATCH_RESOURCE + "/patchInstallationResults/" + row["installation_id"]
        for query in (ac.LEGACY_PATCH_OCT22_QUERY, ac.LEGACY_PATCH_ALERT_QUERY):
            signal["query"] = query
            frozen = ac.wire(legacy)
            with self.subTest(query=query):
                self.tool.validate_intent(legacy, episode, "")
                result = inspect_intent(legacy)
                self.assertEqual(result.episode_id, episode)
                self.assertEqual(result.thread_id, THREAD)
                self.assertEqual(result.create_write_id, legacy["payloads"]["create"]["write_request_id"])
                self.assertEqual(ac.wire(legacy), frozen)


class FeedbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.intent = intent_fixture()

    def setUp(self):
        self.source, self.native, self.store = Client(), NativeFake(), Store()
        self.bridge = Bridge(self.source, self.native, self.store)
        initial = inspect_intent(copy.deepcopy(self.intent))
        self.source.receipts[initial.create_write_id] = {
            "outcome": "applied", "case_reference": copy.deepcopy(REFERENCE)}
        self.binding = self.bridge.bind(copy.deepcopy(self.intent))

    def collect(self):
        return self.bridge.collect(self.binding)

    def row(self):
        return self.store.get(self.binding.episode_id, EVENT)

    def test_real_immutable_intent_format_is_reused_without_new_case(self):
        self.assertEqual(self.binding.thread_id, THREAD)
        self.assertEqual(self.binding.episode_id, ac.episode_for(THREAD))
        self.assertEqual(self.binding.case.reference, REFERENCE)

    def test_invalid_or_recurrence_intent_fails_closed(self):
        for change in (
            lambda x: x.update(recurrence_id=ALERT),
            lambda x: x["binding"].update(investigation_id=str(uuid4())),
            lambda x: x["binding"].update(rule_id=ac.RULE + "-other"),
            lambda x: x["fingerprints"].update(create="0" * 64),
        ):
            value = copy.deepcopy(self.intent)
            change(value)
            with self.assertRaises((FeedbackError, ac.CaseToolError)):
                inspect_intent(value)
        self.assertEqual(self.native.posts, 0)

    def test_native_manual_or_other_plan_thread_is_not_correlated(self):
        for key, value in (("agentMode", "Autonomous"), ("threadOrigin", "User"), ("source", "User")):
            self.native.detail = native_fixture()
            self.native.detail[key] = value
            with self.assertRaises(ac.CaseToolError):
                self.bridge.bind(copy.deepcopy(self.intent))

    def test_unconfirmed_original_create_does_not_receive_feedback(self):
        self.source.receipts.clear()
        with self.assertRaises(FeedbackError):
            self.bridge.bind(copy.deepcopy(self.intent))

    def test_wrong_case_investigation_reference_is_rejected(self):
        self.source.context["investigation_reference"] = str(uuid4())
        with self.assertRaises(FeedbackError):
            self.collect()
        self.assertFalse(self.store.pending(self.binding.episode_id))

    def test_operator_update_reaches_same_thread_and_ack_is_not_an_answer(self):
        self.collect()
        result = self.bridge.deliver(self.binding)
        self.assertEqual(result, "native_received_acknowledged_not_answered")
        self.assertEqual((self.native.posts, self.source.acks), (1, 1))
        payload = json.loads(self.native.value[0]["text"].split("\n")[-1])
        self.assertEqual(payload["original_investigation_id"], THREAD)
        self.assertEqual(payload["event"]["event_id"], EVENT)
        self.assertFalse(payload["execution_authorized"])
        self.assertFalse(payload["human_approval"])
        self.assertEqual(self.row()["status"], "acknowledged")

    def test_repeated_polls_do_not_repeat_native_message(self):
        self.collect()
        self.bridge.deliver(self.binding)
        self.collect()
        self.assertEqual(self.bridge.deliver(self.binding), "no_pending_feedback")
        self.assertEqual((self.native.posts, self.source.acks), (1, 1))

    def test_agent_own_notes_and_sensitive_or_large_content_are_not_forwarded(self):
        for change, expected in (
            ({"actor": "Copilot.Integration"}, "ignored_integration_echo"),
            ({"disposition": "blocked_sensitive_content"}, "blocked_sensitive_content"),
            ({"content": "password=unit-fixture-not-a-real-credential"}, "blocked_sensitive_content"),
            ({"content": "https://unit.fixture.example?sig=unit-fixture"}, "blocked_sensitive_content"),
            ({"content": "Bearer unit-fixture-not-a-real-token"}, "blocked_sensitive_content"),
            ({"content": 'api_key: "unit-fixture-not-a-real-key"'}, "blocked_sensitive_content"),
            ({"content": "x" * 8001}, "blocked_oversized_content"),
        ):
            with self.subTest(expected=expected):
                self.store = Store()
                self.bridge.store = self.store
                self.source.events = [{
                    "event_id": EVENT, "kind": "work_note", "actor": "unit.fixture.operator",
                    "observed_at": "2026-10-22 17:00:00", "content": "Unit fixture only.",
                    "disposition": "pending_delivery", **change,
                }]
                self.collect()
                self.assertEqual(self.row()["status"], expected)
                self.assertNotIn("message", self.row())
                self.assertEqual(self.bridge.deliver(self.binding), "no_pending_feedback")
        self.assertEqual((self.native.posts, self.source.acks), (0, 0))

    def test_busy_original_investigation_defers_without_ack_or_claim(self):
        self.collect()
        self.native.state = "Running"
        self.assertEqual(self.bridge.deliver(self.binding), "deferred_native_busy")
        self.assertEqual((self.native.posts, self.source.acks), (0, 0))
        self.assertEqual(self.row()["status"], "prepared")

    def test_timeout_before_persistence_never_blindly_resends(self):
        self.collect()
        self.native.mode = "timeout_before"
        for _ in range(3):
            self.assertEqual(self.bridge.deliver(self.binding), "delivery_unknown_no_resend")
        self.assertEqual((self.native.posts, self.source.acks), (1, 0))

    def test_timeout_after_persistence_reconciles_without_resending(self):
        self.collect()
        self.native.mode = "timeout_after"
        self.assertEqual(self.bridge.deliver(self.binding), "delivery_unknown_no_resend")
        self.assertEqual(self.bridge.deliver(self.binding), "native_received_acknowledged_not_answered")
        self.assertEqual((self.native.posts, self.source.acks), (1, 1))

    def test_process_interruptions_reconcile_only_never_retry_unknown_post(self):
        for mode, posted in (("crash_before", False), ("crash_after", True)):
            with self.subTest(mode=mode):
                self.store = Store()
                self.bridge.store = self.store
                self.native.value, self.native.posts, self.native.mode = [], 0, mode
                self.source.events = [{
                    "event_id": EVENT, "kind": "work_note", "actor": "unit.fixture.operator",
                    "observed_at": "2026-10-22 17:00:00", "content": "Unit restart fixture.",
                    "disposition": "pending_delivery",
                }]
                self.source.receipts = {}
                self.source.acks = 0
                self.collect()
                with self.assertRaises(KeyboardInterrupt):
                    self.bridge.deliver(self.binding)
                result = self.bridge.deliver(self.binding)
                self.assertEqual(result, "native_received_acknowledged_not_answered" if posted
                                 else "delivery_unknown_no_resend")
                self.assertEqual(self.native.posts, 1)

    def test_http_success_without_native_receipt_is_not_acknowledged(self):
        self.collect()
        self.native.mode = "success_without_receipt"
        self.assertEqual(self.bridge.deliver(self.binding), "delivery_unknown_no_resend")
        self.assertEqual(self.source.acks, 0)

    def test_agent_echo_is_not_a_persisted_user_message_receipt(self):
        self.collect()
        row = self.row()
        self.store.change(self.binding.episode_id, EVENT, row, {"status": "submitting"})
        self.native.value.append({"id": str(uuid4()), "text": row["message"], "author": {"role": "SREAgent"}})
        self.assertEqual(self.bridge.deliver(self.binding), "delivery_unknown_no_resend")
        self.assertEqual((self.native.posts, self.source.acks), (0, 0))

    def test_duplicate_native_receipts_stop_processing(self):
        self.collect()
        row = self.row()
        self.native.value = [{"id": str(uuid4()), "text": row["message"], "author": {"role": "User"}}
                             for _ in range(2)]
        with self.assertRaises(FeedbackError):
            self.bridge.deliver(self.binding)
        self.assertEqual(self.source.acks, 0)

    def test_lost_ack_response_is_reconciled_even_after_source_event_disappears(self):
        self.collect()
        self.source.lose_ack = True
        self.assertEqual(self.bridge.deliver(self.binding), "native_received_ack_unknown")
        self.assertFalse(self.source.events)
        self.collect()
        self.assertEqual(self.bridge.deliver(self.binding), "native_received_acknowledged_not_answered")
        self.assertEqual((self.native.posts, self.source.acks), (1, 1))

    def test_ack_receipt_for_another_event_is_rejected(self):
        self.collect()
        original = self.source.acknowledge

        def wrong(case, payload):
            result = original(case, payload)
            result["event_id"] = "c" * 32
            return result
        self.source.acknowledge = wrong
        with self.assertRaises(FeedbackError):
            self.bridge.deliver(self.binding)
        self.assertEqual(self.row()["status"], "native_received")

    def test_definite_auth_rejection_can_retry_after_permission_propagation(self):
        self.collect()
        self.native.mode = "rejected"
        with self.assertRaises(RejectedDelivery):
            self.bridge.deliver(self.binding)
        self.assertEqual(self.row()["status"], "prepared")
        self.native.mode = "normal"
        self.assertEqual(self.bridge.deliver(self.binding), "native_received_acknowledged_not_answered")
        self.assertEqual(len(self.native.value), 1)

    def test_changed_source_event_id_content_is_not_rebound(self):
        self.collect()
        self.source.events[0]["content"] = "Unit fixture changed under an existing ID."
        with self.assertRaises(FeedbackError):
            self.collect()

    def test_corrupted_durable_message_is_not_posted(self):
        self.collect()
        self.store.change(self.binding.episode_id, EVENT, self.row(), {"message": "Uncorrelated fixture."})
        with self.assertRaises(FeedbackError):
            self.bridge.deliver(self.binding)
        self.assertEqual((self.native.posts, self.source.acks), (0, 0))

    def test_durable_mapping_cannot_switch_case_under_same_episode(self):
        self.collect()
        row = self.store.get(self.binding.episode_id, "_state")
        self.store.change(self.binding.episode_id, "_state", row, {"case_sys_id": "f" * 32})
        with self.assertRaises(FeedbackError):
            self.collect()

    def test_new_state_is_context_not_repair_or_closure_and_needs_no_journal_ack(self):
        self.source.events = []
        self.collect()
        self.assertEqual(self.bridge.deliver(self.binding), "no_pending_feedback")
        self.source.context.update(state={"value": "6", "label": "Resolved"}, revision="2")
        self.collect()
        self.assertEqual(self.bridge.deliver(self.binding), "native_received")
        self.assertEqual((self.native.posts, self.source.acks), (1, 0))
        self.collect()
        self.assertEqual(self.bridge.deliver(self.binding), "no_pending_feedback")

    def test_state_revision_rollback_is_rejected(self):
        self.source.events = []
        self.collect()
        self.source.context["revision"] = "0"
        with self.assertRaises(FeedbackError):
            self.collect()

    def test_crash_after_atomic_state_capture_cannot_duplicate_observation(self):
        self.source.events = []
        self.collect()
        transition = self.store.transition

        def crash_after_commit(*args):
            transition(*args)
            raise KeyboardInterrupt()
        self.store.transition = crash_after_commit
        self.source.context.update(state={"value": "6", "label": "Resolved"}, revision="2")
        with self.assertRaises(KeyboardInterrupt):
            self.collect()
        self.source.context["revision"] = "3"
        self.store.transition = transition
        self.collect()
        self.assertEqual(len(self.store.pending(self.binding.episode_id)), 1)
        self.assertEqual(self.bridge.deliver(self.binding), "native_received")
        self.assertEqual(self.native.posts, 1)

    def test_failed_state_transaction_does_not_advance_snapshot(self):
        self.source.events = []
        self.collect()
        transition = self.store.transition

        def reject(*args):
            raise Conflict("Fixture rejected conditional transaction.")
        self.store.transition = reject
        self.source.context.update(state={"value": "6", "label": "Resolved"}, revision="2")
        with self.assertRaises(Conflict):
            self.collect()
        snapshot = self.store.get(self.binding.episode_id, "_state")
        self.assertEqual(snapshot["revision"], 1)
        self.assertFalse(self.store.pending(self.binding.episode_id))
        self.store.transition = transition
        self.collect()
        self.assertEqual(self.bridge.deliver(self.binding), "native_received")

    def test_unsafe_state_label_is_a_visible_failure_not_a_silent_snapshot_advance(self):
        self.source.events = []
        self.collect()
        self.source.context.update(
            state={"value": "6", "label": "password=unit-fixture-not-a-real-credential"}, revision="2")
        with self.assertRaises(FeedbackError):
            self.collect()
        self.assertEqual(self.store.get(self.binding.episode_id, "_state")["revision"], 1)
        self.assertEqual(self.native.posts, 0)

    def test_concurrent_workers_have_one_conditional_native_submission(self):
        self.collect()
        self.native.barrier = threading.Barrier(2)

        def deliver():
            try:
                return self.bridge.deliver(self.binding)
            except Conflict:
                return "deferred_conflict"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: deliver(), range(2)))
        self.assertIn("deferred_conflict", results)
        self.assertEqual((self.native.posts, self.source.acks), (1, 1))


class Credential:
    def get_token(self, scope):
        return type("Token", (), {"token": "unit-fixture-token"})()


class Http:
    def __init__(self, status=201, endpoint="https://asre-demo-cus--0a1b2c3d.4e5f6a7b.centralus.azuresre.ai"):
        self.status, self.endpoint, self.requests = status, endpoint, []

    def open(self, request, timeout):
        self.requests.append(request)
        if request.get_method() == "POST" and self.status >= 300:
            raise HTTPError(request.full_url, self.status, "unit fixture", {}, io.BytesIO(b""))
        if request.full_url.startswith("https://management.azure.com"):
            value = {"id": AGENT_ID, "properties": {"agentEndpoint": self.endpoint,
                     "powerState": "Running", "provisioningState": "Succeeded",
                     "actionConfiguration": {"mode": "Review", "accessLevel": "Low"}}}
        else:
            value = {"value": [], "state": "Idle"}
        result = io.BytesIO(json.dumps(value).encode())
        result.status = 200 if request.get_method() == "GET" else self.status
        return result


class NativeHttpTests(unittest.TestCase):
    def test_actual_text_only_contract_and_fixed_thread_path(self):
        http = Http()
        native = Native(Credential(), http)
        native.resolve()
        native.prepare(THREAD, "Unit fixture only.")()
        request = http.requests[-1]
        self.assertEqual(request.get_method(), "POST")
        self.assertTrue(request.full_url.endswith("/api/v1/threads/" + THREAD + "/messages"))
        self.assertEqual(json.loads(request.data), {"text": "Unit fixture only."})
        self.assertEqual(request.get_header("Authorization"), "Bearer unit-fixture-token")

    def test_endpoint_cannot_redirect_credentials_to_another_host_or_port(self):
        for endpoint in (
            "https://attacker.example", "http://asre-demo-cus--0a1b2c3d.4e5f6a7b.centralus.azuresre.ai",
            "https://asre-demo-cus--0a1b2c3d.4e5f6a7b.centralus.azuresre.ai:443",
            "https://user@asre-demo-cus--0a1b2c3d.4e5f6a7b.centralus.azuresre.ai",
            "https://asre-demo-cus--0a1b2c3d.4e5f6a7b.centralus.azuresre.ai?x=1",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(FeedbackError):
                    Native(Credential(), Http(endpoint=endpoint)).resolve()

    def test_invalid_thread_identity_cannot_select_a_path_or_query(self):
        native = Native(Credential(), Http())
        native.resolve()
        for value in ("../other", THREAD.upper(), THREAD + "?x=1"):
            with self.assertRaises(FeedbackError):
                native.prepare(value, "Unit fixture.")

    def test_definite_native_rejections_and_ambiguous_server_errors_are_distinct(self):
        for status, error in ((403, RejectedDelivery), (400, RejectedDelivery),
                              (500, UnknownDelivery), (302, UnknownDelivery)):
            http = Http(status)
            native = Native(Credential(), http)
            native.resolve()
            with self.assertRaises(error):
                native.prepare(THREAD, "Unit fixture.")()


if __name__ == "__main__":
    unittest.main()
