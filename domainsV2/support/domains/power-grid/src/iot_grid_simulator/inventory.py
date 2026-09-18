"""Independent, local-only fictional distribution-spares inventory sidecar."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from .io import canonical_json, sha256_file, write_csv, write_jsonl

VERSION = "power-grid-inventory-v1"
MAX_QUANTITY = 2**53 - 1
GRID_FRAME = "fictional local Austin-area km frame; no geolocation"
FAMILIES = ("part_master", "storeroom_location", "part_applicability", "part_stock_txn")
SCHEMA_PATH = Path(__file__).parent / "schemas" / "inventory.schema.json"


class InventoryError(ValueError):
    """An inventory contract or generation input is invalid."""


def stable_id(prefix: str, *content: Any) -> str:
    digest = hashlib.sha256(canonical_json(content).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _validate(value: Any, schema: dict[str, Any], context: str) -> None:
    kinds = {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: type(v) is int,
        "number": lambda v: type(v) in (int, float) and math.isfinite(v),
        "boolean": lambda v: type(v) is bool,
    }
    if "type" in schema and not kinds[schema["type"]](value):
        raise InventoryError(f"{context}: invalid {schema['type']}")
    if "const" in schema and (type(value) is not type(schema["const"]) or value != schema["const"]):
        raise InventoryError(f"{context}: invalid constant")
    if "enum" in schema and value not in schema["enum"]:
        raise InventoryError(f"{context}: unsupported value")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if set(schema.get("required", ())) - value.keys():
            raise InventoryError(f"{context}: missing required fields")
        if schema.get("additionalProperties") is False and value.keys() - properties.keys():
            raise InventoryError(f"{context}: unknown fields")
        for key, child in value.items():
            if key in properties:
                _validate(child, properties[key], f"{context}.{key}")
    elif isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise InventoryError(f"{context}: empty list")
        if schema.get("uniqueItems") and len({canonical_json(v) for v in value}) != len(value):
            raise InventoryError(f"{context}: duplicate values")
        for index, child in enumerate(value):
            _validate(child, schema["items"], f"{context}[{index}]")
    elif isinstance(value, str):
        if len(value.strip()) < schema.get("minLength", 0):
            raise InventoryError(f"{context}: empty text")
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            raise InventoryError(f"{context}: malformed identity")
    elif type(value) in (int, float):
        if not math.isfinite(value) or value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf):
            raise InventoryError(f"{context}: quantity or coordinate out of bounds")


def validate_records(records: dict[str, list[dict[str, Any]]]) -> None:
    """Validate every family, identity, join and opening total without dependencies."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))["$defs"]
    identities = {
        "part_master": ("part_master_id", "part_key", "stock_code"),
        "storeroom_location": ("location_id", "location_code"),
        "part_applicability": ("part_applicability_id",),
        "part_stock_txn": ("part_stock_txn_id",),
    }
    for family in FAMILIES:
        seen = {key: set() for key in identities[family]}
        for index, record in enumerate(records[family], 1):
            _validate(record, schema[family], f"{family} row {index}")
            for key, values in seen.items():
                if record[key] in values:
                    raise InventoryError(f"{family}: duplicate {key}")
                values.add(record[key])
    for family in FAMILIES[:3]:
        if not records[family]:
            raise InventoryError(f"{family}: required catalog is empty")
    parts = {r["part_master_id"]: r for r in records["part_master"]}
    locations = {r["location_id"]: r for r in records["storeroom_location"]}
    for part in parts.values():
        if part["part_master_id"] != stable_id("grid-part", part["part_key"]):
            raise InventoryError("part_master: identity does not match part_key")
    for location in locations.values():
        if location["location_id"] != stable_id("grid-location", location["location_code"]):
            raise InventoryError("storeroom_location: identity does not match location_code")
    for row in records["part_applicability"]:
        part = parts.get(row["part_master_id"])
        if part is None or part["part_key"] != row["part_key"]:
            raise InventoryError("part_applicability: unknown or inconsistent part reference")
        if row["part_applicability_id"] != stable_id("grid-fit", row["part_key"], row["asset_type"], row["asset_config_code"]):
            raise InventoryError("part_applicability: inconsistent identity")
    totals: dict[str, int] = {}
    entries: set[tuple[str, str, str, str]] = set()
    for row in records["part_stock_txn"]:
        entry = (row["part_master_id"], row["location_id"], row["transaction_type"], row["entry_key"])
        if entry in entries:
            raise InventoryError("part_stock_txn: duplicate logical entry identity")
        entries.add(entry)
        part = parts.get(row["part_master_id"])
        location = locations.get(row["location_id"])
        if part is None or location is None:
            raise InventoryError("part_stock_txn: unknown part or location")
        if row["uom"] != part["uom"]:
            raise InventoryError("part_stock_txn: uom disagrees with part")
        if part["required_storage_class"] not in location["storage_classes_supported"]:
            raise InventoryError("part_stock_txn: unsupported storage class")
        content = {key: value for key, value in row.items() if key != "part_stock_txn_id"}
        if row["part_stock_txn_id"] != stable_id("grid-txn", content):
            raise InventoryError("part_stock_txn: inconsistent content identity")
        if row["transaction_type"] == "opening_balance":
            key = row["part_master_id"]
            totals[key] = totals.get(key, 0) + row["quantity"]
            if totals[key] > MAX_QUANTITY:
                raise InventoryError("part_stock_txn: unsafe JavaScript same-SKU total")


