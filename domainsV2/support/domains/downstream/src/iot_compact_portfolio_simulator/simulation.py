"""Streaming compact-portfolio telemetry, events, summaries, and truth isolation."""

from __future__ import annotations

import copy
import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .catalog import (
    CADENCE_SECONDS,
    MODULE_BY_ID,
    MODULE_DEFINITIONS,
    PRODUCT_FAMILIES,
)
from .io import canonical_json

DETECTOR_STATES = {"clear", "estimated", "abstained", "not_required"}


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _round(value: float) -> float:
    return round(float(value), 6)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _hash_id(prefix: str, *parts: object, size: int = 20) -> str:
    material = "|".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(material).hexdigest()[:size]}"


@dataclass
class TelemetryFileEntry:
    path: str
    record_count: int
    sha256: str


class JsonlShardWriter:
    def __init__(self, root: Path, target_records: int) -> None:
        self.root = root
        self.target_records = target_records
        self.root.mkdir(parents=True, exist_ok=True)
        self._index = 0
        self._stream = None
        self._count = 0
        self._digest = None
        self._entries: list[TelemetryFileEntry] = []

    def _close_current(self) -> None:
        if self._stream is None or self._digest is None:
            return
        self._stream.close()
        relative_path = f"telemetry/part-{self._index:05d}.jsonl"
        self._entries.append(
            TelemetryFileEntry(
                path=relative_path,
                record_count=self._count,
                sha256=self._digest.hexdigest(),
            )
        )
        self._stream = None
        self._count = 0
        self._digest = None

    def _open_next(self) -> None:
        self._index += 1
        path = self.root / f"part-{self._index:05d}.jsonl"
        self._stream = path.open("w", encoding="utf-8", newline="\n")
        self._count = 0
        self._digest = hashlib.sha256()

    def write(self, record: dict[str, Any]) -> None:
        if self._stream is None:
            self._open_next()
        elif self._count >= self.target_records:
            self._close_current()
            self._open_next()
        text = canonical_json(record) + "\n"
        self._stream.write(text)
        assert self._digest is not None
        self._digest.update(text.encode("utf-8"))
        self._count += 1

    def close(self) -> list[dict[str, Any]]:
        self._close_current()
        return [
            {
                "path": entry.path,
                "record_type": "telemetry",
                "format": "jsonl",
                "record_count": entry.record_count,
                "sha256": entry.sha256,
            }
            for entry in self._entries
        ]


def _module_signal_offsets(module_id: str) -> dict[str, float]:
    position = list(MODULE_BY_ID).index(module_id)
    return {
        "temperature": 0.03 + position * 0.005,
        "pressure": 0.02 + position * 0.004,
        "utility": 0.01 + position * 0.003,
        "condition": 0.015 + position * 0.002,
    }


def _normalized_yields(module: dict[str, Any], scenario: str, progress: float) -> dict[str, float]:
    weights = copy.deepcopy(module["yield_shares"])
    if scenario == "blend_shift" and module["module_id"] in {"enterprise_blending_dispatch", "renewable_fuels"}:
        shift = 0.08 * progress
        weights["renewable_products"] += shift
        weights["gasoline_and_blendstocks"] = max(0.02, weights["gasoline_and_blendstocks"] - shift * 0.55)
        weights["middle_distillates"] = max(0.02, weights["middle_distillates"] - shift * 0.45)
    total = sum(weights.values())
    return {family: _round(value / total) for family, value in weights.items()}


def _corridor_profile(actual: float, withdrawal: float, fraction: float) -> tuple[list[float], int]:
    segment_count = 6
    node_count = 7
    actual_segment_index = min(segment_count - 1, max(0, int(fraction * segment_count)))
    base_drop = 0.04 + actual * 0.03
    extra_drop = withdrawal * 0.18
    node_pressures = [0.84]
    for node_index in range(segment_count):
        drop = base_drop
        if node_index == actual_segment_index:
            drop += extra_drop
        elif node_index > actual_segment_index:
            drop -= extra_drop * 0.35
        node_pressures.append(_round(max(0.18, node_pressures[-1] - drop)))
    if len(node_pressures) > node_count:
        node_pressures = node_pressures[:node_count]
    return node_pressures, actual_segment_index + 1


