"""Deterministic fictional parts, equipment and inventory layer for downstream.

See
``domains/downstream/research/downstream-parts-inventory/downstream-inventory-design.md``
for the design this module implements, including the reasoning behind every
number here.

What this is not: nothing in this module diagnoses, predicts or recommends.
Equipment populations, part classes, lead bands, criticality, quantities,
storeroom positions, site coordinates and travel times are documented synthetic
heuristics, not measured values, and no standards conformance is claimed.
Standards are named where they are the *origin of a concept* — API 521 explains
why the flare sits away from everything else — never as a compliance claim.
``fit_spec_code`` is fictional and must never be read as a real interchange
claim. Part and equipment names are generic descriptions, never real
manufacturer part numbers.

Consumption is deliberately out of scope. The ledger carries opening balances
only; there are no issues, reservations or work-order draws.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, NamedTuple

from .catalog import AREA_DEFINITIONS
from .common import hash_float, round6, stable_id, stamp

#: Catalog-shaped parts families. No time or quality envelope, matching the
#: existing `assets` and `topology` structural convention.
PARTS_CATALOG_RECORD_TYPES = (
    "equipment_register",
    "part_master",
    "part_applicability",
    "storeroom_location",
    "travel_matrix",
)
#: Transactional families. The ledger is the only one, because opening
#: balances need no work order to attach to.
PARTS_LEDGER_RECORD_TYPES = ("part_stock_txn",)
PARTS_RECORD_TYPES = (*PARTS_CATALOG_RECORD_TYPES, *PARTS_LEDGER_RECORD_TYPES)

#: Every parts family is local-only: it is generated for local review and is
#: not part of the published run contract.
LOCAL_ONLY_RECORD_TYPES = PARTS_RECORD_TYPES

# ---------------------------------------------------------------------------
# Vocabularies. Documented synthesis, not standards.
# ---------------------------------------------------------------------------

PART_CLASSES = ("C", "R", "RO", "CAP")
LEAD_BANDS = ("COM", "ENG", "LLT")
TRACKING_MODES = ("serialized", "lot", "bulk")
UNITS_OF_MEASURE = ("EA", "SET", "KIT", "M", "M2", "M3", "L", "KG", "BOX", "DRUM")
CRITICALITY_CLASSES = ("V", "E", "D")
MOVEMENT_CLASSES = ("F", "S", "N")
ABC_CLASSES = ("A", "B", "C")

#: Physical storage segregation. A refinery warehouse separates stock by what
#: the stock physically needs, which a pipeline corridor does not.
#: ``FLAM`` covers the bunded, ventilated, fire-rated store. ``SECURE`` covers
#: the access-controlled cage. ``PRESERVED`` covers nitrogen-blanketed or
#: VCI-wrapped rotating spares under a shaft-rotation regime.
STORAGE_CLASSES = (
    "YARD",
    "RACK_WH",
    "BIN_WH",
    "CLIMATE",
    "PRESERVED",
    "FLAM",
    "SECURE",
)

#: Shelf-life behaviour. ``NONE`` means the item does not age on the shelf in a
#: way this model represents; it does not mean the item lasts forever.
SHELF_LIFE_CLASSES = (
    "NONE",
    "ELASTOMER_5Y",
    "ELASTOMER_10Y",
    "BATTERY_3Y",
    "ADHESIVE_2Y",
    "LUBRICANT_5Y",
)

#: Nominal shelf life in years by class, for the classes that have one.
#: Banded from ISO 2230 and manufacturer practice; see design evidence ledger
#: rows 15 and 20. ``NONE`` is deliberately absent rather than zero.
SHELF_LIFE_YEARS_SYNTHETIC: dict[str, int] = {
    "ELASTOMER_5Y": 5,
    "ELASTOMER_10Y": 10,
    "BATTERY_3Y": 3,
    "ADHESIVE_2Y": 2,
    "LUBRICANT_5Y": 5,
}

# ---------------------------------------------------------------------------
# Spatial frames
# ---------------------------------------------------------------------------

#: The parts layer owns its own fictional spatial frame. Asset records are
#: never mutated to carry positions.
SITE_FRAME = "fictional-refinery-site-frame-v1"

#: The shared planar grid. This is deliberately the **same frame identifier**
#: the midstream parts layer uses, because it is the same fictional world: the
#: midstream corridor delivers to this refinery. Sharing the identifier is what
#: makes the two domains' positions joinable instead of merely similar.
#:
#: This is not a geodetic reference system. There is no datum, and no latitude
#: or longitude is published for anything. Positions are plane offsets in
#: kilometres from a fictional origin.
#:
#: Be precise about what that does and does not mean. The review portal draws
#: this frame over real Gulf Coast geography. These are therefore synthetic
#: coordinates deliberately placed in a real geographic setting, not numbers
#: without a place. What they are not is a claim: no record here identifies,
#: describes or asserts the position of any real refinery, storeroom, facility
#: or operator, and any spatial coincidence with one is unintended.
GRID_FRAME = "fictional-planar-grid-ms-v1"

#: Where the midstream corridor hands over, read from the midstream parts
#: layer rather than guessed: ``ASSET_ROUTE_POSITION_KM["refinery_receiving_
#: boundary"] == 18.0`` and ``CORRIDOR_BEARING_DEG == 90.0``. Duplicated as
#: constants rather than imported, because downstream must not take a runtime
#: dependency on the midstream package.
CORRIDOR_DELIVERY_POSITION_KM = 18.0
CORRIDOR_BEARING_DEG = 90.0

#: Site dimensions in metres. Mid-capacity band from the site research: a
#: ~200 kbpd refinery occupies roughly 1,000-1,500 m across.
SITE_WIDTH_M = 1300.0
SITE_DEPTH_M = 1200.0

#: Internal vehicle speed in km/h. Refinery speed limits run 15-25 mph in
#: general areas; this is the lower, in-plant figure.
SITE_SPEED_KMH = 25.0
#: Road distance exceeds straight-line distance inside the fence too: the
#: roads run around blocks, not through them.
SITE_CIRCUITY_RANGE: tuple[float, float] = (1.20, 1.45)


def site_origin_grid_km() -> tuple[float, float]:
    """Grid position of the site's southwest corner, in kilometres.

    Derived from the corridor delivery point and the site dimensions, never
    typed as a literal, so that changing the site size cannot leave the map and
    the location table disagreeing.
    """
    bearing = math.radians(CORRIDOR_BEARING_DEG)
    centre_east = CORRIDOR_DELIVERY_POSITION_KM * math.sin(bearing)
    centre_north = CORRIDOR_DELIVERY_POSITION_KM * math.cos(bearing)
    return (
        centre_east - (SITE_WIDTH_M / 2000.0),
        centre_north - (SITE_DEPTH_M / 2000.0),
    )


def grid_position_km(site_x_m: float, site_y_m: float) -> tuple[float, float]:
    """Convert a site position in metres to the shared grid, in kilometres."""
    origin_east, origin_north = site_origin_grid_km()
    return (origin_east + site_x_m / 1000.0, origin_north + site_y_m / 1000.0)


# ---------------------------------------------------------------------------
# Area layout
# ---------------------------------------------------------------------------

#: Area centres on the site grid, in metres from the southwest corner.
#:
#: The layout is **rotated relative to the site research**, deliberately. That
#: research assumed marine crude receipt on the seaward edge, which is the Gulf
#: Coast norm. This site is 3.19 km inland and the midstream corridor already
#: delivers crude to it by pipeline from the west, so crude arrives on the west
#: edge and product leaves east and north. Design section 6.2 records the
#: departure.
#:
#: Flow therefore runs west to east. The cold zone — warehouse, shops, lab,
#: control room — sits along the north edge at the main gate, furthest from the
#: flare. The flare sits in the southeast corner, remote from occupied
#: buildings; API 521 is the origin of that idea, not an approval of this
#: distance.
_AREA_SITE_POSITION_M: dict[str, tuple[float, float]] = {
    "A01": (380.0, 400.0),
    "A02": (520.0, 480.0),
    "A03": (700.0, 520.0),
    "A04": (620.0, 300.0),
    "A05": (450.0, 220.0),
    "A06": (900.0, 700.0),
    "A07": (1050.0, 500.0),
    "A08": (760.0, 780.0),
    "A09": (900.0, 900.0),
    "A10": (1220.0, 90.0),
    "A11": (620.0, 1060.0),
    "A12": (560.0, 900.0),
}

# ---------------------------------------------------------------------------
# Equipment register
# ---------------------------------------------------------------------------

EQUIPMENT_CLASSES = (
    "air_cooled_exchanger",
    "centrifugal_compressor",
    "centrifugal_pump",
    "control_valve",
    "cooling_tower_cell",
    "dcs_sis_controller",
    "distillation_column",
    "electric_motor",
    "fired_heater",
    "flare_system",
    "instrument_transmitter",
    "mcc_switchgear",
    "pressure_relief_valve",
    "process_analyzer",
    "reactor_vessel",
    "reciprocating_compressor",
    "shell_and_tube_exchanger",
    "storage_tank",
    "transformer",
    "variable_frequency_drive",
)

#: Human-readable service description per class.
EQUIPMENT_CLASS_LABELS: dict[str, str] = {
    "air_cooled_exchanger": "Air-cooled exchanger (fin-fan)",
    "centrifugal_compressor": "Centrifugal compressor",
    "centrifugal_pump": "Centrifugal pump",
    "control_valve": "Control valve",
    "cooling_tower_cell": "Cooling tower cell",
    "dcs_sis_controller": "DCS/SIS controller",
    "distillation_column": "Distillation column",
    "electric_motor": "Electric motor",
    "fired_heater": "Fired heater",
    "flare_system": "Flare system",
    "instrument_transmitter": "Instrument transmitter",
    "mcc_switchgear": "Switchgear / motor control centre",
    "pressure_relief_valve": "Pressure relief valve",
    "process_analyzer": "Process analyzer",
    "reactor_vessel": "Reactor vessel",
    "reciprocating_compressor": "Reciprocating compressor",
    "shell_and_tube_exchanger": "Shell-and-tube exchanger",
    "storage_tank": "Storage tank",
    "transformer": "Transformer",
    "variable_frequency_drive": "Variable frequency drive",
}

#: Tag letters used to build equipment tags, e.g. ``A02-P-0003``.
EQUIPMENT_CLASS_TAG_LETTER: dict[str, str] = {
    "air_cooled_exchanger": "EA",
    "centrifugal_compressor": "KC",
    "centrifugal_pump": "P",
    "control_valve": "FV",
    "cooling_tower_cell": "CT",
    "dcs_sis_controller": "IC",
    "distillation_column": "CL",
    "electric_motor": "MO",
    "fired_heater": "FH",
    "flare_system": "FL",
    "instrument_transmitter": "IT",
    "mcc_switchgear": "SG",
    "pressure_relief_valve": "PSV",
    "process_analyzer": "AN",
    "reactor_vessel": "RX",
    "reciprocating_compressor": "KR",
    "shell_and_tube_exchanger": "EX",
    "storage_tank": "TK",
    "transformer": "TR",
    "variable_frequency_drive": "VF",
}

#: Classes that roll up to the area's **support** asset rather than its primary
#: asset. Electrical, instrument and analyser equipment is diagnostic and
#: ancillary; process equipment is primary.
_SUPPORT_EQUIPMENT_CLASSES = frozenset(
    {
        "dcs_sis_controller",
        "electric_motor",
        "instrument_transmitter",
        "mcc_switchgear",
        "process_analyzer",
        "transformer",
        "variable_frequency_drive",
    }
)

#: Classes whose loss stops the area. Drives ``is_critical_service``.
_CRITICAL_EQUIPMENT_CLASSES = frozenset(
    {
        "centrifugal_compressor",
        "distillation_column",
        "fired_heater",
        "flare_system",
        "reactor_vessel",
        "reciprocating_compressor",
        "transformer",
    }
)

#: Classes that carry an installed standby. The research is explicit that
#: critical continuous-service centrifugal pumps are 80-100% installed-spared,
#: while compressors are rarely fully spared and instead hold warehouse
#: rotors.
_SPARED_EQUIPMENT_CLASSES = frozenset({"centrifugal_pump", "cooling_tower_cell"})

#: Representative equipment population per area.
#:
#: These are **not** realistic counts. The research puts 400-1,000 pumps and
#: 400-900 exchangers at a real mid-size refinery. Emitting that would be
#: precision without information: a portal cannot show it and nobody can read
#: it. The register carries enough of each class to characterise the area and
#: to give fitment something concrete to attach to. Design section 4.5 records
#: the reduction as a limitation rather than hiding it.
_EQUIPMENT_POPULATION: dict[str, tuple[tuple[str, int], ...]] = {
    "A01": (
        ("centrifugal_pump", 4),
        ("storage_tank", 4),
        ("control_valve", 2),
        ("electric_motor", 4),
        ("instrument_transmitter", 3),
    ),
    "A02": (
        ("distillation_column", 2),
        ("fired_heater", 2),
        ("shell_and_tube_exchanger", 6),
        ("air_cooled_exchanger", 3),
        ("centrifugal_pump", 6),
        ("control_valve", 4),
        ("pressure_relief_valve", 4),
        ("electric_motor", 6),
        ("instrument_transmitter", 4),
    ),
    "A03": (
        ("reactor_vessel", 2),
        ("distillation_column", 1),
        ("centrifugal_compressor", 2),
        ("centrifugal_pump", 5),
        ("shell_and_tube_exchanger", 5),
        ("air_cooled_exchanger", 2),
        ("fired_heater", 1),
        ("control_valve", 3),
        ("pressure_relief_valve", 3),
        ("electric_motor", 5),
    ),
    "A04": (
        ("reactor_vessel", 3),
        ("distillation_column", 2),
        ("reciprocating_compressor", 2),
        ("centrifugal_pump", 4),
        ("shell_and_tube_exchanger", 4),
        ("fired_heater", 2),
        ("control_valve", 3),
        ("pressure_relief_valve", 3),
        ("electric_motor", 4),
    ),
    "A05": (
        ("reactor_vessel", 2),
        ("distillation_column", 2),
        ("centrifugal_compressor", 1),
        ("centrifugal_pump", 3),
        ("shell_and_tube_exchanger", 3),
        ("control_valve", 2),
        ("pressure_relief_valve", 2),
        ("electric_motor", 3),
    ),
    "A06": (
        ("centrifugal_pump", 3),
        ("control_valve", 4),
        ("process_analyzer", 3),
        ("electric_motor", 3),
        ("instrument_transmitter", 4),
    ),
    "A07": (
        ("storage_tank", 6),
        ("centrifugal_pump", 5),
        ("reciprocating_compressor", 1),
        ("control_valve", 3),
        ("electric_motor", 5),
        ("instrument_transmitter", 4),
    ),
    "A08": (
        ("transformer", 3),
        ("mcc_switchgear", 3),
        ("variable_frequency_drive", 4),
        ("control_valve", 2),
        ("pressure_relief_valve", 3),
        ("electric_motor", 2),
        ("instrument_transmitter", 3),
    ),
    "A09": (
        ("cooling_tower_cell", 4),
        ("centrifugal_pump", 5),
        ("reciprocating_compressor", 2),
        ("shell_and_tube_exchanger", 2),
        ("control_valve", 2),
        ("electric_motor", 6),
        ("instrument_transmitter", 3),
    ),
    "A10": (
        ("flare_system", 2),
        ("control_valve", 1),
        ("pressure_relief_valve", 2),
        ("process_analyzer", 3),
        ("instrument_transmitter", 3),
    ),
    "A11": (
        ("process_analyzer", 4),
        ("instrument_transmitter", 2),
    ),
    "A12": (
        ("dcs_sis_controller", 6),
        ("instrument_transmitter", 2),
    ),
}

#: Synthetic install-year window. The site reads as an operating plant with a
#: mixed-age population rather than one built in a single year.
_INSTALL_YEAR_RANGE: tuple[int, int] = (1998, 2023)

# ---------------------------------------------------------------------------
# Part catalogue
# ---------------------------------------------------------------------------

#: (key, name, part class, lead band, tracking, uom, criticality, movement,
#:  abc, insurance spare, synthetic nominal lead days, storage class,
#:  shelf-life class)
#:
#: Lead days are banded from the parts research lead-time table, which puts
#: commodity items at 1-6 weeks, engineered items at 8-24 weeks and long-lead
#: items at 24-72 weeks in the 2024-2026 market. They are deliberately **not**
#: inherited from the midstream layer, whose ceiling of 70 days this research
#: contradicts directly for pumps, transformers and rotors.
_PART_ROWS: tuple[tuple[Any, ...], ...] = (
    ("actuator_diaphragm", "Generic valve actuator diaphragm", "R", "ENG", "lot", "EA", "E", "S", "B", False, 70, "RACK_WH", "ELASTOMER_5Y"),
    ("analyzer_module", "Generic process analyzer module", "RO", "LLT", "serialized", "EA", "V", "N", "A", False, 210, "CLIMATE", "NONE"),
    ("analyzer_sample_kit", "Generic analyzer sample conditioning kit", "C", "ENG", "lot", "KIT", "E", "S", "B", False, 70, "RACK_WH", "NONE"),
    ("antifriction_bearing", "Generic antifriction bearing", "C", "ENG", "lot", "EA", "V", "F", "B", False, 56, "CLIMATE", "NONE"),
    ("bearing_oil", "Generic bearing lubricating oil", "C", "COM", "bulk", "L", "D", "F", "C", False, 14, "FLAM", "LUBRICANT_5Y"),
    ("burner_tip", "Generic fired-heater burner tip", "R", "ENG", "lot", "EA", "V", "S", "B", False, 84, "RACK_WH", "NONE"),
    ("calibration_gas_cylinder", "Generic analyzer calibration gas cylinder", "C", "COM", "lot", "EA", "E", "F", "C", False, 28, "SECURE", "NONE"),
    ("cleaning_solvent", "Generic parts cleaning solvent", "C", "COM", "bulk", "L", "D", "F", "C", False, 21, "FLAM", "ADHESIVE_2Y"),
    ("column_tray_set", "Generic distillation tray set", "R", "LLT", "lot", "SET", "E", "N", "B", False, 224, "YARD", "NONE"),
    ("compressor_spare_rotor", "Generic centrifugal compressor spare rotor", "CAP", "LLT", "serialized", "EA", "V", "N", "A", True, 336, "PRESERVED", "NONE"),
    ("compressor_valve_assembly", "Generic reciprocating compressor valve assembly", "R", "ENG", "lot", "EA", "V", "S", "B", False, 112, "RACK_WH", "NONE"),
    ("control_valve_trim_set", "Generic control valve trim set", "R", "ENG", "lot", "SET", "V", "S", "A", False, 154, "RACK_WH", "NONE"),
    ("cooling_tower_drive_shaft", "Generic cooling tower drive shaft", "R", "ENG", "lot", "EA", "E", "S", "B", False, 98, "RACK_WH", "NONE"),
    ("cooling_tower_fill_media", "Generic cooling tower fill media", "C", "ENG", "bulk", "M2", "E", "S", "C", False, 84, "YARD", "NONE"),
    ("coupling_element", "Generic drive coupling element", "R", "ENG", "lot", "EA", "E", "S", "B", False, 70, "RACK_WH", "NONE"),
    ("dcs_io_card", "Generic control system input/output card", "R", "ENG", "lot", "EA", "V", "S", "A", False, 112, "CLIMATE", "NONE"),
    ("demister_pad", "Generic vessel demister pad", "R", "ENG", "lot", "EA", "E", "N", "B", False, 84, "RACK_WH", "NONE"),
    ("dry_gas_seal_cartridge", "Generic compressor dry gas seal cartridge", "RO", "LLT", "serialized", "EA", "V", "N", "A", True, 224, "CLIMATE", "ELASTOMER_10Y"),
    ("exchanger_gasket_set", "Generic exchanger gasket set", "C", "ENG", "lot", "SET", "E", "S", "B", False, 56, "RACK_WH", "NONE"),
    ("exchanger_tube_bundle", "Generic exchanger tube bundle", "CAP", "LLT", "serialized", "EA", "E", "N", "A", True, 280, "YARD", "NONE"),
    ("exchanger_tube_plug", "Generic exchanger tube plug", "C", "COM", "bulk", "EA", "D", "F", "C", False, 14, "BIN_WH", "NONE"),
    ("fan_drive_gearbox", "Generic fan drive gearbox", "RO", "LLT", "serialized", "EA", "E", "N", "B", False, 196, "RACK_WH", "NONE"),
    ("fastener_set", "Generic stud, nut and mechanical smalls set", "C", "COM", "bulk", "SET", "D", "F", "C", False, 14, "BIN_WH", "NONE"),
    ("filter_cartridge", "Generic filter cartridge element", "C", "COM", "bulk", "EA", "D", "F", "C", False, 21, "BIN_WH", "NONE"),
    ("fin_fan_blade_set", "Generic air-cooler fan blade set", "R", "ENG", "lot", "SET", "E", "S", "B", False, 112, "YARD", "NONE"),
    ("flame_scanner", "Generic combustion flame scanner", "R", "ENG", "lot", "EA", "V", "S", "B", False, 84, "CLIMATE", "NONE"),
    ("flare_pilot_assembly", "Generic flare pilot and igniter assembly", "R", "ENG", "lot", "EA", "V", "S", "B", False, 105, "RACK_WH", "NONE"),
    ("flare_tip", "Generic flare tip", "CAP", "LLT", "lot", "EA", "V", "N", "A", True, 224, "YARD", "NONE"),
    ("floating_roof_seal", "Generic tank floating-roof seal", "R", "ENG", "lot", "M", "E", "N", "B", False, 112, "RACK_WH", "ELASTOMER_5Y"),
    ("gear_oil", "Generic gear lubricating oil", "C", "COM", "bulk", "L", "D", "F", "C", False, 14, "FLAM", "LUBRICANT_5Y"),
    ("instrument_transmitter_spare", "Generic process transmitter", "RO", "ENG", "serialized", "EA", "V", "S", "A", False, 84, "CLIMATE", "NONE"),
    ("mcc_bucket", "Generic motor control centre bucket", "R", "ENG", "lot", "EA", "E", "S", "B", False, 154, "CLIMATE", "NONE"),
    ("mechanical_seal_cartridge", "Generic pump mechanical seal cartridge", "R", "ENG", "lot", "EA", "V", "S", "B", False, 105, "CLIMATE", "ELASTOMER_5Y"),
    ("motor_spare_complete", "Generic complete spare electric motor", "CAP", "LLT", "serialized", "EA", "V", "N", "A", True, 280, "PRESERVED", "NONE"),
    ("o_ring_kit", "Generic elastomer o-ring kit", "C", "COM", "lot", "KIT", "E", "F", "C", False, 21, "CLIMATE", "ELASTOMER_5Y"),
    ("piston_ring_set", "Generic compressor piston ring set", "R", "ENG", "lot", "SET", "V", "S", "B", False, 98, "RACK_WH", "NONE"),
    ("pump_impeller", "Generic pump impeller", "RO", "LLT", "serialized", "EA", "E", "S", "B", False, 196, "RACK_WH", "NONE"),
    ("pump_shaft_sleeve", "Generic pump shaft sleeve", "R", "ENG", "lot", "EA", "D", "S", "C", False, 84, "RACK_WH", "NONE"),
    ("pump_wear_ring_set", "Generic pump wear ring set", "R", "ENG", "lot", "SET", "E", "S", "B", False, 98, "RACK_WH", "NONE"),
    ("refractory_castable", "Generic refractory castable and anchors", "C", "ENG", "bulk", "KG", "E", "S", "C", False, 56, "BIN_WH", "ADHESIVE_2Y"),
    ("relief_valve_complete", "Generic complete pressure relief valve", "RO", "ENG", "serialized", "EA", "V", "N", "A", False, 140, "CLIMATE", "NONE"),
    ("relief_valve_spring_set", "Generic relief valve spring set", "R", "ENG", "lot", "SET", "E", "N", "B", False, 84, "RACK_WH", "NONE"),
    ("rod_packing_set", "Generic compressor rod packing set", "R", "ENG", "lot", "SET", "V", "S", "B", False, 98, "RACK_WH", "NONE"),
    ("seal_support_accumulator", "Generic seal support system accumulator", "R", "ENG", "lot", "EA", "V", "S", "B", False, 84, "RACK_WH", "ELASTOMER_5Y"),
    ("sheet_gasket_stock", "Generic sheet gasket stock", "C", "COM", "bulk", "M2", "D", "F", "C", False, 14, "BIN_WH", "NONE"),
    ("spiral_wound_gasket_set", "Generic spiral wound gasket set", "C", "COM", "lot", "SET", "E", "F", "C", False, 21, "BIN_WH", "NONE"),
    ("thread_compound", "Generic anti-seize thread compound", "C", "COM", "bulk", "KG", "D", "F", "C", False, 21, "FLAM", "ADHESIVE_2Y"),
    ("tilting_pad_thrust_bearing", "Generic tilting pad thrust bearing", "R", "ENG", "lot", "EA", "V", "N", "B", False, 112, "CLIMATE", "NONE"),
    ("tank_breather_valve", "Generic tank breather and pressure-vacuum valve", "R", "ENG", "lot", "EA", "E", "S", "B", False, 70, "RACK_WH", "NONE"),
    ("transformer_bushing", "Generic transformer bushing", "R", "LLT", "lot", "EA", "V", "N", "A", False, 196, "CLIMATE", "NONE"),
    ("trip_solenoid_valve", "Generic trip solenoid valve", "R", "ENG", "lot", "EA", "V", "S", "B", False, 63, "RACK_WH", "NONE"),
    ("ups_battery", "Generic UPS battery block", "C", "ENG", "lot", "EA", "V", "S", "B", False, 70, "CLIMATE", "BATTERY_3Y"),
    ("valve_packing_set", "Generic valve stem packing set", "C", "COM", "lot", "SET", "E", "F", "C", False, 28, "BIN_WH", "NONE"),
    ("valve_positioner", "Generic valve positioner", "R", "ENG", "lot", "EA", "E", "S", "B", False, 98, "CLIMATE", "NONE"),
    ("vfd_power_module", "Generic drive power module", "R", "ENG", "lot", "EA", "V", "S", "A", False, 154, "CLIMATE", "NONE"),
)

#: Fitment, expressed by equipment class because that is how a practitioner
#: reads it. This is the point of the equipment register: the row now says
#: "this seal cartridge fits a centrifugal pump", which is a true sentence,
#: rather than "this seal fits a distillation train", which is not.
_APPLICABILITY_BY_EQUIPMENT_CLASS: dict[str, tuple[str, ...]] = {
    "air_cooled_exchanger": (
        "fin_fan_blade_set", "fan_drive_gearbox", "coupling_element",
        "antifriction_bearing", "exchanger_tube_plug", "gear_oil",
        "fastener_set",
    ),
    "centrifugal_compressor": (
        "dry_gas_seal_cartridge", "compressor_spare_rotor",
        "tilting_pad_thrust_bearing", "coupling_element", "filter_cartridge",
        "bearing_oil", "spiral_wound_gasket_set", "fastener_set",
    ),
    "centrifugal_pump": (
        "mechanical_seal_cartridge", "seal_support_accumulator",
        "pump_impeller", "pump_wear_ring_set", "pump_shaft_sleeve",
        "antifriction_bearing", "coupling_element", "spiral_wound_gasket_set",
        "fastener_set", "bearing_oil",
    ),
    "control_valve": (
        "control_valve_trim_set", "valve_positioner", "actuator_diaphragm",
        "trip_solenoid_valve", "valve_packing_set", "o_ring_kit",
        "sheet_gasket_stock", "fastener_set",
    ),
    "cooling_tower_cell": (
        "cooling_tower_fill_media", "cooling_tower_drive_shaft",
        "fan_drive_gearbox", "fin_fan_blade_set", "gear_oil", "fastener_set",
    ),
    "dcs_sis_controller": ("dcs_io_card", "ups_battery"),
    "distillation_column": (
        "column_tray_set", "demister_pad", "spiral_wound_gasket_set",
        "fastener_set",
    ),
    "electric_motor": (
        "antifriction_bearing", "motor_spare_complete", "coupling_element",
        "bearing_oil", "fastener_set",
    ),
    "fired_heater": (
        "burner_tip", "flame_scanner", "refractory_castable",
        "spiral_wound_gasket_set", "fastener_set",
    ),
    "flare_system": (
        "flare_tip", "flare_pilot_assembly", "flame_scanner", "fastener_set",
    ),
    "instrument_transmitter": (
        "instrument_transmitter_spare", "o_ring_kit", "fastener_set",
    ),
    "mcc_switchgear": ("mcc_bucket", "fastener_set"),
    "pressure_relief_valve": (
        "relief_valve_complete", "relief_valve_spring_set", "o_ring_kit",
        "spiral_wound_gasket_set", "fastener_set",
    ),
    "process_analyzer": (
        "analyzer_module", "analyzer_sample_kit", "calibration_gas_cylinder",
        "filter_cartridge", "o_ring_kit",
    ),
    "reactor_vessel": (
        "spiral_wound_gasket_set", "demister_pad", "fastener_set",
    ),
    "reciprocating_compressor": (
        "compressor_valve_assembly", "piston_ring_set", "rod_packing_set",
        "antifriction_bearing", "coupling_element", "spiral_wound_gasket_set",
        "filter_cartridge", "gear_oil", "fastener_set",
    ),
    "shell_and_tube_exchanger": (
        "exchanger_gasket_set", "exchanger_tube_bundle", "exchanger_tube_plug",
        "sheet_gasket_stock", "fastener_set",
    ),
    "storage_tank": (
        "floating_roof_seal", "tank_breather_valve", "sheet_gasket_stock",
        "fastener_set",
    ),
    "transformer": ("transformer_bushing", "o_ring_kit", "fastener_set"),
    "variable_frequency_drive": (
        "vfd_power_module", "filter_cartridge", "fastener_set",
    ),
}

#: Inverted at import so that a part can be asked what it fits.
_APPLICABILITY_BY_PART: dict[str, tuple[str, ...]] = {}
for _equipment_class, _keys in _APPLICABILITY_BY_EQUIPMENT_CLASS.items():
    for _key in _keys:
        _APPLICABILITY_BY_PART.setdefault(_key, ())
        _APPLICABILITY_BY_PART[_key] = (
            *_APPLICABILITY_BY_PART[_key],
            _equipment_class,
        )
_APPLICABILITY_BY_PART = {
    key: tuple(sorted(value)) for key, value in _APPLICABILITY_BY_PART.items()
}

#: Housekeeping parts that are stocked because the site needs them, not
#: because a particular equipment class calls for them. Kept explicit so the
#: portal can say why a part with no fitment is nonetheless stocked.
SITE_WIDE_PART_KEYS = frozenset({"cleaning_solvent", "thread_compound"})


def fit_spec_code(part_key: str, equipment_class: str) -> str:
    """Fictional fitment specification code.

    This is a label, not an interchange claim. It must never be read as
    asserting that any real part fits any real equipment.
    """
    return "FIT-" + stable_id("f", part_key, equipment_class).split("-", 1)[1][:10].upper()


# ---------------------------------------------------------------------------
# Storeroom locations
# ---------------------------------------------------------------------------


class LocationSpec(NamedTuple):
    """One storeroom location.

    ``site_x_m`` and ``site_y_m`` are ``None`` for off-site locations. That is
    the honest value: an off-site warehouse is not at a coordinate inside the
    fence, and inventing one outside the fence would be a fabricated position
    rather than a missing one.
    """

    code: str
    name: str
    location_type: str
    profile: str
    storage_classes: tuple[str, ...]
    site_x_m: float | None
    site_y_m: float | None
    handling_minutes: float
    latency_class: str
    velocity_zone: str
    id_tech: str
    zone: str | None = None
    aisle: str | None = None
    rack: str | None = None
    shelf: str | None = None
    bin_code: str | None = None
    parent_code: str | None = None
    off_site_lead_hours: float | None = None


#: Location types that do not drive to equipment. Supplier and vendor lead
#: time is procurement, not travel, so they carry no travel-matrix row.
NON_TRAVEL_LOCATION_TYPES = frozenset(
    {"regional_distribution_centre", "vendor_consignment", "repair_vendor", "oem"}
)

#: Eighteen locations. The catalyst store and the sealed-source store from the
#: research are deliberately **absent**: catalyst is capitalised and
#: contractor-handled rather than warehouse stock, and sealed sources are
#: managed under a radiation licence, not by the storeroom. Neither holds a
#: stocked part, so neither is an inventory location.
_LOCATION_ROWS: tuple[LocationSpec, ...] = (
    LocationSpec(
        "DS-WH-MAIN", "Central warehouse", "central_warehouse", "central",
        ("RACK_WH", "BIN_WH"), 480.0, 1090.0, 12.0, "same_shift", "medium",
        "barcode", "A", "01", "R1", "S2", "B04",
    ),
    LocationSpec(
        "DS-WH-CLIM", "Climate-controlled store", "climate_store", "climate",
        ("CLIMATE",), 545.0, 1085.0, 14.0, "same_shift", "medium", "barcode",
        "C", "01", "R1", "S1", "B01", "DS-WH-MAIN",
    ),
    LocationSpec(
        "DS-WH-ROT", "Rotating equipment preservation bay", "preservation_bay",
        "preserved", ("PRESERVED",), 420.0, 1020.0, 45.0, "next_day", "slow",
        "asset_tag", "P", "01", "R1", "S1", "B01", "DS-WH-MAIN",
    ),
    LocationSpec(
        "DS-WH-YARD", "Permanent laydown yard", "laydown_yard", "yard",
        ("YARD",), 250.0, 1000.0, 60.0, "next_day", "slow", "asset_tag",
        "Y", "01", None, None, "L01", "DS-WH-MAIN",
    ),
    LocationSpec(
        "DS-WH-LUBE", "Lubricant store", "lubricant_store", "lubricant",
        ("FLAM",), 415.0, 1085.0, 12.0, "same_shift", "fast", "barcode",
        "L", "01", "R1", "S1", "B01", "DS-WH-MAIN",
    ),
    LocationSpec(
        "DS-WH-FLAM", "Flammable and chemical store", "flammable_store",
        "chemical", ("FLAM",), 330.0, 1060.0, 18.0, "same_shift", "slow",
        "barcode", "F", "01", "R1", "S1", "B01", "DS-WH-MAIN",
    ),
    LocationSpec(
        "DS-WH-CYL", "Gas cylinder cage", "cylinder_cage", "cylinder",
        ("SECURE",), 300.0, 1105.0, 20.0, "same_shift", "slow", "barcode",
        "G", "01", None, None, "C01", "DS-WH-MAIN",
    ),
    LocationSpec(
        "DS-SHOP-MACH", "Machine shop bench stock", "shop_bench",
        "shop_mech", ("BIN_WH", "RACK_WH"), 380.0, 985.0, 6.0,
        "immediate", "fast", "visual", "M", "01", "R1", "S1", "B01",
        "DS-WH-MAIN",
    ),
    LocationSpec(
        "DS-SHOP-IE", "Instrument and electrical shop bench stock",
        "shop_bench", "shop_ie", ("BIN_WH", "CLIMATE"), 470.0, 975.0, 6.0,
        "immediate", "fast", "visual", "E", "01", "R1", "S1", "B01",
        "DS-WH-MAIN",
    ),
    LocationSpec(
        "DS-SHOP-VLV", "Valve shop bench stock", "shop_bench", "shop_valve",
        ("BIN_WH", "RACK_WH"), 555.0, 975.0, 6.0, "immediate", "fast",
        "visual", "V", "01", "R1", "S1", "B01", "DS-WH-MAIN",
    ),
    LocationSpec(
        "DS-CRIB-W", "West satellite crib", "satellite_crib", "crib",
        ("BIN_WH",), 450.0, 400.0, 4.0, "immediate", "fast", "visual",
        "W", "01", None, None, "B01", "DS-WH-MAIN",
    ),
    LocationSpec(
        "DS-CRIB-C", "Central satellite crib", "satellite_crib", "crib",
        ("BIN_WH",), 660.0, 420.0, 4.0, "immediate", "fast", "visual",
        "N", "01", None, None, "B01", "DS-WH-MAIN",
    ),
    LocationSpec(
        "DS-CRIB-E", "East satellite crib", "satellite_crib", "crib",
        ("BIN_WH",), 960.0, 560.0, 4.0, "immediate", "fast", "visual",
        "T", "01", None, None, "B01", "DS-WH-MAIN",
    ),
    LocationSpec(
        "DS-CRIB-U", "Utilities satellite crib", "satellite_crib", "crib",
        ("BIN_WH",), 820.0, 840.0, 4.0, "immediate", "fast", "visual",
        "U", "01", None, None, "B01", "DS-WH-MAIN",
    ),
    LocationSpec(
        "DS-RDC", "Regional distribution centre",
        "regional_distribution_centre", "regional", STORAGE_CLASSES,
        None, None, 90.0, "next_day", "slow", "barcode",
        off_site_lead_hours=8.0,
    ),
    LocationSpec(
        "DS-VMI", "Vendor consignment stock", "vendor_consignment",
        "consignment", STORAGE_CLASSES, None, None, 60.0, "next_day",
        "medium", "barcode", off_site_lead_hours=24.0,
    ),
    LocationSpec(
        "DS-REPAIR", "Repair vendor", "repair_vendor", "repair",
        STORAGE_CLASSES, None, None, 120.0, "multi_day", "slow", "asset_tag",
        off_site_lead_hours=336.0,
    ),
    LocationSpec(
        "DS-OEM", "Original equipment manufacturer", "oem", "supplier",
        STORAGE_CLASSES, None, None, 240.0, "multi_day", "slow", "barcode",
        off_site_lead_hours=1680.0,
    ),
)

#: Opening quantity before the profile factor, by lead band. Long-lead items
#: are held in ones and twos; commodities are held deep.
BASE_QUANTITY_BY_LEAD_BAND: dict[str, int] = {"COM": 24, "ENG": 6, "LLT": 2}

#: Depth multiplier by stocking profile.
TIER_FACTOR_BY_PROFILE: dict[str, float] = {
    "central": 1.0,
    "climate": 0.8,
    "preserved": 0.4,
    "yard": 0.5,
    "lubricant": 1.2,
    "chemical": 0.8,
    "cylinder": 0.6,
    "shop_mech": 0.3,
    "shop_ie": 0.3,
    "shop_valve": 0.3,
    "crib": 0.4,
    "regional": 1.5,
    "consignment": 1.0,
    "repair": 0.3,
    "supplier": 2.0,
}


class PartsBuilder:
    """Builds the six local-only parts families for one downstream run."""

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
        self.equipment: list[dict[str, Any]] = []
        self.present_equipment_classes: set[str] = set()
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

    def _asset_index(self) -> dict[tuple[str, int], dict[str, Any]]:
        """Published assets keyed by area and asset index."""
        return {
            (asset["area_id"], int(asset["asset_index"])): asset
            for asset in self.assets
        }

    # -- phase 1: equipment register -------------------------------------
    def build_equipment_register(self) -> None:
        assets_by_area = self._asset_index()
        area_names = {
            area["area_id"]: area["display_name"] for area in AREA_DEFINITIONS
        }
        for area_id in sorted(_EQUIPMENT_POPULATION):
            if area_id not in area_names:
                raise ValueError(f"equipment population names unknown area {area_id}")
            area_x, area_y = _AREA_SITE_POSITION_M[area_id]
            ordinal = 0
            for equipment_class, count in _EQUIPMENT_POPULATION[area_id]:
                if equipment_class not in EQUIPMENT_CLASSES:
                    raise ValueError(f"unknown equipment class {equipment_class}")
                asset_index = (
                    2 if equipment_class in _SUPPORT_EQUIPMENT_CLASSES else 1
                )
                parent = assets_by_area.get((area_id, asset_index))
                if parent is None:
                    raise ValueError(
                        f"no published asset {asset_index} for area {area_id}"
                    )
                for item in range(1, count + 1):
                    ordinal += 1
                    tag = (
                        f"{area_id}-{EQUIPMENT_CLASS_TAG_LETTER[equipment_class]}"
                        f"-{ordinal:04d}"
                    )
                    # Equipment is scattered around its area centre rather than
                    # stacked on it, so the site plan reads as a plant and the
                    # travel model has something to vary over.
                    offset_x = 60.0 * hash_float(
                        self.seed, "eqx", tag, low=-1.0, high=1.0
                    )
                    offset_y = 45.0 * hash_float(
                        self.seed, "eqy", tag, low=-1.0, high=1.0
                    )
                    site_x = area_x + offset_x
                    site_y = area_y + offset_y
                    grid_east, grid_north = grid_position_km(site_x, site_y)
                    install_year = _INSTALL_YEAR_RANGE[0] + int(
                        (_INSTALL_YEAR_RANGE[1] - _INSTALL_YEAR_RANGE[0] + 1)
                        * hash_float(self.seed, "year", tag, low=0.0, high=0.999)
                    )
                    record = self.catalog_base("equipment_register", tag)
                    record.update(
                        {
                            "equipment_id": stable_id("equip", self.run_id, tag),
                            "equipment_tag": tag,
                            "area_id": area_id,
                            "area_name": area_names[area_id],
                            "parent_asset_id": parent["asset_id"],
                            "parent_asset_type": parent["asset_type"],
                            "parent_asset_index": asset_index,
                            "equipment_class": equipment_class,
                            "equipment_class_label": EQUIPMENT_CLASS_LABELS[
                                equipment_class
                            ],
                            "service_description": (
                                f"{EQUIPMENT_CLASS_LABELS[equipment_class]} in "
                                f"{area_names[area_id]}"
                            ),
                            "is_spared": equipment_class in _SPARED_EQUIPMENT_CLASSES,
                            "is_critical_service": (
                                equipment_class in _CRITICAL_EQUIPMENT_CLASSES
                            ),
                            "install_year_synthetic": install_year,
                            "site_frame": SITE_FRAME,
                            "site_x_m": round6(site_x),
                            "site_y_m": round6(site_y),
                            "grid_frame": GRID_FRAME,
                            "grid_east_km": round6(grid_east),
                            "grid_north_km": round6(grid_north),
                            "register_scope": "local_only_not_a_published_asset",
                            "position_claim": "none",
                        }
                    )
                    self.present_equipment_classes.add(equipment_class)
                    self.equipment.append(record)
                    self.records["equipment_register"].append(record)
        self.records["equipment_register"].sort(key=lambda item: item["equipment_tag"])

    # -- phase 2: part master --------------------------------------------
    def build_part_master(self) -> None:
        keys = [row[0] for row in _PART_ROWS]
        if len(set(keys)) != len(keys):
            raise ValueError("part keys must be unique")
        for row in _PART_ROWS:
            (
                key, name, part_class, lead_band, tracking_mode, uom,
                criticality, movement, abc, insurance, lead_days,
                storage_class, shelf_life_class,
            ) = row
            if storage_class not in STORAGE_CLASSES:
                raise ValueError(f"{key} declares unknown storage class")
            if shelf_life_class not in SHELF_LIFE_CLASSES:
                raise ValueError(f"{key} declares unknown shelf-life class")
            if uom not in UNITS_OF_MEASURE:
                raise ValueError(f"{key} declares unknown unit of measure")
            fits = _APPLICABILITY_BY_PART.get(key, ())
            if not fits and key not in SITE_WIDE_PART_KEYS:
                raise ValueError(
                    f"{key} fits nothing and is not declared site-wide; "
                    "a part that fits nothing and is not housekeeping is a bug"
                )
            record = self.catalog_base("part_master", key)
            record.update(
                {
                    "part_master_id": stable_id("part", self.run_id, key),
                    "stock_code": f"DSC-{key.upper().replace('_', '-')}",
                    "part_key": key,
                    "part_name": name,
                    "part_scope": "downstream",
                    "part_class": part_class,
                    "lead_band": lead_band,
                    "tracking_mode": tracking_mode,
                    "uom": uom,
                    "criticality_class": criticality,
                    "movement_class": movement,
                    "abc_class": abc,
                    "is_insurance_spare": bool(insurance),
                    "nominal_lead_time_days_synthetic": int(lead_days),
                    "required_storage_class": storage_class,
                    "shelf_life_class": shelf_life_class,
                    "shelf_life_years_synthetic": SHELF_LIFE_YEARS_SYNTHETIC.get(
                        shelf_life_class
                    ),
                    "applicable_equipment_classes": list(fits),
                    "is_site_wide": key in SITE_WIDE_PART_KEYS,
                    "classification_provenance": "synthetic_synthesis_not_a_standard",
                    "cost_claim": "none",
                }
            )
            self.parts_by_key[key] = record
            self.records["part_master"].append(record)
        self.records["part_master"].sort(key=lambda item: item["part_key"])

    # -- phase 3: fitment -------------------------------------------------
    def build_part_applicability(self) -> None:
        for key in sorted(_APPLICABILITY_BY_PART):
            part = self.parts_by_key[key]
            for equipment_class in _APPLICABILITY_BY_PART[key]:
                if equipment_class not in self.present_equipment_classes:
                    # A class with no equipment on this site is not fitment.
                    continue
                record = self.catalog_base("part_applicability", key, equipment_class)
                quantity = 1 + int(
                    4
                    * hash_float(
                        self.seed, "quantity", key, equipment_class,
                        low=0.0, high=0.999,
                    )
                )
                record.update(
                    {
                        "applicability_id": stable_id(
                            "applicability", self.run_id, key, equipment_class
                        ),
                        "part_master_id": part["part_master_id"],
                        "part_key": key,
                        "equipment_class": equipment_class,
                        "equipment_class_label": EQUIPMENT_CLASS_LABELS[
                            equipment_class
                        ],
                        "fit_spec_code": fit_spec_code(key, equipment_class),
                        "quantity_per_assembly": quantity,
                        "condition_eligibility": (
                            "either" if part["part_class"] in {"R", "RO"} else "new"
                        ),
                        "fitment_claim": "fictional_not_a_real_interchange_claim",
                    }
                )
                self.records["part_applicability"].append(record)
        self.records["part_applicability"].sort(
            key=lambda item: (item["part_key"], item["equipment_class"])
        )

    # -- phase 4: locations ----------------------------------------------
    def build_storeroom_locations(self) -> None:
        for spec in _LOCATION_ROWS:
            record = self.catalog_base("storeroom_location", spec.code)
            on_site = spec.site_x_m is not None and spec.site_y_m is not None
            if on_site:
                grid_east, grid_north = grid_position_km(spec.site_x_m, spec.site_y_m)
            else:
                grid_east = grid_north = None
            record.update(
                {
                    "location_id": stable_id("location", self.run_id, spec.code),
                    "location_code": spec.code,
                    "location_name": spec.name,
                    "location_type": spec.location_type,
                    "parent_location_id": (
                        stable_id("location", self.run_id, spec.parent_code)
                        if spec.parent_code
                        else None
                    ),
                    "shelf_address": None
                    if spec.zone is None
                    else "-".join(
                        part
                        for part in (
                            spec.code, spec.zone, spec.aisle, spec.rack,
                            spec.shelf, spec.bin_code,
                        )
                        if part is not None
                    ),
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
                    "nominal_handling_minutes_synthetic": round6(
                        spec.handling_minutes
                    ),
                    "is_on_site": on_site,
                    "site_frame": SITE_FRAME if on_site else None,
                    # Null, not a coordinate outside the fence. An off-site
                    # warehouse has no position on the site grid, and inventing
                    # one would be a fabricated position rather than a missing
                    # one.
                    "site_x_m": round6(spec.site_x_m) if on_site else None,
                    "site_y_m": round6(spec.site_y_m) if on_site else None,
                    "grid_frame": GRID_FRAME if on_site else None,
                    "grid_east_km": round6(grid_east) if on_site else None,
                    "grid_north_km": round6(grid_north) if on_site else None,
                    "off_site_lead_hours_synthetic": spec.off_site_lead_hours,
                    "region_code": "fictional-region-1",
                    "position_claim": "none",
                }
            )
            self.locations_by_code[spec.code] = record
            self.records["storeroom_location"].append(record)
        self.records["storeroom_location"].sort(key=lambda item: item["location_code"])

    # -- phase 5: travel --------------------------------------------------
    def build_travel_matrix(self) -> None:
        """Travel from each on-site location to each area.

        The destination is the **area**, not the individual equipment item.
        A crew drives to the unit and walks the last few metres; emitting a row
        per equipment item would multiply the matrix by twenty for a precision
        the model does not have.
        """
        area_names = {
            area["area_id"]: area["display_name"] for area in AREA_DEFINITIONS
        }
        for spec in _LOCATION_ROWS:
            if spec.location_type in NON_TRAVEL_LOCATION_TYPES:
                # Supplier and vendor lead time is procurement, not travel.
                continue
            location = self.locations_by_code[spec.code]
            for area_id in sorted(_AREA_SITE_POSITION_M):
                area_x, area_y = _AREA_SITE_POSITION_M[area_id]
                straight_m = math.hypot(area_x - spec.site_x_m, area_y - spec.site_y_m)
                circuity = hash_float(
                    self.seed,
                    "circuity",
                    spec.code,
                    area_id,
                    low=SITE_CIRCUITY_RANGE[0],
                    high=SITE_CIRCUITY_RANGE[1],
                )
                road_m = straight_m * circuity
                # Out and back, plus the fixed counter and paperwork overhead
                # the location's latency class carries.
                drive_minutes = 2.0 * (road_m / 1000.0) / SITE_SPEED_KMH * 60.0
                minutes = spec.handling_minutes + drive_minutes
                record = self.catalog_base("travel_matrix", spec.code, area_id)
                record.update(
                    {
                        "travel_id": stable_id(
                            "travel", self.run_id, spec.code, area_id
                        ),
                        "from_location_id": location["location_id"],
                        "from_location_code": spec.code,
                        "from_location_type": location["location_type"],
                        "to_area_id": area_id,
                        "to_area_name": area_names[area_id],
                        "straight_line_m_synthetic": round6(straight_m),
                        "road_distance_m_synthetic": round6(road_m),
                        "circuity_factor_synthetic": round6(circuity),
                        "drive_minutes_synthetic": round6(drive_minutes),
                        "handling_minutes_synthetic": round6(spec.handling_minutes),
                        "retrieval_minutes_synthetic": round6(minutes),
                        "assumed_speed_kmh_synthetic": round6(SITE_SPEED_KMH),
                        "transport_mode": "fictional_light_vehicle",
                        "basis": "deterministic_site_geometry_round_trip_plus_handling",
                        "routing_claim": "none",
                    }
                )
                self.records["travel_matrix"].append(record)
        self.records["travel_matrix"].sort(
            key=lambda item: (item["from_location_code"], item["to_area_id"])
        )

    # -- phase 6: opening stock ------------------------------------------
    def admitted_part_keys(self, profile: str) -> list[str]:
        """The exact set of parts a profile stocks. Never a prose description."""
        every = [row[0] for row in _PART_ROWS]

        def storable(keys: list[str], classes: tuple[str, ...]) -> list[str]:
            return [
                key
                for key in keys
                if self.parts_by_key[key]["required_storage_class"] in classes
            ]

        def fits_any(key: str, classes: frozenset[str]) -> bool:
            return bool(
                set(self.parts_by_key[key]["applicable_equipment_classes"]) & classes
            )

        mech_classes = frozenset(
            {
                "centrifugal_pump", "reciprocating_compressor",
                "centrifugal_compressor", "electric_motor",
                "air_cooled_exchanger", "cooling_tower_cell",
                "shell_and_tube_exchanger",
            }
        )
        ie_classes = frozenset(
            {
                "instrument_transmitter", "dcs_sis_controller",
                "process_analyzer", "variable_frequency_drive",
                "mcc_switchgear", "transformer",
            }
        )
        valve_classes = frozenset({"control_valve", "pressure_relief_valve"})

        if profile == "central":
            return sorted(storable(every, ("RACK_WH", "BIN_WH")))
        if profile == "climate":
            return sorted(storable(every, ("CLIMATE",)))
        if profile == "preserved":
            return sorted(storable(every, ("PRESERVED",)))
        if profile == "yard":
            return sorted(storable(every, ("YARD",)))
        if profile == "lubricant":
            return sorted(["bearing_oil", "gear_oil"])
        if profile == "chemical":
            return sorted(["cleaning_solvent", "thread_compound"])
        if profile == "cylinder":
            return sorted(storable(every, ("SECURE",)))
        if profile == "crib":
            # Fast-moving commodity consumables only: the crib exists to save a
            # drive for the parts a technician needs hourly.
            return sorted(
                key
                for key in storable(every, ("BIN_WH",))
                if self.parts_by_key[key]["part_class"] == "C"
                and self.parts_by_key[key]["lead_band"] == "COM"
            )
        if profile == "shop_mech":
            return sorted(
                key
                for key in storable(every, ("BIN_WH", "RACK_WH"))
                if fits_any(key, mech_classes)
            )
        if profile == "shop_ie":
            return sorted(
                key
                for key in storable(every, ("BIN_WH", "CLIMATE"))
                if fits_any(key, ie_classes)
            )
        if profile == "shop_valve":
            return sorted(
                key
                for key in storable(every, ("BIN_WH", "RACK_WH"))
                if fits_any(key, valve_classes)
            )
        if profile == "regional":
            return sorted(
                key for key in every if self.parts_by_key[key]["part_class"] != "CAP"
            )
        if profile == "consignment":
            return sorted(
                key
                for key in every
                if self.parts_by_key[key]["lead_band"] == "COM"
                and self.parts_by_key[key]["movement_class"] == "F"
            )
        if profile == "repair":
            return sorted(
                key
                for key in every
                if self.parts_by_key[key]["part_class"] in {"R", "RO"}
            )
        if profile == "supplier":
            return sorted(
                key for key in every if self.parts_by_key[key]["lead_band"] == "LLT"
            )
        raise ValueError(f"unknown stocking profile: {profile}")

    def is_eligible(self, key: str, spec: LocationSpec) -> bool:
        """Storage guard. Expected to reject nothing; a rejection is a bug."""
        part = self.parts_by_key[key]
        if part["required_storage_class"] not in spec.storage_classes:
            return False
        return bool(part["applicable_equipment_classes"]) or part["is_site_wide"]

    def opening_quantity(self, key: str, spec: LocationSpec) -> int:
        part = self.parts_by_key[key]
        base = BASE_QUANTITY_BY_LEAD_BAND[part["lead_band"]]
        tier = TIER_FACTOR_BY_PROFILE[spec.profile]
        # The explicit bounds are load-bearing: the default range is signed, so
        # an unbounded draw would make this negative and produce zero stock.
        jitter = int(3 * hash_float(self.seed, "qty", spec.code, key, low=0.0, high=0.999))
        quantity = max(1, int(base * tier) + jitter)
        if part["tracking_mode"] == "serialized":
            quantity = min(quantity, 2 if spec.code == "DS-WH-MAIN" else 1)
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
                "location_code": location["location_code"],
                "location_type": location["location_type"],
                "transaction_type": "opening_balance",
                "quantity": int(quantity),
                "uom": part["uom"],
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
            key=lambda item: (
                item["location_code"], item["part_key"], item["txn_sequence"]
            )
        )

    def build(self) -> dict[str, list[dict[str, Any]]]:
        self.build_equipment_register()
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
    """Build every local-only parts family for one downstream run."""
    return PartsBuilder(config, run_id, scenario_id, assets).build()
