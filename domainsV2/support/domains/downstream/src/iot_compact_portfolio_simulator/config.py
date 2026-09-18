"""Strict configuration contract for the fictional compact portfolio simulator."""

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
MAX_DURATION_SECONDS = 24 * 3600
SCENARIOS = {
    "normal_portfolio",
    "communications_degradation",
    "module_degradation",
    "offsites_withdrawal",
    "portfolio_power_event",
    "blend_shift",
}
DEFAULT_LOCALIZATION = {
    "enabled": True,
    "hidden_withdrawal_route_fraction": 0.58,
    "confirmation_delay_seconds": 900,
}
OPERATOR_TOKENS = ("shell", "exxon", "chevron", "marathon", "enbridge", "bp", "valero")


class ConfigError(ValueError):
    """Raised when a compact portfolio configuration violates its public contract."""


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
    required = {"schema_version", "seed", "scenario", "enterprise", "simulation"}
    optional = {"localization", "output"}
    _expect(required <= set(raw), f"missing keys: {sorted(required - set(raw))}")
    _expect(set(raw) <= required | optional, f"unknown keys: {sorted(set(raw) - required - optional)}")
    _expect(raw["schema_version"] == SCHEMA_VERSION, "schema_version must be 1.0")
    _expect(isinstance(raw["seed"], int) and not isinstance(raw["seed"], bool), "seed must be an integer")
    _expect(raw["scenario"] in SCENARIOS, f"scenario must be one of {sorted(SCENARIOS)}")

    enterprise = raw["enterprise"]
    _expect(
        isinstance(enterprise, dict) and set(enterprise) == {"enterprise_id", "display_name", "time_zone"},
        "enterprise contains unknown or missing keys",
    )
    _expect(
        isinstance(enterprise["enterprise_id"], str)
        and re.fullmatch(r"fictional-[a-z0-9]+(?:-[a-z0-9]+)*", enterprise["enterprise_id"]) is not None,
        "enterprise.enterprise_id must be a lowercase fictional- identifier",
    )
    _expect(
        isinstance(enterprise["display_name"], str) and enterprise["display_name"].strip(),
        "enterprise.display_name is required",
    )
    _expect(
        not any(token in enterprise["display_name"].casefold() for token in OPERATOR_TOKENS),
        "enterprise.display_name must not identify a real operator",
    )
    try:
        ZoneInfo(enterprise["time_zone"])
    except (TypeError, ZoneInfoNotFoundError) as exc:
        raise ConfigError("enterprise.time_zone must be a valid IANA time zone") from exc

    simulation = raw["simulation"]
    _expect(
        isinstance(simulation, dict) and set(simulation) == {"start_utc", "end_utc"},
        "simulation contains unknown or missing keys",
    )
    start = _utc(simulation["start_utc"], "simulation.start_utc")
    end = _utc(simulation["end_utc"], "simulation.end_utc")
    _expect(end > start, "simulation.end_utc must be after start_utc")
    duration_seconds = int((end - start).total_seconds())
    _expect(duration_seconds <= MAX_DURATION_SECONDS, "simulation duration must not exceed 24 hours")
    _expect(duration_seconds % BASE_TICK_SECONDS == 0, "simulation duration must align to 5-second ticks")
    _expect(start.second % BASE_TICK_SECONDS == 0, "simulation.start_utc must align to 5-second ticks")

    localization = raw.setdefault("localization", copy.deepcopy(DEFAULT_LOCALIZATION))
    _expect(
        isinstance(localization, dict) and set(localization) == set(DEFAULT_LOCALIZATION),
        "localization contains unknown or missing keys",
    )
    _expect(isinstance(localization["enabled"], bool), "localization.enabled must be boolean")
    fraction = localization["hidden_withdrawal_route_fraction"]
    _expect(
        isinstance(fraction, (int, float))
        and not isinstance(fraction, bool)
        and 0.05 <= float(fraction) <= 0.95,
        "localization.hidden_withdrawal_route_fraction must be between 0.05 and 0.95",
    )
    localization["hidden_withdrawal_route_fraction"] = float(fraction)
    delay = localization["confirmation_delay_seconds"]
    _expect(
        isinstance(delay, int) and not isinstance(delay, bool) and 60 <= delay <= 86400,
        "localization.confirmation_delay_seconds must be an integer between 60 and 86400",
    )

    output = raw.setdefault(
        "output",
        {"include_csv": False, "telemetry_shard_target_records": 250000},
    )
    _expect(
        isinstance(output, dict) and set(output) == {"include_csv", "telemetry_shard_target_records"},
        "output contains unknown or missing keys",
    )
    _expect(isinstance(output["include_csv"], bool), "output.include_csv must be boolean")
    shard_target = output["telemetry_shard_target_records"]
    _expect(
        isinstance(shard_target, int)
        and not isinstance(shard_target, bool)
        and 1000 <= shard_target <= 1000000,
        "output.telemetry_shard_target_records must be an integer between 1000 and 1000000",
    )
    return copy.deepcopy(raw)


def load_config(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"unable to load configuration: {exc}") from exc
    return validate_config(value)
