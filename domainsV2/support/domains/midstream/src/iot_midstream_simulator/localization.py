"""Telemetry-only synthetic leak localization and delayed confirmation."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any

DETECTOR_MODEL_VERSION = "quasi-steady-spatial-residual-1.0"


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value[:-1] + "+00:00")


def _round(value: float) -> float:
    return round(value, 6)


def _identifier(prefix: str, material: str) -> str:
    return f"{prefix}-{hashlib.sha256(material.encode()).hexdigest()[:16]}"


def _public_configuration_digest(config: dict[str, Any]) -> str:
    localization = {
        key: value
        for key, value in config["localization"].items()
        if key != "hidden_release_route_fraction"
    }
    material = json.dumps(
        {
            "pipeline": config["pipeline"],
            "localization": localization,
        },
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode()).hexdigest()


def evaluate_localization(
    config: dict[str, Any],
    run_id: str,
    scenario_id: str,
    assets: list[dict[str, Any]],
    telemetry: list[dict[str, Any]],
    related_event_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Estimate a sensor interval using delivered telemetry only."""
    detector = next(
        asset for asset in assets if asset["asset_type"] == "leak_detection_engine"
    )
    nodes = sorted(
        (
            asset
            for asset in assets
            if asset["asset_type"] == "pipeline_monitoring_node"
        ),
        key=lambda item: item["route_distance_m"],
    )
    pipeline_id = next(
        asset["asset_id"]
        for asset in assets
        if asset["asset_type"] == "pipeline_segment"
    )
    times = sorted({item["event_time_utc"] for item in telemetry})
    window_times = times[-min(3, len(times)) :]
    evidence = [
        item
        for item in telemetry
        if item["event_time_utc"] in window_times
        and (
            (
                item["asset_id"] == pipeline_id
                and item["measurement"]
                in {"inlet_volume_flow", "outlet_volume_flow"}
            )
            or (
                item["asset_id"] in {node["asset_id"] for node in nodes}
                and item["measurement"] == "line_pressure"
            )
        )
    ]
    expected_count = len(window_times) * (len(nodes) + 2)
    incomplete = (
        len(evidence) != expected_count
        or any(
            item["value"] is None
            or item["quality"]["validity"] != "good"
            or item["clock_state"] != "synchronized"
            for item in evidence
        )
    )
    available_time = max(
        (item["ingest_time_utc"] for item in evidence),
        default=window_times[-1],
    )
    output_id = _identifier("detector", f"{run_id}|{DETECTOR_MODEL_VERSION}")
    base = {
        "schema_version": "1.0",
        "detector_output_id": output_id,
        "run_id": run_id,
        "scenario_id": scenario_id,
        "detector_id": detector["asset_id"],
        "method": "quasi_steady_pressure_gradient_and_flow_balance",
        "model_version": DETECTOR_MODEL_VERSION,
        "model_configuration_digest": _public_configuration_digest(config),
        "evidence_window_start_utc": window_times[0],
        "evidence_window_end_utc": window_times[-1],
        "detection_time_utc": window_times[-1],
        "available_time_utc": available_time,
        "evidence_telemetry_ids": sorted(
            item["telemetry_id"] for item in evidence
        ),
        "related_event_ids": [related_event_id],
        "estimated_segment_id": None,
        "candidate_segment_ids": [],
        "estimated_route_distance_m": None,
        "lower_route_distance_m": None,
        "upper_route_distance_m": None,
        "confidence_class": "unavailable",
        "confidence_basis": [],
        "residual_value": None,
        "residual_unit": "m3/s",
        "abstention_reason": None,
        "quality": {"validity": "good", "reasons": []},
        "fidelity": "quasi_steady_spatial_estimate_not_transient_wave",
        "is_synthetic": True,
        "operational_use": "prohibited",
    }

    if not config["localization"]["enabled"]:
        state = "abstained"
        base.update(
            {
                "detection_state": state,
                "abstention_reason": "synthetic_localization_disabled",
                "quality": {
                    "validity": "questionable",
                    "reasons": ["synthetic_localization_disabled"],
                },
            }
        )
    elif incomplete:
        state = "abstained"
        base.update(
            {
                "detection_state": state,
                "abstention_reason": "insufficient_synchronized_good_evidence",
                "quality": {
                    "validity": "questionable",
                    "reasons": ["incomplete_or_unsynchronized_evidence"],
                },
            }
        )
    else:
        inlet = [
            item["value"]
            for item in evidence
            if item["asset_id"] == pipeline_id
            and item["measurement"] == "inlet_volume_flow"
        ]
        outlet = [
            item["value"]
            for item in evidence
            if item["asset_id"] == pipeline_id
            and item["measurement"] == "outlet_volume_flow"
        ]
        imbalance = sum(inlet) / len(inlet) - sum(outlet) / len(outlet)
        flow_uncertainty = (
            config["pump"]["rated_flow_m3_s"]
            * config["localization"]["flow_measurement_relative_uncertainty"]
        )
        synthetic_noise_floor = 3.0 * math.sqrt(2.0) * flow_uncertainty
        base["residual_value"] = _round(imbalance)
        if imbalance <= synthetic_noise_floor:
            state = "clear"
            base.update(
                {
                    "detection_state": state,
                    "confidence_class": "not_applicable",
                    "confidence_basis": [
                        "flow_balance_within_configured_synthetic_noise_floor"
                    ],
                }
            )
        else:
            average_pressures: list[float] = []
            for node in nodes:
                values = [
                    item["value"]
                    for item in evidence
                    if item["asset_id"] == node["asset_id"]
                ]
                average_pressures.append(sum(values) / len(values))
            route_length = nodes[-1]["route_distance_m"]
            endpoint_drop = average_pressures[0] - average_pressures[-1]
            residuals = [
                pressure
                - (
                    average_pressures[0]
                    - endpoint_drop * node["route_distance_m"] / route_length
                )
                for node, pressure in zip(nodes, average_pressures)
            ]
            change_index = min(
                range(1, len(nodes) - 1),
                key=lambda index: residuals[index],
            )
            lower = nodes[change_index - 1]["route_distance_m"]
            upper = nodes[change_index + 1]["route_distance_m"]
            estimate = nodes[change_index]["route_distance_m"]
            route_id = nodes[0]["route_id"]
            candidate_segments = [
                f"{route_id}-segment-{change_index:03d}",
                f"{route_id}-segment-{change_index + 1:03d}",
            ]
            state = "estimated"
            base.update(
                {
                    "detection_state": state,
                    "estimated_segment_id": candidate_segments[0],
                    "candidate_segment_ids": candidate_segments,
                    "estimated_route_distance_m": _round(estimate),
                    "lower_route_distance_m": _round(lower),
                    "upper_route_distance_m": _round(upper),
                    "confidence_class": "synthetic_medium",
                    "confidence_basis": [
                        "flow_imbalance_above_configured_synthetic_noise_floor",
                        "pressure_profile_change_point",
                        "all_evidence_synchronized_and_good",
                    ],
                }
            )

    event_type = {
        "estimated": "leak_location_estimated",
        "abstained": "leak_location_abstained",
        "clear": "leak_detector_clear",
    }[state]
    detector_event_id = _identifier("event", f"{output_id}|{event_type}")
    detector_event = {
        "schema_version": "1.0",
        "event_id": detector_event_id,
        "run_id": run_id,
        "scenario_id": scenario_id,
        "asset_id": detector["asset_id"],
        "event_time_utc": base["detection_time_utc"],
        "event_type": event_type,
        "severity": "warning" if state == "estimated" else "information",
        "state_transition": {"from": "monitoring", "to": state},
        "related_detector_output_id": output_id,
        "is_synthetic": True,
        "operational_use": "prohibited",
    }
    base["related_event_ids"].append(detector_event_id)
    detector_log = {
        "schema_version": "1.0",
        "log_id": _identifier("log", f"{output_id}|published"),
        "run_id": run_id,
        "scenario_id": scenario_id,
        "asset_id": detector["asset_id"],
        "event_time_utc": base["detection_time_utc"],
        "source_time_utc": base["detection_time_utc"],
        "ingest_time_utc": base["available_time_utc"],
        "sequence": 1,
        "level": "warning" if state == "estimated" else "info",
        "category": "detector_health",
        "code": f"DETECTOR_{state.upper()}",
        "message": f"Synthetic localization detector state: {state}.",
        "clock_state": "synchronized" if not incomplete else "degraded",
        "clock_offset_ms": config["localization"]["clock_offset_ms"],
        "clock_uncertainty_ms": config["localization"]["clock_uncertainty_ms"],
        "communications_latency_ms": 25.0,
        "queue_depth": 0,
        "related_event_id": detector_event_id,
        "related_detector_output_id": output_id,
        "quality": base["quality"],
        "is_synthetic": True,
        "operational_use": "prohibited",
    }
    return base, detector_event, detector_log


