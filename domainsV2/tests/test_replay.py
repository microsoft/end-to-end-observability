"""Real packaged replay tests: no generation, Azure, clocks or stock fixtures."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

from flask import Flask

from energy_domains_v2 import events, replay
from energy_domains_v2.contracts import SURFACES, MONITORING_SURFACES, INVENTORY_SURFACES, domain_for


AT = "2026-09-10T16:00:00Z"


def signal(row):
    return replay._encoded({key: row["payload"].get(key) for key in (
        "value", "quantity", "quality", "origin_state", "delivery_state", "delivery_class",
    )})


class ReplayTests(unittest.TestCase):
    def test_public_version_is_verified_fixture_fingerprint(self):
        from energy_domains_v2.replay import VERSION
        self.assertEqual(VERSION, replay._manifest()["fixture_version"])
        self.assertEqual(len(VERSION), 64)
        for surface in SURFACES:
            self.assertEqual(replay.VERSION, replay.initial_model(surface)["fixture_version"])

    def test_every_anomaly_changes_same_identities_in_every_phase(self):
        for surface in SURFACES:
            original = replay.initial_model(surface)
            identities = replay._spec(surface)["identities"]
            self.assertEqual(len(identities), 10)
            for choice in replay.scenarios(surface):
                changed = replay.apply_action(surface, original, "inject", choice["id"])
                for phase in range(3):
                    with self.subTest(surface=surface, scenario=choice["id"], phase=phase):
                        normal = replay.sample(surface, original, phase, AT)
                        anomaly = replay.sample(surface, changed, phase, AT)
                        self.assertEqual([replay._identity(r) for r in normal["observations"]], identities)
                        self.assertEqual([replay._identity(r) for r in anomaly["observations"]], identities)
                        self.assertNotEqual(list(map(signal, normal["observations"])),
                                            list(map(signal, anomaly["observations"])))
                        rows = events.observations(anomaly, {"run_id": "replay-test", "surface": surface,
                                                           "sequence": phase}, domain_for(surface), AT)
                        self.assertEqual(len(rows), 10)
                        for row in rows:
                            self.assertLess(events.request_size(row["Payload"]), 48_000)
                            self.assertLess(events.request_size(row["Context"]), 48_000)
                        snapshot = replay.snapshot(surface, anomaly["model"])
                        self.assertEqual(snapshot["replay"]["scenario"], choice["id"])
                        self.assertEqual(snapshot["replay"]["phase"], phase)

    def test_normal_cycles_are_real_varying_monitoring_and_stable_stock(self):
        for surface in SURFACES:
            model = replay.initial_model(surface)
            sequence = [list(map(signal, replay.sample(surface, model, phase, AT)["observations"]))
                        for phase in range(3)]
            if surface in MONITORING_SURFACES:
                self.assertGreater(len(set(map(tuple, sequence))), 1)
            else:
                self.assertEqual(sequence[0], sequence[1])
            self.assertEqual(replay.sample(surface, model, 0, AT)["observations"],
                             replay.sample(surface, model, 3, AT)["observations"])

    def test_inventory_focus_null_zero_low_and_recovery(self):
        for surface in INVENTORY_SURFACES:
            model = replay.initial_model(surface)
            focus = replay._spec(surface)["inventory_focus"]

            def focused(candidate):
                return next(row for row in candidate["observations"]
                            if [row["entity_id"], row["location_id"]] == focus)["payload"]

            normal = focused(replay.sample(surface, model, 0, AT))
            for row in replay.sample(surface, model, 0, AT)["observations"]:
                self.assertGreater(row["payload"]["quantity"], 0)
                self.assertEqual(row["payload"]["quality"], "good")
            snapshot = replay.snapshot(surface, model)
            source = {(row["part_master_id"], row["location_id"]): row["quantity"]
                      for row in snapshot["inventory"]["stock"]}
            for row in replay.sample(surface, model, 0, AT)["observations"]:
                self.assertEqual(row["payload"]["quantity"], source[(row["entity_id"], row["location_id"])])
            self.assertIn("not universal health or threshold compliance", snapshot["inventory"]["stock_note"])
            for choice, quantity, quality in (
                ("low_stock", min(9, normal["quantity"] - 1), "good"),
                ("stockout", 0, "good"), ("observation_unavailable", None, "unavailable"),
            ):
                changed = replay.apply_action(surface, model, "inject", choice)
                anomaly = replay.sample(surface, changed, 0, AT)
                self.assertEqual(focused(anomaly)["quantity"], quantity)
                self.assertEqual(focused(anomaly)["quality"], quality)
                for action in ("recover", "reset"):
                    restored = replay.apply_action(surface, anomaly["model"], action)
                    self.assertEqual(focused(replay.sample(surface, restored, 1, AT)), normal)

    def test_last_queued_snapshot_not_future_intervention(self):
        for surface in SURFACES:
            model = replay.initial_model(surface)
            preview = replay.snapshot(surface, model)
            self.assertEqual(preview["replay"]["view_state"], "preview_not_queued")
            first = replay.sample(surface, model, 0, AT)["model"]
            normal = replay.snapshot(surface, first)
            selected = replay.apply_action(surface, first, "inject", replay.scenarios(surface)[0]["id"])
            self.assertEqual(replay.snapshot(surface, selected), normal)
            at = "2026-09-10T16:01:00Z"
            queued = replay.sample(surface, selected, 1, at)["model"]
            snapshot = replay.snapshot(surface, queued)
            self.assertEqual(snapshot["replay"]["scenario"], selected["scenario"])
            self.assertEqual(snapshot["replay"]["last_queued_at"], at)
            recovered = replay.apply_action(surface, queued, "recover")
            self.assertEqual(replay.snapshot(surface, recovered), snapshot)
            self.assertNotEqual(replay.snapshot(surface, replay.sample(surface, recovered, 2, at)["model"]), snapshot)

    def test_sample_compact_immutable_and_never_loads_snapshots(self):
        for surface in SURFACES:
            model = replay.initial_model(surface)
            before = deepcopy(model)
            with patch.object(replay, "_snapshot_fixture", side_effect=AssertionError("tick loaded snapshot")):
                first = replay.sample(surface, model, 0, AT)
                self.assertEqual(first, replay.sample(surface, model, 0, AT))
            self.assertEqual(model, before)
            self.assertLess(len(replay._encoded(first["model"])), 512)
            self.assertEqual(set(first), {"model", "observations"})
            first["observations"][0]["payload"]["quality"] = "mutated"
            self.assertNotEqual(replay.sample(surface, model, 0, AT)["observations"][0]["payload"]["quality"], "mutated")
            returned = replay.snapshot(surface, first["model"])
            returned["replay"]["scenario"] = "mutated"
            self.assertNotEqual(replay.snapshot(surface, first["model"])["replay"]["scenario"], "mutated")

    def test_current_utc_clock_preserves_source_age(self):
        for surface in SURFACES:
            model = replay.initial_model(surface)
            choices = [None] + [s["id"] for s in replay.scenarios(surface)]
            for choice in choices:
                selected = replay.apply_action(surface, model, "inject", choice) if choice else model
                first = replay.sample(surface, selected, 0, AT)
                later = replay.sample(surface, selected, 0, "2026-09-10T17:00:00Z")
                for a, b in zip(first["observations"], later["observations"]):
                    self.assertEqual(replay._utc(b["simulated_at"]) - replay._utc(a["simulated_at"]), timedelta(hours=1))
                    if a["payload"].get("source_time_utc"):
                        age_a = replay._utc(a["simulated_at"]) - replay._utc(a["payload"]["source_time_utc"])
                        age_b = replay._utc(b["simulated_at"]) - replay._utc(b["payload"]["source_time_utc"])
                        self.assertEqual(age_a, age_b)

    def test_manifest_and_every_file_hash_and_bounds(self):
        manifest = replay._manifest()
        entries = []
        for surface in SURFACES:
            spec = replay._spec(surface)
            entries.append(spec["observations"])
            entries.extend(entry for frames in spec["snapshots"].values() for entry in frames)
            for covered in spec["coverage"].values():
                self.assertTrue(all(covered))
        self.assertLessEqual(sum(e["compressed_bytes"] for e in entries), replay.MAX_PACKAGE_BYTES)
        for entry in entries:
            data = replay._load(entry)
            self.assertIsInstance(data, dict)
            self.assertLessEqual(entry["decoded_bytes"], replay.MAX_FILE_BYTES)
        self.assertEqual(sum(e["compressed_bytes"] for e in entries), manifest["total_compressed_bytes"])
        bad = dict(entries[0], sha256="0" * 64)
        with self.assertRaisesRegex(ValueError, "checksum"):
            replay._load(bad)
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            replay._load(dict(entries[0], path="../manifest.json"))

    def test_rejects_invalid_input_and_cross_surface_models(self):
        model = replay.initial_model("power-grid")
        for seed in (0, 1, True, "42", None):
            with self.assertRaises(ValueError):
                replay.initial_model("power-grid", seed)
        for action, scenario in (("inject", "normal"), ("inject", "missing"),
                                 ("start", None), ("recover", "heatwave")):
            with self.assertRaises(ValueError):
                replay.apply_action("power-grid", model, action, scenario)
        for step in (-1, True, 1.2, "1"):
            with self.assertRaises(ValueError):
                replay.sample("power-grid", model, step, AT)
        for at in ("bad", "2026-09-10T16:00:00", "2026-09-10T16:00:00+03:00"):
            with self.assertRaises(ValueError):
                replay.sample("power-grid", model, 0, at)
        with self.assertRaises(ValueError):
            replay.sample("upstream", model, 0, AT)
        with self.assertRaises(ValueError):
            replay.snapshot("power-grid", dict(model, fixture_version="obsolete"))
        with self.assertRaises(ValueError):
            replay.snapshot("power-grid", {k: v for k, v in model.items() if k != "last_queued"})

    def test_two_hour_adapter_soak_has_10800_rows_and_small_models(self):
        models = {surface: replay.initial_model(surface) for surface in SURFACES}
        start = datetime(2026, 9, 10, 16, tzinfo=timezone.utc)
        count = 0
        with patch.object(replay, "_snapshot_fixture", side_effect=AssertionError("soak loaded snapshot")):
            for step in range(120):
                for surface in SURFACES:
                    if step == 10:
                        models[surface] = replay.apply_action(surface, models[surface], "inject", replay.scenarios(surface)[0]["id"])
                    elif step == 15:
                        models[surface] = replay.apply_action(surface, models[surface], "recover")
                    frame = replay.sample(surface, models[surface], step, replay._stamp(start + timedelta(minutes=step)))
                    models[surface] = json.loads(json.dumps(frame["model"]))
                    count += len(frame["observations"])
            self.assertEqual(count, 10800)
            self.assertLess(len(replay._encoded(models)), 4096)

    def test_hosted_import_has_no_domain_generator_dependency(self):
        code = """
