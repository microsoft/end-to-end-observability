"""Locked fictional downstream refinery catalog, devices, tags, and topology."""

from __future__ import annotations

import hashlib
from typing import Any

CADENCE_ALLOCATION = {"5s": 48, "30s": 48, "60s": 24}
CADENCE_SECONDS = {"5s": 5, "30s": 30, "60s": 60}
MONITORING_RECORD_TYPES = (
    "events",
    "alarm_journal",
    "operator_actions",
    "device_logs",
    "audit_logs",
    "delivery_logs",
    "analyzer_results",
    "condition_monitoring",
    "electrical_monitoring",
    "environmental_observations",
    "lab_results",
    "maintenance_context",
    "shipment_context",
)
NORMAL_MONITORING_COUNTS = {
    "events": 12,
    "alarm_journal": 4,
    "operator_actions": 2,
    "device_logs": 12,
    "audit_logs": 2,
    "delivery_logs": 12,
    "analyzer_results": 3,
    "condition_monitoring": 8,
    "electrical_monitoring": 4,
    "environmental_observations": 3,
    "lab_results": 2,
    "maintenance_context": 3,
    "shipment_context": 2,
}
AREA_DEFINITIONS = (
    {"area_id": "A01", "display_name": "Crude receipt and feed storage", "primary_asset_type": "receipt_manifold", "primary_model": "generic-receipt-manifold", "support_asset_type": "feed_tank_farm", "support_model": "generic-feed-tank-farm"},
    {"area_id": "A02", "display_name": "Atmospheric and vacuum distillation", "primary_asset_type": "distillation_train", "primary_model": "generic-distillation-train", "support_asset_type": "heater_and_reflux_support", "support_model": "generic-distillation-support"},
    {"area_id": "A03", "display_name": "Conversion", "primary_asset_type": "conversion_train", "primary_model": "generic-conversion-train", "support_asset_type": "fractionation_support", "support_model": "generic-conversion-support"},
    {"area_id": "A04", "display_name": "Treating and reforming", "primary_asset_type": "treating_train", "primary_model": "generic-treating-train", "support_asset_type": "reforming_support", "support_model": "generic-reforming-support"},
    {"area_id": "A05", "display_name": "Hydrogen and sulfur services", "primary_asset_type": "hydrogen_header", "primary_model": "generic-hydrogen-header", "support_asset_type": "sulfur_recovery_support", "support_model": "generic-sulfur-support"},
    {"area_id": "A06", "display_name": "Blending and quality coordination", "primary_asset_type": "blending_manifold", "primary_model": "generic-blending-manifold", "support_asset_type": "additive_and_quality_support", "support_model": "generic-quality-support"},
    {"area_id": "A07", "display_name": "Product storage, loading and vapor handling", "primary_asset_type": "product_loading_system", "primary_model": "generic-product-loading-system", "support_asset_type": "vapor_recovery_support", "support_model": "generic-vapor-recovery-support"},
    {"area_id": "A08", "display_name": "Steam, fuel gas, power and electrical distribution", "primary_asset_type": "utility_distribution_hub", "primary_model": "generic-utility-distribution-hub", "support_asset_type": "electrical_switchgear_support", "support_model": "generic-switchgear-support"},
    {"area_id": "A09", "display_name": "Cooling/process water, instrument air and wastewater", "primary_asset_type": "water_and_air_hub", "primary_model": "generic-water-and-air-hub", "support_asset_type": "wastewater_support", "support_model": "generic-wastewater-support"},
    {"area_id": "A10", "display_name": "Flare, emissions, fenceline, LDAR and ambient evidence", "primary_asset_type": "environmental_monitoring_hub", "primary_model": "generic-environmental-monitoring-hub", "support_asset_type": "flare_and_ldar_support", "support_model": "generic-flare-ldar-support"},
    {"area_id": "A11", "display_name": "Analyzer, laboratory, maintenance and inspection context", "primary_asset_type": "analyzer_and_lab_hub", "primary_model": "generic-analyzer-lab-hub", "support_asset_type": "maintenance_registry_support", "support_model": "generic-maintenance-support"},
    {"area_id": "A12", "display_name": "Shared OT data systems", "primary_asset_type": "ot_data_hub", "primary_model": "generic-ot-data-hub", "support_asset_type": "historian_and_gateway_support", "support_model": "generic-historian-gateway-support"},
)
TAG_DEFINITIONS = (
    ("throughput_index", "index", "5s", 5, "F1"),
    ("pressure_kpa", "kPa", "5s", 5, "F1"),
    ("temperature_c", "degC", "5s", 5, "F2"),
    ("power_kw", "kW", "5s", 5, "F2"),
    ("inventory_index", "index", "30s", 30, "F1"),
    ("quality_index", "index", "30s", 30, "F2"),
    ("condition_index", "index", "30s", 30, "F2"),
    ("utility_balance_index", "index", "30s", 30, "F2"),
    ("availability_index", "index", "60s", 60, "F3"),
    ("observation_index", "index", "60s", 60, "FH"),
)


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def project_scheduled_telemetry_count(duration_seconds: int) -> int:
    if duration_seconds <= 0:
        return 0
    return sum(
        count * (((duration_seconds - 1) // cadence_seconds) + 1)
        for cadence_class, count in CADENCE_ALLOCATION.items()
        if (cadence_seconds := CADENCE_SECONDS[cadence_class]) is not None
    )


def build_catalog(config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    site_id = config["site"]["site_id"]
    assets: list[dict[str, Any]] = []
    devices: list[dict[str, Any]] = []
    tags: list[dict[str, Any]] = []
    topology: list[dict[str, Any]] = []
    for area_index, area in enumerate(AREA_DEFINITIONS, 1):
        primary_asset_id = _stable_id("asset", site_id, area["area_id"], "asset", "1")
        support_asset_id = _stable_id("asset", site_id, area["area_id"], "asset", "2")
        primary_device_id = _stable_id("device", site_id, area["area_id"], "device", "1")
        support_device_id = _stable_id("device", site_id, area["area_id"], "device", "2")
        assets.extend([
            {"schema_version": "1.0", "site_id": site_id, "area_id": area["area_id"], "area_name": area["display_name"], "asset_id": primary_asset_id, "asset_index": 1, "asset_type": area["primary_asset_type"], "generic_model": area["primary_model"], "display_name": f"{area['display_name']} Primary Asset", "fidelity": "F0", "is_synthetic": True, "operational_use": "prohibited"},
            {"schema_version": "1.0", "site_id": site_id, "area_id": area["area_id"], "area_name": area["display_name"], "asset_id": support_asset_id, "asset_index": 2, "asset_type": area["support_asset_type"], "generic_model": area["support_model"], "display_name": f"{area['display_name']} Support Asset", "fidelity": "F0", "is_synthetic": True, "operational_use": "prohibited"},
        ])
        devices.extend([
            {"schema_version": "1.0", "site_id": site_id, "area_id": area["area_id"], "asset_id": primary_asset_id, "device_id": primary_device_id, "device_index": 1, "device_role": "process_edge", "transport_class": "wired_process_bus", "display_name": f"{area['area_id']} primary process edge", "fidelity": "F0", "is_synthetic": True, "operational_use": "prohibited"},
            {"schema_version": "1.0", "site_id": site_id, "area_id": area["area_id"], "asset_id": support_asset_id, "device_id": support_device_id, "device_index": 2, "device_role": "diagnostic_edge", "transport_class": "ot_gateway", "display_name": f"{area['area_id']} support diagnostic edge", "fidelity": "F0", "is_synthetic": True, "operational_use": "prohibited"},
        ])
        for tag_index, (measurement, unit, cadence_class, cadence_seconds, fidelity) in enumerate(TAG_DEFINITIONS, 1):
            device_id = primary_device_id if tag_index <= 5 else support_device_id
            asset_id = primary_asset_id if tag_index <= 5 else support_asset_id
            tags.append({"schema_version": "1.0", "site_id": site_id, "area_id": area["area_id"], "asset_id": asset_id, "device_id": device_id, "tag_id": _stable_id("tag", site_id, area["area_id"], measurement, str(tag_index)), "tag_index": tag_index, "measurement": measurement, "unit": unit, "cadence_class": cadence_class, "cadence_seconds": cadence_seconds, "fidelity": fidelity, "is_synthetic": True, "operational_use": "prohibited"})
        topology.extend([
            {"schema_version": "1.0", "site_id": site_id, "area_id": area["area_id"], "edge_id": _stable_id("edge", site_id, area["area_id"], "supports"), "source_kind": "asset", "source_id": support_asset_id, "target_kind": "asset", "target_id": primary_asset_id, "relationship": "supports", "fidelity": "F0", "is_synthetic": True, "operational_use": "prohibited"},
            {"schema_version": "1.0", "site_id": site_id, "area_id": area["area_id"], "edge_id": _stable_id("edge", site_id, area["area_id"], "device", "1"), "source_kind": "device", "source_id": primary_device_id, "target_kind": "asset", "target_id": primary_asset_id, "relationship": "observes", "fidelity": "F0", "is_synthetic": True, "operational_use": "prohibited"},
            {"schema_version": "1.0", "site_id": site_id, "area_id": area["area_id"], "edge_id": _stable_id("edge", site_id, area["area_id"], "device", "2"), "source_kind": "device", "source_id": support_device_id, "target_kind": "asset", "target_id": support_asset_id, "relationship": "observes", "fidelity": "F0", "is_synthetic": True, "operational_use": "prohibited"},
        ])
        if area_index < len(AREA_DEFINITIONS):
            next_area = AREA_DEFINITIONS[area_index]
            topology.append({"schema_version": "1.0", "site_id": site_id, "area_id": area["area_id"], "edge_id": _stable_id("edge", site_id, area["area_id"], next_area["area_id"], "handoff"), "source_kind": "asset", "source_id": primary_asset_id, "target_kind": "asset", "target_id": _stable_id("asset", site_id, next_area["area_id"], "asset", "1"), "relationship": "hands_off_to", "fidelity": "F0", "is_synthetic": True, "operational_use": "prohibited"})
    return assets, devices, tags, topology
