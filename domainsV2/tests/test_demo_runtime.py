from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch
import uuid

from energy_domains_v2 import events
from energy_domains_v2.app import configured_runtime, create_app
from energy_domains_v2.contracts import SURFACES
from energy_domains_v2.demo_runtime import DemoRuntime, STATE_KEY
from energy_domains_v2.readiness import LocalReadinessChecker, ReadinessError
from energy_domains_v2.runtime import Backpressure, CommandConflict
from energy_domains_v2.transport import DeliveryError, StoreConflict, StoreUnavailable


class Store:
    def __init__(self):
        self.docs = {}
        self.writes = 0
        self.reads = {}
        self.conflicts = 0
        self.fail_ack = False

    def keys(self):
        return list(self.docs)

    def read(self, key):
        self.reads[key] = self.reads.get(key, 0) + 1
        return deepcopy(self.docs.get(key, (None, None)))

    def write(self, key, document, version):
        current = self.docs.get(key, (None, None))
        if self.conflicts:
            self.conflicts -= 1
            raise StoreConflict("injected conflict")
        if current[1] != version:
            raise StoreConflict("stale")
        if self.fail_ack and document["accepted_records"] > (current[0] or {}).get("accepted_records", 0):
            raise StoreUnavailable("ack unavailable")
        new = str(int(version or "0") + 1)
        self.docs[key] = (deepcopy(document), new)
        self.writes += 1
        return new


class Replay:
    VERSION = "test-v1"

    @staticmethod
    def scenarios(surface):
        return [{"id": "loss", "label": "Loss", "description": "Fictional loss"}]

    @classmethod
    def initial_model(cls, surface, seed=42):
        return {"surface": surface, "seed": seed, "scenario": "normal", "value": 12,
                "fixture_version": cls.VERSION}

    @staticmethod
    def apply_action(surface, model, action, scenario=None):
        model.update(scenario=scenario if action == "inject" else "normal",
                     value=0 if action == "inject" else 12)
        return model

    @staticmethod
    def sample(surface, model, step, at):
        model["sample_at"] = at
        return {"model": model, "observations": [
            {"record_type": "observation", "entity_id": f"fictional-{i}",
             "location_id": "fictional-location", "simulated_at": at,
             "payload": {"value": model["value"]}, "context": {"unit": "each"}}
            for i in range(10)]}

    @staticmethod
    def snapshot(surface, model):
        return {"value": model["value"], "scenario": model["scenario"],
                "catalog": ["large immutable fixture"] * 5000}

    @staticmethod
    def register_routes(app, runtime):
        pass


class Checker:
    def __init__(self):
        self.calls = []
        self.ready = True
        self.error = None
        self.probe_calls = 0
        self.probe_result = {"verified": True, "observations_allowed": True,
                             "audit_denied": True, "code": "AccessVerified"}

    def access_probe(self):
        self.probe_calls += 1
        return deepcopy(self.probe_result)

    def check(self, *args):
        self.calls.append(args)
        if self.error:
            raise ReadinessError(self.error)
        return {"ready": self.ready, "status": "ready" if self.ready else "pending",
                "checked_at": args[2], "surfaces": list(args[3])}


class Sender:
    def __init__(self):
        self.calls = []
        self.failure = None
        self.hook = None

    def send(self, table, records):
        self.calls.append((table, deepcopy(records)))
        if self.hook:
            self.hook(table, records)
        if self.failure:
            raise self.failure


class DemoTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 10, 15, 0, tzinfo=timezone.utc)
        self.store, self.sender, self.checker = Store(), Sender(), Checker()
        self.runtime = self.make_runtime()

    def make_runtime(self, **kwargs):
        return DemoRuntime(self.store, self.sender, {"monitoring": Replay, "inventory": Replay},
                           replay=kwargs.pop("replay", Replay),
                           readiness=kwargs.pop("readiness", self.checker),
                           clock=lambda: self.now, **kwargs)

    def session(self, action="prepare", key=None, **kwargs):
        if action == "prepare" and not self.runtime._legacy_scanned:
            self.runtime.cycle()
        return self.runtime.session_command(action, key=key or str(uuid.uuid4()), **kwargs)

    def command(self, action="inject", scenario="loss", key=None, surface="power-grid", **kwargs):
        return self.runtime.command(surface, action, scenario, key or str(uuid.uuid4()), **kwargs)

    def advance(self, seconds=60):
        self.now += timedelta(seconds=seconds)

    def state(self):
        return self.store.read(STATE_KEY)[0]

    def observations(self):
        return [row for table, rows in self.sender.calls if table == events.OBSERVATIONS for row in rows]

    def ready(self):
        self.session()
        self.runtime.cycle()
        self.advance(600)
        self.runtime.cycle()
        self.assertTrue(self.runtime.session_status()["ready"])

    def test_gets_and_commands_do_not_emit_observations(self):
        self.runtime.snapshot("power-grid")
        self.runtime.status("power-grid")
        self.runtime.session_status()
        self.assertEqual(self.store.writes, 0)
        self.assertEqual(self.sender.calls, [])
        self.session()
        self.assertEqual(self.state()["generated_records"], 0)
        self.assertEqual(self.state()["published"], {})
        self.runtime.snapshot("power-grid")
        self.assertEqual(self.sender.calls, [])

    def test_two_hour_nine_surface_half_open_soak(self):
        self.session()
        for _ in range(120):
            self.runtime.cycle()
            self.advance()
        self.runtime.cycle()
        self.runtime.cycle()
        rows = self.observations()
        self.assertEqual(len(rows), 10800)
        self.assertEqual(len({r["EventId"] for r in rows}), 10800)
        self.assertEqual(self.state()["surface_steps"], {s: 120 for s in SURFACES})
        self.assertFalse(self.runtime.session_status()["running"])
        self.assertEqual(self.runtime.session_status()["phase"], "stopped")
        timestamps = {}
        for row in rows:
            key = (row["Surface"], row["ObservedAt"])
            timestamps[key] = timestamps.get(key, 0) + 1
        self.assertEqual(set(timestamps.values()), {10})
        self.assertEqual(len(self.checker.calls), 110)

    def test_warmup_and_readiness_are_separate_gates(self):
        self.session()
        with self.assertRaises(CommandConflict):
            self.command()
        self.advance(600)
        with self.assertRaises(CommandConflict):
            self.command()
        self.checker.ready = False
        self.runtime.cycle()
        with self.assertRaises(CommandConflict):
            self.command()
        self.checker.ready = True
        self.advance()
        self.runtime.cycle()
        before = self.state()["generated_records"]
        self.command()
        self.assertEqual(self.state()["generated_records"], before)
        self.assertEqual(self.runtime.snapshot("power-grid")["value"], 12)
        self.advance()
        self.runtime.cycle()
        self.assertEqual(self.runtime.snapshot("power-grid")["value"], 0)

    def test_anomaly_substitutes_same_identity_and_recovers_on_next_sample(self):
        self.ready()
        self.command(duration_seconds=60)
        self.advance(30)
        self.runtime.cycle()
        self.assertEqual(self.runtime.snapshot("power-grid")["value"], 12)
        self.advance(30)
        self.runtime.cycle()
        self.assertEqual(self.runtime.snapshot("power-grid")["value"], 12)
        self.command(duration_seconds=300)
        self.advance()
        self.runtime.cycle()
        self.assertEqual(self.runtime.snapshot("power-grid")["value"], 0)
        self.advance(240)
        self.runtime.cycle()
        self.assertEqual(self.runtime.snapshot("power-grid")["value"], 12)
        self.assertIsNone(self.runtime.status("power-grid")["active_anomaly"])
        self.runtime.cycle()
        rows = [r for r in self.observations() if r["Surface"] == "power-grid"]
        self.assertEqual({r["EntityId"] for r in rows}, {f"fictional-{i}" for i in range(10)})
        self.assertNotIn("loss", events.encoded(rows).decode())

    def test_recover_and_reset_hold_last_published_model_until_next_due_tick(self):
        self.ready()
        self.command()
        self.advance()
        self.runtime.cycle()
        self.assertEqual(self.runtime.snapshot("power-grid")["value"], 0)
        generated = self.state()["generated_records"]
        self.command("recover", None)
        self.assertEqual(self.runtime.snapshot("power-grid")["value"], 0)
        self.assertEqual(self.state()["generated_records"], generated)
        self.advance()
        self.runtime.cycle()
        self.assertEqual(self.runtime.snapshot("power-grid")["value"], 12)
        self.command()
        self.command("reset", None)
        self.assertEqual(self.state()["generated_records"], generated + 90)
        self.assertIsNone(self.runtime.status("power-grid")["active_anomaly"])

    def test_expired_state_rejects_commands_even_before_worker_wakes(self):
        self.ready()
        deadline = self.state()["deadline"]
        self.advance(7200)
        with self.assertRaises(CommandConflict):
            self.command()
        writes = self.store.writes
        self.assertFalse(self.runtime.session_status()["running"])
        self.assertEqual(self.store.writes, writes)
        self.runtime = self.make_runtime()
        self.runtime.cycle()
        self.assertEqual(self.state()["deadline"], deadline)
        self.assertFalse(self.state()["running"])

    def test_restart_skips_missed_ticks_without_resetting_deadline(self):
        self.session()
        self.runtime.cycle()
        deadline = self.state()["deadline"]
        self.advance(301)
        self.runtime = self.make_runtime()
        self.runtime.cycle()
        self.assertEqual(self.state()["generated_records"], 180)
        self.assertEqual(self.state()["deadline"], deadline)
        self.runtime.cycle()
        self.assertEqual(self.state()["generated_records"], 180)

    def test_manual_stop_and_repeat_do_not_flood_audits(self):
        self.ready()
        self.session("stop")
        state = self.state()
        for _ in range(10):
            self.session("stop")
        self.assertEqual(self.state()["pending"], state["pending"])
        self.advance(1000)
        self.runtime.cycle()
        self.assertEqual(self.state()["generated_records"], state["generated_records"])
        with self.assertRaises(CommandConflict):
            self.command("reset", None)

    def test_new_prepare_preserves_backlog_ids_and_totals(self):
        self.ready()
        self.session("stop")
        before = self.state()
        self.session(surfaces=["power-grid"])
        after = self.state()
        self.assertNotEqual(after["session_id"], before["session_id"])
        self.assertEqual(after["pending"][:len(before["pending"])], before["pending"])
        self.assertEqual(after["accepted_records"], before["accepted_records"])
        self.assertGreater(after["sequence"], before["sequence"])
        with self.assertRaises(CommandConflict):
            self.session()

    def test_compact_state_and_bounded_persistent_journal(self):
        self.ready()
        self.session("stop")
        for _ in range(180):
            self.session("stop")
        state = self.state()
        del state["pending"]
        self.assertLess(len(events.encoded(state)), 100_000)
        self.assertEqual(len(state["commands"]), 128)
        self.assertNotIn("large immutable fixture", events.encoded(state).decode())

    def test_strict_values_and_idempotency(self):
        for key in (None, "", str(uuid.uuid4()).upper(), str(uuid.uuid4()).replace("-", "")):
            with self.assertRaises(ValueError):
                self.runtime.session_command("prepare", key=key)
        for duration in (True, 0, 599, 7201, 900.5, "7200"):
            with self.assertRaises(ValueError):
                self.session(duration_seconds=duration)
        for surfaces in ([], ["power-grid", "power-grid"], ["invalid"], "power-grid", [None]):
            with self.assertRaises(ValueError):
                self.session(surfaces=surfaces)
        key = str(uuid.uuid4())
        first = self.session(key=key)
        writes = self.store.writes
        self.assertEqual(first, self.session(key=key))
        self.assertEqual(writes, self.store.writes)
        with self.assertRaises(CommandConflict):
            self.session("stop", key=key)
        for duration in (True, 59, 901, "300"):
            with self.assertRaises(ValueError):
                self.command(duration_seconds=duration)
        for seed in (True, 41, 42.0):
            with self.assertRaises(ValueError):
                self.command(seed=seed)
        for action in ("start", "stop", "retry_delivery", [], None):
            with self.assertRaises(ValueError):
                self.command(action, None)

    def test_unselected_surface_rejected_and_anomaly_capped_at_deadline(self):
        self.session(surfaces=["power-grid"], duration_seconds=660)
        self.advance(600)
        self.runtime.cycle()
        with self.assertRaises(CommandConflict):
            self.command(surface="upstream")
        self.command(duration_seconds=900)
        self.assertEqual(self.state()["anomalies"]["power-grid"]["until"], self.state()["deadline"])

    def test_cas_conflicts_retry_three_times_without_duplicate_actions(self):
        self.store.conflicts = 2
        self.session()
        self.assertEqual(len(self.state()["pending"]), 1)
        self.assertEqual(self.store.writes, 1)
        self.store.conflicts = 3
        with self.assertRaises(StoreConflict):
            self.session("stop")
        self.assertTrue(self.state()["running"])

    def test_backpressure_is_atomic_and_controls_always_work(self):
        self.session()
        self.runtime.pending_limit = 4500
        before = self.state()
        self.runtime._tick(self.now)
        after = self.state()
        self.assertEqual(after["models"], before["models"])
        self.assertEqual(after["generated_records"], 0)
        self.assertIn("backlog", after["last_error"])
        self.runtime.pending_limit = 1
        self.runtime.daily_limit = 1
        self.session("stop")
        self.session("retry_delivery")
        self.assertFalse(self.state()["running"])
        self.assertEqual([b["records"][0]["Action"] for b in self.state()["pending"]], ["prepare", "stop"])

    def test_rejected_injection_does_not_partially_mutate_or_journal(self):
        self.ready()
        before = self.state()
        self.runtime.pending_limit = 1
        key = str(uuid.uuid4())
        with self.assertRaises(Backpressure):
            self.command(key=key)
        after = self.state()
        self.assertEqual(after, before)
        self.assertNotIn(key, after["commands"])

    def test_oversize_replay_metadata_is_not_persisted(self):
        self.session()
        original = Replay.sample

        def oversized(*args):
            frame = original(*args)
            frame["model"]["catalog"] = "x" * 100_000
            return frame
        before = self.state()
        with patch.object(Replay, "sample", oversized):
            with self.assertRaises(ValueError):
                self.runtime._tick(self.now)
        self.assertEqual(self.state(), before)

    def test_partial_delivery_and_one_ack_commit(self):
        self.session()
        self.runtime._tick(self.now)
        calls = 0

        def fail_second(table, rows):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise DeliveryError("Throttled", True, 120)
        self.sender.hook = fail_second
        before = self.store.writes
        self.runtime._deliver(self.now)
        self.assertEqual(self.store.writes - before, 1)
        state = self.state()
        self.assertEqual(state["accepted_records"], 1)
        self.assertEqual(len(state["pending"]), 1)
        self.assertEqual(state["pending"][0]["attempts"], 1)
        self.assertEqual(state["pending"][0]["next_attempt"], self.now.timestamp() + 120)
        self.assertIn("Throttled", self.runtime.session_status()["last_error"])

    def test_ack_loss_replays_identical_event_ids(self):
        self.session()
        self.session("stop")
        self.store.fail_ack = True
        self.runtime.cycle()
        first = deepcopy(self.sender.calls)
        self.assertEqual(self.runtime.worker_error, "StoreUnavailable")
        self.assertEqual(self.state()["accepted_records"], 0)
        self.store.fail_ack = False
        self.runtime.cycle()
        self.assertEqual(self.sender.calls[len(first):], first)
        self.assertEqual(self.state()["pending"], [])

    def test_delivery_parks_then_manual_retry_uses_same_records(self):
        self.session()
        self.session("stop")
        original = [b["records"] for b in self.state()["pending"]]
        self.sender.failure = DeliveryError("Forbidden", False)
        self.runtime.cycle()
        self.assertEqual(self.runtime.session_status()["parked_batches"], 2)
        self.runtime.pending_limit = 1
        self.runtime.daily_limit = 1
        self.session("retry_delivery")
        self.sender.failure = None
        self.runtime.cycle()
        self.assertEqual([rows for _, rows in self.sender.calls[-2:]], original)
        self.assertEqual(self.runtime.session_status()["pending_records"], 0)

    def test_network_upload_does_not_hold_mutation_lock(self):
        self.session()
        failures = []

        def stop_while_uploading(table, rows):
            thread = threading.Thread(target=lambda: self.session("stop"))
            thread.start()
            thread.join(2)
            if thread.is_alive():
                failures.append("mutation blocked")
        self.sender.hook = stop_while_uploading
        self.runtime.cycle()
        self.assertEqual(failures, [])
        self.assertFalse(self.state()["running"])
        self.assertEqual(self.state()["generated_records"], 0)

    def test_upload_crossing_deadline_cannot_generate(self):
        self.session(duration_seconds=600)
        self.sender.hook = lambda *_: self.advance(601)
        self.runtime.cycle()
        self.assertEqual(self.state()["generated_records"], 0)
        self.assertFalse(self.state()["running"])

    def test_readiness_latches_proof_but_reports_current_query_failure(self):
        self.ready()
        proof = deepcopy(self.state()["baseline_readiness"])
        self.checker.error = "QueryForbidden"
        self.advance()
        self.runtime.cycle()
        status = self.runtime.session_status()
        self.assertTrue(status["ready"])
        self.assertEqual(status["baseline_readiness"], proof)
        self.assertEqual(status["readiness"]["error"], "QueryForbidden")
        self.assertIn("QueryForbidden", status["last_error"])
        before = len(self.checker.calls)
        self.runtime.session_status()
        self.runtime.status("power-grid")
        self.runtime.cycle()
        self.assertEqual(len(self.checker.calls), before)

    def test_local_checker_never_claims_azure_baseline_queryability(self):
        self.runtime = self.make_runtime(readiness=LocalReadinessChecker(clock=lambda: self.now))
        self.session()
        self.advance(600)
        self.runtime.cycle()
        status = self.runtime.session_status()
        self.assertFalse(status["ready"])
        self.assertFalse(status["readiness"]["ready"])
        self.assertEqual(status["readiness"]["error"], "LocalCaptureNotConfigured")
        self.assertIsNone(status["baseline_readiness"])
        self.assertEqual(status["access_probe"]["code"], "LocalOnly")
        self.assertFalse(status["access_probe"]["verified"])
        with self.assertRaises(CommandConflict):
            self.command()

    def test_normal_capture_rows_enable_local_injection_without_azure_claim(self):
        files = []
        directory = Mock()
        directory.is_dir.return_value = True
        directory.glob.return_value = files
        checker = LocalReadinessChecker(clock=lambda: self.now)
        checker.directory = directory
        self.runtime = self.make_runtime(readiness=checker)

        def capture(table, rows):
            data = events.encoded({"delivery_mode": "local_capture", "table": table, "records": rows})
            path = Mock()
            path.name = f"{uuid.uuid4().hex}.json"
            path.is_symlink.return_value = False
            path.open.side_effect = lambda *_: BytesIO(data)
            path.stat.return_value = Mock(st_mtime=self.now.timestamp())
            files.append(path)
        self.sender.hook = capture
        self.session()
        for _ in range(10):
            self.runtime.cycle()
            self.advance()
        self.runtime.cycle()
        status = self.runtime.session_status()
        self.assertTrue(status["ready"])
        self.assertEqual(status["readiness"]["status"], "local")
        self.assertEqual(status["baseline_readiness"]["status"], "local")
        self.assertFalse(status["access_probe"]["verified"])
        self.assertEqual(status["access_probe"]["code"], "LocalOnly")
        for evidence in status["readiness"]["surfaces"].values():
            self.assertGreaterEqual(evidence["distinct_observed_at"], 9)
            self.assertIsNone(evidence["last_ingested_at"])
        self.command()
        self.advance()
        self.runtime.cycle()
        self.assertEqual(self.runtime.snapshot("power-grid")["value"], 0)

    def test_access_probe_runs_in_worker_and_caches_success_per_process(self):
        self.runtime = self.make_runtime(delivery_mode="azure")
        self.assertEqual(self.runtime.session_status()["access_probe"]["code"], "NotChecked")
        self.assertEqual(self.checker.probe_calls, 0)
        self.runtime.cycle()
        self.assertTrue(self.runtime.session_status()["access_probe"]["verified"])
        self.assertEqual(self.checker.probe_calls, 1)
        for _ in range(5):
            self.advance(600)
            self.runtime.cycle()
            self.runtime.session_status()
        self.assertEqual(self.checker.probe_calls, 1)
        self.runtime = self.make_runtime(delivery_mode="azure")
        self.assertFalse(self.runtime.session_status()["access_probe"]["verified"])
        self.runtime.cycle()
        self.assertEqual(self.checker.probe_calls, 2)
        self.assertEqual(self.store.writes, 0)

    def test_failed_access_probe_retries_once_per_minute_and_surfaces_code(self):
        self.runtime = self.make_runtime(delivery_mode="azure")
        self.checker.probe_result.update(verified=False, audit_denied=False, code="AuditQueryNotDenied")
        self.runtime.cycle()
        status = self.runtime.session_status()["access_probe"]
        self.assertFalse(status["verified"])
        self.assertEqual(status["code"], "AuditQueryNotDenied")
        self.advance(59)
        self.runtime.cycle()
        self.assertEqual(self.checker.probe_calls, 1)
        self.advance(1)
        self.checker.probe_result.update(verified=True, audit_denied=True, code="AccessVerified")
        self.runtime.cycle()
        self.assertEqual(self.checker.probe_calls, 2)
        self.assertTrue(self.runtime.session_status()["access_probe"]["verified"])

    def test_audit_zero_probe_crossing_hour_cannot_be_correlated_to_wrong_window(self):
        self.now = datetime(2026, 9, 10, 15, 59, 59, tzinfo=timezone.utc)
        self.runtime = self.make_runtime(delivery_mode="azure")

        def probe():
            self.advance(2)
            return {"verified": False, "observations_allowed": True,
                    "audit_denied": False, "code": "AuditNoVisibleRows"}

        self.checker.access_probe = probe
        self.runtime.cycle()
        result = self.runtime.session_status()["access_probe"]
        self.assertEqual(result["code"], "ProbeWindowChanged")
        self.assertFalse(result["verified"])
        self.assertFalse(result["audit_denied"])

    def test_local_access_probe_is_explicit_local_only_and_does_not_block_prepare(self):
        checker = LocalReadinessChecker(clock=lambda: self.now)
        self.runtime = self.make_runtime(readiness=checker)
        with patch.object(checker, "access_probe", wraps=checker.access_probe) as probe:
            self.runtime.cycle()
            self.advance(600)
            self.runtime.cycle()
            self.assertEqual(probe.call_count, 1)
        result = self.runtime.session_status()["access_probe"]
        self.assertFalse(result["verified"])
        self.assertEqual(result["code"], "LocalOnly")
        self.session()
        self.assertTrue(self.runtime.session_status()["running"])

    def test_access_probe_errors_are_sanitized_and_do_not_hold_mutation_lock(self):
        self.runtime = self.make_runtime(delivery_mode="azure")
        self.session()
        failures = []

        def probe():
            thread = threading.Thread(target=lambda: self.session("stop"))
            thread.start()
            thread.join(2)
            if thread.is_alive():
                failures.append("mutation blocked")
            raise ReadinessError("QueryForbidden")
        self.runtime._access_complete = False
        self.runtime._access_next = None
        self.checker.access_probe = probe
        self.runtime.cycle()
        self.assertEqual(failures, [])
        self.assertFalse(self.state()["running"])
        self.assertEqual(self.runtime.session_status()["access_probe"]["code"], "QueryForbidden")
        self.assertFalse(self.runtime.session_status()["access_probe"]["verified"])

    def test_malformed_access_probe_cannot_expose_arbitrary_response_fields(self):
        self.runtime = self.make_runtime(delivery_mode="azure")
        self.checker.probe_result = {"verified": True, "observations_allowed": True,
            "audit_denied": True, "code": "server response with arbitrary content",
            "response": "not safe for exposure"}
        self.runtime.cycle()
        result = self.runtime.session_status()["access_probe"]
        self.assertEqual(result["code"], "MalformedAccessProbe")
        self.assertNotIn("response", result)
        self.assertFalse(result["verified"])

    def test_unexpected_probe_failure_is_visible_sanitized_and_rate_limited(self):
        self.runtime = self.make_runtime(delivery_mode="azure")
        with patch.object(self.checker, "access_probe", side_effect=RuntimeError("private response")) as probe:
            self.runtime.cycle()
            self.runtime.cycle()
            self.assertEqual(probe.call_count, 1)
        result = self.runtime.session_status()["access_probe"]
        self.assertEqual(result["code"], "AccessProbeFailed")
        self.assertNotIn("private", events.encoded(result).decode())

    def test_network_query_does_not_hold_mutation_lock(self):
        self.session()
        self.advance(600)
        original = self.checker.check
        failures = []

        def check(*args):
            thread = threading.Thread(target=lambda: self.session("stop"))
            thread.start()
            thread.join(2)
            if thread.is_alive():
                failures.append("mutation blocked")
            return original(*args)
        self.checker.check = check
        self.runtime.cycle()
        self.assertEqual(failures, [])
        self.assertFalse(self.state()["running"])
        self.assertFalse(self.state()["ready"])

    def test_delivery_attempts_are_bounded_across_restarts(self):
        self.session()
        self.session("stop")
        self.sender.failure = DeliveryError("Throttled", True)
        for _ in range(5):
            self.runtime = self.make_runtime()
            self.runtime.cycle()
            self.advance(301)
        self.assertEqual(self.runtime.session_status()["parked_batches"], 2)
        calls = len(self.sender.calls)
        self.runtime = self.make_runtime()
        self.runtime.cycle()
        self.assertEqual(len(self.sender.calls), calls)

    def test_ack_conflicts_do_not_resend_within_same_cycle(self):
        self.session()
        self.session("stop")
        self.store.conflicts = 2
        self.runtime._deliver(self.now)
        self.assertEqual(len(self.sender.calls), 2)
        self.assertEqual(self.state()["pending"], [])

    def test_old_session_readiness_cannot_mark_new_session_ready(self):
        self.session()
        self.advance(600)
        original = self.checker.check

        def change_session(*args):
            self.session("stop")
            self.session()
            return original(*args)
        self.checker.check = change_session
        self.runtime.cycle()
        self.assertFalse(self.state()["ready"])
        self.assertIsNone(self.state()["baseline_readiness"])

    def test_replay_mismatch_never_resets_active_state(self):
        self.session()
        before = deepcopy(self.store.docs)
        class Changed(Replay):
            VERSION = "test-v2"
        runtime = self.make_runtime(replay=Changed)
        with self.assertRaises(ValueError):
            runtime.session_status()
        self.assertEqual(self.store.docs, before)

    def test_real_authored_replay_integrates_all_nine_surfaces(self):
        from energy_domains_v2 import replay
        self.runtime = self.make_runtime(replay=replay)
        for surface in SURFACES:
            self.assertEqual(self.runtime.snapshot(surface)["replay"]["view_state"], "preview_not_queued")
        self.assertEqual(self.store.writes, 0)
        self.session()
        self.runtime.cycle()
        self.assertEqual(self.state()["generated_records"], 90)
        for surface in SURFACES:
            self.assertEqual(self.runtime.snapshot(surface)["replay"]["view_state"], "last_queued")
        state = self.state()
        del state["pending"]
        self.assertLess(len(events.encoded(state)), 100_000)
        self.advance(600)
        self.runtime.cycle()
        for surface in SURFACES:
            scenario = replay.scenarios(surface)[0]["id"]
            self.command(scenario=scenario, surface=surface)
        self.assertEqual(self.state()["generated_records"], 180)
        self.advance()
        self.runtime.cycle()
        self.assertEqual(self.state()["generated_records"], 270)
        for surface in SURFACES:
            self.assertEqual(self.runtime.snapshot(surface)["replay"]["scenario"],
                             replay.scenarios(surface)[0]["id"])

    def test_legacy_backlog_is_drained_without_changing_other_fields(self):
        old = {"version": 1, "surface": "power-grid", "running": True,
               "snapshot": {"keep": [1, 2, 3]}, "model": {"scenario": "loss"},
               "pending": events.batches(events.AUDIT, [{"EventId": "old-id", "Action": "inject"}]),
               "accepted_records": 10, "last_error": None, "commands": {"keep": "this"}}
        self.store.write("power-grid", old, None)
        self.runtime.cycle()
        current = self.store.read("power-grid")[0]
        self.assertEqual(current["pending"], [])
        self.assertEqual(current["accepted_records"], 11)
        for key in ("version", "surface", "running", "snapshot", "model", "commands"):
            self.assertEqual(current[key], old[key])
        self.assertEqual(self.sender.calls[0][1][0]["EventId"], "old-id")
        reads = self.store.reads["power-grid"]
        for _ in range(5):
            self.runtime.cycle()
        self.assertEqual(self.store.reads["power-grid"], reads)
        self.assertIsNone(self.state())

    def test_legacy_parked_backlog_is_visible_and_manual_retry_unparks(self):
        batch = events.batches(events.AUDIT, [{"EventId": "old-id"}])[0]
        batch.update(error="Forbidden", next_attempt=None, attempts=5)
        self.store.write("upstream", {"pending": [batch], "accepted_records": 0,
                                     "last_error": "Forbidden", "running": True}, None)
        self.runtime.cycle()
        self.assertEqual(self.runtime.session_status()["parked_batches"], 1)
        reads = self.store.reads["upstream"]
        self.runtime.cycle()
        self.assertEqual(self.store.reads["upstream"], reads)
        self.session("retry_delivery")
        self.runtime.cycle()
        self.assertEqual(self.runtime.session_status()["pending_records"], 0)
        self.assertEqual(self.sender.calls[0][1][0]["EventId"], "old-id")

    def test_prepare_waits_for_startup_scan_and_all_legacy_pending_to_drain(self):
        batch = events.batches(events.AUDIT, [{"EventId": "old-id"}])[0]
        batch.update(error="Forbidden", next_attempt=None, attempts=5)
        self.store.write("upstream", {"pending": [batch], "accepted_records": 0,
                                     "last_error": "Forbidden", "running": True}, None)
        with self.assertRaisesRegex(CommandConflict, "discovery"):
            self.runtime.session_command("prepare", key=str(uuid.uuid4()))
        status = self.runtime.session_status()
        self.assertFalse(status["legacy_scan_complete"])
        self.assertTrue(status["prepare_blocked"])
        self.runtime.cycle()
        status = self.runtime.session_status()
        self.assertTrue(status["legacy_scan_complete"])
        self.assertEqual(status["legacy_pending_records"], 1)
        self.assertTrue(status["prepare_blocked"])
        with self.assertRaisesRegex(CommandConflict, "legacy delivery backlog"):
            self.session()
        self.assertIsNone(self.state())
        self.session("retry_delivery")
        self.runtime.cycle()
        self.assertFalse(self.runtime.session_status()["prepare_blocked"])
        self.assertEqual(self.runtime.session_status()["legacy_pending_records"], 0)
        self.session()
        self.assertTrue(self.runtime.session_status()["running"])
        self.assertEqual(self.sender.calls[0][1][0]["EventId"], "old-id")

    def test_failed_startup_scan_cannot_be_mistaken_for_an_empty_backlog(self):
        with patch.object(self.store, "keys", side_effect=StoreUnavailable("discovery unavailable")):
            self.runtime.cycle()
        self.assertFalse(self.runtime.session_status()["legacy_scan_complete"])
        with self.assertRaisesRegex(CommandConflict, "discovery"):
            self.runtime.session_command("prepare", key=str(uuid.uuid4()))
        self.assertIsNone(self.state())
        self.runtime.cycle()
        self.session()
        self.assertTrue(self.runtime.session_status()["running"])

    def test_old_manual_retry_is_not_reapplied_to_legacy_after_restart(self):
        batch = events.batches(events.AUDIT, [{"EventId": "old-id"}])[0]
        self.store.write("upstream", {"pending": [batch], "accepted_records": 0,
                                     "last_error": None, "running": True}, None)
        self.session("retry_delivery")
        self.sender.failure = DeliveryError("Forbidden", False)
        self.runtime.cycle()
        self.assertEqual(self.runtime.session_status()["parked_batches"], 1)
        calls = len(self.sender.calls)
        self.runtime = self.make_runtime()
        self.runtime.cycle()
        self.assertEqual(len(self.sender.calls), calls)
        self.assertEqual(self.runtime.session_status()["parked_batches"], 1)

    def test_burst_is_bounded_across_global_and_legacy_outboxes(self):
        self.session()
        self.session("stop")
        for surface in SURFACES:
            self.store.write(surface, {"pending": events.batches(events.AUDIT, [{"EventId": surface}]),
                                     "accepted_records": 0, "last_error": None}, None)
        self.runtime = self.make_runtime()
        self.runtime.cycle()
        self.assertEqual(len(self.sender.calls), 8)
        self.runtime.cycle()
        self.assertEqual(len(self.sender.calls), 11)

    def test_app_csrf_allowlists_idempotency_and_no_startup_smoke(self):
        with patch.dict("os.environ", {"V2_DEPLOY_SMOKE_TOKEN": str(uuid.uuid4())}):
            app = create_app(self.runtime, worker=False)
        client = app.test_client()
        self.assertEqual(client.get("/api/v2/demo").status_code, 200)
        self.assertEqual(self.store.writes, 0)
        self.assertEqual(client.post("/api/v2/demo/commands", json={"action": "prepare"}).status_code, 403)
        token = client.get("/api/v2/session").json["csrf_token"]
        headers = {"Origin": "http://127.0.0.1:8880", "X-CSRF-Token": token,
                   "Idempotency-Key": str(uuid.uuid4())}
        self.runtime.cycle()
        result = client.post("/api/v2/demo/commands", json={"action": "prepare"}, headers=headers)
        self.assertEqual(result.status_code, 202)
        self.assertEqual(client.post("/api/v2/demo/commands", json={"action": "prepare"},
                                    headers=headers).json, result.json)
        self.assertEqual(client.post("/api/v2/demo/commands", json={"action": "stop"},
                                    headers=headers).status_code, 409)
        for body in ({"action": "prepare", "unknown": 1}, {"action": "prepare", "surfaces": None},
                     {"action": "stop", "surfaces": []},
                     {"action": []}, ["prepare"]):
            headers["Idempotency-Key"] = str(uuid.uuid4())
            self.assertEqual(client.post("/api/v2/demo/commands", json=body, headers=headers).status_code, 400)
        self.assertEqual(client.post("/api/v2/surfaces/power-grid/commands",
            json={"action": "inject", "scenario": "loss", "duration_seconds": 300},
            headers=headers).status_code, 400)
        self.assertEqual(client.post("/api/v2/surfaces/power-grid/commands",
            json={"action": "inject", "scenario": "loss", "anomaly_duration_seconds": 300},
            headers=headers).status_code, 409)

    def test_configured_local_app_uses_replay_without_startup_generation(self):
        with patch.dict("os.environ", {"V2_MODE": "local"}, clear=True), \
                patch("energy_domains_v2.app.LocalStateStore", return_value=self.store), \
                patch("energy_domains_v2.app.LocalCaptureSender", return_value=self.sender):
            runtime = configured_runtime()
            app = create_app(runtime, worker=False)
        self.assertIsInstance(runtime, DemoRuntime)
        self.assertIsInstance(runtime.readiness, LocalReadinessChecker)
        from energy_domains_v2.paths import KIT_ROOT
        self.assertEqual(runtime.readiness.directory, KIT_ROOT / ".local" / "capture")
        response = app.test_client().get("/api/v2/demo")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["phase"], "idle")
        self.assertEqual(self.store.writes, 0)
        self.assertEqual(self.sender.calls, [])

    def test_configured_azure_app_requires_workspace_query_identity(self):
        workspace = str(uuid.uuid4())
        env = {"V2_MODE": "azure", "V2_STORAGE_ACCOUNT_URL": "https://example.blob.core.windows.net",
               "V2_STORAGE_CONTAINER": "state", "V2_LOGS_ENDPOINT": "https://example.ingest.monitor.azure.com",
               "V2_DCR_IMMUTABLE_ID": "dcr-example", "V2_LOG_ANALYTICS_WORKSPACE_ID": workspace}
        with patch.dict("os.environ", env, clear=True), \
                patch("energy_domains_v2.app.BlobStateStore", return_value=self.store), \
                patch("energy_domains_v2.app.IngestionSender", return_value=self.sender), \
                patch("energy_domains_v2.readiness.AzureReadinessChecker", return_value=self.checker) as checker:
            runtime = configured_runtime()
        checker.assert_called_once_with(workspace)
        self.assertIsInstance(runtime, DemoRuntime)
        self.assertEqual(runtime.delivery_mode, "azure")
        self.assertEqual(self.store.writes, 0)
        self.assertEqual(self.sender.calls, [])


if __name__ == "__main__":
    unittest.main()
