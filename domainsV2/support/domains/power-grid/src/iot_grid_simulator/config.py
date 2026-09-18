"""Configuration loading and validation without third-party dependencies."""

from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .incident import IncidentError, validate_resolved_incident

SCHEMA_VERSION = "1.0"
SCENARIOS = {
    "normal",
    "heatwave",
    "storm_outage",
    "transformer_degradation",
    "communications_failure",
}
CLIMATE_FIELDS = {
    "temperature_mean_c",
    "temperature_seasonal_amplitude_c",
    "temperature_peak_day_of_year",
    "temperature_daily_amplitude_c",
    "humidity_mean_pct",
    "wind_mean_mps",
    "solar_peak_w_m2",
    "precipitation_probability",
    "load_temperature_base_c",
    "load_temperature_coefficient_per_c",
}
AUSTIN_CLIMATE = {
    "temperature_mean_c": 20.5,
    "temperature_seasonal_amplitude_c": 9.5,
    "temperature_peak_day_of_year": 205.0,
    "temperature_daily_amplitude_c": 7.0,
    "humidity_mean_pct": 62.0,
    "wind_mean_mps": 3.2,
    "solar_peak_w_m2": 950.0,
    "precipitation_probability": 0.08,
    "load_temperature_base_c": 22.0,
    "load_temperature_coefficient_per_c": 0.035,
}
NEW_YORK_CITY_CLIMATE = {
    "temperature_mean_c": 12.9,
    "temperature_seasonal_amplitude_c": 11.9,
    "temperature_peak_day_of_year": 204.0,
    "temperature_daily_amplitude_c": 5.5,
    "humidity_mean_pct": 63.0,
    "wind_mean_mps": 4.0,
    "solar_peak_w_m2": 900.0,
    "precipitation_probability": 0.12,
    "load_temperature_base_c": 18.3,
    "load_temperature_coefficient_per_c": 0.03,
}
NYC_BOROUGHS = (
    "Bronx",
    "Brooklyn",
    "Manhattan",
    "Queens",
    "Staten Island",
)
LOCATION_PRESETS = {
    "austin_tx": {
        "city": "austin",
        "state": "TX",
        "climate": AUSTIN_CLIMATE,
    },
    "new_york_city_ny": {
        "city": "new york city",
        "state": "NY",
        "climate": NEW_YORK_CITY_CLIMATE,
        "areas": NYC_BOROUGHS,
    },
}


class ConfigError(ValueError):
    """Raised when a simulator configuration violates the public contract."""


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise ConfigError(message)


