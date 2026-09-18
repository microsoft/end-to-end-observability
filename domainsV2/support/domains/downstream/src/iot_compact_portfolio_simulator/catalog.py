"""Locked fictional compact-portfolio catalog, topology, devices, and tags."""

from __future__ import annotations

from typing import Any

PRODUCT_FAMILIES = (
    "gasoline_and_blendstocks",
    "middle_distillates",
    "light_gases",
    "heavy_and_specialty",
    "petrochemical_feedstocks",
    "renewable_products",
)
CADENCE_ALLOCATION = {
    "5s": 751,
    "30s": 4134,
    "60s": 1879,
    "1h": 376,
    "event": 376,
}
CADENCE_SECONDS = {"5s": 5, "30s": 30, "60s": 60, "1h": 3600, "event": None}
SITE_DEFINITIONS = (
    {
        "site_id": "fictional-compact-site-a",
        "display_name": "Fictional Intake and Blending Complex",
        "module_ids": (
            "receipt_offsites",
            "gasoline_conversion",
            "non_crude_blendstock",
            "enterprise_blending_dispatch",
        ),
    },
    {
        "site_id": "fictional-compact-site-b",
        "display_name": "Fictional Conversion and Chemical Complex",
        "module_ids": (
            "deep_conversion",
            "distillate_conversion",
            "chemical_integration",
        ),
    },
    {
        "site_id": "fictional-compact-site-c",
        "display_name": "Fictional Specialty and Renewable Hub",
        "module_ids": (
            "specialty_products",
            "renewable_fuels",
        ),
    },
)
MODULE_DEFINITIONS = (
    {
        "site_id": "fictional-compact-site-a",
        "module_id": "receipt_offsites",
        "display_name": "Receipt and Offsites Corridor",
        "equipment_count": 60,
        "device_count": 210,
        "tag_count": 900,
        "cadence_counts": {"5s": 90, "30s": 495, "60s": 225, "1h": 45, "event": 45},
        "primary_product_family": "gasoline_and_blendstocks",
        "supporting_product_families": ("middle_distillates", "renewable_products"),
        "fidelity": "physics_lite_corridor",
        "base_throughput_index": 0.78,
        "initial_inventory_units": 1460.0,
        "yield_shares": {
            "gasoline_and_blendstocks": 0.24,
            "middle_distillates": 0.22,
            "light_gases": 0.12,
            "heavy_and_specialty": 0.16,
            "petrochemical_feedstocks": 0.10,
            "renewable_products": 0.16,
        },
    },
    {
        "site_id": "fictional-compact-site-a",
        "module_id": "gasoline_conversion",
        "display_name": "Gasoline Conversion",
        "equipment_count": 105,
        "device_count": 365,
        "tag_count": 1580,
        "cadence_counts": {"5s": 158, "30s": 869, "60s": 395, "1h": 79, "event": 79},
        "primary_product_family": "gasoline_and_blendstocks",
        "supporting_product_families": ("light_gases", "petrochemical_feedstocks"),
        "fidelity": "causal_aggregate",
        "base_throughput_index": 0.82,
        "initial_inventory_units": 1785.0,
        "yield_shares": {
            "gasoline_and_blendstocks": 0.56,
            "middle_distillates": 0.12,
            "light_gases": 0.12,
            "heavy_and_specialty": 0.08,
            "petrochemical_feedstocks": 0.06,
            "renewable_products": 0.06,
        },
    },
    {
        "site_id": "fictional-compact-site-a",
        "module_id": "non_crude_blendstock",
        "display_name": "Non Crude Blendstock",
        "equipment_count": 40,
        "device_count": 140,
        "tag_count": 600,
        "cadence_counts": {"5s": 60, "30s": 330, "60s": 150, "1h": 30, "event": 30},
        "primary_product_family": "light_gases",
        "supporting_product_families": ("gasoline_and_blendstocks", "petrochemical_feedstocks"),
        "fidelity": "causal_aggregate",
        "base_throughput_index": 0.64,
        "initial_inventory_units": 980.0,
        "yield_shares": {
            "gasoline_and_blendstocks": 0.18,
            "middle_distillates": 0.08,
            "light_gases": 0.36,
            "heavy_and_specialty": 0.10,
            "petrochemical_feedstocks": 0.20,
            "renewable_products": 0.08,
        },
    },
    {
        "site_id": "fictional-compact-site-a",
        "module_id": "enterprise_blending_dispatch",
        "display_name": "Enterprise Blending and Dispatch",
        "equipment_count": 25,
        "device_count": 90,
        "tag_count": 360,
        "cadence_counts": {"5s": 36, "30s": 198, "60s": 90, "1h": 18, "event": 18},
        "primary_product_family": "gasoline_and_blendstocks",
        "supporting_product_families": ("middle_distillates", "renewable_products"),
        "fidelity": "causal_aggregate",
        "base_throughput_index": 0.74,
        "initial_inventory_units": 845.0,
        "yield_shares": {
            "gasoline_and_blendstocks": 0.32,
            "middle_distillates": 0.24,
            "light_gases": 0.06,
            "heavy_and_specialty": 0.10,
            "petrochemical_feedstocks": 0.08,
            "renewable_products": 0.20,
        },
    },
    {
        "site_id": "fictional-compact-site-b",
        "module_id": "deep_conversion",
        "display_name": "Deep Conversion",
        "equipment_count": 95,
        "device_count": 340,
        "tag_count": 1660,
        "cadence_counts": {"5s": 166, "30s": 913, "60s": 415, "1h": 83, "event": 83},
        "primary_product_family": "heavy_and_specialty",
        "supporting_product_families": ("middle_distillates", "petrochemical_feedstocks"),
        "fidelity": "causal_aggregate",
        "base_throughput_index": 0.80,
        "initial_inventory_units": 1735.0,
        "yield_shares": {
            "gasoline_and_blendstocks": 0.18,
            "middle_distillates": 0.22,
            "light_gases": 0.12,
            "heavy_and_specialty": 0.26,
            "petrochemical_feedstocks": 0.16,
            "renewable_products": 0.06,
        },
    },
    {
        "site_id": "fictional-compact-site-b",
        "module_id": "distillate_conversion",
        "display_name": "Distillate Conversion",
        "equipment_count": 70,
        "device_count": 245,
        "tag_count": 1120,
        "cadence_counts": {"5s": 112, "30s": 616, "60s": 280, "1h": 56, "event": 56},
        "primary_product_family": "middle_distillates",
        "supporting_product_families": ("heavy_and_specialty", "petrochemical_feedstocks"),
        "fidelity": "causal_aggregate",
        "base_throughput_index": 0.77,
        "initial_inventory_units": 1365.0,
        "yield_shares": {
            "gasoline_and_blendstocks": 0.10,
            "middle_distillates": 0.56,
            "light_gases": 0.08,
            "heavy_and_specialty": 0.10,
            "petrochemical_feedstocks": 0.10,
            "renewable_products": 0.06,
        },
    },
    {
        "site_id": "fictional-compact-site-b",
        "module_id": "chemical_integration",
        "display_name": "Chemical Integration",
        "equipment_count": 30,
        "device_count": 105,
        "tag_count": 400,
        "cadence_counts": {"5s": 40, "30s": 220, "60s": 100, "1h": 20, "event": 20},
        "primary_product_family": "petrochemical_feedstocks",
        "supporting_product_families": ("light_gases", "heavy_and_specialty"),
        "fidelity": "causal_aggregate",
        "base_throughput_index": 0.66,
        "initial_inventory_units": 910.0,
        "yield_shares": {
            "gasoline_and_blendstocks": 0.08,
            "middle_distillates": 0.06,
            "light_gases": 0.16,
            "heavy_and_specialty": 0.10,
            "petrochemical_feedstocks": 0.50,
            "renewable_products": 0.10,
        },
    },
    {
        "site_id": "fictional-compact-site-c",
        "module_id": "specialty_products",
        "display_name": "Specialty Products",
        "equipment_count": 55,
        "device_count": 190,
        "tag_count": 600,
        "cadence_counts": {"5s": 60, "30s": 330, "60s": 150, "1h": 30, "event": 30},
        "primary_product_family": "heavy_and_specialty",
        "supporting_product_families": ("middle_distillates",),
        "fidelity": "causal_aggregate",
        "base_throughput_index": 0.61,
        "initial_inventory_units": 1125.0,
        "yield_shares": {
            "gasoline_and_blendstocks": 0.10,
            "middle_distillates": 0.10,
            "light_gases": 0.06,
            "heavy_and_specialty": 0.58,
            "petrochemical_feedstocks": 0.10,
            "renewable_products": 0.06,
        },
    },
    {
        "site_id": "fictional-compact-site-c",
        "module_id": "renewable_fuels",
        "display_name": "Renewable Fuels",
        "equipment_count": 25,
        "device_count": 88,
        "tag_count": 296,
        "cadence_counts": {"5s": 29, "30s": 163, "60s": 74, "1h": 15, "event": 15},
        "primary_product_family": "renewable_products",
        "supporting_product_families": ("gasoline_and_blendstocks",),
        "fidelity": "causal_aggregate",
        "base_throughput_index": 0.58,
        "initial_inventory_units": 780.0,
        "yield_shares": {
            "gasoline_and_blendstocks": 0.14,
            "middle_distillates": 0.08,
            "light_gases": 0.06,
            "heavy_and_specialty": 0.06,
            "petrochemical_feedstocks": 0.06,
            "renewable_products": 0.60,
        },
    },
)

