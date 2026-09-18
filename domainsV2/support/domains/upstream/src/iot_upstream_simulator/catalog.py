"""Locked fictional upstream production catalog, wells, devices, signals and topology.

Every catalog asset and record has exactly one owning area from the locked
12-area matrix (U01-U12). Cross-area relationships are expressed only through
`topology` edges and correlation identifiers.
"""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Iterable, Mapping
from typing import Any

from .parts import PARTS_OWNING_AREA, PARTS_RECORD_TYPES

CATALOG_RECORD_TYPES = (
    "sites",
    "areas",
    "wells",
    "assets",
    "devices",
    "signals",
    "topology",
)
TELEMETRY_RECORD_TYPES = ("telemetry",)
#: Sparse public log and workflow families. This tuple is the exact monitoring
#: allowlist used by the ADLS sink wrapper.
WORKFLOW_RECORD_TYPES = (
    "well_tests",
    "production_summaries",
    "handoff_context",
    "sample_acquisitions",
    "samples",
    "analyses",
    "condition_monitoring",
    "environmental_observations",
    "events",
    "alarm_journal",
    "operator_actions",
    "device_logs",
    "audit_logs",
    "delivery_logs",
    "maintenance_context",
    "inspection_context",
)
PRIVATE_RECORD_TYPES = ("ground_truth",)
#: Parts, storeroom, candidate and dispatch families. They are not private truth,
#: but they are deliberately local-only until the portal and ADLS publication
#: phases are approved, so they are excluded from every publication path.
LOCAL_ONLY_RECORD_TYPES = PARTS_RECORD_TYPES
RECORD_TYPES = (
    *CATALOG_RECORD_TYPES,
    *TELEMETRY_RECORD_TYPES,
    *WORKFLOW_RECORD_TYPES,
    *LOCAL_ONLY_RECORD_TYPES,
    *PRIVATE_RECORD_TYPES,
)
PUBLIC_RECORD_TYPES = tuple(
    record_type
    for record_type in RECORD_TYPES
    if record_type not in PRIVATE_RECORD_TYPES and record_type not in LOCAL_ONLY_RECORD_TYPES
)

#: Local-only families the owner has explicitly approved for ADLS run
#: publication (design phase 9, authorized 2026-08-27).
#:
#: This is an allowlist, not a reclassification. The families stay in
#: ``LOCAL_ONLY_RECORD_TYPES``, so every path that consults that tuple still
#: refuses them: the public portal (``dashboard/service.py``), the dashboard
#: reader (``dashboard/data.py``), and the ADLS monitoring sink. Only the run
#: publication path consults this list, and a caller must *also* name the family
#: in its own allowed set before anything is written. Two independent
#: conditions, so widening one of them cannot publish by itself.
ADLS_PUBLISHABLE_LOCAL_ONLY_RECORD_TYPES = PARTS_RECORD_TYPES

#: The record types the run publication path may write. Kept in RECORD_TYPES
#: order so published artifacts enumerate deterministically.
PUBLISHED_RECORD_TYPES = tuple(
    record_type
    for record_type in RECORD_TYPES
    if record_type in PUBLIC_RECORD_TYPES
    or record_type in ADLS_PUBLISHABLE_LOCAL_ONLY_RECORD_TYPES
)

# The private families are the one thing no allowlist may ever reach. Assert it
# here rather than trusting the comprehension above to stay correct: this module
# is where someone would edit the allowlist, so this is where it must fail.
if set(PUBLISHED_RECORD_TYPES) & set(PRIVATE_RECORD_TYPES):
    raise AssertionError(
        "private record types must never appear in PUBLISHED_RECORD_TYPES: "
        f"{sorted(set(PUBLISHED_RECORD_TYPES) & set(PRIVATE_RECORD_TYPES))}"
    )
if not set(ADLS_PUBLISHABLE_LOCAL_ONLY_RECORD_TYPES) <= set(LOCAL_ONLY_RECORD_TYPES):
    raise AssertionError(
        "the publication allowlist may only name local-only families it is "
        "actually widening"
    )

#: Policy keys that may appear in a manifest leaving this process. This is an
#: allowlist, not a deny-list, because deny-lists drift: a new policy key is
#: published by default and nobody notices. Anything not named here is dropped,
#: so adding a key to the internal manifest can never silently publish it.
PUBLIC_POLICY_KEYS = frozenset(
    {
        "is_synthetic",
        "operational_use",
        "public_telemetry_layer",
        "private_truth_publication",
        "monitoring_destination_scope",
        "output_format",
        "standards_relationship",
        "deferred_profiles",
        "ground_truth_exposed",
        "telemetry_excluded",
        "structural_catalog_excluded",
    }
)