def _utc_datetime(value: Any, field: str) -> datetime:
    _expect(isinstance(value, str), f"{field} must be an ISO-8601 UTC string")
    _expect(value.endswith("Z"), f"{field} must end with Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ConfigError(f"{field} is not a valid timestamp") from exc
    _expect(parsed.second == 0 and parsed.microsecond == 0, f"{field} must align to a minute")
    return parsed


def _number(mapping: dict[str, Any], key: str, low: float, high: float) -> None:
    value = mapping.get(key)
    _expect(
        isinstance(value, (int, float)) and not isinstance(value, bool),
        f"{key} must be numeric",
    )
    _expect(low <= float(value) <= high, f"{key} must be between {low} and {high}")


def validate_config(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a configuration into a canonical dictionary."""
    _expect(isinstance(raw, dict), "configuration root must be an object")
    required = {"schema_version", "seed", "scenario", "location", "simulation", "fleet"}
    allowed = required | {"output", "environment"}
    _expect(required <= raw.keys(), f"missing configuration keys: {sorted(required - raw.keys())}")
    _expect(set(raw) <= allowed, f"unknown configuration keys: {sorted(set(raw) - allowed)}")
    _expect(raw["schema_version"] == SCHEMA_VERSION, "schema_version must be 1.0")
    _expect(isinstance(raw["seed"], int) and not isinstance(raw["seed"], bool), "seed must be an integer")
    _expect(raw["scenario"] in SCENARIOS, f"scenario must be one of {sorted(SCENARIOS)}")

    location = raw["location"]
    _expect(isinstance(location, dict), "location must be an object")
    location_allowed = {
        "city",
        "state",
        "time_zone",
        "latitude",
        "longitude",
        "preset",
        "climate",
        "areas",
    }
    _expect(set(location) <= location_allowed, "location contains unknown keys")
    for key in ("city", "state", "time_zone", "latitude", "longitude"):
        _expect(key in location, f"location.{key} is required")
    _expect(isinstance(location["city"], str) and location["city"].strip(), "location.city is required")
    _expect(
        isinstance(location["state"], str) and len(location["state"].strip()) == 2,
        "location.state must be a two-letter descriptive code",
    )
    try:
        ZoneInfo(location["time_zone"])
    except (ZoneInfoNotFoundError, TypeError) as exc:
        raise ConfigError("location.time_zone must be a valid IANA time zone") from exc
    _number(location, "latitude", -90.0, 90.0)
    _number(location, "longitude", -180.0, 180.0)

    preset = location.get("preset")
    supplied_climate = location.get("climate")
    if preset is not None:
        _expect(preset in LOCATION_PRESETS, "unsupported implicit climate preset")
        preset_definition = LOCATION_PRESETS[preset]
        _expect(
            location["city"].casefold() == preset_definition["city"]
            and location["state"].upper() == preset_definition["state"],
            f"{preset} preset requires its documented city and state",
        )
        if supplied_climate is None:
            location["climate"] = copy.deepcopy(preset_definition["climate"])
        else:
            _expect(isinstance(supplied_climate, dict), "location.climate must be an object")
            _expect(CLIMATE_FIELDS <= supplied_climate.keys(), "all explicit climate coefficients are required")
        if "areas" in preset_definition and "areas" not in location:
            location["areas"] = list(preset_definition["areas"])
    else:
        _expect(
            isinstance(supplied_climate, dict) and CLIMATE_FIELDS <= supplied_climate.keys(),
            "locations without a built-in preset require all explicit climate coefficients",
        )

    areas = location.get("areas")
    if areas is not None:
        _expect(isinstance(areas, list), "location.areas must be an array")
        _expect(1 <= len(areas) <= 8, "location.areas must contain between 1 and 8 names")
        _expect(
            all(isinstance(area, str) and area.strip() for area in areas),
            "each location area must be a non-empty string",
        )
        normalized_areas = [area.strip() for area in areas]
        _expect(
            len({area.casefold() for area in normalized_areas}) == len(normalized_areas),
            "location area names must be unique",
        )
        location["areas"] = normalized_areas
        if preset == "new_york_city_ny":
            _expect(
                tuple(normalized_areas) == NYC_BOROUGHS,
                "new_york_city_ny areas are fixed to the five NYC boroughs",
            )

    sensor_fault_windows: list[tuple[datetime, datetime]] = []
    environment = raw.get("environment")
    if environment is not None:
        _expect(isinstance(environment, dict), "environment must be an object")
        _expect(
            bool(environment)
            and set(environment) <= {"storm", "incident", "sensor_faults"},
            "environment supports only storm, incident, and sensor_faults",
        )
        area_names = set(location.get("areas", []))
        storm = environment.get("storm")
        if storm is not None:
            _expect(
                isinstance(storm, dict),
                "environment.storm must be an object",
            )
            storm_fields = {
                "storm_id",
                "revision",
                "issued_at_utc",
                "status",
                "is_synthetic",
                "public_safety",
                "route",
            }
            _expect(
                set(storm) == storm_fields,
                "environment.storm contains unknown or missing keys",
            )
            _expect(
                isinstance(storm["storm_id"], str)
                and storm["storm_id"].startswith("synthetic-"),
                "storm_id must start with synthetic-",
            )
            _expect(
                isinstance(storm["revision"], int)
                and not isinstance(storm["revision"], bool)
                and storm["revision"] >= 1,
                "storm revision must be a positive integer",
            )
            _utc_datetime(
                storm["issued_at_utc"],
                "environment.storm.issued_at_utc",
            )
            _expect(
                storm["status"] in {"Test", "Exercise"},
                "storm status must be Test or Exercise",
            )
            _expect(
                storm["is_synthetic"] is True,
                "storm is_synthetic must be true",
            )
            _expect(
                storm["public_safety"] is False,
                "storm public_safety must be false",
            )
            route = storm["route"]
            _expect(
                isinstance(route, list) and route,
                "storm route must be a non-empty array",
            )
            _expect(
                len(route) <= 100,
                "storm route must contain at most 100 points",
            )
            previous_sequence = 0
            previous_arrival: datetime | None = None
            for index, point in enumerate(route):
                _expect(
                    isinstance(point, dict),
                    f"storm route point {index} must be an object",
                )
                point_fields = {
                    "sequence",
                    "area",
                    "arrival_utc",
                    "departure_utc",
                    "wind_speed_mps",
                    "precipitation_mm",
                    "flood_state",
                    "certainty",
                }
                _expect(
                    set(point) == point_fields,
                    f"storm route point {index} contains unknown or missing keys",
                )
                sequence = point["sequence"]
                _expect(
                    isinstance(sequence, int)
                    and not isinstance(sequence, bool)
                    and sequence == previous_sequence + 1,
                    "storm route sequence must start at 1 and be contiguous",
                )
                previous_sequence = sequence
                _expect(
                    isinstance(point["area"], str)
                    and point["area"] in area_names,
                    "storm route area must match a configured location area",
                )
                arrival = _utc_datetime(
                    point["arrival_utc"],
                    f"environment.storm.route[{index}].arrival_utc",
                )
                departure = _utc_datetime(
                    point["departure_utc"],
                    f"environment.storm.route[{index}].departure_utc",
                )
                _expect(
                    departure > arrival,
                    "storm route departure must follow arrival",
                )
                _expect(
                    previous_arrival is None or arrival >= previous_arrival,
                    "storm route arrivals must be chronological",
                )
                previous_arrival = arrival
                _number(point, "wind_speed_mps", 0.0, 75.0)
                _number(point, "precipitation_mm", 0.0, 100.0)
                _expect(
                    point["flood_state"] in {"none", "watch", "warning"},
                    "storm flood_state must be none, watch, or warning",
                )
                _expect(
                    point["certainty"] in {"Possible", "Likely"},
                    "storm certainty must be Possible or Likely",
                )

        incident = environment.get("incident")
        if incident is not None:
            try:
                validate_resolved_incident(incident, area_names)
            except IncidentError as exc:
                raise ConfigError(str(exc)) from exc

        sensor_faults = environment.get("sensor_faults")
        if sensor_faults is not None:
            _expect(
                isinstance(sensor_faults, list) and sensor_faults,
                "environment.sensor_faults must be a non-empty array",
            )
            _expect(
                len(sensor_faults) <= 100,
                "environment.sensor_faults must contain at most 100 faults",
            )
            fault_ids: set[str] = set()
            for index, fault in enumerate(sensor_faults):
                _expect(
                    isinstance(fault, dict),
                    f"environment.sensor_faults[{index}] must be an object",
                )
                fault_fields = {
                    "fault_id",
                    "asset_id",
                    "measurement",
                    "fault_type",
                    "start_utc",
                    "end_utc",
                    "magnitude",
                }
                _expect(
                    set(fault) == fault_fields,
                    f"environment.sensor_faults[{index}] contains unknown or missing keys",
                )
                fault_id = fault["fault_id"]
                _expect(
                    isinstance(fault_id, str)
                    and fault_id.startswith("synthetic-"),
                    "sensor fault_id must start with synthetic-",
                )
                _expect(
                    fault_id not in fault_ids,
                    "sensor fault_id values must be unique",
                )
                fault_ids.add(fault_id)
                _expect(
                    isinstance(fault["asset_id"], str)
                    and fault["asset_id"].startswith("asset-"),
                    "sensor fault asset_id must start with asset-",
                )
                _expect(
                    isinstance(fault["measurement"], str)
                    and fault["measurement"].strip(),
                    "sensor fault measurement must be a non-empty string",
                )
                _expect(
                    fault["fault_type"] in {"bias", "drift", "stuck", "dropout"},
                    "sensor fault_type must be bias, drift, stuck, or dropout",
                )
                fault_start = _utc_datetime(
                    fault["start_utc"],
                    f"environment.sensor_faults[{index}].start_utc",
                )
                fault_end = _utc_datetime(
                    fault["end_utc"],
                    f"environment.sensor_faults[{index}].end_utc",
                )
                _expect(
                    fault_end > fault_start,
                    "sensor fault end_utc must follow start_utc",
                )
                sensor_fault_windows.append((fault_start, fault_end))
                _number(fault, "magnitude", -1000.0, 1000.0)
                magnitude = float(fault["magnitude"])
                if fault["fault_type"] == "dropout":
                    _expect(
                        magnitude == 0.0,
                        "dropout sensor fault magnitude must be zero",
                    )
                else:
                    _expect(
                        magnitude != 0.0,
                        "bias, drift, and stuck sensor fault magnitude must be non-zero",
                    )

    climate = location["climate"]
    bounds = {
        "temperature_mean_c": (-50.0, 60.0),
        "temperature_seasonal_amplitude_c": (0.0, 40.0),
        "temperature_peak_day_of_year": (1.0, 366.0),
        "temperature_daily_amplitude_c": (0.0, 30.0),
        "humidity_mean_pct": (0.0, 100.0),
        "wind_mean_mps": (0.0, 75.0),
        "solar_peak_w_m2": (0.0, 1500.0),
        "precipitation_probability": (0.0, 1.0),
        "load_temperature_base_c": (-20.0, 40.0),
        "load_temperature_coefficient_per_c": (0.0, 0.25),
    }
    _expect(set(climate) == CLIMATE_FIELDS, "location.climate contains unknown or missing coefficients")
    for key, (low, high) in bounds.items():
        _number(climate, key, low, high)
    if preset is not None:
        _expect(
            all(
                float(climate[key]) == expected
                for key, expected in LOCATION_PRESETS[preset]["climate"].items()
            ),
            f"{preset} climate coefficients are fixed by the synthetic preset",
        )

    simulation = raw["simulation"]
    _expect(isinstance(simulation, dict), "simulation must be an object")
    _expect(
        set(simulation) <= {"start_utc", "end_utc", "interval_minutes"},
        "simulation contains unknown keys",
    )
    _expect(
        {"start_utc", "end_utc", "interval_minutes"} <= simulation.keys(),
        "simulation requires start_utc, end_utc, and interval_minutes",
    )
    start = _utc_datetime(simulation["start_utc"], "simulation.start_utc")
    end = _utc_datetime(simulation["end_utc"], "simulation.end_utc")
    interval = simulation["interval_minutes"]
    _expect(isinstance(interval, int) and not isinstance(interval, bool), "interval_minutes must be an integer")
    _expect(1 <= interval <= 60, "interval_minutes must be between 1 and 60")
    seconds = int((end - start).total_seconds())
    _expect(seconds > 0, "simulation.end_utc must follow start_utc")
    _expect(seconds <= 31 * 86400, "simulation duration must not exceed 31 days")
    _expect(seconds % (interval * 60) == 0, "simulation range must be divisible by interval_minutes")
    for fault_start, fault_end in sensor_fault_windows:
        _expect(
            fault_start < end and start < fault_end,
            "sensor fault interval must overlap the simulation range",
        )
    interval_count = seconds // (interval * 60)
    if raw["scenario"] != "normal":
        _expect(
            interval_count >= 4,
            "non-normal scenarios require at least four telemetry intervals",
        )

    fleet = raw["fleet"]
    _expect(isinstance(fleet, dict), "fleet must be an object")
    fleet_bounds = {
        "feeders": (1, 8),
        "distribution_transformers_per_feeder": (1, 50),
        "ami_meters_per_transformer": (1, 100),
        "reclosers_per_feeder": (1, 4),
        "regulators_per_feeder": (1, 4),
        "capacitor_banks_per_feeder": (1, 8),
        "power_quality_monitors_per_feeder": (1, 8),
    }
    _expect(
        set(fleet) == set(fleet_bounds) | {"pv_fraction", "evse_fraction"},
        "fleet contains unknown or missing keys",
    )
    for key, (low, high) in fleet_bounds.items():
        value = fleet.get(key)
        _expect(isinstance(value, int) and not isinstance(value, bool), f"fleet.{key} must be an integer")
        _expect(low <= value <= high, f"fleet.{key} must be between {low} and {high}")
    for key in ("pv_fraction", "evse_fraction"):
        value = fleet.get(key)
        _expect(isinstance(value, (int, float)) and not isinstance(value, bool), f"fleet.{key} must be numeric")
        _expect(0.0 <= float(value) <= 1.0, f"fleet.{key} must be between 0 and 1")
    if areas is not None:
        _expect(
            fleet["feeders"] >= len(areas),
            "fleet.feeders must be at least the number of location areas",
        )

    output = raw.get("output", {})
    _expect(isinstance(output, dict), "output must be an object")
    _expect(set(output) <= {"include_reference_catalog"}, "output contains unknown keys")
    include_catalog = output.get("include_reference_catalog", False)
    _expect(isinstance(include_catalog, bool), "output.include_reference_catalog must be boolean")

    normalized = copy.deepcopy(raw)
    normalized["location"]["state"] = normalized["location"]["state"].upper()
    normalized["location"]["city"] = normalized["location"]["city"].strip()
    normalized["location"]["latitude"] = float(normalized["location"]["latitude"])
    normalized["location"]["longitude"] = float(normalized["location"]["longitude"])
    for key in CLIMATE_FIELDS:
        normalized["location"]["climate"][key] = float(normalized["location"]["climate"][key])
    normalized["fleet"]["pv_fraction"] = float(normalized["fleet"]["pv_fraction"])
    normalized["fleet"]["evse_fraction"] = float(normalized["fleet"]["evse_fraction"])
    normalized["output"] = {"include_reference_catalog": include_catalog}
    if environment is not None and "storm" in normalized["environment"]:
        for point in normalized["environment"]["storm"]["route"]:
            point["wind_speed_mps"] = float(point["wind_speed_mps"])
            point["precipitation_mm"] = float(point["precipitation_mm"])
    if environment is not None and "sensor_faults" in normalized["environment"]:
        for fault in normalized["environment"]["sensor_faults"]:
            fault["measurement"] = fault["measurement"].strip()
            fault["magnitude"] = float(fault["magnitude"])
    return normalized


def load_config(path: str | Path) -> dict[str, Any]:
    """Load, validate, and normalize JSON configuration."""
    try:
        with Path(path).open("r", encoding="utf-8") as stream:
            raw = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"unable to load configuration: {exc}") from exc
    return validate_config(raw)