# These are fictional design labels, not engineering specifications or fit guarantees.
_PARTS = (
    ("transformer-overhead-small", "Overhead transformer, small illustrative class", "transformer", "serialized", "each", "outdoor_covered", "distribution_transformer"),
    ("transformer-overhead-medium", "Overhead transformer, medium illustrative class", "transformer", "serialized", "each", "outdoor_covered", "distribution_transformer"),
    ("transformer-padmount", "Pad-mounted distribution transformer", "transformer", "serialized", "each", "outdoor_covered", "distribution_transformer"),
    ("switch-air-disconnect", "Overhead air disconnect switch", "switch", "serialized", "each", "outdoor_covered", "switch"),
    ("recloser-overhead", "Overhead automatic recloser", "recloser", "serialized", "each", "outdoor_covered", "recloser"),
    ("switch-operator", "Distribution switch operator assembly", "switch_control", "serialized", "each", "dry", "switch"),
    ("cutout-assembly", "Overhead fuse cutout assembly", "cutout", "lot", "each", "dry", "fused_lateral"),
    ("fuse-link-light", "Fuse link, light illustrative class", "fuse", "lot", "each", "dry", "fused_lateral"),
    ("fuse-link-heavy", "Fuse link, heavy illustrative class", "fuse", "lot", "each", "dry", "fused_lateral"),
    ("insulator-line-post", "Line-post insulator", "insulator", "lot", "each", "dry", "overhead_line"),
    ("insulator-suspension", "Suspension insulator", "insulator", "lot", "each", "dry", "overhead_line"),
    ("conductor-overhead", "Overhead conductor length", "conductor", "bulk", "m", "outdoor_covered", "overhead_line"),
    ("cable-underground", "Underground distribution cable length", "cable", "bulk", "m", "outdoor_covered", "underground_circuit"),
    ("cable-secondary", "Secondary service cable length", "cable", "bulk", "m", "outdoor_covered", "service_connection"),
    ("connector-compression", "Compression connector", "accessory", "lot", "each", "dry", "overhead_line"),
    ("cable-elbow", "Separable cable elbow", "accessory", "lot", "each", "dry", "underground_circuit"),
    ("termination-kit", "Cable termination kit", "accessory", "lot", "kit", "dry", "underground_circuit"),
    ("splice-kit", "Cable splice kit", "accessory", "lot", "kit", "dry", "underground_circuit"),
    ("arrester-overhead", "Overhead surge arrester", "arrester", "lot", "each", "dry", "overhead_line"),
    ("meter-service", "Service metering unit", "meter", "serialized", "each", "controlled_electronics", "service_connection"),
    ("relay-controller", "Protection controller module", "protection_control", "serialized", "each", "controlled_electronics", "recloser"),
    ("control-battery", "Control cabinet battery pack", "protection_control", "lot", "each", "controlled_electronics", "recloser"),
    ("grounding-connector", "Grounding connector", "accessory", "bulk", "each", "dry", "overhead_line"),
    ("crossarm-bracket", "Crossarm attachment bracket", "accessory", "bulk", "each", "dry", "overhead_line"),
)

_LOCATIONS = (
    ("AUS-WH-01", "Fictional Austin central warehouse", "warehouse", 0.0, 0.0),
    ("AUS-WH-02", "Fictional east equipment warehouse", "warehouse", 18.0, 4.0),
    ("AUS-SC-01", "Fictional north service center", "service_center", -4.0, 22.0),
    ("AUS-SC-02", "Fictional south service center", "service_center", 3.0, -19.0),
    ("AUS-ST-01", "Fictional west staging store", "staging", -23.0, 8.0),
    ("AUS-ST-02", "Fictional southeast staging store", "staging", 21.0, -14.0),
    ("AUS-CR-01", "Fictional north crew store", "crew_store", 8.0, 15.0),
    ("AUS-CR-02", "Fictional southwest crew store", "crew_store", -13.0, -12.0),
)