import sys
from energy_domains_v2 import replay
for surface in replay.SURFACES:
    replay.sample(surface, replay.initial_model(surface), 0, '2026-09-10T16:00:00Z')
assert not any(name.startswith(('iot_grid_simulator', 'iot_upstream_simulator',
    'iot_midstream_simulator', 'iot_downstream_refinery_simulator',
    'iot_compact_portfolio_simulator')) for name in sys.modules)
"""
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_existing_nine_route_factories_accept_replayed_snapshots(self):
        from energy_domains_v2 import inventory, monitoring

        class Runtime:
            def snapshot(self, surface):
                model = replay.sample(surface, replay.initial_model(surface), 0, AT)["model"]
                return replay.snapshot(surface, model)

        app = Flask(__name__)
        monitoring.register_routes(app, Runtime())
        inventory.register_routes(app, Runtime())
        client = app.test_client()
        for surface in SURFACES:
            path = ("/" + surface.removesuffix("-inventory") + "/review/inventory"
                    if surface.endswith("-inventory") else monitoring.PAGES[surface][0])
            with self.subTest(surface=surface):
                response = client.get("/api" + path)
                self.assertEqual(response.status_code, 200, response.data[:500])
                page = client.get(path)
                self.assertEqual(page.status_code, 200)
                self.assertIn(b'id="v2-controls"', page.data)
