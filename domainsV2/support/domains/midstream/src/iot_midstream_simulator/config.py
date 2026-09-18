"""Strict configuration contract for fictional liquid-midstream runs."""

from __future__ import annotations

import copy
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SCHEMA_VERSION = "1.0"
SCENARIOS = {
    "normal_transfer",
    "pump_degradation",
    "valve_restriction",
    "pipeline_withdrawal",
    "tank_sensor_drift",
    "communications_failure",
    "power_loss_shutdown",
    "batch_property_change",
}
DEFAULT_LOCALIZATION = {
    "enabled": True,
    "monitoring_node_count": 7,
    "hidden_release_route_fraction": 0.62,
    "pressure_measurement_uncertainty_kpa": 0.25,
    "flow_measurement_relative_uncertainty": 0.0015,
    "clock_offset_ms": 8.0,
    "clock_uncertainty_ms": 2.0,
    "confirmation_delay_seconds": 900,
}


class ConfigError(ValueError):
    """Raised when a midstream configuration violates its safe public contract."""


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise ConfigError(message)


def _number(mapping: dict[str, Any], key: str, low: float, high: float) -> float:
    value = mapping.get(key)
    _expect(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value)),
        f"{key} must be a finite number",
    )
    result = float(value)
    _expect(low <= result <= high, f"{key} must be between {low} and {high}")
    mapping[key] = result
    return result


