"""Small catalog/projection/selection checks; never rebuild replay fixtures."""

import copy
import importlib.util
import json
from pathlib import Path
import unittest

from energy_domains_v2 import monitoring as m
from energy_domains_v2 import events
from energy_domains_v2.monitoring_details import Projection, MAX_LINKS, PROFILE
from energy_domains_v2.monitoring_selection import GROUPS, report, utility


class MonitoringDetailsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalogs = {}
        cls.configs = {}
        for surface, (domain, filename) in m.CONFIGS.items():
            config = json.loads((m.ROOT / "domains" / domain / "configs" / filename).read_text())
            config = m._module(surface, "config").validate_config(config)
            catalog = m._module(surface, "catalog").build_catalog(config)
            if surface == "upstream":
                families = catalog
            elif surface == "power-grid":
                families = dict(zip(("assets", "topology"), catalog[:2]))
            elif surface == "midstream":
                families = dict(zip(("assets", "topology"), catalog))
            else:
                families = dict(zip(("assets", "devices", "tags", "topology"), catalog))
            cls.catalogs[surface] = families
            cls.configs[surface] = config
        path = Path(__file__).resolve().parents[1] / "scripts" / "build_replay_fixtures.py"
        spec = importlib.util.spec_from_file_location("details_builder_test", path)
        cls.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.builder)

    def test_all_catalogs_project_bounded_neutral_static_details(self):
        forbidden = {"health", "scenario", "scenario_id", "deviation_ratio", "state_key",
                     "lifecycle_state", "reasons", "fault", "nominal_value"}

        def inspect(value):
            if isinstance(value, dict):
                self.assertFalse(forbidden.intersection(value))
                for child in value.values():
                    inspect(child)
            elif isinstance(value, list):
                for child in value:
                    inspect(child)

        for surface, families in self.catalogs.items():
            projection = Projection(surface, families, self.configs[surface])
            assets = {a["asset_id"]: a for a in families["assets"]}
            definitions = families.get("signals", families.get("tags", []))
            if not definitions:
                definitions = [{"asset_id": a["asset_id"], "measurement": "active_power", "unit": "kW"}
                               for a in assets.values()]
            for definition in definitions:
                record = {**definition, "value": 1, "event_time_utc": "2026-09-10T12:00:00Z",
                          "quality": {"validity": "good", "reasons": ["private_fault_label"]}}
                asset = assets[record["asset_id"]]
                row = m._neutral(record, asset, surface, projection)
                context = row["context"]
                self.assertEqual(context["details_profile"], PROFILE)
                self.assertLessEqual(len(context["topology"]["links"]), MAX_LINKS)
                self.assertLess(len(json.dumps(context)), 8000)
                inspect(context)
                mutated = {**record, "value": None, "quality": {"validity": "invalid"}}
                self.assertEqual(context, m._neutral(mutated, asset, surface, projection)["context"])
                self.assertEqual(row["payload"]["value"], 1)
                self.assertEqual(row["payload"]["unit"], record["unit"])
                if surface == "upstream":
                    self.assertNotIn("tag_id", row["payload"])
                    self.assertEqual(context["measurement"]["signal_id"], record["signal_id"])
                    self.assertEqual(context["measurement"]["cadence_seconds"], record["cadence_seconds"])
                    if asset.get("well_id"):
                        self.assertIn("lift_type", context["asset"]["well_configuration"])

    def test_configuration_is_curated_and_envelope_is_unchanged(self):
        families = self.catalogs["midstream"]
        projection = Projection("midstream", families, self.configs["midstream"])
        asset = next(a for a in families["assets"] if a["asset_type"] == "pipeline_segment")
        record = {"asset_id": asset["asset_id"], "measurement": "inlet_pressure", "unit": "kPa",
                  "value": 0, "event_time_utc": "2026-09-10T12:00:00Z",
                  "quality": {"validity": "good"}}
        row = m._neutral(record, asset, "midstream", projection)
        parameters = row["context"]["asset"]["configuration"]["parameters"]
        self.assertEqual(parameters["reference_pressure_kpa"], self.configs["midstream"]["pipeline"]["reference_pressure_kpa"])
        self.assertNotIn("hidden_release_route_fraction", json.dumps(row))
        wrapped = events.observations({"observations": [row]},
                                      {"run_id": "unit", "surface": "midstream", "sequence": 0},
                                      "midstream", record["event_time_utc"])
        self.assertEqual(len(wrapped[0]), 16)
        row["context"]["topology"]["links"].clear()
        self.assertTrue(projection.context(record, asset)["topology"]["links"])

    def test_coherence_prefers_same_electrical_asset_not_condition_asset(self):
        def row(entity, name):
            return {"entity_id": entity, "payload": {"measurement": name},
                    "context": {"asset": {}}}
        chosen = [row("meter", "active_power"), row("transformer", "condition_index")]
        self.assertGreater(utility("power-grid", row("meter", "reactive_power"), chosen),
                           utility("power-grid", row("transformer", "reactive_power"), chosen))

    def test_selection_preserves_distinct_unavailable_and_physical_zero(self):
        normal = [{"rows": {
            name: {"entity_id": name, "location_id": "fictional",
                   "context": {}, "payload": {"measurement": name, "value": phase + 1, "quality": "good"}}
            for name in ("active_power", "voltage", "condition_index")
        }} for phase in range(3)]
        failed = copy.deepcopy(normal)
        for frame in failed:
            frame["rows"]["active_power"]["payload"].update(value=None, quality="missing")
            frame["rows"]["voltage"]["payload"].update(value=0, quality="deenergized")
        selected, _, _ = self.builder.selected_identities(
            "power-grid", {"normal": normal, "failure": failed}, None)
        self.assertIn("active_power", selected)
        self.assertIn("voltage", selected)

    def test_literal_quantity_kinds_and_derived_evidence(self):
        cases = [
            ("power-grid", "condition_index", "1", "synthetic_model_index_not_physical_measurement"),
            ("downstream-portfolio", "pressure_index", "index", "synthetic_model_index_not_physical_measurement"),
            ("downstream-portfolio", "inventory_equivalent_volume", "equivalent_volume_units",
             "fictional_equivalent_quantity_not_engineering_volume"),
        ]
        for surface, name, unit, expected in cases:
            families = self.catalogs[surface]
            asset = families["assets"][0]
            context = Projection(surface, families).context({"measurement": name, "unit": unit}, asset)
            self.assertEqual(context["measurement"]["quantity_kind"], expected)
        context = Projection("midstream", self.catalogs["midstream"]).context(
            {"measurement": "mass_flow", "unit": "kg/s"}, self.catalogs["midstream"]["assets"][0])
        self.assertIn("not_independent", context["measurement"]["model_semantics"])
        self.assertNotIn("pressure_basis", context["measurement"])

    def test_selection_keeps_every_phase_and_adds_companions(self):
        for surface, groups in GROUPS.items():
            names = ["coverage_channel"] + [names[0] for _, names in groups] + ["duplicate_channel"] * 12
            normal = []
            for phase in range(3):
                rows = {}
                for index, name in enumerate(names):
                    rows[str(index)] = {
                        "entity_id": "joined-asset", "location_id": "fictional",
                        "payload": {"measurement": name, "value": phase + 1, "quality": "good"},
                        "context": {"asset": {"asset_type": "generic"}},
                    }
                normal.append({"rows": rows})
            sequences = {"normal": normal}
            for choice in m.scenarios(surface):
                frames = copy.deepcopy(normal)
                for frame in frames:
                    frame["rows"]["0"]["payload"]["value"] = None
                    frame["rows"]["0"]["payload"]["quality"] = "invalid"
                sequences[choice["id"]] = frames
            selected, proof, _ = self.builder.selected_identities(surface, sequences, None)
            self.assertLessEqual(len(selected), 10)
            self.assertIn("0", selected)
            self.assertTrue(all(keys for phases in proof.values() for keys in phases))
            self.assertEqual(selected, self.builder.selected_identities(surface, sequences, None)[0])
            chosen = [normal[0]["rows"][key] for key in selected]
            details = report(surface, chosen, list(normal[0]["rows"].values()))
            self.assertEqual(details["available_roles_not_selected"], [])
            self.assertEqual(details["roles_absent_from_source_candidates"], [])


if __name__ == "__main__":
    unittest.main()