#: Top-level manifest keys that may leave this process.
PUBLIC_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "generator",
        "run_id",
        "scenario_id",
        "normalized_config",
        "config_digest_sha256",
        "seed",
        "profile",
        "record_counts",
        "record_family_owner",
        "monitoring_caps",
        "files",
        "policy",
        "fidelity_tiers",
        "determinism",
    }
)


def sanitize_public_manifest(
    manifest: Mapping[str, Any],
    allowed_record_types: Iterable[str] | None = None,
    *,
    allow_adls_publishable: bool = False,
) -> dict[str, Any]:
    """Reduce a manifest to what may safely leave this process.

    One sanitizer serves every publication path, and it works by allowlist at
    both the top level and inside ``policy``. The local-only families must not
    be named anywhere in the result: not in the file inventory, not in the
    counts, not in the family-owner map, and not in a policy field that merely
    describes them.

    ``allowed_record_types`` narrows the inventory further for callers that
    publish less than the full public set, such as the dashboard, which serves
    workflow evidence only. The private families are always withheld, whatever
    a caller asks for.

    ``allow_adls_publishable`` is the ADLS run-publication opt-in and defaults
    to off. While it is off this function withholds *every* local-only family
    no matter what ``allowed_record_types`` contains, which is the behaviour
    every caller had before the allowlist existed. That default is deliberate:
    it means a caller cannot start publishing inventory merely because someone
    widened the set of record types it happens to pass in. Only a caller that
    names this argument can publish the allowlisted families, and it must still
    list them in ``allowed_record_types`` as well.
    """
    publishable = (
        set(ADLS_PUBLISHABLE_LOCAL_ONLY_RECORD_TYPES)
        if allow_adls_publishable
        else set()
    )
    withheld = set(PRIVATE_RECORD_TYPES) | (
        set(LOCAL_ONLY_RECORD_TYPES) - publishable
    )
    allowed = (
        set(PUBLIC_RECORD_TYPES)
        if allowed_record_types is None
        else set(allowed_record_types)
    )
    allowed -= withheld
    sanitized = {
        key: copy.deepcopy(value)
        for key, value in manifest.items()
        if key in PUBLIC_MANIFEST_KEYS
    }

    files = [
        entry
        for entry in sanitized.get("files", [])
        if isinstance(entry, Mapping) and entry.get("record_type") in allowed
    ]
    sanitized["files"] = files

    counts = sanitized.get("record_counts")
    if isinstance(counts, dict):
        sanitized["record_counts"] = {
            record_type: value
            for record_type, value in counts.items()
            if record_type in allowed
        }
    else:
        sanitized["record_counts"] = {
            entry["record_type"]: entry.get("record_count", 0)
            for entry in files
            if entry.get("format", "jsonl") == "jsonl"
        }

    owners = sanitized.get("record_family_owner")
    if isinstance(owners, dict):
        sanitized["record_family_owner"] = {
            record_type: owner
            for record_type, owner in owners.items()
            if record_type in allowed
        }

    policy = sanitized.get("policy")
    policy = dict(policy) if isinstance(policy, Mapping) else {}
    policy = {key: value for key, value in policy.items() if key in PUBLIC_POLICY_KEYS}
    policy["is_synthetic"] = True
    policy["operational_use"] = "prohibited"
    policy["private_truth_publication"] = "excluded"
    if allow_adls_publishable:
        # Only this path actually publishes them, so only this path may name
        # them. Listing the intersection rather than the whole allowlist keeps
        # the statement true even if a caller narrows what it publishes: the
        # manifest never names a family it did not ship.
        published_local_only = sorted(
            set(ADLS_PUBLISHABLE_LOCAL_ONLY_RECORD_TYPES) & allowed
        )
        if published_local_only:
            policy["adls_publishable_local_only_record_types"] = published_local_only
            policy["local_only_publication"] = (
                "adls_run_publication_approved_portal_and_dashboard_still_excluded"
            )
    sanitized["policy"] = policy
    return sanitized

#: Family-level owning area from the canonical record taxonomy. `assets`,
#: `devices`, `signals`, `telemetry` and `events` additionally carry a
#: per-record `area_id` for the physical source area.
RECORD_FAMILY_OWNER = {
    "sites": "U12",
    "areas": "U12",
    "wells": "U01",
    "assets": "U12",
    "devices": "U12",
    "signals": "U12",
    "topology": "U12",
    "telemetry": "U12",
    "well_tests": "U11",
    "production_summaries": "U11",
    "handoff_context": "U07",
    "sample_acquisitions": "U11",
    "samples": "U11",
    "analyses": "U11",
    "condition_monitoring": "U11",
    "environmental_observations": "U10",
    "events": "U12",
    "alarm_journal": "U12",
    "operator_actions": "U12",
    "device_logs": "U12",
    "audit_logs": "U12",
    "delivery_logs": "U12",
    "maintenance_context": "U11",
    "inspection_context": "U11",
    **{record_type: PARTS_OWNING_AREA for record_type in PARTS_RECORD_TYPES},
    "ground_truth": "private",
}

