"""Strict read-only local inventory baseline; route gates are enforced by the gateway."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from iot_upstream_simulator.dashboard.review import is_hosted

from ..inventory import FAMILIES, GRID_FRAME, InventoryError, validate_records

REVIEW_FLAG = "POWER_GRID_PARTS_REVIEW"
HOSTED_FLAG = "POWER_GRID_PARTS_REVIEW_ALLOW_HOSTED"
_TRUTHY = {"1", "true", "yes", "on"}


class PowerGridInventoryError(RuntimeError):
    """The required catalogs or inventory observations cannot be trusted."""


def _flag(environ: Mapping[str, str], name: str) -> bool:
    return str(environ.get(name, "")).strip().lower() in _TRUTHY


def review_enabled(environ: Mapping[str, str] | None = None) -> bool:
    environ = os.environ if environ is None else environ
    return _flag(environ, REVIEW_FLAG) and (not is_hosted(environ) or _flag(environ, HOSTED_FLAG))


def gate_reason(environ: Mapping[str, str] | None = None) -> str:
    environ = os.environ if environ is None else environ
    if not _flag(environ, REVIEW_FLAG):
        return f"{REVIEW_FLAG} is not set"
    if is_hosted(environ) and not _flag(environ, HOSTED_FLAG):
        return f"hosted infrastructure detected and {HOSTED_FLAG} is not set"
    return "enabled"


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InventoryError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read(path: Path, *, required: bool) -> list[dict[str, Any]]:
    if not path.exists() and not required:
        return []
    rows = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise InventoryError(f"{path.name} line {index}: blank record")
        try:
            rows.append(json.loads(line, object_pairs_hook=_object))
        except json.JSONDecodeError as exc:
            raise InventoryError(f"{path.name} line {index}: invalid JSON") from exc
    return rows


class PowerGridInventoryReview:
    def __init__(self, root: str | Path, *, hosted: bool = False) -> None:
        self.root = Path(root)
        self.hosted = hosted

    def inventory(self) -> dict[str, Any]:
        try:
            records = {
                family: _read(self.root / f"{family}.jsonl", required=family != "part_stock_txn")
                for family in FAMILIES
            }
            validate_records(records)
        except (ValueError, OSError, UnicodeError, OverflowError, RecursionError) as exc:
            raise PowerGridInventoryError(f"Invalid Power Grid inventory: {exc}") from exc

        locations_by_id = {row["location_id"]: row for row in records["storeroom_location"]}
        balances: dict[tuple[str, str], int] = {}
        for row in records["part_stock_txn"]:
            if row["transaction_type"] == "opening_balance":
                key = (row["part_master_id"], row["location_id"])
                balances[key] = balances.get(key, 0) + row["quantity"]
        stock = [{
            "part_master_id": part_id, "location_id": location_id,
            "location_code": locations_by_id[location_id]["location_code"],
            "location_type": locations_by_id[location_id]["location_type"],
            "quantity": quantity,
        } for (part_id, location_id), quantity in sorted(balances.items())]
        parts = []
        for part in sorted(records["part_master"], key=lambda row: row["part_key"]):
            held = [dict(row) for row in stock if row["part_master_id"] == part["part_master_id"]]
            fits = sorted(
                (dict(row) for row in records["part_applicability"] if row["part_master_id"] == part["part_master_id"]),
                key=lambda row: row["part_applicability_id"],
            )
            parts.append({**part, "quantity": sum(row["quantity"] for row in held) if held else None,
                          "held_at": held, "fits": fits})
        locations = []
        for location in sorted(locations_by_id.values(), key=lambda row: row["location_code"]):
            positions = [row for row in stock if row["location_id"] == location["location_id"]]
            locations.append({**location, "part_count": len(positions) if positions else None,
                              "quantity": None})
        return {
            "source_dataset": self.root.name, "dataset_selection": "explicit_root",
            "hosted": self.hosted, "domain": "power-grid", "is_synthetic": True,
            "operational_use": "prohibited", "grid_frame": GRID_FRAME,
            "stock_is_known": bool(balances), "stock_basis": "opening_balance_only",
            "stock_note": (
                "Opening balances only; zero is an observed zero. Missing positions remain unknown. "
                "All stock and storage choices are fictional. Same-SKU quantities only; no mixed-unit total."
                if balances else
                "No opening_balance observations: stock is unknown, not zero; movement is unavailable."
            ),
            "totals": {"parts": len(parts), "locations": len(locations), "stock_rows": len(stock)},
            "parts": parts, "locations": locations, "stock": stock,
        }
