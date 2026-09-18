"""Causal physics-lite state, measurement, and delivery simulation."""

from __future__ import annotations

import hashlib
import math
import random
from datetime import datetime, timedelta, timezone
from typing import Any


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _round(value: float) -> float:
    return round(value, 6)


def _noise(
    seed: int,
    asset_id: str,
    measurement: str,
    sequence: int,
    scale: float,
) -> float:
    material = f"{seed}|{asset_id}|{measurement}|{sequence}".encode()
    local = random.Random(int.from_bytes(hashlib.sha256(material).digest()[:8], "big"))
    return local.uniform(-scale, scale)


def _measured(
    seed: int,
    asset_id: str,
    measurement: str,
    sequence: int,
    latent: float,
    uncertainty: float,
) -> float:
    if latent == 0.0:
        return 0.0
    bias = _noise(seed, asset_id, f"{measurement}_bias", 0, uncertainty * 0.45)
    jitter = _noise(seed, asset_id, f"{measurement}_jitter", sequence, uncertainty * 0.45)
    return latent + bias + jitter


def _friction_loss_kpa(
    pipe: dict[str, float],
    density: float,
    viscosity: float,
    flow: float,
    length_m: float,
) -> float:
    if flow <= 0.0 or length_m <= 0.0:
        return 0.0
    area = math.pi * (pipe["diameter_m"] / 2.0) ** 2
    velocity = flow / area
    reynolds = density * velocity * pipe["diameter_m"] / viscosity
    friction = 64.0 / reynolds if reynolds < 2300 else 0.3164 / reynolds**0.25
    return (
        friction
        * (length_m / pipe["diameter_m"])
        * (density * velocity * velocity / 2.0)
        / 1000.0
    )


def _pressure_profile(
    pipe: dict[str, float],
    density: float,
    viscosity: float,
    inlet_pressure_kpa: float,
    inlet_flow_m3_s: float,
    outlet_flow_m3_s: float,
    node_distances_m: list[float],
    release_distance_m: float | None,
) -> list[float]:
    pressures = [inlet_pressure_kpa]
    for start_m, end_m in zip(node_distances_m, node_distances_m[1:]):
        segment_length = end_m - start_m
        if release_distance_m is None:
            upstream_length = segment_length
        else:
            upstream_length = min(
                segment_length,
                max(0.0, release_distance_m - start_m),
            )
        downstream_length = segment_length - upstream_length
        loss = _friction_loss_kpa(
            pipe,
            density,
            viscosity,
            inlet_flow_m3_s,
            upstream_length,
        ) + _friction_loss_kpa(
            pipe,
            density,
            viscosity,
            outlet_flow_m3_s,
            downstream_length,
        )
        pressures.append(max(0.0, pressures[-1] - loss))
    return pressures


