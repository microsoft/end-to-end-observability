"""Causal, deterministic physics-lite telemetry and scenario generation."""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .catalog import area_location_ref
from .config import ConfigError
from .incident import (
    ACTIVE_OUTAGE_STATUSES,
    INCIDENT_SCHEMA_VERSION,
    incident_status_at,
)
from .model_state import normalize_model_state

TELEMETRY_SCHEMA_VERSION = "1.0"
EVENT_SCHEMA_VERSION = "1.0"
GROUND_TRUTH_SCHEMA_VERSION = "1.0"
ALERT_SCHEMA_VERSION = "1.0"
DEVICE_LOG_SCHEMA_VERSION = "1.0"

ELECTRICAL_MEASUREMENTS = {
    "active_power",
    "reactive_power",
    "apparent_power",
    "power_factor",
    "current",
    "voltage",
    "frequency",
}

CLASS_SPECIFIC_MEASUREMENTS = {
    "grid_source": {"source_available", "frequency_deviation"},
    "substation": {
        "station_service_voltage",
        "station_dc_voltage",
        "enclosure_temperature",
        "time_sync_state",
    },
    "substation_transformer_ltc": {
        "top_oil_temperature",
        "winding_hot_spot_temperature",
        "loading",
        "condition_index",
        "cooling_stage",
        "fan_run_state",
        "oil_level",
        "moisture",
        "ltc_tap_position",
    },
    "weather_station": {
        "air_temperature",
        "dew_point_temperature",
        "relative_humidity",
        "wind_speed",
        "wind_gust",
        "wind_direction",
        "barometric_pressure",
        "precipitation",
        "solar_irradiance",
        "sensor_battery_voltage",
    },
    "feeder_relay": {
        "trip_state",
        "pickup_state",
        "self_test_state",
        "station_dc_voltage",
        "operation_count",
        "time_sync_state",
    },
    "feeder_meter": {
        "demand_active_power",
        "voltage_thd",
        "voltage_unbalance",
        "time_sync_state",
    },
    "recloser": {
        "closed_state",
        "lockout_state",
        "reclose_count",
        "operation_count",
        "control_battery_voltage",
        "local_remote_state",
    },
    "regulator": {
        "tap_position",
        "automatic_mode",
        "motor_current",
        "operation_count",
    },
    "capacitor_bank": {
        "switch_state",
        "automatic_mode",
        "neutral_unbalance_current",
        "operation_count",
    },
    "power_quality_monitor": {
        "voltage_thd",
        "current_thd",
        "voltage_unbalance",
        "sag_state",
        "swell_state",
        "interruption_state",
        "time_sync_state",
    },
    "distribution_transformer": {
        "top_oil_temperature",
        "winding_hot_spot_temperature",
        "loading",
        "condition_index",
        "cooling_stage",
        "fan_run_state",
        "oil_level",
        "moisture",
    },
    "ami_smart_meter": {
        "interval_energy",
        "interval_import_energy",
        "interval_export_energy",
        "demand_active_power",
        "outage_state",
        "reverse_energy_state",
        "communications_signal_strength",
    },
    "pv_inverter": {
        "solar_irradiance",
        "dc_voltage",
        "dc_current",
        "dc_power",
        "heatsink_temperature",
        "operating_state",
        "curtailment_state",
    },
    "evse": {
        "commanded_current",
        "charging_state",
        "vehicle_present_state",
        "contactor_state",
        "connector_temperature",
        "session_energy",
        "ground_fault_state",
    },
}

SCENARIO_OUTCOMES = {
    "normal": [
        "seasonal and local diurnal weather variation",
        "temperature-sensitive diversified demand",
        "no injected incident",
    ],
    "heatwave": [
        "elevated ambient temperature",
        "increased temperature-sensitive demand and loading",
        "voltage stress and delayed transformer temperature response",
    ],
    "storm_outage": [
        "wind and precipitation precede protective opening",
        "downstream voltage and power become electrical zero",
        "AMI last-gasp events and staged branch restoration",
    ],
    "transformer_degradation": [
        "selected transformer condition indicator increases",
        "temperature and loading stress produce warning events",
        "degradation label does not guarantee failure",
    ],
    "communications_failure": [
        "affected telemetry becomes missing, stale, or late",
        "missing values remain null rather than electrical zero",
        "physical load and topology remain energized",
    ],
}


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value[:-1] + "+00:00")


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean_float(value: float) -> float:
    rounded = round(float(value), 6)
    return 0.0 if rounded == 0 else rounded


def _noise(seed: int, *parts: object) -> float:
    payload = "|".join([str(seed), *(str(part) for part in parts)]).encode("utf-8")
    integer = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
    return integer / float(2**64 - 1)


def _good_quality(*reasons: str) -> dict[str, Any]:
    return {"validity": "good", "source": "simulator", "reasons": list(reasons)}


def _active_storm_impacts(
    schedule: dict[str, Any], timestamp: datetime
) -> list[dict[str, Any]]:
    return [
        impact
        for impact in schedule["storm_impacts"]
        if impact["arrival"] <= timestamp < impact["departure"]
    ]


def _storm_impact_for_location(
    schedule: dict[str, Any], location_ref: str, timestamp: datetime
) -> dict[str, Any] | None:
    return next(
        (
            impact
            for impact in _active_storm_impacts(schedule, timestamp)
            if impact["location_ref"] == location_ref
        ),
        None,
    )


