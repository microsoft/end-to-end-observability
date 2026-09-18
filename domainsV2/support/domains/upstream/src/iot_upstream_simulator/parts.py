"""Deterministic fictional parts, storeroom, candidate and dispatch layer.

Phases 1-8 of the parts design: a replaceable-part catalog, fitment, storeroom
locations with travel context, ranked alert-to-part candidates, four explicit
dispositions, an append-only stock ledger with derived balances, a demand record
separate from fulfilment, and material-staging dispatch context.

Nothing here diagnoses, predicts, or recommends. A candidate is a fact about the
mapping rule that fired, never a claim about the physical world: no probability
or confidence is ever published. Part classes, lead bands, quantities, travel
times and thresholds are documented synthetic heuristics, not measured values,
and no standards conformance is claimed. `fit_spec_code` is fictional and must
never be read as a real interchange claim.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, NamedTuple

from .common import clamp, envelope, hash_float, round6, stable_id, stamp

#: Catalog-shaped parts families. These carry no time/quality envelope, matching
#: the existing `assets` / `devices` / `signals` structural convention.
PARTS_CATALOG_RECORD_TYPES = (
    "part_master",
    "part_applicability",
    "storeroom_location",
    "travel_matrix",
)
#: Transactional parts families. These carry the full shared envelope, matching
#: `maintenance_context`.
PARTS_WORKFLOW_RECORD_TYPES = (
    "part_candidate",
    "part_stock_txn",
    "part_demand",
    "dispatch_context",
)
PARTS_RECORD_TYPES = (*PARTS_CATALOG_RECORD_TYPES, *PARTS_WORKFLOW_RECORD_TYPES)

#: Every parts family is owned by U11, the well-test, sample, laboratory,
#: maintenance and inspection area.
PARTS_OWNING_AREA = "U11"

#: Version of the alert-to-part mapping rule set. Published on every candidate so
#: a consumer can tell which rules produced a ranking.
MAPPING_RULE_VERSION = "parts-mapping-1.0"

#: Documented synthesis, not a standard. The repairable/consumable split, the
#: beyond-economic-repair threshold and level-of-repair analysis are real named
#: concepts, but no source presents these four values as one canonical taxonomy.
PART_CLASSES = ("C", "R", "RO", "CAP")
LEAD_BANDS = ("COM", "ENG", "LLT")
TRACKING_MODES = ("serialized", "lot", "bulk")
UNITS_OF_MEASURE = ("each", "m", "L", "kg", "roll", "set")
CRITICALITY_CLASSES = ("V", "E", "D")
MOVEMENT_CLASSES = ("F", "S", "N")
ABC_CLASSES = ("A", "B", "C")

#: Storage-class compatibility is driven by tracking mode and unit of measure.
STORAGE_CLASSES = ("open_shelf", "binned", "secured_rack", "controlled_liquid_store")

#: Asset types the parts layer will stage material for. Deliberately narrower
#: than the full asset catalog: an applicability row that cannot be dispatched
#: teaches nothing.
DISPATCHABLE_ASSET_TYPES = (
    "artificial_lift",
    "field_compressor",
    "heater_treater",
    "inlet_separator",
    "oil_transfer_pump",
    "water_transfer_pump",
)

#: Coarse equipment grouping used only for `class_only` rule matches.
ASSET_GROUPS = {
    "artificial_lift": "lift_equipment",
    "field_compressor": "rotating_equipment",
    "oil_transfer_pump": "rotating_equipment",
    "water_transfer_pump": "rotating_equipment",
    "heater_treater": "thermal_process_equipment",
    "inlet_separator": "static_process_equipment",
}

#: (key, name, scope, class, lead band, tracking, uom, criticality, movement,
#:  abc, insurance spare, synthetic nominal lead days)
_PART_ROWS: tuple[tuple[Any, ...], ...] = (
    ("fastener_set", "Generic fastener and mechanical smalls set", "shared", "C", "COM", "bulk", "set", "D", "F", "C", False, 7),
    ("static_seal_gasket", "Generic static seal and gasket", "shared", "C", "COM", "lot", "each", "E", "F", "C", False, 10),
    ("o_ring_kit", "Generic elastomer o-ring kit", "shared", "C", "COM", "lot", "set", "E", "F", "C", False, 7),
    ("rolling_bearing", "Generic rolling-element bearing", "shared", "R", "COM", "lot", "each", "E", "S", "B", False, 21),
    ("lubricant_charge", "Generic lubricant charge", "shared", "C", "COM", "lot", "L", "D", "F", "C", False, 5),
    ("filter_element", "Generic filter element", "shared", "C", "COM", "lot", "each", "E", "F", "B", False, 14),
    ("instrument_cable", "Generic instrument multipair cable", "shared", "C", "COM", "bulk", "m", "D", "S", "C", False, 10),
    ("cable_gland_kit", "Generic termination and gland kit", "shared", "C", "COM", "bulk", "each", "D", "F", "C", False, 7),
    ("control_relay", "Generic control relay", "shared", "R", "COM", "lot", "each", "E", "S", "B", False, 14),
    ("power_supply_module", "Generic control power-supply module", "shared", "RO", "ENG", "serialized", "each", "V", "S", "A", False, 45),
    ("pressure_transmitter", "Generic pressure transmitter", "shared", "RO", "ENG", "serialized", "each", "V", "S", "A", False, 35),
    ("temperature_sensor_probe", "Generic temperature sensor probe", "shared", "C", "COM", "lot", "each", "E", "S", "B", False, 14),
    ("thermowell", "Generic thermowell", "shared", "R", "ENG", "serialized", "each", "E", "N", "B", False, 60),
    ("solenoid_valve", "Generic solenoid valve", "shared", "R", "COM", "serialized", "each", "E", "S", "B", False, 21),
    ("mechanical_seal_cartridge", "Generic mechanical seal cartridge", "shared", "R", "ENG", "lot", "each", "V", "S", "A", False, 60),
    ("shaft_coupling_kit", "Generic shaft coupling kit", "shared", "C", "ENG", "lot", "set", "E", "S", "B", False, 30),
    ("surge_protection_module", "Generic surge protection module", "shared", "C", "COM", "lot", "each", "E", "N", "C", False, 14),
    ("telemetry_radio_module", "Generic telemetry radio module", "shared", "RO", "ENG", "serialized", "each", "V", "N", "A", False, 55),
    ("rod_pump_valve_kit", "Generic rod-pump valve kit", "upstream", "C", "ENG", "lot", "set", "V", "S", "A", False, 40),
    ("sucker_rod_section", "Generic sucker-rod string section", "upstream", "R", "LLT", "serialized", "each", "V", "N", "A", False, 180),
    ("esp_downhole_motor", "Generic electric submersible downhole motor", "upstream", "CAP", "LLT", "serialized", "each", "V", "N", "A", True, 240),
    ("gas_lift_valve_set", "Generic gas-lift valve set", "upstream", "C", "ENG", "lot", "set", "V", "S", "A", False, 45),
    ("plunger_and_seal_set", "Generic plunger and seal set", "upstream", "C", "COM", "lot", "set", "E", "F", "B", False, 12),
    ("chemical_metering_diaphragm", "Generic chemical metering diaphragm", "upstream", "C", "ENG", "lot", "each", "E", "S", "B", False, 25),
    ("compressor_valve_assembly", "Generic compressor valve assembly", "upstream", "R", "ENG", "serialized", "each", "V", "S", "A", False, 75),
    ("heater_firetube_assembly", "Generic heater firetube assembly", "upstream", "CAP", "LLT", "serialized", "each", "V", "N", "A", True, 210),
)

#: Substitution and supersession are one-way where a source part is superseded.
_SUPERSESSION = {"control_relay": "surge_protection_module"}
_SUBSTITUTES = {
    "static_seal_gasket": ("o_ring_kit",),
    "o_ring_kit": ("static_seal_gasket",),
}
_KIT_PARTS = frozenset({"fastener_set", "o_ring_kit", "rod_pump_valve_kit", "gas_lift_valve_set", "shaft_coupling_kit", "plunger_and_seal_set"})
#: Parts that must be replaced together with the primary part.
_COMPANIONS = {
    "mechanical_seal_cartridge": ("o_ring_kit", "fastener_set"),
    "compressor_valve_assembly": ("static_seal_gasket", "fastener_set"),
    "heater_firetube_assembly": ("static_seal_gasket",),
    "esp_downhole_motor": ("o_ring_kit",),
    "rod_pump_valve_kit": ("o_ring_kit",),
}

#: Which asset configurations each part is applicable to. The configuration, not
#: the asset class, is the fitment test (design section 5.6).
_APPLICABILITY: dict[str, tuple[str, ...]] = {
    "fastener_set": ("artificial_lift:rod_pump", "field_compressor:standard", "oil_transfer_pump:standard", "water_transfer_pump:standard"),
    "static_seal_gasket": ("field_compressor:standard", "heater_treater:standard", "inlet_separator:standard"),
    "o_ring_kit": ("artificial_lift:gas_lift", "artificial_lift:esp", "oil_transfer_pump:standard", "artificial_lift:rod_pump"),
    "rolling_bearing": ("field_compressor:standard", "oil_transfer_pump:standard", "water_transfer_pump:standard"),
    "lubricant_charge": ("field_compressor:standard", "oil_transfer_pump:standard"),
    "filter_element": ("field_compressor:standard", "inlet_separator:standard"),
    "instrument_cable": ("inlet_separator:standard", "heater_treater:standard"),
    "cable_gland_kit": ("inlet_separator:standard",),
    "control_relay": ("field_compressor:standard", "water_transfer_pump:standard"),
    "power_supply_module": ("artificial_lift:esp", "field_compressor:standard"),
    "pressure_transmitter": ("inlet_separator:standard", "heater_treater:standard"),
    "temperature_sensor_probe": ("heater_treater:standard",),
    "thermowell": ("heater_treater:standard",),
    "solenoid_valve": ("inlet_separator:standard", "water_transfer_pump:standard"),
    "mechanical_seal_cartridge": ("oil_transfer_pump:standard", "water_transfer_pump:standard"),
    "shaft_coupling_kit": ("oil_transfer_pump:standard", "water_transfer_pump:standard"),
    "surge_protection_module": ("field_compressor:standard",),
    "telemetry_radio_module": ("inlet_separator:standard",),
    "rod_pump_valve_kit": ("artificial_lift:rod_pump",),
    "sucker_rod_section": ("artificial_lift:rod_pump",),
    "esp_downhole_motor": ("artificial_lift:esp",),
    "gas_lift_valve_set": ("artificial_lift:gas_lift",),
    "plunger_and_seal_set": ("artificial_lift:plunger_lift", "artificial_lift:natural_flow"),
    "chemical_metering_diaphragm": ("inlet_separator:standard",),
    "compressor_valve_assembly": ("field_compressor:standard",),
    "heater_firetube_assembly": ("heater_treater:standard",),
}

#: Identifier for the fictional planar coordinate frame every location sits in.
#:
#: This is deliberately **not** a geodetic reference system. There is no datum,
#: no projection, and no correspondence to any point on Earth. Positions are
#: plane offsets in kilometres from a fictional origin, which is the fictional
#: operating field itself. The frame exists so that distance can be *computed
#: from geometry* rather than asserted, which is what lets travel time carry
#: real information.
#:
#: Geodetic latitude and longitude are prohibited for this generator (see the
#: prohibited-claims section of `docs/upstream-simulator.md`), and nothing here
#: is a substitute spelling for them: a plane offset from a fictional origin
#: cannot be plotted on a real map or joined to a real well record. The spatial
#: *statistics* are calibrated to published rural-logistics figures; the
#: *places* are invented.
GRID_FRAME = "fictional-planar-grid-v1"

#: Road-class speeds in km/h, as documented synthetic heuristics.
#:
#: Calibrated to published Texas road data: unpaved oilfield lease roads run
#: 16-32 km/h, county paved roads 80-97, US/state/farm-to-market 97-121, and
#: rural interstates 113-129. The single most important property is that these
#: differ by roughly a factor of five, so effective speed rises with trip
#: length. A model with one speed cannot express that, and it is the reason the
#: previous flat derivation carried no information.
ROAD_CLASS_SPEEDS_KMH: dict[str, float] = {
    "lease_road": 25.0,
    "county_road": 85.0,
    "arterial": 105.0,
    "interstate": 120.0,
}
#: How far a trip runs on each class before promoting to the next, in km. A
#: journey always starts on the lease road, then county roads, then arterials,
#: and only long hauls reach an interstate.
ROAD_CLASS_BANDS_KM: tuple[tuple[str, float], ...] = (
    ("county_road", 25.0),
    ("arterial", 95.0),
)
#: Road distance exceeds straight-line distance. Ballou, Rahimi & Srivastava
#: (2002) report circuity factors of 1.20-1.25 for US rural road networks. The
#: spread is sampled deterministically per origin-destination pair to stand in
#: for route quality, which is why no separate arbitrary time jitter is needed.
CIRCUITY_RANGE: tuple[float, float] = (1.15, 1.30)
#: Unpaved lease road between the public road and the asset, in km. Published
#: figures put typical lease roads at 0.15-0.6 km, reaching several km in
#: remote settings. This is the only per-asset term, and it is what makes
#: travel differ between assets on the same field.
LEASE_ROAD_KM_RANGE: tuple[float, float] = (0.15, 3.0)


class LocationSpec(NamedTuple):
    """One storeroom location in the fictional network.

    ``east_km`` and ``north_km`` are plane offsets from the fictional field at
    the origin. Distances between tiers are calibrated to published oilfield
    logistics figures: field yards serve a 48-80 km primary radius, regional
    warehouses 160-320 km, and a central hub 320-800 km.
    """

    code: str
    location_type: str
    parent_code: str | None
    storage_classes: tuple[str, ...]
    id_tech: str
    velocity_zone: str
    latency_class: str
    #: Handling latency: pick, pack and readiness at this tier, *excluding*
    #: travel. Travel is computed from geometry and added on top.
    handling_minutes: float
    #: Nominal tier service radius, for a consumer deciding what a location is
    #: *meant* to cover. It does not gate dispatch.
    service_radius_km: float
    east_km: float
    north_km: float
    zone: str | None
    aisle: str | None
    rack: str | None
    shelf: str | None
    bin_code: str | None


def _loc(
    code: str,
    location_type: str,
    parent_code: str | None,
    storage_classes: tuple[str, ...],
    id_tech: str,
    velocity_zone: str,
    latency_class: str,
    handling_minutes: float,
    service_radius_km: float,
    east_km: float,
    north_km: float,
    address: tuple[str | None, ...] = (None, None, None, None, None),
) -> LocationSpec:
    zone, aisle, rack, shelf, bin_code = address
    return LocationSpec(
        code, location_type, parent_code, storage_classes, id_tech, velocity_zone,
        latency_class, handling_minutes, service_radius_km, east_km, north_km,
        zone, aisle, rack, shelf, bin_code,
    )


#: The storeroom network. Location *type* still dominates handling latency, but
#: position now dominates travel, so the two are separated rather than folded
#: into one number.
#:
#: Tier separations follow published oilfield logistics practice: vans work
#: within a few km of the assets they serve, field storerooms sit inside a
#: 48-80 km primary radius, regional warehouses 160-320 km out, and a single
#: central hub several hundred km away. Consignment stock sits on the field
#: itself. Supplier-held stock is procurement rather than travel, so it is
#: placed far off-network and carries no travel row at all.
_LOCATION_ROWS: tuple[LocationSpec, ...] = (
    # Central distribution hub.
    _loc("CW1", "central_warehouse", None,
         ("open_shelf", "binned", "secured_rack", "controlled_liquid_store"),
         "rfid", "slow", "next_day", 1440.0, 800.0, 430.0, 215.0,
         ("Z1", "A01", "R03", "S02", "B04")),
    # Regional warehouses.
    _loc("RW1", "regional_warehouse", "CW1",
         ("open_shelf", "binned", "secured_rack"),
         "barcode", "medium", "hours", 240.0, 320.0, 120.0, -82.0,
         ("Z1", "A02", "R01", "S01", "B02")),
    _loc("RW2", "regional_warehouse", "CW1",
         ("open_shelf", "binned", "secured_rack", "controlled_liquid_store"),
         "barcode", "medium", "hours", 240.0, 320.0, -165.0, 122.0,
         ("Z1", "A01", "R02", "S01", "B03")),
    _loc("RW3", "regional_warehouse", "CW1",
         ("open_shelf", "binned", "secured_rack"),
         "rfid", "medium", "hours", 300.0, 320.0, 60.0, 255.0,
         ("Z2", "A03", "R01", "S02", "B01")),
    # Field storerooms.
    _loc("SS1", "site_storeroom", "RW1",
         ("open_shelf", "binned", "controlled_liquid_store"),
         "qr", "fast", "minutes", 25.0, 80.0, 12.0, 8.0,
         ("Z2", "A01", "R02", "S03", "B01")),
    _loc("SS2", "site_storeroom", "RW2",
         ("open_shelf", "binned", "secured_rack"),
         "qr", "fast", "minutes", 30.0, 80.0, -26.0, 14.0,
         ("Z2", "A02", "R01", "S01", "B02")),
    _loc("SS3", "site_storeroom", "RW1",
         ("open_shelf", "binned"),
         "barcode", "medium", "minutes", 35.0, 80.0, 33.0, -22.0,
         ("Z3", "A01", "R01", "S02", "B01")),
    _loc("SS4", "site_storeroom", "RW2",
         ("open_shelf", "binned", "controlled_liquid_store"),
         "qr", "medium", "minutes", 40.0, 80.0, -9.0, -46.0,
         ("Z3", "A02", "R02", "S01", "B03")),
    _loc("SS5", "site_storeroom", "RW3",
         ("open_shelf", "binned", "secured_rack"),
         "barcode", "slow", "hours", 55.0, 80.0, 44.0, 38.0,
         ("Z4", "A01", "R03", "S02", "B02")),
    # Field van stock. Mobile, so no shelf address.
    _loc("VAN1", "field_van_stock", "SS1", ("open_shelf", "binned"),
         "none", "fast", "immediate", 5.0, 50.0, 2.0, 1.5),
    _loc("VAN2", "field_van_stock", "SS2", ("open_shelf", "binned"),
         "none", "fast", "immediate", 5.0, 50.0, -4.0, 3.0),
    _loc("VAN3", "field_van_stock", "SS3", ("open_shelf", "binned"),
         "none", "fast", "immediate", 6.0, 50.0, 6.0, -5.0),
    _loc("VAN4", "field_van_stock", "SS4", ("open_shelf", "binned"),
         "none", "fast", "immediate", 6.0, 50.0, -7.0, -7.0),
    _loc("VAN5", "field_van_stock", "SS5", ("open_shelf", "binned"),
         "none", "fast", "immediate", 7.0, 50.0, 9.0, 6.0),
    # Vendor-managed stock held on the field itself.
    _loc("CON1", "consignment", "SS1", ("binned", "secured_rack"),
         "barcode", "slow", "minutes", 45.0, 25.0, 1.0, -0.8,
         ("Z3", "A01", "R01", "S01", "B01")),
    _loc("CON2", "consignment", "SS5", ("binned", "secured_rack", "controlled_liquid_store"),
         "barcode", "slow", "minutes", 50.0, 25.0, -1.5, 1.2,
         ("Z3", "A02", "R01", "S01", "B02")),
    # Supplier-held. Procurement lead time, not travel.
    _loc("SUP1", "supplier_held", None, ("secured_rack",),
         "none", "non_stocked", "procurement_lead_time", 100800.0, 0.0, 700.0, -565.0),
)
LOCATION_TYPES = tuple(row.location_type for row in _LOCATION_ROWS)
#: Supplier-held stock is reached through procurement, never by driving.
NON_TRAVEL_LOCATION_TYPES = frozenset({"supplier_held"})

#: Deliberately not stocked anywhere with available quantity, so that censored
#: demand is observable (design section 8.3). It has to be a part the reactive
#: rules actually request under a disposition that reaches the storeroom, so it
#: cannot be a CAP/RO/LLT item: those force `diagnose_first`, which never
#: requests material.
STOCKOUT_PART_KEY = "temperature_sensor_probe"
#: Deliberately mis-stocked on the van: the unit carried on the van was fitted
#: A van has one shelf per part, so it carries units fitted for a single asset
#: configuration: the first applicable configuration in sorted order. For this
#: part that is `VAN_MISSTOCK_CONFIG`, so the van unit fits compressors and is
#: rejected on every other rotating asset the same rule offers it to. This is not
#: special-cased in the dispatch logic — it falls out of how stock records what it
#: was put away for. A dataset in which every offer fits teaches a false lesson.
VAN_MISSTOCK_PART_KEY = "rolling_bearing"
VAN_MISSTOCK_CONFIG = "field_compressor:standard"

DISPOSITIONS = ("predetermined_action", "candidate_kit", "diagnose_first", "unresolved")
RULE_MATCH_STRENGTHS = ("exact", "partial", "class_only")
COVERAGE_STATES = ("single_mapping", "multi_mapping", "unmapped")
EVIDENCE_BASES = ("fmeca", "rcm_task", "history", "inferred")
DISPATCH_OUTCOMES = ("resolved_first_visit", "wrong_part", "second_trip_required", "returned_unused")
MATERIAL_STATES = (
    "created",
    "parts_reserved",
    "parts_picked",
    "parts_staged",
    "issued",
    "partially_fulfilled",
    "unfulfilled",
    "cancelled",
)

#: Confirmed ISO 14224 vocabulary only: failure mode, failure mechanism and
#: failure cause. The ordered hierarchy level names could not be verified from
#: open primary sources, so the vendor-neutral `sub_asset_code` is used instead
#: of any standard-specific level name.
#: key -> (sub_asset_code, mode, mechanism, cause, part keys, evidence basis)
_CONFIG_RULES: dict[tuple[str, str, str], tuple[Any, ...]] = {
    ("artificial_lift", "lift_run_state_change", "rod_pump"): (
        "rod_lift_downhole_assembly", "loss_of_output", "mechanical_wear", "operating_influence",
        ("rod_pump_valve_kit", "sucker_rod_section"), "fmeca",
    ),
    ("artificial_lift", "lift_run_state_change", "esp"): (
        "esp_downhole_assembly", "loss_of_output", "electrical_insulation_degradation", "operating_influence",
        ("esp_downhole_motor", "power_supply_module"), "fmeca",
    ),
    ("artificial_lift", "lift_run_state_change", "gas_lift"): (
        "gas_lift_injection_assembly", "erroneous_output", "erosion", "operating_influence",
        ("gas_lift_valve_set", "o_ring_kit"), "rcm_task",
    ),
    ("artificial_lift", "lift_run_state_change", "plunger_lift"): (
        "plunger_travel_assembly", "intermittent_output", "mechanical_wear", "operating_influence",
        ("plunger_and_seal_set",), "rcm_task",
    ),
}
#: (asset_type, event_type) -> exact rule for asset types with one configuration.
_EVENT_RULES: dict[tuple[str, str], tuple[Any, ...]] = {
    ("field_compressor", "compressor_mode_change"): (
        "compressor_gas_path", "loss_of_output", "fouling", "maintenance_influence",
        ("filter_element", "lubricant_charge"), "rcm_task",
    ),
    ("oil_transfer_pump", "oil_transfer_state_change"): (
        "pump_sealing_assembly", "external_leakage", "mechanical_wear", "maintenance_influence",
        ("mechanical_seal_cartridge", "o_ring_kit"), "rcm_task",
    ),
    ("water_transfer_pump", "water_transfer_state_change"): (
        "pump_sealing_assembly", "intermittent_output", "mechanical_wear", "operating_influence",
        ("mechanical_seal_cartridge", "shaft_coupling_kit"), "fmeca",
    ),
}
#: (asset_type,) -> weaker rule used when the event type is unmapped for that asset.
_ASSET_RULES: dict[str, tuple[Any, ...]] = {
    "artificial_lift": (
        "lift_surface_assembly", "intermittent_output", "mechanical_wear", "unknown",
        ("fastener_set", "o_ring_kit"), "inferred",
    ),
}
#: asset group -> weakest rule. A condition summary is a weaker observation than
#: an alarm, so it can only ever match at group level.
_GROUP_RULES: dict[str, tuple[Any, ...]] = {
    "rotating_equipment": (
        "rotating_assembly", "degraded_output", "mechanical_wear", "unknown",
        ("rolling_bearing", "lubricant_charge"), "inferred",
    ),
    "thermal_process_equipment": (
        "thermal_transfer_assembly", "degraded_output", "fouling", "unknown",
        ("temperature_sensor_probe", "static_seal_gasket"), "inferred",
    ),
    "static_process_equipment": (
        "separation_internals", "degraded_output", "fouling", "unknown",
        ("filter_element", "static_seal_gasket"), "inferred",
    ),
    "lift_equipment": (
        "lift_surface_assembly", "degraded_output", "mechanical_wear", "unknown",
        ("fastener_set",), "inferred",
    ),
}

#: Bounds that keep the parts layer sparse and the committed sample small.
MAX_TRIGGERS = 8
#: Strongest evidence first. Also the deterministic backfill order.
TRIGGER_SOURCE_ORDER = ("alarm_journal", "events", "condition_monitoring")
TRIGGER_SOURCE_QUOTA = {"alarm_journal": 3, "events": 3, "condition_monitoring": 2}
MAX_CANDIDATES_PER_TRIGGER = 3
CONDITION_TRIGGER_THRESHOLD = 0.75

#: Legal stock transitions. Physical states are separated from pipeline
#: accounting: `on_order` and `in_transit` are owned but not present and are
#: never counted in on-hand.
PRESENT_STATES = ("available", "reserved", "quarantined", "in_repair")
PIPELINE_STATES = ("on_order", "in_transit")
EXIT_STATES = ("issued", "scrapped")
STOCK_STATES = (*PRESENT_STATES, *PIPELINE_STATES, *EXIT_STATES)
#: transaction type -> (from_state, to_state, creates, removes)
TRANSACTION_TYPES: dict[str, tuple[str | None, str | None, bool, bool]] = {
    "opening_balance": (None, "available", True, False),
    "order_placed": (None, "on_order", True, False),
    "shipped": ("on_order", "in_transit", False, False),
    "received": ("in_transit", "quarantined", False, False),
    "inspection_passed": ("quarantined", "available", False, False),
    "reserved": ("available", "reserved", False, False),
    "reservation_released": ("reserved", "available", False, False),
    "issued": ("reserved", "issued", False, True),
    "returned_unused": ("issued", "quarantined", False, False),
    "repair_started": ("available", "in_repair", False, False),
    "repair_completed": ("in_repair", "available", False, False),
    "scrapped": ("quarantined", "scrapped", False, True),
}


def rank_source_options(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order candidate sources by how soon each can reach the asset.

    Location type is deliberately *not* part of the key. An earlier version
    sorted ``field_van_stock`` first unconditionally, which was indis-
    tinguishable from ranking by time while there was a single van that was
    always nearest. Across a multi-territory network it is simply wrong: a van
    200 km away would outrank a storeroom 10 km away.

    Ties break on ``location_id`` so the ordering is total and deterministic.
    """
    return sorted(
        options,
        key=lambda item: (item["total_minutes"], item["location"]["location_id"]),
    )