def simulate(
    config: dict[str, Any],
    run_id: str,
    assets: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    assets_by_type: dict[str, list[dict[str, Any]]] = {}
    for asset in assets:
        assets_by_type.setdefault(asset["asset_type"], []).append(asset)
    single_id = {
        asset_type: typed_assets[0]["asset_id"]
        for asset_type, typed_assets in assets_by_type.items()
        if len(typed_assets) == 1
    }
    asset_type_by_id = {
        asset["asset_id"]: asset["asset_type"]
        for asset in assets
    }
    monitoring_nodes = sorted(
        assets_by_type.get("pipeline_monitoring_node", []),
        key=lambda item: item["route_distance_m"],
    )
    node_distances = [item["route_distance_m"] for item in monitoring_nodes]
    route_id = monitoring_nodes[0]["route_id"]

    start = datetime.fromisoformat(config["simulation"]["start_utc"][:-1] + "+00:00")
    end = datetime.fromisoformat(config["simulation"]["end_utc"][:-1] + "+00:00")
    dt = config["simulation"]["interval_seconds"]
    steps = int((end - start).total_seconds() / dt) + 1
    onset_index = max(1, steps // 3)
    onset = start + timedelta(seconds=onset_index * dt)
    scenario = config["scenario"]
    scenario_id = (
        f"scenario-{hashlib.sha256(f'{run_id}|{scenario}'.encode()).hexdigest()[:16]}"
    )
    event_id = (
        f"event-{hashlib.sha256(f'{scenario_id}|onset'.encode()).hexdigest()[:16]}"
    )
    causal_asset_types = {
        "normal_transfer": "pump_driver",
        "pump_degradation": "pump_driver",
        "valve_restriction": "control_valve",
        "pipeline_withdrawal": "pipeline_segment",
        "tank_sensor_drift": "storage_tank",
        "communications_failure": "rtu_gateway",
        "power_loss_shutdown": "pump_driver",
        "batch_property_change": "receipt_terminal",
    }
    event = {
        "schema_version": "1.0",
        "event_id": event_id,
        "run_id": run_id,
        "scenario_id": scenario_id,
        "asset_id": single_id[causal_asset_types[scenario]],
        "event_time_utc": _stamp(onset),
        "event_type": scenario,
        "severity": "information" if scenario == "normal_transfer" else "warning",
        "state_transition": {"from": "nominal", "to": scenario},
        "related_detector_output_id": None,
        "is_synthetic": True,
        "operational_use": "prohibited",
    }

    localization = config["localization"]
    release_distance = (
        config["pipeline"]["length_m"]
        * localization["hidden_release_route_fraction"]
        if scenario == "pipeline_withdrawal"
        else None
    )
    release_segment_id = None
    if release_distance is not None:
        interval_count = len(node_distances) - 1
        segment_number = min(
            interval_count,
            int(localization["hidden_release_route_fraction"] * interval_count) + 1,
        )
        release_segment_id = f"{route_id}-segment-{segment_number:03d}"

    affected_assets = sorted(
        asset["asset_id"]
        for asset in assets
        if asset["asset_type"]
        not in {"pipeline_monitoring_node", "leak_detection_engine"}
    )
    ground_truth = {
        "schema_version": "1.0",
        "ground_truth_id": (
            f"truth-{hashlib.sha256(scenario_id.encode()).hexdigest()[:16]}"
        ),
        "run_id": run_id,
        "scenario_id": scenario_id,
        "scenario": scenario,
        "injected_cause": scenario.replace("_", " "),
        "onset_utc": _stamp(onset),
        "end_utc": config["simulation"]["end_utc"],
        "causal_asset_id": event["asset_id"],
        "affected_asset_ids": affected_assets,
        "correlated_event_ids": [event_id],
        "route_id": route_id,
        "actual_segment_id": release_segment_id,
        "actual_route_distance_m": _round(release_distance)
        if release_distance is not None
        else None,
        "injected_withdrawal_rate_m3_s": None,
        "difficulty_factors": {
            "clock_offset_ms": localization["clock_offset_ms"],
            "clock_uncertainty_ms": localization["clock_uncertainty_ms"],
            "measurement_noise": True,
            "communications_degradation": scenario == "communications_failure",
        },
        "is_synthetic": True,
        "operational_use": "prohibited",
        "fidelity": (
            "quasi_steady_spatial_fixture_not_calibrated_leak_transient"
            if scenario == "pipeline_withdrawal"
            else "physics_lite_synthetic"
        ),
    }

    base_log_fields = {
        "schema_version": "1.0",
        "run_id": run_id,
        "scenario_id": scenario_id,
        "ingest_time_utc": event["event_time_utc"],
        "sequence": 1,
        "clock_state": "synchronized",
        "clock_offset_ms": localization["clock_offset_ms"],
        "clock_uncertainty_ms": localization["clock_uncertainty_ms"],
        "communications_latency_ms": 25.0,
        "queue_depth": 0,
        "related_detector_output_id": None,
        "quality": {"validity": "good", "reasons": []},
        "is_synthetic": True,
        "operational_use": "prohibited",
    }
    device_logs: list[dict[str, Any]] = [
        {
            **base_log_fields,
            "log_id": f"log-{hashlib.sha256(event_id.encode()).hexdigest()[:16]}",
            "asset_id": event["asset_id"],
            "event_time_utc": event["event_time_utc"],
            "source_time_utc": event["event_time_utc"],
            "level": "info" if scenario == "normal_transfer" else "warning",
            "category": "synthetic_process",
            "code": scenario.upper(),
            "message": f"Synthetic {scenario.replace('_', ' ')} state entered.",
            "related_event_id": event_id,
        }
    ]
    for index, node in enumerate(monitoring_nodes, 2):
        node_offset = localization["clock_offset_ms"] + _noise(
            config["seed"],
            node["asset_id"],
            "clock_offset",
            0,
            localization["clock_uncertainty_ms"],
        )
        device_logs.append(
            {
                **base_log_fields,
                "log_id": (
                    "log-"
                    + hashlib.sha256(
                        f"{run_id}|{node['asset_id']}|sampling".encode()
                    ).hexdigest()[:16]
                ),
                "asset_id": node["asset_id"],
                "event_time_utc": _stamp(start),
                "source_time_utc": _stamp(start),
                "ingest_time_utc": _stamp(start),
                "sequence": index,
                "clock_offset_ms": _round(node_offset),
                "level": "info",
                "category": "sensor_health",
                "code": "SENSOR_STREAM_STARTED",
                "message": "Synthetic pressure monitoring stream started.",
                "related_event_id": None,
            }
        )
    detector_id = single_id["leak_detection_engine"]
    device_logs.append(
        {
            **base_log_fields,
            "log_id": (
                "log-"
                + hashlib.sha256(f"{run_id}|detector|loaded".encode()).hexdigest()[:16]
            ),
            "asset_id": detector_id,
            "event_time_utc": _stamp(start),
            "source_time_utc": _stamp(start),
            "ingest_time_utc": _stamp(start),
            "sequence": len(device_logs) + 1,
            "level": "info",
            "category": "detector_health",
            "code": "DETECTOR_MODEL_LOADED",
            "message": "Synthetic quasi-steady spatial residual detector loaded.",
            "related_event_id": None,
        }
    )
    if scenario == "communications_failure":
        device_logs.append(
            {
                **base_log_fields,
                "log_id": (
                    "log-"
                    + hashlib.sha256(f"{run_id}|rtu|degraded".encode()).hexdigest()[:16]
                ),
                "asset_id": single_id["rtu_gateway"],
                "event_time_utc": _stamp(onset),
                "source_time_utc": _stamp(onset),
                "ingest_time_utc": _stamp(onset + timedelta(seconds=dt)),
                "sequence": len(device_logs) + 1,
                "clock_state": "holdover",
                "communications_latency_ms": float(dt * 1000),
                "queue_depth": len(monitoring_nodes) * 2,
                "level": "warning",
                "category": "communications",
                "code": "RTU_LINK_DEGRADED",
                "message": "Synthetic telemetry link entered intermittent delivery.",
                "related_event_id": event_id,
                "quality": {
                    "validity": "questionable",
                    "reasons": ["synthetic_communications_outage"],
                },
            }
        )

    fluid = config["fluid"]
    pipe = config["pipeline"]
    pump = config["pump"]
    tank = config["tank"]
    density_base = fluid["density_kg_m3"]
    viscosity = fluid["viscosity_pa_s"]
    tank_area = math.pi * (tank["diameter_m"] / 2.0) ** 2
    volume = tank["initial_volume_m3"]
    bearing_temp = config["site"]["ambient_temperature_c"]
    pressure_uncertainty = localization["pressure_measurement_uncertainty_kpa"]
    flow_uncertainty = (
        pump["rated_flow_m3_s"]
        * localization["flow_measurement_relative_uncertainty"]
    )
    telemetry: list[dict[str, Any]] = []
    sequence = 0
    previous_delivered: dict[tuple[str, str], tuple[float, datetime]] = {}

    def emit(
        asset_id: str,
        measurement: str,
        observed: float | None,
        unit: str,
        uncertainty: float | None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        nonlocal sequence
        sequence += 1
        quality, reasons, source_at = "good", [], at
        if (
            scenario == "tank_sensor_drift"
            and at >= onset
            and measurement == "liquid_level"
        ):
            quality, reasons = "questionable", ["synthetic_sensor_drift"]
        affected_by_comms = asset_type_by_id[asset_id] in {
            "pipeline_segment",
            "pipeline_monitoring_node",
            "metering_skid",
        }
        if scenario == "communications_failure" and at >= onset and affected_by_comms:
            if int((at - start).total_seconds() / dt) % 2:
                observed = None
                source_at = None
                quality, reasons = "missing", ["synthetic_communications_outage"]
            else:
                previous = previous_delivered.get((asset_id, measurement))
                if previous is not None:
                    observed, source_at = previous
                    quality, reasons = "stale", ["synthetic_store_and_forward"]
        record_id = hashlib.sha256(
            f"{run_id}|{asset_id}|{measurement}|{sequence}".encode()
        ).hexdigest()[:20]
        record = {
            "schema_version": "1.0",
            "telemetry_id": f"telemetry-{record_id}",
            "run_id": run_id,
            "scenario_id": scenario_id,
            "asset_id": asset_id,
            "event_time_utc": _stamp(at),
            "source_time_utc": _stamp(source_at)
            if observed is not None and source_at is not None
            else None,
            "ingest_time_utc": _stamp(at),
            "measurement": measurement,
            "value": _round(observed) if observed is not None else None,
            "unit": unit,
            "measurement_uncertainty": _round(uncertainty)
            if uncertainty is not None
            else None,
            "sample_interval_ms": dt * 1000,
            "clock_state": "holdover"
            if scenario == "communications_failure" and at >= onset
            else "synchronized",
            "clock_offset_ms": localization["clock_offset_ms"],
            "clock_uncertainty_ms": localization["clock_uncertainty_ms"],
            "route_id": None,
            "route_distance_m": None,
            "segment_id": None,
            "segment_start_distance_m": None,
            "segment_end_distance_m": None,
            "sequence": sequence,
            "quality": {"validity": quality, "reasons": reasons},
            "is_synthetic": True,
            "operational_use": "prohibited",
        }
        if extra:
            record.update(extra)
        telemetry.append(record)
        if quality == "good" and observed is not None:
            previous_delivered[(asset_id, measurement)] = (observed, at)
        return record

    pipeline_id = single_id["pipeline_segment"]
    meter_id = single_id["metering_skid"]
    boundary_id = single_id["refinery_receiving_boundary"]
    for index in range(steps):
        at = start + timedelta(seconds=index * dt)
        progress = max(0.0, (index - onset_index) / max(1, steps - onset_index - 1))
        active = index >= onset_index
        speed_ratio = 1.0
        valve_position = 100.0
        efficiency = pump["efficiency"]
        flow = pump["rated_flow_m3_s"] * 0.82
        density = density_base
        withdrawal = 0.0
        if scenario == "pump_degradation" and active:
            efficiency *= 1.0 - 0.18 * progress
        elif scenario == "valve_restriction" and active:
            valve_position = 55.0
            flow *= 0.63
        elif scenario == "pipeline_withdrawal" and active:
            withdrawal = min(flow * 0.08, 0.02)
        elif scenario == "power_loss_shutdown" and active:
            elapsed = (index - onset_index) * dt
            speed_ratio = math.exp(-elapsed / pump["coast_down_time_constant_s"])
            if speed_ratio < 0.01:
                speed_ratio = 0.0
            flow *= speed_ratio
        elif scenario == "batch_property_change" and active:
            density *= 1.0 + 0.045 * min(1.0, progress * 2.0)

        outflow = max(0.0, flow - withdrawal)
        head = (
            pump["rated_head_m"]
            * speed_ratio
            * speed_ratio
            * max(0.1, 1.0 - 0.25 * (flow / pump["rated_flow_m3_s"]) ** 2)
        )
        hydraulic_kw = density * 9.80665 * flow * head / 1000.0
        power_kw = hydraulic_kw / efficiency if efficiency and flow else 0.0
        inlet_pressure = (
            pipe["reference_pressure_kpa"] + density * 9.80665 * head / 1000.0
        )
        latent_pressures = _pressure_profile(
            pipe,
            density,
            viscosity,
            inlet_pressure,
            flow,
            outflow,
            node_distances,
            release_distance if active else None,
        )
        common_pressure_noise = _noise(
            config["seed"],
            pipeline_id,
            "pressure_process_noise",
            index,
            pressure_uncertainty * 0.2,
        )
        observed_pressures = [
            _measured(
                config["seed"],
                node["asset_id"],
                "line_pressure",
                index,
                latent + common_pressure_noise,
                pressure_uncertainty,
            )
            for node, latent in zip(monitoring_nodes, latent_pressures)
        ]
        inlet_flow_observed = _measured(
            config["seed"],
            pipeline_id,
            "inlet_volume_flow",
            index,
            flow,
            flow_uncertainty,
        )
        outlet_flow_observed = _measured(
            config["seed"],
            pipeline_id,
            "outlet_volume_flow",
            index,
            outflow,
            flow_uncertainty,
        )
        measured_imbalance = inlet_flow_observed - outlet_flow_observed
        if scenario == "pipeline_withdrawal" and active:
            ground_truth["injected_withdrawal_rate_m3_s"] = _round(withdrawal)

        next_volume = volume - flow * dt
        if next_volume < 0:
            raise ValueError("tank inventory would become negative")
        volume = next_volume
        level = volume / tank_area
        degradation = progress if scenario == "pump_degradation" and active else 0.0
        target_temp = (
            config["site"]["ambient_temperature_c"]
            + 18.0 * (power_kw / max(power_kw, 1.0))
            + 22.0 * degradation
        )
        alpha = 1.0 - math.exp(-dt / pump["thermal_time_constant_s"])
        bearing_temp += (target_temp - bearing_temp) * alpha
        vibration = 1.4 + 3.2 * degradation

        latent_values = [
            (single_id["storage_tank"], "liquid_level", level, "m"),
            (single_id["storage_tank"], "gross_volume", volume, "m3"),
            (single_id["control_valve"], "command_position", 100.0, "%"),
            (single_id["control_valve"], "feedback_position", valve_position, "%"),
            (
                single_id["pump_driver"],
                "speed",
                pump["rated_speed_rpm"] * speed_ratio,
                "rpm",
            ),
            (single_id["pump_driver"], "active_power", power_kw, "kW"),
            (
                single_id["pump_driver"],
                "vibration_velocity_rms",
                vibration,
                "mm/s",
            ),
            (
                single_id["pump_driver"],
                "bearing_temperature",
                bearing_temp,
                "Cel",
            ),
            (single_id["pig_receiver"], "pig_present", 0.0, "1"),
            (single_id["rtu_gateway"], "online_state", 1.0, "1"),
        ]
        for asset_id, measurement, latent, unit in latent_values:
            uncertainty = max(abs(latent) * 0.0005, 0.00001)
            observed = (
                latent
                if latent == 0.0 or unit in {"1", "%"}
                else _measured(
                    config["seed"],
                    asset_id,
                    measurement,
                    index,
                    latent,
                    uncertainty,
                )
            )
            if (
                scenario == "tank_sensor_drift"
                and active
                and measurement == "liquid_level"
            ):
                observed += 0.4 * progress
            emit(asset_id, measurement, observed, unit, uncertainty)

        route_fields = {
            "route_id": route_id,
            "segment_id": None,
            "segment_start_distance_m": 0.0,
            "segment_end_distance_m": pipe["length_m"],
        }
        emit(
            pipeline_id,
            "inlet_pressure",
            observed_pressures[0],
            "kPa",
            pressure_uncertainty,
            route_fields,
        )
        emit(
            pipeline_id,
            "outlet_pressure",
            observed_pressures[-1],
            "kPa",
            pressure_uncertainty,
            route_fields,
        )
        emit(
            pipeline_id,
            "inlet_volume_flow",
            inlet_flow_observed,
            "m3/s",
            flow_uncertainty,
            route_fields,
        )
        emit(
            pipeline_id,
            "outlet_volume_flow",
            outlet_flow_observed,
            "m3/s",
            flow_uncertainty,
            route_fields,
        )
        emit(
            pipeline_id,
            "volumetric_flow",
            outlet_flow_observed,
            "m3/s",
            flow_uncertainty,
            route_fields,
        )
        emit(
            pipeline_id,
            "calculated_imbalance",
            measured_imbalance,
            "m3/s",
            flow_uncertainty * math.sqrt(2.0),
            route_fields,
        )
        for node, pressure in zip(monitoring_nodes, observed_pressures):
            emit(
                node["asset_id"],
                "line_pressure",
                pressure,
                "kPa",
                pressure_uncertainty,
                {
                    "route_id": route_id,
                    "route_distance_m": node["route_distance_m"],
                    "segment_id": None,
                    "segment_start_distance_m": None,
                    "segment_end_distance_m": None,
                },
            )
        meter_flow = _measured(
            config["seed"],
            meter_id,
            "observed_volume_flow",
            index,
            outflow,
            flow_uncertainty,
        )
        emit(
            meter_id,
            "observed_volume_flow",
            meter_flow,
            "m3/s",
            flow_uncertainty,
        )
        emit(
            meter_id,
            "mass_flow",
            meter_flow * density,
            "kg/s",
            flow_uncertainty * density,
        )
        emit(
            meter_id,
            "flowing_density",
            _measured(
                config["seed"],
                meter_id,
                "flowing_density",
                index,
                density,
                max(density * 0.0005, 0.00001),
            ),
            "kg/m3",
            max(density * 0.0005, 0.00001),
        )
        emit(
            boundary_id,
            "receipt_pressure",
            observed_pressures[-1],
            "kPa",
            pressure_uncertainty,
        )
        emit(
            boundary_id,
            "receipt_flow",
            _measured(
                config["seed"],
                boundary_id,
                "receipt_flow",
                index,
                outflow,
                flow_uncertainty,
            ),
            "m3/s",
            flow_uncertainty,
        )

    telemetry.sort(
        key=lambda item: (
            item["event_time_utc"],
            item["asset_id"],
            item["measurement"],
        )
    )
    device_logs.sort(
        key=lambda item: (
            item["event_time_utc"],
            item["asset_id"],
            item["code"],
        )
    )
    return telemetry, [event], device_logs, [ground_truth]
