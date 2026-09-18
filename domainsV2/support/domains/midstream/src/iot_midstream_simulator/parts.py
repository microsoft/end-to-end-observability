"""Deterministic fictional parts, storeroom and inventory layer for midstream.

Implements stage C1 of the cross-domain parts design: the five record families
that need no work order to exist. See
``domains/midstream/research/midstream-parts-inventory/midstream-inventory-design.md``
for the design this module implements, including the reasoning behind every
number here.

What this is not: nothing in this module diagnoses, predicts or recommends.
Part classes, lead bands, criticality, quantities, storeroom positions and
travel times are documented synthetic heuristics, not measured values, and no
standards conformance is claimed. ``fit_spec_code`` is fictional and must never
be read as a real interchange claim. The 17 midstream-scoped part names are
generic descriptions, never real manufacturer part numbers.

Consumption is deliberately out of scope. ``part_candidate`` and
``dispatch_context`` are stage C2 and are not generated here.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, NamedTuple

from .common import hash_float, round6, stable_id, stamp

#: Catalog-shaped parts families. No time or quality envelope, matching the
#: existing `assets` and `topology` structural convention.
PARTS_CATALOG_RECORD_TYPES = (
    "part_master",
    "part_applicability",
    "storeroom_location",
    "travel_matrix",
)
#: Transactional parts families in scope for stage C1. The ledger is the only
#: one, because opening balances need no work order to attach to.
PARTS_LEDGER_RECORD_TYPES = ("part_stock_txn",)
PARTS_RECORD_TYPES = (*PARTS_CATALOG_RECORD_TYPES, *PARTS_LEDGER_RECORD_TYPES)

#: Every parts family is local-only: it is generated for local review and is
#: not part of the published run contract.
LOCAL_ONLY_RECORD_TYPES = PARTS_RECORD_TYPES

#: Documented synthesis, not a standard.
PART_CLASSES = ("C", "R", "RO", "CAP")
LEAD_BANDS = ("COM", "ENG", "LLT")
TRACKING_MODES = ("serialized", "lot", "bulk")
UNITS_OF_MEASURE = ("each", "m", "L", "kg", "roll", "set")
CRITICALITY_CLASSES = ("V", "E", "D")
MOVEMENT_CLASSES = ("F", "S", "N")
ABC_CLASSES = ("A", "B", "C")
STORAGE_CLASSES = ("open_shelf", "binned", "secured_rack", "controlled_liquid_store")

#: The parts layer owns its own fictional spatial frame. Asset records are
#: never mutated to carry positions; see design section 7.1.
ROUTE_FRAME = "fictional-route-frame-v1"

#: Identifier for the fictional planar coordinate frame the route frame is
#: drawn in.
#:
#: This is deliberately **not** a geodetic reference system. There is no datum,
#: and no latitude or longitude is published for anything. Positions are plane
#: offsets in kilometres from a fictional origin, which is the fictional
#: receipt terminal at corridor kilometre zero. Geodetic coordinates are
#: prohibited for this generator.
#:
#: Be precise about what that does and does not mean. The review portal draws
#: this frame over real Gulf Coast geography, and its basemap fixes the origin
#: at a definite point in order to do so. These are therefore **synthetic
#: coordinates deliberately placed in a real geographic setting**, not numbers
#: without a place. What they are not is a claim: no record here identifies,
#: describes or asserts the position of any real facility, operator, terminal,
#: refinery or easement, and any spatial coincidence with one is unintended.
#: The setting is real so that distances read honestly; everything sited in it
#: is invented.
#:
#: The frame exists so that the review portal can *draw* the network rather
#: than schematise it. The grid figures are derived from `route_position_km`,
#: `offset_km` and a bearing, never entered by hand, so a drawn position can
#: never disagree with the distance the same record already publishes.
GRID_FRAME = "fictional-planar-grid-ms-v1"

#: Bearing of the corridor, in degrees clockwise from grid north, from the
#: receipt terminal towards the delivery point. The corridor is a straight
#: line in this frame, so this one angle places every on-corridor position.
CORRIDOR_BEARING_DEG = 90.0

#: Road-class speeds in km/h, as documented synthetic heuristics. The classes
#: differ by roughly a factor of five so that effective speed rises with trip
#: length; a single-speed model cannot express that.
ROAD_CLASS_SPEEDS_KMH: dict[str, float] = {
    "access_road": 25.0,
    "county_road": 85.0,
    "arterial": 105.0,
    "interstate": 120.0,
}
#: How far a trip runs on each class before promoting to the next, in km.
ROAD_CLASS_BANDS_KM: tuple[tuple[str, float], ...] = (
    ("county_road", 25.0),
    ("arterial", 95.0),
)
#: Road distance exceeds corridor distance. Sampled per origin-destination pair
#: to stand in for route quality.
CIRCUITY_RANGE: tuple[float, float] = (1.15, 1.30)
#: The access leg between the public road and the facility, in km. Keyed by
#: asset alone, which is what makes two assets at the same corridor point
#: non-equidistant.
ACCESS_ROAD_KM_RANGE: tuple[float, float] = (0.15, 3.0)

#: (key, name, scope, class, lead band, tracking, uom, criticality, movement,
#:  abc, insurance spare, synthetic nominal lead days)
#:
#: The first 18 are `shared` with upstream and keep upstream's commercial
#: attributes, but their criticality is re-declared for this line: consequence
#: on a transfer corridor is not consequence on a well pad.
_PART_ROWS: tuple[tuple[Any, ...], ...] = (
    ("fastener_set", "Generic fastener and mechanical smalls set", "shared", "C", "COM", "bulk", "set", "D", "F", "C", False, 7),
    ("static_seal_gasket", "Generic static seal and gasket", "shared", "C", "COM", "lot", "each", "E", "F", "C", False, 10),
    ("o_ring_kit", "Generic elastomer o-ring kit", "shared", "C", "COM", "lot", "set", "E", "F", "C", False, 7),
    ("rolling_bearing", "Generic rolling-element bearing", "shared", "R", "COM", "lot", "each", "V", "S", "B", False, 21),
    ("lubricant_charge", "Generic lubricant charge", "shared", "C", "COM", "lot", "L", "E", "F", "C", False, 5),
    ("filter_element", "Generic filter element", "shared", "C", "COM", "lot", "each", "E", "F", "B", False, 14),
    ("instrument_cable", "Generic instrument multipair cable", "shared", "C", "COM", "bulk", "m", "D", "S", "C", False, 10),
    ("cable_gland_kit", "Generic termination and gland kit", "shared", "C", "COM", "bulk", "each", "D", "F", "C", False, 7),
    ("control_relay", "Generic control relay", "shared", "R", "COM", "lot", "each", "E", "S", "B", False, 14),
    ("power_supply_module", "Generic control power-supply module", "shared", "RO", "ENG", "serialized", "each", "V", "S", "A", False, 45),
    ("pressure_transmitter", "Generic pressure transmitter", "shared", "RO", "ENG", "serialized", "each", "V", "S", "A", False, 35),
    ("temperature_sensor_probe", "Generic temperature sensor probe", "shared", "C", "COM", "lot", "each", "E", "S", "B", False, 14),
    ("thermowell", "Generic thermowell", "shared", "R", "ENG", "serialized", "each", "D", "N", "B", False, 60),
    ("solenoid_valve", "Generic solenoid valve", "shared", "R", "COM", "serialized", "each", "E", "S", "B", False, 21),
    ("mechanical_seal_cartridge", "Generic mechanical seal cartridge", "shared", "R", "ENG", "lot", "each", "V", "S", "A", False, 60),
    ("shaft_coupling_kit", "Generic shaft coupling kit", "shared", "C", "ENG", "lot", "set", "E", "S", "B", False, 30),
    ("surge_protection_module", "Generic surge protection module", "shared", "C", "COM", "lot", "each", "E", "N", "C", False, 14),
    ("telemetry_radio_module", "Generic telemetry radio module", "shared", "RO", "ENG", "serialized", "each", "V", "N", "A", False, 55),
    # Midstream-scoped. Each is justified by a failure mode in the research and
    # attaches to an asset type the simulator actually builds.
    ("pump_impeller", "Generic transfer pump impeller", "midstream", "RO", "ENG", "serialized", "each", "V", "S", "A", False, 90),
    ("pump_wear_ring_set", "Generic pump wear ring set", "midstream", "C", "ENG", "lot", "set", "E", "S", "B", False, 45),
    ("pump_shaft_sleeve", "Generic pump shaft sleeve", "midstream", "R", "ENG", "serialized", "each", "E", "S", "B", False, 50),
    ("vfd_cooling_fan", "Generic drive cooling fan", "midstream", "C", "COM", "lot", "each", "E", "F", "B", False, 14),
    ("vfd_dc_link_capacitor", "Generic drive dc-link capacitor", "midstream", "C", "ENG", "lot", "each", "E", "S", "B", False, 40),
    ("vfd_power_module", "Generic drive power module", "midstream", "RO", "LLT", "serialized", "each", "V", "N", "A", True, 150),
    ("mov_actuator_gear_set", "Generic valve actuator gear set", "midstream", "R", "ENG", "serialized", "set", "V", "S", "A", False, 70),
    ("mov_limit_switch_kit", "Generic valve limit switch kit", "midstream", "C", "COM", "lot", "set", "E", "F", "B", False, 14),
    ("valve_stem_packing_set", "Generic valve stem packing set", "midstream", "C", "COM", "lot", "set", "D", "F", "C", False, 10),
    ("strainer_basket", "Generic pipeline strainer basket", "midstream", "RO", "ENG", "serialized", "each", "E", "S", "B", False, 45),
    ("pig_trap_closure_seal", "Generic pig trap closure seal", "midstream", "C", "COM", "lot", "each", "E", "F", "B", False, 12),
    ("meter_wetted_parts_kit", "Generic meter wetted parts kit", "midstream", "R", "ENG", "serialized", "set", "V", "S", "A", False, 60),
    ("cathodic_protection_anode", "Generic cathodic protection anode", "midstream", "C", "ENG", "lot", "each", "E", "S", "B", False, 35),
    ("pipeline_repair_sleeve", "Generic full-encirclement repair sleeve", "midstream", "C", "LLT", "serialized", "each", "V", "N", "A", True, 120),
    ("pipeline_repair_clamp", "Generic bolt-on pipeline repair clamp", "midstream", "C", "ENG", "lot", "each", "V", "N", "A", False, 45),
    ("line_pipe_joint", "Generic line pipe joint", "midstream", "CAP", "LLT", "serialized", "m", "V", "N", "A", True, 180),
    ("spill_containment_kit", "Generic containment and recovery kit", "midstream", "C", "COM", "lot", "set", "V", "F", "B", False, 10),
)

#: Fitment, expressed by asset type because that is how a practitioner reads it.
#: Inverted into a part-keyed index below. `refinery_receiving_boundary` is a
#: handoff marker rather than equipment and is deliberately absent: zero rows,
#: which the portal must render as "no fitment recorded", never as a zero.
_APPLICABILITY_BY_ASSET_TYPE: dict[str, tuple[str, ...]] = {
    "pump_driver": (
        "fastener_set", "static_seal_gasket", "o_ring_kit", "rolling_bearing",
        "lubricant_charge", "mechanical_seal_cartridge", "shaft_coupling_kit",
        "power_supply_module", "control_relay", "pressure_transmitter",
        "temperature_sensor_probe", "instrument_cable", "cable_gland_kit",
        "surge_protection_module",
        "pump_impeller", "pump_wear_ring_set", "pump_shaft_sleeve",
        "vfd_cooling_fan", "vfd_dc_link_capacitor", "vfd_power_module",
    ),
    "control_valve": (
        "fastener_set", "static_seal_gasket", "o_ring_kit", "lubricant_charge",
        "control_relay", "power_supply_module", "instrument_cable",
        "cable_gland_kit", "solenoid_valve",
        "mov_actuator_gear_set", "mov_limit_switch_kit", "valve_stem_packing_set",
    ),
    "block_station": (
        "fastener_set", "static_seal_gasket", "lubricant_charge",
        "instrument_cable", "cable_gland_kit", "surge_protection_module",
        "mov_actuator_gear_set", "mov_limit_switch_kit", "valve_stem_packing_set",
    ),
    "strainer": (
        "fastener_set", "static_seal_gasket", "o_ring_kit", "filter_element",
        "pressure_transmitter",
        "strainer_basket",
    ),
    "storage_tank": (
        "fastener_set", "static_seal_gasket", "o_ring_kit",
        "pressure_transmitter", "temperature_sensor_probe", "thermowell",
        "instrument_cable", "cable_gland_kit",
    ),
    "pig_receiver": (
        "fastener_set", "static_seal_gasket", "o_ring_kit",
        "pig_trap_closure_seal",
    ),
    "metering_skid": (
        "fastener_set", "static_seal_gasket", "o_ring_kit",
        "pressure_transmitter", "temperature_sensor_probe", "thermowell",
        "instrument_cable", "cable_gland_kit", "power_supply_module",
        "meter_wetted_parts_kit",
    ),
    "pipeline_segment": (
        "fastener_set", "static_seal_gasket",
        "cathodic_protection_anode", "pipeline_repair_sleeve",
        "pipeline_repair_clamp", "line_pipe_joint",
    ),
    "pipeline_monitoring_node": (
        "pressure_transmitter", "temperature_sensor_probe", "thermowell",
        "instrument_cable", "cable_gland_kit", "power_supply_module",
        "surge_protection_module", "telemetry_radio_module", "o_ring_kit",
    ),
    "rtu_gateway": (
        "power_supply_module", "control_relay", "surge_protection_module",
        "telemetry_radio_module", "instrument_cable", "cable_gland_kit",
    ),
    "receipt_terminal": (
        "fastener_set", "static_seal_gasket", "o_ring_kit",
        "pressure_transmitter", "instrument_cable", "cable_gland_kit",
    ),
    "leak_detection_engine": (
        "power_supply_module", "surge_protection_module",
    ),
}

#: Parts stocked by location rather than by fitment. A part with no
#: applicability row must not be dropped from the catalogue.
SITE_WIDE_PART_KEYS = frozenset({"spill_containment_kit"})

#: Parts-layer positions along the corridor, in km. Ordering follows the flow
#: chain the simulator already publishes. Monitoring nodes take their position
#: from their own asset record instead; everything else is declared here and
#: carries `position_claim: "none"`. No asset record is modified.
ASSET_ROUTE_POSITION_KM: dict[str, float] = {
    "receipt_terminal": 0.0,
    "storage_tank": 0.1,
    "control_valve": 0.2,
    "strainer": 0.3,
    "pump_driver": 0.4,
    "rtu_gateway": 0.4,
    "leak_detection_engine": 0.4,
    "pipeline_segment": 9.0,
    "block_station": 9.0,
    "pig_receiver": 17.8,
    "metering_skid": 18.0,
    "refinery_receiving_boundary": 18.0,
}


def _invert_applicability() -> dict[str, tuple[str, ...]]:
    index: dict[str, list[str]] = {}
    for asset_type, keys in _APPLICABILITY_BY_ASSET_TYPE.items():
        config_code = f"{asset_type}:standard"
        for key in keys:
            index.setdefault(key, []).append(config_code)
    return {key: tuple(sorted(set(codes))) for key, codes in sorted(index.items())}


#: part_key -> the asset configuration codes it is fitted to.
_APPLICABILITY: dict[str, tuple[str, ...]] = _invert_applicability()


class LocationSpec(NamedTuple):
    """One storeroom location in the fictional corridor network.

    ``route_position_km`` is the point on the corridor the location accesses
    the line from, and is never null: an off-corridor location declares the
    access point rather than leaving the travel formula undefined.
    ``offset_km`` is the road leg out to that point, and
    ``access_bearing_deg`` is the direction that leg runs, clockwise from grid
    north. The bearing carries no extra distance information -- it only says
    which way the same ``offset_km`` points -- but it is what lets the network
    be drawn rather than schematised, and it is chosen to reflect where a tier
    of this kind is actually sited (see ``_LOCATION_ROWS``).
    """

    code: str
    location_type: str
    parent_code: str | None
    storage_classes: tuple[str, ...]
    id_tech: str
    velocity_zone: str
    latency_class: str
    handling_minutes: float
    service_radius_km: float
    route_position_km: float
    offset_km: float
    is_mobile: bool
    profile: str
    zone: str | None
    aisle: str | None
    rack: str | None
    shelf: str | None
    bin_code: str | None
    access_bearing_deg: float


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
    route_position_km: float,
    offset_km: float,
    is_mobile: bool,
    profile: str,
    address: tuple[str | None, ...] = (None, None, None, None, None),
    access_bearing_deg: float = 0.0,
) -> LocationSpec:
    zone, aisle, rack, shelf, bin_code = address
    return LocationSpec(
        code, location_type, parent_code, storage_classes, id_tech, velocity_zone,
        latency_class, handling_minutes, service_radius_km, route_position_km,
        offset_km, is_mobile, profile, zone, aisle, rack, shelf, bin_code,
        access_bearing_deg,
    )


def grid_position_km(spec: LocationSpec) -> tuple[float, float]:
    """Kilometres east and north of the receipt terminal.

    Derived, never declared: the corridor bearing places the access point at
    ``route_position_km``, then the access bearing carries ``offset_km`` out
    from it. The straight-line distance back to that access point is therefore
    exactly ``offset_km`` by construction, so a drawn marker cannot contradict
    the distance the same record publishes as text.
    """

    corridor = math.radians(CORRIDOR_BEARING_DEG)
    east = spec.route_position_km * math.sin(corridor)
    north = spec.route_position_km * math.cos(corridor)
    if spec.offset_km:
        access = math.radians(spec.access_bearing_deg)
        east += spec.offset_km * math.sin(access)
        north += spec.offset_km * math.cos(access)
    return east, north


#: The 11-location network. The line itself is short, so the tier structure
#: lives in the regional supply network behind it rather than along it.
#:
#: `storage_classes` is derived from each location's admitted set, not guessed
#: alongside it: a location must support every storage class the parts it is
#: meant to hold derive to. See design section 6.4.
#:
#: `access_bearing_deg` says which way each off-corridor road leg runs. It
#: changes no distance and no travel time; it exists so the network can be
#: drawn. The directions are not arbitrary:
#:
#: * The corridor runs inland from the receipt terminal, so the whole supply
#:   network sits **behind** the line rather than seaward of it. Every
#:   off-corridor bearing therefore points into the land quadrant.
#: * The regional tier sits inland of the corridor on the coastal plain, near
#:   enough for an hours-class response.
#: * The central warehouse is further inland again and further north. This is a
#:   synthetic siting assumption, not a sourced industry statistic: the design
#:   ledger records no measured figure for post-hurricane coastal access, so no
#:   duration is asserted here. The reasoning is only that stock held to
#:   restore a coastal asset is worth little if it shares that asset's
#:   exposure, so a central warehouse drawn seaward of what it protects would
#:   contradict the reason it exists.
#: * Supplier-held stock is reached by procurement rather than by driving, and
#:   sits in the distant industrial belt up the coast.
_LOCATION_ROWS: tuple[LocationSpec, ...] = (
    _loc("MS-CW1", "central_warehouse", None,
         ("open_shelf", "binned", "secured_rack", "controlled_liquid_store"),
         "rfid", "slow", "next_day", 1440.0, 800.0, 0.0, 140.0, False, "central",
         ("Z1", "A01", "R03", "S02", "B04"), 330.0),
    _loc("MS-DIST", "regional_warehouse", "MS-CW1",
         ("open_shelf", "binned", "secured_rack", "controlled_liquid_store"),
         "barcode", "medium", "hours", 240.0, 320.0, 0.0, 35.0, False, "regional",
         ("Z1", "A02", "R01", "S01", "B02"), 295.0),
    _loc("MS-TERM", "site_storeroom", "MS-DIST",
         ("open_shelf", "binned", "secured_rack", "controlled_liquid_store"),
         "qr", "fast", "minutes", 30.0, 80.0, 0.0, 0.0, False, "broad_site",
         ("Z2", "A01", "R02", "S03", "B01")),
    _loc("MS-PUMP", "site_storeroom", "MS-DIST",
         ("open_shelf", "binned", "secured_rack", "controlled_liquid_store"),
         "qr", "fast", "minutes", 25.0, 80.0, 0.4, 0.0, False, "deep_narrow",
         ("Z2", "A02", "R01", "S01", "B02")),
    _loc("MS-DEL", "site_storeroom", "MS-DIST",
         ("open_shelf", "binned", "secured_rack"),
         "qr", "medium", "minutes", 35.0, 80.0, 18.0, 0.0, False, "delivery_site",
         ("Z3", "A01", "R01", "S02", "B01")),
    # A locked cabinet at the block valve station, not a storeroom. It holds an
    # exact, named four-part set so that its contents are testable.
    _loc("MS-CAB", "field_cabinet", "MS-TERM", ("binned",),
         "none", "fast", "immediate", 10.0, 5.0, 9.0, 0.0, False, "cabinet"),
    _loc("MS-CACHE", "emergency_cache", "MS-DIST",
         ("open_shelf", "binned", "secured_rack"),
         "barcode", "slow", "minutes", 60.0, 40.0, 9.2, 0.1, False, "response",
         ("Z3", "A02", "R01", "S01", "B03"), 0.0),
    # Van stock is mobile. The position is the home base it replenishes at,
    # never a claim about where the van is. That base is the regional
    # warehouse, which is why these share its bearing and offset.
    _loc("MS-VAN1", "field_van_stock", "MS-DIST",
         ("open_shelf", "binned", "controlled_liquid_store"),
         "none", "fast", "immediate", 5.0, 50.0, 0.0, 35.0, True, "van_mech",
         (None, None, None, None, None), 295.0),
    _loc("MS-VAN2", "field_van_stock", "MS-DIST", ("open_shelf", "binned"),
         "none", "fast", "immediate", 5.0, 50.0, 0.0, 35.0, True, "van_ie",
         (None, None, None, None, None), 295.0),
    _loc("MS-CON1", "consignment", "MS-DIST",
         ("open_shelf", "binned", "secured_rack"),
         "barcode", "slow", "minutes", 45.0, 25.0, 0.0, 35.0, False, "consignment",
         ("Z3", "A01", "R01", "S01", "B01"), 295.0),
    # Supplier-held stock is reached through procurement, never by driving.
    _loc("MS-SUP1", "supplier_held", None, ("secured_rack",),
         "none", "non_stocked", "procurement_lead_time", 100800.0, 0.0, 0.0, 400.0,
         False, "supplier", (None, None, None, None, None), 50.0),
)
LOCATION_TYPES = tuple(dict.fromkeys(row.location_type for row in _LOCATION_ROWS))
NON_TRAVEL_LOCATION_TYPES = frozenset({"supplier_held"})

#: Held centrally only. Excluded from every profile except `central`, so the
#: invariant is enforced by the rule rather than hoped for.
CENTRAL_ONLY_PART_KEYS = frozenset({"vfd_power_module", "line_pipe_joint"})

#: The cabinet holds exactly these four. All four are `lot` and so derive to
#: `binned`, which is the cabinet's only storage class: the admitted set and
#: the storage capability agree by construction.
CABINET_PART_KEYS = (
    "mov_limit_switch_kit",
    "valve_stem_packing_set",
    "pig_trap_closure_seal",
    "vfd_cooling_fan",
)

#: The emergency cache holds the repair response set.
REPAIR_PART_KEYS = (
    "pipeline_repair_sleeve",
    "pipeline_repair_clamp",
    "cathodic_protection_anode",
    "spill_containment_kit",
)

#: Asset types whose parts a mechanical crew carries, and an instrument and
#: electrical crew respectively. Defined once and referenced by the profiles so
#: that no profile depends on prose.
MECH_ASSET_TYPES = ("pump_driver", "control_valve", "block_station", "strainer", "pig_receiver")
IE_ASSET_TYPES = ("rtu_gateway", "pipeline_monitoring_node", "leak_detection_engine", "metering_skid")

#: Nominal opening quantity by lead band, before the profile factor.
BASE_QUANTITY_BY_LEAD_BAND: dict[str, int] = {"COM": 8, "ENG": 3, "LLT": 1}
#: Profile depth factor.
TIER_FACTOR_BY_PROFILE: dict[str, float] = {
    "central": 3.0,
    "regional": 2.0,
    "broad_site": 1.0,
    "delivery_site": 1.0,
    "deep_narrow": 1.0,
    "response": 1.0,
    "cabinet": 0.5,
    "van_mech": 0.5,
    "van_ie": 0.5,
    "consignment": 1.5,
    "supplier": 1.0,
}


def storage_class_for(part: dict[str, Any]) -> str:
    """Storage class required by a part, derived from tracking mode and unit.

    Storage is derived and never declared, so a part's unit and tracking mode
    together fix where it can be held.
    """
    if part["uom"] in {"L", "kg"}:
        return "controlled_liquid_store"
    if part["tracking_mode"] == "serialized":
        return "secured_rack"
    if part["tracking_mode"] == "lot":
        return "binned"
    return "open_shelf"


def fit_spec_code(part_key: str, asset_config_code: str) -> str:
    """Fictional fit/form/function key. Never a real interchange claim."""
    return stable_id("fit", part_key, asset_config_code)


def road_segments_km(road_km: float, access_km: float) -> list[tuple[str, float]]:
    """Apportion a road distance across road classes, nearest class first."""
    remaining = max(road_km, 0.0)
    access = min(max(access_km, 0.0), remaining)
    segments: list[tuple[str, float]] = []
    if access > 0.0:
        segments.append(("access_road", access))
    remaining -= access
    for road_class, band_km in ROAD_CLASS_BANDS_KM:
        if remaining <= 0.0:
            break
        leg = min(remaining, band_km)
        segments.append((road_class, leg))
        remaining -= leg
    if remaining > 0.0:
        segments.append(("interstate", remaining))
    return segments


def travel_minutes_for(road_km: float, access_km: float) -> tuple[float, str]:
    """Minutes to drive ``road_km``, and the class contributing the most time."""
    segments = road_segments_km(road_km, access_km)
    minutes = 0.0
    by_class: dict[str, float] = {}
    for road_class, leg_km in segments:
        leg_minutes = 60.0 * leg_km / ROAD_CLASS_SPEEDS_KMH[road_class]
        minutes += leg_minutes
        by_class[road_class] = by_class.get(road_class, 0.0) + leg_minutes
    if not by_class:
        return 0.0, "none"
    dominant = max(sorted(by_class), key=lambda name: by_class[name])
    return minutes, dominant


def fold_opening_balances(transactions: list[dict[str, Any]]) -> dict[tuple[str, str], int]:
    """Fold the ledger into derived balances, keyed by (part, location).

    Balances are always derived, never stored and incremented.
    """
    balances: dict[tuple[str, str], int] = {}
    for txn in transactions:
        key = (txn["part_master_id"], txn["location_id"])
        balances[key] = balances.get(key, 0) + int(txn["quantity"])
    return balances


class PartsBuilder:
    """Builds the five stage C1 parts families for one midstream run."""

    def __init__(
        self,
        config: dict[str, Any],
        run_id: str,
        scenario_id: str,
        assets: list[dict[str, Any]],
    ) -> None:
        self.config = config
        self.run_id = run_id
        self.scenario_id = scenario_id
        self.assets = assets
        self.seed = int(config["seed"])
        self.site_id = config["site"]["site_id"]
        self.start = datetime.fromisoformat(
            config["simulation"]["start_utc"].replace("Z", "+00:00")
        )
        self.parts_by_key: dict[str, dict[str, Any]] = {}
        self.locations_by_code: dict[str, dict[str, Any]] = {}
        self.applicability_index: set[tuple[str, str]] = set()
        self.records: dict[str, list[dict[str, Any]]] = {
            record_type: [] for record_type in PARTS_RECORD_TYPES
        }
        self.txn_sequence = 0

    # -- helpers ---------------------------------------------------------
    def catalog_base(self, record_type: str, *id_parts: str) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "run_id": self.run_id,
            "scenario_id": self.scenario_id,
            "site_id": self.site_id,
            "record_type": record_type,
            "record_id": stable_id(record_type, self.run_id, *id_parts),
            "is_synthetic": True,
            "operational_use": "prohibited",
        }

    def asset_config_code(self, asset: dict[str, Any]) -> str:
        """Configuration key. Midstream has one configuration per asset type."""
        return f"{asset['asset_type']}:standard"

    def asset_route_position_km(self, asset: dict[str, Any]) -> float:
        """Parts-layer corridor position for an asset, in km.

        Monitoring nodes carry a real route distance on their own record, so
        that value is used. Every other position is declared by the parts layer
        and claims nothing about a real place.
        """
        if asset["asset_type"] == "pipeline_monitoring_node":
            return float(asset["route_distance_m"]) / 1000.0
        return ASSET_ROUTE_POSITION_KM[asset["asset_type"]]

    # -- phase 1 ---------------------------------------------------------
    def build_part_master(self) -> None:
        keys = [row[0] for row in _PART_ROWS]
        if len(set(keys)) != len(keys):
            raise ValueError("part keys must be unique")
        for row in _PART_ROWS:
            (
                key, name, scope, part_class, lead_band, tracking_mode, uom,
                criticality, movement, abc, insurance, lead_days,
            ) = row
            record = self.catalog_base("part_master", key)
            part_master_id = stable_id("part", self.run_id, key)
            record.update(
                {
                    "part_master_id": part_master_id,
                    "stock_code": f"MSC-{key.upper().replace('_', '-')}",
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
                    "nominal_lead_time_days_synthetic": int(lead_days),
                    "required_storage_class": storage_class_for(
                        {"uom": uom, "tracking_mode": tracking_mode}
                    ),
                    "applicable_asset_configs": list(_APPLICABILITY.get(key, ())),
                    "is_site_wide": key in SITE_WIDE_PART_KEYS,
                    "classification_provenance": "synthetic_synthesis_not_a_standard",
                    "cost_claim": "none",
                }
            )
            self.parts_by_key[key] = record
            self.records["part_master"].append(record)
        self.records["part_master"].sort(key=lambda item: item["part_key"])

    # -- phase 2 ---------------------------------------------------------
    def build_part_applicability(self) -> None:
        present_configs = {self.asset_config_code(asset) for asset in self.assets}
        for key in sorted(_APPLICABILITY):
            part = self.parts_by_key[key]
            for config_code in _APPLICABILITY[key]:
                if config_code not in present_configs:
                    continue
                record = self.catalog_base("part_applicability", key, config_code)
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
                        "condition_eligibility": (
                            "either" if part["part_class"] in {"R", "RO"} else "new"
                        ),
                        "fitment_claim": "fictional_not_a_real_interchange_claim",
                    }
                )
                self.applicability_index.add((key, config_code))
                self.records["part_applicability"].append(record)
        self.records["part_applicability"].sort(
            key=lambda item: (item["part_key"], item["asset_config_code"])
        )

    # -- phase 3 ---------------------------------------------------------
    def build_storeroom_locations(self) -> None:
        for spec in _LOCATION_ROWS:
            record = self.catalog_base("storeroom_location", spec.code)
            grid_east_km, grid_north_km = grid_position_km(spec)
            record.update(
                {
                    "location_id": stable_id("location", self.run_id, spec.code),
                    "location_code": spec.code,
                    "shelf_address": None
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
                    "stocking_profile": spec.profile,
                    "id_tech": spec.id_tech,
                    "velocity_zone": spec.velocity_zone,
                    "dispatch_latency_class": spec.latency_class,
                    "nominal_dispatch_minutes_synthetic": round6(spec.handling_minutes),
                    "route_frame": ROUTE_FRAME,
                    "route_position_km": round6(spec.route_position_km),
                    "offset_km": round6(spec.offset_km),
                    "on_route": spec.offset_km == 0.0,
                    "is_mobile": spec.is_mobile,
                    "service_radius_km_synthetic": (
                        # A procurement-only location does not drive to assets,
                        # so a service radius is not applicable rather than
                        # zero. Numeric zero is reserved for a measured zero.
                        None
                        if spec.location_type in NON_TRAVEL_LOCATION_TYPES
                        else round6(spec.service_radius_km)
                    ),
                    "geo_zone_code": f"fictional-geo-zone-{spec.code.lower()}",
                    "position_claim": "none",
                    # Plane offsets in the fictional grid, derived from the two
                    # figures above. `position_claim` stays "none" in the
                    # narrow sense the field carries elsewhere in this
                    # repository: the record asserts no real-world position.
                    # These are synthetic coordinates, and the portal does draw
                    # them over a real setting, but nothing here identifies or
                    # locates a real facility. See GRID_FRAME.
                    "grid_frame": GRID_FRAME,
                    "grid_east_km": round6(grid_east_km),
                    "grid_north_km": round6(grid_north_km),
                    "access_bearing_deg": round6(spec.access_bearing_deg),
                }
            )
            self.locations_by_code[spec.code] = record
            self.records["storeroom_location"].append(record)

    # -- phase 4 ---------------------------------------------------------
    def dispatchable_assets(self) -> list[dict[str, Any]]:
        """Assets with at least one fitment row.

        An asset with no applicability row is not a destination, which is what
        excludes the refinery receiving boundary.
        """
        assets = [
            asset
            for asset in self.assets
            if any(
                (key, self.asset_config_code(asset)) in self.applicability_index
                for key in _APPLICABILITY
            )
        ]
        return sorted(assets, key=lambda item: item["asset_id"])

    def build_travel_matrix(self) -> None:
        destinations = self.dispatchable_assets()
        for spec in _LOCATION_ROWS:
            if spec.location_type in NON_TRAVEL_LOCATION_TYPES:
                # Supplier lead time is procurement, not travel.
                continue
            location = self.locations_by_code[spec.code]
            for asset in destinations:
                asset_position = self.asset_route_position_km(asset)
                # Crews drive along the corridor, not across a plane, then
                # take the off-route leg once.
                corridor_km = abs(asset_position - spec.route_position_km) + spec.offset_km
                circuity = hash_float(
                    self.seed,
                    "circuity",
                    spec.code,
                    asset["asset_id"],
                    low=CIRCUITY_RANGE[0],
                    high=CIRCUITY_RANGE[1],
                )
                # Keyed by asset alone, which is what makes two assets at the
                # same corridor point non-equidistant.
                access_km = hash_float(
                    self.seed,
                    "access_road",
                    asset["asset_id"],
                    low=ACCESS_ROAD_KM_RANGE[0],
                    high=ACCESS_ROAD_KM_RANGE[1],
                )
                road_km = corridor_km * circuity + access_km
                minutes, dominant = travel_minutes_for(road_km, access_km)
                record = self.catalog_base("travel_matrix", spec.code, asset["asset_id"])
                record.update(
                    {
                        "travel_id": stable_id("travel", self.run_id, spec.code, asset["asset_id"]),
                        "from_location_id": location["location_id"],
                        "from_location_type": location["location_type"],
                        "to_asset_id": asset["asset_id"],
                        "to_asset_type": asset["asset_type"],
                        "to_asset_route_position_km": round6(asset_position),
                        "travel_minutes_synthetic": round6(minutes),
                        "distance_km_synthetic": round6(road_km),
                        "corridor_km_synthetic": round6(corridor_km),
                        "circuity_factor_synthetic": round6(circuity),
                        "access_road_km_synthetic": round6(access_km),
                        "effective_speed_kmh_synthetic": round6(
                            (60.0 * road_km / minutes) if minutes > 0 else 0.0
                        ),
                        "dominant_road_class": dominant,
                        "transport_mode": "fictional_light_vehicle",
                        "basis": "deterministic_corridor_geometry_with_road_class_segments",
                        "routing_claim": "none",
                    }
                )
                self.records["travel_matrix"].append(record)
        self.records["travel_matrix"].sort(
            key=lambda item: (item["from_location_id"], item["to_asset_id"])
        )

    # -- phase 5 (opening stock) ----------------------------------------
    def _fitted_to_any(self, key: str, asset_types: tuple[str, ...]) -> bool:
        configs = _APPLICABILITY.get(key, ())
        return any(f"{asset_type}:standard" in configs for asset_type in asset_types)

    def admitted_part_keys(self, profile: str) -> list[str]:
        """The exact set of parts a profile stocks. Never a prose description."""
        every = [row[0] for row in _PART_ROWS]

        def commercial(key: str) -> bool:
            return self.parts_by_key[key]["lead_band"] in {"COM", "ENG"}

        def central_ok(keys: list[str]) -> list[str]:
            return [key for key in keys if key not in CENTRAL_ONLY_PART_KEYS]

        if profile == "central":
            return sorted(every)
        if profile == "regional":
            return sorted(central_ok(
                [key for key in every if self.parts_by_key[key]["part_class"] != "CAP"]
            ))
        if profile == "broad_site":
            return sorted(central_ok([key for key in every if commercial(key)]))
        if profile == "delivery_site":
            # The custody and delivery end of the line: instrument and
            # electrical parts plus the meter's own. Deliberately not the same
            # set as `broad_site`, which is why it is a separate profile.
            return sorted(central_ok([
                key for key in every
                if commercial(key)
                and self._fitted_to_any(key, IE_ASSET_TYPES)
            ]))
        if profile == "deep_narrow":
            return sorted(central_ok([
                key for key in every
                if commercial(key) and self._fitted_to_any(key, ("pump_driver",))
            ]))
        if profile == "cabinet":
            return sorted(CABINET_PART_KEYS)
        if profile == "response":
            return sorted(central_ok(list(REPAIR_PART_KEYS)))
        if profile == "van_mech":
            return sorted(central_ok([
                key for key in every
                if self._fitted_to_any(key, MECH_ASSET_TYPES)
                and self.parts_by_key[key]["part_class"] == "C"
                and self.parts_by_key[key]["lead_band"] == "COM"
            ]))
        if profile == "van_ie":
            return sorted(central_ok([
                key for key in every
                if self._fitted_to_any(key, IE_ASSET_TYPES)
                and self.parts_by_key[key]["part_class"] == "C"
                and self.parts_by_key[key]["lead_band"] == "COM"
            ]))
        if profile == "consignment":
            return sorted(central_ok(["cathodic_protection_anode", "fastener_set"]))
        if profile == "supplier":
            return sorted(central_ok(["pipeline_repair_sleeve"]))
        raise ValueError(f"unknown stocking profile: {profile}")

    def is_eligible(self, key: str, spec: LocationSpec) -> bool:
        """Storage guard. Expected to reject nothing; a rejection is a bug."""
        part = self.parts_by_key[key]
        if storage_class_for(part) not in spec.storage_classes:
            return False
        return bool(_APPLICABILITY.get(key)) or key in SITE_WIDE_PART_KEYS

    def opening_quantity(self, key: str, spec: LocationSpec) -> int:
        part = self.parts_by_key[key]
        base = BASE_QUANTITY_BY_LEAD_BAND[part["lead_band"]]
        tier = TIER_FACTOR_BY_PROFILE[spec.profile]
        # The explicit bounds are load-bearing: the default range is signed, so
        # an unbounded draw would make this negative and produce zero stock.
        jitter = int(3 * hash_float(self.seed, "qty", spec.code, key, low=0.0, high=0.999))
        quantity = max(1, int(base * tier) + jitter)
        if part["tracking_mode"] == "serialized":
            quantity = min(quantity, 2 if spec.code == "MS-CW1" else 1)
        return quantity

    def add_opening_balance(
        self,
        part: dict[str, Any],
        location: dict[str, Any],
        quantity: int,
        moment: datetime,
        *,
        serial_ref: str | None = None,
        lot_ref: str | None = None,
    ) -> None:
        self.txn_sequence += 1
        sequence = self.txn_sequence
        record = self.catalog_base(
            "part_stock_txn",
            part["part_key"],
            location["location_code"],
            str(sequence),
        )
        record.update(
            {
                "txn_id": stable_id(
                    "stock-txn", self.run_id, part["part_key"],
                    location["location_code"], str(sequence),
                ),
                "txn_sequence": sequence,
                "part_master_id": part["part_master_id"],
                "part_key": part["part_key"],
                "location_id": location["location_id"],
                "location_type": location["location_type"],
                "transaction_type": "opening_balance",
                "quantity": int(quantity),
                "from_state": None,
                "to_state": "available",
                "serial_ref": serial_ref,
                "lot_ref": lot_ref,
                "event_time_utc": stamp(moment),
                "source_time_utc": stamp(moment),
                "ingest_time_utc": stamp(moment + timedelta(seconds=60)),
                "quality": "good",
                "origin_state": "workflow_entry",
                "balance_policy": "derived_by_folding_never_stored",
            }
        )
        self.records["part_stock_txn"].append(record)

    def build_opening_stock(self) -> None:
        moment = self.start + timedelta(seconds=60)
        for spec in _LOCATION_ROWS:
            location = self.locations_by_code[spec.code]
            for key in self.admitted_part_keys(spec.profile):
                if not self.is_eligible(key, spec):
                    raise ValueError(
                        f"{spec.code} admits {key} but cannot store it; "
                        "the profile and the storage classes disagree"
                    )
                part = self.parts_by_key[key]
                quantity = self.opening_quantity(key, spec)
                if part["tracking_mode"] == "serialized":
                    for ordinal in range(1, quantity + 1):
                        self.add_opening_balance(
                            part, location, 1, moment,
                            serial_ref=stable_id(
                                "serial", self.run_id, key, spec.code, str(ordinal)
                            ),
                        )
                else:
                    self.add_opening_balance(
                        part, location, quantity, moment,
                        lot_ref=stable_id("lot", self.run_id, key, spec.code)
                        if part["tracking_mode"] == "lot"
                        else None,
                    )
        self.records["part_stock_txn"].sort(
            key=lambda item: (item["location_id"], item["part_key"], item["txn_sequence"])
        )

    def build(self) -> dict[str, list[dict[str, Any]]]:
        self.build_part_master()
        self.build_part_applicability()
        self.build_storeroom_locations()
        self.build_travel_matrix()
        self.build_opening_stock()
        return self.records


def build_parts(
    config: dict[str, Any],
    run_id: str,
    scenario_id: str,
    assets: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Build every stage C1 parts family for one run."""
    return PartsBuilder(config, run_id, scenario_id, assets).build()