class PartsLedgerError(RuntimeError):
    """Raised when the stock ledger violates a conservation invariant."""


def plane_distance_km(
    east_a: float, north_a: float, east_b: float = 0.0, north_b: float = 0.0
) -> float:
    """Straight-line distance in the fictional plane, in km.

    Euclidean rather than great-circle on purpose. The frame is a plane with a
    fictional origin, not a sphere with a datum, so a haversine here would
    imply a geodetic meaning the data does not have.
    """
    return math.hypot(east_a - east_b, north_a - north_b)


def road_segments_km(road_km: float, lease_km: float) -> list[tuple[str, float]]:
    """Apportion a road distance across road classes, nearest class first.

    Every trip begins on the unpaved lease road, then county roads, then
    arterials, and only a long haul reaches an interstate. Returned in travel
    order so the segmentation can be read directly.
    """
    remaining = max(road_km, 0.0)
    lease = min(max(lease_km, 0.0), remaining)
    segments: list[tuple[str, float]] = []
    if lease > 0.0:
        segments.append(("lease_road", lease))
    remaining -= lease
    for road_class, band_km in ROAD_CLASS_BANDS_KM:
        if remaining <= 0.0:
            break
        leg = min(remaining, band_km)
        segments.append((road_class, leg))
        remaining -= leg
    if remaining > 0.0:
        segments.append(("interstate", remaining))
    return segments