MODULE_BY_ID = {item["module_id"]: item for item in MODULE_DEFINITIONS}
SITE_BY_ID = {item["site_id"]: item for item in SITE_DEFINITIONS}

_ASSET_TYPE_CYCLES = {
    "receipt_offsites": (
        ("monitoring_node", "generic-corridor-monitoring-node"),
        ("corridor_segment", "generic-offsites-corridor-segment"),
        ("receipt_manifold", "generic-receipt-manifold"),
        ("storage_tank", "generic-vertical-storage-tank"),
        ("transfer_pump", "generic-transfer-pump"),
        ("control_valve", "generic-control-valve"),
        ("metering_skid", "generic-metering-skid"),
        ("sampling_skid", "generic-sampling-skid"),
        ("dispatcher_rack", "generic-dispatcher-rack"),
        ("analytics_engine", "generic-bounded-corridor-detector"),
    ),
}
_DEFAULT_ASSET_TYPES = (
    ("reactor_train", "generic-conversion-train"),
    ("heater", "generic-fired-heater"),
    ("separator", "generic-separator"),
    ("storage_tank", "generic-buffer-tank"),
    ("transfer_pump", "generic-transfer-pump"),
    ("heat_exchanger", "generic-heat-exchanger"),
    ("compressor", "generic-compressor"),
    ("control_valve", "generic-control-valve"),
    ("metering_skid", "generic-metering-skid"),
    ("analyzer_skid", "generic-online-analyzer"),
    ("blend_header", "generic-blend-header"),
)
_DEVICE_ROLE_CYCLE = (
    "sensor_pack",
    "control_io",
    "condition_monitor",
    "communications_gateway",
    "quality_analyzer",
)
_FAST_MEASUREMENTS = (
    "throughput_actual",
    "pressure_index",
    "temperature_index",
    "utility_load_index",
    "condition_index",
    "inventory_balance_rate",
    "asset_load_index",
    "power_index",
)
_SLOW_MEASUREMENTS = (
    "throughput_target",
    "inventory_equivalent_volume",
    "pressure_index",
    "temperature_index",
    "utility_load_index",
    "condition_index",
    "clock_offset_measurement",
    "queue_depth_measurement",
)
_HOURLY_MEASUREMENTS = (
    "hourly_throughput_total",
    "hourly_inventory_delta",
    "hourly_condition_score",
    "hourly_heartbeat",
)
_EVENT_MEASUREMENTS = (
    "communications_state",
    "device_fault_state",
    "quality_state",
    "dispatch_state",
)


