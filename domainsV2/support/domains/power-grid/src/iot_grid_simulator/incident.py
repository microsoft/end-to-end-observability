"""Synthetic operational incident contract and lifecycle helpers."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Any

INCIDENT_SCHEMA_VERSION = "1.0"
INCIDENT_STATUSES = (
    "detected",
    "investigating",
    "mitigating",
    "monitoring",
    "resolved",
)
ACTIVE_OUTAGE_STATUSES = {
    "detected",
    "investigating",
    "mitigating",
}


class IncidentError(ValueError):
    """Raised when an incident template or resolved incident is invalid."""


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise IncidentError(message)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(value: Any, field: str) -> datetime:
    _expect(isinstance(value, str) and value.endswith("Z"), f"{field} must be UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise IncidentError(f"{field} must be a valid timestamp") from exc
    _expect(
        parsed.second == 0 and parsed.microsecond == 0,
        f"{field} must align to a minute",
    )
    return parsed


def _validate_common(value: dict[str, Any], configured_areas: set[str]) -> None:
    _expect(
        isinstance(value["incident_id"], str)
        and value["incident_id"].startswith("synthetic-"),
        "incident_id must start with synthetic-",
    )
    for field in ("title", "owner_team", "detection_source", "root_cause"):
        _expect(
            isinstance(value[field], str) and value[field].strip(),
            f"incident {field} must be a non-empty string",
        )
    _expect(
        value["incident_type"] == "distribution_transformer_outage",
        "incident_type must be distribution_transformer_outage",
    )
    _expect(
        value["severity"] in {"minor", "major", "critical"},
        "incident severity must be minor, major, or critical",
    )
    _expect(
        value["exercise_status"] in {"Test", "Exercise"},
        "incident exercise_status must be Test or Exercise",
    )
    _expect(value["is_synthetic"] is True, "incident is_synthetic must be true")
    _expect(value["public_safety"] is False, "incident public_safety must be false")
    _expect(value["scope"] == "Private", "incident scope must be Private")
    _expect(
        isinstance(value["area"], str) and value["area"] in configured_areas,
        "incident area must match a configured location area",
    )
    _expect(
        value["caused_by"] is None
        or (
            isinstance(value["caused_by"], str)
            and value["caused_by"].startswith("synthetic-")
        ),
        "incident caused_by must be null or a synthetic identifier",
    )


def validate_incident_template(
    value: dict[str, Any],
    configured_areas: set[str],
) -> dict[str, Any]:
    """Validate a relative-time incident command template."""
    expected = {
        "incident_id",
        "title",
        "incident_type",
        "severity",
        "exercise_status",
        "is_synthetic",
        "public_safety",
        "scope",
        "area",
        "owner_team",
        "detection_source",
        "root_cause",
        "caused_by",
        "lifecycle",
    }
    _expect(
        isinstance(value, dict) and set(value) == expected,
        "incident template contains unknown or missing keys",
    )
    _validate_common(value, configured_areas)
    lifecycle = value["lifecycle"]
    _expect(
        isinstance(lifecycle, list) and len(lifecycle) == len(INCIDENT_STATUSES),
        "incident lifecycle must contain five transitions",
    )
    previous_offset = -1.0
    for index, transition in enumerate(lifecycle, start=1):
        _expect(
            isinstance(transition, dict)
            and set(transition) == {"sequence", "status", "offset_minutes"},
            f"incident lifecycle transition {index} has invalid fields",
        )
        _expect(
            transition["sequence"] == index,
            "incident lifecycle sequence must start at 1 and be contiguous",
        )
        _expect(
            transition["status"] == INCIDENT_STATUSES[index - 1],
            "incident lifecycle statuses must follow the documented order",
        )
        offset = transition["offset_minutes"]
        _expect(
            isinstance(offset, int)
            and not isinstance(offset, bool)
            and previous_offset < offset <= 7 * 24 * 60,
            "incident lifecycle offsets must be whole, increasing minutes within seven days",
        )
        if index == 1:
            _expect(offset == 0, "incident detected offset must be zero")
        previous_offset = float(offset)
    return copy.deepcopy(value)


def validate_resolved_incident(
    value: dict[str, Any],
    configured_areas: set[str],
) -> dict[str, Any]:
    """Validate an absolute-time incident embedded in a simulation config."""
    expected = {
        "incident_id",
        "title",
        "incident_type",
        "severity",
        "exercise_status",
        "is_synthetic",
        "public_safety",
        "scope",
        "area",
        "owner_team",
        "detection_source",
        "root_cause",
        "caused_by",
        "lifecycle",
    }
    _expect(
        isinstance(value, dict) and set(value) == expected,
        "environment.incident contains unknown or missing keys",
    )
    _validate_common(value, configured_areas)
    lifecycle = value["lifecycle"]
    _expect(
        isinstance(lifecycle, list) and len(lifecycle) == len(INCIDENT_STATUSES),
        "incident lifecycle must contain five transitions",
    )
    previous_at: datetime | None = None
    for index, transition in enumerate(lifecycle, start=1):
        _expect(
            isinstance(transition, dict)
            and set(transition) == {"sequence", "status", "at_utc"},
            f"incident lifecycle transition {index} has invalid fields",
        )
        _expect(
            transition["sequence"] == index,
            "incident lifecycle sequence must start at 1 and be contiguous",
        )
        _expect(
            transition["status"] == INCIDENT_STATUSES[index - 1],
            "incident lifecycle statuses must follow the documented order",
        )
        at = _parse_utc(
            transition["at_utc"],
            f"environment.incident.lifecycle[{index - 1}].at_utc",
        )
        _expect(
            previous_at is None or at > previous_at,
            "incident lifecycle timestamps must be increasing",
        )
        previous_at = at
    return copy.deepcopy(value)


def resolve_incident_template(
    value: dict[str, Any],
    simulated_start: datetime,
) -> dict[str, Any]:
    """Resolve relative lifecycle offsets against a simulation boundary."""
    resolved = copy.deepcopy(value)
    resolved["lifecycle"] = [
        {
            "sequence": transition["sequence"],
            "status": transition["status"],
            "at_utc": _timestamp(
                simulated_start
                + timedelta(minutes=float(transition["offset_minutes"]))
            ),
        }
        for transition in value["lifecycle"]
    ]
    return resolved


def incident_status_at(
    incident: dict[str, Any],
    timestamp: datetime,
) -> str | None:
    """Return the lifecycle state visible at a simulation timestamp."""
    transitions = incident["lifecycle"]
    detected = _parse_utc(transitions[0]["at_utc"], "incident detected time")
    if timestamp < detected:
        return None
    current = transitions[0]["status"]
    for transition in transitions[1:]:
        if timestamp < _parse_utc(transition["at_utc"], "incident transition time"):
            break
        current = transition["status"]
    return current