AREA_DEFINITIONS = (
    {"area_id": "U01", "display_name": "Well and completion context"},
    {"area_id": "U02", "display_name": "Wellhead, tree, choke and flowline"},
    {"area_id": "U03", "display_name": "Artificial lift"},
    {"area_id": "U04", "display_name": "Primary separation and field treatment"},
    {"area_id": "U05", "display_name": "Field gas compression and conditioning"},
    {"area_id": "U06", "display_name": "Produced water"},
    {"area_id": "U07", "display_name": "Oil storage and operational handoff"},
    {"area_id": "U08", "display_name": "Chemical, flow-assurance and solids context"},
    {"area_id": "U09", "display_name": "Power, fuel and field utilities"},
    {"area_id": "U10", "display_name": "Flare, vent, gas-detection and environmental evidence"},
    {"area_id": "U11", "display_name": "Well tests, samples, laboratory, maintenance and inspection"},
    {"area_id": "U12", "display_name": "Shared field OT and data systems"},
)
AREA_IDS = tuple(area["area_id"] for area in AREA_DEFINITIONS)
AREA_INDEX = {area["area_id"]: index for index, area in enumerate(AREA_DEFINITIONS, 1)}

#: The eight approved Release 1 archetypes.
ARCHETYPES = (
    "onshore_oil_well_pad",
    "onshore_gas_well_pad",
    "multiwell_mixed_lift_pad",
    "central_production_facility",
    "field_compression_and_conditioning",
    "produced_water_storage_and_transfer",
    "oil_tank_battery_and_handoff",
    "shared_field_utilities_and_ot",
)

LIFT_TYPES = ("rod_pump", "esp", "gas_lift", "plunger_lift", "natural_flow")
MIXED_PAD_LIFT_CYCLE = ("esp", "gas_lift", "plunger_lift", "rod_pump", "natural_flow")

