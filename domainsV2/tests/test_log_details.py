from copy import deepcopy
from datetime import datetime, timezone
import unittest
from unittest.mock import patch
import uuid

from energy_domains_v2 import events, replay
from energy_domains_v2.app import create_app
from energy_domains_v2.contracts import SURFACES
from energy_domains_v2.demo_runtime import DemoRuntime
from test_demo_runtime import Checker, Replay as RouteAdapter, Sender, Store


class LogDetailsTests(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.now = datetime(2026, 9, 12, 16, tzinfo=timezone.utc)
        self.runtime = DemoRuntime(
            self.store, Sender(), {"monitoring": RouteAdapter, "inventory": RouteAdapter},
            replay=replay, readiness=Checker(), clock=lambda: self.now)
        self.runtime._legacy_scanned = True

    def test_all_surface_previews_are_read_only_and_have_no_fake_event_ids(self):
        for surface in SURFACES:
            before = deepcopy(self.store.docs)
            detail = self.runtime.log_details(surface)
            self.assertEqual(detail["view_state"], "preview_not_queued")
            self.assertEqual(detail["record_count"], 10)
            self.assertNotIn("EventId", detail["records"][0])
            self.assertEqual(self.store.docs, before)

    def test_queued_details_equal_original_outbox_and_survive_restart(self):
        self.runtime.session_command("prepare", key=str(uuid.uuid4()), duration_seconds=900)
        self.runtime._tick(self.now)
        state, _ = self.runtime._load()
        original = [r for b in state["pending"] if b["table"] == events.OBSERVATIONS
                    for r in b["records"]]
        for surface in SURFACES:
            detail = self.runtime.log_details(surface)
            self.assertEqual(detail["view_state"], "last_queued")
            self.assertEqual(detail["records"], [r for r in original if r["Surface"] == surface])
        restarted = DemoRuntime(
            self.store, Sender(), {"monitoring": RouteAdapter, "inventory": RouteAdapter},
            replay=replay, readiness=Checker(), clock=lambda: self.now)
        self.assertEqual(restarted.log_details("power-grid"), self.runtime.log_details("power-grid"))

    def test_next_intervention_does_not_change_last_queued_observations(self):
        for surface in SURFACES:
            model = replay.sample(surface, replay.initial_model(surface), 1,
                                  self.now.isoformat())["model"]
            before = replay.observation_view(surface, model)
            changed = replay.apply_action(surface, model, "inject", replay.scenarios(surface)[0]["id"])
            self.assertEqual(replay.observation_view(surface, changed)["observations"],
                             before["observations"])

    def test_api_rejects_unknown_surface_and_does_not_enqueue(self):
        client = create_app(self.runtime, worker=False).test_client()
        for surface in SURFACES:
            response = client.get(f"/api/v2/surfaces/{surface}/observations")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["record_count"], 10)
        self.assertEqual(client.get("/api/v2/surfaces/not-a-domain/observations").status_code, 400)
        self.assertFalse(self.store.docs)

    def test_maintenance_disables_worker_and_all_http_mutations(self):
        with patch.dict("os.environ", {"V2_MAINTENANCE_MODE": "1"}), \
                patch.object(self.runtime, "start_worker") as start:
            client = create_app(self.runtime, worker=True).test_client()
            start.assert_not_called()
            live = client.get("/api/health/live").json
            self.assertTrue(live["maintenance"])
            self.assertFalse(live["worker_enabled"])
            self.assertEqual(live["fixture_version"], replay.VERSION)
            for path in ("/api/v2/demo/commands", "/api/v2/surfaces/power-grid/commands",
                         "/not-a-route"):
                self.assertEqual(client.post(path, json={"action": "prepare"}).status_code, 503)
            self.assertFalse(self.store.docs)

    def test_invalid_maintenance_setting_fails_closed(self):
        with patch.dict("os.environ", {"V2_MAINTENANCE_MODE": "true"}):
            with self.assertRaisesRegex(RuntimeError, "V2_MAINTENANCE_MODE"):
                create_app(self.runtime, worker=False)


if __name__ == "__main__":
    unittest.main()