def simulate(
    config: dict[str, Any],
    run_id: str,
    assets: list[dict[str, Any]],
    devices: list[dict[str, Any]],
    tags: list[dict[str, Any]],
    output_directory: Path,
) -> dict[str, Any]:
    enterprise_id = config["enterprise"]["enterprise_id"]
    scenario = config["scenario"]
    start = datetime.fromisoformat(config["simulation"]["start_utc"][:-1] + "+00:00")
    end = datetime.fromisoformat(config["simulation"]["end_utc"][:-1] + "+00:00")
    total_ticks = int((end - start).total_seconds() / 5)
    onset_tick = min(max(1, total_ticks // 3), max(1, total_ticks - 1))
    onset = start + timedelta(seconds=onset_tick * 5)
    scenario_id = _hash_id("scenario", run_id, scenario, size=16)
    telemetry_writer = JsonlShardWriter(
        output_directory / "telemetry",
        config["output"]["telemetry_shard_target_records"],
    )

    assets_by_id = {asset["asset_id"]: asset for asset in assets}
    devices_by_id = {device["device_id"]: device for device in devices}
    tags_by_module: dict[str, list[dict[str, Any]]] = {}
    for tag in tags:
        tags_by_module.setdefault(tag["module_id"], []).append(tag)
    for module_tags in tags_by_module.values():
        module_tags.sort(key=lambda item: item["tag_id"])

    receipt_assets = [
        asset["asset_id"]
        for asset in assets
        if asset["module_id"] == "receipt_offsites" and asset["asset_type"] == "monitoring_node"
    ]
    receipt_assets.sort()
    corridor_segments = [
        asset["asset_id"]
        for asset in assets
        if asset["module_id"] == "receipt_offsites" and asset["asset_type"] == "corridor_segment"
    ]
    corridor_segments.sort()
    corridor_segments = corridor_segments[:6]
    detector_asset_id = next(
        asset["asset_id"]
        for asset in assets
        if asset["module_id"] == "receipt_offsites" and asset["asset_type"] == "analytics_engine"
    )

    heartbeat_device_ids = [device["device_id"] for device in devices if device["device_role"] == "communications_gateway"][:9]
    measurement_bias_targets = {
        tag["tag_id"]
        for tag in tags
        if tag["module_id"] == "receipt_offsites" and tag["measurement"] == "inventory_equivalent_volume"
    }
    duplicate_targets = {
        tag["tag_id"]
        for tag in tags
        if tag["module_id"] == "receipt_offsites" and tag["measurement"] == "corridor_outlet_flow"
    }
    store_forward_targets = {
        tag["tag_id"]
        for tag in tags
        if tag["module_id"] == "receipt_offsites" and tag["measurement"] == "corridor_line_pressure"
    }

    quality_good = {"validity": "good", "source": "synthetic_model", "reasons": []}
    events: list[dict[str, Any]] = []
    device_logs: list[dict[str, Any]] = []
    product_accumulators = {
        module["module_id"]: {
            "throughput_sum": 0.0,
            "tick_count": 0,
            "yield_sum": {family: 0.0 for family in PRODUCT_FAMILIES},
            "equivalent_volume": {family: 0.0 for family in PRODUCT_FAMILIES},
            "inventory_open": module["initial_inventory_units"],
            "inventory_close": module["initial_inventory_units"],
        }
        for module in MODULE_DEFINITIONS
    }
    scheduled_count = 0
    scenario_added_count = 0
    evidence_telemetry_ids: list[str] = []
    latest_source_by_tag: dict[str, tuple[Any, datetime]] = {}

    module_states = {
        module["module_id"]: {
            "throughput_target": module["base_throughput_index"] * 100.0,
            "throughput_actual": module["base_throughput_index"] * 96.0,
            "inventory": module["initial_inventory_units"],
            "temperature_index": 0.48,
            "pressure_index": 0.51,
            "utility_load_index": 0.44,
            "condition_index": 0.87,
            "yields": copy.deepcopy(module["yield_shares"]),
        }
        for module in MODULE_DEFINITIONS
    }

    def emit_device_log(
        *,
        code: str,
        message: str,
        device_id: str | None,
        asset_id: str | None,
        module_id: str | None,
        at: datetime,
        level: str = "info",
        category: str = "synthetic_operations",
        queue_depth: int = 0,
        clock_state: str = "synchronized",
        quality: dict[str, Any] | None = None,
    ) -> None:
        log_id = _hash_id("log", run_id, code, device_id or asset_id or module_id, at.isoformat(), len(device_logs))
        site_id = MODULE_BY_ID[module_id]["site_id"] if module_id else None
        device_logs.append(
            {
                "schema_version": "1.0",
                "log_id": log_id,
                "run_id": run_id,
                "scenario_id": scenario_id,
                "enterprise_id": enterprise_id,
                "site_id": site_id,
                "module_id": module_id,
                "asset_id": asset_id,
                "device_id": device_id,
                "event_time_utc": _stamp(at),
                "source_time_utc": _stamp(at),
                "ingest_time_utc": _stamp(at),
                "level": level,
                "category": category,
                "code": code,
                "message": message,
                "queue_depth": queue_depth,
                "clock_state": clock_state,
                "clock_offset_ms": 37.0 if clock_state == "holdover" else 4.0,
                "clock_uncertainty_ms": 9.0 if clock_state == "holdover" else 1.5,
                "quality": quality or quality_good,
                "is_synthetic": True,
                "operational_use": "prohibited",
            }
        )

    def emit_event(
        *,
        event_type: str,
        scope: str,
        at: datetime,
        site_id: str | None = None,
        module_id: str | None = None,
        asset_id: str | None = None,
        severity: str = "information",
        details: dict[str, Any] | None = None,
    ) -> str:
        event_id = _hash_id("event", run_id, event_type, scope, site_id, module_id, asset_id, at.isoformat(), len(events))
        events.append(
            {
                "schema_version": "1.0",
                "event_id": event_id,
                "run_id": run_id,
                "scenario_id": scenario_id,
                "enterprise_id": enterprise_id,
                "site_id": site_id,
                "module_id": module_id,
                "asset_id": asset_id,
                "event_time_utc": _stamp(at),
                "event_type": event_type,
                "scope": scope,
                "severity": severity,
                "state_transition": {"from": "nominal", "to": event_type},
                "details": details or {},
                "is_synthetic": True,
                "operational_use": "prohibited",
            }
        )
        return event_id

    def telemetry_value(tag: dict[str, Any], state: dict[str, Any], tick_index: int, progress: float, corridor: dict[str, Any]) -> float:
        measurement = tag["measurement"]
        asset = assets_by_id[tag["asset_id"]]
        asset_factor = (asset["asset_index"] % 11) * 0.004
        if measurement == "throughput_actual":
            return _round(state["throughput_actual"] * (0.97 + asset_factor))
        if measurement == "throughput_target":
            return _round(state["throughput_target"] * (0.985 + asset_factor))
        if measurement == "inventory_equivalent_volume":
            return _round(state["inventory"] * (0.998 + asset_factor / 4.0))
        if measurement == "inventory_balance_rate":
            return _round((state["throughput_target"] - state["throughput_actual"]) * 0.6)
        if measurement == "temperature_index":
            return _round(_clamp(state["temperature_index"] + asset_factor))
        if measurement == "pressure_index":
            return _round(_clamp(state["pressure_index"] + asset_factor))
        if measurement == "utility_load_index":
            return _round(_clamp(state["utility_load_index"] + asset_factor))
        if measurement == "condition_index":
            return _round(_clamp(state["condition_index"] - asset_factor / 2.0))
        if measurement == "asset_load_index":
            return _round(_clamp(state["throughput_actual"] / max(1.0, state["throughput_target"]) + asset_factor))
        if measurement == "power_index":
            return _round(_clamp(state["utility_load_index"] * 0.92 + asset_factor))
        if measurement == "yield_share":
            return _round(state["yields"][tag["product_family"]])
        if measurement == "clock_offset_measurement":
            return _round(4.0 + (tick_index % 5) * 0.25 + asset_factor)
        if measurement == "queue_depth_measurement":
            return float((tick_index + asset["asset_index"]) % 4)
        if measurement == "hourly_throughput_total":
            return _round(state["throughput_actual"] * 60.0)
        if measurement == "hourly_inventory_delta":
            return _round(abs(state["throughput_target"] - state["throughput_actual"]) * 12.0)
        if measurement == "hourly_condition_score":
            return _round(_clamp(state["condition_index"]))
        if measurement == "hourly_heartbeat":
            return 1.0
        if measurement == "corridor_line_pressure":
            node_position = max(0, min(len(corridor["pressures"]) - 1, receipt_assets.index(tag["asset_id"])))
            return _round(corridor["pressures"][node_position])
        if measurement == "corridor_inlet_flow":
            return _round(corridor["inlet_flow"])
        if measurement == "corridor_outlet_flow":
            return _round(corridor["outlet_flow"])
        if measurement == "corridor_imbalance":
            return _round(corridor["inlet_flow"] - corridor["outlet_flow"])
        return _round(progress)

    base_event_id = emit_event(
        event_type=scenario,
        scope="enterprise",
        at=onset if scenario != "normal_portfolio" else start,
        severity="warning" if scenario != "normal_portfolio" else "information",
        details={"policy": "synthetic_analytics_only"},
    )
    emit_device_log(
        code="PORTFOLIO_GENERATION_STARTED",
        message="Synthetic compact portfolio generation started.",
        device_id=None,
        asset_id=None,
        module_id=None,
        at=start,
    )
    for device_id in heartbeat_device_ids[:3]:
        device = devices_by_id[device_id]
        emit_device_log(
            code="HEARTBEAT_POLICY_ACTIVE",
            message="Heartbeat evidence active for this fictional communications device.",
            device_id=device_id,
            asset_id=device["asset_id"],
            module_id=device["module_id"],
            at=start,
        )
    emit_device_log(
        code="DEADBAND_POLICY_DECLARED",
        message="Deadband behavior is represented in logs and summaries without suppressing scheduled records.",
        device_id=None,
        asset_id=None,
        module_id="enterprise_blending_dispatch",
        at=start,
    )

    for tick_index in range(total_ticks):
        at = start + timedelta(seconds=tick_index * 5)
        progress = 0.0 if tick_index < onset_tick else (tick_index - onset_tick) / max(1, total_ticks - onset_tick - 1)
        for module in MODULE_DEFINITIONS:
            module_id = module["module_id"]
            state = module_states[module_id]
            offsets = _module_signal_offsets(module_id)
            rhythm = math.sin((tick_index + list(MODULE_BY_ID).index(module_id) * 7) / 9.0) * 0.025
            target = module["base_throughput_index"] * 100.0 * (1.0 + rhythm)

            if scenario == "module_degradation" and module_id == "deep_conversion" and tick_index >= onset_tick:
                target *= 1.0 - 0.22 * max(0.25, progress)
                state["condition_index"] = _clamp(0.87 - 0.28 * progress)
            elif scenario == "portfolio_power_event" and module["site_id"] == "fictional-compact-site-b" and tick_index >= onset_tick:
                recovery = 0.14 + 0.26 * progress
                target *= recovery
                state["condition_index"] = _clamp(0.71 - 0.05 * (1.0 - progress))
            else:
                state["condition_index"] = _clamp(0.86 - offsets["condition"] + rhythm / 4.0)

            if scenario == "blend_shift" and module_id in {"enterprise_blending_dispatch", "renewable_fuels"} and tick_index >= onset_tick:
                target *= 1.0 + 0.06 * progress
            if scenario == "offsites_withdrawal" and module_id == "receipt_offsites" and tick_index >= onset_tick:
                target *= 1.0 - 0.08 * progress

            state["throughput_target"] = target
            lag = 0.22 if module["fidelity"] == "physics_lite_corridor" else 0.18
            state["throughput_actual"] += (state["throughput_target"] - state["throughput_actual"]) * lag
            if scenario == "portfolio_power_event" and module["site_id"] == "fictional-compact-site-b" and tick_index >= onset_tick and state["throughput_actual"] < 2.0:
                state["throughput_actual"] = 0.0
            inflow = state["throughput_target"] * 0.985
            outflow = state["throughput_actual"] * 0.982
            inventory_delta = (inflow - outflow) * (5.0 / 3600.0)
            state["inventory"] = max(0.0, state["inventory"] + inventory_delta)
            state["temperature_index"] = _clamp(0.41 + state["throughput_actual"] / 240.0 + offsets["temperature"] + rhythm)
            state["pressure_index"] = _clamp(0.46 + state["throughput_actual"] / 260.0 + offsets["pressure"] - max(0.0, inventory_delta) * 0.004)
            state["utility_load_index"] = _clamp(0.25 + state["throughput_actual"] / 200.0 + offsets["utility"])
            state["yields"] = _normalized_yields(module, scenario, progress if tick_index >= onset_tick else 0.0)

            product_accumulators[module_id]["throughput_sum"] += state["throughput_actual"]
            product_accumulators[module_id]["tick_count"] += 1
            product_accumulators[module_id]["inventory_close"] = state["inventory"]
            for family, share in state["yields"].items():
                product_accumulators[module_id]["yield_sum"][family] += share
                product_accumulators[module_id]["equivalent_volume"][family] += state["throughput_actual"] * share * (5.0 / 3600.0)

        receipt_state = module_states["receipt_offsites"]
        withdrawal = 0.0
        if scenario == "offsites_withdrawal" and tick_index >= onset_tick:
            withdrawal = 4.5 + 2.0 * progress
        corridor_pressures, actual_segment_number = _corridor_profile(
            actual=receipt_state["throughput_actual"] / 100.0,
            withdrawal=withdrawal / 100.0,
            fraction=config["localization"]["hidden_withdrawal_route_fraction"],
        )
        corridor = {
            "pressures": corridor_pressures,
            "inlet_flow": _round(receipt_state["throughput_actual"]),
            "outlet_flow": _round(max(0.0, receipt_state["throughput_actual"] - withdrawal)),
            "actual_segment_number": actual_segment_number,
            "withdrawal": _round(withdrawal),
        }

        for tag in tags:
            cadence_seconds = tag["cadence_seconds"]
            if cadence_seconds is None:
                continue
            if (tick_index * 5) % cadence_seconds != 0:
                continue
            module_id = tag["module_id"]
            state = module_states[module_id]
            value = telemetry_value(tag, state, tick_index, progress, corridor)
            event_time = at
            source_time = at
            ingest_time = at
            delivery_class = "heartbeat" if tag["measurement"] == "hourly_heartbeat" else "scheduled"
            quality = copy.deepcopy(quality_good)
            clock_state = "synchronized"

            if scenario == "communications_degradation" and tick_index >= onset_tick and tag["module_id"] == "receipt_offsites":
                slot = (tick_index + len(tag["tag_id"])) % 8
                if slot == 0:
                    delivery_class = "late"
                    ingest_time = at + timedelta(seconds=45)
                    clock_state = "holdover"
                    quality = {"validity": "late", "source": "synthetic_delivery_overlay", "reasons": ["delivery_delayed"]}
                elif slot == 1 and tag["tag_id"] in store_forward_targets:
                    delivery_class = "stale"
                    clock_state = "holdover"
                    previous = latest_source_by_tag.get(tag["tag_id"])
                    if previous is not None:
                        value, source_time = previous
                    quality = {"validity": "stale", "source": "synthetic_delivery_overlay", "reasons": ["store_and_forward_pending"]}
                elif slot == 2:
                    delivery_class = "missing"
                    clock_state = "holdover"
                    value = None
                    source_time = None
                    quality = {"validity": "missing", "source": "synthetic_delivery_overlay", "reasons": ["communications_loss"]}
                elif slot == 3 and tag["tag_id"] in measurement_bias_targets:
                    value = _round(float(value) + 7.5)
                    quality = {"validity": "questionable", "source": "synthetic_measurement_overlay", "reasons": ["measurement_bias"]}
            if (
                scenario == "communications_degradation"
                and tick_index >= onset_tick
                and tag["tag_id"] in measurement_bias_targets
                and value is not None
                and tick_index % 6 == 0
            ):
                value = _round(float(value) + 7.5)
                quality = {"validity": "questionable", "source": "synthetic_measurement_overlay", "reasons": ["measurement_bias"]}

            if scenario == "portfolio_power_event" and tick_index >= onset_tick and tag["module_id"] in {"deep_conversion", "distillate_conversion", "chemical_integration"} and tag["measurement"] in {"throughput_actual", "power_index"}:
                if module_states[tag["module_id"]]["throughput_actual"] == 0.0:
                    value = 0.0
                    quality = {"validity": "good", "source": "synthetic_model", "reasons": ["deenergized"]}

            telemetry_id = _hash_id(
                "telemetry",
                run_id,
                tag["tag_id"],
                event_time.isoformat(),
                source_time.isoformat() if source_time else "none",
                ingest_time.isoformat(),
                delivery_class,
                len(evidence_telemetry_ids) if tag["measurement"] == "corridor_imbalance" else scheduled_count,
            )
            record = {
                "schema_version": "1.0",
                "telemetry_id": telemetry_id,
                "run_id": run_id,
                "scenario_id": scenario_id,
                "enterprise_id": enterprise_id,
                "site_id": tag["site_id"],
                "module_id": tag["module_id"],
                "asset_id": tag["asset_id"],
                "device_id": tag["device_id"],
                "tag_id": tag["tag_id"],
                "event_time_utc": _stamp(event_time),
                "source_time_utc": _stamp(source_time) if source_time else None,
                "ingest_time_utc": _stamp(ingest_time),
                "measurement": tag["measurement"],
                "value": None if value is None else (_round(value) if isinstance(value, (int, float)) else value),
                "unit": tag["unit"],
                "cadence_class": tag["cadence_class"],
                "cadence_seconds": tag["cadence_seconds"],
                "delivery_class": delivery_class,
                "quality": quality,
                "clock_state": clock_state,
                "clock_offset_ms": 37.0 if clock_state == "holdover" else 4.0,
                "clock_uncertainty_ms": 9.0 if clock_state == "holdover" else 1.5,
                "product_family": tag["product_family"],
                "fidelity": MODULE_BY_ID[tag["module_id"]]["fidelity"],
                "is_synthetic": True,
                "operational_use": "prohibited",
            }
            telemetry_writer.write(record)
            scheduled_count += 1
            if source_time is not None and value is not None:
                latest_source_by_tag[tag["tag_id"]] = (value, source_time)
            if scenario == "offsites_withdrawal" and tag["measurement"] in {"corridor_line_pressure", "corridor_inlet_flow", "corridor_outlet_flow", "corridor_imbalance"} and tick_index >= onset_tick:
                evidence_telemetry_ids.append(telemetry_id)

            if scenario == "communications_degradation" and tick_index >= onset_tick and tag["tag_id"] in duplicate_targets and delivery_class in {"scheduled", "late"} and tick_index % 3 == 0:
                duplicate = copy.deepcopy(record)
                duplicate["telemetry_id"] = _hash_id("telemetry", telemetry_id, "duplicate")
                duplicate["delivery_class"] = "duplicate"
                duplicate["quality"] = {"validity": "duplicate", "source": "synthetic_delivery_overlay", "reasons": ["duplicate_delivery"]}
                telemetry_writer.write(duplicate)
                scenario_added_count += 1

            if scenario == "communications_degradation" and tick_index >= onset_tick and tag["tag_id"] in store_forward_targets and delivery_class == "stale" and source_time is not None and tick_index % 2 == 0:
                backfill = copy.deepcopy(record)
                backfill["telemetry_id"] = _hash_id("telemetry", telemetry_id, "store-and-forward")
                backfill["delivery_class"] = "store_and_forward"
                backfill["source_time_utc"] = _stamp(source_time)
                backfill["ingest_time_utc"] = _stamp(at + timedelta(seconds=70))
                backfill["quality"] = {"validity": "good", "source": "synthetic_delivery_overlay", "reasons": ["store_and_forward_replayed"]}
                telemetry_writer.write(backfill)
                scenario_added_count += 1

    if scenario == "communications_degradation":
        emit_event(
            event_type="communications_overlay_active",
            scope="module",
            at=onset,
            site_id="fictional-compact-site-a",
            module_id="receipt_offsites",
            asset_id=detector_asset_id,
            severity="warning",
            details={"delivery_classes": ["late", "stale", "missing", "duplicate", "store_and_forward"]},
        )
        emit_device_log(
            code="CLOCK_HOLDOVER_ENTERED",
            message="Synthetic communications degradation moved corridor devices into clock holdover.",
            device_id=None,
            asset_id=detector_asset_id,
            module_id="receipt_offsites",
            at=onset,
            level="warning",
            category="communications",
            queue_depth=12,
            clock_state="holdover",
            quality={"validity": "questionable", "source": "synthetic_delivery_overlay", "reasons": ["clock_holdover"]},
        )
    elif scenario == "module_degradation":
        emit_event(
            event_type="module_degradation_applied",
            scope="module",
            at=onset,
            site_id="fictional-compact-site-b",
            module_id="deep_conversion",
            severity="warning",
            details={"affected_metric": "condition_index"},
        )
    elif scenario == "portfolio_power_event":
        emit_event(
            event_type="portfolio_power_event_active",
            scope="site",
            at=onset,
            site_id="fictional-compact-site-b",
            severity="warning",
            details={"safe_stop": True},
        )
    elif scenario == "blend_shift":
        emit_event(
            event_type="blend_shift_applied",
            scope="module",
            at=onset,
            site_id="fictional-compact-site-a",
            module_id="enterprise_blending_dispatch",
            severity="information",
            details={"target_family": "renewable_products"},
        )
    elif scenario == "offsites_withdrawal":
        emit_event(
            event_type="offsites_withdrawal_active",
            scope="module",
            at=onset,
            site_id="fictional-compact-site-a",
            module_id="receipt_offsites",
            asset_id=detector_asset_id,
            severity="warning",
            details={"detector": "bounded_corridor"},
        )

    telemetry_files = telemetry_writer.close()
    product_summaries: list[dict[str, Any]] = []
    for module in MODULE_DEFINITIONS:
        module_id = module["module_id"]
        accumulator = product_accumulators[module_id]
        tick_count = max(1, accumulator["tick_count"])
        for family in PRODUCT_FAMILIES:
            product_summaries.append(
                {
                    "schema_version": "1.0",
                    "summary_id": _hash_id("summary", run_id, module_id, family),
                    "run_id": run_id,
                    "scenario_id": scenario_id,
                    "enterprise_id": enterprise_id,
                    "site_id": module["site_id"],
                    "module_id": module_id,
                    "product_family": family,
                    "window_start_utc": config["simulation"]["start_utc"],
                    "window_end_utc": config["simulation"]["end_utc"],
                    "equivalent_volume_units": _round(accumulator["equivalent_volume"][family]),
                    "average_yield_share": _round(accumulator["yield_sum"][family] / tick_count),
                    "average_throughput_index": _round(accumulator["throughput_sum"] / tick_count / 100.0),
                    "opening_inventory_units": _round(accumulator["inventory_open"]),
                    "closing_inventory_units": _round(accumulator["inventory_close"]),
                    "fidelity": module["fidelity"],
                    "is_synthetic": True,
                    "operational_use": "prohibited",
                }
            )
    product_summaries.sort(key=lambda item: (item["module_id"], item["product_family"]))

    detector_state = "not_required"
    abstention_reason = None
    estimated_segment_id = None
    lower_fraction = None
    estimate_fraction = None
    upper_fraction = None
    candidate_segment_ids: list[str] = []
    evidence_window_start = _stamp(start)
    evidence_window_end = _stamp(end - timedelta(seconds=5)) if total_ticks else _stamp(start)
    confidence_class = "not_applicable"
    related_event_ids = [base_event_id]
    if scenario == "normal_portfolio":
        detector_state = "clear"
        confidence_class = "informational"
    elif scenario == "communications_degradation":
        detector_state = "abstained"
        abstention_reason = "insufficient_synchronized_good_evidence"
        confidence_class = "low"
    elif scenario == "offsites_withdrawal" and config["localization"]["enabled"]:
        detector_state = "estimated"
        actual_index = corridor["actual_segment_number"] - 1
        lower_index = max(0, actual_index - 1)
        upper_index = min(len(corridor_segments) - 1, actual_index + 1)
        candidate_segment_ids = corridor_segments[lower_index : upper_index + 1]
        estimated_segment_id = corridor_segments[actual_index]
        lower_fraction = _round(lower_index / len(corridor_segments))
        estimate_fraction = _round((actual_index + 0.5) / len(corridor_segments))
        upper_fraction = _round((upper_index + 1.0) / len(corridor_segments))
        confidence_class = "bounded_estimate"
        evidence_telemetry_ids = sorted(set(evidence_telemetry_ids))
    detector_outputs = [
        {
            "schema_version": "1.0",
            "detector_output_id": _hash_id("detector", run_id, scenario),
            "run_id": run_id,
            "scenario_id": scenario_id,
            "module_id": "receipt_offsites",
            "detector_id": detector_asset_id,
            "method": "bounded_fictional_corridor_balance",
            "model_version": "1.0",
            "evidence_window_start_utc": evidence_window_start,
            "evidence_window_end_utc": evidence_window_end,
            "detection_time_utc": evidence_window_end,
            "available_time_utc": _stamp(end),
            "detection_state": detector_state,
            "estimated_segment_id": estimated_segment_id,
            "candidate_segment_ids": candidate_segment_ids,
            "lower_route_distance_fraction": lower_fraction,
            "estimated_route_distance_fraction": estimate_fraction,
            "upper_route_distance_fraction": upper_fraction,
            "abstention_reason": abstention_reason,
            "confidence_class": confidence_class,
            "evidence_telemetry_ids": evidence_telemetry_ids,
            "related_event_ids": related_event_ids,
            "quality": {"validity": "good", "source": "synthetic_detector", "reasons": []},
            "fidelity": "bounded_fictional_corridor_only",
            "is_synthetic": True,
            "operational_use": "prohibited",
        }
    ]
    if detector_state == "estimated":
        emit_device_log(
            code="DETECTOR_ESTIMATED",
            message="Synthetic receipt/offsites detector published a bounded fictional corridor estimate.",
            device_id=None,
            asset_id=detector_asset_id,
            module_id="receipt_offsites",
            at=end,
            level="info",
            category="detector",
        )

    physical_confirmation_events: list[dict[str, Any]] = []
    if detector_state == "estimated":
        available_after = end + timedelta(seconds=config["localization"]["confirmation_delay_seconds"])
        physical_confirmation_events.append(
            {
                "schema_version": "1.0",
                "confirmation_id": _hash_id("confirmation", run_id, scenario),
                "run_id": run_id,
                "scenario_id": scenario_id,
                "module_id": "receipt_offsites",
                "dispatch_time_utc": _stamp(end),
                "confirmation_time_utc": _stamp(available_after),
                "available_after_utc": _stamp(available_after),
                "inspection_method": "synthetic_delayed_corridor_review",
                "result": "bounded_confirmation",
                "confirmed_segment_id": estimated_segment_id,
                "confirmed_corridor_lower_fraction": lower_fraction,
                "confirmed_corridor_upper_fraction": upper_fraction,
                "related_detector_output_ids": [detector_outputs[0]["detector_output_id"]],
                "fidelity": "synthetic_confirmation_not_field_evidence",
                "is_synthetic": True,
                "operational_use": "prohibited",
            }
        )
        emit_device_log(
            code="SYNTHETIC_CONFIRMATION_RECORDED",
            message="Synthetic delayed corridor confirmation recorded without exposing exact truth.",
            device_id=None,
            asset_id=detector_asset_id,
            module_id="receipt_offsites",
            at=available_after,
            level="info",
            category="detector",
        )

    ground_truth = [
        {
            "schema_version": "1.0",
            "ground_truth_id": _hash_id("truth", run_id, scenario),
            "run_id": run_id,
            "scenario_id": scenario_id,
            "scenario": scenario,
            "enterprise_id": enterprise_id,
            "module_id": "receipt_offsites" if scenario in {"communications_degradation", "offsites_withdrawal"} else {
                "module_degradation": "deep_conversion",
                "portfolio_power_event": "deep_conversion",
                "blend_shift": "enterprise_blending_dispatch",
                "normal_portfolio": "receipt_offsites",
            }[scenario],
            "onset_utc": _stamp(onset if scenario != "normal_portfolio" else start),
            "end_utc": config["simulation"]["end_utc"],
            "injected_cause": scenario.replace("_", " "),
            "actual_segment_id": corridor_segments[corridor["actual_segment_number"] - 1] if scenario == "offsites_withdrawal" else None,
            "actual_route_distance_fraction": _round(config["localization"]["hidden_withdrawal_route_fraction"]) if scenario == "offsites_withdrawal" else None,
            "actual_withdrawal_index": corridor["withdrawal"] if scenario == "offsites_withdrawal" else None,
            "affected_site_ids": (
                [MODULE_BY_ID["deep_conversion"]["site_id"]]
                if scenario == "module_degradation"
                else [MODULE_BY_ID["receipt_offsites"]["site_id"]]
                if scenario in {"communications_degradation", "offsites_withdrawal", "normal_portfolio"}
                else [MODULE_BY_ID["deep_conversion"]["site_id"]]
                if scenario == "portfolio_power_event"
                else [MODULE_BY_ID["enterprise_blending_dispatch"]["site_id"]]
            ),
            "correlated_event_ids": sorted(item["event_id"] for item in events),
            "fidelity": "private_truth_not_for_public_detector_or_portal",
            "is_synthetic": True,
            "operational_use": "prohibited",
        }
    ]

    events.sort(key=lambda item: (item["event_time_utc"], item["event_id"]))
    device_logs.sort(key=lambda item: (item["event_time_utc"], item["log_id"]))

    return {
        "telemetry_files": telemetry_files,
        "scheduled_telemetry_record_count": scheduled_count,
        "scenario_added_telemetry_record_count": scenario_added_count,
        "product_summaries": product_summaries,
        "events": events,
        "device_logs": device_logs,
        "detector_outputs": detector_outputs,
        "physical_confirmation_events": physical_confirmation_events,
        "ground_truth": ground_truth,
    }
