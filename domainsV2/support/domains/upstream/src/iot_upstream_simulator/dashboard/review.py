"""Local-only review surface for the parts, storeroom and dispatch families.

These eight families are deliberately unreachable through the public portal:
``service.py`` places them in ``PRIVATE_RECORD_TYPES`` and every store there
refuses them. That guard is correct and is not weakened here.

This module is the *reviewer's* door, not a publication path. It reads the same
local artifacts so a human can inspect what was generated before deciding
whether any of it should ever be published. It therefore refuses to operate
unless the reviewer has explicitly opted in, and refuses again if it detects it
is running on hosted infrastructure, unless a second, separate flag says the
owner really means it.

Publication of these families is gated behind design phases 9 and 10. Nothing
in this module opens that gate.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..catalog import LOCAL_ONLY_RECORD_TYPES
from ..parts import derived_available, derived_on_hand, fold_ledger

#: Reviewer opt-in. Absent or falsey means the review surface does not exist.
REVIEW_FLAG = "UPSTREAM_PARTS_REVIEW"

#: Second, separate opt-in required before the surface will run on hosted
#: infrastructure. Deliberately distinct from ``REVIEW_FLAG`` so that a local
#: habit cannot follow a deployment into Azure by accident.
HOSTED_FLAG = "UPSTREAM_PARTS_REVIEW_ALLOW_HOSTED"

#: Environment variables that indicate hosted infrastructure rather than a
#: developer machine. Any one of these being set is treated as hosted.
HOSTED_MARKERS = (
    "WEBSITE_INSTANCE_ID",
    "WEBSITE_SITE_NAME",
    "CONTAINER_APP_NAME",
    "CONTAINER_APP_REVISION",
    "KUBERNETES_SERVICE_HOST",
    "AZURE_CONTAINER_APP_NAME",
)

#: The families this surface may read. Exactly the local-only parts families.
REVIEW_RECORD_TYPES = tuple(LOCAL_ONLY_RECORD_TYPES)

MAX_PAGE_SIZE = 200
_TRUTHY = frozenset({"1", "true", "yes", "on"})


class PartsReviewError(RuntimeError):
    """Raised when the review surface is unavailable or misused."""


class PartsReviewDisabled(PartsReviewError):
    """Raised when the reviewer has not opted in."""


class PartsReviewNotFound(PartsReviewError):
    """Raised when a requested run, record type or alert does not exist.

    Distinct from the base error so a mistyped identifier reads as a 404 rather
    than implying the whole review surface has failed.
    """


def _flag(environ: Mapping[str, str], name: str) -> bool:
    return str(environ.get(name, "")).strip().lower() in _TRUTHY


def is_hosted(environ: Mapping[str, str] | None = None) -> bool:
    """Report whether this process looks like hosted infrastructure."""
    environ = os.environ if environ is None else environ
    return any(environ.get(marker) for marker in HOSTED_MARKERS)


def review_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Report whether the review surface may be served at all.

    Two independent conditions must hold. The reviewer must opt in, and if the
    process looks hosted the owner must separately say so. The default in every
    other case is off.
    """
    environ = os.environ if environ is None else environ
    if not _flag(environ, REVIEW_FLAG):
        return False
    if is_hosted(environ) and not _flag(environ, HOSTED_FLAG):
        return False
    return True


def gate_reason(environ: Mapping[str, str] | None = None) -> str:
    """Explain, for logs only, why the surface is off. Never sent to a client."""
    environ = os.environ if environ is None else environ
    if not _flag(environ, REVIEW_FLAG):
        return f"{REVIEW_FLAG} is not set"
    if is_hosted(environ) and not _flag(environ, HOSTED_FLAG):
        return f"hosted infrastructure detected and {HOSTED_FLAG} is not set"
    return "enabled"


