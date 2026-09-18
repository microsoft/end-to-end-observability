"""Backward-compatible midstream adapter for the shared synthetic overlay."""

from typing import Any

from inventory_movement import (
    SCENARIO_ID,
    THRESHOLD,
    InventoryMovementError,
    build_inventory_movement as _build_inventory_movement,
)

from .review import MidstreamInventoryError


def build_inventory_movement(inventory: dict[str, Any]) -> dict[str, Any]:
    """Retain the midstream error contract while sharing the pure engine."""
    try:
        return _build_inventory_movement(inventory)
    except InventoryMovementError as error:
        raise MidstreamInventoryError(str(error)) from error
