"""Offline, allowlisted inventory catalog projection for frozen V2 replay."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path


DETAILS_PROFILE = "domain-details-v1"
PART_FIELDS = (
    "part_class", "part_scope", "tracking_mode", "required_storage_class",
    "tracking_policy_note", "classification_provenance", "abc_class",
    "criticality_class", "movement_class", "lead_band",
    "nominal_lead_time_days_synthetic", "is_insurance_spare", "is_kit",
    "is_site_wide", "shelf_life_class", "shelf_life_years_synthetic",
)
LOCATION_FIELDS = (
    "parent_location_id", "site_id", "area_id", "region_code", "zone", "aisle",
    "rack", "shelf", "bin_code", "shelf_address", "stocking_profile", "id_tech",
)
FIT_FIELDS = (
    "asset_type", "asset_config_code", "equipment_class", "equipment_class_label",
    "fit_spec_code", "quantity_per_assembly", "fitment_claim",
    "effective_from_utc", "effective_to_utc",
)
FAMILIES = ("part_master", "storeroom_location", "part_applicability", "part_stock_txn")
MAX_FITS = 12
MAX_STORAGE_CLASSES = 16
MAX_DETAIL_BYTES = 12000


def _scalar(value, field: str):
    if type(value) not in (str, int, float, bool):
        raise ValueError(f"Invalid inventory detail scalar: {field}")
    if isinstance(value, str) and (not value.strip() or len(value) > 512):
        raise ValueError(f"Invalid or oversized inventory detail: {field}")
    json.dumps(value, allow_nan=False)
    return value


def _project(row: dict, fields: tuple[str, ...]) -> dict:
    return {key: _scalar(row[key], key) for key in fields if row.get(key) is not None}


def _required(row: dict, fields: tuple[str, ...]) -> None:
    for field in fields:
        if row.get(field) is None:
            raise ValueError(f"Missing required inventory detail: {field}")
        _scalar(row[field], field)


def _bounded(rows: list, limit: int) -> dict:
    ordered = sorted(rows, key=lambda row: json.dumps(row, sort_keys=True, allow_nan=False))
    encoded = json.dumps(ordered, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {"items": ordered[:limit], "total_count": len(ordered),
            "included_count": min(limit, len(ordered)), "partial": len(ordered) > limit,
            "content_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest()}


def _read(root: Path, family: str) -> tuple[list[dict], str]:
    path = root / f"{family}.jsonl"
    data = path.read_bytes()
    rows = [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"Invalid inventory source records: {family}")
    return rows, hashlib.sha256(data).hexdigest()


def _index(rows: list[dict], key: str) -> dict:
    indexed = {}
    for row in rows:
        _required(row, (key,))
        identity = row[key]
        if identity in indexed:
            raise ValueError(f"Duplicate inventory detail identity: {key}")
        indexed[identity] = row
    return indexed


def load_details(root: Path, domain: str, source: dict) -> dict:
    """Read explicit source files only during offline model initialization.

    The review readers discard applicability identities, dates and location
    hierarchy. Read those exact rows, never infer fitment from monitoring assets.
    Transaction contents are not copied: V2 quantity evolves independently.
    """
    catalogs, hashes = {}, {}
    for family in FAMILIES:
        catalogs[family], hashes[f"{family}.jsonl"] = _read(root, family)
    parts = _index(catalogs["part_master"], "part_master_id")
    locations = _index(catalogs["storeroom_location"], "location_id")
    if (set(parts) != {row["part_master_id"] for row in source["parts"]}
            or set(locations) != {row["location_id"] for row in source["locations"]}):
        raise ValueError("Inventory detail catalogs disagree with review snapshot")
    fits = {identity: [] for identity in parts}
    identities = set()
    for row in catalogs["part_applicability"]:
        _required(row, ("part_master_id", "fit_spec_code", "fitment_claim",
                        "quantity_per_assembly"))
        identity = row.get("part_applicability_id", row.get("applicability_id"))
        _scalar(identity, "applicability_id")
        if identity in identities or row["part_master_id"] not in parts:
            raise ValueError("Invalid inventory applicability identity")
        identities.add(identity)
        target_fields = (("equipment_class",) if domain == "downstream"
                         else ("asset_type", "asset_config_code"))
        _required(row, target_fields)
        fits[row["part_master_id"]].append(
            {"applicability_id": identity, **_project(row, FIT_FIELDS)})
    projected_parts = {}
    for identity, row in parts.items():
        _required(row, ("part_class", "tracking_mode", "required_storage_class"))
        detail = _project(row, PART_FIELDS)
        detail["catalog_claim"] = "fictional_synthetic_not_an_engineering_standard"
        if row.get("specification") is not None:
            detail["specification"] = {
                "kind": "fictional_catalog_description",
                "text": _scalar(row["specification"], "specification"),
                "rating_claim": "no_verified_ratings_or_compatibility",
            }
        projected_parts[identity] = {
            "part_details": detail,
            "applicability": {
                **_bounded(fits[identity], MAX_FITS),
                "scope": "synthetic_catalog_rule_not_installed_asset_or_bom_join",
                "claim": "not_a_real_interchange_or_dispatch_claim",
                "validity_basis": "source_catalog_dates_only_not_replay_time_evaluation",
            },
        }
    projected_locations = {}
    for identity, row in locations.items():
        storage = row.get("storage_classes_supported")
        if not isinstance(storage, list) or not storage:
            raise ValueError("Missing required inventory detail: storage_classes_supported")
        detail = _project(row, LOCATION_FIELDS)
        detail["storage_classes_supported"] = _bounded(
            [_scalar(item, "storage_classes_supported") for item in storage],
            MAX_STORAGE_CLASSES)
        detail["capability_claim"] = "fictional_catalog_capability_not_item_condition"
        projected_locations[identity] = detail
    return {
        "parts": projected_parts, "locations": projected_locations,
        "provenance": {
            "source_domain": domain, "source_files_sha256": hashes,
            "projection": DETAILS_PROFILE, "source_selection": "explicit_root",
            "stock_basis": "v2_simulated_position",
            "initialization_basis": "validated_opening_balance_transactions_only",
            "quantity_semantics": "per_sku_and_uom_not_available_to_promise",
            "quality_semantics": "quantity_observation_not_item_condition",
            "context_temporality": "static_catalog_not_current_stock_state",
            "monitoring_asset_join": "not_established",
        },
    }


def position_details(details: dict, part_id: str, location_id: str) -> dict:
    result = {"details_profile": DETAILS_PROFILE,
              **deepcopy(details["parts"][part_id]),
              "location_details": deepcopy(details["locations"][location_id]),
              "inventory_provenance": deepcopy(details["provenance"])}
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > MAX_DETAIL_BYTES:
        raise ValueError("Inventory detail exceeds bounded profile size")
    return result
