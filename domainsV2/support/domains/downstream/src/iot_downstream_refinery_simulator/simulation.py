"""Deterministic downstream refinery telemetry and sparse monitoring simulation."""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .catalog import AREA_DEFINITIONS, MONITORING_RECORD_TYPES, NORMAL_MONITORING_COUNTS

TWO_PI = math.pi * 2.0
RECORD_FIDELITY = {
    "events": "F0",
    "alarm_journal": "F0",
    "operator_actions": "F0",
    "device_logs": "F0",
    "audit_logs": "F0",
    "delivery_logs": "F0",
    "analyzer_results": "FH",
    "condition_monitoring": "F2",
    "electrical_monitoring": "F2",
    "environmental_observations": "FH",
    "lab_results": "FH",
    "maintenance_context": "F0",
    "shipment_context": "F3",
}
SCENARIO_AREA_MAP = {
    "normal_run": ("A01",),
    "receipt_distillation_shift": ("A01", "A02"),
    "conversion_treating_degradation": ("A03", "A04", "A05"),
    "blending_loading_shift": ("A06", "A07"),
    "utility_sag": ("A08", "A09", "A02", "A03", "A04", "A05"),
    "environmental_workflow": ("A10", "A11"),
    "analyzer_delay": ("A06", "A11"),
    "communications_degradation": ("A12", "A08", "A10"),
}
AREA_INDEX = {item["area_id"]: index for index, item in enumerate(AREA_DEFINITIONS, 1)}


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _round(value: float) -> float:
    rounded = round(float(value), 6)
    return 0.0 if rounded == -0.0 else rounded


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _hash_float(seed: int, *parts: str, low: float = -1.0, high: float = 1.0) -> float:
    digest = hashlib.sha256(f"{seed}|{'|'.join(parts)}".encode("utf-8")).digest()
    scaled = int.from_bytes(digest[:8], "big") / 2**64
    return low + (high - low) * scaled


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _scenario_physical_delta(scenario: str, area_id: str, active: bool) -> dict[str, float]:
    zero = {"throughput": 0.0, "pressure": 0.0, "temperature": 0.0, "power": 0.0, "inventory": 0.0, "quality": 0.0, "condition": 0.0, "utility": 0.0, "availability": 0.0, "observation": 0.0}
    if not active or scenario == "communications_degradation":
        return zero
    if scenario == "receipt_distillation_shift" and area_id in {"A01", "A02"}:
        return {"throughput": -0.06, "pressure": 8.0, "temperature": 3.2, "power": 6.0, "inventory": 0.03, "quality": -0.04, "condition": -0.01, "utility": 0.02, "availability": -0.01, "observation": 0.04}
    if scenario == "conversion_treating_degradation" and area_id in {"A03", "A04", "A05"}:
        return {"throughput": -0.08, "pressure": 10.0, "temperature": 6.5, "power": 12.0, "inventory": 0.02, "quality": -0.06, "condition": -0.08, "utility": 0.05, "availability": -0.02, "observation": 0.05}
    if scenario == "blending_loading_shift" and area_id in {"A06", "A07"}:
        return {"throughput": 0.05, "pressure": 5.0, "temperature": 1.8, "power": 7.0, "inventory": -0.06, "quality": 0.05, "condition": -0.01, "utility": 0.03, "availability": -0.01, "observation": 0.03}
    if scenario == "utility_sag" and area_id in {"A08", "A09", "A02", "A03", "A04", "A05"}:
        primary = area_id in {"A08", "A09"}
        return {"throughput": -0.09 if primary else -0.04, "pressure": -12.0 if primary else -5.0, "temperature": 4.0 if primary else 2.0, "power": -18.0 if primary else -9.0, "inventory": 0.01, "quality": -0.03 if not primary else -0.01, "condition": -0.03, "utility": -0.12 if primary else -0.07, "availability": -0.03, "observation": 0.06}
    if scenario == "environmental_workflow" and area_id in {"A10", "A11"}:
        return {"throughput": 0.0, "pressure": 0.0, "temperature": 0.8, "power": 0.0, "inventory": 0.0, "quality": 0.0, "condition": -0.01, "utility": 0.0, "availability": -0.02, "observation": 0.22}
    if scenario == "analyzer_delay" and area_id in {"A06", "A11"}:
        return {"throughput": 0.0, "pressure": 0.0, "temperature": 0.5, "power": 0.0, "inventory": 0.0, "quality": -0.02, "condition": 0.0, "utility": 0.0, "availability": -0.04, "observation": 0.12}
    return zero


