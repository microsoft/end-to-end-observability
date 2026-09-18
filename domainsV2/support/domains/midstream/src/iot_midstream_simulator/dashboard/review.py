"""Read-only inventory review for the midstream parts layer.

This is the scenario-free view: it reports the inventory itself rather than
what any scenario did to it. Midstream generates no consumption, so the
nominal position is the whole ledger — but the fold is still restricted to
``opening_balance`` explicitly, so the view keeps its meaning if consumption is
added later.

Nothing here is a balance read from storage. Quantities are always derived by
folding the append-only ledger.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from iot_upstream_simulator.dashboard.review import is_hosted


class MidstreamInventoryError(RuntimeError):
    """Raised when the inventory view cannot be served."""


#: Reviewer opt-in for this domain. Deliberately its own flag rather than the
#: upstream one: opting into upstream parts is not a decision about midstream
#: parts, and a shared switch would turn one reviewer's habit into access to a
#: second domain's local-only data without anyone choosing it.
REVIEW_FLAG = "MIDSTREAM_PARTS_REVIEW"

#: Second, separate opt-in required before the view will run on hosted
#: infrastructure, so a local habit cannot follow a deployment into Azure.
HOSTED_FLAG = "MIDSTREAM_PARTS_REVIEW_ALLOW_HOSTED"

_TRUTHY = {"1", "true", "yes", "on"}


def _flag(environ: Mapping[str, str], name: str) -> bool:
    return str(environ.get(name, "")).strip().lower() in _TRUTHY


def review_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Report whether the midstream inventory view may be served at all."""
    environ = os.environ if environ is None else environ
    if not _flag(environ, REVIEW_FLAG):
        return False
    if is_hosted(environ) and not _flag(environ, HOSTED_FLAG):
        return False
    return True


def gate_reason(environ: Mapping[str, str] | None = None) -> str:
    """Explain, for logs only, why the view is off. Never sent to a client."""
    environ = os.environ if environ is None else environ
    if not _flag(environ, REVIEW_FLAG):
        return f"{REVIEW_FLAG} is not set"
    if is_hosted(environ) and not _flag(environ, HOSTED_FLAG):
        return f"hosted infrastructure detected and {HOSTED_FLAG} is not set"
    return "enabled"


#: Families that describe the inventory itself.
INVENTORY_RECORD_TYPES = (
    "part_master",
    "storeroom_location",
    "part_applicability",
    "part_stock_txn",
)

#: The transaction that establishes the nominal position.
NOMINAL_TRANSACTION = "opening_balance"

#: Fields copied straight through to the part table.
_PART_FIELDS = (
    "part_master_id",
    "part_key",
    "part_name",
    "stock_code",
    "part_class",
    "part_scope",
    "criticality_class",
    "abc_class",
    "movement_class",
    "lead_band",
    "nominal_lead_time_days_synthetic",
    "tracking_mode",
    "uom",
    "required_storage_class",
    "is_insurance_spare",
    "is_site_wide",
    "operational_use",
)

