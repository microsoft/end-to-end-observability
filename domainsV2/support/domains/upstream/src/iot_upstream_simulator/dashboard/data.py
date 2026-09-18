"""Checksum-verified snapshots for the fictional upstream production dashboard.

Only finalized local artifacts are read. The private ``ground_truth`` family is
never opened, never verified, and never reachable through this service.

The committed sample run is deliberately pruned: the bulky ``telemetry``,
``signals``, ``topology`` and ``device_logs`` artifacts are absent even though
their manifest entries remain. Every record family except the site identity is
therefore optional here, so the portal still renders a truthful, clearly
degraded snapshot instead of failing closed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from ..catalog import (
    AREA_DEFINITIONS,
    CATALOG_RECORD_TYPES,
    LOCAL_ONLY_RECORD_TYPES,
    RECORD_TYPES,
    TELEMETRY_RECORD_TYPES,
    WORKFLOW_RECORD_TYPES,
)

MAX_MANIFEST_BYTES = 4 * 1024 * 1024
#: Never opened by this service under any circumstance. ``ground_truth`` is
#: private, and the parts, storeroom and dispatch families are local-only:
#: publication is gated behind design phases 9 and 10, so the public snapshot
#: must not read them or even disclose whether they exist.
FORBIDDEN_RECORD_TYPES = frozenset({"ground_truth", *LOCAL_ONLY_RECORD_TYPES})
#: Families the snapshot may load. ``ground_truth`` is structurally excluded.
READABLE_RECORD_TYPES = tuple(
    record_type
    for record_type in RECORD_TYPES
    if record_type not in FORBIDDEN_RECORD_TYPES
)
#: Families that must be present for the snapshot to describe a real site.
REQUIRED_RECORD_TYPES = ("sites",)

QUALITY_RANK = {"good": 0, "uncertain": 1, "bad": 2}
SEVERITY_RANK = {"info": 0, "attention": 1}
HEALTH_BY_QUALITY = {"good": "nominal", "uncertain": "degraded", "bad": "impaired"}
HEALTH_RANK = {"unknown": 0, "nominal": 1, "degraded": 2, "impaired": 3}
DEGRADED_DELIVERY_STATES = frozenset(
    {"late", "stale", "missing", "duplicate", "out_of_order", "store_and_forward"}
)
RECENT_RECORD_LIMIT = 24


class UpstreamDashboardError(RuntimeError):
    """Raised when a usable fictional upstream dataset cannot be loaded."""


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _safe_relative(root: Path, relative: str) -> Path:
    candidate = PurePosixPath(relative)
    if (
        not relative
        or candidate.is_absolute()
        or ".." in candidate.parts
        or "\\" in relative
    ):
        raise UpstreamDashboardError(f"artifact path is unsafe: {relative}")
    path = (root / Path(*candidate.parts)).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as error:
        raise UpstreamDashboardError(
            f"artifact path is unsafe: {relative}"
        ) from error
    return path


def _latest(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    ordered = [item for item in records if isinstance(item.get("event_time_utc"), str)]
    if not ordered:
        return records[-1] if records else None
    return max(ordered, key=lambda item: item["event_time_utc"])


def _tail(records: list[dict[str, Any]], limit: int = RECENT_RECORD_LIMIT) -> list[dict[str, Any]]:
    ordered = sorted(
        records,
        key=lambda item: (item.get("event_time_utc") or "", item.get("record_id") or ""),
    )
    return ordered[-limit:]


class UpstreamSnapshotService:
    """Read one finalized local dataset without trusting partial artifacts."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    # ------------------------------------------------------------------
    # Artifact loading
    # ------------------------------------------------------------------

    def _manifest(self) -> dict[str, Any]:
        manifest_path = self.root / "manifest.json"
        if not manifest_path.is_file():
            raise UpstreamDashboardError("upstream manifest is unavailable")
        try:
            if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
                raise UpstreamDashboardError("upstream manifest is too large")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except UpstreamDashboardError:
            raise
        except (OSError, json.JSONDecodeError) as error:
            raise UpstreamDashboardError(
                "upstream manifest is unavailable"
            ) from error
        if not isinstance(manifest, dict) or not isinstance(
            manifest.get("run_id"), str
        ):
            raise UpstreamDashboardError("upstream manifest is invalid")
        return manifest

    def _load(
        self, manifest: dict[str, Any]
    ) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
        entries = {
            item.get("record_type"): item
            for item in manifest.get("files", [])
            if isinstance(item, dict) and item.get("format", "jsonl") == "jsonl"
        }
        families: dict[str, list[dict[str, Any]]] = {}
        missing: list[str] = []
        for record_type in READABLE_RECORD_TYPES:
            entry = entries.get(record_type)
            if entry is None:
                missing.append(record_type)
                continue
            path = _safe_relative(self.root, str(entry.get("path", "")))
            if not path.is_file():
                # The committed sample prunes bulky families on purpose.
                missing.append(record_type)
                continue
            try:
                payload = path.read_bytes()
            except OSError as error:
                raise UpstreamDashboardError(
                    f"artifact could not be read: {record_type}"
                ) from error
            if hashlib.sha256(payload).hexdigest() != entry.get("sha256"):
                raise UpstreamDashboardError(
                    f"artifact checksum mismatch: {record_type}"
                )
            try:
                families[record_type] = _jsonl(path)
            except json.JSONDecodeError as error:
                raise UpstreamDashboardError(
                    f"artifact is not valid JSONL: {record_type}"
                ) from error
        for record_type in REQUIRED_RECORD_TYPES:
            if not families.get(record_type):
                raise UpstreamDashboardError(
                    f"required artifact is unavailable: {record_type}"
                )
        return families, missing

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def get_snapshot(self) -> dict[str, Any]:
        manifest = self._manifest()
        families, missing = self._load(manifest)

        def family(name: str) -> list[dict[str, Any]]:
            return families.get(name, [])

        site = family("sites")[0]
        areas = family("areas")
        wells = family("wells")
        assets = family("assets")
        devices = family("devices")
        telemetry = family("telemetry")
        events = family("events")
        alarms = family("alarm_journal")
        condition = family("condition_monitoring")
        delivery = family("delivery_logs")
        device_logs = family("device_logs")
        well_tests = family("well_tests")
        summaries = family("production_summaries")
        handoffs = family("handoff_context")
        environmental = family("environmental_observations")
        analyses = family("analyses")
        samples = family("samples")
        acquisitions = family("sample_acquisitions")
        operator_actions = family("operator_actions")
        audit_logs = family("audit_logs")
        maintenance = family("maintenance_context")
        inspection = family("inspection_context")

        runtime = [
            record
            for record_type in (*TELEMETRY_RECORD_TYPES, *WORKFLOW_RECORD_TYPES)
            for record in family(record_type)
        ]
        latest_time = max(
            (
                record["event_time_utc"]
                for record in runtime
                if isinstance(record.get("event_time_utc"), str)
            ),
            default=None,
        )

        quality_counts: dict[str, int] = {}
        freshness_counts: dict[str, int] = {}
        delivery_counts: dict[str, int] = {}
        origin_counts: dict[str, int] = {}
        for record in runtime:
            for bucket, key in (
                (quality_counts, "quality_class"),
                (freshness_counts, "freshness_state"),
                (delivery_counts, "delivery_state"),
                (origin_counts, "origin_state"),
            ):
                value = record.get(key, "unknown")
                bucket[value] = bucket.get(value, 0) + 1

        telemetry_by_asset: dict[str, list[dict[str, Any]]] = {}
        for record in telemetry:
            telemetry_by_asset.setdefault(record["asset_id"], []).append(record)
        latest_measurements: dict[str, list[dict[str, Any]]] = {}
        for asset_id, records in telemetry_by_asset.items():
            newest: dict[str, dict[str, Any]] = {}
            for record in records:
                measurement = record.get("measurement")
                current = newest.get(measurement)
                if current is None or record.get("event_time_utc", "") >= current.get(
                    "event_time_utc", ""
                ):
                    newest[measurement] = record
            latest_measurements[asset_id] = [
                {
                    "measurement": item.get("measurement"),
                    "unit": item.get("unit"),
                    "value": item.get("value"),
                    "event_time_utc": item.get("event_time_utc"),
                    "source_time_utc": item.get("source_time_utc"),
                    "quality_class": item.get("quality_class"),
                    "quality_reason": item.get("quality_reason"),
                    "freshness_state": item.get("freshness_state"),
                    "origin_state": item.get("origin_state"),
                    "delivery_state": item.get("delivery_state"),
                    "fidelity": item.get("fidelity"),
                }
                for item in sorted(newest.values(), key=lambda entry: entry["measurement"])
            ]

        condition_by_asset: dict[str, dict[str, Any]] = {}
        for record in sorted(condition, key=lambda item: item.get("event_time_utc", "")):
            condition_by_asset[record.get("asset_id")] = record
        alarms_by_asset: dict[str, list[dict[str, Any]]] = {}
        for record in alarms:
            alarms_by_asset.setdefault(record.get("asset_id"), []).append(record)
        events_by_asset: dict[str, list[dict[str, Any]]] = {}
        for record in events:
            events_by_asset.setdefault(record.get("asset_id"), []).append(record)

        equipment_health: list[dict[str, Any]] = []
        enriched_assets: list[dict[str, Any]] = []
        for asset in sorted(assets, key=lambda item: item.get("asset_index", 0)):
            asset_id = asset["asset_id"]
            measurements = latest_measurements.get(asset_id, [])
            qualities = [item.get("quality_class", "unknown") for item in measurements]
            deliveries = [item.get("delivery_state", "unknown") for item in measurements]
            asset_events = events_by_asset.get(asset_id, [])
            asset_alarms = alarms_by_asset.get(asset_id, [])
            condition_record = condition_by_asset.get(asset_id)
            worst_quality = (
                max(qualities, key=lambda value: QUALITY_RANK.get(value, 1))
                if qualities
                else "unavailable"
            )
            highest_severity = max(
                (record.get("severity", "info") for record in asset_events),
                key=lambda value: SEVERITY_RANK.get(value, 0),
                default=None,
            )
            if qualities:
                state = HEALTH_BY_QUALITY.get(worst_quality, "degraded")
            elif condition_record or asset_events or asset_alarms:
                state = "observed"
            else:
                state = "metadata_only"
            if state in {"nominal", "observed"} and any(
                value in DEGRADED_DELIVERY_STATES for value in deliveries
            ):
                state = "degraded"
            if highest_severity == "attention" and state in {
                "nominal",
                "observed",
                "metadata_only",
            }:
                state = "attention"
            if any(record.get("alarm_state") == "raised" for record in asset_alarms):
                state = "attention"
            health = {
                "asset_id": asset_id,
                "asset_type": asset.get("asset_type"),
                "area_id": asset.get("area_id"),
                "well_id": asset.get("well_id"),
                "state": state,
                "measurement_count": len(measurements),
                "worst_quality": worst_quality,
                "health_index": (
                    condition_record.get("health_index") if condition_record else None
                ),
                "diagnosis_claim": (
                    condition_record.get("diagnosis_claim") if condition_record else None
                ),
                "highest_event_severity": highest_severity,
                "open_alarm_count": sum(
                    1 for record in asset_alarms if record.get("alarm_state") == "raised"
                ),
            }
            equipment_health.append(health)
            enriched_assets.append(
                {
                    "asset_id": asset_id,
                    "asset_type": asset.get("asset_type"),
                    "generic_model": asset.get("generic_model"),
                    "display_name": asset.get("display_name"),
                    "archetype": asset.get("archetype"),
                    "area_id": asset.get("area_id"),
                    "area_name": asset.get("area_name"),
                    "well_id": asset.get("well_id"),
                    "pad_id": asset.get("pad_id"),
                    "fidelity": asset.get("fidelity"),
                    "measurements": measurements,
                    "health": health,
                }
            )

        health_by_asset = {item["asset_id"]: item for item in equipment_health}
        well_views = []
        for well in sorted(wells, key=lambda item: item.get("display_name", "")):
            well_id = well["well_id"]
            well_test = _latest(
                [item for item in well_tests if item.get("well_id") == well_id]
            )
            well_states = [
                health_by_asset[asset["asset_id"]]["state"]
                for asset in assets
                if asset.get("well_id") == well_id
                and asset["asset_id"] in health_by_asset
            ]
            well_views.append(
                {
                    "well_id": well_id,
                    "display_name": well.get("display_name"),
                    "pad_id": well.get("pad_id"),
                    "pad_display_name": well.get("pad_display_name"),
                    "archetype": well.get("archetype"),
                    "lift_type": well.get("lift_type"),
                    "lifecycle_state": well.get("lifecycle_state"),
                    "asset_count": len(well_states),
                    "state": (
                        max(
                            well_states,
                            key=lambda value: HEALTH_RANK.get(value, 0),
                        )
                        if well_states
                        else "metadata_only"
                    ),
                    "latest_well_test": (
                        {
                            "well_test_id": well_test.get("well_test_id"),
                            "event_time_utc": well_test.get("event_time_utc"),
                            "test_method_class": well_test.get("test_method_class"),
                            "oil_rate_m3d": well_test.get("oil_rate_m3d"),
                            "gas_rate_ksm3d": well_test.get("gas_rate_ksm3d"),
                            "water_rate_m3d": well_test.get("water_rate_m3d"),
                            "flowing_pressure_kpa": well_test.get("flowing_pressure_kpa"),
                            "rate_uncertainty_index": well_test.get(
                                "rate_uncertainty_index"
                            ),
                            "interpretation": well_test.get("interpretation"),
                            "allocation_claim": well_test.get("allocation_claim"),
                            "quality_class": well_test.get("quality_class"),
                            "validation_state": well_test.get("validation_state"),
                        }
                        if well_test
                        else None
                    ),
                }
            )

        latest_summary = _latest(
            [item for item in summaries if item.get("summary_scope") == "facility"]
        ) or _latest(summaries)
        latest_handoff = _latest(handoffs)
        latest_environmental = _latest(environmental)
        worst_condition = min(
            condition,
            key=lambda item: item.get("health_index", 1.0),
            default=None,
        )

        area_names = {
            area["area_id"]: area["display_name"] for area in AREA_DEFINITIONS
        }
        area_views = [
            {
                "area_id": area.get("area_id"),
                "area_name": area.get("area_name") or area_names.get(area.get("area_id")),
                "area_ordinal": area.get("area_ordinal"),
                "asset_count": sum(
                    1 for asset in assets if asset.get("area_id") == area.get("area_id")
                ),
                "attention_count": sum(
                    1
                    for record in events
                    if record.get("area_id") == area.get("area_id")
                    and record.get("severity") == "attention"
                ),
            }
            for area in sorted(areas, key=lambda item: item.get("area_ordinal", 0))
        ]

        return {
            "source": {
                "run_id": manifest["run_id"],
                "scenario_id": manifest.get("scenario_id"),
                "scenario": manifest.get("normalized_config", {}).get("scenario"),
                "site_id": site.get("site_id"),
                "site": site.get("display_name"),
                "facility_id": site.get("facility_id"),
                "facility": site.get("facility_display_name"),
                "time_zone": site.get("time_zone"),
                "event_time_utc": latest_time,
                "onset_utc": manifest.get("profile", {}).get("onset_utc"),
                "interval_seconds": manifest.get("profile", {}).get("interval_seconds"),
                "tick_count": manifest.get("profile", {}).get("tick_count"),
                "generator": manifest.get("generator", {}),
                "schema_version": manifest.get("schema_version"),
                "config_digest_sha256": manifest.get("config_digest_sha256"),
            },
            "policy": {
                "is_synthetic": True,
                "operational_use": "prohibited",
                "private_truth_publication": "excluded",
                "ground_truth_exposed": False,
                "exact_location_claim": "prohibited",
                "custody_transfer_use": "prohibited",
                "allocation_claim": "not_fiscal_allocation",
                "release_quantification_claim": "not_quantified",
                "public_telemetry_layer": manifest.get("policy", {}).get(
                    "public_telemetry_layer", "delivered_observation_only"
                ),
                "standards_relationship": manifest.get("policy", {}).get(
                    "standards_relationship",
                    "inspired_by_not_conformant_with_witsml_prodml_resqml_opcua_isa95",
                ),
                "field_calibration": "none",
            },
            "coverage": {
                "available_families": sorted(families),
                "missing_families": sorted(missing),
                "catalog_families": list(CATALOG_RECORD_TYPES),
                "workflow_families": list(WORKFLOW_RECORD_TYPES),
                "degraded": bool(missing),
                "note": (
                    "Absent families are pruned artifacts, not evidence of loss."
                ),
            },
            "kpis": {
                "production_summary": (
                    {
                        "summary_id": latest_summary.get("summary_id"),
                        "summary_scope": latest_summary.get("summary_scope"),
                        "period_start_utc": latest_summary.get("period_start_utc"),
                        "period_end_utc": latest_summary.get("period_end_utc"),
                        "oil_volume_m3": latest_summary.get("oil_volume_m3"),
                        "gas_volume_ksm3": latest_summary.get("gas_volume_ksm3"),
                        "water_volume_m3": latest_summary.get("water_volume_m3"),
                        "uptime_index": latest_summary.get("uptime_index"),
                        "allocation_claim": latest_summary.get("allocation_claim"),
                        "quality_class": latest_summary.get("quality_class"),
                    }
                    if latest_summary
                    else None
                ),
                "handoff": (
                    {
                        "handoff_id": latest_handoff.get("handoff_id"),
                        "transfer_state": latest_handoff.get("transfer_state"),
                        "reconciliation_state": latest_handoff.get("reconciliation_state"),
                        "operational_rate_index": latest_handoff.get(
                            "operational_rate_index"
                        ),
                        "operational_total_index": latest_handoff.get(
                            "operational_total_index"
                        ),
                        "inventory_change_index": latest_handoff.get(
                            "inventory_change_index"
                        ),
                        "uncertainty_index": latest_handoff.get("uncertainty_index"),
                        "custody_claim": latest_handoff.get("custody_claim"),
                    }
                    if latest_handoff
                    else None
                ),
                "condition": (
                    {
                        "condition_id": worst_condition.get("condition_id"),
                        "asset_id": worst_condition.get("asset_id"),
                        "health_index": worst_condition.get("health_index"),
                        "vibration_index": worst_condition.get("vibration_index"),
                        "thermal_index": worst_condition.get("thermal_index"),
                        "restriction_index": worst_condition.get("restriction_index"),
                        "diagnosis_claim": worst_condition.get("diagnosis_claim"),
                        "remaining_life_claim": worst_condition.get(
                            "remaining_life_claim"
                        ),
                        "interpretation": worst_condition.get("interpretation"),
                    }
                    if worst_condition
                    else None
                ),
                "environment": (
                    {
                        "observation_id": latest_environmental.get("observation_id"),
                        "observation_class": latest_environmental.get(
                            "observation_class"
                        ),
                        "qualitative_level": latest_environmental.get(
                            "qualitative_level"
                        ),
                        "detector_availability_index": latest_environmental.get(
                            "detector_availability_index"
                        ),
                        "wind_speed_ms": latest_environmental.get("wind_speed_ms"),
                        "ambient_temperature_c": latest_environmental.get(
                            "ambient_temperature_c"
                        ),
                        "quantified_release_claim": latest_environmental.get(
                            "quantified_release_claim"
                        ),
                        "source_attribution": latest_environmental.get(
                            "source_attribution"
                        ),
                        "compliance_claim": latest_environmental.get("compliance_claim"),
                    }
                    if latest_environmental
                    else None
                ),
            },
            "overview": {
                "well_count": len(wells),
                "pad_count": site.get("pad_count"),
                "area_count": len(areas) or len(AREA_DEFINITIONS),
                "asset_count": len(assets),
                "device_count": len(devices),
                "attention_event_count": sum(
                    1 for record in events if record.get("severity") == "attention"
                ),
                "raised_alarm_count": sum(
                    1 for record in alarms if record.get("alarm_state") == "raised"
                ),
                "operator_action_count": len(operator_actions),
                "laboratory_result_count": len(analyses),
                "sample_count": len(samples),
                "sample_acquisition_count": len(acquisitions),
                "maintenance_context_count": len(maintenance),
                "inspection_context_count": len(inspection),
                "audit_log_count": len(audit_logs),
            },
            "quality": {
                "runtime_record_count": len(runtime),
                "quality_class_counts": quality_counts,
                "freshness_state_counts": freshness_counts,
                "delivery_state_counts": delivery_counts,
                "origin_state_counts": origin_counts,
                "good_fraction": (
                    quality_counts.get("good", 0) / len(runtime) if runtime else 0.0
                ),
            },
            "delivery": {
                "max_latency_ms": max(
                    (float(item.get("latency_ms", 0.0) or 0.0) for item in delivery),
                    default=0.0,
                ),
                "max_queue_depth": max(
                    (int(item.get("queue_depth", 0) or 0) for item in delivery),
                    default=0,
                ),
                "sequence_gap_total": sum(
                    int(item.get("sequence_gap_count", 0) or 0) for item in delivery
                ),
                "replay_states": sorted(
                    {
                        str(item.get("replay_state"))
                        for item in delivery
                        if item.get("replay_state")
                    }
                ),
                "device_log_count": len(device_logs),
                "degraded_delivery_count": sum(
                    1
                    for record in runtime
                    if record.get("delivery_state") in DEGRADED_DELIVERY_STATES
                ),
            },
            "areas": area_views,
            "wells": well_views,
            "assets": enriched_assets,
            "equipment_health": equipment_health,
            "workflows": {
                "events": _tail(events),
                "alarm_journal": _tail(alarms),
                "operator_actions": _tail(operator_actions),
                "well_tests": _tail(well_tests),
                "production_summaries": _tail(summaries),
                "condition_monitoring": _tail(condition),
                "environmental_observations": _tail(environmental),
                "handoff_context": _tail(handoffs),
                "delivery_logs": _tail(delivery),
                "analyses": _tail(analyses),
                "maintenance_context": _tail(maintenance),
                "inspection_context": _tail(inspection),
            },
            "provenance": {
                "artifact_count": sum(
                    1
                    for item in manifest.get("files", [])
                    if item.get("record_type") not in FORBIDDEN_RECORD_TYPES
                ),
                "record_counts": {
                    key: value
                    for key, value in manifest.get("record_counts", {}).items()
                    if key not in FORBIDDEN_RECORD_TYPES
                },
                "files": [
                    {
                        "path": item.get("path"),
                        "record_type": item.get("record_type"),
                        "format": item.get("format"),
                        "record_count": item.get("record_count"),
                        "loaded": item.get("record_type") in families,
                    }
                    for item in manifest.get("files", [])
                    if item.get("record_type") not in FORBIDDEN_RECORD_TYPES
                ],
                "ground_truth_excluded_from_api": True,
            },
        }


__all__ = [
    "READABLE_RECORD_TYPES",
    "UpstreamDashboardError",
    "UpstreamSnapshotService",
]