def _base_state(area_index: int, fraction: float) -> dict[str, float]:
    swing = math.sin(TWO_PI * fraction + area_index * 0.31)
    ripple = math.cos(TWO_PI * fraction * 0.5 + area_index * 0.17)
    return {
        "throughput": 0.58 + area_index * 0.018 + swing * 0.032,
        "pressure": 185.0 + area_index * 10.5 + swing * 6.5,
        "temperature": 34.0 + area_index * 2.8 + ripple * 2.7,
        "power": 110.0 + area_index * 11.5 + swing * 5.2,
        "inventory": 0.42 + area_index * 0.023 + ripple * 0.016,
        "quality": 0.71 + (area_index % 4) * 0.017 + swing * 0.012,
        "condition": 0.91 - area_index * 0.008 + ripple * 0.01,
        "utility": 0.73 + area_index * 0.012 + swing * 0.014,
        "availability": 0.992 - (area_index % 3) * 0.002,
        "observation": 0.28 + area_index * 0.018 + ripple * 0.01,
    }


def _measurement_value(measurement: str, state: dict[str, float]) -> float:
    mapping = {
        "throughput_index": _clamp(state["throughput"], 0.0, 1.25),
        "pressure_kpa": _clamp(state["pressure"], 0.0, 400.0),
        "temperature_c": _clamp(state["temperature"], -20.0, 200.0),
        "power_kw": _clamp(state["power"], 0.0, 500.0),
        "inventory_index": _clamp(state["inventory"], 0.0, 1.2),
        "quality_index": _clamp(state["quality"], 0.0, 1.2),
        "condition_index": _clamp(state["condition"], 0.0, 1.2),
        "utility_balance_index": _clamp(state["utility"], 0.0, 1.2),
        "availability_index": _clamp(state["availability"], 0.0, 1.0),
        "observation_index": _clamp(state["observation"], 0.0, 1.0),
    }
    return mapping[measurement]


def _delivery_adjustment(config: dict[str, Any], scenario: str, area_id: str, measurement: str, scheduled_time: datetime, onset: datetime, sequence: int, measured_value: float, previous_record: dict[str, Any] | None) -> tuple[Any, str, datetime, str, dict[str, Any], str, float, float]:
    latency_seconds = 2.0 + _hash_float(config["seed"], area_id, measurement, str(sequence), low=0.0, high=6.0)
    delivery_class = "delivered"
    clock_state = "synchronized"
    quality = {"validity": "good", "source": "delivered_observation", "reasons": []}
    source_time = scheduled_time
    delivered_value: Any = _round(measured_value)
    uncertainty_ms = 3.0
    active = scheduled_time >= onset
    if scenario == "communications_degradation" and active and area_id in SCENARIO_AREA_MAP[scenario]:
        if sequence % 17 == 0:
            delivered_value = None
            delivery_class = "missing"
            clock_state = "degraded"
            quality = {"validity": "bad", "source": "delivered_observation", "reasons": ["communications_path_unavailable"]}
            latency_seconds = 120.0
            uncertainty_ms = 28.0
        elif sequence % 13 == 0 and previous_record is not None and previous_record["value"] is not None:
            delivered_value = previous_record["value"]
            source_time = datetime.fromisoformat(previous_record["source_time_utc"][:-1] + "+00:00")
            delivery_class = "stale"
            clock_state = "degraded"
            quality = {"validity": "uncertain", "source": "delivered_observation", "reasons": ["stale_delivery"]}
            latency_seconds = 75.0
            uncertainty_ms = 24.0
        elif sequence % 11 == 0:
            delivery_class = "late"
            clock_state = "degraded"
            quality = {"validity": "uncertain", "source": "delivered_observation", "reasons": ["late_delivery"]}
            latency_seconds = 90.0
            uncertainty_ms = 20.0
        elif sequence % 7 == 0:
            delivery_class = "store_and_forward"
            clock_state = "recovered"
            quality = {"validity": "good", "source": "delivered_observation", "reasons": ["store_and_forward"]}
            latency_seconds = 60.0
            uncertainty_ms = 16.0
    elif scenario == "analyzer_delay" and active and area_id in SCENARIO_AREA_MAP[scenario] and measurement in {"quality_index", "observation_index"}:
        delivery_class = "late"
        clock_state = "delayed_analysis"
        quality = {"validity": "uncertain", "source": "delivered_observation", "reasons": ["analyzer_cycle_delayed"]}
        latency_seconds = 180.0
        uncertainty_ms = 12.0
    elif scenario == "environmental_workflow" and active and area_id == "A10" and measurement == "observation_index":
        delivery_class = "late"
        clock_state = "workflow_buffered"
        quality = {"validity": "uncertain", "source": "delivered_observation", "reasons": ["workflow_buffering"]}
        latency_seconds = 135.0
        uncertainty_ms = 10.0
    ingest_time = scheduled_time + timedelta(seconds=latency_seconds)
    clock_offset_ms = _round(5.0 + AREA_INDEX[area_id] * 1.75 + _hash_float(config["seed"], area_id, measurement, "clock", low=-1.25, high=1.25))
    return delivered_value, _stamp(source_time), ingest_time, delivery_class, quality, clock_state, clock_offset_ms, _round(uncertainty_ms)


