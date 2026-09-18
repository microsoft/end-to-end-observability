"""Stateless, portal-only inventory movement and alert demonstration.

The opening ledger remains authoritative and untouched. These synthetic frames
are not transactions, operational alerts, or inputs to any publication sink.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any


class InventoryMovementError(RuntimeError):
    """Raised when an inventory cannot support a truthful movement overlay."""


SCENARIO_ID = "depletion_restock_v1"
REPLENISHMENT_SCENARIO_ID = "replenishment_depletion_v1"
THRESHOLD = 10
_LABELS = (
    "Opening stock",
    "Issue to 12",
    "Issue to threshold",
    "Below threshold",
    "Out of stock",
    "Replenished",
)
_REPLENISHMENT_LABELS = (
    "Opening stock",
    "Synthetic receipt to 12",
    "Issue to threshold",
    "Below threshold",
    "Out of stock",
    "Synthetic replenishment to 12",
)


def _text(row: dict[str, Any], field: str, context: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise InventoryMovementError(f"{context} has missing or invalid {field}")
    return value


def _rows(inventory: dict[str, Any], field: str) -> list[dict[str, Any]]:
    value = inventory.get(field)
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise InventoryMovementError(f"inventory {field} must be a list of objects")
    return value


def _catalog(
    rows: list[dict[str, Any]],
    identity: str,
    fields: tuple[str, ...],
    unique_fields: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    human_keys: dict[str, set[str]] = {field: set() for field in unique_fields}
    for row in rows:
        key = _text(row, identity, "inventory catalog")
        for field in fields:
            _text(row, field, key)
        if key in result or any(row[field] in human_keys[field] for field in unique_fields):
            raise InventoryMovementError(f"duplicate inventory identity: {key}")
        result[key] = row
        for field in unique_fields:
            human_keys[field].add(row[field])
    return result


def build_inventory_movement(
    inventory: dict[str, Any], *, scenario_id: str = SCENARIO_ID
) -> dict[str, Any]:
    """Build repeatable frames from a baseline without mutating it.

    Malformed identities and quantities fail explicitly, rather than presenting
    unknown stock as healthy or zero. A valid but unavailable opening position
    still permits baseline viewing, with no playable scenario.

    The explicit replenishment policy accepts existing positions below 12,
    preserving their actual opening before synthetic adjustment and depletion.
    Neither policy fabricates a position or changes the source inventory.
    """
    if scenario_id not in (SCENARIO_ID, REPLENISHMENT_SCENARIO_ID):
        raise InventoryMovementError(f"unsupported inventory movement scenario: {scenario_id!r}")
    replenish = scenario_id == REPLENISHMENT_SCENARIO_ID
    if not isinstance(inventory, dict):
        raise InventoryMovementError("inventory must be an object")
    source = _text(inventory, "source_dataset", "inventory")
    if not isinstance(inventory.get("stock_is_known"), bool):
        raise InventoryMovementError("inventory stock_is_known must be a boolean")
    parts = _catalog(
        _rows(inventory, "parts"),
        "part_master_id",
        ("part_key", "stock_code", "part_name", "uom", "tracking_mode"),
        ("part_key", "stock_code"),
    )
    locations = _catalog(
        _rows(inventory, "locations"),
        "location_id",
        ("location_code", "location_type"),
        ("location_code",),
    )
    stock = deepcopy(_rows(inventory, "stock"))
    seen: set[tuple[str, str]] = set()
    for row in stock:
        part_id = _text(row, "part_master_id", "stock")
        location_id = _text(row, "location_id", "stock")
        key = (part_id, location_id)
        if key in seen:
            raise InventoryMovementError(f"duplicate stock position: {key}")
        seen.add(key)
        if part_id not in parts or location_id not in locations:
            raise InventoryMovementError(f"unknown stock reference: {key}")
        for field in ("location_code", "location_type"):
            if _text(row, field, "stock") != locations[location_id][field]:
                raise InventoryMovementError(f"stock {field} disagrees with {location_id}")
        quantity = row.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
            raise InventoryMovementError(
                f"stock quantity for {key} is unknown or invalid; "
                "expected a nonnegative integer"
            )

    def context(row: dict[str, Any]) -> dict[str, Any]:
        part = parts[row["part_master_id"]]
        return {
            "part_master_id": row["part_master_id"],
            "location_id": row["location_id"],
            "part_name": part["part_name"],
            "location_code": row["location_code"],
            "uom": part["uom"],
        }

    def identifier(prefix: str, row: dict[str, Any], frame: int) -> str:
        # Preserve the original compact UTF-8 JSON and 16-hex SHA-256 IDs.
        components = json.dumps(
            [scenario_id, source, row["part_master_id"], row["location_id"], frame],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(components.encode("utf-8")).hexdigest()[:16]
        return f"{prefix}-{digest}"

    eligible = sorted(
        (row for row in stock if replenish or row["quantity"] >= 12),
        key=lambda row: (
            parts[row["part_master_id"]]["tracking_mode"] == "serialized",
            parts[row["part_master_id"]]["part_key"],
            parts[row["part_master_id"]]["stock_code"],
            row["location_code"],
            row["part_master_id"],
            row["location_id"],
        ),
    )
    focus = eligible[:3] if inventory["stock_is_known"] else []
    opening = {
        (row["part_master_id"], row["location_id"]): row["quantity"]
        for row in focus
    }
    unavailable_reason = None
    if not inventory["stock_is_known"]:
        unavailable_reason = "Opening stock is unknown; movement playback is unavailable."
    elif not focus:
        unavailable_reason = (
            "No stocked position is available for synthetic replenishment."
            if replenish else "No stocked position has an opening quantity of at least 12."
        )
    result: dict[str, Any] = {
        "scenario_id": scenario_id,
        "title": "Replenishment and depletion" if replenish else "Depletion and replenishment",
        "threshold": THRESHOLD,
        "is_synthetic": True,
        "operational_use": "prohibited",
        "azure_connected": False,
        "available": bool(focus),
        "unavailable_reason": unavailable_reason,
        "focus": [context(row) for row in focus],
        "frames": [],
    }
    active: dict[tuple[str, str], dict[str, Any]] = {}
    for index in range(6 if focus else 1):
        movements = []
        transitions = []
        for row in stock:
            key = (row["part_master_id"], row["location_id"])
            before = row["quantity"]
            if index and key in opening:
                after = (12, 10, 9, 0, 12 if replenish else opening[key])[index - 1]
                if after != before:
                    movements.append({
                        "movement_id": identifier("inventory-movement", row, index),
                        **context(row),
                        "quantity_before": before,
                        "delta": after - before,
                        "quantity_after": after,
                        "kind": "receipt" if after > before else "issue",
                        "reason": (
                            ("Synthetic replenishment to 12"
                             if replenish else "Synthetic replenishment to opening stock")
                            if after > before
                            else "Synthetic consumption for depletion demonstration"
                        ),
                    })
                    row["quantity"] = after
            if not inventory["stock_is_known"]:
                continue
            quantity = row["quantity"]
            previous = active.get(key)
            low = quantity < THRESHOLD
            if not low and previous is None:
                continue
            part = parts[row["part_master_id"]]
            alert = {
                **context(row),
                "alert_id": (
                    previous["alert_id"] if previous else identifier("inventory-alert", row, index)
                ),
                "state": "fired" if low else "resolved",
                "status": "out_of_stock" if quantity == 0 else "low" if low else "healthy",
                "part_key": part["part_key"],
                "stock_code": part["stock_code"],
                "location_type": row["location_type"],
                "quantity": quantity,
                "threshold": THRESHOLD,
                "frame_index": index,
                "elapsed_minutes": index * 5,
                "source_dataset": source,
                "reason": (
                    "replenished" if not low
                    else previous["reason"] if previous
                    else "initial_low" if index == 0
                    else "threshold_crossed"
                ),
                "is_synthetic": True,
                "operational_use": "prohibited",
            }
            if low:
                active[key] = alert
                if previous is None:
                    transitions.append(alert)
            else:
                del active[key]
                transitions.append(alert)
        result["frames"].append({
            "index": index,
            "elapsed_minutes": index * 5,
            "label": (
                "Synthetic stock adjustment to 12"
                if replenish and index == 1 and any(quantity >= 12 for quantity in opening.values())
                else (_REPLENISHMENT_LABELS if replenish else _LABELS)[index]
            ),
            "stock": deepcopy(stock),
            "movements": movements,
            "alerts": deepcopy(transitions),
            "active_alerts": deepcopy(list(active.values())),
        })
    return result