#: (measurement, unit, cadence_multiple, fidelity, scope, state_key)
ASSET_SIGNAL_TEMPLATES: dict[str, tuple[tuple[str, str, int, str, str, str], ...]] = {
    "well_context": (
        ("producing_state_index", "index", 5, "F0", "well", "well_online"),
    ),
    "wellhead_tree": (
        ("tubing_pressure_kpa", "kPa", 1, "F1", "well", "p_tubing"),
        ("casing_pressure_kpa", "kPa", 2, "F1", "well", "p_casing"),
        ("wellhead_temperature_c", "degC", 2, "F2", "well", "t_wellhead"),
        ("choke_position_class_index", "index", 5, "F0", "well", "choke_class"),
    ),
    "flowline_manifold": (
        ("flowline_pressure_kpa", "kPa", 1, "F1", "well", "p_flowline"),
        ("flowline_temperature_c", "degC", 2, "F2", "well", "t_flowline"),
        ("flowline_restriction_index", "index", 5, "F2", "well", "restriction"),
        ("flowline_flow_state_index", "index", 2, "F1", "well", "flow_state"),
    ),
    "artificial_lift": (
        ("lift_run_state_index", "index", 1, "F1", "well", "lift_run"),
        ("lift_load_index", "index", 1, "F2", "well", "lift_load"),
        ("lift_current_a", "A", 1, "F1", "well", "lift_current"),
        ("lift_cycle_index", "index", 5, "F2", "well", "lift_cycle"),
    ),
    "inlet_separator": (
        ("separator_pressure_kpa", "kPa", 1, "F1", "facility", "sep_pressure"),
        ("separator_oil_level_index", "index", 1, "F1", "facility", "sep_oil_level"),
        ("separator_water_level_index", "index", 1, "F1", "facility", "sep_water_level"),
        ("separator_gas_rate_ksm3d", "kSm3/d", 1, "F1", "facility", "gas_rate"),
    ),
    "free_water_knockout": (
        ("fwko_water_rate_m3d", "m3/d", 2, "F1", "facility", "water_rate"),
        ("fwko_level_index", "index", 2, "F1", "facility", "fwko_level"),
    ),
    "heater_treater": (
        ("treater_temperature_c", "degC", 1, "F2", "facility", "treater_temp"),
        ("treater_duty_index", "index", 2, "F2", "facility", "treater_duty"),
        ("treater_oil_rate_m3d", "m3/d", 2, "F1", "facility", "oil_rate"),
    ),
    "field_compressor": (
        ("compressor_suction_pressure_kpa", "kPa", 1, "F1", "facility", "comp_suction"),
        ("compressor_discharge_pressure_kpa", "kPa", 1, "F1", "facility", "comp_discharge"),
        ("compressor_power_kw", "kW", 1, "F2", "facility", "comp_power"),
        ("compressor_vibration_index", "index", 2, "F2", "facility", "comp_vibration"),
        ("compressor_mode_index", "index", 5, "F0", "facility", "comp_mode"),
    ),
    "dehydration_unit": (
        ("dehydration_outlet_quality_index", "index", 5, "FH", "facility", "dehy_quality"),
        ("dehydration_circulation_index", "index", 5, "F2", "facility", "dehy_circulation"),
    ),
    "produced_water_tank": (
        ("water_tank_level_index", "index", 1, "F1", "facility", "water_tank_level"),
        ("water_tank_inventory_m3", "m3", 2, "F1", "facility", "water_inventory"),
    ),
    "water_transfer_pump": (
        ("water_pump_run_state_index", "index", 1, "F1", "facility", "water_pump_run"),
        ("water_transfer_rate_m3d", "m3/d", 1, "F1", "facility", "water_transfer_rate"),
    ),
    "reinjection_interface": (
        ("reinjection_route_state_index", "index", 5, "F0", "facility", "reinjection_route"),
    ),
    "oil_tank_battery": (
        ("oil_tank_level_index", "index", 1, "F1", "facility", "oil_tank_level"),
        ("oil_tank_inventory_m3", "m3", 2, "F1", "facility", "oil_inventory"),
        ("oil_tank_temperature_c", "degC", 5, "F2", "facility", "oil_tank_temp"),
    ),
    "oil_transfer_pump": (
        ("transfer_pump_run_state_index", "index", 1, "F1", "facility", "transfer_pump_run"),
        ("oil_transfer_rate_m3d", "m3/d", 1, "F1", "facility", "oil_transfer_rate"),
    ),
    "sales_gathering_meter": (
        ("oil_handoff_rate_index", "index", 1, "F1", "facility", "handoff_rate"),
        ("oil_handoff_total_index", "index", 2, "F3", "facility", "handoff_total"),
        ("oil_handoff_status_index", "index", 5, "F0", "facility", "handoff_status"),
    ),
    "gas_gathering_meter": (
        ("gas_handoff_rate_index", "index", 1, "F1", "facility", "gas_handoff_rate"),
        ("gas_handoff_total_index", "index", 2, "F3", "facility", "gas_handoff_total"),
        ("gas_handoff_status_index", "index", 5, "F0", "facility", "gas_handoff_status"),
    ),
    "chemical_injection_skid": (
        ("chemical_inventory_index", "index", 5, "F0", "facility", "chem_inventory"),
        ("chemical_injection_index", "index", 2, "FH", "facility", "chem_injection"),
    ),
    "restriction_context_monitor": (
        ("solids_context_index", "index", 5, "FH", "facility", "solids_index"),
        ("differential_pressure_index", "index", 2, "F2", "facility", "dp_index"),
    ),
    "field_generator": (
        ("generator_power_kw", "kW", 1, "F1", "facility", "gen_power"),
        ("generator_voltage_v", "V", 1, "F1", "facility", "gen_voltage"),
        ("generator_availability_index", "index", 2, "F1", "facility", "gen_availability"),
    ),
    "fuel_gas_header": (
        ("fuel_gas_pressure_kpa", "kPa", 2, "F1", "facility", "fuel_gas_pressure"),
    ),
    "instrument_air_header": (
        ("instrument_air_pressure_kpa", "kPa", 2, "F1", "facility", "air_pressure"),
    ),
    "flare_vent_router": (
        ("flare_route_state_index", "index", 5, "F0", "facility", "flare_route"),
        ("flare_load_index", "index", 5, "FH", "facility", "flare_load"),
    ),
    "gas_detection_array": (
        ("detector_availability_index", "index", 5, "FH", "facility", "detector_availability"),
        ("detector_observation_index", "index", 5, "FH", "facility", "detector_observation"),
    ),
    "weather_station": (
        ("ambient_temperature_c", "degC", 5, "FH", "facility", "ambient_temp"),
        ("wind_speed_ms", "m/s", 5, "FH", "facility", "wind_speed"),
    ),
    "well_test_separator": (
        ("test_separator_state_index", "index", 5, "F0", "facility", "test_state"),
        ("test_separator_rate_index", "index", 5, "F1", "facility", "test_rate"),
    ),
    "sample_and_lab_context": (
        ("sample_workflow_index", "index", 5, "FH", "facility", "sample_workflow"),
    ),
    "maintenance_registry": (
        ("maintenance_state_index", "index", 5, "F0", "facility", "maintenance_state"),
    ),
    "field_rtu_gateway": (
        ("gateway_queue_depth_count", "count", 1, "F0", "facility", "queue_depth"),
        ("gateway_latency_ms", "ms", 1, "F2", "facility", "latency_ms"),
    ),
    "historian_node": (
        ("historian_sequence_gap_index", "index", 2, "F0", "facility", "sequence_gap"),
    ),
    "scada_clock_source": (
        ("clock_offset_ms_index", "ms", 5, "F0", "facility", "clock_offset"),
    ),
}

