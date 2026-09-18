"""Fictional asset and topology construction."""

from __future__ import annotations

import re
from typing import Any

ASSET_SCHEMA_VERSION = "1.0"
TOPOLOGY_SCHEMA_VERSION = "1.0"


def area_location_ref(area: str) -> str:
    """Return the stable fictional location reference for an area name."""
    slug = re.sub(r"[^a-z0-9]+", "-", area.casefold()).strip("-")
    return f"fictional-area-{slug}"


def _asset(
    asset_id: str,
    asset_type: str,
    generic_model: str,
    location_ref: str,
    rated_kva: float,
    nominal_voltage_v: float,
    phases: list[str],
    cadence: int,
    **ratings: float,
) -> dict[str, Any]:
    combined_ratings = {"rated_kva": rated_kva, **ratings}
    return {
        "schema_version": ASSET_SCHEMA_VERSION,
        "asset_id": asset_id,
        "asset_type": asset_type,
        "generic_model": generic_model,
        "fictional": True,
        "location_ref": location_ref,
        "ratings": combined_ratings,
        "phases": phases,
        "nominal_voltage_v": nominal_voltage_v,
        "telemetry_cadence_seconds": cadence,
        "reference_catalog_key": None,
    }


def build_catalog(
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Build deterministic generic assets, fictional edges, and simulation indexes."""
    fleet = config["fleet"]
    cadence = config["simulation"]["interval_minutes"] * 60
    city_slug = re.sub(r"[^a-z0-9]+", "-", config["location"]["city"].casefold()).strip("-")
    location_ref = f"fictional-region-{city_slug or 'location'}"
    area_location_refs = [
        area_location_ref(area) for area in config["location"].get("areas", [])
    ]
    assets: list[dict[str, Any]] = []
    asset_index: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    edge_counter = 0
    valid_from = config["simulation"]["start_utc"]
    valid_to = config["simulation"]["end_utc"]

    def add(asset: dict[str, Any]) -> str:
        assets.append(asset)
        asset_index[asset["asset_id"]] = asset
        return asset["asset_id"]

    def connect(source: str, target: str, relationship: str = "energizes") -> None:
        nonlocal edge_counter
        edge_counter += 1
        source_phases = asset_index[source]["phases"]
        target_phases = asset_index[target]["phases"]
        phases = (
            [phase for phase in target_phases if phase in source_phases]
            if relationship == "energizes"
            else []
        )
        edges.append(
            {
                "schema_version": TOPOLOGY_SCHEMA_VERSION,
                "topology_version": "fictional-v1",
                "edge_id": f"edge-{edge_counter:04d}",
                "source_asset_id": source,
                "target_asset_id": target,
                "relationship": relationship,
                "phases": phases,
                "valid_from_utc": valid_from,
                "valid_to_utc": valid_to,
            }
        )

    grid = add(
        _asset(
            "asset-grid-source-001",
            "grid_source",
            "generic-grid-source",
            location_ref,
            100000.0,
            138000.0,
            ["A", "B", "C"],
            cadence,
        )
    )
    substation = add(
        _asset(
            "asset-substation-001",
            "substation",
            "generic-distribution-substation",
            location_ref,
            50000.0,
            138000.0,
            ["A", "B", "C"],
            cadence,
        )
    )
    station_transformer = add(
        _asset(
            "asset-substation-transformer-001",
            "substation_transformer_ltc",
            "generic-substation-transformer-ltc",
            location_ref,
            25000.0,
            13800.0,
            ["A", "B", "C"],
            cadence,
            thermal_time_constant_minutes=180.0,
            synthetic_top_oil_rise_c=50.0,
        )
    )
    weather = add(
        _asset(
            "asset-weather-station-001",
            "weather_station",
            "generic-weather-station",
            location_ref,
            0.0,
            0.0,
            [],
            cadence,
        )
    )
    connect(grid, substation)
    connect(substation, station_transformer)
    connect(weather, substation, "observes")

    context: dict[str, Any] = {
        "grid_id": grid,
        "substation_id": substation,
        "station_transformer_id": station_transformer,
        "weather_id": weather,
        "feeders": {},
        "transformer_meters": {},
        "meter_pv": {},
        "meter_evse": {},
        "asset_feeder": {},
        "asset_transformer": {},
    }
    all_meter_ids: list[str] = []

    for feeder_number in range(1, fleet["feeders"] + 1):
        suffix = f"{feeder_number:03d}"
        feeder_location_ref = (
            area_location_refs[(feeder_number - 1) % len(area_location_refs)]
            if area_location_refs
            else location_ref
        )
        relay = add(
            _asset(
                f"asset-feeder-relay-{suffix}",
                "feeder_relay",
                "generic-feeder-protection-relay",
                feeder_location_ref,
                15000.0,
                13800.0,
                ["A", "B", "C"],
                cadence,
            )
        )
        feeder_meter = add(
            _asset(
                f"asset-feeder-meter-{suffix}",
                "feeder_meter",
                "generic-feeder-revenue-meter",
                feeder_location_ref,
                15000.0,
                13800.0,
                ["A", "B", "C"],
                cadence,
            )
        )
        connect(station_transformer, relay)
        connect(relay, feeder_meter)
        chain_parent = feeder_meter
        reclosers: list[str] = []
        for device_number in range(1, fleet["reclosers_per_feeder"] + 1):
            device_id = add(
                _asset(
                    f"asset-recloser-{suffix}-{device_number:02d}",
                    "recloser",
                    "generic-three-phase-recloser",
                    feeder_location_ref,
                    12000.0,
                    13800.0,
                    ["A", "B", "C"],
                    cadence,
                )
            )
            connect(chain_parent, device_id)
            chain_parent = device_id
            reclosers.append(device_id)
        regulators: list[str] = []
        for device_number in range(1, fleet["regulators_per_feeder"] + 1):
            device_id = add(
                _asset(
                    f"asset-regulator-{suffix}-{device_number:02d}",
                    "regulator",
                    "generic-step-voltage-regulator",
                    feeder_location_ref,
                    10000.0,
                    13800.0,
                    ["A", "B", "C"],
                    cadence,
                    synthetic_tap_step_percent=0.625,
                )
            )
            connect(chain_parent, device_id)
            chain_parent = device_id
            regulators.append(device_id)

        capacitor_banks: list[str] = []
        for device_number in range(1, fleet["capacitor_banks_per_feeder"] + 1):
            device_id = add(
                _asset(
                    f"asset-capacitor-bank-{suffix}-{device_number:02d}",
                    "capacitor_bank",
                    "generic-switched-capacitor-bank",
                    feeder_location_ref,
                    0.0,
                    13800.0,
                    ["A", "B", "C"],
                    cadence,
                    synthetic_rated_kvar=15.0,
                )
            )
            connect(chain_parent, device_id)
            capacitor_banks.append(device_id)

        pq_monitors: list[str] = []
        for device_number in range(1, fleet["power_quality_monitors_per_feeder"] + 1):
            device_id = add(
                _asset(
                    f"asset-power-quality-monitor-{suffix}-{device_number:02d}",
                    "power_quality_monitor",
                    "generic-power-quality-monitor",
                    feeder_location_ref,
                    0.0,
                    13800.0,
                    ["A", "B", "C"],
                    cadence,
                )
            )
            connect(chain_parent, device_id)
            pq_monitors.append(device_id)

        transformers: list[str] = []
        feeder_meter_ids: list[str] = []
        for transformer_number in range(1, fleet["distribution_transformers_per_feeder"] + 1):
            transformer_id = add(
                _asset(
                    f"asset-distribution-transformer-{suffix}-{transformer_number:03d}",
                    "distribution_transformer",
                    "generic-pole-mount-transformer",
                    feeder_location_ref,
                    25.0,
                    240.0,
                    ["A", "B"],
                    cadence,
                    thermal_time_constant_minutes=120.0,
                    synthetic_top_oil_rise_c=45.0,
                )
            )
            connect(chain_parent, transformer_id)
            transformers.append(transformer_id)
            context["transformer_meters"][transformer_id] = []
            context["asset_transformer"][transformer_id] = transformer_id
            for meter_number in range(1, fleet["ami_meters_per_transformer"] + 1):
                meter_id = add(
                    _asset(
                        f"asset-ami-meter-{suffix}-{transformer_number:03d}-{meter_number:03d}",
                        "ami_smart_meter",
                        "generic-ami-smart-meter",
                        feeder_location_ref,
                        15.0,
                        240.0,
                        [["A"], ["B"], ["A", "B"]][(meter_number - 1) % 3],
                        cadence,
                        synthetic_service_limit_kw=12.0,
                    )
                )
                connect(transformer_id, meter_id)
                context["transformer_meters"][transformer_id].append(meter_id)
                context["asset_transformer"][meter_id] = transformer_id
                feeder_meter_ids.append(meter_id)
                all_meter_ids.append(meter_id)

        feeder_assets = [relay, feeder_meter, *reclosers, *regulators, *capacitor_banks, *pq_monitors, *transformers]
        for asset_id in feeder_assets + feeder_meter_ids:
            context["asset_feeder"][asset_id] = suffix
        context["feeders"][suffix] = {
            "relay": relay,
            "meter": feeder_meter,
            "reclosers": reclosers,
            "regulators": regulators,
            "capacitor_banks": capacitor_banks,
            "pq_monitors": pq_monitors,
            "transformers": transformers,
            "ami_meters": feeder_meter_ids,
        }

    pv_count = min(len(all_meter_ids), max(1 if fleet["pv_fraction"] > 0 else 0, round(len(all_meter_ids) * fleet["pv_fraction"])))
    evse_count = min(len(all_meter_ids), max(1 if fleet["evse_fraction"] > 0 else 0, round(len(all_meter_ids) * fleet["evse_fraction"])))
    for index, meter_id in enumerate(all_meter_ids):
        context["meter_pv"][meter_id] = None
        context["meter_evse"][meter_id] = None
        if index < pv_count:
            meter_phases = asset_index[meter_id]["phases"]
            pv_id = add(
                _asset(
                    f"asset-pv-inverter-{index + 1:04d}",
                    "pv_inverter",
                    "generic-grid-interactive-pv-inverter",
                    asset_index[meter_id]["location_ref"],
                    7.6,
                    240.0,
                    meter_phases,
                    cadence,
                    synthetic_dc_capacity_kw=8.0,
                )
            )
            connect(meter_id, pv_id)
            context["meter_pv"][meter_id] = pv_id
            context["asset_feeder"][pv_id] = context["asset_feeder"][meter_id]
            context["asset_transformer"][pv_id] = context["asset_transformer"][meter_id]
        if index >= len(all_meter_ids) - evse_count:
            meter_phases = asset_index[meter_id]["phases"]
            evse_id = add(
                _asset(
                    f"asset-evse-{index + 1:04d}",
                    "evse",
                    "generic-level-2-evse",
                    asset_index[meter_id]["location_ref"],
                    11.5,
                    240.0,
                    meter_phases,
                    cadence,
                    synthetic_max_power_kw=9.6,
                )
            )
            connect(meter_id, evse_id)
            context["meter_evse"][meter_id] = evse_id
            context["asset_feeder"][evse_id] = context["asset_feeder"][meter_id]
            context["asset_transformer"][evse_id] = context["asset_transformer"][meter_id]

    assets.sort(key=lambda item: item["asset_id"])
    edges.sort(key=lambda item: item["edge_id"])
    context["asset_by_id"] = {asset["asset_id"]: asset for asset in assets}
    context["all_meter_ids"] = all_meter_ids
    return assets, edges, context
