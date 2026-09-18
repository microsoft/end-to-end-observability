from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch
import uuid

from energy_domains_v2 import events
from energy_domains_v2.app import create_app
from energy_domains_v2.runtime import Runtime, Backpressure, CommandConflict
from energy_domains_v2.transport import StoreConflict


class Store:
    def __init__(self):
        self.docs = {}
        self.writes = 0

    def read(self, key):
        return deepcopy(self.docs.get(key, (None, None)))

    def write(self, key, document, version):
        _, current = self.read(key)
        if version != current:
            raise StoreConflict("stale")
        new = str(int(current or "0") + 1)
        self.docs[key] = (deepcopy(document), new)
        self.writes += 1
        return new

    def keys(self):
        return list(self.docs)


class Adapter:
    @staticmethod
    def scenarios(surface):
        return [{"id": "loss", "label": "Loss", "description": "Fictional test"}]

    @staticmethod
    def initial_model(surface, seed):
        return {"scenario": "normal", "value": 12, "seed": seed}

    @staticmethod
    def apply_action(surface, model, action, scenario=None):
        result = deepcopy(model)
        result.update(value=0 if action == "inject" else 12,
                      scenario=scenario if action == "inject" else "normal")
        return result

    @staticmethod
    def frame(surface, model, step, at):
        return {
            "model": deepcopy(model), "snapshot": {"value": model["value"]},
            "observations": [{
                "record_type": "observation", "entity_id": "fictional-one",
                "location_id": "store-one", "simulated_at": at,
                "payload": {"value": model["value"], "unit": "each", "quality": "good"},
                "context": {"location_claim": "fictional"},
            }],
        }

    @staticmethod
    def register_routes(app, runtime):
        pass


