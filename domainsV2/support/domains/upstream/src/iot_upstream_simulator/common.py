"""Shared deterministic primitives for the upstream simulator layers.

These helpers guarantee the repository determinism contract: content-derived
identifiers, stable ordering, finite floats rounded to six decimals, and a
single public time/quality/freshness/revision envelope shared by every runtime
record family.
"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from typing import Any

#: Public delivery evidence may land after the half-open simulation window ends.
#: The delayed-availability window is bounded and documented so that every public
#: timestamp still obeys a declared window.
MAX_DELIVERY_DELAY_SECONDS = 3600


def stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value[:-1] + "+00:00")


def round6(value: float) -> float:
    rounded = round(float(value), 6)
    if not math.isfinite(rounded):
        raise ValueError("simulation produced a non-finite value")
    return 0.0 if rounded == -0.0 else rounded


def stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def hash_float(seed: int, *parts: str, low: float = -1.0, high: float = 1.0) -> float:
    digest = hashlib.sha256(f"{seed}|{'|'.join(parts)}".encode("utf-8")).digest()
    return low + (high - low) * (int.from_bytes(digest[:8], "big") / 2**64)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def envelope(
    *,
    event_time: str,
    source_time: str,
    server_time: str,
    ingest_time: str,
    available_time: str | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
    quality_class: str = "good",
    quality_reason: str = "nominal",
    freshness_state: str = "fresh",
    origin_state: str = "measured",
    delivery_state: str = "delivered",
    clock_offset_ms: float = 0.0,
    clock_uncertainty_ms: float = 0.0,
    revision: int = 1,
    supersedes_record_id: str | None = None,
    validation_state: str = "not_applicable",
) -> dict[str, Any]:
    """Build the shared public time, quality, freshness and revision envelope."""
    return {
        "event_time_utc": event_time,
        "source_time_utc": source_time,
        "server_time_utc": server_time,
        "ingest_time_utc": ingest_time,
        "available_time_utc": available_time,
        "period_start_utc": period_start,
        "period_end_utc": period_end,
        "quality_class": quality_class,
        "quality_reason": quality_reason,
        "freshness_state": freshness_state,
        "origin_state": origin_state,
        "delivery_state": delivery_state,
        "clock_offset_ms": round6(clock_offset_ms),
        "clock_uncertainty_ms": round6(clock_uncertainty_ms),
        "revision": revision,
        "supersedes_record_id": supersedes_record_id,
        "validation_state": validation_state,
    }


#: Exact field names of the shared envelope, used by contract tests.
TIME_QUALITY_FIELDS = tuple(
    envelope(event_time="", source_time="", server_time="", ingest_time="")
)

QUALITY_CLASSES = ("good", "uncertain", "bad")
FRESHNESS_STATES = ("fresh", "aging", "stale", "unknown")
DELIVERY_STATES = (
    "delivered",
    "late",
    "stale",
    "missing",
    "duplicate",
    "out_of_order",
    "store_and_forward",
    "batch_delivered",
    "workflow_delivered",
)
ORIGIN_STATES = (
    "measured",
    "drifting",
    "frozen",
    "deenergized",
    "process_off",
    "calibration_hold",
    "substituted",
    "workflow_entry",
    "derived_summary",
    "laboratory_entry",
    "external_observation",
)
VALIDATION_STATES = (
    "not_applicable",
    "provisional",
    "validated",
    "retracted",
    "superseded",
    "invalidated",
)


__all__ = [
    "DELIVERY_STATES",
    "FRESHNESS_STATES",
    "MAX_DELIVERY_DELAY_SECONDS",
    "ORIGIN_STATES",
    "QUALITY_CLASSES",
    "TIME_QUALITY_FIELDS",
    "VALIDATION_STATES",
    "clamp",
    "envelope",
    "hash_float",
    "parse",
    "round6",
    "stable_id",
    "stamp",
]
