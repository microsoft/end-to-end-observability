from copy import deepcopy
from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import unittest

from energy_domains_v2.contracts import SURFACES
from energy_domains_v2.demo_runtime import DemoRuntime, STATE_KEY
from energy_domains_v2.transport import StoreConflict

spec = spec_from_file_location("transition_replay_state",
                              Path(__file__).parents[1] / "scripts" / "transition_replay_state.py")
transition_module = module_from_spec(spec)
spec.loader.exec_module(transition_module)


class Replay:
    @staticmethod
    def initial_model(surface, seed):
        return {"fixture_version": "new"}


class Store:
    def __init__(self, state):
        self.docs = {STATE_KEY: (deepcopy(state), "etag-1")}
        self.writes = []
        self.conflict = False
        self.archive_hook = None

    def keys(self):
        return sorted(self.docs)

    def read(self, key):
        return deepcopy(self.docs.get(key, (None, None)))

    def write(self, key, document, etag):
        if self.read(key)[1] != etag or (key == STATE_KEY and self.conflict):
            raise StoreConflict("Conflict")
        next_etag = f"etag-{len(self.writes) + 2}"
        self.docs[key] = (deepcopy(document), next_etag)
        self.writes.append(key)
        if key != STATE_KEY and self.archive_hook:
            self.archive_hook(self)
        return next_etag


