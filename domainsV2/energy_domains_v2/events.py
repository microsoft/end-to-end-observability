"""Neutral observation envelopes and separately routed injection audit records."""

from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any

OBSERVATIONS = "EnergyObservations_CL"
AUDIT = "EnergyInjectionAudit_CL"
MAX_BATCH_BYTES = 190_000
MAX_FIELD_BYTES = 48_000
FORBIDDEN = {
    "scenario", "scenario_id", "scenario_name", "ground_truth", "is_anomaly",
    "isanomaly", "anomaly_type", "expected_outcome", "detector_outputs",
    "detector_verdict", "fault_injected", "injected",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def request_size(value: Any) -> int:
    return max(len(encoded(value)), len(json.dumps(value, allow_nan=False).encode("utf-8")))


def _neutral(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or key.casefold().replace("_", "") in {
                    name.replace("_", "") for name in FORBIDDEN}:
                raise ValueError("Detector labels are not observation fields")
            _neutral(item)
    elif isinstance(value, list):
        for item in value:
            _neutral(item)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Observation numbers must be finite")
    elif isinstance(value, str) and len(value.encode("utf-8")) > MAX_FIELD_BYTES:
        raise ValueError("Observation field is too large")


def event_id(run_id: str, sequence: int, index: int) -> str:
    return hashlib.sha256(f"{run_id}:{sequence}:{index}".encode()).hexdigest()


def observations(frame: dict, state: dict, domain: str, at: str) -> list[dict]:
    result = []
    for index, row in enumerate(frame["observations"]):
        _neutral(row)
        payload, context = row["payload"], row["context"]
        if not isinstance(payload, dict) or not isinstance(context, dict):
            raise ValueError("Observation payload and context must be objects")
        if max(request_size(payload), request_size(context)) > MAX_FIELD_BYTES:
            raise ValueError("Observation dynamic field is too large")
        simulated = row["simulated_at"] or at
        datetime.fromisoformat(simulated.replace("Z", "+00:00"))
        result.append({
            "TimeGenerated": at, "ObservedAt": at, "SimulatedAt": simulated,
            "SchemaVersion": "1", "EventId": event_id(state["run_id"], state["sequence"], index),
            "RunId": state["run_id"], "Domain": domain, "Surface": state["surface"],
            "Portal": "inventory" if state["surface"].endswith("-inventory") else "monitoring",
            "RecordType": row["record_type"], "EntityId": row["entity_id"],
            "LocationId": row["location_id"], "Payload": payload, "Context": context,
            "IsSynthetic": True, "OperationalUse": "prohibited",
        })
    if not result:
        raise ValueError("A simulation frame must have observable records")
    return result


def audit(state: dict, domain: str, action: str, scenario: str | None, at: str) -> dict:
    return {
        "TimeGenerated": at, "SchemaVersion": "1",
        "EventId": event_id(state["run_id"], state["sequence"], -1),
        "RunId": state["run_id"], "Domain": domain, "Surface": state["surface"],
        "Action": action, "Scenario": scenario or "", "Outcome": "accepted",
        "IsSynthetic": True, "OperationalUse": "prohibited",
    }


def batches(table: str, rows: list[dict]) -> list[dict]:
    result, current = [], []
    for row in rows:
        if request_size([row]) > MAX_BATCH_BYTES:
            raise ValueError("A single record exceeds the bounded ingestion batch")
        if current and request_size(current + [row]) > MAX_BATCH_BYTES:
            result.append(_batch(table, current))
            current = []
        current.append(row)
    if current:
        result.append(_batch(table, current))
    return result


def _batch(table: str, records: list[dict]) -> dict:
    return {
        "id": hashlib.sha256(encoded([table, records])).hexdigest(),
        "table": table, "records": records, "attempts": 0,
        "next_attempt": 0.0, "error": None,
    }
