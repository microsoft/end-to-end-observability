from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker


COLUMNS = (
    "TimeGenerated", "ObservedAt", "SimulatedAt", "SchemaVersion", "EventId",
    "RunId", "Domain", "Surface", "Portal", "RecordType", "EntityId",
    "LocationId", "Payload", "Context", "IsSynthetic", "OperationalUse",
)
SURFACES = (
    "power-grid", "upstream", "midstream", "downstream", "downstream-portfolio",
    "power-grid-inventory", "upstream-inventory", "midstream-inventory", "downstream-inventory",
)
FORBIDDEN_KEYS = frozenset(name.replace("_", "") for name in (
    "scenario", "scenario_id", "scenario_name", "ground_truth", "is_anomaly",
    "isanomaly", "anomaly_type", "expected_outcome", "detector_outputs",
    "detector_verdict", "fault_injected", "injected",
))
SCHEMA_PATH = Path(__file__).with_name("contracts") / "energy-observations.v1.schema.json"
MAX_FIELD_BYTES = 48_000


class ContractError(ValueError):
    """Source evidence does not satisfy its accepted contract."""


def utc(value: str | datetime) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ContractError("invalid timestamp") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ContractError("timestamp must have an explicit timezone")
    return value.astimezone(timezone.utc)


def iso(value: str | datetime) -> str:
    return utc(value).isoformat().replace("+00:00", "Z")


def json_value(value: Any, depth: int = 0) -> Any:
    if depth > 24:
        raise ContractError("evidence nesting exceeds the supported limit")
    if isinstance(value, datetime):
        return iso(value)
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ContractError("JSON keys must be strings")
        return {key: json_value(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [json_value(item, depth + 1) for item in value]
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ContractError("evidence contains an unsupported or nonfinite JSON value")


def canonical(value: Any) -> bytes:
    return json.dumps(json_value(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def strict_json(value: str | bytes) -> Any:
    def object_pairs(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ContractError("duplicate JSON object key")
            result[key] = item
        return result

    def invalid_constant(_):
        raise ContractError("nonfinite JSON number")

    try:
        return json.loads(value, object_pairs_hook=object_pairs, parse_constant=invalid_constant)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ContractError("invalid JSON") from exc


@lru_cache(maxsize=1)
def source_validator() -> Draft202012Validator:
    schema = strict_json(SCHEMA_PATH.read_bytes())
    Draft202012Validator.check_schema(schema)
    checker = FormatChecker()

    @checker.checks("date-time", raises=ContractError)
    def date_time(value):
        if not isinstance(value, str):
            return True
        utc(value)
        return True

    return Draft202012Validator(schema, format_checker=checker)


def _neutral(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.casefold().replace("_", "") in FORBIDDEN_KEYS:
                raise ContractError("control/evaluation labels are forbidden in observation evidence")
            _neutral(item)
    elif isinstance(value, list):
        for item in value:
            _neutral(item)
    elif isinstance(value, str) and len(value.encode("utf-8")) > MAX_FIELD_BYTES:
        raise ContractError("source field exceeds its contract byte limit")


@dataclass(frozen=True)
class ProducerScope:
    workspace_resource_id: str

    def __post_init__(self):
        if not isinstance(self.workspace_resource_id, str) or not re.fullmatch(
            r"/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[^/]+/providers/"
            r"Microsoft\.OperationalInsights/workspaces/[^/]+",
            self.workspace_resource_id, re.IGNORECASE,
        ):
            raise ContractError("an explicit Log Analytics workspace resource ID is required")
        try:
            UUID(self.workspace_resource_id.split("/")[2])
        except ValueError as exc:
            raise ContractError("workspace subscription ID must be a UUID") from exc

    @property
    def key(self) -> str:
        return digest(self.workspace_resource_id.casefold())


@dataclass(frozen=True)
class Observation:
    producer: ProducerScope
    document: bytes

    @classmethod
    def parse(cls, producer: ProducerScope, row: dict[str, Any]) -> Observation:
        row = json_value(row)
        if not isinstance(row, dict):
            raise ContractError("observation must be an object")
        for field in ("TimeGenerated", "ObservedAt", "SimulatedAt"):
            if field in row:
                row[field] = iso(row[field])
        payload = row.get("Payload")
        if isinstance(payload, dict) and payload.get("source_time_utc") is not None:
            payload["source_time_utc"] = iso(payload["source_time_utc"])
        errors = list(source_validator().iter_errors(row))
        if errors:
            error = errors[0]
            path = ".".join(str(part) for part in error.absolute_path) or "$"
            raise ContractError(f"observation schema violation at {path}: {error.validator}")
        _neutral(row)
        for field in ("Payload", "Context"):
            if max(len(canonical(row[field])),
                   len(json.dumps(row[field], allow_nan=False).encode("utf-8"))) > MAX_FIELD_BYTES:
                raise ContractError(f"{field} exceeds its contract byte limit")
        for component in ("tag_id", "phase"):
            if payload.get(component) is not None and not isinstance(payload[component], str):
                raise ContractError(f"identity component {component} must be string or null")
        context = row["Context"]
        if context.get("details_profile") == "domain-details-v1":
            if row["Portal"] == "monitoring":
                measurement, topology = context["measurement"], context["topology"]
                if any(measurement[key] != payload[key] for key in ("measurement", "unit")):
                    raise ContractError("rich measurement context conflicts with payload")
                if topology["available_link_count"] != len(topology["links"]) + topology["omitted_link_count"]:
                    raise ContractError("topology coverage counts are inconsistent")
            else:
                applicability = context["applicability"]
                count = len(applicability["items"])
                if (applicability["included_count"] != count or applicability["total_count"] < count
                        or applicability["partial"] != (applicability["total_count"] > count)):
                    raise ContractError("inventory applicability coverage is inconsistent")
        return cls(producer, canonical(row))

    @property
    def row(self) -> dict[str, Any]:
        return strict_json(self.document)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.document).hexdigest()

    @property
    def event_key(self) -> str:
        return digest([self.producer.key, self.row["EventId"]])

    @property
    def source_sha256(self) -> str:
        # Azure may replace TimeGenerated independently on duplicate delivery.
        return digest({key: value for key, value in self.row.items() if key != "TimeGenerated"})

    @property
    def signal_identity(self) -> tuple[str | None, ...]:
        row = self.row
        return tuple(row[key] for key in
                     ("Domain", "Surface", "RunId", "RecordType", "EntityId", "LocationId")) + tuple(
                         row["Payload"].get(key) for key in ("measurement", "tag_id", "phase"))

    @property
    def signal_key(self) -> str:
        return digest([self.producer.key, self.signal_identity_as_list()])

    def signal_identity_as_list(self) -> list[str | None]:
        return list(self.signal_identity)

    @property
    def source_age_seconds(self) -> float | None:
        row = self.row
        source_time = row["Payload"].get("source_time_utc")
        if source_time is None:
            return None
        return (utc(row["SimulatedAt"]) - utc(source_time)).total_seconds()