def build_synthetic_confirmation(
    config: dict[str, Any],
    detector_output: dict[str, Any],
    ground_truth: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Create delayed confirmation after detector evaluation has completed."""
    actual_distance = ground_truth["actual_route_distance_m"]
    if actual_distance is None:
        return [], [], []
    detector_time = _parse(detector_output["detection_time_utc"])
    confirmation_time = detector_time + timedelta(
        seconds=config["localization"]["confirmation_delay_seconds"]
    )
    confirmation_id = _identifier(
        "confirmation",
        f"{ground_truth['ground_truth_id']}|{_stamp(confirmation_time)}",
    )
    event_id = _identifier("event", f"{confirmation_id}|recorded")
    confirmation = {
        "schema_version": "1.0",
        "confirmation_id": confirmation_id,
        "run_id": detector_output["run_id"],
        "scenario_id": detector_output["scenario_id"],
        "dispatch_time_utc": detector_output["detection_time_utc"],
        "confirmation_time_utc": _stamp(confirmation_time),
        "available_after_utc": _stamp(confirmation_time),
        "inspection_method": "synthetic_route_inspection",
        "inspection_coverage": "configured_fictional_route",
        "result": "confirmed",
        "confirmed_segment_id": ground_truth["actual_segment_id"],
        "confirmed_route_distance_m": actual_distance,
        "lower_route_distance_m": actual_distance,
        "upper_route_distance_m": actual_distance,
        "observation_quality": "synthetic_exact_inside_scenario_only",
        "related_detector_output_ids": [
            detector_output["detector_output_id"]
        ],
        "related_event_ids": [event_id],
        "fidelity": "synthetic_confirmation_not_field_evidence",
        "is_synthetic": True,
        "operational_use": "prohibited",
    }
    event = {
        "schema_version": "1.0",
        "event_id": event_id,
        "run_id": detector_output["run_id"],
        "scenario_id": detector_output["scenario_id"],
        "asset_id": detector_output["detector_id"],
        "event_time_utc": confirmation["confirmation_time_utc"],
        "event_type": "synthetic_physical_confirmation_recorded",
        "severity": "information",
        "state_transition": {"from": "estimated", "to": "synthetically_confirmed"},
        "related_detector_output_id": detector_output["detector_output_id"],
        "is_synthetic": True,
        "operational_use": "prohibited",
    }
    log = {
        "schema_version": "1.0",
        "log_id": _identifier("log", f"{confirmation_id}|available"),
        "run_id": detector_output["run_id"],
        "scenario_id": detector_output["scenario_id"],
        "asset_id": detector_output["detector_id"],
        "event_time_utc": confirmation["confirmation_time_utc"],
        "source_time_utc": confirmation["confirmation_time_utc"],
        "ingest_time_utc": confirmation["available_after_utc"],
        "sequence": 1,
        "level": "info",
        "category": "synthetic_confirmation",
        "code": "SYNTHETIC_CONFIRMATION_RECORDED",
        "message": "Delayed fictional route inspection result became available.",
        "clock_state": "synchronized",
        "clock_offset_ms": 0.0,
        "clock_uncertainty_ms": 0.0,
        "communications_latency_ms": 0.0,
        "queue_depth": 0,
        "related_event_id": event_id,
        "related_detector_output_id": detector_output["detector_output_id"],
        "quality": {"validity": "good", "reasons": []},
        "is_synthetic": True,
        "operational_use": "prohibited",
    }
    return [confirmation], [event], [log]
