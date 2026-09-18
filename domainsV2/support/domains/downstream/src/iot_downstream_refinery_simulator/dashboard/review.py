"""Read-only inventory review for the downstream refinery parts layer.

This is the scenario-free view: it reports the inventory itself rather than
what any scenario did to it. Downstream generates no consumption, so the
nominal position is the whole ledger — but the fold is still restricted to
``opening_balance`` explicitly, so the view keeps its meaning if consumption is
added later.

Nothing here is a balance read from storage. Quantities are always derived by
folding the append-only ledger.

The spatial model differs from midstream. Midstream storerooms sit along a
transfer corridor and are placed by route position and offset. A refinery is a
fenced site, so locations here are placed on a local site grid in metres, and
the same point is also published in the shared planar-grid frame so the site
can be located relative to the midstream corridor that feeds it.

Off-site locations — the distribution centre, consignment stock, the repair
vendor and the OEM — carry no coordinate at all. That is deliberate and is
preserved rather than repaired: inventing a point outside the fence would draw
a fact that does not exist.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from iot_upstream_simulator.dashboard.review import is_hosted

from ..parts import GRID_FRAME, SITE_DEPTH_M, SITE_FRAME, SITE_WIDTH_M


class DownstreamInventoryError(RuntimeError):
    """Raised when the inventory view cannot be served."""


#: Reviewer opt-in for this domain. Deliberately its own flag rather than the
#: midstream or upstream one: opting into one domain's parts is not a decision
#: about another's, and a shared switch would turn one reviewer's habit into
#: access to a second domain's local-only data without anyone choosing it.
REVIEW_FLAG = "DOWNSTREAM_PARTS_REVIEW"

#: Second, separate opt-in required before the view will run on hosted
#: infrastructure, so a local habit cannot follow a deployment into Azure.
HOSTED_FLAG = "DOWNSTREAM_PARTS_REVIEW_ALLOW_HOSTED"

_TRUTHY = {"1", "true", "yes", "on"}


def _flag(environ: Mapping[str, str], name: str) -> bool:
    return str(environ.get(name, "")).strip().lower() in _TRUTHY


def review_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Report whether the downstream inventory view may be served at all."""
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
    "equipment_register",
    "part_master",
    "storeroom_location",
    "part_applicability",
    "part_stock_txn",
    "travel_matrix",
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
    "shelf_life_class",
    "shelf_life_years_synthetic",
    "is_insurance_spare",
    "is_site_wide",
    "applicable_equipment_classes",
    "operational_use",
)

#: Fields copied straight through to the location table. The site fields are
#: the subject; the planar-grid fields are derived from them and are what lets
#: the portal place the refinery in the same world as the midstream corridor.
_LOCATION_FIELDS = (
    "location_id",
    "location_code",
    "location_name",
    "location_type",
    "storage_classes_supported",
    "stocking_profile",
    "id_tech",
    "velocity_zone",
    "dispatch_latency_class",
    "nominal_handling_minutes_synthetic",
    "off_site_lead_hours_synthetic",
    "is_on_site",
    "zone",
    "aisle",
    "rack",
    "shelf",
    "bin_code",
    "shelf_address",
    "site_frame",
    "site_x_m",
    "site_y_m",
    "grid_frame",
    "grid_east_km",
    "grid_north_km",
    "position_claim",
)