def _iter_times(start: datetime, end: datetime, cadence_seconds: int) -> Iterable[datetime]:
    current = start
    step = timedelta(seconds=cadence_seconds)
    while current < end:
        yield current
        current += step


def _record_common(run_id: str, scenario_id: str, site_id: str, area_id: str, fidelity: str) -> dict[str, Any]:
    return {"schema_version": "1.0", "run_id": run_id, "scenario_id": scenario_id, "site_id": site_id, "area_id": area_id, "fidelity": fidelity, "is_synthetic": True, "operational_use": "prohibited"}


def _choose_telemetry_ids(telemetry_records: list[dict[str, Any]], area_id: str, measurement: str, limit: int = 3) -> list[str]:
    return [item["telemetry_id"] for item in telemetry_records if item["area_id"] == area_id and item["measurement"] == measurement][:limit]


def _area_asset_id(assets_by_area: dict[str, list[dict[str, Any]]], area_id: str, index: int = 0) -> str:
    return assets_by_area[area_id][index]["asset_id"]


def _area_device_id(devices_by_area: dict[str, list[dict[str, Any]]], area_id: str, index: int = 0) -> str:
    return devices_by_area[area_id][index]["device_id"]


def simulate(config: dict[str, Any], run_id: str, assets: list[dict[str, Any]], devices: list[dict[str, Any]], tags: list[dict[str, Any]]) -> dict[str, Any]:
    site_id = config["site"]["site_id"]
    scenario = config["scenario"]
    start = datetime.fromisoformat(config["simulation"]["start_utc"][:-1] + "+00:00")
    end = datetime.fromisoformat(config["simulation"]["end_utc"][:-1] + "+00:00")
    duration_seconds = int((end - start).total_seconds())
    onset_offset = min(max(60, duration_seconds // 2), duration_seconds - 1)
    onset = start + timedelta(seconds=onset_offset)
    last_public_time = end - timedelta(seconds=1)

    def public_stamp(value: datetime) -> str:
        return _stamp(min(max(value, start), last_public_time))

    scenario_id = _stable_id("scenario", run_id, scenario)
    assets_by_area: dict[str, list[dict[str, Any]]] = {}
    devices_by_area: dict[str, list[dict[str, Any]]] = {}
    for item in assets:
        assets_by_area.setdefault(item["area_id"], []).append(item)
    for item in devices:
        devices_by_area.setdefault(item["area_id"], []).append(item)
    for values in assets_by_area.values():
        values.sort(key=lambda item: item["asset_index"])
    for values in devices_by_area.values():
        values.sort(key=lambda item: item["device_index"])

    telemetry: list[dict[str, Any]] = []
    previous_by_tag: dict[str, dict[str, Any]] = {}
    ordered_tags = sorted(tags, key=lambda item: (item["area_id"], item["tag_index"], item["tag_id"]))
    denominator = max(duration_seconds - 5, 5)
    for tag in ordered_tags:
        area_index = AREA_INDEX[tag["area_id"]]
        sequence = 0
        for scheduled_time in _iter_times(start, end, tag["cadence_seconds"]):
            sequence += 1
            fraction = (scheduled_time - start).total_seconds() / denominator
            active = scheduled_time >= onset
            state = _base_state(area_index, fraction)
            delta = _scenario_physical_delta(scenario, tag["area_id"], active)
            state = {key: state[key] + delta[key] for key in state}
            measured_value = _measurement_value(tag["measurement"], state) + _hash_float(config["seed"], tag["tag_id"], str(sequence), low=-0.006, high=0.006)
            previous = previous_by_tag.get(tag["tag_id"])
            delivered_value, source_time_utc, ingest_time, delivery_class, quality, clock_state, clock_offset_ms, clock_uncertainty_ms = _delivery_adjustment(config, scenario, tag["area_id"], tag["measurement"], scheduled_time, onset, sequence, measured_value, previous)
            record = {
                **_record_common(run_id, scenario_id, site_id, tag["area_id"], tag["fidelity"]),
                "telemetry_id": _stable_id("telemetry", run_id, tag["tag_id"], _stamp(scheduled_time)),
                "asset_id": tag["asset_id"],
                "device_id": tag["device_id"],
                "tag_id": tag["tag_id"],
                "event_time_utc": _stamp(scheduled_time),
                "source_time_utc": source_time_utc,
                "ingest_time_utc": public_stamp(ingest_time),
                "measurement": tag["measurement"],
                "value": delivered_value,
                "unit": tag["unit"],
                "cadence_class": tag["cadence_class"],
                "cadence_seconds": tag["cadence_seconds"],
                "delivery_class": delivery_class,
                "quality": quality,
                "clock_state": clock_state,
                "clock_offset_ms": clock_offset_ms,
                "clock_uncertainty_ms": clock_uncertainty_ms,
                "sequence": sequence,
            }
            telemetry.append(record)
            previous_by_tag[tag["tag_id"]] = record

    area_windows = {area["area_id"]: (onset - timedelta(seconds=30), onset + timedelta(seconds=45 + AREA_INDEX[area["area_id"]] * 2), min(end, onset + timedelta(seconds=105 + AREA_INDEX[area["area_id"]] * 2))) for area in AREA_DEFINITIONS}
    severity = "info" if scenario == "normal_run" else "warning"
    focus_areas = set(SCENARIO_AREA_MAP[scenario])
    monitoring: dict[str, list[dict[str, Any]]] = {name: [] for name in MONITORING_RECORD_TYPES}

    for area in AREA_DEFINITIONS:
        area_id = area["area_id"]
        _, area_mid, _ = area_windows[area_id]
        monitoring["events"].append({**_record_common(run_id, scenario_id, site_id, area_id, RECORD_FIDELITY["events"]), "event_id": _stable_id("event", run_id, area_id), "asset_id": _area_asset_id(assets_by_area, area_id), "event_time_utc": public_stamp(area_mid), "source_time_utc": public_stamp(area_mid), "ingest_time_utc": public_stamp(area_mid + timedelta(seconds=6)), "event_type": scenario, "severity": severity if area_id in focus_areas else "info", "state_transition": {"from": "steady_state", "to": "attention_required" if area_id in focus_areas and scenario != "normal_run" else "steady_state"}, "summary": f"Synthetic monitoring window for {area['display_name']}.", "evidence_telemetry_ids": _choose_telemetry_ids(telemetry, area_id, "throughput_index")})

    for area_id in ("A02", "A03", "A08", "A12"):
        event = next(item for item in monitoring["events"] if item["area_id"] == area_id)
        monitoring["alarm_journal"].append({**_record_common(run_id, scenario_id, site_id, area_id, RECORD_FIDELITY["alarm_journal"]), "alarm_id": _stable_id("alarm", run_id, area_id), "event_id": event["event_id"], "asset_id": _area_asset_id(assets_by_area, area_id), "event_time_utc": event["event_time_utc"], "source_time_utc": event["source_time_utc"], "ingest_time_utc": event["ingest_time_utc"], "alarm_state": "active" if scenario != "normal_run" and area_id in focus_areas else "clear", "priority_class": "monitoring", "message": f"Synthetic alarm lifecycle evidence for {area_id}."})

    for offset, area_id in enumerate(("A12", "A10"), 1):
        event = next(item for item in monitoring["events"] if item["area_id"] == area_id)
        action_time = datetime.fromisoformat(event["event_time_utc"][:-1] + "+00:00") + timedelta(seconds=offset * 15)
        monitoring["operator_actions"].append({**_record_common(run_id, scenario_id, site_id, area_id, RECORD_FIDELITY["operator_actions"]), "action_id": _stable_id("action", run_id, area_id), "event_id": event["event_id"], "asset_id": _area_asset_id(assets_by_area, area_id), "action_time_utc": public_stamp(action_time), "action_type": "acknowledge_and_review", "actor_role": "fictional_monitoring_room", "note": f"Synthetic note recorded for {scenario}."})

    for area in AREA_DEFINITIONS:
        area_id = area["area_id"]
        _, area_mid, _ = area_windows[area_id]
        monitoring["device_logs"].append({**_record_common(run_id, scenario_id, site_id, area_id, RECORD_FIDELITY["device_logs"]), "log_id": _stable_id("log", run_id, area_id), "asset_id": _area_asset_id(assets_by_area, area_id, 1), "device_id": _area_device_id(devices_by_area, area_id, 1), "event_time_utc": public_stamp(area_mid), "source_time_utc": public_stamp(area_mid), "ingest_time_utc": public_stamp(area_mid + timedelta(seconds=8)), "level": "warning" if scenario != "normal_run" and area_id in focus_areas else "info", "category": "synthetic_monitoring", "code": scenario.upper(), "message": f"Synthetic {scenario} device-log evidence for {area['display_name']}.", "quality": {"validity": "good", "source": "device_log", "reasons": []}})
        monitoring["delivery_logs"].append({**_record_common(run_id, scenario_id, site_id, area_id, RECORD_FIDELITY["delivery_logs"]), "delivery_id": _stable_id("delivery", run_id, area_id), "asset_id": _area_asset_id(assets_by_area, area_id, 1), "device_id": _area_device_id(devices_by_area, area_id, 1), "event_time_utc": public_stamp(area_mid + timedelta(seconds=12)), "source_time_utc": public_stamp(area_mid), "ingest_time_utc": public_stamp(area_mid + timedelta(seconds=18)), "source_domain": "field_collection", "destination_domain": "refinery_monitoring_store", "delivery_state": "degraded" if scenario == "communications_degradation" and area_id in focus_areas else "delivered", "queue_depth": 3 if scenario == "communications_degradation" and area_id in focus_areas else 0, "latency_ms": _round(40.0 + AREA_INDEX[area_id] * 4.5 + (180.0 if scenario == "communications_degradation" and area_id in focus_areas else 0.0)), "evidence_telemetry_ids": _choose_telemetry_ids(telemetry, area_id, "throughput_index", limit=2)})

    for area_id in ("A12", "A11"):
        event = next(item for item in monitoring["events"] if item["area_id"] == area_id)
        revision_time = datetime.fromisoformat(event["event_time_utc"][:-1] + "+00:00") + timedelta(seconds=10)
        monitoring["audit_logs"].append({**_record_common(run_id, scenario_id, site_id, area_id, RECORD_FIDELITY["audit_logs"]), "audit_id": _stable_id("audit", run_id, area_id), "asset_id": _area_asset_id(assets_by_area, area_id, 1), "revision_time_utc": public_stamp(revision_time), "action": "bounded_configuration_review", "actor_role": "fictional_ot_service", "summary": f"Synthetic audit trail for {scenario}.", "related_event_id": event["event_id"]})

    for area_id, measurement in (("A02", "quality_index"), ("A06", "quality_index"), ("A11", "observation_index")):
        area_start, area_mid, _ = area_windows[area_id]
        monitoring["analyzer_results"].append({**_record_common(run_id, scenario_id, site_id, area_id, RECORD_FIDELITY["analyzer_results"]), "result_id": _stable_id("analyzer", run_id, area_id), "asset_id": _area_asset_id(assets_by_area, area_id, 0), "device_id": _area_device_id(devices_by_area, area_id, 1), "sample_id": _stable_id("sample", run_id, area_id, "analyzer"), "sample_time_utc": public_stamp(area_start + timedelta(seconds=20)), "analysis_complete_utc": public_stamp(area_mid + timedelta(seconds=25)), "stream_measurement": measurement, "result_value": _round(0.74 + AREA_INDEX[area_id] * 0.01 + (0.05 if scenario == "analyzer_delay" and area_id in focus_areas else 0.0)), "unit": "index", "result_state": "delayed" if scenario == "analyzer_delay" and area_id in focus_areas else "available", "evidence_telemetry_ids": _choose_telemetry_ids(telemetry, area_id, measurement)})

    for area_id in ("A01", "A02", "A03", "A04", "A05", "A08", "A09", "A11"):
        area_start, _, area_end = area_windows[area_id]
        monitoring["condition_monitoring"].append({**_record_common(run_id, scenario_id, site_id, area_id, RECORD_FIDELITY["condition_monitoring"]), "condition_id": _stable_id("condition", run_id, area_id), "asset_id": _area_asset_id(assets_by_area, area_id, 1), "device_id": _area_device_id(devices_by_area, area_id, 1), "window_start_utc": public_stamp(area_start), "window_end_utc": public_stamp(area_end), "health_index": _round(0.88 - AREA_INDEX[area_id] * 0.01 - (0.08 if scenario == "conversion_treating_degradation" and area_id in focus_areas else 0.0)), "vibration_index": _round(0.22 + AREA_INDEX[area_id] * 0.015 + (0.06 if scenario == "conversion_treating_degradation" and area_id in focus_areas else 0.0)), "evidence_telemetry_ids": _choose_telemetry_ids(telemetry, area_id, "condition_index")})

    for area_id in ("A02", "A05", "A08", "A09"):
        area_start, _, area_end = area_windows[area_id]
        monitoring["electrical_monitoring"].append({**_record_common(run_id, scenario_id, site_id, area_id, RECORD_FIDELITY["electrical_monitoring"]), "electrical_id": _stable_id("electrical", run_id, area_id), "asset_id": _area_asset_id(assets_by_area, area_id, 0), "device_id": _area_device_id(devices_by_area, area_id, 0), "window_start_utc": public_stamp(area_start), "window_end_utc": public_stamp(area_end), "voltage_kv": _round(12.4 - (0.7 if scenario == "utility_sag" and area_id in focus_areas else 0.0) - AREA_INDEX[area_id] * 0.05), "current_a": _round(180.0 + AREA_INDEX[area_id] * 11.0 + (25.0 if scenario == "utility_sag" and area_id in focus_areas else 0.0)), "power_kw": _round(360.0 + AREA_INDEX[area_id] * 16.0 - (90.0 if scenario == "utility_sag" and area_id in focus_areas else 0.0)), "evidence_telemetry_ids": _choose_telemetry_ids(telemetry, area_id, "power_kw")})

    for area_id in ("A05", "A07", "A10"):
        _, area_mid, area_end = area_windows[area_id]
        monitoring["environmental_observations"].append({**_record_common(run_id, scenario_id, site_id, area_id, RECORD_FIDELITY["environmental_observations"]), "observation_id": _stable_id("environment", run_id, area_id), "asset_id": _area_asset_id(assets_by_area, area_id, 1), "sample_id": _stable_id("sample", run_id, area_id, "environment"), "event_time_utc": public_stamp(area_mid + timedelta(seconds=18)), "analysis_complete_utc": public_stamp(area_end + timedelta(seconds=25)), "observation_kind": "bounded_monitoring_result", "observation_value": _round(0.31 + AREA_INDEX[area_id] * 0.025 + (0.18 if scenario == "environmental_workflow" and area_id in focus_areas else 0.0)), "observation_state": "workflow_attention" if scenario == "environmental_workflow" and area_id in focus_areas else "observed", "evidence_telemetry_ids": _choose_telemetry_ids(telemetry, area_id, "observation_index")})

    for area_id in ("A06", "A11"):
        area_start, _, area_end = area_windows[area_id]
        monitoring["lab_results"].append({**_record_common(run_id, scenario_id, site_id, area_id, RECORD_FIDELITY["lab_results"]), "lab_result_id": _stable_id("lab", run_id, area_id), "asset_id": _area_asset_id(assets_by_area, area_id, 1), "sample_id": _stable_id("sample", run_id, area_id, "lab"), "product_stream": "fictional_blend_component" if area_id == "A06" else "fictional_context_sample", "sample_time_utc": public_stamp(area_start + timedelta(seconds=15)), "analysis_complete_utc": public_stamp(area_end + timedelta(seconds=30)), "approved_utc": public_stamp(area_end + timedelta(seconds=45)), "result_index": _round(0.69 + AREA_INDEX[area_id] * 0.012 + (0.04 if scenario == "analyzer_delay" and area_id in focus_areas else 0.0)), "result_state": "delayed_for_synthetic_review" if scenario == "analyzer_delay" and area_id in focus_areas else "available_for_synthetic_review"})

    for area_id in ("A03", "A08", "A11"):
        _, area_mid, _ = area_windows[area_id]
        monitoring["maintenance_context"].append({**_record_common(run_id, scenario_id, site_id, area_id, RECORD_FIDELITY["maintenance_context"]), "maintenance_id": _stable_id("maintenance", run_id, area_id), "asset_id": _area_asset_id(assets_by_area, area_id, 1), "work_order_id": _stable_id("workorder", run_id, area_id), "event_time_utc": public_stamp(area_mid + timedelta(seconds=20)), "maintenance_state": "review_requested" if scenario != "normal_run" and area_id in focus_areas else "monitor_only", "inspection_class": "synthetic_window_check", "summary": f"Synthetic maintenance context for {scenario}."})

    for area_id in ("A07", "A06"):
        area_start, _, area_end = area_windows[area_id]
        monitoring["shipment_context"].append({**_record_common(run_id, scenario_id, site_id, area_id, RECORD_FIDELITY["shipment_context"]), "shipment_id": _stable_id("shipment", run_id, area_id), "asset_id": _area_asset_id(assets_by_area, area_id, 0), "transfer_id": _stable_id("transfer", run_id, area_id), "window_start_utc": public_stamp(area_start), "window_end_utc": public_stamp(area_end), "transfer_state": "reconciled" if scenario != "blending_loading_shift" or area_id not in focus_areas else "shifted", "batch_volume_index": _round(0.55 + AREA_INDEX[area_id] * 0.02 + (0.08 if scenario == "blending_loading_shift" and area_id in focus_areas else 0.0)), "evidence_telemetry_ids": _choose_telemetry_ids(telemetry, area_id, "inventory_index")})

    for record_type in MONITORING_RECORD_TYPES:
        expected = NORMAL_MONITORING_COUNTS[record_type]
        if len(monitoring[record_type]) != expected:
            raise ValueError(f"{record_type} count mismatch: expected {expected}, got {len(monitoring[record_type])}")

    private_area_states = []
    for area in AREA_DEFINITIONS:
        area_id = area["area_id"]
        delta = _scenario_physical_delta("normal_run" if scenario == "communications_degradation" else scenario, area_id, scenario != "normal_run")
        private_area_states.append({"area_id": area_id, "throughput_bias": _round(delta["throughput"]), "pressure_bias": _round(delta["pressure"]), "temperature_bias": _round(delta["temperature"]), "power_bias": _round(delta["power"]), "inventory_bias": _round(delta["inventory"]), "quality_bias": _round(delta["quality"]), "condition_bias": _round(delta["condition"]), "utility_bias": _round(delta["utility"]), "availability_bias": _round(delta["availability"]), "observation_bias": _round(delta["observation"]),})
    private_signature = hashlib.sha256(repr(private_area_states).encode("utf-8")).hexdigest()
    ground_truth = [{"schema_version": "1.0", "ground_truth_id": _stable_id("truth", run_id, scenario), "run_id": run_id, "scenario_id": scenario_id, "site_id": site_id, "scenario": scenario, "onset_utc": _stamp(onset), "end_utc": _stamp(end), "causal_area_id": sorted(focus_areas)[0], "affected_area_ids": sorted(focus_areas), "delivery_scenario_only": scenario == "communications_degradation", "actual_process_state_signature": private_signature, "latent_area_states": private_area_states, "is_synthetic": True, "operational_use": "prohibited", "fidelity": "F2"}]
    total_monitoring_records = sum(len(values) for values in monitoring.values())
    return {"scenario_id": scenario_id, "telemetry": telemetry, **monitoring, "ground_truth": ground_truth, "scheduled_telemetry_record_count": len(telemetry), "total_monitoring_record_count": total_monitoring_records}