class TransitionTests(unittest.TestCase):
    def setUp(self):
        self.state = DemoRuntime(None, None, {}, replay=Replay, readiness=None)._initial()
        self.state.update(replay_version="old", phase="stopped", session_id="prior-run",
                          accepted_records=123, generated_records=120,
                          day="2026-09-12", daily_bytes=456,
                          published={"history": {"unknown": [None, 0, "µ"]}},
                          published_envelopes={"power-grid": [{"EventId": "prior-event"}]},
                          surface_generated={"power-grid": 120}, sequence=9)
        self.store = Store(self.state)

    def run_transition(self, **kwargs):
        return transition_module.transition(self.store, Replay, "old", **kwargs)

    def apply(self):
        return self.run_transition(apply=True, writers_stopped=True)

    def test_dry_run_and_stopped_drained_success(self):
        plan = self.run_transition()
        self.assertEqual(plan["status"], "dry_run")
        self.assertEqual(self.store.writes, [])
        result = self.apply()
        archive = self.store.read(result["archive_key"])[0]
        self.assertEqual(archive["source_state"], self.state)
        self.assertEqual(archive["source_etag"], "etag-1")
        self.assertEqual(archive["source_sha256"], transition_module.digest(self.state))
        self.assertNotIn(result["archive_key"], SURFACES)
        self.assertLessEqual(len(result["archive_key"]), 80)
        state = self.store.read(STATE_KEY)[0]
        self.assertEqual(state["replay_version"], "new")
        self.assertEqual(state["phase"], "idle")
        self.assertEqual(state["models"], {})
        self.assertEqual(state["published"], {})
        self.assertEqual(state["published_envelopes"], {})
        for name in transition_module.ACCOUNTING:
            self.assertEqual(state[name], self.state[name])

    def test_active_pending_parked_and_unknown_pending_rejected(self):
        for update in ({"running": True}, {"phase": "warming"},
                       {"pending": [{"records": [], "next_attempt": None}]},
                       {"pending": None}):
            with self.subTest(update=update):
                self.store = Store({**self.state, **update})
                with self.assertRaises(ValueError):
                    self.apply()
                self.assertEqual(self.store.writes, [])

    def test_legacy_pending_or_active_rejected(self):
        for update in ({"pending": [{"records": [1], "next_attempt": None}]},
                       {"running": True}):
            self.store = Store(self.state)
            self.store.docs[SURFACES[0]] = ({"running": False, "pending": [], **update}, "legacy")
            with self.assertRaises(ValueError):
                self.apply()
            self.assertEqual(self.store.writes, [])

    def test_wrong_version_and_missing_writer_confirmation(self):
        with self.assertRaises(ValueError):
            transition_module.transition(self.store, Replay, "wrong", apply=True, writers_stopped=True)
        with self.assertRaises(ValueError):
            self.run_transition(apply=True)
        self.assertEqual(self.store.writes, [])

    def test_cas_conflict_leaves_lossless_archive_and_old_state(self):
        self.store.conflict = True
        with self.assertRaises(StoreConflict):
            self.apply()
        self.assertEqual(self.store.read(STATE_KEY)[0], self.state)
        self.assertEqual(len(self.store.writes), 1)
        self.store.conflict = False
        self.assertEqual(self.apply()["status"], "applied")

    def test_existing_archive_conflict(self):
        key = transition_module.archive_key("old", "new")
        self.store.docs[key] = ({"different": True}, "archive")
        with self.assertRaises(StoreConflict):
            self.apply()
        self.assertEqual(self.store.writes, [])

    def test_rerun_is_verified_noop(self):
        self.apply()
        writes = list(self.store.writes)
        self.assertEqual(self.apply()["status"], "already_applied")
        self.assertEqual(self.store.writes, writes)
        key = transition_module.archive_key("old", "new")
        self.store.docs[key][0]["source_state"]["daily_bytes"] = 0
        with self.assertRaises(ValueError):
            self.apply()

    def test_runtime_ignores_archive_and_still_refuses_implicit_reset(self):
        runtime = DemoRuntime(self.store, None, {}, replay=Replay, readiness=None)
        with self.assertRaises(ValueError):
            runtime._load()
        self.assertEqual(self.store.writes, [])
        self.apply()
        runtime._drain_legacy(datetime.now(timezone.utc))
        self.assertTrue(runtime._legacy_scanned)
        self.assertEqual(runtime._legacy, set())
        self.assertEqual(runtime._load()[0]["replay_version"], "new")

    def test_drained_legacy_state_is_not_modified(self):
        legacy = {"running": False, "pending": [], "accepted_records": 90}
        self.store.docs[SURFACES[0]] = (deepcopy(legacy), "legacy-etag")
        self.apply()
        self.assertEqual(self.store.read(SURFACES[0]), (legacy, "legacy-etag"))

    def test_completed_legacy_smoke_marker_is_preserved_and_rechecked(self):
        key = "smoke-" + "a" * 24
        marker = {"status": "completed", "surfaces": list(SURFACES)}
        self.store.docs[key] = (deepcopy(marker), "smoke-etag")
        self.apply()
        self.assertEqual(self.store.read(key), (marker, "smoke-etag"))
        self.store = Store(self.state)
        self.store.docs[key] = (deepcopy(marker), "smoke-etag")
        self.store.archive_hook = lambda store: store.docs.update(
            {key: (deepcopy(marker), "changed-etag")})
        with self.assertRaises(StoreConflict):
            self.apply()
        self.assertEqual(self.store.read(STATE_KEY)[0], self.state)

    def test_unknown_or_incomplete_smoke_markers_remain_blocked(self):
        key = "smoke-" + "a" * 24
        for name, marker in (
            (key, {"status": "running", "surfaces": list(SURFACES)}),
            (key, {"status": "completed", "surfaces": []}),
            (key, {"status": "completed", "surfaces": list(SURFACES), "pending": []}),
            ("unknown", {"status": "completed", "surfaces": list(SURFACES)}),
        ):
            with self.subTest(key=name, marker=marker):
                self.store = Store(self.state)
                self.store.docs[name] = (marker, "marker-etag")
                with self.assertRaises(ValueError):
                    self.apply()
                self.assertEqual(self.store.writes, [])

    def test_concurrent_source_legacy_and_archive_changes_rejected(self):
        def source(store):
            state, _ = store.read(STATE_KEY)
            store.docs[STATE_KEY] = (state, "concurrent-etag")

        def legacy(store):
            store.docs[SURFACES[0]] = ({"running": False, "pending": []}, "new-key")

        def archive(store):
            key = transition_module.archive_key("old", "new")
            store.docs[key] = ({"corrupt": True}, "changed")

        for hook in (source, legacy, archive):
            with self.subTest(hook=hook.__name__):
                self.store = Store(self.state)
                self.store.archive_hook = hook
                with self.assertRaises(StoreConflict):
                    self.apply()
                self.assertEqual(self.store.read(STATE_KEY)[0], self.state)


if __name__ == "__main__":
    unittest.main()