#: (area_id, asset_type, archetype, device_role, transport_class)
FACILITY_ASSET_DEFINITIONS = (
    ("U04", "inlet_separator", "central_production_facility", "process_edge", "wired_field_bus"),
    ("U04", "free_water_knockout", "central_production_facility", "process_edge", "wired_field_bus"),
    ("U04", "heater_treater", "central_production_facility", "process_edge", "wired_field_bus"),
    ("U05", "field_compressor", "field_compression_and_conditioning", "rotating_equipment_edge", "wired_field_bus"),
    ("U05", "dehydration_unit", "field_compression_and_conditioning", "process_edge", "wired_field_bus"),
    ("U06", "produced_water_tank", "produced_water_storage_and_transfer", "inventory_edge", "wireless_field_link"),
    ("U06", "water_transfer_pump", "produced_water_storage_and_transfer", "rotating_equipment_edge", "wired_field_bus"),
    ("U06", "reinjection_interface", "produced_water_storage_and_transfer", "handoff_edge", "wireless_field_link"),
    ("U07", "oil_tank_battery", "oil_tank_battery_and_handoff", "inventory_edge", "wireless_field_link"),
    ("U07", "oil_transfer_pump", "oil_tank_battery_and_handoff", "rotating_equipment_edge", "wired_field_bus"),
    ("U07", "sales_gathering_meter", "oil_tank_battery_and_handoff", "handoff_edge", "wired_field_bus"),
    ("U07", "gas_gathering_meter", "oil_tank_battery_and_handoff", "handoff_edge", "wired_field_bus"),
    ("U08", "chemical_injection_skid", "shared_field_utilities_and_ot", "process_edge", "wireless_field_link"),
    ("U08", "restriction_context_monitor", "shared_field_utilities_and_ot", "diagnostic_edge", "wireless_field_link"),
    ("U09", "field_generator", "shared_field_utilities_and_ot", "electrical_edge", "wired_field_bus"),
    ("U09", "fuel_gas_header", "shared_field_utilities_and_ot", "process_edge", "wired_field_bus"),
    ("U09", "instrument_air_header", "shared_field_utilities_and_ot", "process_edge", "wired_field_bus"),
    ("U10", "flare_vent_router", "shared_field_utilities_and_ot", "process_edge", "wired_field_bus"),
    ("U10", "gas_detection_array", "shared_field_utilities_and_ot", "environmental_edge", "wireless_field_link"),
    ("U10", "weather_station", "shared_field_utilities_and_ot", "environmental_edge", "wireless_field_link"),
    ("U11", "well_test_separator", "central_production_facility", "process_edge", "wired_field_bus"),
    ("U11", "sample_and_lab_context", "shared_field_utilities_and_ot", "workflow_edge", "ot_gateway"),
    ("U11", "maintenance_registry", "shared_field_utilities_and_ot", "workflow_edge", "ot_gateway"),
    ("U12", "field_rtu_gateway", "shared_field_utilities_and_ot", "ot_gateway", "ot_gateway"),
    ("U12", "historian_node", "shared_field_utilities_and_ot", "historian", "ot_gateway"),
    ("U12", "scada_clock_source", "shared_field_utilities_and_ot", "clock_source", "ot_gateway"),
)

HANDOFF_INTERFACES = (
    ("handoff-oil-gathering", "oil_gathering"),
    ("handoff-gas-gathering", "gas_gathering"),
    ("handoff-water-disposal-interface", "water_disposal_interface"),
    ("handoff-water-truck-out", "water_truck_out"),
)


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _hash_unit(*parts: str) -> float:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def _common(site_id: str, area_id: str, fidelity: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "site_id": site_id,
        "area_id": area_id,
        "area_name": next(area["display_name"] for area in AREA_DEFINITIONS if area["area_id"] == area_id),
        "fidelity": fidelity,
        "is_synthetic": True,
        "operational_use": "prohibited",
    }