def _require_foldable(ledger: list[dict[str, Any]]) -> None:
    """Reject a truncated ledger row before anything tries to fold it.

    ``fold_ledger`` indexes these keys directly, so a missing one would raise a
    bare ``KeyError`` and reach the reviewer as an opaque 500. Every caller that
    folds must run this first; the check lives here rather than in one method so
    a second folding path cannot quietly skip it.
    """
    required = {
        "part_master_id",
        "location_id",
        "quantity",
        "from_state",
        "to_state",
    }
    for number, record in enumerate(ledger, start=1):
        missing = required - set(record)
        if missing:
            raise PartsReviewError(
                "part_stock_txn.jsonl record "
                f"{number} is missing {', '.join(sorted(missing))}"
            )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise PartsReviewError(
                        f"{path.name} line {number} is not valid JSON: {error.msg}"
                    ) from error
                if not isinstance(record, dict):
                    raise PartsReviewError(
                        f"{path.name} line {number} is not a JSON object"
                    )
                records.append(record)
    except OSError as error:
        raise PartsReviewError(f"cannot read {path.name}: {error}") from error
    return records


def _read_manifest(directory: Path) -> dict[str, Any]:
    path = directory / "manifest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise PartsReviewError(
            f"{path} is not valid JSON: {error.msg}"
        ) from error
    except OSError as error:
        raise PartsReviewError(f"cannot read {path}: {error}") from error
    if not isinstance(payload, dict):
        raise PartsReviewError(f"{path} is not a JSON object")
    return payload