def build_schedule(
    config: dict[str, Any], run_id: str, context: dict[str, Any]
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """Create deterministic scenario timing, events, and joinable ground truth."""
    start = _parse_utc(config["simulation"]["start_utc"])
    end = _parse_utc(config["simulation"]["end_utc"])
    step = timedelta(minutes=config["simulation"]["interval_minutes"])
    count = int((end - start) / step)
    onset_index = max(1, count // 3) if count > 1 else 0
    end_index = min(count, max(onset_index + 1, (3 * count) // 4))
    onset = start + onset_index * step
    incident_end = start + end_index * step
    scenario = config["scenario"]
    if scenario == "storm_outage":
        end_index = min(end_index, count - 2)
        incident_end = start + end_index * step
    scenario_id = f"scenario-{run_id}-{scenario}"
    events: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []
    incidents: list[dict[str, Any]] = []
    event_counter = 0

    def event(
        timestamp: datetime,
        event_type: str,
        severity: str,
        asset_id: str | None,
        before: str,
        after: str,
        causation_id: str | None = None,
        correlation_id: str | None = None,
    ) -> str:
        nonlocal event_counter
        event_counter += 1
        event_id = f"event-{run_id}-{event_counter:04d}"
        events.append(
            {
                "schema_version": EVENT_SCHEMA_VERSION,
                "event_id": event_id,
                "run_id": run_id,
                "scenario_id": scenario_id,
                "asset_id": asset_id,
                "event_time_utc": _timestamp(timestamp),
                "time_zone": config["location"]["time_zone"],
                "event_type": event_type,
                "severity": severity,
                "state_transition": {"from": before, "to": after},
                "correlation_id": correlation_id or scenario_id,
                "causation_id": causation_id,
                "quality": _good_quality(),
            }
        )
        return event_id

    schedule: dict[str, Any] = {
        "scenario_id": scenario_id,
        "onset": onset,
        "end": incident_end,
        "affected_asset_ids": [],
        "onset_event_id": None,
        "restoration_times": {},
        "storm_impacts": [],
        "storm_event_ids_by_area": {},
        "incident": None,
        "incident_event_ids_by_status": {},
        "sensor_faults": [],
    }

    if scenario == "normal":
        onset = start
        incident_end = end
        schedule["onset"] = onset
        schedule["end"] = incident_end
        label = "baseline"
        cause = "none"
    elif scenario == "heatwave":
        schedule["affected_asset_ids"] = sorted(context["asset_by_id"])
        schedule["onset_event_id"] = event(
            onset, "heat_advisory_started", "warning", context["weather_id"], "normal", "heatwave"
        )
        event(incident_end, "heat_advisory_ended", "information", context["weather_id"], "heatwave", "normal")
        label = "heatwave"
        cause = "synthetic elevated-temperature forcing"
    elif scenario == "storm_outage":
        affected: set[str] = set()
        warning_time = max(start, onset - step)
        warning_id = event(
            warning_time, "storm_warning", "warning", context["weather_id"], "normal", "severe_weather"
        )
        first_open: str | None = None
        for feeder in context["feeders"].values():
            for recloser_id in feeder["reclosers"]:
                open_id = event(
                    onset,
                    "protective_device_opened",
                    "critical",
                    recloser_id,
                    "closed",
                    "open",
                    warning_id,
                )
                first_open = first_open or open_id
            for asset_id in (
                feeder["reclosers"]
                + feeder["regulators"]
                + feeder["capacitor_banks"]
                + feeder["pq_monitors"]
                + feeder["transformers"]
                + feeder["ami_meters"]
            ):
                affected.add(asset_id)
            for meter_id in feeder["ami_meters"]:
                pv_id = context["meter_pv"].get(meter_id)
                evse_id = context["meter_evse"].get(meter_id)
                if pv_id:
                    affected.add(pv_id)
                if evse_id:
                    affected.add(evse_id)
                event(onset, "ami_last_gasp", "critical", meter_id, "energized", "deenergized", first_open)
            for recloser_id in feeder["reclosers"]:
                event(
                    incident_end,
                    "protective_device_closed",
                    "information",
                    recloser_id,
                    "open",
                    "closed",
                    first_open,
                )
            for transformer_index, transformer_id in enumerate(feeder["transformers"]):
                last_telemetry_time = end - step
                restore_at = min(
                    last_telemetry_time,
                    incident_end + transformer_index * step,
                )
                schedule["restoration_times"][transformer_id] = restore_at
                event(
                    restore_at,
                    "service_restored",
                    "information",
                    transformer_id,
                    "deenergized",
                    "energized",
                    first_open,
                )
        schedule["affected_asset_ids"] = sorted(affected)
        schedule["onset_event_id"] = first_open
        if schedule["restoration_times"]:
            schedule["end"] = max(schedule["restoration_times"].values())
        label = "storm_outage"
        cause = "synthetic severe weather and protective opening"
    elif scenario == "transformer_degradation":
        transformer_id = sorted(context["transformer_meters"])[0]
        schedule["affected_asset_ids"] = [transformer_id]
        warning_id = event(
            onset,
            "condition_warning_started",
            "warning",
            transformer_id,
            "baseline",
            "degrading",
        )
        schedule["onset_event_id"] = warning_id
        event(
            incident_end,
            "condition_observation_ended",
            "information",
            transformer_id,
            "degrading",
            "elevated_condition_index",
            warning_id,
        )
        label = "transformer_degradation"
        cause = "synthetic latent condition progression under thermal stress"
    else:
        affected_meters = sorted(context["all_meter_ids"])[: min(2, len(context["all_meter_ids"]))]
        schedule["affected_asset_ids"] = affected_meters
        failure_id = event(
            onset,
            "communications_link_failed",
            "warning",
            affected_meters[0],
            "available",
            "impaired",
        )
        schedule["onset_event_id"] = failure_id
        event(
            incident_end,
            "communications_link_restored",
            "information",
            affected_meters[0],
            "impaired",
            "available",
            failure_id,
        )
        label = "communications_failure"
        cause = "synthetic telemetry transport impairment"

    configured_faults = config.get("environment", {}).get("sensor_faults", [])
    target_intervals: dict[tuple[str, str], list[tuple[datetime, datetime]]] = {}
    for fault in configured_faults:
        asset_id = fault["asset_id"]
        asset = context["asset_by_id"].get(asset_id)
        if asset is None:
            raise ConfigError(f"sensor fault references unknown asset_id: {asset_id}")
        allowed_measurements = set(CLASS_SPECIFIC_MEASUREMENTS[asset["asset_type"]])
        if asset["asset_type"] != "weather_station":
            allowed_measurements.update(ELECTRICAL_MEASUREMENTS)
        if fault["measurement"] not in allowed_measurements:
            raise ConfigError(
                "sensor fault measurement is not emitted for "
                f"{asset['asset_type']}: {fault['measurement']}"
            )
        fault_start = _parse_utc(fault["start_utc"])
        fault_end = _parse_utc(fault["end_utc"])
        target_key = (asset_id, fault["measurement"])
        for existing_start, existing_end in target_intervals.setdefault(
            target_key, []
        ):
            if fault_start < existing_end and existing_start < fault_end:
                raise ConfigError(
                    "sensor fault intervals must not overlap for the same "
                    "asset and measurement"
                )
        target_intervals[target_key].append((fault_start, fault_end))
        started_event_id = (
            event(
                fault_start,
                "sensor_fault_started",
                "warning",
                asset_id,
                "normal",
                fault["fault_type"],
                correlation_id=fault["fault_id"],
            )
            if start <= fault_start < end
            else None
        )
        cleared_event_id = (
            event(
                fault_end,
                "sensor_fault_cleared",
                "information",
                asset_id,
                fault["fault_type"],
                "normal",
                causation_id=started_event_id,
                correlation_id=fault["fault_id"],
            )
            if start < fault_end <= end
            else None
        )
        schedule["sensor_faults"].append(
            {
                **fault,
                "start": fault_start,
                "end": fault_end,
                "started_event_id": started_event_id,
                "cleared_event_id": cleared_event_id,
            }
        )

    if schedule["sensor_faults"]:
        fault_asset_ids = {
            fault["asset_id"] for fault in schedule["sensor_faults"]
        }
        schedule["affected_asset_ids"] = sorted(
            set(schedule["affected_asset_ids"]) | fault_asset_ids
        )
        fault_onset = min(fault["start"] for fault in schedule["sensor_faults"])
        fault_end = max(fault["end"] for fault in schedule["sensor_faults"])
        schedule["onset"] = min(schedule["onset"], fault_onset)
        schedule["end"] = max(schedule["end"], fault_end)
        if scenario == "normal":
            label = "sensor_fault_overlay"
            cause = "synthetic sensor measurement fault injection"
        else:
            label = f"{label}+sensor_fault_overlay"
            cause = f"{cause}; synthetic sensor measurement fault injection"

    storm = config.get("environment", {}).get("storm")
    if storm is not None:
        storm_affected: set[str] = set()
        active_onsets: list[datetime] = []
        active_ends: list[datetime] = []
        for point in storm["route"]:
            arrival = _parse_utc(point["arrival_utc"])
            departure = _parse_utc(point["departure_utc"])
            location_ref = area_location_ref(point["area"])
            impact = {
                **point,
                "arrival": arrival,
                "departure": departure,
                "location_ref": location_ref,
            }
            schedule["storm_impacts"].append(impact)
            area_assets = sorted(
                asset_id
                for asset_id, asset in context["asset_by_id"].items()
                if asset["location_ref"] == location_ref
            )
            representative_asset = next(
                (
                    asset_id
                    for asset_id in area_assets
                    if context["asset_by_id"][asset_id]["asset_type"] == "feeder_relay"
                ),
                area_assets[0] if area_assets else context["weather_id"],
            )
            if point["flood_state"] != "none":
                severity = "Severe" if point["flood_state"] == "warning" else "Moderate"
                alerts.append(
                    {
                        "schema_version": ALERT_SCHEMA_VERSION,
                        "alert_id": (
                            f"alert-{run_id}-{storm['storm_id']}-"
                            f"{point['sequence']:03d}"
                        ),
                        "run_id": run_id,
                        "scenario_id": scenario_id,
                        "storm_id": storm["storm_id"],
                        "storm_revision": storm["revision"],
                        "is_synthetic": True,
                        "public_safety": False,
                        "status": storm["status"],
                        "message_type": (
                            "Alert" if storm["revision"] == 1 else "Update"
                        ),
                        "scope": "Private",
                        "event_time_utc": storm["issued_at_utc"],
                        "effective_utc": storm["issued_at_utc"],
                        "onset_utc": point["arrival_utc"],
                        "expires_utc": point["departure_utc"],
                        "area_ref": location_ref,
                        "area_name": point["area"],
                        "event": (
                            "Flood Warning"
                            if point["flood_state"] == "warning"
                            else "Flood Watch"
                        ),
                        "urgency": "Expected",
                        "severity": severity,
                        "certainty": point["certainty"],
                        "headline": (
                            f"SYNTHETIC EXERCISE: {point['area']} "
                            f"{point['flood_state'].upper()}"
                        ),
                        "description": (
                            "Synthetic storm-route impact for software and SRE "
                            "exercise use only."
                        ),
                        "instruction": (
                            "Do not use this synthetic alert for public-safety "
                            "or operational decisions."
                        ),
                        "quality": _good_quality(
                            "synthetic_exercise", "not_public_safety"
                        ),
                    }
                )
            if arrival < end and departure > start:
                active_onsets.append(max(start, arrival))
                active_ends.append(min(end, departure))
                storm_affected.update(area_assets)
                impact_event_id = event(
                    max(start, arrival),
                    (
                        "synthetic_flood_warning_active"
                        if point["flood_state"] == "warning"
                        else "synthetic_storm_area_active"
                    ),
                    "warning" if point["flood_state"] == "warning" else "information",
                    representative_asset,
                    "forecast",
                    point["flood_state"],
                )
                schedule["storm_event_ids_by_area"][location_ref] = impact_event_id
        if active_onsets:
            schedule["onset"] = min(active_onsets)
            schedule["end"] = max(active_ends)
            schedule["affected_asset_ids"] = sorted(
                set(schedule["affected_asset_ids"]) | storm_affected
            )
            schedule["onset_event_id"] = (
                schedule["onset_event_id"]
                or next(iter(schedule["storm_event_ids_by_area"].values()))
            )
            label = "storm_route_overlay"
            cause = "synthetic predicted storm route and area flood risk"

    incident = config.get("environment", {}).get("incident")
    if incident is not None:
        location_ref = area_location_ref(incident["area"])
        transformer_id = next(
            (
                asset_id
                for asset_id, asset in sorted(context["asset_by_id"].items())
                if asset["asset_type"] == "distribution_transformer"
                and asset["location_ref"] == location_ref
            ),
            None,
        )
        if transformer_id is None:
            raise ValueError(
                f"incident area has no distribution transformer: {incident['area']}"
            )
        meter_ids = list(context["transformer_meters"][transformer_id])
        affected = {transformer_id, *meter_ids}
        for meter_id in meter_ids:
            if context["meter_pv"].get(meter_id):
                affected.add(context["meter_pv"][meter_id])
            if context["meter_evse"].get(meter_id):
                affected.add(context["meter_evse"][meter_id])
        affected_ids = sorted(affected)
        detected_at = _parse_utc(incident["lifecycle"][0]["at_utc"])
        resolved_at = _parse_utc(incident["lifecycle"][-1]["at_utc"])
        schedule["incident"] = {
            "definition": incident,
            "target_asset_id": transformer_id,
            "affected_asset_ids": affected_ids,
            "detected_at": detected_at,
            "resolved_at": resolved_at,
        }
        schedule["affected_asset_ids"] = sorted(
            set(schedule["affected_asset_ids"]) | affected
        )
        schedule["onset"] = detected_at
        schedule["end"] = resolved_at

        previous_status = "normal"
        for transition in incident["lifecycle"]:
            transition_at = _parse_utc(transition["at_utc"])
            if start <= transition_at < end:
                current_status = transition["status"]
                transition_event_id = event(
                    transition_at,
                    f"incident_{current_status}",
                    (
                        "critical"
                        if incident["severity"] in {"major", "critical"}
                        and current_status in ACTIVE_OUTAGE_STATUSES
                        else "warning"
                        if current_status == "monitoring"
                        else "information"
                    ),
                    transformer_id,
                    previous_status,
                    current_status,
                    causation_id=incident["caused_by"],
                    correlation_id=incident["incident_id"],
                )
                schedule["incident_event_ids_by_status"][
                    current_status
                ] = transition_event_id
            previous_status = transition["status"]

        timestamp = start
        incident_sequence = 0
        while timestamp < end:
            current_status = incident_status_at(incident, timestamp)
            if current_status is not None:
                incident_sequence += 1
                incidents.append(
                    {
                        "schema_version": INCIDENT_SCHEMA_VERSION,
                        "incident_record_id": (
                            f"incident-record-{run_id}-{incident_sequence:06d}"
                        ),
                        "incident_id": incident["incident_id"],
                        "run_id": run_id,
                        "scenario_id": scenario_id,
                        "is_synthetic": True,
                        "public_safety": False,
                        "exercise_status": incident["exercise_status"],
                        "scope": "Private",
                        "title": incident["title"],
                        "incident_type": incident["incident_type"],
                        "severity": incident["severity"],
                        "status": current_status,
                        "area_ref": location_ref,
                        "area_name": incident["area"],
                        "owner_team": incident["owner_team"],
                        "detection_source": incident["detection_source"],
                        "root_cause": incident["root_cause"],
                        "caused_by": incident["caused_by"],
                        "correlation_id": incident["incident_id"],
                        "detected_at_utc": incident["lifecycle"][0]["at_utc"],
                        "updated_at_utc": _timestamp(timestamp),
                        "resolved_at_utc": incident["lifecycle"][-1]["at_utc"],
                        "target_asset_id": transformer_id,
                        "affected_asset_ids": affected_ids,
                        "affected_service_points": len(meter_ids),
                        "service_state": (
                            "interrupted"
                            if current_status in ACTIVE_OUTAGE_STATUSES
                            else "restored_under_monitoring"
                            if current_status == "monitoring"
                            else "restored"
                        ),
                        "lifecycle": incident["lifecycle"],
                        "quality": _good_quality(
                            "synthetic_exercise",
                            "not_public_safety",
                        ),
                    }
                )
            timestamp += step
        label = "post_storm_incident"
        cause = incident["root_cause"]

    event_ids = [item["event_id"] for item in events]
    expected_symptoms = SCENARIO_OUTCOMES[scenario]
    if storm is not None and schedule["storm_event_ids_by_area"]:
        expected_symptoms = [
            "synthetic forecast snapshot with borough route timing",
            "area-linked flood watch or warning records",
            "elevated wind and precipitation during active route intervals",
            "deterministic voltage and telemetry-delivery effects in active areas",
        ]
    if incident is not None:
        expected_symptoms = [
            "synthetic incident detection and response lifecycle",
            "distribution transformer and downstream service interruption",
            "investigation and mitigation state transitions",
            "service restoration followed by monitoring and resolution",
        ]
    if schedule["sensor_faults"]:
        expected_symptoms = [
            *expected_symptoms,
            "measurement-specific synthetic sensor bias, drift, stuck value, or dropout",
            "questionable or missing quality without changing latent physical state",
            "sensor-fault onset and clear events with causal identifiers",
        ]
    ground_truth = [
        {
            "schema_version": GROUND_TRUTH_SCHEMA_VERSION,
            "ground_truth_id": f"ground-truth-{run_id}-{scenario}",
            "run_id": run_id,
            "scenario_id": scenario_id,
            "scenario": scenario,
            "scenario_interval": {
                "onset_utc": _timestamp(schedule["onset"]),
                "end_utc": _timestamp(schedule["end"]),
            },
            "affected_asset_ids": schedule["affected_asset_ids"],
            "injected_cause": cause,
            "expected_symptoms": expected_symptoms,
            "label": label,
            "correlated_event_ids": event_ids,
            "quality": _good_quality("synthetic_ground_truth"),
        }
    ]
    events.sort(key=lambda item: (item["event_time_utc"], item["event_id"]))
    alerts.sort(key=lambda item: (item["onset_utc"], item["area_ref"]))
    incidents.sort(
        key=lambda item: (item["updated_at_utc"], item["incident_record_id"])
    )
    return schedule, events, ground_truth, alerts, incidents


def _is_energized(
    asset_id: str, timestamp: datetime, scenario: str, schedule: dict[str, Any], context: dict[str, Any]
) -> bool:
    incident = schedule.get("incident")
    if (
        incident is not None
        and asset_id in incident["affected_asset_ids"]
        and incident_status_at(incident["definition"], timestamp)
        in ACTIVE_OUTAGE_STATUSES
    ):
        return False
    if scenario != "storm_outage" or asset_id not in schedule["affected_asset_ids"]:
        return True
    if timestamp < schedule["onset"]:
        return True
    asset = context["asset_by_id"][asset_id]
    if asset["asset_type"] in {"distribution_transformer", "ami_smart_meter", "pv_inverter", "evse"}:
        transformer_id = context["asset_transformer"][asset_id]
        return timestamp >= schedule["restoration_times"][transformer_id]
    return timestamp >= schedule["end"] or timestamp >= min(
        schedule["restoration_times"].values(), default=schedule["end"]
    )


def _weather(
    timestamp: datetime,
    config: dict[str, Any],
    schedule: dict[str, Any],
) -> dict[str, float]:
    climate = config["location"]["climate"]
    local = timestamp.astimezone(ZoneInfo(config["location"]["time_zone"]))
    local_hour = local.hour + local.minute / 60.0
    day_of_year = local.timetuple().tm_yday
    seasonal = climate["temperature_seasonal_amplitude_c"] * math.cos(
        2
        * math.pi
        * (day_of_year - climate["temperature_peak_day_of_year"])
        / 365.2425
    )
    temperature = (
        climate["temperature_mean_c"]
        + seasonal
        + climate["temperature_daily_amplitude_c"] * math.sin(2 * math.pi * (local_hour - 10.0) / 24.0)
        + (_noise(config["seed"], "temperature", timestamp.isoformat()) - 0.5) * 1.2
    )
    scenario = config["scenario"]
    active = schedule["onset"] <= timestamp < schedule["end"]
    if scenario == "heatwave" and active:
        temperature += 8.0
    humidity = max(
        5.0,
        min(
            100.0,
            climate["humidity_mean_pct"]
            - 1.2 * (temperature - climate["temperature_mean_c"])
            + (_noise(config["seed"], "humidity", timestamp.isoformat()) - 0.5) * 8.0,
        ),
    )
    daylight = max(0.0, math.sin(math.pi * (local_hour - 6.0) / 12.0))
    irradiance = climate["solar_peak_w_m2"] * daylight
    wind = max(
        0.0,
        climate["wind_mean_mps"]
        + (_noise(config["seed"], "wind", timestamp.isoformat()) - 0.5) * 2.0,
    )
    rain_probability = climate["precipitation_probability"]
    precipitation = 0.0
    if _noise(config["seed"], "rain", timestamp.isoformat()) < rain_probability:
        precipitation = 0.5 + 2.5 * _noise(
            config["seed"], "rain_amount", timestamp.isoformat()
        )
        irradiance *= 0.65
    if scenario == "storm_outage" and schedule["onset"] - timedelta(
        minutes=config["simulation"]["interval_minutes"]
    ) <= timestamp < schedule["end"]:
        wind += 13.0
        precipitation = 8.0 + 5.0 * _noise(
            config["seed"], "storm_rain", timestamp.isoformat()
        )
        irradiance *= 0.2
    active_impacts = _active_storm_impacts(schedule, timestamp)
    if active_impacts:
        wind = max(wind, max(item["wind_speed_mps"] for item in active_impacts))
        precipitation = max(
            precipitation,
            max(item["precipitation_mm"] for item in active_impacts),
        )
    return {
        "air_temperature": _clean_float(temperature),
        "relative_humidity": _clean_float(humidity),
        "wind_speed": _clean_float(wind),
        "precipitation": _clean_float(precipitation),
        "solar_irradiance": _clean_float(irradiance),
    }


def _load_shape(local_hour: float) -> float:
    morning = math.exp(-((local_hour - 7.5) / 2.2) ** 2)
    evening = math.exp(-((local_hour - 19.0) / 3.0) ** 2)
    return 0.28 + 0.20 * morning + 0.55 * evening


def _electrical_values(
    p_kw: float,
    q_kvar: float,
    nominal_voltage: float,
    rated_kva: float,
    energized: bool,
    phase_count: int,
    seed: int,
    timestamp: datetime,
    asset_id: str,
) -> dict[str, float]:
    if not energized:
        return {
            "active_power": 0.0,
            "reactive_power": 0.0,
            "apparent_power": 0.0,
            "power_factor": 0.0,
            "current": 0.0,
            "voltage": 0.0,
            "frequency": 0.0,
        }
    apparent = math.hypot(p_kw, q_kvar)
    loading = apparent / rated_kva if rated_kva > 0 else 0.0
    voltage = nominal_voltage * (1.0 - min(0.08, 0.025 * loading))
    pf = abs(p_kw) / apparent if apparent > 1e-12 else 0.0
    denominator = voltage * (math.sqrt(3.0) if phase_count >= 3 else 1.0)
    current = apparent * 1000.0 / denominator if denominator > 0 else 0.0
    frequency = (
        60.0
        + (
            _noise(seed, "frequency", timestamp.isoformat(), asset_id)
            - 0.5
        )
        * 0.02
    )
    return {
        "active_power": _clean_float(p_kw),
        "reactive_power": _clean_float(q_kvar),
        "apparent_power": _clean_float(apparent),
        "power_factor": _clean_float(pf),
        "current": _clean_float(current),
        "voltage": _clean_float(voltage),
        "frequency": _clean_float(frequency),
    }


def _active_sensor_fault(
    schedule: dict[str, Any],
    asset_id: str,
    measurement: str,
    timestamp: datetime,
) -> dict[str, Any] | None:
    return next(
        (
            fault
            for fault in schedule["sensor_faults"]
            if fault["asset_id"] == asset_id
            and fault["measurement"] == measurement
            and fault["start"] <= timestamp < fault["end"]
        ),
        None,
    )


def _apply_sensor_fault(
    fault: dict[str, Any],
    value: float,
    timestamp: datetime,
) -> tuple[float | None, dict[str, Any]]:
    fault_type = fault["fault_type"]
    magnitude = float(fault["magnitude"])
    if fault_type == "dropout":
        return None, {
            "validity": "missing",
            "source": "simulator",
            "reasons": ["sensor_dropout", fault["fault_id"]],
        }
    if fault_type == "bias":
        output = value + magnitude
    elif fault_type == "drift":
        duration = max(
            1.0,
            (fault["end"] - fault["start"]).total_seconds(),
        )
        progress = (timestamp - fault["start"]).total_seconds() / duration
        output = value + magnitude * max(0.0, min(1.0, progress))
    else:
        output = magnitude
    return _clean_float(output), {
        "validity": "questionable",
        "source": "simulator",
        "reasons": [f"sensor_{fault_type}", fault["fault_id"]],
    }


def simulate_telemetry(
    config: dict[str, Any],
    run_id: str,
    assets: list[dict[str, Any]],
    context: dict[str, Any],
    schedule: dict[str, Any],
    model_state: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Simulate weather, causal load/DER flow, device states, and quality."""
    start = _parse_utc(config["simulation"]["start_utc"])
    end = _parse_utc(config["simulation"]["end_utc"])
    interval_minutes = config["simulation"]["interval_minutes"]
    step = timedelta(minutes=interval_minutes)
    timestamps: list[datetime] = []
    current = start
    while current < end:
        timestamps.append(current)
        current += step

    asset_by_id = context["asset_by_id"]
    state = normalize_model_state(model_state, assets)
    thermal_rise = state["thermal_rise_c"]
    records: list[dict[str, Any]] = []
    last_delivered_values: dict[tuple[str, str], tuple[float, datetime]] = {}
    sequence = 0
    units = {
        "active_power": "kW",
        "reactive_power": "kvar",
        "apparent_power": "kVA",
        "power_factor": "1",
        "current": "A",
        "voltage": "V",
        "frequency": "Hz",
        "interval_energy": "kW.h",
        "interval_import_energy": "kW.h",
        "interval_export_energy": "kW.h",
        "session_energy": "kW.h",
        "top_oil_temperature": "Cel",
        "winding_hot_spot_temperature": "Cel",
        "enclosure_temperature": "Cel",
        "dew_point_temperature": "Cel",
        "heatsink_temperature": "Cel",
        "connector_temperature": "Cel",
        "loading": "%",
        "oil_level": "%",
        "voltage_thd": "%",
        "current_thd": "%",
        "voltage_unbalance": "%",
        "communications_signal_strength": "%",
        "condition_index": "1",
        "solar_irradiance": "W/m2",
        "barometric_pressure": "Pa",
        "wind_direction": "deg",
        "wind_gust": "m/s",
        "sensor_battery_voltage": "V",
        "station_service_voltage": "V",
        "station_dc_voltage": "V",
        "control_battery_voltage": "V",
        "dc_voltage": "V",
        "dc_current": "A",
        "dc_power": "kW",
        "commanded_current": "A",
        "motor_current": "A",
        "neutral_unbalance_current": "A",
        "moisture": "ppm",
        "frequency_deviation": "Hz",
        "demand_active_power": "kW",
        "tap_position": "1",
        "ltc_tap_position": "1",
        "switch_state": "1",
        "closed_state": "1",
        "trip_state": "1",
        "source_available": "1",
        "time_sync_state": "1",
        "cooling_stage": "1",
        "fan_run_state": "1",
        "pickup_state": "1",
        "self_test_state": "1",
        "operation_count": "1",
        "lockout_state": "1",
        "reclose_count": "1",
        "local_remote_state": "1",
        "automatic_mode": "1",
        "sag_state": "1",
        "swell_state": "1",
        "interruption_state": "1",
        "outage_state": "1",
        "reverse_energy_state": "1",
        "operating_state": "1",
        "curtailment_state": "1",
        "charging_state": "1",
        "vehicle_present_state": "1",
        "contactor_state": "1",
        "ground_fault_state": "1",
        "air_temperature": "Cel",
        "relative_humidity": "%",
        "wind_speed": "m/s",
        "precipitation": "mm",
    }

    def append_record(
        timestamp: datetime,
        asset_id: str,
        measurement: str,
        value: float | None,
        aggregation: str = "instantaneous",
    ) -> None:
        nonlocal sequence
        sequence += 1
        output_value = _clean_float(value) if value is not None else None
        quality = _good_quality()
        ingest = timestamp + timedelta(seconds=2)
        source_time = timestamp
        delivery_key = (asset_id, measurement)
        energized = _is_energized(
            asset_id,
            timestamp,
            config["scenario"],
            schedule,
            context,
        )
        sensor_fault = _active_sensor_fault(
            schedule,
            asset_id,
            measurement,
            timestamp,
        )
        if sensor_fault is not None and output_value is not None and energized:
            output_value, quality = _apply_sensor_fault(
                sensor_fault,
                output_value,
                timestamp,
            )
        if (
            config["scenario"] == "communications_failure"
            and asset_id in schedule["affected_asset_ids"]
            and schedule["onset"] <= timestamp < schedule["end"]
        ):
            quality_case = min(
                2,
                int(
                    _noise(
                        config["seed"],
                        "communications_quality",
                        timestamp.isoformat(),
                        asset_id,
                        measurement,
                    )
                    * 3
                ),
            )
            if quality_case == 0:
                output_value = None
                quality = {"validity": "missing", "source": "simulator", "reasons": ["communications_unavailable"]}
            elif quality_case == 1:
                previous = last_delivered_values.get(delivery_key)
                if previous is not None:
                    output_value, source_time = previous
                quality = {"validity": "stale", "source": "simulator", "reasons": ["communications_delayed"]}
            else:
                quality = {"validity": "late", "source": "simulator", "reasons": ["communications_delayed"]}
                ingest = timestamp + step + timedelta(seconds=5)
        elif not energized:
            quality = _good_quality("deenergized")
        else:
            storm_impact = _storm_impact_for_location(
                schedule,
                asset_by_id[asset_id]["location_ref"],
                timestamp,
            )
            if storm_impact is not None:
                quality["reasons"].append("synthetic_storm_area")
                if _noise(
                    config["seed"],
                    "storm_delivery",
                    timestamp.isoformat(),
                    asset_id,
                    measurement,
                ) < 0.08:
                    if quality["validity"] == "good":
                        quality["validity"] = "late"
                    quality["reasons"].append(
                        "synthetic_storm_communications_delay"
                    )
                    ingest = timestamp + step + timedelta(seconds=5)
        related_event_id = (
            schedule["onset_event_id"]
            if config["scenario"] != "normal"
            else None
        )
        storm_impact = _storm_impact_for_location(
            schedule,
            asset_by_id[asset_id]["location_ref"],
            timestamp,
        )
        if storm_impact is not None:
            related_event_id = schedule["storm_event_ids_by_area"].get(
                storm_impact["location_ref"],
                related_event_id,
            )
        if sensor_fault is not None:
            related_event_id = sensor_fault["started_event_id"]
        incident_schedule = schedule.get("incident")
        if (
            incident_schedule is not None
            and asset_id in incident_schedule["affected_asset_ids"]
        ):
            incident_status = incident_status_at(
                incident_schedule["definition"],
                timestamp,
            )
            related_event_id = schedule[
                "incident_event_ids_by_status"
            ].get(incident_status, related_event_id)
        records.append(
            {
                "schema_version": TELEMETRY_SCHEMA_VERSION,
                "telemetry_id": f"telemetry-{run_id}-{sequence:09d}",
                "run_id": run_id,
                "scenario_id": schedule["scenario_id"],
                "asset_id": asset_id,
                "event_time_utc": _timestamp(timestamp),
                "source_time_utc": _timestamp(source_time),
                "ingest_time_utc": _timestamp(ingest),
                "time_zone": config["location"]["time_zone"],
                "measurement": measurement,
                "value": output_value,
                "unit": units[measurement],
                "phase": "none" if not asset_by_id[asset_id]["phases"] else "total",
                "aggregation": aggregation,
                "interval_seconds": interval_minutes * 60,
                "sequence": sequence,
                "related_event_id": related_event_id,
                "quality": quality,
            }
        )
        if (
            quality["validity"] not in {"missing", "stale"}
            and output_value is not None
        ):
            last_delivered_values[delivery_key] = (output_value, timestamp)

    for timestamp_index, timestamp in enumerate(timestamps):
        weather = _weather(timestamp, config, schedule)
        weather_extra = {
            "dew_point_temperature": weather["air_temperature"]
            - (100.0 - weather["relative_humidity"]) / 5.0,
            "wind_gust": weather["wind_speed"]
            + 1.0
            + 3.0
            * _noise(
                config["seed"],
                "wind_gust",
                timestamp.isoformat(),
            ),
            "wind_direction": 360.0
            * _noise(
                config["seed"],
                "wind_direction",
                timestamp.isoformat(),
            ),
            "barometric_pressure": 101325.0
            + 900.0
            * (
                _noise(
                    config["seed"],
                    "barometric_pressure",
                    timestamp.isoformat(),
                )
                - 0.5
            )
            - 18.0 * weather["precipitation"],
            "sensor_battery_voltage": 12.4
            + 0.4
            * _noise(
                config["seed"],
                "weather_battery",
                timestamp.isoformat(),
            ),
        }
        for measurement in sorted({**weather, **weather_extra}):
            value = (
                weather[measurement]
                if measurement in weather
                else weather_extra[measurement]
            )
            append_record(timestamp, context["weather_id"], measurement, value)

        local = timestamp.astimezone(ZoneInfo(config["location"]["time_zone"]))
        local_hour = local.hour + local.minute / 60.0
        climate = config["location"]["climate"]
        meter_power: dict[str, tuple[float, float]] = {}
        device_power: dict[str, tuple[float, float]] = {}
        for meter_index, meter_id in enumerate(context["all_meter_ids"]):
            energized = _is_energized(meter_id, timestamp, config["scenario"], schedule, context)
            diversity = 0.75 + 0.5 * _noise(config["seed"], "diversity", meter_id)
            temperature_multiplier = 1.0 + climate["load_temperature_coefficient_per_c"] * max(
                0.0, weather["air_temperature"] - climate["load_temperature_base_c"]
            )
            base_p = 7.0 * _load_shape(local_hour) * diversity * temperature_multiplier
            base_q = base_p * math.tan(math.acos(0.96))
            pv_id = context["meter_pv"].get(meter_id)
            pv_p = 0.0
            if pv_id:
                capacity = asset_by_id[pv_id]["ratings"]["synthetic_dc_capacity_kw"]
                peak = max(1.0, climate["solar_peak_w_m2"])
                pv_p = -capacity * 0.90 * weather["solar_irradiance"] / peak if energized else 0.0
                device_power[pv_id] = (pv_p, 0.0)
            evse_id = context["meter_evse"].get(meter_id)
            evse_p = 0.0
            if evse_id and (local_hour >= 18.0 or local_hour < 1.0):
                evse_p = 5.5 * (
                    0.8
                    + 0.2
                    * _noise(
                        config["seed"],
                        "evse",
                        timestamp.isoformat(),
                        meter_id,
                    )
                )
            if not energized:
                base_p = base_q = evse_p = pv_p = 0.0
            if evse_id:
                device_power[evse_id] = (evse_p, evse_p * math.tan(math.acos(0.98)))
            meter_power[meter_id] = (
                base_p + evse_p + pv_p,
                base_q + evse_p * math.tan(math.acos(0.98)),
            )

        power_by_asset: dict[str, tuple[float, float]] = dict(device_power)
        power_by_asset.update(meter_power)
        for transformer_id, meter_ids in context["transformer_meters"].items():
            p_total = sum(meter_power[meter_id][0] for meter_id in meter_ids)
            q_total = sum(meter_power[meter_id][1] for meter_id in meter_ids)
            power_by_asset[transformer_id] = (p_total, q_total)

        total_station_p = 0.0
        total_station_q = 0.0
        state_by_asset: dict[str, float] = {}
        for feeder in context["feeders"].values():
            feeder_p = sum(meter_power[meter_id][0] for meter_id in feeder["ami_meters"])
            customer_q = sum(meter_power[meter_id][1] for meter_id in feeder["ami_meters"])
            energized_caps = [
                asset_id
                for asset_id in feeder["capacitor_banks"]
                if _is_energized(asset_id, timestamp, config["scenario"], schedule, context)
            ]
            cap_each = -15.0 if customer_q > 15.0 and energized_caps else 0.0
            for asset_id in feeder["capacitor_banks"]:
                cap_q = cap_each if asset_id in energized_caps else 0.0
                power_by_asset[asset_id] = (0.0, cap_q)
                state_by_asset[asset_id] = 1.0 if cap_q else 0.0
            feeder_q = customer_q + cap_each * len(energized_caps)
            total_station_p += feeder_p
            total_station_q += feeder_q
            flow_devices = (
                [feeder["relay"], feeder["meter"]]
                + feeder["reclosers"]
                + feeder["regulators"]
                + feeder["pq_monitors"]
            )
            for asset_id in flow_devices:
                energized = _is_energized(asset_id, timestamp, config["scenario"], schedule, context)
                power_by_asset[asset_id] = (feeder_p if energized else 0.0, feeder_q if energized else 0.0)
            for asset_id in feeder["reclosers"]:
                state_by_asset[asset_id] = (
                    1.0
                    if _is_energized(asset_id, timestamp, config["scenario"], schedule, context)
                    else 0.0
                )
            for asset_id in feeder["regulators"]:
                ratio = math.hypot(feeder_p, feeder_q) / max(1.0, asset_by_id[asset_id]["ratings"]["rated_kva"])
                state_by_asset[asset_id] = 2.0 if ratio > 0.75 else (1.0 if ratio > 0.45 else 0.0)
            state_by_asset[feeder["relay"]] = (
                1.0
                if config["scenario"] == "storm_outage"
                and schedule["onset"] <= timestamp < schedule["end"]
                else 0.0
            )

        for asset_id in (context["grid_id"], context["substation_id"], context["station_transformer_id"]):
            power_by_asset[asset_id] = (total_station_p, total_station_q)

        transformer_extra: dict[str, dict[str, float]] = {}
        alpha_cache: dict[float, float] = {}
        for transformer_id in thermal_rise:
            asset = asset_by_id[transformer_id]
            p_kw, q_kvar = power_by_asset.get(transformer_id, (0.0, 0.0))
            rated = asset["ratings"]["rated_kva"]
            loading_ratio = math.hypot(p_kw, q_kvar) / rated if rated else 0.0
            tau = asset["ratings"]["thermal_time_constant_minutes"]
            if tau not in alpha_cache:
                alpha_cache[tau] = 1.0 - math.exp(-interval_minutes / tau)
            target_rise = asset["ratings"]["synthetic_top_oil_rise_c"] * min(2.0, loading_ratio) ** 1.6
            thermal_rise[transformer_id] += alpha_cache[tau] * (target_rise - thermal_rise[transformer_id])
            condition = 0.05 + max(0.0, thermal_rise[transformer_id] - 35.0) * 0.006
            if (
                config["scenario"] == "transformer_degradation"
                and transformer_id in schedule["affected_asset_ids"]
                and timestamp >= schedule["onset"]
            ):
                duration = max(step.total_seconds(), (schedule["end"] - schedule["onset"]).total_seconds())
                progress = min(1.0, (timestamp - schedule["onset"]).total_seconds() / duration)
                condition += 0.45 * progress
            transformer_extra[transformer_id] = {
                "top_oil_temperature": weather["air_temperature"] + thermal_rise[transformer_id],
                "winding_hot_spot_temperature": (
                    weather["air_temperature"]
                    + thermal_rise[transformer_id]
                    + 25.0 * min(1.5, loading_ratio)
                ),
                "loading": loading_ratio * 100.0,
                "condition_index": min(1.0, condition),
                "cooling_stage": (
                    2.0
                    if loading_ratio >= 1.0
                    else 1.0
                    if loading_ratio >= 0.7
                    else 0.0
                ),
                "fan_run_state": 1.0 if loading_ratio >= 0.7 else 0.0,
                "oil_level": max(85.0, 97.0 - 3.0 * condition),
                "moisture": (
                    8.0
                    + 18.0 * condition
                    + 2.0
                    * _noise(
                        config["seed"],
                        "transformer_moisture",
                        timestamp.isoformat(),
                        transformer_id,
                    )
                ),
            }

        for asset in assets:
            asset_id = asset["asset_id"]
            asset_type = asset["asset_type"]
            if asset_type == "weather_station":
                continue
            p_kw, q_kvar = power_by_asset.get(asset_id, (0.0, 0.0))
            energized = _is_energized(asset_id, timestamp, config["scenario"], schedule, context)
            electrical = _electrical_values(
                p_kw,
                q_kvar,
                asset["nominal_voltage_v"]
                * (
                    0.97
                    if (
                        impact := _storm_impact_for_location(
                            schedule,
                            asset["location_ref"],
                            timestamp,
                        )
                    )
                    and impact["flood_state"] == "warning"
                    else 0.99
                    if impact is not None
                    else 1.0
                ),
                asset["ratings"]["rated_kva"],
                energized,
                len(asset["phases"]),
                config["seed"],
                timestamp,
                asset_id,
            )
            for measurement in (
                "active_power",
                "reactive_power",
                "apparent_power",
                "power_factor",
                "current",
                "voltage",
                "frequency",
            ):
                append_record(timestamp, asset_id, measurement, electrical[measurement])
            loading_ratio = math.hypot(p_kw, q_kvar) / max(
                1.0,
                asset["ratings"]["rated_kva"],
            )
            if asset_type == "grid_source":
                append_record(
                    timestamp,
                    asset_id,
                    "source_available",
                    1.0 if energized else 0.0,
                )
                append_record(
                    timestamp,
                    asset_id,
                    "frequency_deviation",
                    electrical["frequency"] - 60.0 if energized else 0.0,
                )
            elif asset_type == "substation":
                append_record(
                    timestamp,
                    asset_id,
                    "station_service_voltage",
                    120.0 if energized else 0.0,
                )
                append_record(
                    timestamp,
                    asset_id,
                    "station_dc_voltage",
                    (
                        125.0
                        + _noise(
                            config["seed"],
                            "station_dc",
                            timestamp.isoformat(),
                            asset_id,
                        )
                        - 0.5
                    )
                    if energized
                    else 0.0,
                )
                append_record(
                    timestamp,
                    asset_id,
                    "enclosure_temperature",
                    weather["air_temperature"] + 2.0,
                )
                append_record(timestamp, asset_id, "time_sync_state", 1.0)
            elif asset_type == "ami_smart_meter":
                interval_energy = p_kw * interval_minutes / 60.0
                append_record(
                    timestamp,
                    asset_id,
                    "interval_energy",
                    interval_energy,
                    "interval",
                )
                append_record(
                    timestamp,
                    asset_id,
                    "interval_import_energy",
                    max(0.0, interval_energy),
                    "interval",
                )
                append_record(
                    timestamp,
                    asset_id,
                    "interval_export_energy",
                    max(0.0, -interval_energy),
                    "interval",
                )
                append_record(
                    timestamp,
                    asset_id,
                    "demand_active_power",
                    max(0.0, p_kw),
                    "interval",
                )
                append_record(
                    timestamp,
                    asset_id,
                    "outage_state",
                    0.0 if energized else 1.0,
                )
                append_record(
                    timestamp,
                    asset_id,
                    "reverse_energy_state",
                    1.0 if p_kw < 0.0 else 0.0,
                )
                append_record(
                    timestamp,
                    asset_id,
                    "communications_signal_strength",
                    72.0
                    + 24.0
                    * _noise(
                        config["seed"],
                        "ami_signal",
                        timestamp.isoformat(),
                        asset_id,
                    ),
                )
            elif asset_type in {"substation_transformer_ltc", "distribution_transformer"}:
                for measurement, value in transformer_extra[asset_id].items():
                    append_record(timestamp, asset_id, measurement, value)
                if asset_type == "substation_transformer_ltc":
                    voltage_error_pct = (
                        100.0
                        * (
                            asset["nominal_voltage_v"] - electrical["voltage"]
                        )
                        / max(1.0, asset["nominal_voltage_v"])
                    )
                    append_record(
                        timestamp,
                        asset_id,
                        "ltc_tap_position",
                        float(
                            max(
                                -16,
                                min(16, round(voltage_error_pct / 0.625)),
                            )
                        ),
                    )
            elif asset_type == "pv_inverter":
                append_record(timestamp, asset_id, "solar_irradiance", weather["solar_irradiance"])
                dc_power = max(0.0, -p_kw / 0.96)
                dc_voltage = 400.0 if dc_power > 0.0 else 0.0
                append_record(timestamp, asset_id, "dc_power", dc_power)
                append_record(timestamp, asset_id, "dc_voltage", dc_voltage)
                append_record(
                    timestamp,
                    asset_id,
                    "dc_current",
                    dc_power * 1000.0 / dc_voltage if dc_voltage else 0.0,
                )
                append_record(
                    timestamp,
                    asset_id,
                    "heatsink_temperature",
                    weather["air_temperature"] + 1.8 * dc_power,
                )
                append_record(
                    timestamp,
                    asset_id,
                    "operating_state",
                    1.0 if dc_power > 0.0 and energized else 0.0,
                )
                append_record(timestamp, asset_id, "curtailment_state", 0.0)
            elif asset_type == "recloser":
                closed_state = state_by_asset[asset_id]
                fault_occurred = (
                    config["scenario"] == "storm_outage"
                    and timestamp >= schedule["onset"]
                )
                append_record(timestamp, asset_id, "closed_state", closed_state)
                append_record(
                    timestamp,
                    asset_id,
                    "lockout_state",
                    1.0 if not closed_state and fault_occurred else 0.0,
                )
                append_record(
                    timestamp,
                    asset_id,
                    "reclose_count",
                    1.0
                    if fault_occurred and timestamp >= schedule["end"]
                    else 0.0,
                )
                append_record(
                    timestamp,
                    asset_id,
                    "operation_count",
                    (
                        2.0
                        if fault_occurred and timestamp >= schedule["end"]
                        else 1.0
                        if fault_occurred
                        else 0.0
                    ),
                )
                append_record(
                    timestamp,
                    asset_id,
                    "control_battery_voltage",
                    24.4
                    + 0.8
                    * _noise(
                        config["seed"],
                        "recloser_battery",
                        timestamp.isoformat(),
                        asset_id,
                    ),
                )
                append_record(timestamp, asset_id, "local_remote_state", 1.0)
            elif asset_type == "regulator":
                tap_position = state_by_asset[asset_id]
                append_record(timestamp, asset_id, "tap_position", tap_position)
                append_record(timestamp, asset_id, "automatic_mode", 1.0)
                append_record(
                    timestamp,
                    asset_id,
                    "motor_current",
                    0.0 if tap_position == 0.0 else 1.8 + 0.2 * abs(tap_position),
                )
                append_record(
                    timestamp,
                    asset_id,
                    "operation_count",
                    abs(tap_position),
                )
            elif asset_type == "capacitor_bank":
                switch_state = state_by_asset[asset_id]
                append_record(timestamp, asset_id, "switch_state", switch_state)
                append_record(timestamp, asset_id, "automatic_mode", 1.0)
                append_record(
                    timestamp,
                    asset_id,
                    "neutral_unbalance_current",
                    (
                        0.05
                        + 0.15
                        * _noise(
                            config["seed"],
                            "capacitor_unbalance",
                            timestamp.isoformat(),
                            asset_id,
                        )
                    )
                    if switch_state
                    else 0.0,
                )
                append_record(
                    timestamp,
                    asset_id,
                    "operation_count",
                    switch_state,
                )
            elif asset_type == "feeder_relay":
                trip_state = state_by_asset[asset_id]
                append_record(timestamp, asset_id, "trip_state", trip_state)
                append_record(timestamp, asset_id, "pickup_state", trip_state)
                append_record(timestamp, asset_id, "self_test_state", 1.0)
                append_record(
                    timestamp,
                    asset_id,
                    "station_dc_voltage",
                    125.0
                    + _noise(
                        config["seed"],
                        "relay_dc",
                        timestamp.isoformat(),
                        asset_id,
                    )
                    - 0.5,
                )
                append_record(
                    timestamp,
                    asset_id,
                    "operation_count",
                    trip_state,
                )
                append_record(timestamp, asset_id, "time_sync_state", 1.0)
            elif asset_type == "feeder_meter":
                append_record(
                    timestamp,
                    asset_id,
                    "demand_active_power",
                    max(0.0, p_kw),
                    "interval",
                )
                append_record(
                    timestamp,
                    asset_id,
                    "voltage_thd",
                    2.0 + 4.0 * loading_ratio,
                )
                append_record(
                    timestamp,
                    asset_id,
                    "voltage_unbalance",
                    0.3
                    + _noise(
                        config["seed"],
                        "meter_unbalance",
                        timestamp.isoformat(),
                        asset_id,
                    ),
                )
                append_record(timestamp, asset_id, "time_sync_state", 1.0)
            elif asset_type == "power_quality_monitor":
                feeder_loading = math.hypot(p_kw, q_kvar) / 15000.0
                append_record(
                    timestamp,
                    asset_id,
                    "voltage_thd",
                    2.0 + 6.0 * feeder_loading,
                )
                append_record(
                    timestamp,
                    asset_id,
                    "current_thd",
                    3.0 + 8.0 * feeder_loading,
                )
                append_record(
                    timestamp,
                    asset_id,
                    "voltage_unbalance",
                    0.4
                    + 1.2
                    * _noise(
                        config["seed"],
                        "pq_unbalance",
                        timestamp.isoformat(),
                        asset_id,
                    ),
                )
                voltage_ratio = electrical["voltage"] / max(
                    1.0,
                    asset["nominal_voltage_v"],
                )
                append_record(
                    timestamp,
                    asset_id,
                    "sag_state",
                    1.0 if 0.0 < voltage_ratio < 0.9 else 0.0,
                )
                append_record(
                    timestamp,
                    asset_id,
                    "swell_state",
                    1.0 if voltage_ratio > 1.1 else 0.0,
                )
                append_record(
                    timestamp,
                    asset_id,
                    "interruption_state",
                    0.0 if energized else 1.0,
                )
                append_record(timestamp, asset_id, "time_sync_state", 1.0)
            elif asset_type == "evse":
                charging = p_kw > 0.0 and energized
                voltage = max(1.0, electrical["voltage"])
                commanded_current = p_kw * 1000.0 / voltage if charging else 0.0
                append_record(
                    timestamp,
                    asset_id,
                    "commanded_current",
                    commanded_current,
                )
                append_record(
                    timestamp,
                    asset_id,
                    "charging_state",
                    1.0 if charging else 0.0,
                )
                append_record(
                    timestamp,
                    asset_id,
                    "vehicle_present_state",
                    1.0
                    if local_hour >= 18.0 or local_hour < 1.0
                    else 0.0,
                )
                append_record(
                    timestamp,
                    asset_id,
                    "contactor_state",
                    1.0 if charging else 0.0,
                )
                append_record(
                    timestamp,
                    asset_id,
                    "connector_temperature",
                    weather["air_temperature"] + 1.2 * max(0.0, p_kw),
                )
                append_record(
                    timestamp,
                    asset_id,
                    "session_energy",
                    max(0.0, p_kw) * interval_minutes / 60.0,
                    "interval",
                )
                append_record(timestamp, asset_id, "ground_fault_state", 0.0)

    records.sort(key=lambda item: (item["event_time_utc"], item["asset_id"], item["measurement"]))
    for index, record in enumerate(records, start=1):
        record["sequence"] = index
        record["telemetry_id"] = f"telemetry-{run_id}-{index:09d}"
    if model_state is not None:
        model_state.clear()
        model_state.update(
            {
                "schema_version": state["schema_version"],
                "as_of_utc": _timestamp(timestamps[-1]) if timestamps else state["as_of_utc"],
                "thermal_rise_c": {
                    asset_id: float(rise)
                    for asset_id, rise in sorted(thermal_rise.items())
                },
            }
        )
    return records


def build_device_logs(
    config: dict[str, Any],
    run_id: str,
    assets: list[dict[str, Any]],
    schedule: dict[str, Any],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build deterministic synthetic device-originated logs without SRE actions."""
    start = config["simulation"]["start_utc"]
    logs: list[dict[str, Any]] = []

    def append_log(
        *,
        event_time_utc: str,
        asset_id: str,
        level: str,
        category: str,
        code: str,
        message: str,
        attributes: dict[str, Any],
        related_event_id: str | None,
        correlation_id: str,
    ) -> None:
        logs.append(
            {
                "schema_version": DEVICE_LOG_SCHEMA_VERSION,
                "log_id": "",
                "run_id": run_id,
                "scenario_id": schedule["scenario_id"],
                "asset_id": asset_id,
                "event_time_utc": event_time_utc,
                "source_time_utc": event_time_utc,
                "time_zone": config["location"]["time_zone"],
                "is_synthetic": True,
                "level": level,
                "category": category,
                "code": code,
                "message": message,
                "attributes": attributes,
                "related_event_id": related_event_id,
                "correlation_id": correlation_id,
                "quality": _good_quality("synthetic_device_log"),
            }
        )

    for asset in assets:
        append_log(
            event_time_utc=start,
            asset_id=asset["asset_id"],
            level="information",
            category="lifecycle",
            code="SELF_TEST_OK",
            message="Synthetic device self-test completed.",
            attributes={
                "asset_type": asset["asset_type"],
                "generic_model": asset["generic_model"],
                "fictional": True,
            },
            related_event_id=None,
            correlation_id=schedule["scenario_id"],
        )

    category_by_event = {
        "communications_link_failed": "communications",
        "communications_link_restored": "communications",
        "protective_device_opened": "protection",
        "protective_device_closed": "protection",
        "ami_last_gasp": "protection",
        "condition_warning_started": "condition",
        "condition_observation_ended": "condition",
        "sensor_fault_started": "sensor",
        "sensor_fault_cleared": "sensor",
        "service_restored": "control",
    }
    level_by_severity = {
        "information": "information",
        "warning": "warning",
        "critical": "critical",
    }
    for event_record in events:
        asset_id = event_record["asset_id"]
        if asset_id is None:
            continue
        event_type = event_record["event_type"]
        append_log(
            event_time_utc=event_record["event_time_utc"],
            asset_id=asset_id,
            level=level_by_severity.get(
                event_record["severity"],
                "information",
            ),
            category=category_by_event.get(event_type, "environment"),
            code=event_type.upper(),
            message=f"Synthetic device event recorded: {event_type}.",
            attributes={
                "state_transition": event_record["state_transition"],
                "event_severity": event_record["severity"],
            },
            related_event_id=event_record["event_id"],
            correlation_id=event_record["correlation_id"],
        )

    logs.sort(
        key=lambda item: (
            item["event_time_utc"],
            item["asset_id"],
            item["category"],
            item["code"],
        )
    )
    for index, record in enumerate(logs, start=1):
        record["log_id"] = f"device-log-{run_id}-{index:09d}"
    return logs