def plan_pads(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Deterministically expand the fleet configuration into fictional pads."""
    site_id = config["site"]["site_id"]
    fleet = config["fleet"]
    pads: list[dict[str, Any]] = []
    for index in range(fleet["oil_well_pads"]):
        pads.append({"kind": "oil", "archetype": "onshore_oil_well_pad", "well_count": 1, "ordinal": index + 1})
    for index in range(fleet["gas_well_pads"]):
        pads.append({"kind": "gas", "archetype": "onshore_gas_well_pad", "well_count": 1, "ordinal": index + 1})
    for index in range(fleet["multiwell_pads"]):
        pads.append(
            {
                "kind": "mixed",
                "archetype": "multiwell_mixed_lift_pad",
                "well_count": fleet["wells_per_multiwell_pad"],
                "ordinal": index + 1,
            }
        )
    for position, pad in enumerate(pads, 1):
        pad["pad_index"] = position
        pad["pad_id"] = _stable_id("pad", site_id, pad["kind"], str(pad["ordinal"]))
        pad["display_name"] = f"Fictional {pad['kind']} pad {pad['ordinal']}"
    return pads


def _well_records(config: dict[str, Any], pads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    site_id = config["site"]["site_id"]
    wells: list[dict[str, Any]] = []
    well_position = 0
    for pad in pads:
        for slot in range(1, pad["well_count"] + 1):
            well_position += 1
            well_id = _stable_id("well", site_id, pad["pad_id"], str(slot))
            if pad["kind"] == "oil":
                lift_type = "rod_pump"
                well_kind = "oil"
            elif pad["kind"] == "gas":
                lift_type = "plunger_lift"
                well_kind = "gas"
            else:
                lift_type = MIXED_PAD_LIFT_CYCLE[(slot - 1) % len(MIXED_PAD_LIFT_CYCLE)]
                well_kind = "oil" if slot % 2 == 1 else "gas"
            deliverability = 0.55 + 0.40 * _hash_unit(well_id, "deliverability")
            water_cut = (0.28 if well_kind == "oil" else 0.12) + 0.18 * _hash_unit(well_id, "water_cut")
            gas_ratio = (0.35 if well_kind == "oil" else 1.65) + 0.35 * _hash_unit(well_id, "gas_ratio")
            wells.append(
                {
                    **_common(site_id, "U01", "F0"),
                    "well_id": well_id,
                    "well_index": well_position,
                    "pad_id": pad["pad_id"],
                    "pad_display_name": pad["display_name"],
                    "archetype": pad["archetype"],
                    "display_name": f"Fictional well {well_position:02d}",
                    "well_kind": well_kind,
                    "lifecycle_state": "producing",
                    "lift_type": lift_type,
                    "producing_interval_label": f"fictional-producing-interval-{(well_position % 3) + 1}",
                    "completion_context_class": "fictional-generic-completion-context",
                    "completion_revision": 1,
                    "base_deliverability_index": round(deliverability, 6),
                    "nominal_water_fraction": round(water_cut, 6),
                    "nominal_gas_ratio_index": round(gas_ratio, 6),
                }
            )
    return wells


def build_catalog(config: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Build the deterministic fictional upstream catalog and topology."""
    site_id = config["site"]["site_id"]
    facility_id = config["facility"]["facility_id"]
    pads = plan_pads(config)
    wells = _well_records(config, pads)

    sites = [
        {
            **_common(site_id, "U12", "F0"),
            "display_name": config["site"]["display_name"],
            "time_zone": config["site"]["time_zone"],
            "facility_id": facility_id,
            "facility_display_name": config["facility"]["display_name"],
            "profile": "fictional_onshore_production_and_field_processing",
            "pad_count": len(pads),
            "well_count": len(wells),
            "archetypes": list(ARCHETYPES),
        }
    ]
    areas = [
        {
            **_common(site_id, area["area_id"], "F0"),
            "area_ordinal": AREA_INDEX[area["area_id"]],
            "facility_id": facility_id,
            "ownership_scope": "single_owning_area",
        }
        for area in AREA_DEFINITIONS
    ]

    assets: list[dict[str, Any]] = []
    devices: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    topology: list[dict[str, Any]] = []
    interval = config["simulation"]["interval_seconds"]

    def add_asset(
        area_id: str,
        asset_type: str,
        archetype: str,
        device_role: str,
        transport_class: str,
        display_name: str,
        well_id: str | None,
        pad_id: str | None,
        scope_key: str,
    ) -> dict[str, Any]:
        asset_id = _stable_id("asset", site_id, area_id, asset_type, scope_key)
        device_id = _stable_id("device", site_id, area_id, asset_type, scope_key)
        asset = {
            **_common(site_id, area_id, "F0"),
            "asset_id": asset_id,
            "asset_index": len(assets) + 1,
            "asset_type": asset_type,
            "generic_model": f"generic-{asset_type.replace('_', '-')}",
            "archetype": archetype,
            "display_name": display_name,
            "facility_id": facility_id,
            "well_id": well_id,
            "pad_id": pad_id,
        }
        assets.append(asset)
        devices.append(
            {
                **_common(site_id, area_id, "F0"),
                "device_id": device_id,
                "device_index": len(devices) + 1,
                "asset_id": asset_id,
                "well_id": well_id,
                "device_role": device_role,
                "transport_class": transport_class,
                "display_name": f"{display_name} edge device",
                "clock_source_class": "fictional_field_clock",
            }
        )
        for signal_index, (measurement, unit, multiple, fidelity, scope, state_key) in enumerate(
            ASSET_SIGNAL_TEMPLATES[asset_type], 1
        ):
            signals.append(
                {
                    **_common(site_id, area_id, fidelity),
                    "signal_id": _stable_id("signal", site_id, asset_id, measurement),
                    "signal_index": len(signals) + 1,
                    "asset_id": asset_id,
                    "device_id": device_id,
                    "well_id": well_id,
                    "asset_type": asset_type,
                    "measurement": measurement,
                    "unit": unit,
                    "cadence_multiple": multiple,
                    "cadence_seconds": interval * multiple,
                    "state_scope": scope,
                    "state_key": state_key,
                    "signal_ordinal": signal_index,
                }
            )
        topology.append(
            {
                **_common(site_id, area_id, "F0"),
                "edge_id": _stable_id("edge", site_id, device_id, asset_id, "observes"),
                "source_kind": "device",
                "source_id": device_id,
                "target_kind": "asset",
                "target_id": asset_id,
                "relationship": "observes",
            }
        )
        return asset

    def add_edge(area_id: str, source_id: str, target_id: str, relationship: str, target_kind: str = "asset") -> None:
        topology.append(
            {
                **_common(site_id, area_id, "F0"),
                "edge_id": _stable_id("edge", site_id, source_id, target_id, relationship),
                "source_kind": "asset",
                "source_id": source_id,
                "target_kind": target_kind,
                "target_id": target_id,
                "relationship": relationship,
            }
        )

    wells_by_pad: dict[str, list[dict[str, Any]]] = {}
    for well in wells:
        wells_by_pad.setdefault(well["pad_id"], []).append(well)

    pad_manifolds: dict[str, str] = {}
    well_assets: dict[str, dict[str, str]] = {}
    for pad in pads:
        pad_wells = wells_by_pad.get(pad["pad_id"], [])
        for well in pad_wells:
            context = add_asset(
                "U01",
                "well_context",
                pad["archetype"],
                "well_context_edge",
                "wireless_field_link",
                f"{well['display_name']} context",
                well["well_id"],
                pad["pad_id"],
                well["well_id"],
            )
            tree = add_asset(
                "U02",
                "wellhead_tree",
                pad["archetype"],
                "process_edge",
                "wired_field_bus",
                f"{well['display_name']} wellhead tree",
                well["well_id"],
                pad["pad_id"],
                well["well_id"],
            )
            lift = add_asset(
                "U03",
                "artificial_lift",
                pad["archetype"],
                "rotating_equipment_edge",
                "wired_field_bus",
                f"{well['display_name']} {well['lift_type']} lift",
                well["well_id"],
                pad["pad_id"],
                well["well_id"],
            )
            well_assets[well["well_id"]] = {
                "well_context": context["asset_id"],
                "wellhead_tree": tree["asset_id"],
                "artificial_lift": lift["asset_id"],
            }
            add_edge("U01", context["asset_id"], tree["asset_id"], "flows_to")
            add_edge("U03", lift["asset_id"], context["asset_id"], "lifts")
        manifold = add_asset(
            "U02",
            "flowline_manifold",
            pad["archetype"],
            "process_edge",
            "wired_field_bus",
            f"{pad['display_name']} flowline manifold",
            pad_wells[0]["well_id"] if pad_wells else None,
            pad["pad_id"],
            pad["pad_id"],
        )
        pad_manifolds[pad["pad_id"]] = manifold["asset_id"]
        for well in pad_wells:
            add_edge("U02", well_assets[well["well_id"]]["wellhead_tree"], manifold["asset_id"], "flows_to")

    facility_assets: dict[str, str] = {}
    for area_id, asset_type, archetype, device_role, transport_class in FACILITY_ASSET_DEFINITIONS:
        asset = add_asset(
            area_id,
            asset_type,
            archetype,
            device_role,
            transport_class,
            f"{config['facility']['display_name']} {asset_type.replace('_', ' ')}",
            None,
            None,
            facility_id,
        )
        facility_assets[asset_type] = asset["asset_id"]

    for pad in pads:
        add_edge("U02", pad_manifolds[pad["pad_id"]], facility_assets["inlet_separator"], "flows_to")
        add_edge("U11", pad_manifolds[pad["pad_id"]], facility_assets["well_test_separator"], "routes_test_to")

    process_edges = (
        ("U04", "inlet_separator", "free_water_knockout", "flows_to"),
        ("U04", "inlet_separator", "heater_treater", "flows_to"),
        ("U04", "inlet_separator", "field_compressor", "flows_to"),
        ("U04", "heater_treater", "oil_tank_battery", "flows_to"),
        ("U05", "field_compressor", "dehydration_unit", "flows_to"),
        ("U05", "dehydration_unit", "gas_gathering_meter", "flows_to"),
        ("U06", "free_water_knockout", "produced_water_tank", "flows_to"),
        ("U06", "produced_water_tank", "water_transfer_pump", "flows_to"),
        ("U06", "water_transfer_pump", "reinjection_interface", "flows_to"),
        ("U07", "oil_tank_battery", "oil_transfer_pump", "flows_to"),
        ("U07", "oil_transfer_pump", "sales_gathering_meter", "flows_to"),
        ("U08", "chemical_injection_skid", "inlet_separator", "injects_into"),
        ("U08", "restriction_context_monitor", "heater_treater", "monitors"),
        ("U09", "field_generator", "field_compressor", "powers"),
        ("U09", "field_generator", "water_transfer_pump", "powers"),
        ("U09", "field_generator", "oil_transfer_pump", "powers"),
        ("U09", "fuel_gas_header", "field_generator", "supplies"),
        ("U09", "instrument_air_header", "inlet_separator", "supplies"),
        ("U10", "inlet_separator", "flare_vent_router", "vents_to"),
        ("U10", "dehydration_unit", "flare_vent_router", "vents_to"),
        ("U10", "gas_detection_array", "flare_vent_router", "monitors"),
        ("U10", "weather_station", "gas_detection_array", "contextualizes"),
        ("U11", "sample_and_lab_context", "well_test_separator", "samples"),
        ("U11", "maintenance_registry", "field_compressor", "tracks"),
        ("U12", "field_rtu_gateway", "historian_node", "delivers_to"),
        ("U12", "scada_clock_source", "field_rtu_gateway", "synchronizes"),
    )
    for area_id, source_type, target_type, relationship in process_edges:
        add_edge(area_id, facility_assets[source_type], facility_assets[target_type], relationship)

    for lift_well_id, lift_map in sorted(well_assets.items()):
        add_edge("U09", facility_assets["field_generator"], lift_map["artificial_lift"], "powers")

    handoff_edges = (
        ("U07", "sales_gathering_meter", "handoff-oil-gathering"),
        ("U07", "gas_gathering_meter", "handoff-gas-gathering"),
        ("U06", "reinjection_interface", "handoff-water-disposal-interface"),
        ("U06", "produced_water_tank", "handoff-water-truck-out"),
    )
    for area_id, source_type, interface_id in handoff_edges:
        add_edge(area_id, facility_assets[source_type], interface_id, "hands_off_to", target_kind="handoff_interface")

    return {
        "sites": sites,
        "areas": areas,
        "wells": wells,
        "assets": assets,
        "devices": devices,
        "signals": signals,
        "topology": topology,
    }


def catalog_index(catalog: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Derive stable lookup maps used by the simulation and workflow layers."""
    assets_by_type: dict[str, list[dict[str, Any]]] = {}
    for asset in catalog["assets"]:
        assets_by_type.setdefault(asset["asset_type"], []).append(asset)
    devices_by_asset = {device["asset_id"]: device for device in catalog["devices"]}
    signals_by_asset: dict[str, list[dict[str, Any]]] = {}
    for signal in catalog["signals"]:
        signals_by_asset.setdefault(signal["asset_id"], []).append(signal)
    return {
        "assets_by_type": assets_by_type,
        "assets_by_id": {asset["asset_id"]: asset for asset in catalog["assets"]},
        "devices_by_asset": devices_by_asset,
        "signals_by_asset": signals_by_asset,
        "wells_by_id": {well["well_id"]: well for well in catalog["wells"]},
    }


def facility_asset(catalog: dict[str, list[dict[str, Any]]], asset_type: str) -> dict[str, Any]:
    for asset in catalog["assets"]:
        if asset["asset_type"] == asset_type and asset["well_id"] is None:
            return asset
    raise KeyError(f"facility asset not found: {asset_type}")


__all__ = [
    "AREA_DEFINITIONS",
    "AREA_IDS",
    "AREA_INDEX",
    "ARCHETYPES",
    "ASSET_SIGNAL_TEMPLATES",
    "CATALOG_RECORD_TYPES",
    "HANDOFF_INTERFACES",
    "LIFT_TYPES",
    "LOCAL_ONLY_RECORD_TYPES",
    "ADLS_PUBLISHABLE_LOCAL_ONLY_RECORD_TYPES",
    "PUBLISHED_RECORD_TYPES",
    "PRIVATE_RECORD_TYPES",
    "PUBLIC_RECORD_TYPES",
    "RECORD_FAMILY_OWNER",
    "RECORD_TYPES",
    "TELEMETRY_RECORD_TYPES",
    "WORKFLOW_RECORD_TYPES",
    "build_catalog",
    "catalog_index",
    "facility_asset",
    "plan_pads",
]