def travel_minutes_for(road_km: float, lease_km: float) -> tuple[float, str]:
    """Minutes to drive ``road_km``, and the class contributing the most time.

    Time is summed over road-class segments rather than divided by one speed.
    That is the whole point: effective speed then rises with trip length,
    because only long trips reach the fast classes.
    """
    segments = road_segments_km(road_km, lease_km)
    minutes = 0.0
    by_class: dict[str, float] = {}
    for road_class, leg_km in segments:
        leg_minutes = 60.0 * leg_km / ROAD_CLASS_SPEEDS_KMH[road_class]
        minutes += leg_minutes
        by_class[road_class] = by_class.get(road_class, 0.0) + leg_minutes
    if not by_class:
        return 0.0, "none"
    # Ties resolve by class name so the result is order-independent.
    dominant = max(sorted(by_class), key=lambda name: by_class[name])
    return minutes, dominant


def storage_class_for(part: dict[str, Any]) -> str:
    """Storage class required by a part, derived from tracking mode and unit."""
    if part["uom"] in {"L", "kg"}:
        return "controlled_liquid_store"
    if part["tracking_mode"] == "serialized":
        return "secured_rack"
    if part["tracking_mode"] == "lot":
        return "binned"
    return "open_shelf"


def fit_spec_code(part_key: str, asset_config_code: str) -> str:
    """Fictional fit/form/function key. Never a real interchange claim.

    The key binds a part to a specific asset *configuration*. Matching on asset
    class alone can never produce this value, which is what makes the acceptance
    rule in `fit_is_verified` meaningful.
    """
    return stable_id("fit", part_key, asset_config_code)


