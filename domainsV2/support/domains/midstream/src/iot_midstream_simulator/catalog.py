"""Stable fictional equipment and liquid-flow topology."""

from __future__ import annotations

from typing import Any


def build_catalog(config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    site_id = config["site"]["site_id"]
    cadence = config["simulation"]["interval_seconds"]
    route_id = f"{site_id}-route-001"
    route_length = config["pipeline"]["length_m"]
    node_count = config["localization"]["monitoring_node_count"]
    definitions = [
        ("receipt-001", "receipt_terminal", "generic-receipt-manifold"),
        ("tank-001", "storage_tank", "generic-vertical-storage-tank"),
        ("valve-001", "control_valve", "generic-motor-operated-valve"),
        ("strainer-001", "strainer", "generic-pipeline-strainer"),
        ("pump-001", "pump_driver", "generic-centrifugal-pump-vfd"),
        ("pipe-001", "pipeline_segment", "generic-liquid-pipeline-segment"),
        ("block-001", "block_station", "generic-block-valve-station"),
        ("pig-001", "pig_receiver", "generic-idle-pig-receiver"),
        ("meter-001", "metering_skid", "generic-liquid-metering-skid"),
        ("rtu-001", "rtu_gateway", "generic-industrial-gateway"),
        ("detector-001", "leak_detection_engine", "generic-synthetic-detector"),
        ("boundary-001", "refinery_receiving_boundary", "generic-downstream-handoff"),
    ]
    assets = [
        {
            "schema_version": "1.0",
            "asset_id": f"{site_id}-{suffix}",
            "asset_type": asset_type,
            "generic_model": model,
            "site_id": site_id,
            "route_id": None,
            "route_distance_m": None,
            "segment_start_distance_m": None,
            "segment_end_distance_m": None,
            "is_synthetic": True,
            "operational_use": "prohibited",
            "telemetry_cadence_seconds": cadence,
        }
        for suffix, asset_type, model in definitions
    ]
    for index in range(node_count):
        distance = route_length * index / (node_count - 1)
        assets.append(
            {
                "schema_version": "1.0",
                "asset_id": f"{site_id}-pressure-node-{index + 1:03d}",
                "asset_type": "pipeline_monitoring_node",
                "generic_model": "generic-pressure-monitoring-node",
                "site_id": site_id,
                "route_id": route_id,
                "route_distance_m": round(distance, 6),
                "segment_start_distance_m": None,
                "segment_end_distance_m": None,
                "is_synthetic": True,
                "operational_use": "prohibited",
                "telemetry_cadence_seconds": cadence,
            }
        )
    for asset in assets:
        if asset["asset_type"] == "pipeline_segment":
            asset.update(
                {
                    "route_id": route_id,
                    "route_distance_m": None,
                    "segment_start_distance_m": 0.0,
                    "segment_end_distance_m": route_length,
                }
            )
    by_suffix = {suffix: f"{site_id}-{suffix}" for suffix, _, _ in definitions}
    flow = ["receipt-001", "tank-001", "valve-001", "strainer-001", "pump-001", "pipe-001", "block-001", "pig-001", "meter-001", "boundary-001"]
    topology = [
        {
            "schema_version": "1.0",
            "edge_id": f"flow-{index:03d}",
            "source_asset_id": by_suffix[source],
            "target_asset_id": by_suffix[target],
            "relationship": "hands_off_to" if target == "boundary-001" else "flows_to",
            "is_synthetic": True,
            "operational_use": "prohibited",
        }
        for index, (source, target) in enumerate(zip(flow, flow[1:]), 1)
    ]
    for index, target in enumerate(flow, len(topology) + 1):
        topology.append(
            {
                "schema_version": "1.0",
                "edge_id": f"comm-{index:03d}",
                "source_asset_id": by_suffix[target],
                "target_asset_id": by_suffix["rtu-001"],
                "relationship": "communicates_via",
                "is_synthetic": True,
                "operational_use": "prohibited",
            }
        )
    detector_id = by_suffix["detector-001"]
    rtu_id = by_suffix["rtu-001"]
    topology.append(
        {
            "schema_version": "1.0",
            "edge_id": f"comm-{len(topology) + 1:03d}",
            "source_asset_id": rtu_id,
            "target_asset_id": detector_id,
            "relationship": "feeds",
            "is_synthetic": True,
            "operational_use": "prohibited",
        }
    )
    pipeline_id = by_suffix["pipe-001"]
    for asset in assets:
        if asset["asset_type"] != "pipeline_monitoring_node":
            continue
        topology.extend(
            [
                {
                    "schema_version": "1.0",
                    "edge_id": f"monitor-{len(topology) + 1:03d}",
                    "source_asset_id": asset["asset_id"],
                    "target_asset_id": pipeline_id,
                    "relationship": "monitors",
                    "is_synthetic": True,
                    "operational_use": "prohibited",
                },
                {
                    "schema_version": "1.0",
                    "edge_id": f"comm-{len(topology) + 2:03d}",
                    "source_asset_id": asset["asset_id"],
                    "target_asset_id": rtu_id,
                    "relationship": "communicates_via",
                    "is_synthetic": True,
                    "operational_use": "prohibited",
                },
            ]
        )
    return assets, topology
