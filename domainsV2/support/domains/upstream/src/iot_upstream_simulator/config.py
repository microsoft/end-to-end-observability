"""Strict configuration contract for the fictional upstream production simulator.

Everything validated here describes a fictional onshore production profile. The
contract is inspired by, not conformant with or wire-compatible with, WITSML,
PRODML, RESQML, OPC UA, or ISA-95. No operational, fiscal, safety, or
compliance use is supported.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SCHEMA_VERSION = "1.0"
MAX_DURATION_SECONDS = 6 * 3600
MAX_TICKS = 1440
INTERVAL_SECONDS_CHOICES = (10, 15, 30, 60, 120, 300)
FIDELITY_TIERS = ("F0", "F1", "F2", "F3", "FH")

#: The 19 approved Release 1 scenarios from the upstream research document.
SCENARIOS = (
    "normal_production",
    "deliverability_decline",
    "liquid_loading",
    "slugging",
    "flowline_restriction",
    "artificial_lift_degradation",
    "esp_trip_restart",
    "separator_upset",
    "heater_treater_weakness",
    "compressor_degradation",
    "produced_water_constraint",
    "tank_transfer_imbalance",
    "corrosion_erosion_context",
    "gas_release_observation",
    "utility_power_loss",
    "instrument_drift_stuck",
    "communications_degradation",
    "maintenance_inspection_workflow",
    "flare_vent_environment_workflow",
)
SCENARIO_SET = frozenset(SCENARIOS)

DEFAULT_FLEET = {
    "oil_well_pads": 1,
    "gas_well_pads": 1,
    "multiwell_pads": 1,
    "wells_per_multiwell_pad": 3,
}
DEFAULT_FIDELITY = {
    "measurement_effects": True,
    "delivery_effects": True,
    "sparse_workflows": True,
}
DEFAULT_MONITORING = {
    "max_records_per_type": 20000,
    "max_total_records": 120000,
}
FLEET_BOUNDS = {
    "oil_well_pads": (0, 4),
    "gas_well_pads": (0, 4),
    "multiwell_pads": (0, 3),
    "wells_per_multiwell_pad": (2, 6),
}
MAX_WELLS = 24

#: Real operator, service-company and basin/formation tokens are rejected so the
#: generated profile can never be mistaken for a reconstruction of a real field.
PROHIBITED_TOKENS = (
    "exxon",
    "chevron",
    "conocophillips",
    "occidental",
    "halliburton",
    "schlumberger",
    "baker hughes",
    "weatherford",
    "pioneer natural",
    "devon energy",
    "eog resources",
    "apache corp",
    "hess corp",
    "diamondback",
    "continental resources",
    "aramco",
    "petrobras",
    "equinor",
    "totalenergies",
    "bakken",
    "permian",
    "eagle ford",
    "wolfcamp",
    "marcellus",
    "niobrara",
    "haynesville",
    "utica",
)


class ConfigError(ValueError):
    """Raised when an upstream configuration violates its safe contract."""


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise ConfigError(message)


def _utc(value: Any, field: str) -> datetime:
    _expect(isinstance(value, str) and value.endswith("Z"), f"{field} must be UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ConfigError(f"{field} must be an ISO-8601 UTC timestamp") from exc
    _expect(parsed.microsecond == 0, f"{field} must not contain fractional seconds")
    return parsed


def _fictional_identifier(value: Any, field: str) -> None:
    _expect(
        isinstance(value, str)
        and re.fullmatch(r"fictional-[a-z0-9]+(?:-[a-z0-9]+)*", value) is not None,
        f"{field} must be a lowercase fictional- identifier",
    )


def _display_name(value: Any, field: str) -> None:
    _expect(isinstance(value, str) and value.strip(), f"{field} is required")
    folded = value.casefold()
    _expect(
        not any(token in folded for token in PROHIBITED_TOKENS),
        f"{field} must not identify a real operator, service company, or basin",
    )


def _bounded_int(container: dict[str, Any], key: str, low: int, high: int, label: str) -> None:
    value = container[key]
    _expect(
        isinstance(value, int) and not isinstance(value, bool) and low <= value <= high,
        f"{label}.{key} must be an integer between {low} and {high}",
    )


def validate_config(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize an upstream configuration.

    Returns a deep copy with defaults materialized so the normalized document is
    a stable, hashable input to the deterministic run identifier.
    """
    _expect(isinstance(raw, dict), "configuration root must be an object")
    raw = copy.deepcopy(raw)
    required = {"schema_version", "seed", "scenario", "site", "facility", "simulation"}
    optional = {"fleet", "fidelity", "monitoring"}
    _expect(required <= set(raw), f"missing keys: {sorted(required - set(raw))}")
    _expect(
        set(raw) <= required | optional,
        f"unknown keys: {sorted(set(raw) - required - optional)}",
    )
    _expect(raw["schema_version"] == SCHEMA_VERSION, "schema_version must be 1.0")
    _expect(
        isinstance(raw["seed"], int) and not isinstance(raw["seed"], bool) and 0 <= raw["seed"] <= 2**63 - 1,
        "seed must be a non-negative integer",
    )
    _expect(raw["scenario"] in SCENARIO_SET, f"scenario must be one of {sorted(SCENARIOS)}")

    site = raw["site"]
    _expect(
        isinstance(site, dict) and set(site) == {"site_id", "display_name", "time_zone"},
        "site contains unknown or missing keys",
    )
    _fictional_identifier(site["site_id"], "site.site_id")
    _display_name(site["display_name"], "site.display_name")
    try:
        ZoneInfo(site["time_zone"])
    except (TypeError, ZoneInfoNotFoundError) as exc:
        raise ConfigError("site.time_zone must be a valid IANA time zone") from exc

    facility = raw["facility"]
    _expect(
        isinstance(facility, dict) and set(facility) == {"facility_id", "display_name"},
        "facility contains unknown or missing keys",
    )
    _fictional_identifier(facility["facility_id"], "facility.facility_id")
    _display_name(facility["display_name"], "facility.display_name")

    simulation = raw["simulation"]
    _expect(
        isinstance(simulation, dict)
        and set(simulation) == {"start_utc", "end_utc", "interval_seconds"},
        "simulation contains unknown or missing keys",
    )
    interval = simulation["interval_seconds"]
    _expect(
        isinstance(interval, int)
        and not isinstance(interval, bool)
        and interval in INTERVAL_SECONDS_CHOICES,
        f"simulation.interval_seconds must be one of {list(INTERVAL_SECONDS_CHOICES)}",
    )
    start = _utc(simulation["start_utc"], "simulation.start_utc")
    end = _utc(simulation["end_utc"], "simulation.end_utc")
    _expect(end > start, "simulation.end_utc must be after start_utc")
    duration_seconds = int((end - start).total_seconds())
    _expect(
        duration_seconds <= MAX_DURATION_SECONDS,
        "simulation duration must not exceed six hours",
    )
    _expect(
        duration_seconds % interval == 0,
        "simulation duration must align to simulation.interval_seconds",
    )
    _expect(
        int(start.timestamp()) % interval == 0,
        "simulation.start_utc must align to simulation.interval_seconds",
    )
    _expect(
        duration_seconds // interval <= MAX_TICKS,
        f"simulation must not exceed {MAX_TICKS} ticks",
    )

    fleet = raw.setdefault("fleet", copy.deepcopy(DEFAULT_FLEET))
    _expect(
        isinstance(fleet, dict) and set(fleet) == set(DEFAULT_FLEET),
        "fleet contains unknown or missing keys",
    )
    for key, (low, high) in FLEET_BOUNDS.items():
        _bounded_int(fleet, key, low, high, "fleet")
    well_count = (
        fleet["oil_well_pads"]
        + fleet["gas_well_pads"]
        + fleet["multiwell_pads"] * fleet["wells_per_multiwell_pad"]
    )
    _expect(well_count >= 1, "fleet must describe at least one fictional well")
    _expect(well_count <= MAX_WELLS, f"fleet must not exceed {MAX_WELLS} fictional wells")

    fidelity = raw.setdefault("fidelity", copy.deepcopy(DEFAULT_FIDELITY))
    _expect(
        isinstance(fidelity, dict) and set(fidelity) == set(DEFAULT_FIDELITY),
        "fidelity contains unknown or missing keys",
    )
    for key in sorted(DEFAULT_FIDELITY):
        _expect(isinstance(fidelity[key], bool), f"fidelity.{key} must be a boolean")

    monitoring = raw.setdefault("monitoring", copy.deepcopy(DEFAULT_MONITORING))
    _expect(
        isinstance(monitoring, dict) and set(monitoring) == set(DEFAULT_MONITORING),
        "monitoring contains unknown or missing keys",
    )
    _bounded_int(monitoring, "max_records_per_type", 1, 200000, "monitoring")
    _bounded_int(monitoring, "max_total_records", 1, 2000000, "monitoring")

    return copy.deepcopy(raw)


def derive_scenario_id(run_id: str, scenario: str) -> str:
    """Content-derived scenario identifier; never random and never wall-clock."""
    digest = hashlib.sha256(f"{run_id}|{scenario}".encode("utf-8")).hexdigest()[:16]
    return f"scenario-{digest}"


def load_config(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"unable to load configuration: {exc}") from exc
    return validate_config(value)


__all__ = [
    "ConfigError",
    "FIDELITY_TIERS",
    "INTERVAL_SECONDS_CHOICES",
    "MAX_WELLS",
    "SCENARIOS",
    "SCENARIO_SET",
    "SCHEMA_VERSION",
    "derive_scenario_id",
    "load_config",
    "validate_config",
]