def fit_is_verified(part_key: str, asset_config_code: str, offered_fit_spec_code: str) -> bool:
    """A stock unit may only be issued if it passes the configuration fit test."""
    return offered_fit_spec_code == fit_spec_code(part_key, asset_config_code)


def stocked_fit_is_verified(
    part_key: str, asset_config_code: str, stocked_fit_spec_codes: tuple[str, ...]
) -> bool:
    """Does any unit held at this location fit this asset configuration?

    The stocked codes are recorded when the stock is created, from the
    configuration each unit was actually put away for. They are never rebuilt
    from the demand, which is what stops the fit test from being a tautology: a
    location can hold plenty of a part and still hold none that fits.
    """
    return fit_spec_code(part_key, asset_config_code) in stocked_fit_spec_codes


def fold_ledger(transactions: list[dict[str, Any]]) -> dict[tuple[str, str, str], int]:
    """Fold the append-only ledger into derived balances.

    Balances are always derived, never stored and incremented. The key is
    (part_master_id, location_id, state).
    """
    balances: dict[tuple[str, str, str], int] = {}
    for txn in transactions:
        part_id = txn["part_master_id"]
        location_id = txn["location_id"]
        quantity = txn["quantity"]
        from_state = txn["from_state"]
        to_state = txn["to_state"]
        if from_state is not None:
            key = (part_id, location_id, from_state)
            balances[key] = balances.get(key, 0) - quantity
        if to_state is not None:
            key = (part_id, location_id, to_state)
            balances[key] = balances.get(key, 0) + quantity
    return balances


def derived_on_hand(balances: dict[tuple[str, str, str], int], part_id: str, location_id: str) -> int:
    """On-hand includes in-repair and quarantined stock, never pipeline stock."""
    return sum(balances.get((part_id, location_id, state), 0) for state in PRESENT_STATES)


def derived_available(balances: dict[tuple[str, str, str], int], part_id: str, location_id: str) -> int:
    return balances.get((part_id, location_id, "available"), 0)


def validate_ledger(transactions: list[dict[str, Any]]) -> None:
    """Enforce every conservation invariant across the full ledger fold.

    Byte-identical output that fails to conserve quantity is still wrong, so
    these invariants — not the transition names — are the real acceptance
    criteria.
    """
    running: dict[tuple[str, str, str], int] = {}
    serial_positions: dict[str, tuple[str, str]] = {}
    for index, txn in enumerate(transactions):
        transaction_type = txn["transaction_type"]
        if transaction_type not in TRANSACTION_TYPES:
            raise PartsLedgerError(f"unknown transaction type at {index}: {transaction_type}")
        expected_from, expected_to, creates, removes = TRANSACTION_TYPES[transaction_type]
        if txn["from_state"] != expected_from or txn["to_state"] != expected_to:
            raise PartsLedgerError(f"illegal state transition at {index}: {transaction_type}")
        quantity = txn["quantity"]
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            raise PartsLedgerError(f"quantity must be a positive integer at {index}")
        if txn["conserves_quantity"] is not (not creates and not removes):
            raise PartsLedgerError(f"conservation flag disagrees with transaction type at {index}")
        part_id = txn["part_master_id"]
        location_id = txn["location_id"]
        if expected_from is not None:
            key = (part_id, location_id, expected_from)
            running[key] = running.get(key, 0) - quantity
            if running[key] < 0:
                raise PartsLedgerError(
                    f"derived balance went negative at {index}: {expected_from}"
                )
        if expected_to is not None:
            key = (part_id, location_id, expected_to)
            running[key] = running.get(key, 0) + quantity
        for state in STOCK_STATES:
            if running.get((part_id, location_id, state), 0) < 0:
                raise PartsLedgerError(f"derived balance went negative at {index}: {state}")
        on_hand = sum(running.get((part_id, location_id, state), 0) for state in PRESENT_STATES)
        if running.get((part_id, location_id, "reserved"), 0) > on_hand:
            raise PartsLedgerError(f"reserved exceeds on-hand at {index}")
        serial = txn["serial_ref"]
        if serial is not None:
            if expected_to in EXIT_STATES:
                serial_positions.pop(serial, None)
            else:
                held = serial_positions.get(serial)
                if held is not None and held[0] != location_id:
                    raise PartsLedgerError(
                        f"serialized unit is in two locations at {index}: {serial}"
                    )
                serial_positions[serial] = (location_id, expected_to or "")
    if running != fold_ledger(transactions):
        raise PartsLedgerError("incremental fold disagrees with whole-ledger fold")


