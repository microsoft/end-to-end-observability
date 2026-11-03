"""Native journey projection and read-only endpoint boundaries."""

import json
import unittest
from pathlib import Path
from unittest.mock import Mock

from dashboard.journey import build_journey, catalog, message_events
from dashboard.server import AzureReads, Dashboard, ReadError, SRE_SCOPE
from test_dashboard import ENDPOINT, NOW, client, thread

IDENTITY = "00000021-000a-4000-a000-000000000021"
CASE_URL = "https://example-instance.service-now.com/incident.do?sys_id=0000000000000000000000000000006a"


def public_message(**changes):
    value = {"id": "message-1", "timeStamp": NOW, "author": {"role": "SREAgent"},
             "messageType": None, "text": "Collected current provider evidence."}
    value.update(changes)
    return value


def observed_thread(**changes):
    value = {
        "id": IDENTITY, "title": "Native incident", "created_at": NOW, "updated_at": NOW,
        "rule_id": catalog()[0]["rule_id"], "incident_state": "resolved",
        "execution_state": "Complete", "plan_id": "asre-azure-monitor-review",
    }
    value.update(changes)
    return value


def available(data):
    return {"status": "empty" if data == [] else "ok", "checked_at": NOW, "data": data}


class JourneyTests(unittest.TestCase):
    def test_catalog_matches_production_profiles_and_expected_skills(self):
        root = Path(__file__).resolve().parents[1]
        workflow = json.loads((root/"agent"/"workflows"/"azure-monitor-review.json").read_text())
        profiles = catalog()
        self.assertEqual([profile["id"] for profile in profiles], workflow["case_profiles"])
        self.assertEqual(len({profile["rule_id"] for profile in profiles}), 5)
        self.assertTrue(all(profile["skills"][-1] == "asre-case-and-verification" for profile in profiles))
        self.assertEqual(profiles[-1]["id"], "backup-job")
        self.assertEqual({profile["qualification"] for profile in profiles},
                         {"Automatic case delivery accepted historically; not a current case-state claim"})

    def test_resolved_alert_is_not_investigation_or_delivery_completion(self):
        result = build_journey(observed_thread(execution_state=None), available([]), available([]))
        stages = {stage["id"]: stage for stage in result["stages"]}
        self.assertEqual(stages["signal"]["state"], "completed")
        self.assertEqual(stages["route"]["state"], "completed")
        self.assertEqual(stages["investigate"]["state"], "not_observed")
        self.assertEqual(stages["case"]["state"], "not_observed")
        self.assertEqual(stages["verify"]["state"], "not_observed")

    def test_native_run_completion_does_not_verify_case_or_invent_end_time(self):
        result = build_journey(observed_thread(), available([]), available([]))
        stages = {stage["id"]: stage for stage in result["stages"]}
        self.assertEqual(stages["investigate"]["state"], "completed")
        self.assertIsNone(stages["investigate"]["at"])
        self.assertEqual(stages["verify"]["state"], "not_observed")

    def test_case_links_are_explicitly_reported_not_verified(self):
        message = public_message(text=f"Created [INC0900008]({CASE_URL}).")
        result = build_journey(observed_thread(), available([message]), available([]))
        self.assertEqual(result["case_references"][0]["verification"], "agent_reported")
        self.assertEqual(result["case_references"][0]["url"], CASE_URL)
        self.assertEqual(result["stages"][3]["state"], "reported")
        self.assertEqual(result["stages"][4]["state"], "not_observed")

    def test_reasoning_insights_unknown_types_and_human_text_are_excluded(self):
        rows = [public_message(messageType=kind, text="private text") for kind in
                ("Reasoning", "TrajectoryInsight", "UnknownFutureType", "ToolArguments")]
        rows.append(public_message(author={"role": "User"}, text="untrusted human assertion"))
        events, references = message_events(rows)
        self.assertEqual(events, [])
        self.assertEqual(references, [])

    def test_case_url_allowlist_blocks_external_hosts_paths_and_extra_arguments(self):
        for url in ("https://evil.invalid/incident.do?sys_id="+"a"*32,
                    CASE_URL+"&redirect=evil", CASE_URL.replace("https:", "http:"),
                    CASE_URL.replace("incident.do", "other.do")):
            with self.subTest(url=url):
                _, cases = message_events([public_message(text=f"[INC0900008]({url})")])
                self.assertEqual(cases, [])

    def test_public_updates_are_bounded_redacted_and_not_html_interpreted(self):
        text = "Bearer secret-token\naccess_token: hidden\n<script>evil()</script>\n" + "x"*5000
        events, _ = message_events([public_message(text=text)])
        rendered = events[0]["text"]
        self.assertNotIn("secret-token", rendered)
        self.assertNotIn("hidden", rendered)
        self.assertLessEqual(len(rendered), 2400)
        self.assertIn("<script>", rendered)

    def test_hook_completion_is_not_named_tool_success(self):
        row = public_message(hookExecution={"status": "Completed", "durationMs": 120}, text="Hook completed")
        events, _ = message_events([row])
        self.assertEqual(events[0]["kind"], "tool_audit")
        self.assertIn("not proof", events[0]["text"])
        self.assertNotIn("tool_name", events[0])

    def test_pending_native_approval_is_waiting_not_a_new_mandatory_gate(self):
        result = build_journey(observed_thread(execution_state="InProgress"),
                               available([]), available([{"id": "approval-1"}]))
        self.assertEqual(result["stages"][2]["state"], "waiting")
        self.assertEqual(result["approvals"]["pending_count"], 1)
        self.assertEqual(build_journey(observed_thread(), available([]), available([]))["approvals"]["pending_count"], 0)

    def test_denied_sources_remain_explicit_and_do_not_discard_native_run_state(self):
        denied = {"status": "error", "error": {"code": "HTTP_403", "message": "Denied"}}
        result = build_journey(observed_thread(), denied, denied)
        self.assertEqual(result["stages"][2]["state"], "completed")
        self.assertIsNone(result["approvals"]["pending_count"])
        self.assertEqual(result["sources"]["messages"]["error"]["code"], "HTTP_403")
        self.assertEqual(result["events"], [])

    def test_event_limit_and_bad_timestamps_are_explicit(self):
        events, _ = message_events([public_message(id=str(i)) for i in range(300)])
        self.assertEqual(len(events), 250)
        self.assertEqual(events[0]["id"], "50")
        with self.assertRaises(ValueError):
            message_events([public_message(timeStamp="bad")])

    def test_missing_plan_binding_is_not_claimed_matched(self):
        result = build_journey(observed_thread(plan_id="other-plan"), available([]), available([]))
        self.assertEqual(result["stages"][1]["state"], "not_observed")


