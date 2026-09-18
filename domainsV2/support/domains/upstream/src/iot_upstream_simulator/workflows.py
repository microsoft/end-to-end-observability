"""Sparse public workflow, log and private ground-truth families for upstream.

Sparse evidence may correlate with physical state but never proves a cause. A
delayed well test does not retroactively alter telemetry, a work order does not
prove a diagnosis, and an external observation never establishes a source.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .catalog import WORKFLOW_RECORD_TYPES
from .common import (
    MAX_DELIVERY_DELAY_SECONDS,
    clamp,
    envelope,
    hash_float,
    parse,
    round6,
    stable_id,
    stamp,
)

#: Per-scenario private truth taken directly from the approved research table.
SCENARIO_TRUTH: dict[str, dict[str, Any]] = {
    "normal_production": {
        "latent_change": "bounded demand and deliverability variation",
        "expected_public_evidence": ["coherent rates, pressures, inventories and utilities"],
        "confusers": ["measurement noise", "deadband"],
        "safe_fidelity": ["F1", "F2"],
    },
    "deliverability_decline": {
        "latent_change": "slow well potential decrease",
        "expected_public_evidence": ["rate and pressure normalized trend", "later well-test revision"],
        "confusers": ["curtailment", "choke or route change"],
        "safe_fidelity": ["F2", "F3"],
    },
    "liquid_loading": {
        "latent_change": "cyclic gas-well flow impairment",
        "expected_public_evidence": ["rate and pressure cycles", "liquid-handling context"],
        "confusers": ["slugging", "bad meter"],
        "safe_fidelity": ["F2", "FH"],
    },
    "slugging": {
        "latent_change": "unstable multiphase arrival",
        "expected_public_evidence": ["oscillating flow", "separator level swing", "suction pressure swing"],
        "confusers": ["valve hunting", "level transmitter bias"],
        "safe_fidelity": ["F2"],
    },
    "flowline_restriction": {
        "latent_change": "slow resistance increase",
        "expected_public_evidence": ["pressure-drop index rises", "rate weakens"],
        "confusers": ["valve position change", "fluid property change"],
        "safe_fidelity": ["F2", "FH"],
    },
    "artificial_lift_degradation": {
        "latent_change": "lift efficiency and cycle reliability decline",
        "expected_public_evidence": ["lift load and cycle diverge from production response"],
        "confusers": ["reservoir decline"],
        "safe_fidelity": ["F2", "FH"],
    },
    "esp_trip_restart": {
        "latent_change": "lift availability changes",
        "expected_public_evidence": ["electrical trip", "pressure and rate decay", "restart lag"],
        "confusers": ["power disturbance"],
        "safe_fidelity": ["F1", "F2"],
    },
    "separator_upset": {
        "latent_change": "inlet variability or control capacity loss",
        "expected_public_evidence": ["level and pressure cycling", "downstream disturbance"],
        "confusers": ["slugging", "transmitter fault"],
        "safe_fidelity": ["F2"],
    },
    "heater_treater_weakness": {
        "latent_change": "available treatment duty declines",
        "expected_public_evidence": ["thermal response weakens", "quality surrogate drifts"],
        "confusers": ["feed or property change"],
        "safe_fidelity": ["F2"],
    },
    "compressor_degradation": {
        "latent_change": "efficiency and condition decline",
        "expected_public_evidence": ["power, temperature, vibration and pressure evidence diverge"],
        "confusers": ["suction change", "cooler fouling"],
        "safe_fidelity": ["F2", "FH"],
    },
    "produced_water_constraint": {
        "latent_change": "transfer or treatment capacity falls",
        "expected_public_evidence": ["water inventory rises", "route and pump evidence changes"],
        "confusers": ["haul-off delay", "level bias"],
        "safe_fidelity": ["F1", "F2"],
    },
    "tank_transfer_imbalance": {
        "latent_change": "inventory and handoff fail to reconcile",
        "expected_public_evidence": ["level, rate, total and transfer lifecycle diverge"],
        "confusers": ["meter drift", "delayed summary"],
        "safe_fidelity": ["F1", "F3"],
    },
    "corrosion_erosion_context": {
        "latent_change": "latent condition deteriorates",
        "expected_public_evidence": ["inspection and condition trend", "possible later release event"],
        "confusers": ["fabrication defect", "mechanical damage"],
        "safe_fidelity": ["FH"],
    },
    "gas_release_observation": {
        "latent_change": "intermittent loss-of-containment evidence",
        "expected_public_evidence": ["detector disagreement", "plume observation", "process disturbance"],
        "confusers": ["maintenance vent", "weather"],
        "safe_fidelity": ["FH"],
    },
    "utility_power_loss": {
        "latent_change": "site or partial supply fails",
        "expected_public_evidence": ["simultaneous stops", "stale values", "recovery events"],
        "confusers": ["network-only outage"],
        "safe_fidelity": ["F1", "F2"],
    },
    "instrument_drift_stuck": {
        "latent_change": "observation bias or freeze",
        "expected_public_evidence": ["cross-signal disagreement", "calibration context"],
        "confusers": ["genuine steady state"],
        "safe_fidelity": ["F2"],
    },
    "communications_degradation": {
        "latent_change": "delivery layer fails",
        "expected_public_evidence": ["null, stale, late, duplicate and out-of-order records"],
        "confusers": ["intentional buffering"],
        "safe_fidelity": ["F2"],
    },
    "maintenance_inspection_workflow": {
        "latent_change": "equipment impaired or under work",
        "expected_public_evidence": ["work context", "bypass context", "return-to-service evidence"],
        "confusers": ["retroactive paperwork"],
        "safe_fidelity": ["F0", "FH"],
    },
    "flare_vent_environment_workflow": {
        "latent_change": "route or observation state changes",
        "expected_public_evidence": ["sparse event", "delayed external observation"],
        "confusers": ["maintenance", "nearby source", "weather"],
        "safe_fidelity": ["FH"],
    },
}

RECORD_FIDELITY = {
    "well_tests": "F3",
    "production_summaries": "F3",
    "handoff_context": "F3",
    "sample_acquisitions": "F0",
    "samples": "F0",
    "analyses": "FH",
    "condition_monitoring": "F2",
    "environmental_observations": "FH",
    "events": "F0",
    "alarm_journal": "F0",
    "operator_actions": "F0",
    "device_logs": "F0",
    "audit_logs": "F0",
    "delivery_logs": "F2",
    "maintenance_context": "F0",
    "inspection_context": "F0",
}
#: Change-only lifecycle observables that produce `events`.
TRANSITION_KEYS = (
    ("facility", "comp_mode", "U05", "compressor_mode_change"),
    ("facility", "transfer_pump_run", "U07", "oil_transfer_state_change"),
    ("facility", "water_pump_run", "U06", "water_transfer_state_change"),
    ("facility", "gen_availability", "U09", "utility_availability_change"),
    ("facility", "flare_route", "U10", "flare_route_change"),
    ("facility", "test_state", "U11", "well_test_window_change"),
    ("facility", "sample_workflow", "U11", "sample_workflow_change"),
    ("facility", "maintenance_state", "U11", "maintenance_state_change"),
    ("facility", "reinjection_route", "U06", "reinjection_route_change"),
)
WELL_TRANSITION_KEYS = (
    ("well_online", "U01", "well_production_state_change"),
    ("lift_run", "U03", "lift_run_state_change"),
)
ALARM_EVENT_TYPES = (
    "utility_availability_change",
    "compressor_mode_change",
    "lift_run_state_change",
    "scenario_onset",
)
SAMPLE_POINTS = (
    ("oil_tank_battery", "U11", "fictional_oil_stream", "density_surrogate_index"),
    ("produced_water_tank", "U11", "fictional_produced_water_stream", "water_property_surrogate_index"),
    ("dehydration_unit", "U11", "fictional_gas_stream", "outlet_quality_surrogate_index"),
)
CONDITION_ASSET_TYPES = (
    "field_compressor",
    "oil_transfer_pump",
    "water_transfer_pump",
    "heater_treater",
    "inlet_separator",
)


class _Builder:
    """Deterministic sparse-record builder bound to one simulation context."""

    def __init__(self, context: dict[str, Any]) -> None:
        self.context = context
        self.config = context["config"]
        self.run_id = context["run_id"]
        self.scenario = context["scenario"]
        self.scenario_id = context["scenario_id"]
        self.catalog = context["catalog"]
        self.series = context["series"]
        self.start: datetime = context["start"]
        self.end: datetime = context["end"]
        self.onset: datetime = context["onset"]
        self.interval: int = context["interval"]
        self.site_id = self.config["site"]["site_id"]
        self.facility_id = self.config["facility"]["facility_id"]
        self.seed = self.config["seed"]
        self.duration = int((self.end - self.start).total_seconds())
        self.latest_public = self.end - timedelta(seconds=1)
        self.latest_delivery = self.end + timedelta(seconds=MAX_DELIVERY_DELAY_SECONDS - 1)
        self.signals_by_asset: dict[str, list[dict[str, Any]]] = {}
        for signal in self.catalog["signals"]:
            self.signals_by_asset.setdefault(signal["asset_id"], []).append(signal)
        self.assets_by_type: dict[str, list[dict[str, Any]]] = {}
        for asset in self.catalog["assets"]:
            self.assets_by_type.setdefault(asset["asset_type"], []).append(asset)
        self.device_by_asset = {device["asset_id"]: device for device in self.catalog["devices"]}
        self.wells = sorted(self.catalog["wells"], key=lambda item: item["well_index"])
        self.target_well_ids = list(self.series[0]["target_well_ids"]) if self.series else []
        self.records: dict[str, list[dict[str, Any]]] = {name: [] for name in WORKFLOW_RECORD_TYPES}
        self.records["ground_truth"] = []

    # -- helpers ---------------------------------------------------------
    def observed(self, value: datetime) -> str:
        return stamp(min(max(value, self.start), self.latest_public))

    def delivered(self, value: datetime) -> str:
        return stamp(min(max(value, self.start), self.latest_delivery))

    def at(self, ratio: float) -> datetime:
        return self.start + timedelta(seconds=int(self.duration * clamp(ratio, 0.0, 0.999)))

    def facility(self, asset_type: str) -> dict[str, Any]:
        for asset in self.assets_by_type.get(asset_type, []):
            if asset["well_id"] is None:
                return asset
        raise KeyError(f"facility asset not found: {asset_type}")

    def evidence(self, asset_id: str, limit: int = 3) -> list[str]:
        return [signal["signal_id"] for signal in self.signals_by_asset.get(asset_id, [])][:limit]

    def base(self, record_type: str, area_id: str, *id_parts: str) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "run_id": self.run_id,
            "scenario_id": self.scenario_id,
            "site_id": self.site_id,
            "area_id": area_id,
            "record_type": record_type,
            "record_id": stable_id(record_type, self.run_id, *id_parts),
            "fidelity": RECORD_FIDELITY.get(record_type, "F0"),
            "is_synthetic": True,
            "operational_use": "prohibited",
        }

    def close(self, record: dict[str, Any], **envelope_kwargs: Any) -> dict[str, Any]:
        record.update(envelope(**envelope_kwargs))
        record["is_synthetic"] = True
        record["operational_use"] = "prohibited"
        return record

    def frames(self, low: int, high: int) -> list[dict[str, Any]]:
        return self.series[low:high]

    def integrate(self, frames: list[dict[str, Any]], scope: str, key: str, well_id: str | None) -> float:
        total = 0.0
        for frame in frames:
            value = frame["wells"][well_id][key] if scope == "well" else frame["facility"][key]
            total += value * self.interval / 86400.0
        return total

    def average(self, frames: list[dict[str, Any]], scope: str, key: str, well_id: str | None) -> float:
        if not frames:
            return 0.0
        total = 0.0
        for frame in frames:
            total += frame["wells"][well_id][key] if scope == "well" else frame["facility"][key]
        return total / len(frames)

    def interpretation(self, area_id: str) -> str:
        if self.context["delivery_only"]:
            return "insufficient_evidence"
        if area_id in self.context["scenario_areas"]:
            return "consistent_with"
        return "possible"

    # -- families --------------------------------------------------------
    def build_events(self) -> None:
        onset_event = self.base("events", self.context["scenario_areas"][0], "scenario_onset")
        onset_event.update(
            {
                "event_id": onset_event["record_id"],
                "asset_id": None,
                "well_id": None,
                "device_id": None,
                "event_type": "scenario_onset",
                "event_class": "lifecycle",
                "severity": "info" if self.scenario == "normal_production" else "attention",
                "state_transition": {"from": "baseline", "to": self.scenario},
                "correlation_id": stable_id("correlation", self.run_id, self.scenario),
                "summary": f"Synthetic upstream monitoring window entered state {self.scenario}.",
                "evidence_signal_ids": [],
                "interpretation": "consistent_with" if self.scenario != "normal_production" else "possible",
            }
        )
        moment = self.onset
        self.records["events"].append(
            self.close(
                onset_event,
                event_time=self.observed(moment),
                source_time=self.observed(moment),
                server_time=self.delivered(moment + timedelta(seconds=3)),
                ingest_time=self.delivered(moment + timedelta(seconds=6)),
                origin_state="workflow_entry",
                delivery_state="workflow_delivered",
            )
        )

        for scope, key, area_id, event_type in TRANSITION_KEYS:
            previous: float | None = None
            emitted = 0
            asset_type = {
                "comp_mode": "field_compressor",
                "transfer_pump_run": "oil_transfer_pump",
                "water_pump_run": "water_transfer_pump",
                "gen_availability": "field_generator",
                "flare_route": "flare_vent_router",
                "test_state": "well_test_separator",
                "sample_workflow": "sample_and_lab_context",
                "maintenance_state": "maintenance_registry",
                "reinjection_route": "reinjection_interface",
            }[key]
            asset = self.facility(asset_type)
            for frame in self.series:
                value = frame["facility"][key]
                if previous is not None and round6(value) != round6(previous) and emitted < 6:
                    emitted += 1
                    moment = frame["time"]
                    record = self.base("events", area_id, event_type, str(frame["tick"]))
                    record.update(
                        {
                            "event_id": record["record_id"],
                            "asset_id": asset["asset_id"],
                            "well_id": None,
                            "device_id": self.device_by_asset[asset["asset_id"]]["device_id"],
                            "event_type": event_type,
                            "event_class": "state_transition",
                            "severity": "attention" if area_id in self.context["scenario_areas"] else "info",
                            "state_transition": {"from": f"{round6(previous)}", "to": f"{round6(value)}"},
                            "correlation_id": stable_id("correlation", self.run_id, event_type),
                            "summary": f"Synthetic {event_type.replace('_', ' ')} observed on {asset['display_name']}.",
                            "evidence_signal_ids": self.evidence(asset["asset_id"], 2),
                            "interpretation": self.interpretation(area_id),
                        }
                    )
                    self.records["events"].append(
                        self.close(
                            record,
                            event_time=self.observed(moment),
                            source_time=self.observed(moment),
                            server_time=self.delivered(moment + timedelta(seconds=2)),
                            ingest_time=self.delivered(moment + timedelta(seconds=5)),
                            origin_state="workflow_entry",
                            delivery_state="workflow_delivered",
                        )
                    )
                previous = value

        for well in self.wells:
            for key, area_id, event_type in WELL_TRANSITION_KEYS:
                previous = None
                emitted = 0
                asset_type = "well_context" if key == "well_online" else "artificial_lift"
                asset = next(
                    item
                    for item in self.assets_by_type[asset_type]
                    if item["well_id"] == well["well_id"]
                )
                for frame in self.series:
                    value = frame["wells"][well["well_id"]][key]
                    if previous is not None and round6(value) != round6(previous) and emitted < 4:
                        emitted += 1
                        moment = frame["time"]
                        record = self.base("events", area_id, event_type, well["well_id"], str(frame["tick"]))
                        record.update(
                            {
                                "event_id": record["record_id"],
                                "asset_id": asset["asset_id"],
                                "well_id": well["well_id"],
                                "device_id": self.device_by_asset[asset["asset_id"]]["device_id"],
                                "event_type": event_type,
                                "event_class": "state_transition",
                                "severity": "attention" if well["well_id"] in self.target_well_ids else "info",
                                "state_transition": {"from": f"{round6(previous)}", "to": f"{round6(value)}"},
                                "correlation_id": stable_id("correlation", self.run_id, event_type, well["well_id"]),
                                "summary": f"Synthetic {event_type.replace('_', ' ')} on {well['display_name']}.",
                                "evidence_signal_ids": self.evidence(asset["asset_id"], 2),
                                "interpretation": self.interpretation(area_id),
                            }
                        )
                        self.records["events"].append(
                            self.close(
                                record,
                                event_time=self.observed(moment),
                                source_time=self.observed(moment),
                                server_time=self.delivered(moment + timedelta(seconds=2)),
                                ingest_time=self.delivered(moment + timedelta(seconds=5)),
                                origin_state="workflow_entry",
                                delivery_state="workflow_delivered",
                            )
                        )
                    previous = value
        self.records["events"].sort(key=lambda item: (item["event_time_utc"], item["record_id"]))

    def build_alarm_journal(self) -> None:
        for event in self.records["events"]:
            if event["event_type"] not in ALARM_EVENT_TYPES:
                continue
            moment = parse(event["event_time_utc"])
            record = self.base("alarm_journal", event["area_id"], event["event_id"])
            record.update(
                {
                    "alarm_id": stable_id("alarm", self.run_id, event["event_id"]),
                    "event_id": event["event_id"],
                    "asset_id": event["asset_id"],
                    "well_id": event["well_id"],
                    "alarm_state": "raised" if event["severity"] == "attention" else "cleared",
                    "priority_class": "monitoring",
                    "correlation_id": event["correlation_id"],
                    "message": f"Synthetic alarm lifecycle evidence for {event['event_type']}.",
                    "interpretation": event["interpretation"],
                }
            )
            self.records["alarm_journal"].append(
                self.close(
                    record,
                    event_time=self.observed(moment),
                    source_time=self.observed(moment),
                    server_time=self.delivered(moment + timedelta(seconds=4)),
                    ingest_time=self.delivered(moment + timedelta(seconds=9)),
                    origin_state="workflow_entry",
                    delivery_state="workflow_delivered",
                )
            )

    def build_operator_actions(self) -> None:
        for offset, alarm in enumerate(self.records["alarm_journal"][:4], 1):
            moment = parse(alarm["event_time_utc"]) + timedelta(seconds=offset * 20)
            record = self.base("operator_actions", alarm["area_id"], alarm["alarm_id"])
            record.update(
                {
                    "action_id": stable_id("action", self.run_id, alarm["alarm_id"]),
                    "alarm_id": alarm["alarm_id"],
                    "event_id": alarm["event_id"],
                    "asset_id": alarm["asset_id"],
                    "well_id": alarm["well_id"],
                    "action_type": "acknowledge_and_review",
                    "actor_role": "fictional_field_monitoring_role",
                    "note": "Synthetic acknowledgement note. No executable command is represented.",
                }
            )
            self.records["operator_actions"].append(
                self.close(
                    record,
                    event_time=self.observed(moment),
                    source_time=self.observed(moment),
                    server_time=self.delivered(moment + timedelta(seconds=3)),
                    ingest_time=self.delivered(moment + timedelta(seconds=7)),
                    origin_state="workflow_entry",
                    delivery_state="workflow_delivered",
                )
            )

    def build_device_logs(self) -> None:
        degraded = self.scenario == "communications_degradation"
        for device in sorted(self.catalog["devices"], key=lambda item: item["device_index"]):
            for phase, ratio in (("heartbeat", 0.10), ("diagnostic", 0.70)):
                moment = self.at(ratio)
                record = self.base("device_logs", device["area_id"], device["device_id"], phase)
                record.update(
                    {
                        "log_id": stable_id("device-log", self.run_id, device["device_id"], phase),
                        "device_id": device["device_id"],
                        "asset_id": device["asset_id"],
                        "well_id": device["well_id"],
                        "level": "warning" if degraded and phase == "diagnostic" else "info",
                        "category": "synthetic_device_lifecycle",
                        "code": f"UPSTREAM_{phase.upper()}",
                        "message": f"Synthetic {phase} record for {device['display_name']}.",
                        "device_role": device["device_role"],
                        "transport_class": device["transport_class"],
                    }
                )
                self.records["device_logs"].append(
                    self.close(
                        record,
                        event_time=self.observed(moment),
                        source_time=self.observed(moment),
                        server_time=self.delivered(moment + timedelta(seconds=2)),
                        ingest_time=self.delivered(moment + timedelta(seconds=4 + (90 if degraded else 0))),
                        quality_class="uncertain" if degraded and phase == "diagnostic" else "good",
                        quality_reason="late_delivery" if degraded and phase == "diagnostic" else "nominal",
                        freshness_state="aging" if degraded and phase == "diagnostic" else "fresh",
                        origin_state="workflow_entry",
                        delivery_state="late" if degraded and phase == "diagnostic" else "workflow_delivered",
                    )
                )

    def build_delivery_logs(self) -> None:
        gateway = self.facility("field_rtu_gateway")
        historian = self.facility("historian_node")
        degraded = self.scenario == "communications_degradation"
        batch_count = 4
        for index in range(batch_count):
            ratio = (index + 0.5) / batch_count
            moment = self.at(ratio)
            frame = self.series[min(int(ratio * len(self.series)), len(self.series) - 1)]
            queue_depth = int(round(frame["facility"]["queue_depth"]))
            latency_ms = round6(frame["facility"]["latency_ms"])
            record = self.base("delivery_logs", "U12", "batch", str(index))
            record.update(
                {
                    "delivery_id": stable_id("delivery", self.run_id, "batch", str(index)),
                    "batch_id": stable_id("batch", self.run_id, str(index)),
                    "source_id": gateway["asset_id"],
                    "destination_id": historian["asset_id"],
                    "source_domain": "fictional_field_collection",
                    "destination_domain": "fictional_upstream_monitoring_store",
                    "queue_depth": queue_depth,
                    "latency_ms": latency_ms,
                    "replay_state": "replayed" if degraded and index >= 2 else "not_replayed",
                    "sequence_gap_count": 1 if degraded else 0,
                    "record_count": max(1, len(self.context["telemetry"]) // batch_count),
                    "interpretation": "consistent_with" if degraded else "possible",
                }
            )
            self.records["delivery_logs"].append(
                self.close(
                    record,
                    event_time=self.observed(moment),
                    source_time=self.observed(moment),
                    server_time=self.delivered(moment + timedelta(milliseconds=int(latency_ms * 0.6))),
                    ingest_time=self.delivered(moment + timedelta(milliseconds=int(latency_ms))),
                    quality_class="uncertain" if degraded else "good",
                    quality_reason="store_and_forward_replay" if degraded else "nominal",
                    freshness_state="aging" if degraded else "fresh",
                    origin_state="workflow_entry",
                    delivery_state="store_and_forward" if degraded else "batch_delivered",
                    clock_offset_ms=frame["facility"]["clock_offset"],
                    clock_uncertainty_ms=28.0 if degraded else 4.0,
                )
            )

    def build_well_tests(self) -> None:
        low = int(len(self.series) * 0.30)
        high = max(low + 1, int(len(self.series) * 0.55))
        frames = self.frames(low, high)
        if not frames:
            return
        period_start = frames[0]["time"]
        period_end = frames[-1]["time"] + timedelta(seconds=self.interval)
        separator = self.facility("well_test_separator")
        retracting = self.scenario in {"deliverability_decline", "liquid_loading", "tank_transfer_imbalance"}
        for well in self.wells:
            oil = self.average(frames, "well", "q_oil", well["well_id"])
            water = self.average(frames, "well", "q_water", well["well_id"])
            gas = self.average(frames, "well", "q_gas", well["well_id"])
            tubing = self.average(frames, "well", "p_tubing", well["well_id"])
            test_id = stable_id("well-test", self.run_id, well["well_id"])
            uncertainty = round6(0.06 + 0.04 * hash_float(self.seed, well["well_id"], "test", low=0.0, high=1.0))
            targeted = well["well_id"] in self.target_well_ids
            previous_record_id: str | None = None
            for revision, state in enumerate(
                ("provisional", "retracted" if retracting and targeted else "validated"), 1
            ):
                available = period_end + timedelta(seconds=600 * revision)
                record = self.base("well_tests", "U11", well["well_id"], str(revision))
                record.update(
                    {
                        "well_test_id": test_id,
                        "well_id": well["well_id"],
                        "asset_id": separator["asset_id"],
                        "device_id": self.device_by_asset[separator["asset_id"]]["device_id"],
                        "test_method_class": "fictional_separator_test",
                        "oil_rate_m3d": round6(oil),
                        "water_rate_m3d": round6(water),
                        "gas_rate_ksm3d": round6(gas),
                        "flowing_pressure_kpa": round6(tubing),
                        "rate_uncertainty_index": uncertainty,
                        "evidence_signal_ids": self.evidence(separator["asset_id"], 2),
                        "interpretation": self.interpretation("U11"),
                        "allocation_claim": "none",
                    }
                )
                self.records["well_tests"].append(
                    self.close(
                        record,
                        event_time=self.observed(period_end - timedelta(seconds=1)),
                        source_time=self.observed(period_end - timedelta(seconds=1)),
                        server_time=self.delivered(available),
                        ingest_time=self.delivered(available + timedelta(seconds=30)),
                        available_time=self.delivered(available),
                        period_start=self.observed(period_start),
                        period_end=self.observed(period_end - timedelta(seconds=1)),
                        quality_class="uncertain" if revision == 1 else "good",
                        quality_reason="provisional_result" if revision == 1 else "revised_result",
                        freshness_state="aging",
                        origin_state="workflow_entry",
                        delivery_state="late",
                        revision=revision,
                        supersedes_record_id=previous_record_id,
                        validation_state=state,
                    )
                )
                previous_record_id = record["record_id"]

    def build_production_summaries(self) -> None:
        period_seconds = min(3600, self.duration)
        ticks_per_period = max(1, period_seconds // self.interval)
        bucket_count = max(1, len(self.series) // ticks_per_period)
        for bucket in range(bucket_count):
            low = bucket * ticks_per_period
            high = low + ticks_per_period if bucket < bucket_count - 1 else len(self.series)
            frames = self.frames(low, high)
            if not frames:
                continue
            period_start = frames[0]["time"]
            period_end = frames[-1]["time"] + timedelta(seconds=self.interval)
            scopes: list[tuple[str, str | None]] = [("well", well["well_id"]) for well in self.wells]
            scopes.append(("facility", None))
            for scope, well_id in scopes:
                if scope == "well":
                    oil = self.integrate(frames, "well", "q_oil", well_id)
                    water = self.integrate(frames, "well", "q_water", well_id)
                    gas = self.integrate(frames, "well", "q_gas", well_id)
                    uptime = self.average(frames, "well", "well_online", well_id)
                else:
                    oil = self.integrate(frames, "facility", "oil_rate", None)
                    water = self.integrate(frames, "facility", "water_rate", None)
                    gas = self.integrate(frames, "facility", "gas_rate", None)
                    uptime = self.average(frames, "facility", "gen_availability", None)
                summary_id = stable_id("summary", self.run_id, scope, well_id or self.facility_id, str(bucket))
                previous_record_id = None
                revisions = ("provisional", "validated") if bucket == bucket_count - 1 else ("validated",)
                for revision, state in enumerate(revisions, 1):
                    available = period_end + timedelta(seconds=900 * revision)
                    record = self.base(
                        "production_summaries", "U11", scope, well_id or self.facility_id, str(bucket), str(revision)
                    )
                    record.update(
                        {
                            "summary_id": summary_id,
                            "summary_scope": scope,
                            "well_id": well_id,
                            "facility_id": self.facility_id,
                            "oil_volume_m3": round6(oil),
                            "water_volume_m3": round6(water),
                            "gas_volume_ksm3": round6(gas),
                            "uptime_index": round6(uptime),
                            "summary_basis": "derived_from_public_observations",
                            "allocation_claim": "none",
                            "interpretation": self.interpretation("U11"),
                        }
                    )
                    self.records["production_summaries"].append(
                        self.close(
                            record,
                            event_time=self.observed(period_end - timedelta(seconds=1)),
                            source_time=self.observed(period_end - timedelta(seconds=1)),
                            server_time=self.delivered(available),
                            ingest_time=self.delivered(available + timedelta(seconds=45)),
                            available_time=self.delivered(available),
                            period_start=self.observed(period_start),
                            period_end=self.observed(period_end - timedelta(seconds=1)),
                            quality_class="uncertain" if state == "provisional" else "good",
                            quality_reason="provisional_summary" if state == "provisional" else "revised_summary",
                            freshness_state="aging",
                            origin_state="derived_summary",
                            delivery_state="late",
                            revision=revision,
                            supersedes_record_id=previous_record_id,
                            validation_state=state,
                        )
                    )
                    previous_record_id = record["record_id"]

    def build_handoff_context(self) -> None:
        imbalance = self.scenario == "tank_transfer_imbalance"
        definitions = (
            ("oil_gathering", "sales_gathering_meter", "oil_tank_battery", "handoff_rate", "oil_inventory", "U07"),
            ("gas_gathering", "gas_gathering_meter", "dehydration_unit", "gas_handoff_rate", "gas_handoff_total", "U07"),
            ("water_disposal_interface", "reinjection_interface", "produced_water_tank", "water_transfer_rate", "water_inventory", "U07"),
        )
        for domain, meter_type, source_type, rate_key, inventory_key, area_id in definitions:
            meter = self.facility(meter_type)
            source = self.facility(source_type)
            transfer_id = stable_id("transfer", self.run_id, domain)
            for phase, ratio, state in (
                ("start", 0.20, "active"),
                ("middle", 0.55, "active"),
                ("complete", 0.90, "complete"),
            ):
                index = min(int(ratio * len(self.series)), len(self.series) - 1)
                frame = self.series[index]
                moment = frame["time"]
                window_low = max(0, index - max(1, len(self.series) // 6))
                frames = self.frames(window_low, index + 1)
                inventory_change = (
                    frame["facility"][inventory_key] - self.series[window_low]["facility"][inventory_key]
                )
                delivered_index = self.integrate(frames, "facility", rate_key, None)
                record = self.base("handoff_context", area_id, domain, phase)
                record.update(
                    {
                        "handoff_id": stable_id("handoff", self.run_id, domain, phase),
                        "transfer_id": transfer_id,
                        "destination_domain": domain,
                        "meter_asset_id": meter["asset_id"],
                        "source_asset_id": source["asset_id"],
                        "transfer_state": state,
                        "operational_rate_index": round6(frame["facility"][rate_key]),
                        "operational_total_index": round6(delivered_index),
                        "inventory_change_index": round6(inventory_change),
                        "reconciliation_state": (
                            "unreconciled_within_synthetic_tolerance"
                            if imbalance and domain == "oil_gathering"
                            else "reconciled_within_synthetic_tolerance"
                        ),
                        "uncertainty_index": round6(0.05 + (0.09 if imbalance and domain == "oil_gathering" else 0.0)),
                        "custody_claim": "none",
                        "interpretation": self.interpretation(area_id),
                        "evidence_signal_ids": self.evidence(meter["asset_id"], 3),
                    }
                )
                self.records["handoff_context"].append(
                    self.close(
                        record,
                        event_time=self.observed(moment),
                        source_time=self.observed(moment),
                        server_time=self.delivered(moment + timedelta(seconds=20)),
                        ingest_time=self.delivered(moment + timedelta(seconds=45)),
                        period_start=self.observed(self.series[window_low]["time"]),
                        period_end=self.observed(moment),
                        origin_state="derived_summary",
                        delivery_state="batch_delivered",
                        validation_state="provisional" if state == "active" else "validated",
                    )
                )

    def build_sample_workflow(self) -> None:
        for point_index, (asset_type, area_id, stream, analyte) in enumerate(SAMPLE_POINTS):
            asset = self.facility(asset_type)
            acquisition_time = self.at(0.62 + point_index * 0.02)
            acquisition_id = stable_id("acquisition", self.run_id, asset_type)
            sample_id = stable_id("sample", self.run_id, asset_type)
            record = self.base("sample_acquisitions", area_id, asset_type)
            record.update(
                {
                    "acquisition_id": acquisition_id,
                    "asset_id": asset["asset_id"],
                    "well_id": None,
                    "stream_label": stream,
                    "acquisition_method_class": "fictional_manual_grab",
                    "container_class": "fictional_generic_container",
                    "custody_workflow_state": "acquired",
                    "interpretation": self.interpretation(area_id),
                }
            )
            self.records["sample_acquisitions"].append(
                self.close(
                    record,
                    event_time=self.observed(acquisition_time),
                    source_time=self.observed(acquisition_time),
                    server_time=self.delivered(acquisition_time + timedelta(seconds=120)),
                    ingest_time=self.delivered(acquisition_time + timedelta(seconds=180)),
                    origin_state="workflow_entry",
                    delivery_state="workflow_delivered",
                    validation_state="validated",
                )
            )

            sample_record = self.base("samples", area_id, asset_type)
            sample_record.update(
                {
                    "sample_id": sample_id,
                    "acquisition_id": acquisition_id,
                    "subsample_id": stable_id("subsample", self.run_id, asset_type),
                    "asset_id": asset["asset_id"],
                    "stream_label": stream,
                    "sample_state": "received",
                    "custody_workflow_state": "transferred_to_fictional_laboratory",
                    "interpretation": self.interpretation(area_id),
                }
            )
            received = acquisition_time + timedelta(seconds=900)
            self.records["samples"].append(
                self.close(
                    sample_record,
                    event_time=self.observed(acquisition_time),
                    source_time=self.observed(acquisition_time),
                    server_time=self.delivered(received),
                    ingest_time=self.delivered(received + timedelta(seconds=60)),
                    available_time=self.delivered(received),
                    origin_state="workflow_entry",
                    delivery_state="late",
                    validation_state="validated",
                )
            )

            qc_fail = point_index == 1
            revisions = ("provisional", "invalidated", "validated") if qc_fail else ("provisional", "validated")
            previous_record_id = None
            base_value = clamp(
                0.55 + 0.30 * hash_float(self.seed, asset_type, "analysis", low=0.0, high=1.0),
                0.0,
                1.0,
            )
            for revision, state in enumerate(revisions, 1):
                available = acquisition_time + timedelta(seconds=1800 * revision)
                record = self.base("analyses", area_id, asset_type, str(revision))
                record.update(
                    {
                        "analysis_id": stable_id("analysis", self.run_id, asset_type),
                        "sample_id": sample_id,
                        "asset_id": asset["asset_id"],
                        "analyte_label": analyte,
                        "result_index": round6(base_value + 0.02 * revision),
                        "result_uncertainty_index": round6(0.07 + 0.01 * revision),
                        "qc_state": "fail" if state == "invalidated" else "pass",
                        "certified_claim": "none",
                        "interpretation": self.interpretation(area_id),
                    }
                )
                self.records["analyses"].append(
                    self.close(
                        record,
                        event_time=self.observed(acquisition_time),
                        source_time=self.observed(acquisition_time),
                        server_time=self.delivered(available),
                        ingest_time=self.delivered(available + timedelta(seconds=90)),
                        available_time=self.delivered(available),
                        quality_class="uncertain" if state != "validated" else "good",
                        quality_reason="provisional_result" if state == "provisional" else (
                            "quality_control_failure" if state == "invalidated" else "revised_result"
                        ),
                        freshness_state="aging",
                        origin_state="laboratory_entry",
                        delivery_state="late",
                        revision=revision,
                        supersedes_record_id=previous_record_id,
                        validation_state=state,
                    )
                )
                previous_record_id = record["record_id"]

    def build_condition_monitoring(self) -> None:
        half = max(1, len(self.series) // 2)
        windows = ((0, half), (half, len(self.series)))
        targets: list[tuple[dict[str, Any], str, str, str]] = []
        for asset_type in CONDITION_ASSET_TYPES:
            asset = self.facility(asset_type)
            targets.append((asset, asset["area_id"], "facility", ""))
        for well in self.wells:
            asset = next(item for item in self.assets_by_type["artificial_lift"] if item["well_id"] == well["well_id"])
            targets.append((asset, "U03", "well", well["well_id"]))
        for window_index, (low, high) in enumerate(windows):
            frames = self.frames(low, high)
            if not frames:
                continue
            period_start = frames[0]["time"]
            period_end = frames[-1]["time"] + timedelta(seconds=self.interval)
            for asset, area_id, scope, well_id in targets:
                if scope == "well":
                    load = self.average(frames, "well", "lift_load", well_id)
                    cycle = self.average(frames, "well", "lift_cycle", well_id)
                    health = clamp(0.55 + 0.35 * cycle, 0.0, 1.0)
                    vibration = clamp(0.20 + 0.30 * (1.0 - cycle), 0.0, 1.5)
                    thermal = self.average(frames, "well", "t_wellhead", well_id)
                    restriction = self.average(frames, "well", "restriction", well_id)
                else:
                    load = self.average(frames, "facility", "comp_power", None) / 400.0
                    vibration = self.average(frames, "facility", "comp_vibration", None)
                    health = clamp(0.92 - vibration, 0.0, 1.0)
                    thermal = self.average(frames, "facility", "treater_temp", None)
                    restriction = self.average(frames, "facility", "dp_index", None)
                record = self.base("condition_monitoring", area_id, asset["asset_id"], str(window_index))
                record.update(
                    {
                        "condition_id": stable_id("condition", self.run_id, asset["asset_id"], str(window_index)),
                        "asset_id": asset["asset_id"],
                        "device_id": self.device_by_asset[asset["asset_id"]]["device_id"],
                        "well_id": well_id or None,
                        "health_index": round6(health),
                        "vibration_index": round6(vibration),
                        "thermal_index": round6(thermal),
                        "restriction_index": round6(restriction),
                        "load_index": round6(load),
                        "diagnosis_claim": "none",
                        "remaining_life_claim": "none",
                        "interpretation": self.interpretation(area_id),
                        "evidence_signal_ids": self.evidence(asset["asset_id"], 3),
                    }
                )
                self.records["condition_monitoring"].append(
                    self.close(
                        record,
                        event_time=self.observed(period_end - timedelta(seconds=1)),
                        source_time=self.observed(period_end - timedelta(seconds=1)),
                        server_time=self.delivered(period_end + timedelta(seconds=60)),
                        ingest_time=self.delivered(period_end + timedelta(seconds=120)),
                        period_start=self.observed(period_start),
                        period_end=self.observed(period_end - timedelta(seconds=1)),
                        origin_state="derived_summary",
                        delivery_state="batch_delivered",
                        validation_state="validated",
                    )
                )

    def build_environmental_observations(self) -> None:
        detector = self.facility("gas_detection_array")
        weather = self.facility("weather_station")
        flare = self.facility("flare_vent_router")
        for index, ratio in enumerate((0.25, 0.55, 0.85)):
            position = min(int(ratio * len(self.series)), len(self.series) - 1)
            frame = self.series[position]
            moment = frame["time"]
            observation = frame["facility"]["detector_observation"]
            availability = frame["facility"]["detector_availability"]
            if availability <= 0.0:
                level = "indeterminate"
            elif observation >= 0.20:
                level = "elevated"
            else:
                level = "background"
            available = moment + timedelta(seconds=1800 + 600 * index)
            record = self.base("environmental_observations", "U10", "detector", str(index))
            record.update(
                {
                    "observation_id": stable_id("observation", self.run_id, "detector", str(index)),
                    "monitor_asset_id": detector["asset_id"],
                    "context_asset_id": flare["asset_id"],
                    "observation_class": "bounded_qualitative_gas_observation",
                    "qualitative_level": level,
                    "detector_availability_index": round6(availability),
                    "ambient_temperature_c": round6(frame["facility"]["ambient_temp"]),
                    "wind_speed_ms": round6(frame["facility"]["wind_speed"]),
                    "weather_asset_id": weather["asset_id"],
                    "source_attribution": "not_determined",
                    "quantified_release_claim": "none",
                    "compliance_claim": "none",
                    "interpretation": (
                        "possible"
                        if self.scenario in {"gas_release_observation", "flare_vent_environment_workflow"}
                        else "insufficient_evidence"
                    ),
                    "evidence_signal_ids": self.evidence(detector["asset_id"], 2),
                }
            )
            self.records["environmental_observations"].append(
                self.close(
                    record,
                    event_time=self.observed(moment),
                    source_time=self.observed(moment),
                    server_time=self.delivered(available),
                    ingest_time=self.delivered(available + timedelta(seconds=120)),
                    available_time=self.delivered(available),
                    quality_class="uncertain" if level == "indeterminate" else "good",
                    quality_reason="monitor_unavailable" if level == "indeterminate" else "sparse_external_observation",
                    freshness_state="aging",
                    origin_state="external_observation",
                    delivery_state="late",
                    validation_state="provisional",
                )
            )

    def build_maintenance_and_inspection(self) -> None:
        impaired = self.scenario in {
            "maintenance_inspection_workflow",
            "corrosion_erosion_context",
            "compressor_degradation",
            "artificial_lift_degradation",
        }
        focus_types = ("field_compressor", "heater_treater", "oil_transfer_pump")
        for index, asset_type in enumerate(focus_types):
            asset = self.facility(asset_type)
            opened = self.at(0.18 + index * 0.05)
            closed = self.at(0.78 + index * 0.02)
            work_id = stable_id("work-order", self.run_id, asset_type)
            record = self.base("maintenance_context", "U11", asset_type)
            record.update(
                {
                    "maintenance_id": stable_id("maintenance", self.run_id, asset_type),
                    "work_order_id": work_id,
                    "asset_id": asset["asset_id"],
                    "device_id": self.device_by_asset[asset["asset_id"]]["device_id"],
                    "work_class": "fictional_planned_field_work",
                    "work_state": "in_progress" if impaired else "closed",
                    "impairment_state": "impaired" if impaired and index == 0 else "not_impaired",
                    "return_to_service_state": "pending" if impaired and index == 0 else "returned",
                    "finding_summary": "Synthetic maintenance context only. No diagnosis or fitness decision.",
                    "fitness_decision": "none",
                    "interpretation": self.interpretation("U11"),
                }
            )
            self.records["maintenance_context"].append(
                self.close(
                    record,
                    event_time=self.observed(opened),
                    source_time=self.observed(opened),
                    server_time=self.delivered(closed),
                    ingest_time=self.delivered(closed + timedelta(seconds=300)),
                    available_time=self.delivered(closed),
                    period_start=self.observed(opened),
                    period_end=self.observed(closed),
                    origin_state="workflow_entry",
                    delivery_state="workflow_delivered",
                    validation_state="provisional" if impaired else "validated",
                )
            )

            inspected = self.at(0.42 + index * 0.03)
            inspection = self.base("inspection_context", "U11", asset_type)
            inspection.update(
                {
                    "inspection_id": stable_id("inspection", self.run_id, asset_type),
                    "work_order_id": work_id,
                    "asset_id": asset["asset_id"],
                    "device_id": self.device_by_asset[asset["asset_id"]]["device_id"],
                    "inspection_class": "fictional_visual_and_condition_check",
                    "calibration_state": "calibration_due" if impaired and index == 1 else "within_synthetic_interval",
                    "condition_context_index": round6(
                        clamp(
                            0.78 - 0.30 * self.series[-1]["modifiers"]["condition_add"],
                            0.0,
                            1.0,
                        )
                    ),
                    "finding_class": "context_only",
                    "fitness_for_service_claim": "none",
                    "remaining_life_claim": "none",
                    "interpretation": self.interpretation("U11"),
                }
            )
            self.records["inspection_context"].append(
                self.close(
                    inspection,
                    event_time=self.observed(inspected),
                    source_time=self.observed(inspected),
                    server_time=self.delivered(inspected + timedelta(seconds=900)),
                    ingest_time=self.delivered(inspected + timedelta(seconds=1200)),
                    available_time=self.delivered(inspected + timedelta(seconds=900)),
                    origin_state="workflow_entry",
                    delivery_state="late",
                    validation_state="validated",
                )
            )

    def build_audit_logs(self) -> None:
        revisable = ("well_tests", "production_summaries", "analyses")
        for family in revisable:
            for record in self.records[family]:
                if record["revision"] < 2:
                    continue
                moment = parse(record["available_time_utc"] or record["event_time_utc"])
                audit = self.base("audit_logs", "U11", family, record["record_id"])
                audit.update(
                    {
                        "audit_id": stable_id("audit", self.run_id, record["record_id"]),
                        "subject_record_type": family,
                        "subject_record_id": record["record_id"],
                        "superseded_record_id": record["supersedes_record_id"],
                        "action": "append_only_revision",
                        "actor_role": "fictional_data_stewardship_role",
                        "summary": f"Synthetic append-only revision recorded for {family}.",
                        "access_path_claim": "none",
                    }
                )
                self.records["audit_logs"].append(
                    self.close(
                        audit,
                        event_time=self.observed(moment),
                        source_time=self.observed(moment),
                        server_time=self.delivered(moment + timedelta(seconds=30)),
                        ingest_time=self.delivered(moment + timedelta(seconds=60)),
                        origin_state="workflow_entry",
                        delivery_state="workflow_delivered",
                        validation_state="validated",
                    )
                )
        gateway = self.facility("field_rtu_gateway")
        moment = self.at(0.5)
        service = self.base("audit_logs", "U12", "gateway_service")
        service.update(
            {
                "audit_id": stable_id("audit", self.run_id, "gateway_service"),
                "subject_record_type": "devices",
                "subject_record_id": self.device_by_asset[gateway["asset_id"]]["device_id"],
                "superseded_record_id": None,
                "action": "bounded_configuration_review",
                "actor_role": "fictional_ot_service_role",
                "summary": "Synthetic service history entry. No credential or access path is represented.",
                "access_path_claim": "none",
            }
        )
        self.records["audit_logs"].append(
            self.close(
                service,
                event_time=self.observed(moment),
                source_time=self.observed(moment),
                server_time=self.delivered(moment + timedelta(seconds=20)),
                ingest_time=self.delivered(moment + timedelta(seconds=40)),
                origin_state="workflow_entry",
                delivery_state="workflow_delivered",
                validation_state="validated",
            )
        )

    def build_ground_truth(self) -> None:
        truth = SCENARIO_TRUTH[self.scenario]
        final = self.series[-1]["modifiers"] if self.series else {}
        affected_areas = list(self.context["scenario_areas"])
        affected_assets = sorted(
            asset["asset_id"] for asset in self.catalog["assets"] if asset["area_id"] in affected_areas
        )
        record = {
            "schema_version": "1.0",
            "run_id": self.run_id,
            "scenario_id": self.scenario_id,
            "site_id": self.site_id,
            "record_type": "ground_truth",
            "record_id": stable_id("ground-truth", self.run_id, self.scenario),
            "ground_truth_id": stable_id("ground-truth", self.run_id, self.scenario),
            "scenario": self.scenario,
            "latent_change": truth["latent_change"],
            "expected_public_evidence": list(truth["expected_public_evidence"]),
            "confusers": list(truth["confusers"]),
            "safe_fidelity": list(truth["safe_fidelity"]),
            "onset_utc": stamp(self.onset),
            "window_start_utc": stamp(self.start),
            "window_end_utc": stamp(self.end),
            "affected_area_ids": affected_areas,
            "affected_asset_ids": affected_assets,
            "target_well_ids": list(self.target_well_ids),
            "delivery_layer_only": bool(self.context["delivery_only"]),
            "measurement_layer_only": bool(self.context["measurement_only"]),
            "latent_parameters": {key: round6(value) for key, value in sorted(final.items())},
            "actual_physical_state_signature": self.context["physical_signature"],
            "abstention_guidance": (
                "Public analytics must use consistent_with, possible, or insufficient_evidence "
                "and must abstain when evidence is missing, bad, or observationally ambiguous."
            ),
            "publication_policy": "never_publish_to_monitoring_sink",
            "fidelity": "F2",
            "is_synthetic": True,
            "operational_use": "prohibited",
        }
        self.records["ground_truth"].append(record)

    def build(self) -> dict[str, list[dict[str, Any]]]:
        if self.config["fidelity"]["sparse_workflows"]:
            self.build_events()
            self.build_alarm_journal()
            self.build_operator_actions()
            self.build_device_logs()
            self.build_delivery_logs()
            self.build_well_tests()
            self.build_production_summaries()
            self.build_handoff_context()
            self.build_sample_workflow()
            self.build_condition_monitoring()
            self.build_environmental_observations()
            self.build_maintenance_and_inspection()
            self.build_audit_logs()
        self.build_ground_truth()
        for name in self.records:
            self.records[name].sort(key=lambda item: (item.get("event_time_utc", ""), item["record_id"]))
        return self.records


def build_workflow_records(context: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Build every sparse public family plus the private ground-truth family."""
    return _Builder(context).build()


__all__ = ["RECORD_FIDELITY", "SCENARIO_TRUTH", "build_workflow_records"]