def build_inventory(seed: int = 42) -> dict[str, list[dict[str, Any]]]:
    if type(seed) is not int or not 0 <= seed <= MAX_QUANTITY:
        raise InventoryError("seed must be a nonnegative JavaScript-safe integer")
    records: dict[str, list[dict[str, Any]]] = {family: [] for family in FAMILIES}
    common = {"is_synthetic": True, "operational_use": "prohibited"}
    for index, (key, name, kind, tracking, uom, storage, asset) in enumerate(_PARTS):
        part_id = stable_id("grid-part", key)
        records["part_master"].append({
            **common, "record_type": "part_master", "part_master_id": part_id,
            "part_key": key, "part_name": name, "stock_code": f"GRID-{index + 1:04d}",
            "part_class": kind, "tracking_mode": tracking, "uom": uom,
            "required_storage_class": storage,
            "specification": f"Fictional illustrative {name.lower()}; no verified ratings or compatibility.",
            "tracking_policy_note": "Illustrative policy only; individual serial numbers and lots are not modeled.",
        })
        records["part_applicability"].append({
            **common, "record_type": "part_applicability",
            "part_applicability_id": stable_id("grid-fit", key, asset, "fictional-generic-v1"),
            "part_master_id": part_id, "part_key": key, "asset_type": asset,
            "asset_config_code": "fictional-generic-v1", "quantity_per_assembly": 1,
            "fit_spec_code": f"illustrative-{key}",
            "fitment_claim": "fictional illustrative grouping; not verified compatibility",
        })
    for code, name, kind, east, north in _LOCATIONS:
        classes = ["dry", "controlled_electronics"]
        if kind != "crew_store":
            classes.append("outdoor_covered")
        records["storeroom_location"].append({
            **common, "record_type": "storeroom_location",
            "location_id": stable_id("grid-location", code), "location_code": code,
            "location_name": name, "location_type": kind, "grid_east_km": east,
            "grid_north_km": north, "grid_frame": GRID_FRAME,
            "storage_classes_supported": sorted(classes),
        })
    for index, part in enumerate(records["part_master"][:-2]):
        eligible = [loc for loc in records["storeroom_location"]
                    if part["required_storage_class"] in loc["storage_classes_supported"]]
        for offset in range(3):
            location = eligible[(index + offset) % len(eligible)]
            variability = int(stable_id("q", seed, part["part_key"], offset)[2:10], 16)
            quantity = (2 + variability % 7 if part["tracking_mode"] == "serialized"
                        else 24 + variability % 97)
            if (index + offset) % 11 == 0:
                quantity = 0
            row = {
                **common, "record_type": "part_stock_txn",
                "part_master_id": part["part_master_id"], "location_id": location["location_id"],
                "transaction_type": "opening_balance", "quantity": quantity, "uom": part["uom"],
                "entry_key": "illustrative-opening-v1",
            }
            row["part_stock_txn_id"] = stable_id("grid-txn", row)
            records["part_stock_txn"].append(row)
    for family in FAMILIES:
        records[family].sort(key=canonical_json)
    validate_records(records)
    return records


def reference_catalog() -> dict[str, Any]:
    path = Path(__file__).parent / "data" / "inventory" / "reference_catalog.json"
    return json.loads(path.read_text(encoding="utf-8"))


def generate_inventory(output: str | Path, *, seed: int = 42) -> dict[str, Any]:
    """Write only into a new or empty local directory; never overwrite existing work."""
    records = build_inventory(seed)
    root = Path(output)
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise FileExistsError(f"inventory output must be a new or empty directory: {root}")
    root.mkdir(parents=True, exist_ok=True)
    artifacts = []
    for family in FAMILIES:
        for suffix, writer in (("jsonl", write_jsonl), ("csv", write_csv)):
            path = root / f"{family}.{suffix}"
            writer(path, records[family])
            artifacts.append({"path": path.name, "records": len(records[family]), "sha256": sha256_file(path)})
    for name, value in (("reference_catalog.json", reference_catalog()),
                        ("inventory.schema.json", json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))):
        path = root / name
        path.write_text(canonical_json(value) + "\n", encoding="utf-8", newline="\n")
        artifacts.append({"path": name, "sha256": sha256_file(path)})
    manifest = {
        "generator_version": VERSION, "seed": seed, "is_synthetic": True,
        "operational_use": "prohibited", "local_only": True,
        "run_id": stable_id("grid-inventory", VERSION, seed, artifacts),
        "stock_basis": "opening_balance_only",
        "counts": {family: len(records[family]) for family in FAMILIES},
        "artifacts": artifacts,
    }
    (root / "manifest.json").write_text(canonical_json(manifest) + "\n", encoding="utf-8", newline="\n")
    return manifest