#: Fields copied straight through to the location table. The corridor fields
#: are the subject; the planar-grid fields are derived from them and are what
#: lets the portal draw the network over real geography instead of
#: schematising it.
_LOCATION_FIELDS = (
    "location_id",
    "location_code",
    "location_type",
    "storage_classes_supported",
    "stocking_profile",
    "id_tech",
    "velocity_zone",
    "dispatch_latency_class",
    "nominal_dispatch_minutes_synthetic",
    "route_frame",
    "route_position_km",
    "offset_km",
    "on_route",
    "is_mobile",
    "service_radius_km_synthetic",
    "zone",
    "aisle",
    "rack",
    "shelf",
    "bin_code",
    "grid_frame",
    "grid_east_km",
    "grid_north_km",
    "access_bearing_deg",
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for position, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise MidstreamInventoryError(
                f"{path.name} line {position} is not readable as JSON"
            ) from error
    return records


def fold_opening_balances(ledger: list[dict[str, Any]]) -> dict[tuple[str, str], int]:
    """Fold the ledger into derived balances. Never read from a stored value."""
    balances: dict[tuple[str, str], int] = {}
    for record in ledger:
        key = (record["part_master_id"], record["location_id"])
        balances[key] = balances.get(key, 0) + int(record["quantity"])
    return balances


def _require_foldable(ledger: list[dict[str, Any]]) -> None:
    """Fail closed on a ledger that cannot be trusted.

    Validation runs over the whole ledger, not the filtered subset, so the
    reported position points at the real row.
    """
    for position, record in enumerate(ledger, 1):
        for field in ("part_master_id", "location_id", "quantity"):
            if field not in record:
                raise MidstreamInventoryError(
                    f"part_stock_txn record {position} is missing {field}"
                )
        if not isinstance(record["quantity"], int) or isinstance(record["quantity"], bool):
            raise MidstreamInventoryError(
                f"part_stock_txn record {position} has a non-integer quantity"
            )


def _require_finite(value: Any, what: str) -> float:
    """Return a spatial value, or fail.

    A missing or non-finite coordinate must never be coerced to zero: on the
    map that silently moves a record to the start of the corridor, which reads
    as a fact rather than as the absence of one.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MidstreamInventoryError(f"{what} is missing or not a number")
    if value != value or value in (float("inf"), float("-inf")):
        raise MidstreamInventoryError(f"{what} is not a finite number")
    return float(value)


class MidstreamInventoryReview:
    """Serve the midstream inventory from a generated dataset directory."""

    def __init__(self, root: str | Path, *, hosted: bool = False) -> None:
        self.root = Path(root)
        self.hosted = hosted

    def _resolve(self) -> Path:
        """Find the dataset directory to read.

        The configured root may be the dataset directory itself or a parent
        holding several generated runs. Both are accepted so that a deployment
        does not have to be reconfigured every time a run is regenerated.

        When several runs qualify the selection is **lexical**: the greatest
        directory name wins. That is deterministic and needs no timestamp it
        would have to trust, but it is not the same as the newest run, and it
        is reported on the payload so the reader is not left guessing which
        dataset the page is showing.
        """
        if not self.root.exists():
            raise MidstreamInventoryError(
                f"no generated midstream dataset at {self.root}"
            )
        if (self.root / "part_master.jsonl").exists():
            return self.root
        candidates = sorted(
            child
            for child in self.root.iterdir()
            if child.is_dir() and (child / "part_master.jsonl").exists()
        )
        if not candidates:
            raise MidstreamInventoryError(
                f"no midstream parts artifacts under {self.root}; generate a "
                "midstream dataset first"
            )
        return candidates[-1]

    def inventory(self) -> dict[str, Any]:
        directory = self._resolve()
        present = {
            record_type: (directory / f"{record_type}.jsonl").exists()
            for record_type in INVENTORY_RECORD_TYPES
        }
        parts = _read_jsonl(directory / "part_master.jsonl")
        locations = _read_jsonl(directory / "storeroom_location.jsonl")
        applicability = _read_jsonl(directory / "part_applicability.jsonl")
        ledger = _read_jsonl(directory / "part_stock_txn.jsonl")
        travel = _read_jsonl(directory / "travel_matrix.jsonl")

        locations_by_id = {
            record["location_id"]: record
            for record in locations
            if "location_id" in record
        }

        _require_foldable(ledger)
        opening = [
            record
            for record in ledger
            if record.get("transaction_type") == NOMINAL_TRANSACTION
        ]
        stock_known = bool(opening)
        balances = fold_opening_balances(opening) if opening else {}

        fits_by_part: dict[str, list[dict[str, Any]]] = {}
        for record in applicability:
            fits_by_part.setdefault(record.get("part_key"), []).append(
                {
                    "asset_type": record.get("asset_type"),
                    "asset_config_code": record.get("asset_config_code"),
                    "quantity_per_assembly": record.get("quantity_per_assembly"),
                    "fit_spec_code": record.get("fit_spec_code"),
                    "fitment_claim": record.get("fitment_claim"),
                }
            )

        stock_rows: list[dict[str, Any]] = []
        held_by_part: dict[str, list[dict[str, Any]]] = {}
        for part_id, location_id in sorted(balances):
            location = locations_by_id.get(location_id, {})
            row = {
                "part_master_id": part_id,
                "location_id": location_id,
                "location_code": location.get("location_code"),
                "location_type": location.get("location_type"),
                "quantity": balances[(part_id, location_id)],
            }
            stock_rows.append(row)
            held_by_part.setdefault(part_id, []).append(row)

        part_rows = []
        for record in sorted(parts, key=lambda item: str(item.get("part_key", ""))):
            part_id = record.get("part_master_id")
            held = held_by_part.get(part_id, [])
            fits = sorted(
                fits_by_part.get(record.get("part_key"), []),
                key=lambda item: str(item.get("asset_config_code", "")),
            )
            part_rows.append(
                {
                    **{field: record.get(field) for field in _PART_FIELDS},
                    "quantity": sum(row["quantity"] for row in held) if stock_known else None,
                    "location_count": len(held) if stock_known else None,
                    "held_at": held,
                    "fits": fits,
                    # Absence of fitment is a fact about the catalogue, never a
                    # zero quantity.
                    "has_fitment": bool(fits),
                }
            )

        location_rows = []
        for record in sorted(
            locations, key=lambda item: str(item.get("location_code", ""))
        ):
            code = record.get("location_code")
            _require_finite(
                record.get("route_position_km"), f"{code} route_position_km"
            )
            _require_finite(record.get("offset_km"), f"{code} offset_km")
            held = [
                row
                for row in stock_rows
                if row["location_id"] == record.get("location_id")
            ]
            location_rows.append(
                {
                    **{field: record.get(field) for field in _LOCATION_FIELDS},
                    "part_count": len(held) if stock_known else None,
                    "quantity": sum(row["quantity"] for row in held) if stock_known else None,
                }
            )

        asset_rows = self._corridor_assets(travel)

        return {
            "source_dataset": directory.name,
            "dataset_selection": (
                "explicit_root"
                if directory == self.root
                else "greatest_directory_name_under_root"
            ),
            "hosted": self.hosted,
            "domain": "midstream",
            "families_present": present,
            "route_frame": "fictional-route-frame-v1",
            "grid_frame": "fictional-planar-grid-ms-v1",
            "position_claim": "none",
            "corridor_length_km": max(
                [row["route_position_km"] for row in asset_rows] or [0.0]
            ),
            "stock_basis": "nominal_opening_position",
            "stock_is_known": stock_known,
            "stock_note": (
                "Quantities are the nominal position: the append-only ledger "
                "folded over opening_balance transactions only. Nothing is "
                "read from a stored balance. This domain generates no "
                "consumption, so the nominal position is the whole ledger. "
                "The transfer corridor is short, but the supply network behind "
                "it is regional: most stock sits off the corridor, and the "
                "distance to it is the point of the location model."
                if stock_known
                else "part_stock_txn.jsonl records no opening_balance "
                "transaction, so no quantity can be derived. This is unknown, "
                "not zero."
            ),
            "scenario_invariance": {
                "claim": "inventory_is_generated_identically_in_every_scenario",
                "basis": "asserted_by_contract_test_not_by_this_page",
            },
            "totals": {
                "parts": len(part_rows) if present["part_master"] else None,
                "locations": len(location_rows) if present["storeroom_location"] else None,
                "stock_rows": len(stock_rows) if stock_known else None,
                "units": sum(row["quantity"] for row in stock_rows) if stock_known else None,
                "fitment_rows": len(applicability) if present["part_applicability"] else None,
                "travel_rows": len(travel),
            },
            "parts": part_rows,
            "locations": location_rows,
            "stock": stock_rows,
            "assets": asset_rows,
        }

    def _corridor_assets(self, travel: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Asset positions along the corridor, for the map.

        The corridor position is parts-layer data and is published on the
        travel row rather than recovered by inverting the distance formula,
        which would silently depend on which origin happened to be chosen.

        Every row is validated before any deduplication. Checking only the
        first row seen for an asset would accept a later row that disagreed
        with it, and the disagreement would never surface: the reader would be
        shown one of two contradictory positions with no sign that the other
        existed.
        """
        seen: dict[str, dict[str, Any]] = {}
        for position, row in enumerate(travel, 1):
            asset_id = row.get("to_asset_id")
            if not isinstance(asset_id, str) or not asset_id:
                raise MidstreamInventoryError(
                    f"travel_matrix row {position} is missing to_asset_id"
                )
            asset_type = row.get("to_asset_type")
            if not isinstance(asset_type, str) or not asset_type:
                raise MidstreamInventoryError(
                    f"travel_matrix row {position} is missing to_asset_type"
                )
            route_position_km = _require_finite(
                row.get("to_asset_route_position_km"),
                f"travel_matrix row {position} to_asset_route_position_km",
            )
            entry = {
                "asset_id": asset_id,
                "asset_type": asset_type,
                "route_position_km": route_position_km,
            }
            existing = seen.get(asset_id)
            if existing is None:
                seen[asset_id] = entry
            elif existing != entry:
                raise MidstreamInventoryError(
                    f"travel_matrix row {position} disagrees with an earlier "
                    f"row about {asset_id}"
                )
        return [seen[asset_id] for asset_id in sorted(seen)]
