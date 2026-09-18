"""Checksum-verified snapshots for the fictional midstream dashboard."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class MidstreamDashboardError(RuntimeError):
    """Raised when a complete midstream dataset cannot be loaded."""


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


class MidstreamSnapshotService:
    """Read one finalized local dataset without trusting partial artifacts."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def get_snapshot(self) -> dict[str, Any]:
        manifest_path = self.root / "manifest.json"
        if not manifest_path.is_file():
            raise MidstreamDashboardError("midstream manifest is unavailable")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entries = {item["path"]: item for item in manifest.get("files", [])}

        def records(name: str) -> list[dict[str, Any]]:
            entry = entries.get(name)
            path = self.root / name
            if entry is None or not path.is_file():
                raise MidstreamDashboardError(f"required artifact is unavailable: {name}")
            if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                raise MidstreamDashboardError(f"artifact checksum mismatch: {name}")
            return _jsonl(path)

        def optional_records(name: str) -> list[dict[str, Any]]:
            if name not in entries:
                return []
            return records(name)

        assets = records("assets.jsonl")
        topology = records("topology.jsonl")
        telemetry = records("telemetry.jsonl")
        events = records("events.jsonl")
        logs = records("device_logs.jsonl")
        detector_outputs = optional_records("detector_outputs.jsonl")
        confirmations = optional_records("physical_confirmation_events.jsonl")
        latest_time = max(item["event_time_utc"] for item in telemetry)
        latest = [item for item in telemetry if item["event_time_utc"] == latest_time]
        values = {
            f"{item['asset_id']}|{item['measurement']}": item for item in latest
        }

        def by_measurement(name: str) -> dict[str, Any] | None:
            return next((item for item in latest if item["measurement"] == name), None)

        def derived_measurement(
            measurement: str,
            value: float | None,
            unit: str,
            evidence: list[dict[str, Any]],
        ) -> dict[str, Any] | None:
            if value is None:
                return None
            validity = (
                "good"
                if all(
                    item.get("quality", {}).get("validity") == "good"
                    for item in evidence
                )
                else "questionable"
            )
            return {
                "measurement": measurement,
                "value": round(value, 6),
                "unit": unit,
                "event_time_utc": latest_time,
                "quality": {
                    "validity": validity,
                    "reasons": ["derived_for_dashboard"],
                },
                "is_synthetic": True,
                "operational_use": "prohibited",
            }

        inlet_pressure = by_measurement("inlet_pressure")
        outlet_pressure = by_measurement("outlet_pressure")
        pressure_loss = derived_measurement(
            "pipeline_pressure_loss",
            (
                inlet_pressure["value"] - outlet_pressure["value"]
                if inlet_pressure
                and outlet_pressure
                and inlet_pressure["value"] is not None
                and outlet_pressure["value"] is not None
                else None
            ),
            "kPa",
            [item for item in (inlet_pressure, outlet_pressure) if item],
        )
        interval_seconds = manifest["normalized_config"]["simulation"][
            "interval_seconds"
        ]
        flow_records = [
            item
            for item in telemetry
            if item["measurement"] == "volumetric_flow"
            and item["value"] is not None
        ]
        transferred_volume = derived_measurement(
            "integrated_transfer_volume",
            (
                sum(item["value"] for item in flow_records[1:]) * interval_seconds
                if len(flow_records) > 1
                else 0.0
            ),
            "m3",
            flow_records,
        )

        quality_counts: dict[str, int] = {}
        clock_states: dict[str, int] = {}
        for item in latest:
            validity = item.get("quality", {}).get("validity", "unknown")
            quality_counts[validity] = quality_counts.get(validity, 0) + 1
            clock_state = item.get("clock_state", "unavailable")
            clock_states[clock_state] = clock_states.get(clock_state, 0) + 1

        event_by_asset: dict[str, dict[str, Any]] = {}
        for item in events:
            event_by_asset[item["asset_id"]] = item
        logs_by_asset: dict[str, list[dict[str, Any]]] = {}
        for item in logs:
            logs_by_asset.setdefault(item["asset_id"], []).append(item)
        severity_rank = {"info": 0, "warning": 1, "error": 2, "critical": 3}

        enriched_assets: list[dict[str, Any]] = []
        equipment_health: list[dict[str, Any]] = []
        for asset in assets:
            measurements = [
                item
                for key, item in values.items()
                if key.startswith(asset["asset_id"] + "|")
            ]
            validities = [
                item.get("quality", {}).get("validity", "unknown")
                for item in measurements
            ]
            related_logs = logs_by_asset.get(asset["asset_id"], [])
            highest_log = max(
                related_logs,
                key=lambda item: severity_rank.get(item.get("level", "info"), 0),
                default=None,
            )
            latest_event = event_by_asset.get(asset["asset_id"])
            if any(validity in {"missing", "bad"} for validity in validities):
                state = "unavailable"
            elif any(validity in {"stale", "questionable", "uncertain"} for validity in validities):
                state = "degraded"
            elif highest_log and severity_rank.get(highest_log.get("level", "info"), 0) > 0:
                state = "attention"
            elif measurements or related_logs:
                state = "observed"
            else:
                state = "metadata_only"
            health = {
                "asset_id": asset["asset_id"],
                "asset_type": asset["asset_type"],
                "state": state,
                "measurement_count": len(measurements),
                "worst_quality": (
                    "missing"
                    if "missing" in validities
                    else "bad"
                    if "bad" in validities
                    else "stale"
                    if "stale" in validities
                    else "questionable"
                    if "questionable" in validities
                    else "good"
                    if validities
                    else "unavailable"
                ),
                "latest_event_state": (
                    latest_event.get("state_transition", {}).get("to")
                    if latest_event
                    else None
                ),
                "highest_log_level": (
                    highest_log.get("level") if highest_log else None
                ),
            }
            enriched_assets.append({**asset, "measurements": measurements, "health": health})
            equipment_health.append(health)

        monitoring_nodes = [
            {
                "asset_id": asset["asset_id"],
                "route_id": asset.get("route_id"),
                "route_distance_m": asset.get("route_distance_m"),
                "pressure": values.get(f"{asset['asset_id']}|line_pressure"),
                "health": next(
                    item
                    for item in equipment_health
                    if item["asset_id"] == asset["asset_id"]
                ),
            }
            for asset in assets
            if asset["asset_type"] == "pipeline_monitoring_node"
        ]
        monitoring_nodes.sort(key=lambda item: item["route_distance_m"])

        latest_detector = detector_outputs[-1] if detector_outputs else None
        latest_confirmation = confirmations[-1] if confirmations else None
        confirmation_summary = (
            {
                "available": True,
                "result": latest_confirmation.get("result"),
                "dispatch_time_utc": latest_confirmation.get("dispatch_time_utc"),
                "confirmation_time_utc": latest_confirmation.get(
                    "confirmation_time_utc"
                ),
                "available_after_utc": latest_confirmation.get("available_after_utc"),
                "fidelity": latest_confirmation.get("fidelity"),
                "is_synthetic": True,
                "operational_use": "prohibited",
            }
            if latest_confirmation
            else {
                "available": False,
                "result": "not_required"
                if not latest_detector
                or latest_detector.get("detection_state") == "clear"
                else "pending",
                "dispatch_time_utc": None,
                "confirmation_time_utc": None,
                "available_after_utc": None,
                "fidelity": "synthetic_confirmation_not_field_evidence",
                "is_synthetic": True,
                "operational_use": "prohibited",
            }
        )
        max_latency = max(
            (float(item.get("communications_latency_ms", 0.0)) for item in logs),
            default=0.0,
        )
        max_queue = max(
            (int(item.get("queue_depth", 0)) for item in logs),
            default=0,
        )
        max_clock_offset = max(
            (abs(float(item.get("clock_offset_ms", 0.0))) for item in latest),
            default=0.0,
        )
        max_clock_uncertainty = max(
            (float(item.get("clock_uncertainty_ms", 0.0)) for item in latest),
            default=0.0,
        )
        receipt_flow = by_measurement("receipt_flow")
        transfer_state = (
            "active"
            if receipt_flow
            and receipt_flow["value"] is not None
            and receipt_flow["value"] > 0.0
            else "inactive"
        )

        return {
            "source": {
                "run_id": manifest["run_id"],
                "scenario_id": manifest["scenario_id"],
                "event_time_utc": latest_time,
                "site": manifest["normalized_config"]["site"]["display_name"],
                "scenario": manifest["normalized_config"]["scenario"],
                "generator": manifest["generator"],
                "schema_version": manifest["schema_version"],
                "config_digest_sha256": manifest["config_digest_sha256"],
            },
            "policy": {
                "is_synthetic": manifest["is_synthetic"],
                "operational_use": manifest["operational_use"],
                "custody_transfer_use": manifest["custody_transfer_use"],
                "leak_model": manifest["leak_model"],
                "localization_model": manifest.get("localization_model"),
                "exact_location_claim": manifest.get(
                    "localization_exact_location_claim",
                    "prohibited",
                ),
                "detector_truth_access": manifest.get(
                    "detector_truth_access",
                    "prohibited",
                ),
                "field_calibration": manifest.get("field_calibration", "none"),
                "physical_confirmation": manifest.get(
                    "physical_confirmation",
                    "synthetic_only",
                ),
            },
            "kpis": {
                "receipt_flow": receipt_flow,
                "receipt_pressure": by_measurement("receipt_pressure"),
                "tank_level": by_measurement("liquid_level"),
                "pump_power": by_measurement("active_power"),
                "imbalance": by_measurement("calculated_imbalance"),
                "pressure_loss": pressure_loss,
                "transferred_volume": transferred_volume,
            },
            "overview": {
                "transfer_state": transfer_state,
                "equipment_count": len(assets),
                "monitoring_node_count": len(monitoring_nodes),
                "attention_event_count": sum(
                    1
                    for item in events
                    if item.get("severity") in {"warning", "error", "critical"}
                ),
                "evidence_state": (
                    latest_detector.get("detection_state")
                    if latest_detector
                    else "unavailable"
                ),
            },
            "quality": {
                "latest_record_count": len(latest),
                "validity_counts": quality_counts,
                "clock_state_counts": clock_states,
                "good_fraction": (
                    quality_counts.get("good", 0) / len(latest)
                    if latest
                    else 0.0
                ),
            },
            "communications": {
                "max_latency_ms": max_latency,
                "max_queue_depth": max_queue,
                "max_abs_clock_offset_ms": max_clock_offset,
                "max_clock_uncertainty_ms": max_clock_uncertainty,
                "degraded_log_count": sum(
                    1
                    for item in logs
                    if item.get("quality", {}).get("validity") != "good"
                    or item.get("level") in {"warning", "error", "critical"}
                ),
            },
            "localization": {
                "detector": latest_detector,
                "confirmation": confirmation_summary,
                "ground_truth_exposed": False,
            },
            "route": {
                "route_id": (
                    monitoring_nodes[0]["route_id"] if monitoring_nodes else None
                ),
                "length_m": manifest["normalized_config"]["pipeline"]["length_m"],
                "monitoring_nodes": monitoring_nodes,
            },
            "assets": enriched_assets,
            "equipment_health": equipment_health,
            "topology": topology,
            "events": events[-24:],
            "device_logs": logs[-24:],
            "provenance": {
                "artifact_count": len(manifest.get("files", [])),
                "record_counts": manifest.get("record_counts", {}),
                "files": [
                    {
                        "path": item["path"],
                        "record_type": item["record_type"],
                        "format": item["format"],
                        "record_count": item["record_count"],
                    }
                    for item in manifest.get("files", [])
                ],
                "ground_truth_excluded_from_api": True,
            },
        }