class Sender:
    def __init__(self):
        self.calls = []
        self.failure = None

    def send(self, table, records):
        if self.failure:
            raise self.failure
        self.calls.append((table, deepcopy(records)))


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 10, tzinfo=timezone.utc)
        self.store, self.sender = Store(), Sender()
        self.adapters = {"monitoring": Adapter, "inventory": Adapter}
        self.runtime = Runtime(self.store, self.sender, self.adapters, clock=lambda: self.now)

    def command(self, action, scenario=None, key=None, **kwargs):
        return self.runtime.command("power-grid", action, scenario, key or str(uuid.uuid4()), **kwargs)

    def advance(self, seconds=2):
        self.now += timedelta(seconds=seconds)

    def test_get_never_generates_or_persists_logs(self):
        self.assertEqual(self.runtime.snapshot("power-grid")["value"], 12)
        self.assertFalse(self.runtime.status("power-grid")["running"])
        self.assertEqual(self.store.writes, 0)
        self.assertEqual(self.sender.calls, [])

    def test_selector_exposes_all_nine_portals(self):
        client = create_app(self.runtime, worker=False).test_client()
        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        for domain in ("power-grid", "upstream", "midstream", "downstream"):
            self.assertIn(f'href="/{domain}"', html)
            self.assertIn(f'href="/{domain}/review/inventory"', html)
        self.assertIn('href="/downstream/portfolio"', html)
        self.assertEqual(self.store.writes, 0)

    def test_idempotency_and_mismatched_key(self):
        key = str(uuid.uuid4())
        first = self.command("start", key=key)
        writes = self.store.writes
        self.assertEqual(self.command("start", key=key), first)
        self.assertEqual(self.store.writes, writes)
        with self.assertRaises(CommandConflict):
            self.command("stop", key=key)

    def test_mutation_has_observations_and_separate_audit(self):
        self.command("start")
        self.advance()
        self.command("inject", "loss")
        state, _ = self.store.read("power-grid")
        observations = [r for b in state["pending"] if b["table"] == events.OBSERVATIONS for r in b["records"]]
        audit = [r for b in state["pending"] if b["table"] == events.AUDIT for r in b["records"]]
        self.assertEqual([r["Payload"]["value"] for r in observations], [12, 0])
        self.assertEqual([r["Scenario"] for r in audit], ["", "loss"])
        self.assertNotIn("loss", events.encoded(observations).decode())
        self.assertTrue(all(r["IsSynthetic"] for r in observations))
        self.assertEqual(len({r["EventId"] for r in observations + audit}), 4)

    def test_reset_preserves_delivery_history_and_restores_state(self):
        self.command("start")
        self.advance()
        self.command("inject", "loss")
        before = self.runtime.status("power-grid")["pending_records"]
        self.advance()
        self.command("reset")
        self.assertEqual(self.runtime.snapshot("power-grid")["value"], 12)
        self.assertGreater(self.runtime.status("power-grid")["pending_records"], before)

    def test_restart_delivers_pending_without_get_reemission(self):
        self.command("start")
        self.command("stop")
        self.runtime = Runtime(self.store, self.sender, self.adapters, clock=lambda: self.now)
        for _ in range(5):
            self.runtime.cycle()
        self.assertEqual(self.runtime.status("power-grid")["pending_records"], 0)
        count = sum(len(records) for _, records in self.sender.calls)
        self.assertEqual(count, 3)
        self.runtime.snapshot("power-grid")
        self.runtime.cycle()
        self.assertEqual(sum(len(r) for _, r in self.sender.calls), count)

    def test_duration_and_seed_are_strictly_bounded(self):
        for duration in (True, 0, 1801, 10.5):
            with self.assertRaises(ValueError):
                self.command("start", duration_seconds=duration)
        for seed in (True, -1, 9007199254740992):
            with self.assertRaises(ValueError):
                self.command("start", seed=seed)
        self.assertEqual(self.store.writes, 0)
        for action, scenario in (([], None), ("inject", []), ("inject", {})):
            with self.assertRaises(ValueError):
                self.command(action, scenario)

    def test_backpressure_does_not_accept_partial_state(self):
        self.runtime.pending_limit = 1
        with self.assertRaises(Backpressure):
            self.command("start")
        self.assertEqual(self.store.writes, 0)

    def test_stop_remains_available_with_full_observation_budget(self):
        self.command("start")
        self.runtime.pending_limit = 1
        self.command("stop")
        self.assertFalse(self.runtime.status("power-grid")["running"])
        before = self.runtime.status("power-grid")["pending_records"]
        self.command("stop")
        self.assertEqual(self.runtime.status("power-grid")["pending_records"], before)

    def test_run_expires_and_stop_does_not_drop_pending(self):
        self.command("start", duration_seconds=10)
        self.advance(11)
        self.runtime.cycle()
        self.assertFalse(self.runtime.status("power-grid")["running"])
        self.assertGreater(self.runtime.status("power-grid")["pending_records"], 0)

    def test_failed_delivery_is_retained_then_retried(self):
        class Transient(Exception):
            code = "429"
            retry_after = 60
            retryable = True
        self.command("start")
        self.command("stop")
        self.sender.failure = Transient()
        with patch("energy_domains_v2.runtime.DeliveryError", Transient):
            self.runtime.cycle()
        state, _ = self.store.read("power-grid")
        self.assertEqual(state["pending"][0]["attempts"], 1)
        self.assertEqual(state["pending"][0]["next_attempt"], self.now.timestamp() + 60)
        self.assertEqual(state["accepted_records"], 0)
        self.sender.failure = None
        self.advance(61)
        for _ in range(5):
            self.runtime.cycle()
        self.assertEqual(self.runtime.status("power-grid")["pending_records"], 0)

    def test_retry_does_not_need_more_outbox_or_daily_capacity(self):
        self.command("start")
        self.command("stop")
        state, version = self.store.read("power-grid")
        for batch in state["pending"]:
            batch.update(error="403", attempts=5, next_attempt=None)
        state["daily_bytes"] = self.runtime.daily_limit + 16_000
        self.store.write("power-grid", state, version)
        self.runtime.pending_limit = 1
        before = len(state["pending"])
        self.command("retry_delivery")
        current, _ = self.store.read("power-grid")
        self.assertEqual(len(current["pending"]), before)
        for _ in range(5):
            self.runtime.cycle()
        self.assertEqual(self.runtime.status("power-grid")["pending_records"], 0)

    def test_smoke_failure_survives_successful_worker_cycle(self):
        self.runtime.smoke_error = "DeploymentSmokeFailed"
        self.runtime.cycle()
        self.assertEqual(self.runtime.status("power-grid")["worker_error"], "DeploymentSmokeFailed")

    def test_detector_labels_are_rejected(self):
        state = {"surface": "power-grid", "run_id": "run", "sequence": 0}
        frame = Adapter.frame("power-grid", Adapter.initial_model("power-grid", 42), 0, self.now.isoformat())
        frame["observations"][0]["payload"]["is_anomaly"] = True
        with self.assertRaises(ValueError):
            events.observations(frame, state, "power-grid", self.now.isoformat())

    def test_batches_include_sdk_unicode_expansion(self):
        rows = [{"EventId": str(index), "Payload": {"text": "\u00b0" * 1000}}
                for index in range(100)]
        batches = events.batches(events.OBSERVATIONS, rows)
        self.assertGreater(len(batches), 1)
        self.assertEqual(sum(len(batch["records"]) for batch in batches), 100)
        self.assertTrue(all(events.request_size(batch["records"]) <= events.MAX_BATCH_BYTES
                            for batch in batches))

    def test_csrf_origin_and_request_validation(self):
        app = create_app(self.runtime, worker=False)
        client = app.test_client()
        url = "/api/v2/surfaces/power-grid/commands"
        body = {"action": "start"}
        self.assertEqual(client.post(url, json=body).status_code, 403)
        token = client.get("/api/v2/session").get_json()["csrf_token"]
        headers = {
            "Origin": "http://127.0.0.1:8880", "X-CSRF-Token": token,
            "Idempotency-Key": str(uuid.uuid4()),
        }
        self.assertEqual(client.post(url, json={"action": "start", "extra": 1}, headers=headers).status_code, 400)
        self.assertEqual(client.post(url, json=body, headers=headers).status_code, 202)
        self.assertEqual(client.post(url, json=body, headers=headers).status_code, 202)
        self.assertEqual(client.get(url).status_code, 405)


if __name__ == "__main__":
    unittest.main()
