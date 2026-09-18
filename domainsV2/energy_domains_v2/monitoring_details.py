"""Bounded catalog projections, never simulation state or private diagnostics.

Sources are the owning packages' catalog.py (asset/device/signal/topology
definitions) and simulation.py (literal quantity and shared-model semantics).
No numeric engineering reference is synthesized here.
"""

from __future__ import annotations

from copy import deepcopy
from collections import defaultdict

PROFILE = "domain-details-v1"
MAX_LINKS = 4


def _pick(source, names):
    return {key: deepcopy(source[key]) for key in names if source.get(key) is not None}


class Projection:
    def __init__(self, surface, families, config=None):
        self.surface = surface
        self.config = config or {}
        self.signals = {r["signal_id"]: r for r in families.get("signals", [])}
        self.tags = {r["tag_id"]: r for r in families.get("tags", [])}
        self.devices = {r["device_id"]: r for r in families.get("devices", [])}
        self.wells = {r["well_id"]: r for r in families.get("wells", [])}
        self.links = defaultdict(list)
        for edge in families.get("topology", []):
            source = edge.get("source_id", edge.get("source_asset_id"))
            target = edge.get("target_id", edge.get("target_asset_id"))
            link = _pick(edge, ("edge_id", "schema_version", "topology_version", "relationship",
                                "phases", "valid_from_utc", "valid_to_utc"))
            link.update(source_id=source, target_id=target,
                        source_kind=edge.get("source_kind", "asset"),
                        target_kind=edge.get("target_kind", "asset"))
            self.links[source].append(link)
            self.links[target].append(link)

    def context(self, record, asset):
        surface = self.surface
        asset_context = _pick(asset, (
            "schema_version", "asset_type", "generic_model", "enterprise_id", "site_id",
            "area_id", "area_name", "facility_id", "pad_id", "well_id", "module_id",
            "archetype", "location_ref", "fidelity", "route_id", "route_distance_m",
            "segment_start_distance_m", "segment_end_distance_m",
        ))
        asset_context["semantics"] = "fictional_catalog_configuration_not_operating_state"
        if surface == "power-grid":
            asset_context.update(_pick(asset, ("nominal_voltage_v", "phases")))
            ratings = _pick(asset.get("ratings", {}), (
                "rated_kva", "thermal_time_constant_minutes", "synthetic_top_oil_rise_c",
                "synthetic_tap_step_percent", "synthetic_rated_kvar",
                "synthetic_service_limit_kw", "synthetic_dc_capacity_kw", "synthetic_max_power_kw",
            ))
            if ratings:
                asset_context["ratings"] = ratings
                asset_context["ratings_semantics"] = "synthetic_model_parameters_not_approved_limits"
        if surface == "upstream":
            well = self.wells.get(asset.get("well_id"), {})
            if well:
                asset_context["well_configuration"] = _pick(well, (
                    "well_id", "pad_id", "well_kind", "lift_type", "archetype",
                    "producing_interval_label", "completion_context_class", "completion_revision",
                ))
                asset_context["well_join"] = "assets.well_id=wells.well_id; not_exclusive_pad_ownership"
        if surface == "midstream" and asset.get("route_id"):
            asset_context["route_semantics"] = "fictional_route_distance_not_geodetic_position"
        if surface == "midstream":
            configured = {
                "pipeline_segment": ("pipeline", ("length_m", "diameter_m", "roughness_m",
                                                 "reference_pressure_kpa", "effective_bulk_modulus_kpa")),
                "pump_driver": ("pump", ("rated_flow_m3_s", "rated_head_m", "rated_speed_rpm",
                                        "efficiency", "thermal_time_constant_s", "coast_down_time_constant_s")),
                "storage_tank": ("tank", ("diameter_m", "height_m")),
            }.get(asset.get("asset_type"))
            if configured:
                section, keys = configured
                parameters = _pick(self.config.get(section, {}), keys)
                if parameters:
                    asset_context["configuration"] = {
                        "source": f"normalized_config.{section}",
                        "parameters": parameters,
                        "semantics": "fictional_model_parameters_not_approved_limits_or_pressure_basis",
                    }
        if surface == "downstream":
            asset_context["equipment_scope"] = "aggregate_monitoring_asset_not_local_only_equipment_register"
        if surface == "downstream-portfolio" and asset.get("product_family"):
            asset_context["primary_product_family"] = asset["product_family"]
            asset_context["product_semantics"] = "asset_primary_family_not_observed_tag_material"

        definition = (self.signals.get(record.get("signal_id"))
                      or self.tags.get(record.get("tag_id")) or {})
        measurement = _pick(definition, (
            "signal_id", "tag_id", "device_id", "cadence_seconds", "cadence_class",
            "fidelity", "state_scope", "product_family",
        ))
        # Telemetry carries these source fields even without an available catalog join.
        for key in ("signal_id", "cadence_seconds", "fidelity", "product_family"):
            if key not in measurement and record.get(key) is not None:
                measurement[key] = deepcopy(record[key])
        measurement.update(_pick(record, ("measurement", "unit", "phase", "aggregation", "interval_seconds")))
        if "cadence_seconds" in measurement:
            measurement["cadence_source"] = "source.cadence_seconds"
        elif asset.get("telemetry_cadence_seconds") is not None:
            measurement["cadence_seconds"] = asset["telemetry_cadence_seconds"]
            measurement["cadence_source"] = "assets.telemetry_cadence_seconds"
        measurement["cadence_semantics"] = "scheduled_source_interval_not_delivery_freshness"
        if record.get("signal_id"):
            measurement["identity_mapping"] = "source.signal_id; envelope_tag_identity_unchanged"
        unit = record.get("unit", "")
        name = record["measurement"]
        if unit == "index" or name == "condition_index":
            kind = "synthetic_model_index_not_physical_measurement"
        elif unit.startswith("equivalent_volume_units"):
            kind = "fictional_equivalent_quantity_not_engineering_volume"
        elif unit in {"1", "state", "count", "share"}:
            kind = "dimensionless_state_count_or_fraction"
        else:
            kind = "synthetic_quantity_in_literal_source_unit"
        measurement["quantity_kind"] = kind
        if surface == "power-grid" and name == "condition_index":
            measurement["model_semantics"] = "derived_heuristic_condition_index_not_independent_physical_evidence"
        if surface == "midstream":
            semantics = {
                "mass_flow": "derived_from_meter_volume_flow_and_model_density_not_independent",
                "volumetric_flow": "alias_of_pipeline_outlet_volume_flow_not_independent",
                "outlet_pressure": "shared_model_reading_with_final_pressure_node_and_receipt_pressure",
                "inlet_pressure": "shared_model_reading_with_first_pressure_node",
                "receipt_pressure": "shared_model_reading_with_pipeline_outlet_and_final_pressure_node",
                "line_pressure": "endpoint_nodes_share_pipeline_endpoint_readings",
                "calculated_imbalance": "derived_inlet_minus_outlet_volume_flow_not_independent",
            }
            if name in semantics:
                measurement["model_semantics"] = semantics[name]
        device = self.devices.get(definition.get("device_id", record.get("device_id")), {})
        if device:
            measurement["device"] = _pick(device, (
                "device_id", "device_role", "transport_class", "clock_source_class",
            ))
        links = sorted(self.links.get(asset["asset_id"], []), key=lambda link: (
            link["relationship"] in {"communicates_via", "observes", "feeds"},
            link["edge_id"],
        ))
        return {
            "details_profile": PROFILE,
            "asset": asset_context,
            "measurement": measurement,
            "topology": {
                "semantics": "fictional_catalog_relationships_not_engineered_connectivity",
                "source": "catalog.topology",
                "links": deepcopy(links[:MAX_LINKS]),
                "available_link_count": len(links),
                "omitted_link_count": max(0, len(links) - MAX_LINKS),
            },
        }
