"""V2 monitoring adapter contracts; no Azure credentials or network needed."""

import copy
import json
import sys
import unittest
from unittest.mock import patch

from flask import Flask

from energy_domains_v2 import monitoring as m
from energy_domains_v2 import events
from energy_domains_v2.contracts import domain_for


AT = "2026-09-10T12:00:00Z"


class MonitoringTests(unittest.TestCase):
    def test_all_supported_choices_change_real_measurements(self):
        for surface in m.MONITORING_SURFACES:
            model = m.initial_model(surface, 7)
            baseline = m.frame(surface, model, 0, AT)
            original = copy.deepcopy(model)
            self.assertGreaterEqual(len(m.scenarios(surface)), 3)
            normal = baseline["observations"]
            for choice in m.scenarios(surface):
                with self.subTest(surface=surface, scenario=choice["id"]):
                    injected = m.apply_action(surface, model, "inject", choice["id"])
                    generated = m.frame(surface, injected, 0, AT)
                    observed = generated["observations"]
                    self.assertNotEqual(normal, observed)
                    envelopes = events.observations(
                        generated, {"run_id": "monitoring-test", "sequence": 0, "surface": surface},
                        domain_for(surface), AT,
                    )
                    self.assertEqual(len(envelopes), len(observed))
                    for row in observed:
                        self.assertLess(events.request_size(row["payload"]), 48_000)
                        self.assertLess(events.request_size(row["context"]), 48_000)
                    self.assertEqual(model, original)
                    self.assertEqual(m.apply_action(surface, injected, "recover"), model)
                    self.assertEqual(m.apply_action(surface, injected, "reset"), model)

    def test_deterministic_restart_and_immutable_inputs(self):
        for surface in m.MONITORING_SURFACES:
            with self.subTest(surface=surface):
                model = m.initial_model(surface, 17)
                first = m.frame(surface, model, 0, AT)
                self.assertEqual(first, m.frame(surface, model, 0, AT))
                restored = json.loads(json.dumps(first["model"]))
                next_frame = m.frame(surface, restored, 1, AT)
                self.assertNotEqual(first["observations"], next_frame["observations"])
                self.assertEqual(model["cursor"], 0)
                first["observations"][0]["payload"]["value"] = 999999
                fresh = m.frame(surface, model, 0, AT)
                self.assertNotEqual(fresh["observations"][0]["payload"]["value"], 999999)

    def test_observation_allowlist_and_context(self):
        forbidden = {"scenario", "scenario_id", "scenario_active", "ground_truth", "is_anomaly",
                     "health", "deviation_ratio", "nominal_value", "diagnosis_claim", "detector"}

        def inspect(value):
            if isinstance(value, dict):
                self.assertFalse(forbidden.intersection(value))
                for child in value.values():
                    inspect(child)
            elif isinstance(value, list):
                for child in value:
                    inspect(child)

        for surface in m.MONITORING_SURFACES:
            result = m.frame(surface, m.initial_model(surface, 7), 0, AT)
            inspect(result["observations"])
            json.dumps(result, allow_nan=False)
            for observation in result["observations"]:
                self.assertTrue(observation["entity_id"])
                self.assertTrue(observation["location_id"])
                self.assertTrue(observation["context"]["location_is_fictional"])
                self.assertIn("unit", observation["payload"])
                self.assertIn("quality", observation["payload"])
            self.assertNotIn("dashboard.app", sys.modules)

    def test_unavailable_is_null_and_outage_is_zero(self):
        model = m.initial_model("power-grid", 7)
        missing = m.frame("power-grid", m.apply_action("power-grid", model, "inject", "communications_failure"), 0, AT)
        unavailable = [r for r in missing["observations"] if r["payload"]["value"] is None]
        self.assertTrue(unavailable)
        self.assertTrue(all(r["payload"]["quality"] != "good" for r in unavailable))
        outage = m.frame("power-grid", m.apply_action("power-grid", model, "inject", "storm_outage"), 0, AT)
        zeros = [r for r in outage["observations"] if r["payload"]["quality"] == "deenergized"]
        self.assertTrue(zeros)
        self.assertTrue(any(r["payload"]["value"] == 0 for r in zeros))

    def test_rejects_invalid_commands_and_cross_domain_models(self):
        model = m.initial_model("upstream", 7)
        for action, scenario in (("inject", "unknown"), ("inject", "normal_production"),
                                 ("recover", "slugging"), ("launch", None)):
            with self.assertRaises(ValueError):
                m.apply_action("upstream", model, action, scenario)
        with self.assertRaises(ValueError):
            m.frame("midstream", model, 0, AT)
        for seed in (True, -1, 2**31, "7"):
            with self.assertRaises(ValueError):
                m.initial_model("upstream", seed)
        for at in ("invalid", "2026-09-10T12:00:00", "2026-09-10T12:00:00+03:00"):
            with self.assertRaises(ValueError):
                m.frame("upstream", model, 0, at)
        with self.assertRaises(ValueError):
            m.frame("upstream", model, True, AT)

    def test_portfolio_selection_and_chart_shapes(self):
        for surface in m.MONITORING_SURFACES:
            snapshot = m.frame(surface, m.initial_model(surface, 7), 0, AT)["snapshot"]
            if surface == "power-grid":
                self.assertGreater(len(snapshot["weather_history"]), 1)
                self.assertEqual(len({a["location_ref"] for a in snapshot["devices"]}), 6)
            elif surface == "downstream-portfolio":
                self.assertEqual(len(snapshot["sites"]), 3)
                self.assertEqual(len(snapshot["modules"]), 9)
                self.assertTrue(snapshot["product_families"])
                self.assertLess(len(m.frame(surface, m.initial_model(surface, 7), 0, AT)["observations"]), 300)
            else:
                live = snapshot.get("live", snapshot)
                self.assertTrue(live["assets"] if surface != "downstream" else live["devices"])

    def test_routes_read_persisted_snapshots_and_never_command(self):
        snapshots = {s: m.frame(s, m.initial_model(s, 7), 0, AT)["snapshot"] for s in m.MONITORING_SURFACES}

        class Runtime:
            def snapshot(self, surface):
                return copy.deepcopy(snapshots[surface])

            def command(self, *args):
                raise AssertionError("GET must never command or log")

        app = Flask(__name__)
        with patch("energy_domains_v2.ui.render_portal", side_effect=lambda html, surface: html):
            m.register_routes(app, Runtime())
        client = app.test_client()
        with patch.object(m, "frame", side_effect=AssertionError("GET regenerated data")):
            for surface, (path, _) in m.PAGES.items():
                with self.subTest(surface=surface):
                    response = client.get(path)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(b"/v2/monitoring-view.js", response.data)
                    first = client.get(f"/api{path}")
                    self.assertEqual(first.status_code, 200)
                    self.assertEqual(first.json, client.get(f"/api{path}").json)
                    self.assertEqual(client.post(f"/api{path}/control/start").status_code, 404)
            for surface in ("upstream", "midstream", "downstream"):
                live = client.get(f"/api/{surface}/live").json
                items, key, segment = (("devices", "device_id", "devices") if surface == "downstream"
                                       else ("assets", "asset_id", "assets"))
                entity = live[items][0][key]
                self.assertEqual(client.get(f"/api/{surface}/live/{segment}/{entity}").status_code, 200)
                self.assertEqual(client.get(f"/api/{surface}/live/{segment}/unknown").status_code, 404)
            with client.get("/v2/monitoring-view.js") as asset:
                self.assertEqual(asset.status_code, 200)


if __name__ == "__main__":
    unittest.main()