def _utc(value: Any, field: str) -> datetime:
    _expect(isinstance(value, str) and value.endswith("Z"), f"{field} must be UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ConfigError(f"{field} must be an ISO-8601 UTC timestamp") from exc
    _expect(parsed.microsecond == 0, f"{field} must not contain fractional seconds")
    return parsed


def validate_config(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a configuration without accepting real OT details."""
    _expect(isinstance(raw, dict), "configuration root must be an object")
    required = {
        "schema_version",
        "seed",
        "scenario",
        "site",
        "simulation",
        "fluid",
        "pipeline",
        "tank",
        "pump",
    }
    _expect(required <= set(raw), f"missing keys: {sorted(required - set(raw))}")
    _expect(
        set(raw) <= required | {"output", "localization"},
        f"unknown keys: {sorted(set(raw) - required - {'output', 'localization'})}",
    )
    _expect(raw["schema_version"] == SCHEMA_VERSION, "schema_version must be 1.0")
    _expect(isinstance(raw["seed"], int) and not isinstance(raw["seed"], bool), "seed must be an integer")
    _expect(raw["scenario"] in SCENARIOS, f"scenario must be one of {sorted(SCENARIOS)}")

    site = raw["site"]
    _expect(isinstance(site, dict), "site must be an object")
    _expect(
        set(site) == {"site_id", "display_name", "time_zone", "ambient_temperature_c"},
        "site contains unknown or missing keys",
    )
    _expect(
        isinstance(site["site_id"], str)
        and re.fullmatch(r"fictional-[a-z0-9]+(?:-[a-z0-9]+)*", site["site_id"]) is not None,
        "site.site_id must be a lowercase fictional- identifier",
    )
    _expect(isinstance(site["display_name"], str) and site["display_name"].strip(), "site.display_name is required")
    _expect(
        not any(token in site["display_name"].casefold() for token in ("shell", "exxon", "chevron", "marathon", "enbridge")),
        "site.display_name must not identify a real operator",
    )
    try:
        ZoneInfo(site["time_zone"])
    except (TypeError, ZoneInfoNotFoundError) as exc:
        raise ConfigError("site.time_zone must be a valid IANA time zone") from exc
    _number(site, "ambient_temperature_c", -40.0, 60.0)

    simulation = raw["simulation"]
    _expect(
        isinstance(simulation, dict)
        and set(simulation) == {"start_utc", "end_utc", "interval_seconds"},
        "simulation contains unknown or missing keys",
    )
    start = _utc(simulation["start_utc"], "simulation.start_utc")
    end = _utc(simulation["end_utc"], "simulation.end_utc")
    _expect(end > start, "simulation.end_utc must be after start_utc")
    _expect((end - start).total_seconds() <= 7 * 86400, "simulation duration must not exceed seven days")
    interval = simulation["interval_seconds"]
    _expect(isinstance(interval, int) and not isinstance(interval, bool), "interval_seconds must be an integer")
    _expect(5 <= interval <= 3600, "interval_seconds must be between 5 and 3600")
    _expect((end - start).total_seconds() % interval == 0, "duration must align to interval_seconds")

    sections = {
        "fluid": {
            "density_kg_m3": (650.0, 1050.0),
            "viscosity_pa_s": (0.0001, 2.0),
            "temperature_c": (-20.0, 90.0),
        },
        "pipeline": {
            "length_m": (100.0, 200000.0),
            "diameter_m": (0.1, 1.5),
            "roughness_m": (0.000001, 0.01),
            "reference_pressure_kpa": (100.0, 10000.0),
            "effective_bulk_modulus_kpa": (10000.0, 5000000.0),
        },
        "tank": {
            "diameter_m": (5.0, 100.0),
            "height_m": (3.0, 30.0),
            "initial_volume_m3": (1.0, 200000.0),
        },
        "pump": {
            "rated_flow_m3_s": (0.01, 5.0),
            "rated_head_m": (5.0, 500.0),
            "rated_speed_rpm": (100.0, 10000.0),
            "efficiency": (0.2, 0.95),
            "thermal_time_constant_s": (30.0, 86400.0),
            "coast_down_time_constant_s": (1.0, 600.0),
        },
    }
    for section_name, fields in sections.items():
        section = raw[section_name]
        _expect(isinstance(section, dict) and set(section) == set(fields), f"{section_name} contains unknown or missing keys")
        for key, bounds in fields.items():
            _number(section, key, *bounds)

    radius = raw["tank"]["diameter_m"] / 2.0
    capacity = math.pi * radius * radius * raw["tank"]["height_m"]
    _expect(raw["tank"]["initial_volume_m3"] < capacity * 0.95, "tank initial volume must be below 95% capacity")
    output = raw.setdefault("output", {"include_csv": True})
    _expect(isinstance(output, dict) and set(output) == {"include_csv"}, "output supports only include_csv")
    _expect(isinstance(output["include_csv"], bool), "output.include_csv must be boolean")

    localization = raw.setdefault("localization", copy.deepcopy(DEFAULT_LOCALIZATION))
    _expect(
        isinstance(localization, dict)
        and set(localization) == set(DEFAULT_LOCALIZATION),
        "localization contains unknown or missing keys",
    )
    _expect(
        isinstance(localization["enabled"], bool),
        "localization.enabled must be boolean",
    )
    node_count = localization["monitoring_node_count"]
    _expect(
        isinstance(node_count, int)
        and not isinstance(node_count, bool)
        and 3 <= node_count <= 21,
        "localization.monitoring_node_count must be an integer between 3 and 21",
    )
    _number(localization, "hidden_release_route_fraction", 0.05, 0.95)
    _number(localization, "pressure_measurement_uncertainty_kpa", 0.01, 20.0)
    _number(localization, "flow_measurement_relative_uncertainty", 0.00001, 0.05)
    _number(localization, "clock_offset_ms", -1000.0, 1000.0)
    _number(localization, "clock_uncertainty_ms", 0.0, 1000.0)
    delay = localization["confirmation_delay_seconds"]
    _expect(
        isinstance(delay, int)
        and not isinstance(delay, bool)
        and 60 <= delay <= 86400,
        "localization.confirmation_delay_seconds must be an integer between 60 and 86400",
    )
    return copy.deepcopy(raw)


def load_config(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"unable to load configuration: {exc}") from exc
    return validate_config(value)
