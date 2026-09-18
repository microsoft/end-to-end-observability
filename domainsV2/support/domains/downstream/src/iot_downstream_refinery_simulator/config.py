"""Strict configuration contract for the fictional downstream refinery simulator."""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SCHEMA_VERSION = "1.0"
BASE_TICK_SECONDS = 5
MAX_DURATION_SECONDS = 3600
SCENARIOS = {
    "normal_run",
    "receipt_distillation_shift",
    "conversion_treating_degradation",
    "blending_loading_shift",
    "utility_sag",
    "environmental_workflow",
    "analyzer_delay",
    "communications_degradation",
}
DEFAULT_MONITORING = {
    "max_records_per_type": 1000,
    "max_total_records": 10000,
}
OPERATOR_TOKENS = (
    "shell",
    "exxon",
    "chevron",
    "marathon",
    "enbridge",
    "bp",
    "valero",
    "phillips 66",
)


class ConfigError(ValueError):
    """Raised when a downstream refinery configuration violates its safe contract."""



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



def validate_config(raw: dict[str, Any]) -> dict[str, Any]:
    _expect(isinstance(raw, dict), "configuration root must be an object")
    required = {"schema_version", "seed", "scenario", "site", "simulation"}
    optional = {"monitoring"}
    _expect(required <= set(raw), f"missing keys: {sorted(required - set(raw))}")
    _expect(set(raw) <= required | optional, f"unknown keys: {sorted(set(raw) - required - optional)}")
    _expect(raw["schema_version"] == SCHEMA_VERSION, "schema_version must be 1.0")
    _expect(isinstance(raw["seed"], int) and not isinstance(raw["seed"], bool), "seed must be an integer")
    _expect(raw["scenario"] in SCENARIOS, f"scenario must be one of {sorted(SCENARIOS)}")

    site = raw["site"]
    _expect(
        isinstance(site, dict) and set(site) == {"site_id", "display_name", "time_zone"},
        "site contains unknown or missing keys",
    )
    _expect(
        isinstance(site["site_id"], str)
        and re.fullmatch(r"fictional-[a-z0-9]+(?:-[a-z0-9]+)*", site["site_id"]) is not None,
        "site.site_id must be a lowercase fictional- identifier",
    )
    _expect(
        isinstance(site["display_name"], str) and site["display_name"].strip(),
        "site.display_name is required",
    )
    _expect(
        not any(token in site["display_name"].casefold() for token in OPERATOR_TOKENS),
        "site.display_name must not identify a real operator",
    )
    try:
        ZoneInfo(site["time_zone"])
    except (TypeError, ZoneInfoNotFoundError) as exc:
        raise ConfigError("site.time_zone must be a valid IANA time zone") from exc

    simulation = raw["simulation"]
    _expect(
        isinstance(simulation, dict) and set(simulation) == {"start_utc", "end_utc"},
        "simulation contains unknown or missing keys",
    )
    start = _utc(simulation["start_utc"], "simulation.start_utc")
    end = _utc(simulation["end_utc"], "simulation.end_utc")
    _expect(end > start, "simulation.end_utc must be after start_utc")
    duration_seconds = int((end - start).total_seconds())
    _expect(duration_seconds <= MAX_DURATION_SECONDS, "simulation duration must not exceed one hour")
    _expect(duration_seconds % BASE_TICK_SECONDS == 0, "simulation duration must align to 5-second ticks")
    _expect(start.second % BASE_TICK_SECONDS == 0, "simulation.start_utc must align to 5-second ticks")

    monitoring = raw.setdefault("monitoring", copy.deepcopy(DEFAULT_MONITORING))
    _expect(
        isinstance(monitoring, dict) and set(monitoring) == set(DEFAULT_MONITORING),
        "monitoring contains unknown or missing keys",
    )
    for key, low, high in (
        ("max_records_per_type", 1, 1000),
        ("max_total_records", 1, 10000),
    ):
        value = monitoring[key]
        _expect(
            isinstance(value, int) and not isinstance(value, bool) and low <= value <= high,
            f"monitoring.{key} must be an integer between {low} and {high}",
        )

    return copy.deepcopy(raw)



def load_config(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"unable to load configuration: {exc}") from exc
    return validate_config(value)