class JourneyReadTests(unittest.TestCase):
    def test_only_exact_uuid_get_detail_message_and_approval_paths_are_allowed(self):
        reads = client({})
        for path in (f"/api/v1/threads/{IDENTITY}", f"/api/v1/threads/{IDENTITY}/messages",
                     f"/api/v1/approvals/{IDENTITY}"):
            reads._read(ENDPOINT+path, SRE_SCOPE)
        for path in (f"/api/v1/threads/{IDENTITY}/messages?all=true",
                     f"/api/v1/approvals/{IDENTITY}/decision",
                     "/api/v1/threads/not-a-uuid/messages", f"/api/v1/threads/{IDENTITY}/execute"):
            with self.subTest(path=path), self.assertRaises(PermissionError):
                reads._read(ENDPOINT+path, SRE_SCOPE)
        with self.assertRaises(PermissionError):
            reads._read(ENDPOINT+f"/api/v1/threads/{IDENTITY}/messages", SRE_SCOPE, {"text": "start"})

    def test_detail_scope_is_checked_before_reading_any_messages(self):
        reads = client({})
        reads._read = Mock(return_value=thread(id=IDENTITY, source="Conversation"))
        with self.assertRaises(ReadError):
            reads.incident(ENDPOINT, IDENTITY)
        self.assertEqual(reads._read.call_count, 1)

    def test_actual_detail_and_events_form_a_normalized_journey(self):
        raw = thread(id=IDENTITY, incidentDetails={
            "filterId": "asre-azure-monitor-review", "investigationStatus": "Complete"})
        reads = client({})
        def response(url, scope):
            if url.endswith("/messages"):
                return {"value": [public_message()], "state": "Idle"}
            if "/approvals/" in url:
                return []
            return raw
        reads._read = Mock(side_effect=response)
        result = reads.incident(ENDPOINT, IDENTITY)
        self.assertEqual(result["execution_state"], "Complete")
        self.assertEqual(len(result["events"]), 1)
        self.assertNotIn("startMessage", json.dumps(result))

    def test_unknown_membership_is_rejected_without_detail_read(self):
        reads = Mock()
        reads.agent.return_value = {"endpoint": ENDPOINT}
        reads.threads.return_value = []
        with self.assertRaises(ReadError) as failure:
            Dashboard(reads).incident(IDENTITY)
        self.assertEqual(failure.exception.code, "NOT_FOUND")
        reads.incident.assert_not_called()

    def test_detail_cache_and_force_use_only_known_members(self):
        reads = Mock()
        reads.agent.return_value = {"endpoint": ENDPOINT}
        reads.threads.return_value = [{"id": IDENTITY}]
        reads.incident.return_value = {"thread": observed_thread()}
        dashboard = Dashboard(reads)
        self.assertFalse(dashboard.incident(IDENTITY)["cached"])
        self.assertTrue(dashboard.incident(IDENTITY)["cached"])
        dashboard.incident(IDENTITY, True)
        self.assertEqual(reads.incident.call_count, 2)
        with self.assertRaises(ValueError):
            dashboard.incident("../other")


if __name__ == "__main__":
    unittest.main()