class _PartsBuilder:
    """Deterministic parts-layer builder bound to one simulation run."""

    def __init__(self, context: dict[str, Any]) -> None:
        self.config = context["config"]
        self.run_id = context["run_id"]
        self.scenario = context["scenario"]
        self.scenario_id = context["scenario_id"]
        self.catalog = context["catalog"]
        self.workflow = context["workflow"]
        self.start: datetime = context["start"]
        self.end: datetime = context["end"]
        self.site_id = self.config["site"]["site_id"]
        self.seed = int(self.config["seed"])
        self.duration = max(1, int((self.end - self.start).total_seconds()))
        self.latest_public = self.end - timedelta(seconds=1)
        self.records: dict[str, list[dict[str, Any]]] = {name: [] for name in PARTS_RECORD_TYPES}

        self.wells_by_id = {well["well_id"]: well for well in self.catalog["wells"]}
        self.assets_by_id = {asset["asset_id"]: asset for asset in self.catalog["assets"]}
        self.parts_by_key: dict[str, dict[str, Any]] = {}
        self.parts_by_id: dict[str, dict[str, Any]] = {}
        self.locations_by_code: dict[str, dict[str, Any]] = {}
        self.locations_by_id: dict[str, dict[str, Any]] = {}
        self.applicability_index: dict[tuple[str, str], dict[str, Any]] = {}
        self.travel_index: dict[tuple[str, str], float] = {}
        self.stock_home: dict[str, list[str]] = {}
        #: (part_key, location_id) -> the fit spec codes the units put away at
        #: that location were actually fitted for. Recorded when stock is created,
        #: never rebuilt from demand.
        self.stock_fit: dict[tuple[str, str], tuple[str, ...]] = {}
        self.transactions: list[dict[str, Any]] = []
        self.txn_sequence = 0
        self.work_orders: dict[str, str] = {}
        for record in self.workflow.get("maintenance_context", []):
            self.work_orders[record["asset_id"]] = record["work_order_id"]

    # -- helpers ---------------------------------------------------------
    def at(self, ratio: float) -> datetime:
        return self.start + timedelta(seconds=int(self.duration * clamp(ratio, 0.0, 0.999)))

    def observed(self, value: datetime) -> str:
        return stamp(min(max(value, self.start), self.latest_public))

    def catalog_base(self, record_type: str, *id_parts: str) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "run_id": self.run_id,
            "scenario_id": self.scenario_id,
            "site_id": self.site_id,
            "area_id": PARTS_OWNING_AREA,
            "record_type": record_type,
            "record_id": stable_id(record_type, self.run_id, *id_parts),
            "fidelity": "F0",
            "is_synthetic": True,
            "operational_use": "prohibited",
        }

    def workflow_base(self, record_type: str, *id_parts: str) -> dict[str, Any]:
        record = self.catalog_base(record_type, *id_parts)
        record["fidelity"] = "F0"
        return record

    def close(self, record: dict[str, Any], moment: datetime, **overrides: Any) -> dict[str, Any]:
        settings: dict[str, Any] = {
            "event_time": self.observed(moment),
            "source_time": self.observed(moment),
            "server_time": self.observed(moment + timedelta(seconds=30)),
            "ingest_time": self.observed(moment + timedelta(seconds=60)),
            "origin_state": "workflow_entry",
            "delivery_state": "workflow_delivered",
            "validation_state": "validated",
        }
        settings.update(overrides)
        record.update(envelope(**settings))
        record["is_synthetic"] = True
        record["operational_use"] = "prohibited"
        return record

    def asset_config_code(self, asset: dict[str, Any]) -> str:
        """Configuration key. Asset class alone is deliberately not sufficient."""
        if asset["asset_type"] == "artificial_lift":
            well = self.wells_by_id.get(asset["well_id"])
            variant = well["lift_type"] if well else "unknown"
        else:
            variant = "standard"
        return f"{asset['asset_type']}:{variant}"

    # -- phase 1 ---------------------------------------------------------
    def build_part_master(self) -> None:
        keys = [row[0] for row in _PART_ROWS]
        for row in _PART_ROWS:
            (
                key,
                name,
                scope,
                part_class,
                lead_band,
                tracking_mode,
                uom,
                criticality,
                movement,
                abc,
                insurance,
                lead_days,
            ) = row
            record = self.catalog_base("part_master", key)
            part_master_id = stable_id("part", self.run_id, key)
            superseded_by = _SUPERSESSION.get(key)
            supersedes = next((source for source, target in _SUPERSESSION.items() if target == key), None)
            record.update(
                {
                    "part_master_id": part_master_id,
                    "stock_code": f"FSC-{key.upper().replace('_', '-')}",
                    "part_key": key,
                    "part_name": name,
                    "part_scope": scope,
                    "part_class": part_class,
                    "lead_band": lead_band,
                    "tracking_mode": tracking_mode,
                    "uom": uom,
                    "criticality_class": criticality,
                    "movement_class": movement,
                    "abc_class": abc,
                    "is_insurance_spare": bool(insurance),
                    "is_kit": key in _KIT_PARTS,
                    "kit_member_part_keys": sorted(_COMPANIONS.get(key, ())) if key in _KIT_PARTS else [],
                    "substitute_part_keys": sorted(_SUBSTITUTES.get(key, ())),
                    "supersedes_part_key": supersedes,
                    "superseded_by_part_key": superseded_by,
                    "nominal_lead_time_days_synthetic": int(lead_days),
                    "required_storage_class": storage_class_for(
                        {"uom": uom, "tracking_mode": tracking_mode}
                    ),
                    "applicable_asset_configs": sorted(_APPLICABILITY.get(key, ())),
                    "classification_provenance": "synthetic_synthesis_not_a_standard",
                    "cost_claim": "none",
                }
            )
            self.parts_by_key[key] = record
            self.parts_by_id[part_master_id] = record
            self.records["part_master"].append(record)
        if len(set(keys)) != len(keys):
            raise ValueError("part keys must be unique")
        self.records["part_master"].sort(key=lambda item: item["part_key"])

    # -- phase 2 ---------------------------------------------------------
    def build_part_applicability(self) -> None:
        present_configs = {
            self.asset_config_code(asset)
            for asset in self.catalog["assets"]
            if asset["asset_type"] in DISPATCHABLE_ASSET_TYPES
        }
        effective_from = self.start - timedelta(days=365)
        effective_to = self.end + timedelta(days=365)
        for key in sorted(_APPLICABILITY):
            part = self.parts_by_key[key]
            for config_code in sorted(_APPLICABILITY[key]):
                if config_code not in present_configs:
                    continue
                record = self.catalog_base("part_applicability", key, config_code)
                companions = sorted(
                    self.parts_by_key[companion]["part_master_id"]
                    for companion in _COMPANIONS.get(key, ())
                )
                quantity = 1 + int(
                    4 * hash_float(self.seed, "quantity", key, config_code, low=0.0, high=0.999)
                )
                record.update(
                    {
                        "applicability_id": stable_id("applicability", self.run_id, key, config_code),
                        "part_master_id": part["part_master_id"],
                        "part_key": key,
                        "asset_type": config_code.split(":", 1)[0],
                        "asset_config_code": config_code,
                        "fit_spec_code": fit_spec_code(key, config_code),
                        "quantity_per_assembly": quantity,
                        "effective_from_utc": stamp(effective_from),
                        "effective_to_utc": stamp(effective_to),
                        "interchange_direction": (
                            "supersedes_only" if key in _SUPERSESSION else "bidirectional"
                        ),
                        "interchange_condition": (
                            "fictional_one_way_supersession"
                            if key in _SUPERSESSION
                            else "none"
                        ),
                        "kit_id": stable_id("kit", key) if key in _KIT_PARTS else None,
                        "companion_part_master_ids": companions,
                        "condition_eligibility": (
                            "either" if part["part_class"] in {"R", "RO"} else "new"
                        ),
                        "fitment_claim": "fictional_not_a_real_interchange_claim",
                    }
                )
                self.applicability_index[(key, config_code)] = record
                self.records["part_applicability"].append(record)
        self.records["part_applicability"].sort(
            key=lambda item: (item["part_key"], item["asset_config_code"])
        )

    # -- phase 3 ---------------------------------------------------------
    def build_storeroom_locations(self) -> None:
        for spec in _LOCATION_ROWS:
            record = self.catalog_base("storeroom_location", spec.code)
            record.update(
                {
                    "location_id": stable_id("location", self.run_id, spec.code),
                    "location_code": spec.code
                    if spec.zone is None
                    else f"{spec.code}-{spec.zone}-{spec.aisle}-{spec.rack}-{spec.shelf}-{spec.bin_code}",
                    "location_type": spec.location_type,
                    "parent_location_id": (
                        stable_id("location", self.run_id, spec.parent_code)
                        if spec.parent_code
                        else None
                    ),
                    "region_code": "fictional-region-1",
                    "zone": spec.zone,
                    "aisle": spec.aisle,
                    "rack": spec.rack,
                    "shelf": spec.shelf,
                    "bin_code": spec.bin_code,
                    "storage_classes_supported": list(spec.storage_classes),
                    "id_tech": spec.id_tech,
                    "velocity_zone": spec.velocity_zone,
                    "dispatch_latency_class": spec.latency_class,
                    "nominal_dispatch_minutes_synthetic": round6(spec.handling_minutes),
                    "grid_frame": GRID_FRAME,
                    "grid_east_km": round6(spec.east_km),
                    "grid_north_km": round6(spec.north_km),
                    "grid_distance_to_field_km": round6(
                        plane_distance_km(spec.east_km, spec.north_km)
                    ),
                    "service_radius_km_synthetic": (
                        # A procurement-only location does not drive to assets,
                        # so a service radius is not applicable rather than
                        # zero. Numeric zero is reserved for a real measured
                        # zero; null is how this generator says "not
                        # applicable".
                        None
                        if spec.location_type in NON_TRAVEL_LOCATION_TYPES
                        else round6(spec.service_radius_km)
                    ),
                    "geo_zone_code": f"fictional-geo-zone-{spec.code.lower()}",
                    "geo_claim": "none",
                }
            )
            self.locations_by_code[spec.code] = record
            self.locations_by_id[record["location_id"]] = record
            self.records["storeroom_location"].append(record)

    def dispatchable_assets(self) -> list[dict[str, Any]]:
        assets = [
            asset
            for asset in self.catalog["assets"]
            if asset["asset_type"] in DISPATCHABLE_ASSET_TYPES
            and any(
                (key, self.asset_config_code(asset)) in self.applicability_index
                for key in _APPLICABILITY
            )
        ]
        return sorted(assets, key=lambda item: item["asset_id"])

    def build_travel_matrix(self) -> None:
        for location_code, location in sorted(self.locations_by_code.items()):
            if location["location_type"] in NON_TRAVEL_LOCATION_TYPES:
                # Supplier lead time is procurement, not travel.
                continue
            straight_km = location["grid_distance_to_field_km"]
            for asset in self.dispatchable_assets():
                # Route quality varies by pair. This replaces the previous
                # arbitrary time jitter with a term that has a physical
                # meaning and a published range.
                circuity = hash_float(
                    self.seed,
                    "circuity",
                    location_code,
                    asset["asset_id"],
                    low=CIRCUITY_RANGE[0],
                    high=CIRCUITY_RANGE[1],
                )
                # The unpaved last mile is the only per-asset term, and it is
                # why two assets on one field are not equidistant.
                lease_km = hash_float(
                    self.seed,
                    "lease_road",
                    asset["asset_id"],
                    low=LEASE_ROAD_KM_RANGE[0],
                    high=LEASE_ROAD_KM_RANGE[1],
                )
                road_km = straight_km * circuity + lease_km
                minutes, dominant = travel_minutes_for(road_km, lease_km)
                record = self.catalog_base("travel_matrix", location_code, asset["asset_id"])
                record.update(
                    {
                        "travel_id": stable_id("travel", self.run_id, location_code, asset["asset_id"]),
                        "from_location_id": location["location_id"],
                        "from_location_type": location["location_type"],
                        "to_asset_id": asset["asset_id"],
                        "to_asset_type": asset["asset_type"],
                        "travel_minutes_synthetic": round6(minutes),
                        "distance_km_synthetic": round6(road_km),
                        "straight_line_km_synthetic": round6(straight_km),
                        "circuity_factor_synthetic": round6(circuity),
                        "lease_road_km_synthetic": round6(lease_km),
                        "effective_speed_kmh_synthetic": round6(
                            (60.0 * road_km / minutes) if minutes > 0 else 0.0
                        ),
                        "dominant_road_class": dominant,
                        "transport_mode": "fictional_light_vehicle",
                        "basis": "deterministic_plane_geometry_with_road_class_segments",
                        "routing_claim": "none",
                    }
                )
                self.travel_index[(location["location_id"], asset["asset_id"])] = round6(minutes)
                self.records["travel_matrix"].append(record)
        self.records["travel_matrix"].sort(
            key=lambda item: (item["from_location_id"], item["to_asset_id"])
        )

    # -- phase 6 (opening stock) ----------------------------------------
    #: Van codes in travel order. Every technician carries the same common kit,
    #: so a fast-moving part is stocked on each van rather than only the first.
    VAN_CODES = tuple(
        spec.code for spec in _LOCATION_ROWS if spec.location_type == "field_van_stock"
    )

    def _pick_code(self, part: dict[str, Any], codes: tuple[str, ...]) -> str:
        """Choose one location code for a part, deterministically."""
        index = int(
            len(codes) * hash_float(self.seed, "home", part["part_key"], low=0.0, high=0.999)
        )
        return codes[min(index, len(codes) - 1)]

    def home_location_code(self, part: dict[str, Any]) -> str:
        """Which tier owns a part, and which location within that tier.

        The tier follows from the part's commercial character, exactly as
        before. Spreading across the locations *within* a tier is what makes
        the sourcing decision non-trivial: with one location per tier there was
        never a choice to make.
        """
        if part["part_class"] == "CAP" or part["lead_band"] == "LLT":
            return "CW1"
        if part["part_class"] == "RO":
            return self._pick_code(part, ("RW1", "RW2", "RW3"))
        if part["part_class"] == "R" and part["movement_class"] == "N":
            return self._pick_code(part, ("CON1", "CON2"))
        return self._pick_code(part, ("SS1", "SS2", "SS3", "SS4", "SS5"))

    def add_transaction(
        self,
        transaction_type: str,
        part: dict[str, Any],
        location: dict[str, Any],
        quantity: int,
        moment: datetime,
        *,
        serial_ref: str | None = None,
        lot_ref: str | None = None,
        reservation_id: str | None = None,
        demand_request_id: str | None = None,
        dispatch_id: str | None = None,
        work_order_id: str | None = None,
        asset_id: str | None = None,
    ) -> dict[str, Any]:
        from_state, to_state, creates, removes = TRANSACTION_TYPES[transaction_type]
        self.txn_sequence += 1
        sequence = self.txn_sequence
        record = self.workflow_base(
            "part_stock_txn",
            transaction_type,
            part["part_key"],
            location["location_code"],
            str(sequence),
        )
        record.update(
            {
                "txn_id": stable_id(
                    "stock-txn", self.run_id, transaction_type, part["part_key"],
                    location["location_code"], str(sequence),
                ),
                "txn_sequence": sequence,
                "part_master_id": part["part_master_id"],
                "part_key": part["part_key"],
                "location_id": location["location_id"],
                "location_type": location["location_type"],
                "transaction_type": transaction_type,
                "quantity": int(quantity),
                "from_state": from_state,
                "to_state": to_state,
                "conserves_quantity": not creates and not removes,
                "creates_quantity": creates,
                "removes_quantity": removes,
                "serial_ref": serial_ref,
                "lot_ref": lot_ref,
                "reservation_id": reservation_id,
                "demand_request_id": demand_request_id,
                "dispatch_id": dispatch_id,
                "work_order_id": work_order_id,
                "asset_id": asset_id,
                        "stocked_fit_spec_codes": list(
                            self.stock_fit.get((part["part_key"], location["location_id"]), ())
                        ),
                        "balance_policy": "derived_by_folding_never_stored",
            }
        )
        self.close(record, moment)
        self.transactions.append(record)
        return record

    def serial_for(self, part: dict[str, Any], location_code: str, ordinal: int) -> str | None:
        if part["tracking_mode"] != "serialized":
            return None
        return stable_id("serial", self.run_id, part["part_key"], location_code, str(ordinal))

    def lot_for(self, part: dict[str, Any], location_code: str) -> str | None:
        if part["tracking_mode"] != "lot":
            return None
        return stable_id("lot", self.run_id, part["part_key"], location_code)

    def configs_for(self, part_key: str) -> list[str]:
        """Every asset configuration this part has an applicability row for."""
        return sorted(
            {config for (key, config) in self.applicability_index if key == part_key}
        )

    def record_stocked_fit(
        self, part_key: str, location: dict[str, Any], configs: list[str]
    ) -> None:
        """Record which configurations the units put away here were fitted for."""
        self.stock_fit[(part_key, location["location_id"])] = tuple(
            fit_spec_code(part_key, config) for config in configs
        )

    def build_opening_stock(self) -> None:
        stocked_keys = sorted(
            {key for (key, _config) in self.applicability_index}
        )
        moment = self.at(0.02)
        for key in stocked_keys:
            part = self.parts_by_key[key]
            configs = self.configs_for(key)
            home_code = self.home_location_code(part)
            location = self.locations_by_code[home_code]
            if storage_class_for(part) not in location["storage_classes_supported"]:
                location = next(
                    candidate
                    for _code, candidate in sorted(self.locations_by_code.items())
                    if storage_class_for(part) in candidate["storage_classes_supported"]
                )
            self.stock_home[key] = [location["location_id"]]
            if key == STOCKOUT_PART_KEY:
                # Deliberately not stocked: censored demand must be observable.
                continue
            # A warehouse or storeroom puts away units for every configuration it
            # serves. A van has one shelf, so it carries units fitted for a single
            # configuration; that is where a fit rejection comes from.
            self.record_stocked_fit(key, location, configs)
            quantity = 2 + int(
                9 * hash_float(self.seed, "opening", key, low=0.0, high=0.999)
            )
            if part["tracking_mode"] == "serialized":
                quantity = min(quantity, 3)
                for ordinal in range(1, quantity + 1):
                    self.add_transaction(
                        "opening_balance",
                        part,
                        location,
                        1,
                        moment,
                        serial_ref=self.serial_for(part, location["location_code"], ordinal),
                    )
            else:
                self.add_transaction(
                    "opening_balance",
                    part,
                    location,
                    quantity,
                    moment,
                    lot_ref=self.lot_for(part, location["location_code"]),
                )
            if part["movement_class"] == "F" and part["tracking_mode"] != "serialized":
                for van_code in self.VAN_CODES:
                    van = self.locations_by_code[van_code]
                    if storage_class_for(part) not in van["storage_classes_supported"]:
                        continue
                    self.stock_home[key].append(van["location_id"])
                    # Every van carries the same single configuration for a
                    # part, so a fit rejection on one van is a rejection on all
                    # of them. A second van holding a different configuration
                    # would silently repair the mis-stock and delete the lesson.
                    self.record_stocked_fit(key, van, configs[:1])
                    self.add_transaction(
                        "opening_balance",
                        part,
                        van,
                        2,
                        moment,
                        lot_ref=self.lot_for(part, van["location_code"]),
                    )
        misstock = self.parts_by_key[VAN_MISSTOCK_PART_KEY]
        van = self.locations_by_code["VAN1"]
        misstock_configs = self.configs_for(VAN_MISSTOCK_PART_KEY)
        if storage_class_for(misstock) in van["storage_classes_supported"] and misstock_configs:
            self.stock_home.setdefault(VAN_MISSTOCK_PART_KEY, []).append(van["location_id"])
            self.record_stocked_fit(VAN_MISSTOCK_PART_KEY, van, misstock_configs[:1])
            self.add_transaction(
                "opening_balance",
                misstock,
                van,
                1,
                moment,
                lot_ref=self.lot_for(misstock, van["location_code"]),
            )

    # -- phases 4 and 5 --------------------------------------------------
    def triggers(self) -> list[dict[str, Any]]:
        """Observations that may imply a part replacement, in a total order."""
        by_source: dict[str, list[dict[str, Any]]] = {
            "alarm_journal": [],
            "events": [],
            "condition_monitoring": [],
        }
        seen: set[str] = set()
        for alarm in self.workflow.get("alarm_journal", []):
            if alarm["alarm_state"] != "raised" or alarm["asset_id"] is None:
                continue
            event = next(
                (
                    item
                    for item in self.workflow.get("events", [])
                    if item["event_id"] == alarm["event_id"]
                ),
                None,
            )
            if event is None:
                continue
            by_source["alarm_journal"].append(
                {
                    "alert_id": alarm["alarm_id"],
                    "event_id": alarm["event_id"],
                    "asset_id": alarm["asset_id"],
                    "event_type": event["event_type"],
                    "observed_at_utc": alarm["event_time_utc"],
                    "source": "alarm_journal",
                }
            )
            seen.add(alarm["event_id"])
        for event in self.workflow.get("events", []):
            if event["event_id"] in seen or event["severity"] != "attention":
                continue
            if event["asset_id"] is None:
                continue
            by_source["events"].append(
                {
                    "alert_id": event["event_id"],
                    "event_id": event["event_id"],
                    "asset_id": event["asset_id"],
                    "event_type": event["event_type"],
                    "observed_at_utc": event["event_time_utc"],
                    "source": "events",
                }
            )
        for condition in self.workflow.get("condition_monitoring", []):
            if condition["health_index"] >= CONDITION_TRIGGER_THRESHOLD:
                continue
            by_source["condition_monitoring"].append(
                {
                    "alert_id": condition["condition_id"],
                    "event_id": None,
                    "asset_id": condition["asset_id"],
                    "event_type": None,
                    "observed_at_utc": condition["event_time_utc"],
                    "source": "condition_monitoring",
                }
            )
        for candidates in by_source.values():
            candidates.sort(key=lambda item: (item["observed_at_utc"], item["alert_id"]))

        # A plain time sort lets the always-populated condition summaries crowd
        # out the rarer alarm and event triggers, which would hide the exact and
        # partial rule paths entirely. Reserve a quota per source first, then
        # backfill unused quota in evidence-strength order.
        selected: list[dict[str, Any]] = []
        taken = {source: 0 for source in by_source}
        for source in TRIGGER_SOURCE_ORDER:
            quota = TRIGGER_SOURCE_QUOTA[source]
            chosen = by_source[source][:quota]
            taken[source] = len(chosen)
            selected.extend(chosen)
        for source in TRIGGER_SOURCE_ORDER:
            if len(selected) >= MAX_TRIGGERS:
                break
            extra = by_source[source][taken[source] : taken[source] + MAX_TRIGGERS - len(selected)]
            taken[source] += len(extra)
            selected.extend(extra)
        selected.sort(key=lambda item: (item["observed_at_utc"], item["alert_id"]))
        return selected[:MAX_TRIGGERS]

    def resolve_rule(self, asset: dict[str, Any], event_type: str | None) -> tuple[Any, ...] | None:
        """Return (rule, match strength) or None when no rule fires."""
        asset_type = asset["asset_type"]
        config_code = self.asset_config_code(asset)
        variant = config_code.split(":", 1)[1]
        if event_type is not None:
            rule = _CONFIG_RULES.get((asset_type, event_type, variant))
            if rule is not None:
                return rule, "exact"
            rule = _EVENT_RULES.get((asset_type, event_type))
            if rule is not None:
                return rule, "exact"
            rule = _ASSET_RULES.get(asset_type)
            if rule is not None:
                return rule, "partial"
        group = ASSET_GROUPS.get(asset_type)
        if group is not None and _GROUP_RULES.get(group) is not None:
            return _GROUP_RULES[group], "class_only"
        return None

    def choose_disposition(self, parts: list[dict[str, Any]], asset_id: str) -> str:
        if not parts:
            return "unresolved"
        if asset_id in self.work_orders and len(parts) > 1:
            # The maintenance plan already states what the job consumes, so no
            # fault isolation is required. This is the intended majority path.
            return "predetermined_action"
        if any(
            part["part_class"] in {"CAP", "RO"} or part["lead_band"] == "LLT"
            for part in parts
        ):
            return "diagnose_first"
        if len(parts) == 1:
            return "predetermined_action"
        return "candidate_kit"

    def build_part_candidates(self) -> list[dict[str, Any]]:
        groups: list[dict[str, Any]] = []
        for trigger in self.triggers():
            asset = self.assets_by_id.get(trigger["asset_id"])
            if asset is None:
                continue
            config_code = self.asset_config_code(asset)
            resolved = self.resolve_rule(asset, trigger["event_type"])
            moment = datetime.fromisoformat(trigger["observed_at_utc"][:-1] + "+00:00")
            if resolved is None:
                record = self.workflow_base("part_candidate", trigger["alert_id"], "unmapped")
                record.update(
                    self.candidate_fields(
                        trigger=trigger,
                        asset=asset,
                        config_code=config_code,
                        sub_asset_code=None,
                        failure_mode=None,
                        failure_mechanism=None,
                        failure_cause=None,
                        part=None,
                        rank=1,
                        match_strength=None,
                        coverage_state="unmapped",
                        evidence_basis=None,
                        disposition="unresolved",
                        rule_id=None,
                    )
                )
                self.close(record, moment)
                self.records["part_candidate"].append(record)
                groups.append(
                    {
                        "trigger": trigger,
                        "asset": asset,
                        "config_code": config_code,
                        "disposition": "unresolved",
                        "parts": [],
                        "moment": moment,
                    }
                )
                continue
            rule, match_strength = resolved
            sub_asset_code, mode, mechanism, cause, part_keys, evidence_basis = rule
            eligible = [
                self.parts_by_key[key]
                for key in part_keys
                if (key, config_code) in self.applicability_index
            ]
            if not eligible:
                eligible = [self.parts_by_key[key] for key in part_keys]
                match_strength = "class_only"
            eligible = eligible[:MAX_CANDIDATES_PER_TRIGGER]
            # Deterministic total order with an explicit documented tie-break
            # key. Rank carries no probabilistic meaning.
            ordered = sorted(
                eligible,
                key=lambda part: (
                    0 if (part["part_key"], config_code) in self.applicability_index else 1,
                    {"C": 0, "R": 1, "RO": 2, "CAP": 3}[part["part_class"]],
                    part["part_key"],
                    part["part_master_id"],
                ),
            )
            disposition = self.choose_disposition(ordered, asset["asset_id"])
            coverage = "single_mapping" if len(ordered) == 1 else "multi_mapping"
            rule_id = stable_id("mapping-rule", MAPPING_RULE_VERSION, sub_asset_code, mode)
            for rank, part in enumerate(ordered, 1):
                record = self.workflow_base(
                    "part_candidate", trigger["alert_id"], part["part_key"]
                )
                record.update(
                    self.candidate_fields(
                        trigger=trigger,
                        asset=asset,
                        config_code=config_code,
                        sub_asset_code=sub_asset_code,
                        failure_mode=mode,
                        failure_mechanism=mechanism,
                        failure_cause=cause,
                        part=part,
                        rank=rank,
                        match_strength=match_strength,
                        coverage_state=coverage,
                        evidence_basis=evidence_basis,
                        disposition=disposition,
                        rule_id=rule_id,
                    )
                )
                self.close(record, moment)
                self.records["part_candidate"].append(record)
            groups.append(
                {
                    "trigger": trigger,
                    "asset": asset,
                    "config_code": config_code,
                    "disposition": disposition,
                    "parts": ordered,
                    "moment": moment,
                }
            )
        self.records["part_candidate"].sort(
            key=lambda item: (item["observed_at_utc"], item["alert_id"], item["rank"])
        )
        return groups

    def candidate_fields(
        self,
        *,
        trigger: dict[str, Any],
        asset: dict[str, Any],
        config_code: str,
        sub_asset_code: str | None,
        failure_mode: str | None,
        failure_mechanism: str | None,
        failure_cause: str | None,
        part: dict[str, Any] | None,
        rank: int,
        match_strength: str | None,
        coverage_state: str,
        evidence_basis: str | None,
        disposition: str,
        rule_id: str | None,
    ) -> dict[str, Any]:
        return {
            "candidate_id": stable_id(
                "candidate",
                self.run_id,
                trigger["alert_id"],
                part["part_key"] if part else "unmapped",
            ),
            "alert_id": trigger["alert_id"],
            "alert_source": trigger["source"],
            "event_id": trigger["event_id"],
            "observed_at_utc": trigger["observed_at_utc"],
            "asset_id": asset["asset_id"],
            "asset_type": asset["asset_type"],
            "asset_config_code": config_code,
            "well_id": asset["well_id"],
            "sub_asset_code": sub_asset_code,
            "failure_mode": failure_mode,
            "failure_mechanism": failure_mechanism,
            "failure_cause": failure_cause,
            "candidate_part_master_id": part["part_master_id"] if part else None,
            "candidate_part_key": part["part_key"] if part else None,
            "rank": rank,
            "rank_key": "applicability,part_class,part_key,part_master_id",
            "rule_match_strength": match_strength,
            "coverage_state": coverage_state,
            "evidence_basis": evidence_basis,
            "disposition": disposition,
            "mapping_rule_id": rule_id,
            "mapping_rule_version": MAPPING_RULE_VERSION,
            "diagnosis_claim": "none",
            "probability_claim": "none",
            "root_cause_claim": "none",
            "interpretation": "possible" if part else "insufficient_evidence",
        }

    # -- phases 7 and 8 --------------------------------------------------
    def source_options(self, part: dict[str, Any], asset: dict[str, Any]) -> list[dict[str, Any]]:
        """Rank stocking locations by how soon the part can reach the asset.

        The previous ordering put van stock first unconditionally. With a
        single van that was indistinguishable from ranking by time, because the
        van was always nearest. Across a real network it is wrong: it would
        rank a van in another territory ahead of a storeroom a few km away.

        Total time is handling plus travel. Handling is the tier's readiness
        latency and travel now comes from geometry, so the two are additive
        rather than folded into one constant.
        """
        balances = fold_ledger(self.transactions)
        options: list[dict[str, Any]] = []
        for location_id in self.stock_home.get(part["part_key"], []):
            location = self.locations_by_id[location_id]
            available = derived_available(balances, part["part_master_id"], location_id)
            if available <= 0:
                continue
            travel_minutes = self.travel_index.get((location_id, asset["asset_id"]))
            if travel_minutes is None:
                # No travel row: the location is reached by procurement, so its
                # nominal latency is the whole story.
                travel_minutes = location["nominal_dispatch_minutes_synthetic"]
                total_minutes = travel_minutes
            else:
                total_minutes = (
                    location["nominal_dispatch_minutes_synthetic"] + travel_minutes
                )
            options.append(
                {
                    "location": location,
                    "available": available,
                    "travel_minutes": travel_minutes,
                    "total_minutes": round6(total_minutes),
                }
            )
        return rank_source_options(options)

    def build_dispatches(self, groups: list[dict[str, Any]]) -> None:
        for index, group in enumerate(groups):
            asset = group["asset"]
            trigger = group["trigger"]
            config_code = group["config_code"]
            disposition = group["disposition"]
            moment = group["moment"]
            request_moment = moment + timedelta(seconds=60)
            dispatch_id = stable_id("dispatch", self.run_id, trigger["alert_id"])
            work_order_id = self.work_orders.get(asset["asset_id"])
            staged: list[str] = []
            demand_ids: list[str] = []
            source_location_ids: list[str] = []
            source_types: list[str] = []
            reservation_ids: list[str] = []
            travel_legs: list[float] = []
            fit_verified = False
            truck_checked = False
            van_fit_rejected = False
            fit_rejected = False
            shortfall = False
            blocked_by_fit = False

            considered = [] if disposition in {"unresolved", "diagnose_first"} else group["parts"]
            for order, part in enumerate(considered):
                request_id = stable_id(
                    "demand", self.run_id, trigger["alert_id"], part["part_key"]
                )
                demand_ids.append(request_id)
                requested_qty = self.applicability_index[
                    (part["part_key"], config_code)
                ]["quantity_per_assembly"] if (part["part_key"], config_code) in self.applicability_index else 1
                options = self.source_options(part, asset)
                if options and options[0]["location"]["location_type"] == "field_van_stock":
                    truck_checked = True
                chosen: dict[str, Any] | None = None
                part_fit_rejected = False
                for option in options:
                    location = option["location"]
                    covered = self.stock_fit.get(
                        (part["part_key"], location["location_id"]), ()
                    )
                    if not stocked_fit_is_verified(part["part_key"], config_code, covered):
                        # The location holds this part, but not fitted for this
                        # configuration. Availability is irrelevant.
                        if location["location_type"] == "field_van_stock":
                            van_fit_rejected = True
                        fit_rejected = True
                        part_fit_rejected = True
                        continue
                    if option["available"] < requested_qty:
                        continue
                    chosen = option
                    break
                fulfilled = 0
                latency: float | None = None
                if chosen is not None:
                    location = chosen["location"]
                    reservation_id = stable_id(
                        "reservation", self.run_id, trigger["alert_id"], part["part_key"]
                    )
                    reserve_moment = request_moment + timedelta(seconds=30 * (order + 1))
                    issue_moment = reserve_moment + timedelta(seconds=60)
                    self.add_transaction(
                        "reserved", part, location, requested_qty, reserve_moment,
                        reservation_id=reservation_id, demand_request_id=request_id,
                        dispatch_id=dispatch_id, work_order_id=work_order_id,
                        asset_id=asset["asset_id"],
                        lot_ref=self.lot_for(part, location["location_code"]),
                    )
                    self.add_transaction(
                        "issued", part, location, requested_qty, issue_moment,
                        reservation_id=reservation_id, demand_request_id=request_id,
                        dispatch_id=dispatch_id, work_order_id=work_order_id,
                        asset_id=asset["asset_id"],
                        lot_ref=self.lot_for(part, location["location_code"]),
                    )
                    fulfilled = requested_qty
                    staged.append(part["part_master_id"])
                    fit_verified = True
                    source_location_ids.append(location["location_id"])
                    source_types.append(location["location_type"])
                    reservation_ids.append(reservation_id)
                    travel_legs.append(chosen["travel_minutes"])
                    latency = round6(chosen["travel_minutes"])
                else:
                    shortfall = True
                    if part_fit_rejected:
                        # The same part was both offered unfitted and never
                        # supplied. Only this identity earns the wrong-part label.
                        blocked_by_fit = True
                self.records["part_demand"].append(
                    self.build_demand_record(
                        request_id=request_id,
                        trigger=trigger,
                        asset=asset,
                        part=part,
                        requested_qty=requested_qty,
                        fulfilled_qty=fulfilled,
                        dispatch_id=dispatch_id,
                        work_order_id=work_order_id,
                        moment=request_moment,
                        latency=latency,
                        config_code=config_code,
                        fit_rejected_at_source=part_fit_rejected,
                    )
                )

            if disposition == "unresolved":
                material_state = "created"
                outcome = None
            elif disposition == "diagnose_first":
                material_state = "created"
                outcome = None
            elif blocked_by_fit:
                # The wrong-part label is earned only when one and the same part
                # was offered unfitted for this configuration and was then never
                # supplied from anywhere. A fit rejection the crew recovered from,
                # or an unrelated stockout, is a different story and is reported
                # as such through `van_stock_fit_rejected` and
                # `second_trip_required`.
                material_state = "partially_fulfilled" if staged else "unfulfilled"
                outcome = "wrong_part"
            elif not staged:
                material_state = "unfulfilled"
                outcome = "second_trip_required"
            elif shortfall:
                material_state = "partially_fulfilled"
                outcome = "second_trip_required"
            elif disposition == "candidate_kit" and len(staged) > 1:
                # A candidate kit is speculative by construction, so the surplus
                # comes back. A predetermined action consumes what the plan named,
                # so it does not.
                material_state = "issued"
                outcome = "returned_unused"
            else:
                material_state = "issued"
                outcome = "resolved_first_visit"

            if outcome == "returned_unused":
                returned_part = self.parts_by_id[staged[-1]]
                return_location = self.locations_by_id[source_location_ids[-1]]
                self.add_transaction(
                    "returned_unused",
                    returned_part,
                    return_location,
                    1,
                    request_moment + timedelta(seconds=300),
                    dispatch_id=dispatch_id,
                    work_order_id=work_order_id,
                    asset_id=asset["asset_id"],
                    lot_ref=self.lot_for(returned_part, return_location["location_code"]),
                )

            record = self.workflow_base("dispatch_context", trigger["alert_id"])
            record.update(
                {
                    "dispatch_id": dispatch_id,
                    "work_order_id": work_order_id,
                    "work_order_link_state": "linked" if work_order_id else "unlinked",
                    "triggering_alert_id": trigger["alert_id"],
                    "asset_id": asset["asset_id"],
                    "asset_type": asset["asset_type"],
                    "asset_config_code": config_code,
                    "well_id": asset["well_id"],
                    "disposition": disposition,
                    "candidate_part_master_ids": sorted(
                        part["part_master_id"] for part in group["parts"]
                    ),
                    "staged_part_master_ids": sorted(staged),
                    "demand_request_ids": sorted(demand_ids),
                    "fit_spec_verified": fit_verified,
                    "part_source_location_id": (
                        source_location_ids[0] if source_location_ids else None
                    ),
                    "part_source_location_ids": sorted(set(source_location_ids)),
                    "part_source_type": source_types[0] if source_types else None,
                    "part_source_count": len(set(source_location_ids)),
                    "truck_stock_checked": truck_checked,
                    "van_stock_fit_rejected": van_fit_rejected,
                    "fit_rejected_anywhere": fit_rejected,
                    "reservation_id": reservation_ids[0] if reservation_ids else None,
                    "reservation_ids": sorted(reservation_ids),
                    "travel_minutes_synthetic": (
                        # The trip is as long as its longest leg, not its last one.
                        round6(max(travel_legs)) if travel_legs else None
                    ),
                    "material_state": material_state,
                    "outcome": outcome,
                    "priority_class": "monitoring",
                    "crew_assignment_claim": "none",
                    "diagnosis_claim": "none",
                    "interpretation": "possible" if staged else "insufficient_evidence",
                }
            )
            self.close(record, request_moment + timedelta(seconds=360 + index))
            self.records["dispatch_context"].append(record)

    def build_demand_record(
        self,
        *,
        request_id: str,
        trigger: dict[str, Any],
        asset: dict[str, Any],
        part: dict[str, Any],
        requested_qty: int,
        fulfilled_qty: int,
        dispatch_id: str,
        work_order_id: str | None,
        moment: datetime,
        latency: float | None,
        config_code: str,
        fit_rejected_at_source: bool,
    ) -> dict[str, Any]:
        """Demand is recorded at request time, before any reservation.

        Issue history is censored by supply: when stock is unavailable no issue
        transaction exists even though demand did. Forecasting from fulfilment
        would therefore under-forecast exactly the parts that most often run out.
        """
        outstanding = requested_qty - fulfilled_qty
        backordered = outstanding if outstanding > 0 and part["lead_band"] != "COM" else 0
        lost = outstanding - backordered
        record = self.workflow_base("part_demand", request_id)
        record.update(
            {
                "request_id": request_id,
                "requested_utc": self.observed(moment),
                "required_by_utc": self.observed(moment + timedelta(seconds=3600)),
                "part_master_id": part["part_master_id"],
                "part_key": part["part_key"],
                "substitute_group_key": part["substitute_part_keys"][0]
                if part["substitute_part_keys"]
                else None,
                "asset_id": asset["asset_id"],
                "asset_config_code": config_code,
                "alert_id": trigger["alert_id"],
                "dispatch_id": dispatch_id,
                "work_order_id": work_order_id,
                "demand_reason": "corrective",
                "requested_qty": int(requested_qty),
                "fulfilled_qty": int(fulfilled_qty),
                "substituted_qty": 0,
                "backordered_qty": int(backordered),
                "deferred_qty": 0,
                "cancelled_qty": 0,
                "lost_qty": int(lost),
                "fulfilment_latency_minutes": latency,
                "fulfilment_state": "fulfilled" if fulfilled_qty >= requested_qty else "unfulfilled",
                "fit_rejected_at_source": fit_rejected_at_source,
                "censoring_note": "demand_is_recorded_before_fulfilment_and_is_not_censored_by_supply",
            }
        )
        return self.close(record, moment)

    # -- assembly --------------------------------------------------------
    def build(self) -> dict[str, list[dict[str, Any]]]:
        self.build_part_master()
        self.build_part_applicability()
        self.build_storeroom_locations()
        self.build_travel_matrix()
        self.build_opening_stock()
        groups = self.build_part_candidates()
        self.build_dispatches(groups)
        self.records["part_stock_txn"] = list(self.transactions)
        validate_ledger(self.records["part_stock_txn"])
        for name in ("part_demand", "dispatch_context"):
            self.records[name].sort(key=lambda item: (item["event_time_utc"], item["record_id"]))
        self.records["part_stock_txn"].sort(key=lambda item: item["txn_sequence"])
        return self.records


