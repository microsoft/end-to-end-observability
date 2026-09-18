"""Curated catalog context must never become a second stock-state channel."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from energy_domains_v2 import inventory, inventory_details as details
from energy_domains_v2.contracts import INVENTORY_SURFACES


def source_shape(domain):
    """Small source-shaped records, not regenerated or modified V1 inventory."""
    part = {
        "part_master_id": "part-1", "part_key": "test-part", "stock_code": "TEST-1",
        "part_name": "Fictional test part", "part_class": "C", "tracking_mode": "lot",
        "required_storage_class": "binned", "uom": "each",
        "classification_provenance": "synthetic_synthesis_not_a_standard",
        "manufacturer": "reference_only_do_not_copy", "scenario_id": "do_not_copy",
        "quantity": 99, "held_at": [{"quantity": 99}],
    }
    location = {
        "location_id": "location-1", "location_code": "TEST-STORE",
        "location_type": "site_storeroom", "storage_classes_supported": ["binned"],
        "parent_location_id": None, "zone": "Z1", "aisle": "A1", "rack": "R1",
        "shelf": "S1", "bin_code": "B1", "quantity": 99,
    }
    fit = {
        "applicability_id": "fit-row-1", "part_master_id": "part-1",
        "asset_type": "pump_driver", "asset_config_code": "pump_driver:standard",
        "fit_spec_code": "fit-1", "quantity_per_assembly": 2,
        "fitment_claim": "fictional_not_a_real_interchange_claim",
        "condition_eligibility": "new", "scenario_id": "do_not_copy",
        "installed_asset_id": "do_not_copy", "interchange_direction": "bidirectional",
    }
    if domain == "power-grid":
        part.update(part_class="accessory", required_storage_class="dry",
                    specification="Fictional illustrative part; no verified ratings or compatibility.",
                    tracking_policy_note="Individual serial numbers and lots are not modeled.")
        location["storage_classes_supported"] = ["dry"]
        fit["part_applicability_id"] = fit.pop("applicability_id")
        fit.update(asset_type="overhead_line", asset_config_code="fictional-generic-v1")
    elif domain == "upstream":
        fit.update(effective_from_utc="2025-08-19T12:00:00Z",
                   effective_to_utc="2027-08-19T12:30:00Z")
    elif domain == "downstream":
        fit.pop("asset_type")
        fit.pop("asset_config_code")
        fit.update(equipment_class="electric_motor", equipment_class_label="Electric motor")
        part.update(shelf_life_class="durable", shelf_life_years_synthetic=None)
        location.update(parent_location_id="parent-1", is_on_site=False,
                        site_x_m=None, site_y_m=None)
    records = {
        "part_master": [part], "storeroom_location": [location],
        "part_applicability": [fit],
        "part_stock_txn": [{"transaction_type": "opening_balance", "quantity": 99,
                            "to_state": "available", "reservation_id": "do_not_copy"}],
    }
    source = {"parts": [deepcopy(part)], "locations": [deepcopy(location)],
              "stock": [{"part_master_id": "part-1", "location_id": "location-1",
                         "quantity": 20, "quality": "good", "uom": "each"}],
              "totals": {}, "domain": domain}
    return records, source


def projected(domain, records, source):
    def read(root, family):
        rows = records[family]
        digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
        return deepcopy(rows), digest
    with patch.object(details, "_read", side_effect=read):
        return details.load_details(Path("."), domain, source)


class InventoryDetailTests(unittest.TestCase):
    def test_four_source_shapes_are_allowlisted_and_typed(self):
        for surface in INVENTORY_SURFACES:
            domain = surface.removesuffix("-inventory")
            with self.subTest(domain=domain):
                records, source = source_shape(domain)
                result = projected(domain, records, source)
                context = details.position_details(result, "part-1", "location-1")
                self.assertEqual(context["details_profile"], "domain-details-v1")
                self.assertEqual(context["part_details"]["tracking_mode"], "lot")
                fit = context["applicability"]["items"][0]
                self.assertEqual(fit["applicability_id"], "fit-row-1")
                self.assertEqual(fit["quantity_per_assembly"], 2)
                self.assertFalse(context["applicability"]["partial"])
                self.assertEqual(context["location_details"]["bin_code"], "B1")
                self.assertEqual(len(context["inventory_provenance"]["source_files_sha256"]), 4)
                if domain == "power-grid":
                    self.assertEqual(context["part_details"]["specification"]["kind"],
                                     "fictional_catalog_description")
                else:
                    self.assertNotIn("specification", context["part_details"])
                if domain == "upstream":
                    self.assertEqual(fit["effective_to_utc"], records["part_applicability"][0]["effective_to_utc"])
                if domain == "downstream":
                    self.assertEqual(fit["equipment_class"], "electric_motor")
                    self.assertNotIn("asset_type", fit)
                    self.assertNotIn("shelf_life_years_synthetic", context["part_details"])
                encoded = json.dumps(context, allow_nan=False)
                for forbidden in ("do_not_copy", "condition_eligibility", "held_at",
                                  '"quantity":', "manufacturer", "reservation_id"):
                    self.assertNotIn(forbidden, encoded)

    def test_actions_leave_static_context_and_payload_semantics_unchanged(self):
        for surface in INVENTORY_SURFACES:
            domain = surface.removesuffix("-inventory")
            records, source = source_shape(domain)
            source["details"] = projected(domain, records, source)
            with self.subTest(surface=surface), patch.object(inventory, "_opening", return_value=source):
                model = inventory.initial_model(surface, 42)
                original = deepcopy(model)
                normal = inventory.frame(surface, model, 0, "2026-09-10T12:00:00Z")
                context = normal["observations"][0]["context"]
                for choice, quantity, quality in (
                    ("low_stock", 9, "good"), ("stockout", 0, "good"),
                    ("observation_unavailable", None, "unavailable"),
                ):
                    changed = inventory.apply_action(surface, model, "inject", choice)
                    observation = inventory.frame(surface, changed, 1, "2026-09-10T12:01:00Z")["observations"][0]
                    self.assertEqual(observation["context"], context)
                    self.assertEqual(observation["payload"]["quantity"], quantity)
                    self.assertEqual(observation["payload"]["quality"], quality)
                    self.assertEqual(set(observation["payload"]), {
                        "part_master_id", "part_key", "stock_code", "quantity", "uom", "quality"})
                    for action in ("recover", "reset"):
                        restored = inventory.apply_action(surface, changed, action)
                        self.assertEqual(restored, model)
                self.assertEqual(model, original)

    def test_bounds_report_total_partial_and_content_identity(self):
        records, source = source_shape("midstream")
        fit = records["part_applicability"][0]
        records["part_applicability"] = [
            {**fit, "applicability_id": f"fit-{i}"} for i in range(details.MAX_FITS + 3)]
        first = projected("midstream", records, source)
        records["part_applicability"].reverse()
        self.assertEqual(first["parts"], projected("midstream", records, source)["parts"])
        fits = first["parts"]["part-1"]["applicability"]
        self.assertEqual(fits["included_count"], details.MAX_FITS)
        self.assertEqual(fits["total_count"], details.MAX_FITS + 3)
        self.assertTrue(fits["partial"])
        self.assertEqual(len(fits["content_sha256"]), 64)
        with patch.object(details, "MAX_DETAIL_BYTES", 1), self.assertRaises(ValueError):
            details.position_details(first, "part-1", "location-1")

    def test_missing_and_malformed_required_facts_fail_without_defaults(self):
        for family, field in (("part_master", "tracking_mode"),
                              ("storeroom_location", "storage_classes_supported"),
                              ("part_applicability", "fit_spec_code")):
            records, source = source_shape("midstream")
            records[family][0].pop(field)
            with self.subTest(field=field), self.assertRaises(ValueError):
                projected("midstream", records, source)
        for bad_value in ({}, "x" * 513, float("nan")):
            records, source = source_shape("midstream")
            records["part_master"][0]["part_class"] = bad_value
            with self.subTest(value=type(bad_value)), self.assertRaises(ValueError):
                projected("midstream", records, source)

    def test_real_sources_match_and_remain_read_only_when_present(self):
        checked = 0
        for surface in INVENTORY_SURFACES:
            root = inventory.dataset_root(surface)
            if not all((root / f"{family}.jsonl").is_file() for family in details.FAMILIES):
                continue
            checked += 1
            before = {family: hashlib.sha256((root / f"{family}.jsonl").read_bytes()).hexdigest()
                      for family in details.FAMILIES}
            model = inventory.initial_model(surface, 42)
            with patch.object(details, "_read", side_effect=AssertionError("frame must use frozen facts")):
                frame = inventory.frame(surface, model, 0, "2026-09-10T12:00:00Z")
            for row in frame["observations"]:
                part = next(p for p in model["opening"]["parts"] if p["part_master_id"] == row["entity_id"])
                self.assertEqual(row["context"]["part_details"]["part_class"], part["part_class"])
                self.assertLess(len(json.dumps(row).encode("utf-8")), 12000)
            self.assertEqual(before, {
                family: hashlib.sha256((root / f"{family}.jsonl").read_bytes()).hexdigest()
                for family in details.FAMILIES})
        self.assertGreater(checked, 0, "At least one reviewed source dataset is required")


if __name__ == "__main__":
    unittest.main()