class PartsReviewStore:
    """Read-only view over the local-only parts families in one dataset root.

    A root is either a single dataset directory containing ``manifest.json``, or
    a parent directory holding several such directories. Nothing here writes,
    mutates or republishes; every method returns plain data.
    """

    def __init__(
        self, root: str | Path, environ: Mapping[str, str] | None = None
    ) -> None:
        environ = os.environ if environ is None else environ
        if not review_enabled(environ):
            raise PartsReviewDisabled(
                "the parts review surface is disabled: " + gate_reason(environ)
            )
        self.root = Path(root)
        # Kept so a view can report *where* it is serving from. The page header
        # asserts "local only", which stops being true the moment the owner
        # opens the hosted flag, and a banner that lies about its own exposure
        # is worse than no banner.
        self.hosted = is_hosted(environ)
        if not self.root.exists():
            raise PartsReviewError(f"review root does not exist: {self.root}")
        if not self.root.is_dir():
            raise PartsReviewError(f"review root is not a directory: {self.root}")

    # -- run discovery ----------------------------------------------------

    def _run_directories(self) -> list[Path]:
        if (self.root / "manifest.json").exists():
            return [self.root]
        try:
            entries = list(self.root.iterdir())
        except OSError as error:
            raise PartsReviewError(
                f"cannot list review root {self.root}: {error}"
            ) from error
        return sorted(
            path
            for path in entries
            if path.is_dir() and (path / "manifest.json").exists()
        )

    def _resolve(self, run_id: str | None) -> Path:
        directories = self._run_directories()
        if not directories:
            raise PartsReviewError(
                f"no generated dataset with a manifest was found under {self.root}"
            )
        if run_id is None:
            return directories[0]
        for directory in directories:
            if self._run_id(directory) == run_id:
                return directory
        raise PartsReviewNotFound("unknown run identifier")

    @staticmethod
    def _run_id(directory: Path) -> str:
        manifest = _read_manifest(directory)
        return str(manifest.get("run_id", directory.name))

    def runs(self) -> list[dict[str, Any]]:
        """List every reviewable dataset under the root."""
        entries = []
        for directory in self._run_directories():
            manifest = _read_manifest(directory)
            entries.append(
                {
                    "run_id": str(manifest.get("run_id", directory.name)),
                    "scenario": manifest.get("scenario"),
                    "directory": directory.name,
                    "generator_version": manifest.get("generator_version"),
                    "parts_families_present": sorted(
                        record_type
                        for record_type in REVIEW_RECORD_TYPES
                        if (directory / f"{record_type}.jsonl").exists()
                    ),
                }
            )
        return entries

    # -- record access ----------------------------------------------------

    def _load(self, record_type: str, run_id: str | None = None) -> list[dict[str, Any]]:
        if record_type not in REVIEW_RECORD_TYPES:
            raise PartsReviewNotFound("unsupported review record type")
        return _read_jsonl(self._resolve(run_id) / f"{record_type}.jsonl")

    def families(self, run_id: str | None = None) -> list[dict[str, Any]]:
        """Report every parts family and how many records it holds."""
        directory = self._resolve(run_id)
        summary = []
        for record_type in REVIEW_RECORD_TYPES:
            path = directory / f"{record_type}.jsonl"
            summary.append(
                {
                    "record_type": record_type,
                    "present": path.exists(),
                    "record_count": len(_read_jsonl(path)),
                    "bytes": path.stat().st_size if path.exists() else 0,
                }
            )
        return summary

    def records(
        self,
        record_type: str,
        *,
        run_id: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Page one family. Paging is bounded so a browser cannot be flooded."""
        records = self._load(record_type, run_id)
        offset = max(0, int(offset))
        limit = max(1, min(int(limit), MAX_PAGE_SIZE))
        window = records[offset : offset + limit]
        return {
            "record_type": record_type,
            "total": len(records),
            "offset": offset,
            "limit": limit,
            "returned": len(window),
            "fields": sorted(window[0]) if window else [],
            "records": window,
        }

    # -- the three review lenses -----------------------------------------

    def trace(self, alert_id: str, *, run_id: str | None = None) -> dict[str, Any]:
        """Follow one alert from candidate through demand and stock to dispatch."""
        candidates = [
            record
            for record in self._load("part_candidate", run_id)
            if record.get("alert_id") == alert_id
        ]
        if not candidates:
            raise PartsReviewNotFound("unknown alert identifier")
        dispatches = [
            record
            for record in self._load("dispatch_context", run_id)
            if record.get("triggering_alert_id") == alert_id
        ]
        request_ids = {
            request_id
            for dispatch in dispatches
            for request_id in dispatch.get("demand_request_ids", [])
        }
        dispatch_ids = {dispatch.get("dispatch_id") for dispatch in dispatches}
        demands = [
            record
            for record in self._load("part_demand", run_id)
            if record.get("request_id") in request_ids
        ]
        transactions = [
            record
            for record in self._load("part_stock_txn", run_id)
            if record.get("dispatch_id") in dispatch_ids
        ]
        legs_present = {
            "candidates": bool(candidates),
            "dispatches": bool(dispatches),
            "demands": bool(demands),
            "transactions": bool(transactions),
        }
        return {
            "alert_id": alert_id,
            "candidates": sorted(candidates, key=lambda record: record.get("rank", 0)),
            "dispatches": dispatches,
            "demands": demands,
            "transactions": sorted(
                transactions, key=lambda record: record.get("txn_sequence", 0)
            ),
            "legs_present": legs_present,
            "chain_is_complete": all(legs_present.values()),
            "chain_note": (
                "A chain is complete only when all four legs are present. An "
                "incomplete chain is a legitimate outcome, not an error: an "
                "unresolved candidate group, or a dispatch that needed no "
                "material, produces no stock movement."
            ),
        }

    def alerts(
        self, *, run_id: str | None = None, limit: int = 200
    ) -> dict[str, Any]:
        """List the traceable alerts, oldest evidence first.

        Truncation is reported rather than hidden, so a reviewer is never left
        believing they have seen every alert in a large dataset.
        """
        seen: dict[str, dict[str, Any]] = {}
        for record in self._load("part_candidate", run_id):
            alert_id = record.get("alert_id")
            if alert_id and alert_id not in seen:
                seen[alert_id] = {
                    "alert_id": alert_id,
                    "asset_id": record.get("asset_id"),
                    "observed_at_utc": record.get("observed_at_utc"),
                    "alert_source": record.get("alert_source"),
                    "coverage_state": record.get("coverage_state"),
                }
        dispositions = {
            record.get("triggering_alert_id"): record.get("disposition")
            for record in self._load("dispatch_context", run_id)
        }
        for alert_id, entry in seen.items():
            entry["disposition"] = dispositions.get(alert_id)
        limit = max(1, min(int(limit), MAX_PAGE_SIZE))
        ordered = sorted(
            seen.values(),
            key=lambda entry: (entry.get("observed_at_utc") or "", entry["alert_id"]),
        )
        page = ordered[:limit]
        return {
            "alerts": page,
            "total": len(ordered),
            "returned": len(page),
            "limit": limit,
            "truncated": len(page) < len(ordered),
            "order": "observed_at_utc ascending, then alert_id",
        }

    def balances(self, *, run_id: str | None = None) -> dict[str, Any]:
        """Fold the ledger live, so the reviewer sees balances being derived.

        No balance is read from any record. This calls the same fold the
        simulator and its tests use, which is the point: the reviewer is shown
        the derivation, not a stored number that could silently disagree.
        """
        ledger = self._load("part_stock_txn", run_id)
        if not ledger:
            return {
                "available": False,
                "note": (
                    "part_stock_txn.jsonl is not present in this dataset. The "
                    "committed sample deliberately omits the bulkiest families; "
                    "generate a dataset to review the ledger."
                ),
                "rows": [],
            }
        # Validate before folding, not after: fold_ledger indexes these keys
        # directly, so a truncated row would raise a bare KeyError and surface
        # as a 500 before any guard placed below it could fire.
        _require_foldable(ledger)
        balances = fold_ledger(ledger)
        parts = {
            record["part_master_id"]: record
            for record in self._load("part_master", run_id)
            if "part_master_id" in record
        }
        locations = {
            record["location_id"]: record
            for record in self._load("storeroom_location", run_id)
            if "location_id" in record
        }
        pairs = sorted(
            {
                (record["part_master_id"], record["location_id"])
                for record in ledger
            }
        )
        rows = []
        for part_id, location_id in pairs:
            part = parts.get(part_id, {})
            location = locations.get(location_id, {})
            rows.append(
                {
                    "part_master_id": part_id,
                    "part_key": part.get("part_key"),
                    "location_id": location_id,
                    "location_code": location.get("location_code"),
                    "location_type": location.get("location_type"),
                    "on_hand": derived_on_hand(balances, part_id, location_id),
                    "available": derived_available(balances, part_id, location_id),
                }
            )
        return {
            "available": True,
            "balance_policy": "derived_by_folding_never_stored",
            "transaction_count": len(ledger),
            "rows": rows,
        }

    # -- the scenario-free inventory view ---------------------------------

    #: Families that describe the inventory itself rather than what a scenario
    #: did to it. Their business content is generated the same way in every
    #: scenario; only surrogate identifiers are salted per run.
    INVENTORY_RECORD_TYPES = (
        "part_master",
        "storeroom_location",
        "part_applicability",
        "part_stock_txn",
    )

    #: The transaction that establishes the nominal position. Everything else in
    #: the ledger is scenario-driven consumption.
    NOMINAL_TRANSACTION = "opening_balance"

    def inventory(self, *, run_id: str | None = None) -> dict[str, Any]:
        """Report the inventory itself, with no scenario applied.

        Stock here is the *nominal* position: the ledger folded over
        ``opening_balance`` transactions only. Reservations and issues belong to
        a scenario, so including them would smuggle the scenario dimension back
        into a view whose whole purpose is to exclude it.

        The claim that this is scenario-free is checked rather than asserted.
        Every dataset under the root is compared on business content, and the
        result is published so a reader can see whether the invariance actually
        holds for the data in front of them.
        """
        directory = self._resolve(run_id)
        present = {
            record_type: (directory / f"{record_type}.jsonl").exists()
            for record_type in self.INVENTORY_RECORD_TYPES
        }
        parts = self._load("part_master", run_id)
        locations = self._load("storeroom_location", run_id)
        applicability = self._load("part_applicability", run_id)
        ledger = self._load("part_stock_txn", run_id)

        locations_by_id = {
            record["location_id"]: record
            for record in locations
            if "location_id" in record
        }

        opening = [
            record
            for record in ledger
            if record.get("transaction_type") == self.NOMINAL_TRANSACTION
        ]
        stock_known = bool(opening)
        # Validate the whole ledger, not the filtered subset: _require_foldable
        # reports a record position, and filtering first would make it point at
        # the wrong row. This fails closed — a truncated row the nominal
        # position never folds still refuses the page — which is deliberate and
        # matches balances(): a ledger that cannot be trusted in part should not
        # be presented as trustworthy in whole.
        _require_foldable(ledger)
        balances = fold_ledger(opening) if opening else {}

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
        for part_id, location_id in sorted(
            {
                (record["part_master_id"], record["location_id"])
                for record in opening
            }
        ):
            location = locations_by_id.get(location_id, {})
            quantity = derived_on_hand(balances, part_id, location_id)
            row = {
                "part_master_id": part_id,
                "location_id": location_id,
                "location_code": location.get("location_code"),
                "location_type": location.get("location_type"),
                "quantity": quantity,
            }
            stock_rows.append(row)
            held_by_part.setdefault(part_id, []).append(row)

        part_rows = []
        for record in sorted(parts, key=lambda item: str(item.get("part_key", ""))):
            part_id = record.get("part_master_id")
            held = held_by_part.get(part_id, [])
            part_rows.append(
                {
                    **{
                        field: record.get(field)
                        for field in (
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
                            "is_kit",
                            "operational_use",
                        )
                    },
                    "quantity": sum(row["quantity"] for row in held) if stock_known else None,
                    "location_count": len(held) if stock_known else None,
                    "held_at": held,
                    "fits": sorted(
                        fits_by_part.get(record.get("part_key"), []),
                        key=lambda item: str(item.get("asset_config_code", "")),
                    ),
                }
            )

        location_rows = []
        for record in sorted(
            locations, key=lambda item: str(item.get("location_code", ""))
        ):
            held = [
                row
                for row in stock_rows
                if row["location_id"] == record.get("location_id")
            ]
            location_rows.append(
                {
                    **{
                        field: record.get(field)
                        for field in (
                            "location_id",
                            "location_code",
                            "location_type",
                            "storage_classes_supported",
                            "id_tech",
                            "velocity_zone",
                            "dispatch_latency_class",
                            "nominal_dispatch_minutes_synthetic",
                            "grid_frame",
                            "grid_east_km",
                            "grid_north_km",
                            "grid_distance_to_field_km",
                            "service_radius_km_synthetic",
                            "zone",
                            "aisle",
                            "rack",
                            "shelf",
                            "bin_code",
                        )
                    },
                    "part_count": len(held) if stock_known else None,
                    "quantity": sum(row["quantity"] for row in held) if stock_known else None,
                }
            )

        return {
            "source_dataset": directory.name,
            "hosted": self.hosted,
            "families_present": present,
            "stock_basis": "nominal_opening_position",
            "stock_is_known": stock_known,
            "stock_note": (
                "Quantities are the nominal position: the append-only ledger "
                "folded over opening_balance transactions only. Reservations "
                "and issues are scenario consumption and are excluded on "
                "purpose. Nothing is read from a stored balance."
                if stock_known
                else "part_stock_txn.jsonl is not present in this dataset, so "
                "no quantity can be derived. This is unknown, not zero."
                if not present["part_stock_txn"]
                else "part_stock_txn.jsonl is present but records no "
                "opening_balance transaction, so this dataset establishes no "
                "nominal position and no quantity can be derived. This is "
                "unknown, not zero."
            ),
            "scenario_invariance": self._inventory_invariance(),
            "totals": {
                "parts": len(part_rows) if present["part_master"] else None,
                "locations": len(location_rows) if present["storeroom_location"] else None,
                "stock_rows": len(stock_rows) if stock_known else None,
                "units": sum(row["quantity"] for row in stock_rows) if stock_known else None,
                "fitment_rows": len(applicability) if present["part_applicability"] else None,
            },
            "parts": part_rows,
            "locations": location_rows,
            "stock": stock_rows,
        }

    def _inventory_invariance(self) -> dict[str, Any]:
        """Check, rather than assume, that the inventory does not vary by scenario.

        Surrogate identifiers are salted per run, so they are excluded from the
        comparison; everything a reader would call inventory is compared.

        Three outcomes are distinguished rather than collapsed. A family that
        genuinely matches across runs is invariant. A family missing from any
        dataset was never compared, and calling that invariant would be the
        same unknown-treated-as-zero mistake one level up. A single dataset
        matches itself trivially, so ``holds`` is ``None`` rather than ``True``:
        the API is the contract for any future consumer, and a boolean that
        means both "true" and "untested" is an unearned claim.
        """

        def is_surrogate(field: str) -> bool:
            # Identifiers are content-derived per run, including list-valued
            # cross references such as companion_part_master_ids.
            return field.endswith(("_id", "_ids"))

        def signature(records: list[dict[str, Any]]) -> str:
            return json.dumps(
                sorted(
                    json.dumps(
                        {
                            field: value
                            for field, value in record.items()
                            if not is_surrogate(field)
                        },
                        sort_keys=True,
                    )
                    for record in records
                )
            )

        catalogue = ("part_master", "storeroom_location", "part_applicability")
        directories = self._run_directories()
        signatures: dict[str, set[str]] = {name: set() for name in catalogue}
        signatures["nominal_stock_position"] = set()
        missing: dict[str, set[str]] = {}

        def cannot_compare(family: str, reason: str) -> None:
            # Accumulated rather than overwritten: two datasets can fail for two
            # different reasons, and reporting only the last one read would be
            # true but incomplete.
            missing.setdefault(family, set()).add(reason)

        for directory in directories:
            for record_type in catalogue:
                path = directory / f"{record_type}.jsonl"
                if not path.exists():
                    cannot_compare(
                        record_type, "missing from at least one dataset"
                    )
                    continue
                signatures[record_type].add(signature(_read_jsonl(path)))

            path = directory / "part_stock_txn.jsonl"
            join = directory / "storeroom_location.jsonl"
            if not path.exists() or not join.exists():
                # The stock position is only signable when both the ledger and
                # the location table it joins to are present. Without the join
                # every location would collapse to the same placeholder, and a
                # missing table would be reported as a real difference.
                absent = sorted(
                    name
                    for name, candidate in (
                        ("part_stock_txn", path),
                        ("storeroom_location", join),
                    )
                    if not candidate.exists()
                )
                cannot_compare(
                    "nominal_stock_position",
                    f"{' and '.join(absent)} is missing from at least one "
                    "dataset"
                    if len(absent) == 1
                    else f"{' and '.join(absent)} are missing from at least "
                    "one dataset",
                )
                continue
            locations = {
                record["location_id"]: record.get("location_code")
                for record in _read_jsonl(join)
                if "location_id" in record
            }
            # Signed on the run-stable location_code rather than location_type,
            # which is only 1:1 with a location by coincidence of the current
            # fixture and would silently collapse distinct rows if that changed.
            opening = [
                record
                for record in _read_jsonl(path)
                if record.get("transaction_type") == self.NOMINAL_TRANSACTION
            ]
            if any(record.get("location_id") not in locations for record in opening):
                # An unjoinable row cannot be signed honestly, and guessing
                # would manufacture either a match or a difference.
                cannot_compare(
                    "nominal_stock_position",
                    "at least one opening_balance row references a location "
                    "that is not in storeroom_location, so it cannot be signed",
                )
                continue
            signatures["nominal_stock_position"].add(
                json.dumps(
                    sorted(
                        (
                            (
                                str(record.get("part_key")),
                                str(locations[record["location_id"]]),
                                record.get("quantity"),
                            )
                            for record in opening
                        ),
                        # A total order even when a quantity is null; comparing
                        # None to int would raise instead of reporting.
                        key=repr,
                    ),
                    default=repr,
                )
            )

        comparable = sorted(name for name in signatures if name not in missing)
        varies = sorted(name for name in comparable if len(signatures[name]) > 1)
        if len(directories) < 2:
            holds: bool | None = None
        elif not comparable:
            holds = None
        else:
            holds = not varies

        return {
            "datasets_compared": len(directories),
            "datasets": [directory.name for directory in directories],
            "holds": holds,
            "families_compared": comparable,
            "families_that_vary": varies,
            "families_not_comparable": sorted(missing),
            "not_comparable_reasons": {
                family: "; ".join(sorted(reasons))
                for family, reasons in sorted(missing.items())
            },
            "note": (
                "Compared on business content across every dataset under the "
                "review root, ignoring surrogate identifiers, which are "
                "content-derived and differ per run by design. This describes "
                "only the datasets named here, not every scenario the "
                "generator can produce. Fewer than two datasets, or a family "
                "absent from any of them, leaves the question untested rather "
                "than answered."
            ),
        }

    def outcomes(self, *, run_id: str | None = None) -> dict[str, Any]:
        """Summarize dispositions and outcomes for the datasets under the root.

        When the root holds several generated scenarios this aggregates across
        them and names which ones were included, so the reviewer is never shown
        a distribution without knowing its population.
        """
        directories = self._run_directories() if run_id is None else [self._resolve(run_id)]
        dispositions: Counter[str] = Counter()
        outcomes: Counter[str] = Counter()
        fulfilment: Counter[str] = Counter()
        scenarios: list[str] = []
        fit_rejections = 0
        dispatch_total = 0
        for directory in directories:
            manifest = _read_manifest(directory)
            scenarios.append(str(manifest.get("scenario", directory.name)))
            for record in _read_jsonl(directory / "dispatch_context.jsonl"):
                dispatch_total += 1
                dispositions[str(record.get("disposition"))] += 1
                outcomes[str(record.get("outcome"))] += 1
                if record.get("van_stock_fit_rejected"):
                    fit_rejections += 1
            for record in _read_jsonl(directory / "part_demand.jsonl"):
                fulfilment[str(record.get("fulfilment_state"))] += 1
        return {
            "scenarios_included": sorted(scenarios),
            "scenario_count": len(scenarios),
            "dispatch_count": dispatch_total,
            "dispositions": dict(sorted(dispositions.items())),
            "outcomes": dict(sorted(outcomes.items())),
            "fulfilment_states": dict(sorted(fulfilment.items())),
            "van_fit_rejections": fit_rejections,
            "note": (
                "Distributions describe only the datasets present under the "
                "review root, not all 19 scenarios, unless all 19 were generated "
                "there."
            ),
        }


__all__ = [
    "HOSTED_FLAG",
    "HOSTED_MARKERS",
    "MAX_PAGE_SIZE",
    "PartsReviewDisabled",
    "PartsReviewError",
    "PartsReviewNotFound",
    "PartsReviewStore",
    "REVIEW_FLAG",
    "REVIEW_RECORD_TYPES",
    "gate_reason",
    "is_hosted",
    "review_enabled",
]