def project_scheduled_telemetry_count(duration_seconds: int) -> int:
    if duration_seconds <= 0:
        return 0
    return sum(
        count * (((duration_seconds - 1) // cadence_seconds) + 1)
        for cadence, count in CADENCE_ALLOCATION.items()
        if (cadence_seconds := CADENCE_SECONDS[cadence]) is not None
    )


def _iter_asset_models(module_id: str, count: int) -> list[tuple[str, str]]:
    cycle = _ASSET_TYPE_CYCLES.get(module_id, _DEFAULT_ASSET_TYPES)
    if module_id == "receipt_offsites":
        seeded = [
            ("monitoring_node", "generic-corridor-monitoring-node"),
            ("monitoring_node", "generic-corridor-monitoring-node"),
            ("monitoring_node", "generic-corridor-monitoring-node"),
            ("monitoring_node", "generic-corridor-monitoring-node"),
            ("monitoring_node", "generic-corridor-monitoring-node"),
            ("monitoring_node", "generic-corridor-monitoring-node"),
            ("monitoring_node", "generic-corridor-monitoring-node"),
            ("corridor_segment", "generic-offsites-corridor-segment"),
            ("corridor_segment", "generic-offsites-corridor-segment"),
            ("corridor_segment", "generic-offsites-corridor-segment"),
            ("corridor_segment", "generic-offsites-corridor-segment"),
            ("corridor_segment", "generic-offsites-corridor-segment"),
            ("corridor_segment", "generic-offsites-corridor-segment"),
            ("receipt_manifold", "generic-receipt-manifold"),
            ("storage_tank", "generic-vertical-storage-tank"),
            ("transfer_pump", "generic-transfer-pump"),
            ("control_valve", "generic-control-valve"),
            ("metering_skid", "generic-metering-skid"),
            ("analytics_engine", "generic-bounded-corridor-detector"),
        ]
        remaining = [cycle[index % len(cycle)] for index in range(max(0, count - len(seeded)))]
        return seeded + remaining
    return [cycle[index % len(cycle)] for index in range(count)]


def _measurement_spec(module: dict[str, Any], cadence_class: str, ordinal: int) -> tuple[str, str, str | None]:
    if cadence_class == "5s" and module["module_id"] == "receipt_offsites":
        if ordinal <= 7:
            return ("corridor_line_pressure", "index", None)
        if ordinal == 8:
            return ("corridor_inlet_flow", "equivalent_volume_units_per_hour", None)
        if ordinal == 9:
            return ("corridor_outlet_flow", "equivalent_volume_units_per_hour", None)
        if ordinal == 10:
            return ("corridor_imbalance", "equivalent_volume_units_per_hour", None)
    if cadence_class in {"5s", "30s", "60s"}:
        if ordinal <= len(PRODUCT_FAMILIES):
            family = PRODUCT_FAMILIES[ordinal - 1]
            return ("yield_share", "share", family)
    if cadence_class == "5s":
        measurement = _FAST_MEASUREMENTS[(ordinal - 1) % len(_FAST_MEASUREMENTS)]
        return (measurement, "index" if measurement.endswith("_index") or measurement == "power_index" else "equivalent_volume_units_per_hour", None)
    if cadence_class == "30s":
        measurement = _SLOW_MEASUREMENTS[(ordinal - 1) % len(_SLOW_MEASUREMENTS)]
        unit = {
            "throughput_target": "equivalent_volume_units_per_hour",
            "inventory_equivalent_volume": "equivalent_volume_units",
            "clock_offset_measurement": "ms",
            "queue_depth_measurement": "count",
        }.get(measurement, "index")
        return (measurement, unit, None)
    if cadence_class == "60s":
        measurement = _SLOW_MEASUREMENTS[(ordinal - 1) % len(_SLOW_MEASUREMENTS)]
        unit = {
            "throughput_target": "equivalent_volume_units_per_hour",
            "inventory_equivalent_volume": "equivalent_volume_units",
            "clock_offset_measurement": "ms",
            "queue_depth_measurement": "count",
        }.get(measurement, "index")
        return (measurement, unit, None)
    if cadence_class == "1h":
        measurement = _HOURLY_MEASUREMENTS[(ordinal - 1) % len(_HOURLY_MEASUREMENTS)]
        unit = "count" if measurement == "hourly_heartbeat" else "equivalent_volume_units"
        if measurement == "hourly_condition_score":
            unit = "index"
        return (measurement, unit, module["primary_product_family"])
    measurement = _EVENT_MEASUREMENTS[(ordinal - 1) % len(_EVENT_MEASUREMENTS)]
    return (measurement, "state", module["primary_product_family"])


def build_catalog(config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    enterprise_id = config["enterprise"]["enterprise_id"]
    assets: list[dict[str, Any]] = []
    devices: list[dict[str, Any]] = []
    tags: list[dict[str, Any]] = []
    topology: list[dict[str, Any]] = []

    asset_ids_by_module: dict[str, list[str]] = {}
    device_ids_by_asset: dict[str, list[str]] = {}

    for site in SITE_DEFINITIONS:
        topology.append(
            {
                "schema_version": "1.0",
                "edge_id": f"{site['site_id']}-edge-enterprise",
                "source_kind": "enterprise",
                "source_id": enterprise_id,
                "target_kind": "site",
                "target_id": site["site_id"],
                "relationship": "contains",
                "enterprise_id": enterprise_id,
                "site_id": site["site_id"],
                "module_id": None,
                "is_synthetic": True,
                "operational_use": "prohibited",
            }
        )

    for module in MODULE_DEFINITIONS:
        site_id = module["site_id"]
        module_id = module["module_id"]
        topology.append(
            {
                "schema_version": "1.0",
                "edge_id": f"{site_id}-{module_id}-edge-site",
                "source_kind": "site",
                "source_id": site_id,
                "target_kind": "module",
                "target_id": module_id,
                "relationship": "contains",
                "enterprise_id": enterprise_id,
                "site_id": site_id,
                "module_id": module_id,
                "is_synthetic": True,
                "operational_use": "prohibited",
            }
        )

        asset_ids_by_module[module_id] = []
        for asset_index, (asset_type, generic_model) in enumerate(
            _iter_asset_models(module_id, module["equipment_count"]),
            1,
        ):
            asset_id = f"{site_id}-{module_id}-asset-{asset_index:03d}"
            asset_ids_by_module[module_id].append(asset_id)
            assets.append(
                {
                    "schema_version": "1.0",
                    "enterprise_id": enterprise_id,
                    "site_id": site_id,
                    "module_id": module_id,
                    "asset_id": asset_id,
                    "asset_index": asset_index,
                    "asset_type": asset_type,
                    "generic_model": generic_model,
                    "display_name": f"{module_id}-{asset_type}-{asset_index:03d}",
                    "product_family": module["primary_product_family"],
                    "fidelity": module["fidelity"],
                    "is_synthetic": True,
                    "operational_use": "prohibited",
                }
            )
            topology.append(
                {
                    "schema_version": "1.0",
                    "edge_id": f"{asset_id}-edge-module",
                    "source_kind": "module",
                    "source_id": module_id,
                    "target_kind": "asset",
                    "target_id": asset_id,
                    "relationship": "contains",
                    "enterprise_id": enterprise_id,
                    "site_id": site_id,
                    "module_id": module_id,
                    "is_synthetic": True,
                    "operational_use": "prohibited",
                }
            )

        for edge_index, (source_asset_id, target_asset_id) in enumerate(
            zip(asset_ids_by_module[module_id], asset_ids_by_module[module_id][1:]),
            1,
        ):
            topology.append(
                {
                    "schema_version": "1.0",
                    "edge_id": f"{module_id}-flow-{edge_index:03d}",
                    "source_kind": "asset",
                    "source_id": source_asset_id,
                    "target_kind": "asset",
                    "target_id": target_asset_id,
                    "relationship": "flows_to",
                    "enterprise_id": enterprise_id,
                    "site_id": site_id,
                    "module_id": module_id,
                    "is_synthetic": True,
                    "operational_use": "prohibited",
                }
            )

        base_device_count = module["device_count"] // module["equipment_count"]
        extra_devices = module["device_count"] % module["equipment_count"]
        device_index = 0
        for asset_position, asset_id in enumerate(asset_ids_by_module[module_id], 1):
            device_ids_by_asset[asset_id] = []
            asset_device_count = base_device_count + (1 if asset_position <= extra_devices else 0)
            for local_device_index in range(1, asset_device_count + 1):
                device_index += 1
                device_id = f"{asset_id}-device-{local_device_index:02d}"
                device_ids_by_asset[asset_id].append(device_id)
                devices.append(
                    {
                        "schema_version": "1.0",
                        "enterprise_id": enterprise_id,
                        "site_id": site_id,
                        "module_id": module_id,
                        "asset_id": asset_id,
                        "device_id": device_id,
                        "device_index": device_index,
                        "device_role": _DEVICE_ROLE_CYCLE[(device_index - 1) % len(_DEVICE_ROLE_CYCLE)],
                        "transport_class": "synthetic-industrial-ethernet",
                        "fidelity": module["fidelity"],
                        "is_synthetic": True,
                        "operational_use": "prohibited",
                    }
                )
                topology.append(
                    {
                        "schema_version": "1.0",
                        "edge_id": f"{device_id}-edge-asset",
                        "source_kind": "asset",
                        "source_id": asset_id,
                        "target_kind": "device",
                        "target_id": device_id,
                        "relationship": "communicates_via",
                        "enterprise_id": enterprise_id,
                        "site_id": site_id,
                        "module_id": module_id,
                        "is_synthetic": True,
                        "operational_use": "prohibited",
                    }
                )

        module_devices = [device for device in devices if device["module_id"] == module_id]
        cadence_tag_ordinals = {cadence: 0 for cadence in module["cadence_counts"]}
        device_cursor = 0
        for cadence_class in ("5s", "30s", "60s", "1h", "event"):
            for _ in range(module["cadence_counts"][cadence_class]):
                cadence_tag_ordinals[cadence_class] += 1
                ordinal = cadence_tag_ordinals[cadence_class]
                if module_id == "receipt_offsites" and cadence_class == "5s" and ordinal <= 10:
                    special_asset_offsets = {
                        1: 0,
                        2: 1,
                        3: 2,
                        4: 3,
                        5: 4,
                        6: 5,
                        7: 6,
                        8: 7,
                        9: 12,
                        10: 18,
                    }
                    asset_id = asset_ids_by_module[module_id][special_asset_offsets[ordinal]]
                    device = next(
                        candidate
                        for candidate in module_devices
                        if candidate["asset_id"] == asset_id
                    )
                else:
                    device = module_devices[device_cursor % len(module_devices)]
                    device_cursor += 1
                measurement, unit, product_family = _measurement_spec(
                    module,
                    cadence_class,
                    ordinal,
                )
                tag_id = f"{device['device_id']}-tag-{ordinal:04d}-{cadence_class}"
                tags.append(
                    {
                        "schema_version": "1.0",
                        "enterprise_id": enterprise_id,
                        "site_id": site_id,
                        "module_id": module_id,
                        "asset_id": device["asset_id"],
                        "device_id": device["device_id"],
                        "tag_id": tag_id,
                        "measurement": measurement,
                        "unit": unit,
                        "cadence_class": cadence_class,
                        "cadence_seconds": CADENCE_SECONDS[cadence_class],
                        "product_family": product_family,
                        "fidelity": module["fidelity"],
                        "is_synthetic": True,
                        "operational_use": "prohibited",
                    }
                )
                topology.append(
                    {
                        "schema_version": "1.0",
                        "edge_id": f"{tag_id}-edge-device",
                        "source_kind": "device",
                        "source_id": device["device_id"],
                        "target_kind": "tag",
                        "target_id": tag_id,
                        "relationship": "measures",
                        "enterprise_id": enterprise_id,
                        "site_id": site_id,
                        "module_id": module_id,
                        "is_synthetic": True,
                        "operational_use": "prohibited",
                    }
                )

    inter_module_flows = (
        ("receipt_offsites", "gasoline_conversion"),
        ("receipt_offsites", "distillate_conversion"),
        ("non_crude_blendstock", "gasoline_conversion"),
        ("gasoline_conversion", "enterprise_blending_dispatch"),
        ("distillate_conversion", "enterprise_blending_dispatch"),
        ("deep_conversion", "chemical_integration"),
        ("deep_conversion", "specialty_products"),
        ("enterprise_blending_dispatch", "renewable_fuels"),
    )
    for flow_index, (source_module_id, target_module_id) in enumerate(inter_module_flows, 1):
        topology.append(
            {
                "schema_version": "1.0",
                "edge_id": f"inter-module-{flow_index:03d}",
                "source_kind": "asset",
                "source_id": asset_ids_by_module[source_module_id][-1],
                "target_kind": "asset",
                "target_id": asset_ids_by_module[target_module_id][0],
                "relationship": "hands_off_to",
                "enterprise_id": enterprise_id,
                "site_id": MODULE_BY_ID[source_module_id]["site_id"],
                "module_id": source_module_id,
                "is_synthetic": True,
                "operational_use": "prohibited",
            }
        )

    assets.sort(key=lambda item: item["asset_id"])
    devices.sort(key=lambda item: item["device_id"])
    tags.sort(key=lambda item: item["tag_id"])
    topology.sort(key=lambda item: item["edge_id"])
    return assets, devices, tags, topology
