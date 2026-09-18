"""Serializable long-memory state for continuous physical models."""

from __future__ import annotations

from typing import Any

MODEL_STATE_SCHEMA_VERSION = "1.0"
TRANSFORMER_TYPES = {
    "substation_transformer_ltc",
    "distribution_transformer",
}


class ModelStateError(ValueError):
    """Raised when persisted simulator model state is invalid."""


def initial_model_state(assets: list[dict[str, Any]]) -> dict[str, Any]:
    """Create deterministic initial state for all stateful assets."""
    return {
        "schema_version": MODEL_STATE_SCHEMA_VERSION,
        "as_of_utc": None,
        "thermal_rise_c": {
            asset["asset_id"]: 0.5
            for asset in assets
            if asset["asset_type"] in TRANSFORMER_TYPES
        },
    }


def normalize_model_state(
    value: dict[str, Any] | None,
    assets: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate persisted state and fill state for newly introduced assets."""
    initial = initial_model_state(assets)
    if value is None:
        return initial
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "as_of_utc",
        "thermal_rise_c",
    }:
        raise ModelStateError("model state contains unknown or missing keys")
    if value["schema_version"] != MODEL_STATE_SCHEMA_VERSION:
        raise ModelStateError(
            f"model state schema_version must be {MODEL_STATE_SCHEMA_VERSION}"
        )
    if value["as_of_utc"] is not None and not isinstance(value["as_of_utc"], str):
        raise ModelStateError("model state as_of_utc must be a string or null")
    thermal = value["thermal_rise_c"]
    if not isinstance(thermal, dict):
        raise ModelStateError("model state thermal_rise_c must be an object")
    expected_ids = set(initial["thermal_rise_c"])
    unknown_ids = set(thermal) - expected_ids
    if unknown_ids:
        raise ModelStateError(
            f"model state contains unknown transformer IDs: {sorted(unknown_ids)}"
        )
    normalized_thermal = dict(initial["thermal_rise_c"])
    for asset_id, rise in thermal.items():
        if (
            not isinstance(rise, (int, float))
            or isinstance(rise, bool)
            or not 0.0 <= float(rise) <= 200.0
        ):
            raise ModelStateError(
                f"model state thermal rise for {asset_id} must be between 0 and 200 C"
            )
        normalized_thermal[asset_id] = float(rise)
    return {
        "schema_version": MODEL_STATE_SCHEMA_VERSION,
        "as_of_utc": value["as_of_utc"],
        "thermal_rise_c": normalized_thermal,
    }