#: Fields copied straight through to the equipment table.
_EQUIPMENT_FIELDS = (
    "equipment_id",
    "equipment_tag",
    "equipment_class",
    "equipment_class_label",
    "service_description",
    "area_id",
    "area_name",
    "parent_asset_id",
    "parent_asset_type",
    "is_critical_service",
    "is_spared",
    "install_year_synthetic",
    "site_frame",
    "site_x_m",
    "site_y_m",
    "grid_frame",
    "grid_east_km",
    "grid_north_km",
    "register_scope",
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
            raise DownstreamInventoryError(
                f"{path.name} line {position} is not readable as JSON"
            ) from error
    return records


def fold_opening_balances(ledger: list[dict[str, Any]]) -> dict[tuple[str, str], int]:
    """Fold the ledger into derived balances. Never read from a stored value.

    The filter on ``transaction_type`` is applied here rather than left to the
    caller. The function's name is a promise, and a caller that hands it a
    mixed ledger must get the opening position back, not a silent sum of
    opening balances and movements.
    """
    balances: dict[tuple[str, str], int] = {}
    for record in ledger:
        if record.get("transaction_type") != NOMINAL_TRANSACTION:
            continue
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
                raise DownstreamInventoryError(
                    f"part_stock_txn record {position} is missing {field}"
                )
        if not isinstance(record["quantity"], int) or isinstance(record["quantity"], bool):
            raise DownstreamInventoryError(
                f"part_stock_txn record {position} has a non-integer quantity"
            )


def _require_finite(value: Any, what: str) -> float:
    """Return a spatial value, or fail.

    A missing or non-finite coordinate must never be coerced to zero: on the
    site plan that silently moves a record to the south-west corner of the
    fence, which reads as a fact rather than as the absence of one.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DownstreamInventoryError(f"{what} is missing or not a number")
    if value != value or value in (float("inf"), float("-inf")):
        raise DownstreamInventoryError(f"{what} is not a finite number")
    return float(value)


def _require_absent(value: Any, what: str) -> None:
    """Require that an off-site coordinate is genuinely absent.

    ``None`` is the only acceptable value. A number here would mean an off-site
    location had been given a position on the site plan, which is exactly the
    fabrication the location model exists to avoid, and it would be drawn
    without any indication that it was invented.
    """
    if value is not None:
        raise DownstreamInventoryError(
            f"{what} is set on an off-site location; it must be null"
        )


class DownstreamInventoryReview:
    """Serve the downstream inventory from a generated dataset directory."""

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
            raise DownstreamInventoryError(
                f"no generated downstream dataset at {self.root}"
            )
        if (self.root / "part_master.jsonl").exists():
            return self.root
        candidates = sorted(
            child
            for child in self.root.iterdir()
            if child.is_dir() and (child / "part_master.jsonl").exists()
        )
        if not candidates:
            raise DownstreamInventoryError(
                f"no downstream parts artifacts under {self.root}; generate a "
                "downstream dataset first"
            )
        return candidates[-1]

    def inventory(self) -> dict[str, Any]:
        directory = self._resolve()
        present = {
            record_type: (directory / f"{record_type}.jsonl").exists()
            for record_type in INVENTORY_RECORD_TYPES
        }
        equipment = _read_jsonl(directory / "equipment_register.jsonl")
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
                    "equipment_class": record.get("equipment_class"),
                    "equipment_class_label": record.get("equipment_class_label"),
                    "quantity_per_assembly": record.get("quantity_per_assembly"),
                    "fit_spec_code": record.get("fit_spec_code"),
                    "condition_eligibility": record.get("condition_eligibility"),
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
                "location_name": location.get("location_name"),
                "location_type": location.get("location_type"),
                "is_on_site": location.get("is_on_site"),
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
                key=lambda item: str(item.get("equipment_class", "")),
            )
            part_rows.append(
                {
                    **{field: record.get(field) for field in _PART_FIELDS},
                    "quantity": sum(row["quantity"] for row in held) if stock_known else None,
                    "location_count": len(held) if stock_known else None,
                    "held_at": held,
                    "fits": fits,
                    # Absence of fitment is a fact about the catalogue, never a
                    # zero quantity. Site-wide consumables legitimately fit no
                    # single equipment class.
                    "has_fitment": bool(fits),
                }
            )

        location_rows = self._location_rows(locations, stock_rows, stock_known)
        equipment_rows, area_rows = self._equipment_and_areas(equipment)
        travel_rows = self._travel_rows(travel, locations_by_id)

        on_site = [row for row in location_rows if row.get("is_on_site")]
        off_site = [row for row in location_rows if not row.get("is_on_site")]

        return {
            "source_dataset": directory.name,
            "dataset_selection": (
                "explicit_root"
                if directory == self.root
                else "greatest_directory_name_under_root"
            ),
            "hosted": self.hosted,
            "domain": "downstream",
            "families_present": present,
            "site_frame": SITE_FRAME,
            "grid_frame": GRID_FRAME,
            "position_claim": "none",
            # The fence comes from the generator's own constants rather than
            # being restated here, so the drawn boundary cannot drift from the
            # frame the coordinates were produced in.
            "site_fence_m": {"width": SITE_WIDTH_M, "depth": SITE_DEPTH_M},
            "site_extent_m": self._site_extent(location_rows, equipment_rows),
            "stock_basis": "nominal_opening_position",
            "stock_is_known": stock_known,
            "stock_note": (
                "Quantities are the nominal position: the append-only ledger "
                "folded over opening_balance transactions only. Nothing is "
                "read from a stored balance. This domain generates no "
                "consumption, so the nominal position is the whole ledger. "
                "The refinery is a fenced site, so most stock sits inside the "
                "fence; the off-site rows are the supply network behind it and "
                "carry no coordinate at all."
                if stock_known
                else "part_stock_txn.jsonl records no opening_balance "
                "transaction, so no quantity can be derived. This is unknown, "
                "not zero."
            ),
            "scenario_invariance": {
                "claim": (
                    "inventory_substance_is_identical_in_every_scenario_only_"
                    "run_scoped_identifiers_differ"
                ),
                "basis": "asserted_by_contract_test_not_by_this_page",
                "note": (
                    "Every business field -- part, location, quantity, fitment, "
                    "position and travel time -- is generated identically in "
                    "every scenario. The surrogate identifiers are derived from "
                    "run and scenario, so those differ between runs and are not "
                    "part of the claim."
                ),
            },
            "totals": {
                "equipment": len(equipment_rows) if present["equipment_register"] else None,
                "areas": len(area_rows),
                "parts": len(part_rows) if present["part_master"] else None,
                "locations": len(location_rows) if present["storeroom_location"] else None,
                "locations_on_site": len(on_site) if present["storeroom_location"] else None,
                "locations_off_site": len(off_site) if present["storeroom_location"] else None,
                "stock_rows": len(stock_rows) if stock_known else None,
                "units": sum(row["quantity"] for row in stock_rows) if stock_known else None,
                "fitment_rows": len(applicability) if present["part_applicability"] else None,
                "travel_rows": len(travel_rows),
            },
            "equipment": equipment_rows,
            "areas": area_rows,
            "parts": part_rows,
            "locations": location_rows,
            "stock": stock_rows,
            "travel": travel_rows,
        }

    def _location_rows(
        self,
        locations: list[dict[str, Any]],
        stock_rows: list[dict[str, Any]],
        stock_known: bool,
    ) -> list[dict[str, Any]]:
        """Project locations, validating the coordinate against the location.

        On-site locations must have a finite point. Off-site locations must
        have none. Both directions are enforced, because a missing on-site
        coordinate and a fabricated off-site one are the two ways this table
        could lie on the map.
        """
        rows: list[dict[str, Any]] = []
        for record in sorted(
            locations, key=lambda item: str(item.get("location_code", ""))
        ):
            code = record.get("location_code")
            on_site = record.get("is_on_site")
            if not isinstance(on_site, bool):
                raise DownstreamInventoryError(
                    f"{code} is_on_site is missing or not a boolean"
                )
            if on_site:
                _require_finite(record.get("site_x_m"), f"{code} site_x_m")
                _require_finite(record.get("site_y_m"), f"{code} site_y_m")
                _require_finite(record.get("grid_east_km"), f"{code} grid_east_km")
                _require_finite(record.get("grid_north_km"), f"{code} grid_north_km")
            else:
                _require_absent(record.get("site_x_m"), f"{code} site_x_m")
                _require_absent(record.get("site_y_m"), f"{code} site_y_m")
                _require_absent(record.get("grid_east_km"), f"{code} grid_east_km")
                _require_absent(record.get("grid_north_km"), f"{code} grid_north_km")
            held = [
                row
                for row in stock_rows
                if row["location_id"] == record.get("location_id")
            ]
            rows.append(
                {
                    **{field: record.get(field) for field in _LOCATION_FIELDS},
                    "part_count": len(held) if stock_known else None,
                    "quantity": sum(row["quantity"] for row in held) if stock_known else None,
                }
            )
        return rows

    def _equipment_and_areas(
        self, equipment: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Project the equipment register and roll it up by area.

        Every equipment row is on site, so every one must carry a finite point.
        The area rollup is derived here rather than read from a stored summary,
        so the counts on the page cannot drift from the rows behind them.
        """
        rows: list[dict[str, Any]] = []
        for position, record in enumerate(
            sorted(equipment, key=lambda item: str(item.get("equipment_tag", ""))), 1
        ):
            tag = record.get("equipment_tag") or f"row {position}"
            _require_finite(record.get("site_x_m"), f"{tag} site_x_m")
            _require_finite(record.get("site_y_m"), f"{tag} site_y_m")
            rows.append({field: record.get(field) for field in _EQUIPMENT_FIELDS})

        areas: dict[str, dict[str, Any]] = {}
        for row in rows:
            area_id = row.get("area_id")
            entry = areas.setdefault(
                area_id,
                {
                    "area_id": area_id,
                    "area_name": row.get("area_name"),
                    "equipment_count": 0,
                    "critical_count": 0,
                    "spared_count": 0,
                    "equipment_classes": set(),
                    "site_x_m": None,
                    "site_y_m": None,
                },
            )
            entry["equipment_count"] += 1
            entry["critical_count"] += 1 if row.get("is_critical_service") else 0
            entry["spared_count"] += 1 if row.get("is_spared") else 0
            entry["equipment_classes"].add(row.get("equipment_class"))

        area_rows = []
        for area_id in sorted(areas):
            entry = areas[area_id]
            members = [row for row in rows if row.get("area_id") == area_id]
            # The area marker is the mean of its equipment, so it is a summary
            # of published rows rather than a separate coordinate that could
            # disagree with them.
            entry["site_x_m"] = round(
                sum(row["site_x_m"] for row in members) / len(members), 6
            )
            entry["site_y_m"] = round(
                sum(row["site_y_m"] for row in members) / len(members), 6
            )
            entry["equipment_classes"] = sorted(entry["equipment_classes"])
            entry["class_count"] = len(entry["equipment_classes"])
            area_rows.append(entry)
        return rows, area_rows

    def _travel_rows(
        self,
        travel: list[dict[str, Any]],
        locations_by_id: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Project the travel matrix, failing on a row that points nowhere.

        A row whose origin is not a known location would be drawn as a line
        from an unknown place, so it is rejected rather than skipped: silently
        dropping it would hide the inconsistency instead of reporting it.
        """
        rows: list[dict[str, Any]] = []
        for position, record in enumerate(travel, 1):
            location_id = record.get("from_location_id")
            if location_id not in locations_by_id:
                raise DownstreamInventoryError(
                    f"travel_matrix row {position} has an unknown "
                    "from_location_id"
                )
            rows.append(
                {
                    "travel_id": record.get("travel_id"),
                    "from_location_id": location_id,
                    "from_location_code": record.get("from_location_code"),
                    "from_location_type": record.get("from_location_type"),
                    "to_area_id": record.get("to_area_id"),
                    "to_area_name": record.get("to_area_name"),
                    "transport_mode": record.get("transport_mode"),
                    "straight_line_m_synthetic": record.get("straight_line_m_synthetic"),
                    "road_distance_m_synthetic": record.get("road_distance_m_synthetic"),
                    "drive_minutes_synthetic": record.get("drive_minutes_synthetic"),
                    "handling_minutes_synthetic": record.get("handling_minutes_synthetic"),
                    "retrieval_minutes_synthetic": record.get("retrieval_minutes_synthetic"),
                    "routing_claim": record.get("routing_claim"),
                }
            )
        return rows

    def _site_extent(
        self,
        locations: list[dict[str, Any]],
        equipment: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Report the drawn extent of the site, derived from the rows drawn.

        The portal needs a viewport. Deriving it from the data rather than
        hard-coding the fence means the map cannot silently crop a row that
        falls outside an assumed boundary.
        """
        xs = [
            row["site_x_m"]
            for row in list(locations) + list(equipment)
            if isinstance(row.get("site_x_m"), (int, float))
            and not isinstance(row.get("site_x_m"), bool)
        ]
        ys = [
            row["site_y_m"]
            for row in list(locations) + list(equipment)
            if isinstance(row.get("site_y_m"), (int, float))
            and not isinstance(row.get("site_y_m"), bool)
        ]
        if not xs or not ys:
            return {"known": False}
        return {
            "known": True,
            "min_x_m": min(xs),
            "max_x_m": max(xs),
            "min_y_m": min(ys),
            "max_y_m": max(ys),
        }