def build_parts_records(context: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Build every parts family for one run."""
    return _PartsBuilder(context).build()


__all__ = [
    "ABC_CLASSES",
    "ASSET_GROUPS",
    "COVERAGE_STATES",
    "CRITICALITY_CLASSES",
    "DISPATCHABLE_ASSET_TYPES",
    "DISPATCH_OUTCOMES",
    "DISPOSITIONS",
    "EVIDENCE_BASES",
    "LEAD_BANDS",
    "LOCATION_TYPES",
    "MAPPING_RULE_VERSION",
    "MATERIAL_STATES",
    "MOVEMENT_CLASSES",
    "PARTS_CATALOG_RECORD_TYPES",
    "PARTS_OWNING_AREA",
    "PARTS_RECORD_TYPES",
    "PARTS_WORKFLOW_RECORD_TYPES",
    "PART_CLASSES",
    "PRESENT_STATES",
    "PartsLedgerError",
    "RULE_MATCH_STRENGTHS",
    "STOCKOUT_PART_KEY",
    "STOCK_STATES",
    "STORAGE_CLASSES",
    "TRACKING_MODES",
    "TRANSACTION_TYPES",
    "UNITS_OF_MEASURE",
    "VAN_MISSTOCK_PART_KEY",
    "build_parts_records",
    "derived_available",
    "derived_on_hand",
    "fit_is_verified",
    "fit_spec_code",
    "fold_ledger",
    "storage_class_for",
    "validate_ledger",
]
