"""Deterministic causal simulation for the fictional upstream production profile.

Three layers are kept strictly separate, exactly as the approved research
document requires:

1. **Private physical state** - bounded deliverability, pressure, thermal,
   inventory, utility, restriction and condition states.
2. **Measured state** - synthetic instrument response after noise, drift,
   freeze, calibration hold and de-energized behaviour.
3. **Delivered state** - public observations after latency, clock skew,
   missing, stale, late, duplicate, out-of-order and store-and-forward effects.

Communications faults change delivered evidence only. They never change the
private physical process state.
"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timedelta
from typing import Any

from .catalog import AREA_INDEX, catalog_index, facility_asset
from .common import (
    DELIVERY_STATES,
    FRESHNESS_STATES,
    MAX_DELIVERY_DELAY_SECONDS,
    ORIGIN_STATES,
    QUALITY_CLASSES,
    TIME_QUALITY_FIELDS,
    clamp as _clamp,
    envelope as _envelope,
    hash_float as _hash_float,
    round6 as _round,
    stable_id as _stable_id,
    stamp as _stamp,
)
from .config import derive_scenario_id
from .workflows import build_workflow_records

TWO_PI = math.pi * 2.0
OIL_TANK_CAPACITY_M3 = 220.0
WATER_TANK_CAPACITY_M3 = 260.0
NOMINAL_GAS_KSM3D = 90.0
NOMINAL_OIL_M3D = 260.0

#: Signals owned by the shared OT data systems area. They describe the data
#: system itself, so they are excluded from the private physical signature used
#: to prove that communications scenarios preserve the physical truth.
OT_DIAGNOSTIC_KEYS = ("queue_depth", "latency_ms", "sequence_gap", "clock_offset")

NOISE_SCALE = {
    "kPa": 1.4,
    "degC": 0.25,
    "index": 0.004,
    "kW": 1.1,
    "A": 0.25,
    "m3/d": 0.35,
    "kSm3/d": 0.12,
    "m3": 0.12,
    "V": 1.2,
    "m/s": 0.12,
    "ms": 0.9,
    "count": 0.0,
}
#: Scenario -> (owning/affected areas, short synthetic description).
SCENARIO_AREAS = {
    "normal_production": ("U01", "U02", "U04", "U07"),
    "deliverability_decline": ("U01", "U02", "U11"),
    "liquid_loading": ("U01", "U02", "U04"),
    "slugging": ("U02", "U04"),
    "flowline_restriction": ("U02", "U08"),
    "artificial_lift_degradation": ("U03", "U01"),
    "esp_trip_restart": ("U03", "U09"),
    "separator_upset": ("U04", "U02"),
    "heater_treater_weakness": ("U04", "U07"),
    "compressor_degradation": ("U05", "U11"),
    "produced_water_constraint": ("U06", "U04"),
    "tank_transfer_imbalance": ("U07", "U11"),
    "corrosion_erosion_context": ("U08", "U11"),
    "gas_release_observation": ("U10", "U02"),
    "utility_power_loss": ("U09", "U03", "U05", "U06", "U07"),
    "instrument_drift_stuck": ("U02", "U04", "U11"),
    "communications_degradation": ("U12",),
    "maintenance_inspection_workflow": ("U11", "U10"),
    "flare_vent_environment_workflow": ("U10", "U11"),
}
#: Scenarios whose latent change is not in the physical process layer.
DELIVERY_ONLY_SCENARIOS = ("communications_degradation",)

BASELINE_DELIVERY_BANDS = (
    ("missing", 0.010),
    ("stale", 0.026),
    ("late", 0.048),
    ("store_and_forward", 0.070),
    ("duplicate", 0.086),
    ("out_of_order", 0.100),
)

COMMS_DEGRADED_DELIVERY_BANDS = (
    ("missing", 0.100),
    ("stale", 0.230),
    ("late", 0.360),
    ("store_and_forward", 0.480),
    ("duplicate", 0.580),
    ("out_of_order", 0.650),
)
MEASUREMENT_ONLY_SCENARIOS = ("instrument_drift_stuck",)


def _select_wells(scenario: str, wells: list[dict[str, Any]]) -> list[str]:
    """Deterministically choose the wells carrying a well-scoped latent change."""
    if not wells:
        return []
    ordered = sorted(wells, key=lambda item: item["well_index"])
    gas_wells = [well for well in ordered if well["well_kind"] == "gas"]
    oil_wells = [well for well in ordered if well["well_kind"] == "oil"]
    esp_wells = [well for well in ordered if well["lift_type"] == "esp"]
    lifted = [well for well in ordered if well["lift_type"] != "natural_flow"]
    if scenario == "liquid_loading":
        chosen = gas_wells or ordered
    elif scenario == "slugging":
        chosen = oil_wells or ordered
    elif scenario == "esp_trip_restart":
        chosen = esp_wells or lifted or ordered
    elif scenario == "artificial_lift_degradation":
        chosen = lifted or ordered
    else:
        chosen = ordered
    return [chosen[0]["well_id"]]


def _neutral_modifiers() -> dict[str, float]:
    return {
        "deliverability_scale": 1.0,
        "restriction_add": 0.0,
        "water_fraction_add": 0.0,
        "lift_available": 1.0,
        "lift_efficiency_scale": 1.0,
        "slug_amplitude": 0.0,
        "sep_upset_amplitude": 0.0,
        "treater_duty_scale": 1.0,
        "comp_efficiency_scale": 1.0,
        "comp_vibration_add": 0.0,
        "water_capacity_scale": 1.0,
        "oil_transfer_scale": 1.0,
        "meter_bias": 0.0,
        "utility_scale": 1.0,
        "condition_add": 0.0,
        "detector_add": 0.0,
        "flare_load_add": 0.0,
        "chem_impairment": 0.0,
        "workflow_impairment": 0.0,
        "ot_degradation": 0.0,
    }


def _modifiers(scenario: str, active: bool, progress: float, cycle: float) -> dict[str, float]:
    """Latent scenario modifiers. Bounded, documented synthetic heuristics only."""
    mods = _neutral_modifiers()
    if not active:
        return mods
    if scenario == "normal_production":
        return mods
    if scenario == "deliverability_decline":
        mods["deliverability_scale"] = 1.0 - 0.26 * progress
    elif scenario == "liquid_loading":
        mods["deliverability_scale"] = 1.0 - 0.22 * progress * (0.55 + 0.45 * math.sin(TWO_PI * cycle * 4.0))
        mods["water_fraction_add"] = 0.10 * progress
    elif scenario == "slugging":
        mods["slug_amplitude"] = 0.24 * progress
        mods["sep_upset_amplitude"] = 0.20 * progress
    elif scenario == "flowline_restriction":
        mods["restriction_add"] = 0.46 * progress
    elif scenario == "artificial_lift_degradation":
        mods["lift_efficiency_scale"] = 1.0 - 0.32 * progress
    elif scenario == "esp_trip_restart":
        if progress < 0.30:
            mods["lift_available"] = 0.0
        elif progress < 0.45:
            mods["lift_efficiency_scale"] = 0.45 + 3.6 * (progress - 0.30)
    elif scenario == "separator_upset":
        mods["sep_upset_amplitude"] = 0.34 * progress
    elif scenario == "heater_treater_weakness":
        mods["treater_duty_scale"] = 1.0 - 0.36 * progress
    elif scenario == "compressor_degradation":
        mods["comp_efficiency_scale"] = 1.0 - 0.27 * progress
        mods["comp_vibration_add"] = 0.19 * progress
        mods["condition_add"] = 0.16 * progress
    elif scenario == "produced_water_constraint":
        mods["water_capacity_scale"] = 1.0 - 0.58 * progress
    elif scenario == "tank_transfer_imbalance":
        mods["meter_bias"] = -0.14 * progress
        mods["oil_transfer_scale"] = 1.0 - 0.18 * progress
    elif scenario == "corrosion_erosion_context":
        mods["condition_add"] = 0.34 * progress
        mods["restriction_add"] = 0.06 * progress
    elif scenario == "gas_release_observation":
        mods["detector_add"] = 0.42 * progress * (0.5 + 0.5 * math.sin(TWO_PI * cycle * 6.0))
        mods["flare_load_add"] = 0.10 * progress
    elif scenario == "utility_power_loss":
        mods["utility_scale"] = 0.0 if progress < 0.45 else 1.0
    elif scenario == "instrument_drift_stuck":
        return mods
    elif scenario == "communications_degradation":
        mods["ot_degradation"] = 1.0
    elif scenario == "maintenance_inspection_workflow":
        mods["workflow_impairment"] = 1.0
        mods["chem_impairment"] = 0.24 * progress
    elif scenario == "flare_vent_environment_workflow":
        mods["flare_load_add"] = 0.34 * progress
        mods["detector_add"] = 0.12 * progress
    return mods


def _physical_series(
    config: dict[str, Any],
    wells: list[dict[str, Any]],
    scenario: str,
    start: datetime,
    end: datetime,
    interval: int,
    onset: datetime,
) -> list[dict[str, Any]]:
    """Integrate the bounded private physical state across the half-open window."""
    tick_count = int((end - start).total_seconds()) // interval
    ordered_wells = sorted(wells, key=lambda item: item["well_index"])
    target_well_ids = frozenset(_select_wells(scenario, ordered_wells))
    onset_span = max((end - onset).total_seconds(), float(interval))

    treater_temp = 58.0
    oil_inventory = OIL_TANK_CAPACITY_M3 * 0.40
    water_inventory = WATER_TANK_CAPACITY_M3 * 0.36
    handoff_total = 0.0
    gas_handoff_total = 0.0
    series: list[dict[str, Any]] = []

    for tick in range(tick_count):
        moment = start + timedelta(seconds=tick * interval)
        fraction = tick / max(tick_count - 1, 1)
        active = moment >= onset
        progress = _clamp((moment - onset).total_seconds() / onset_span, 0.0, 1.0) if active else 0.0
        mods = _modifiers(scenario, active, progress, fraction)
        seconds_of_day = moment.hour * 3600 + moment.minute * 60 + moment.second
        day_fraction = seconds_of_day / 86400.0
        ambient = 16.0 + 10.0 * math.sin(TWO_PI * (day_fraction - 0.25))
        wind_speed = 3.4 + 1.9 * math.sin(TWO_PI * (day_fraction * 1.3 + 0.12))
        utility = mods["utility_scale"]

        well_states: dict[str, dict[str, float]] = {}
        oil_in = water_in = gas_in = lift_current_total = 0.0
        restriction_total = 0.0
        for well in ordered_wells:
            targeted = well["well_id"] in target_well_ids
            local = mods if targeted else _neutral_modifiers()
            if not targeted:
                local["utility_scale"] = utility
            phase = _hash_float(config["seed"], well["well_id"], "phase", low=0.0, high=TWO_PI)
            diurnal = 0.025 * math.sin(TWO_PI * fraction + phase)
            deliverability = _clamp(
                well["base_deliverability_index"] * (1.0 + diurnal) * local["deliverability_scale"],
                0.0,
                1.4,
            )
            restriction = _clamp(
                0.06 + 0.04 * _hash_float(config["seed"], well["well_id"], "restriction", low=0.0, high=1.0)
                + local["restriction_add"],
                0.0,
                0.9,
            )
            lift_efficiency = _clamp(local["lift_efficiency_scale"], 0.0, 1.2)
            natural_flow = well["lift_type"] == "natural_flow"
            lift_available = 1.0 if natural_flow else local["lift_available"]
            lift_run = 1.0 if (lift_available > 0.0 and utility > 0.0) or natural_flow and utility > 0.0 else 0.0
            slug = local["slug_amplitude"] * math.sin(TWO_PI * fraction * 9.0 + phase)
            flow_scale = _clamp(
                deliverability * (1.0 - 0.35 * restriction) * (0.86 + 0.14 * lift_efficiency) * (1.0 + slug),
                0.0,
                2.0,
            )
            if lift_run == 0.0:
                flow_scale = 0.0
            q_total = 95.0 * flow_scale
            water_fraction = _clamp(well["nominal_water_fraction"] + local["water_fraction_add"], 0.0, 0.95)
            q_water = q_total * water_fraction
            q_oil = q_total - q_water
            q_gas = q_total * well["nominal_gas_ratio_index"] * (0.9 + 0.2 * deliverability) / 10.0
            shut_in = flow_scale <= 0.0
            p_tubing = 220.0 + 3400.0 * deliverability * (1.15 if shut_in else (1.0 - 0.30 * restriction))
            p_casing = p_tubing * 1.15 + 45.0 + (120.0 if well["lift_type"] == "gas_lift" else 0.0)
            p_flowline = 0.0 if shut_in else max(120.0, p_tubing * 0.28 * (1.0 - 0.40 * restriction))
            lift_load = 0.0 if lift_run == 0.0 else _clamp(0.55 + 0.30 * deliverability * lift_efficiency - 0.22 * (1.0 - lift_efficiency), 0.0, 1.2)
            lift_current = 0.0 if lift_run == 0.0 else 18.0 + 42.0 * lift_load
            lift_cycle = 0.0 if lift_run == 0.0 else _clamp(0.90 * lift_efficiency + 0.05 * math.sin(TWO_PI * fraction * 7.0 + phase), 0.0, 1.2)
            well_states[well["well_id"]] = {
                "well_online": 0.0 if shut_in else 1.0,
                "p_tubing": p_tubing,
                "p_casing": p_casing,
                "t_wellhead": 0.0 if shut_in else ambient + 12.0 + 22.0 * deliverability,
                "choke_class": 0.0 if shut_in else 3.0,
                "p_flowline": p_flowline,
                "t_flowline": 0.0 if shut_in else ambient + 6.0 + 14.0 * deliverability,
                "restriction": restriction,
                "flow_state": 0.0 if shut_in else 1.0,
                "lift_run": lift_run,
                "lift_load": lift_load,
                "lift_current": lift_current,
                "lift_cycle": lift_cycle,
                "q_oil": q_oil,
                "q_water": q_water,
                "q_gas": q_gas,
                "deliverability": deliverability,
            }
            oil_in += q_oil
            water_in += q_water
            gas_in += q_gas
            lift_current_total += lift_current
            restriction_total += restriction

        well_count = max(len(ordered_wells), 1)
        average_restriction = restriction_total / well_count
        sep_upset = mods["sep_upset_amplitude"] * math.sin(TWO_PI * fraction * 11.0)
        treater_target = ambient + 34.0 + 18.0 * mods["treater_duty_scale"]
        treater_temp += (treater_target - treater_temp) * 0.18
        treater_duty = _clamp(0.72 * mods["treater_duty_scale"], 0.0, 1.2) * utility
        oil_rate = oil_in * (0.94 + 0.05 * mods["treater_duty_scale"]) * utility
        water_rate = water_in * utility
        gas_rate = gas_in * utility

        water_capacity = water_in * 1.35 * mods["water_capacity_scale"] * utility
        water_level_before = water_inventory / WATER_TANK_CAPACITY_M3
        water_pump_run = 1.0 if water_level_before > 0.30 and water_capacity > 0.0 else 0.0
        water_transfer_rate = min(water_capacity, water_rate * 1.15) if water_pump_run > 0.0 else 0.0
        water_inventory = _clamp(
            water_inventory + (water_rate - water_transfer_rate) * interval / 86400.0,
            0.0,
            WATER_TANK_CAPACITY_M3,
        )

        oil_capacity = NOMINAL_OIL_M3D * mods["oil_transfer_scale"] * utility
        oil_level_before = oil_inventory / OIL_TANK_CAPACITY_M3
        transfer_pump_run = 1.0 if oil_level_before > 0.34 and oil_capacity > 0.0 else 0.0
        oil_transfer_rate = min(oil_capacity, oil_rate * 1.2) if transfer_pump_run > 0.0 else 0.0
        oil_inventory = _clamp(
            oil_inventory + (oil_rate - oil_transfer_rate) * interval / 86400.0,
            0.0,
            OIL_TANK_CAPACITY_M3,
        )
        handoff_rate = (oil_transfer_rate * (1.0 + mods["meter_bias"])) / NOMINAL_OIL_M3D
        handoff_total += handoff_rate * interval / 86400.0
        gas_handoff_rate = gas_rate / NOMINAL_GAS_KSM3D
        gas_handoff_total += gas_handoff_rate * interval / 86400.0

        comp_running = utility > 0.0 and gas_rate > 0.0
        comp_suction = 300.0 + 18.0 * (gas_rate / NOMINAL_GAS_KSM3D) if comp_running else 105.0
        comp_efficiency = _clamp(mods["comp_efficiency_scale"], 0.35, 1.2)
        comp_discharge = (comp_suction * 3.4 + 180.0) * comp_efficiency if comp_running else comp_suction
        comp_power = 0.0 if not comp_running else (140.0 + 26.0 * (gas_rate / NOMINAL_GAS_KSM3D) * 10.0) / comp_efficiency
        comp_vibration = 0.0 if not comp_running else _clamp(0.22 + 0.04 * math.sin(TWO_PI * fraction * 5.0) + mods["comp_vibration_add"], 0.0, 1.5)
        gen_power = 0.0 if utility <= 0.0 else 210.0 + comp_power * 0.55 + lift_current_total * 0.4
        ot_degraded = mods["ot_degradation"]

        facility_state = {
            "sep_pressure": 320.0 + 26.0 * (gas_rate / NOMINAL_GAS_KSM3D) + 40.0 * sep_upset,
            "sep_oil_level": _clamp(0.48 + 0.10 * math.sin(TWO_PI * fraction * 3.0) + 0.35 * sep_upset, 0.02, 0.98),
            "sep_water_level": _clamp(0.42 + 0.08 * math.cos(TWO_PI * fraction * 3.0) + 0.30 * sep_upset, 0.02, 0.98),
            "gas_rate": gas_rate,
            "water_rate": water_rate,
            "fwko_level": _clamp(0.45 + 0.07 * math.sin(TWO_PI * fraction * 2.5) + 0.20 * sep_upset, 0.02, 0.98),
            "treater_temp": treater_temp,
            "treater_duty": treater_duty,
            "oil_rate": oil_rate,
            "comp_suction": comp_suction,
            "comp_discharge": comp_discharge,
            "comp_power": comp_power,
            "comp_vibration": comp_vibration,
            "comp_mode": 2.0 if comp_running else 0.0,
            "dehy_quality": _clamp(0.88 * comp_efficiency - 0.10 * mods["condition_add"], 0.0, 1.0) * (1.0 if comp_running else 0.0),
            "dehy_circulation": _clamp(0.70 * comp_efficiency, 0.0, 1.2) * (1.0 if comp_running else 0.0),
            "water_tank_level": water_inventory / WATER_TANK_CAPACITY_M3,
            "water_inventory": water_inventory,
            "water_pump_run": water_pump_run,
            "water_transfer_rate": water_transfer_rate,
            "reinjection_route": 1.0 if water_pump_run > 0.0 else 0.0,
            "oil_tank_level": oil_inventory / OIL_TANK_CAPACITY_M3,
            "oil_inventory": oil_inventory,
            "oil_tank_temp": treater_temp * 0.55 + ambient * 0.45,
            "transfer_pump_run": transfer_pump_run,
            "oil_transfer_rate": oil_transfer_rate,
            "handoff_rate": handoff_rate,
            "handoff_total": handoff_total,
            "handoff_status": 2.0 if transfer_pump_run > 0.0 else 1.0,
            "gas_handoff_rate": gas_handoff_rate,
            "gas_handoff_total": gas_handoff_total,
            "gas_handoff_status": 2.0 if comp_running else 1.0,
            "chem_inventory": _clamp(0.85 - 0.10 * fraction - mods["chem_impairment"], 0.0, 1.0),
            "chem_injection": _clamp(0.70 * (1.0 - mods["chem_impairment"]), 0.0, 1.2) * utility,
            "solids_index": _clamp(0.12 + 0.50 * average_restriction + 0.50 * mods["condition_add"], 0.0, 1.5),
            "dp_index": _clamp(0.18 + 0.90 * average_restriction, 0.0, 1.5),
            "gen_power": gen_power,
            "gen_voltage": 0.0 if utility <= 0.0 else 480.0 - 6.0 * math.sin(TWO_PI * fraction * 4.0),
            "gen_availability": utility,
            "fuel_gas_pressure": 620.0 - 40.0 * (1.0 - utility),
            "air_pressure": 690.0 * utility + 90.0 * (1.0 - utility),
            "flare_route": 2.0 if mods["flare_load_add"] > 0.15 else 1.0,
            "flare_load": _clamp(0.12 + 0.04 * math.sin(TWO_PI * fraction * 2.0) + mods["flare_load_add"], 0.0, 1.5),
            "detector_availability": 0.5 if mods["workflow_impairment"] > 0.0 else 1.0,
            "detector_observation": _clamp(0.05 + 0.02 * math.sin(TWO_PI * fraction * 3.0) + mods["detector_add"], 0.0, 1.0),
            "ambient_temp": ambient,
            "wind_speed": wind_speed,
            "test_state": 1.0 if 0.30 <= fraction < 0.55 else 0.0,
            "test_rate": (oil_in / well_count) / 95.0 if 0.30 <= fraction < 0.55 else 0.0,
            "sample_workflow": 1.0 if 0.60 <= fraction < 0.72 else 0.0,
            "maintenance_state": 1.0 if mods["workflow_impairment"] > 0.0 else 0.0,
            "queue_depth": 24.0 * ot_degraded * (1.0 if active else 0.0),
            "latency_ms": 45.0 + 12.0 * math.sin(TWO_PI * fraction * 6.0) + 900.0 * ot_degraded * (1.0 if active else 0.0),
            "sequence_gap": 1.0 if ot_degraded > 0.0 and active else 0.0,
            "clock_offset": 4.0 + 2.0 * math.sin(TWO_PI * fraction * 2.0) + 42.0 * ot_degraded * (1.0 if active else 0.0),
        }
        series.append(
            {
                "tick": tick,
                "time": moment,
                "fraction": fraction,
                "progress": progress,
                "active": active,
                "wells": well_states,
                "facility": facility_state,
                "modifiers": mods,
                "target_well_ids": sorted(target_well_ids),
                "ambient_c": ambient,
            }
        )
    return series


def _physical_signature(series: list[dict[str, Any]]) -> str:
    """Hash the private physical process state, excluding OT data-system state."""
    digest = hashlib.sha256()
    for frame in series:
        for well_id in sorted(frame["wells"]):
            state = frame["wells"][well_id]
            for key in sorted(state):
                digest.update(f"{well_id}|{key}|{_round(state[key]):.6f}|".encode("utf-8"))
        facility = frame["facility"]
        for key in sorted(facility):
            if key in OT_DIAGNOSTIC_KEYS:
                continue
            digest.update(f"facility|{key}|{_round(facility[key]):.6f}|".encode("utf-8"))
    return digest.hexdigest()


def _drift_signal_ids(signals: list[dict[str, Any]]) -> tuple[frozenset[str], frozenset[str]]:
    candidates = sorted(
        signal["signal_id"]
        for signal in signals
        if signal["measurement"] in {"tubing_pressure_kpa", "separator_pressure_kpa", "treater_temperature_c", "flowline_pressure_kpa"}
    )
    drifting = frozenset(candidates[0::2])
    frozen = frozenset(candidates[1::2])
    return drifting, frozen


def _build_telemetry(
    config: dict[str, Any],
    run_id: str,
    scenario_id: str,
    scenario: str,
    catalog: dict[str, list[dict[str, Any]]],
    series: list[dict[str, Any]],
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    site_id = config["site"]["site_id"]
    seed = config["seed"]
    measurement_effects = config["fidelity"]["measurement_effects"]
    delivery_effects = config["fidelity"]["delivery_effects"]
    drifting_ids, frozen_ids = _drift_signal_ids(catalog["signals"])
    latest_public = end - timedelta(seconds=1)
    latest_delivery = end + timedelta(seconds=MAX_DELIVERY_DELAY_SECONDS - 1)

    def observed(value: datetime) -> str:
        return _stamp(min(max(value, start), latest_public))

    def delivered(value: datetime) -> str:
        return _stamp(min(max(value, start), latest_delivery))

    telemetry: list[dict[str, Any]] = []
    ordered_signals = sorted(catalog["signals"], key=lambda item: (item["signal_index"], item["signal_id"]))
    for signal in ordered_signals:
        multiple = signal["cadence_multiple"]
        scope = signal["state_scope"]
        state_key = signal["state_key"]
        noise_scale = NOISE_SCALE[signal["unit"]]
        signal_records: list[dict[str, Any]] = []
        previous: dict[str, Any] | None = None
        last_measured = 0.0
        sequence = 0
        for frame in series:
            if frame["tick"] % multiple != 0:
                continue
            sequence += 1
            if scope == "well":
                well_id = signal["well_id"]
                raw = frame["wells"][well_id][state_key]
            else:
                well_id = None
                raw = frame["facility"][state_key]
            utility_off = frame["facility"]["gen_availability"] <= 0.0

            origin_state = "measured"
            quality_reason = "nominal"
            if raw == 0.0:
                measured = 0.0
                origin_state = "deenergized" if utility_off else "process_off"
                quality_reason = "physical_zero_supported_by_process_state"
            else:
                noise = _hash_float(seed, signal["signal_id"], str(sequence), low=-noise_scale, high=noise_scale)
                measured = raw + (noise if measurement_effects else 0.0)
                if measurement_effects and scenario == "instrument_drift_stuck" and frame["active"]:
                    if signal["signal_id"] in frozen_ids and previous is not None:
                        measured = last_measured
                        origin_state = "frozen"
                        quality_reason = "instrument_value_frozen"
                    elif signal["signal_id"] in drifting_ids:
                        measured = measured * (1.0 + 0.11 * frame["progress"])
                        origin_state = "drifting"
                        quality_reason = "instrument_drift_suspected"
            if signal["unit"] == "count":
                measured = float(int(round(measured)))
            last_measured = measured

            scheduled = frame["time"]
            latency = 1.5 + _hash_float(seed, signal["signal_id"], str(sequence), "latency", low=0.0, high=4.0)
            quality_class = "good" if origin_state != "drifting" else "uncertain"
            if origin_state == "frozen":
                quality_class = "uncertain"
            freshness_state = "fresh"
            delivery_state = "delivered"
            source_time = scheduled
            value: Any = _round(measured)
            clock_uncertainty = 3.0
            clock_offset = _round(
                4.0
                + AREA_INDEX[signal["area_id"]] * 0.35
                + _hash_float(seed, signal["signal_id"], "clock", low=-0.75, high=0.75)
            )
            emit_duplicate = False
            mark_out_of_order = False
            comms_active = (
                delivery_effects
                and scenario == "communications_degradation"
                and frame["active"]
            )
            if delivery_effects:
                if comms_active:
                    clock_offset = _round(clock_offset + 40.0)
                    bands = COMMS_DEGRADED_DELIVERY_BANDS
                else:
                    bands = BASELINE_DELIVERY_BANDS
                bucket = _hash_float(
                    seed, signal["signal_id"], str(sequence), "delivery", low=0.0, high=1.0
                )
                effect = ""
                for name, threshold in bands:
                    if bucket < threshold:
                        effect = name
                        break
                if effect == "stale" and (previous is None or previous["value"] is None):
                    effect = "late"
                if effect == "missing":
                    value = None
                    delivery_state = "missing"
                    quality_class = "bad"
                    quality_reason = "communications_path_unavailable"
                    freshness_state = "unknown"
                    origin_state = "measured"
                    latency = 180.0
                    clock_uncertainty = 32.0
                elif effect == "stale":
                    value = previous["value"]
                    source_time = datetime.fromisoformat(previous["source_time_utc"][:-1] + "+00:00")
                    delivery_state = "stale"
                    quality_class = "uncertain"
                    quality_reason = "stale_repeat_of_prior_sample"
                    freshness_state = "stale"
                    origin_state = "substituted"
                    latency = 95.0
                    clock_uncertainty = 26.0
                elif effect == "late":
                    delivery_state = "late"
                    quality_class = "uncertain"
                    quality_reason = "late_delivery"
                    freshness_state = "aging"
                    latency = 120.0
                    clock_uncertainty = 22.0
                elif effect == "store_and_forward":
                    delivery_state = "store_and_forward"
                    quality_reason = "store_and_forward_replay"
                    freshness_state = "aging"
                    latency = 75.0
                    clock_uncertainty = 18.0
                elif effect == "duplicate":
                    emit_duplicate = True
                elif effect == "out_of_order":
                    mark_out_of_order = True

            server_time = scheduled + timedelta(seconds=latency * 0.6)
            ingest_time = scheduled + timedelta(seconds=latency)
            record = {
                "schema_version": "1.0",
                "run_id": run_id,
                "scenario_id": scenario_id,
                "site_id": site_id,
                "area_id": signal["area_id"],
                "record_type": "telemetry",
                "record_id": _stable_id("telemetry", run_id, signal["signal_id"], _stamp(scheduled)),
                "well_id": well_id,
                "asset_id": signal["asset_id"],
                "device_id": signal["device_id"],
                "signal_id": signal["signal_id"],
                "measurement": signal["measurement"],
                "unit": signal["unit"],
                "value": value,
                "cadence_seconds": signal["cadence_seconds"],
                "sequence": sequence,
                "duplicate_of_record_id": None,
                "fidelity": signal["fidelity"],
                **_envelope(
                    event_time=observed(scheduled),
                    source_time=observed(source_time),
                    server_time=delivered(server_time),
                    ingest_time=delivered(ingest_time),
                    quality_class=quality_class,
                    quality_reason=quality_reason,
                    freshness_state=freshness_state,
                    origin_state=origin_state,
                    delivery_state="out_of_order" if mark_out_of_order else delivery_state,
                    clock_offset_ms=clock_offset,
                    clock_uncertainty_ms=clock_uncertainty,
                ),
                "is_synthetic": True,
                "operational_use": "prohibited",
            }
            if mark_out_of_order:
                record["quality_reason"] = "out_of_order_delivery"
                record["freshness_state"] = "aging"
                record["quality_class"] = "uncertain"
            signal_records.append(record)
            previous = record
            if emit_duplicate:
                duplicate = dict(record)
                duplicate["record_id"] = _stable_id(
                    "telemetry", run_id, signal["signal_id"], _stamp(scheduled), "duplicate"
                )
                duplicate["duplicate_of_record_id"] = record["record_id"]
                duplicate["delivery_state"] = "duplicate"
                duplicate["quality_class"] = "uncertain"
                duplicate["quality_reason"] = "duplicate_delivery"
                duplicate["freshness_state"] = "aging"
                signal_records.append(duplicate)
        for index, record in enumerate(signal_records):
            if record["delivery_state"] == "out_of_order" and index > 0:
                signal_records[index - 1], signal_records[index] = (
                    signal_records[index],
                    signal_records[index - 1],
                )
        telemetry.extend(signal_records)
    return telemetry


def simulate(
    config: dict[str, Any],
    run_id: str,
    catalog: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Run the physical, measurement, delivery and sparse-evidence layers.

    Returns a flat mapping with ``scenario_id``, run metadata, one list per
    runtime record family (``telemetry``, every sparse workflow family, and the
    private ``ground_truth``), plus ``record_counts``.
    """
    scenario = config["scenario"]
    scenario_id = derive_scenario_id(run_id, scenario)
    start = datetime.fromisoformat(config["simulation"]["start_utc"][:-1] + "+00:00")
    end = datetime.fromisoformat(config["simulation"]["end_utc"][:-1] + "+00:00")
    interval = config["simulation"]["interval_seconds"]
    duration_seconds = int((end - start).total_seconds())
    onset_offset = max(interval, (duration_seconds // 3) // interval * interval)
    onset = start + timedelta(seconds=min(onset_offset, duration_seconds - interval))

    series = _physical_series(config, catalog["wells"], scenario, start, end, interval, onset)
    telemetry = _build_telemetry(config, run_id, scenario_id, scenario, catalog, series, start, end)
    signature = _physical_signature(series)

    context = {
        "config": config,
        "run_id": run_id,
        "scenario": scenario,
        "scenario_id": scenario_id,
        "catalog": catalog,
        "index": catalog_index(catalog),
        "series": series,
        "telemetry": telemetry,
        "start": start,
        "end": end,
        "onset": onset,
        "interval": interval,
        "physical_signature": signature,
        "scenario_areas": SCENARIO_AREAS[scenario],
        "delivery_only": scenario in DELIVERY_ONLY_SCENARIOS,
        "measurement_only": scenario in MEASUREMENT_ONLY_SCENARIOS,
        "facility_asset": lambda asset_type: facility_asset(catalog, asset_type),
        "max_delivery_delay_seconds": MAX_DELIVERY_DELAY_SECONDS,
    }
    workflows = build_workflow_records(context)

    result: dict[str, Any] = {
        "scenario_id": scenario_id,
        "onset_utc": _stamp(onset),
        "tick_count": len(series),
        "physical_signature": signature,
        "telemetry": telemetry,
    }
    result.update(workflows)
    result["record_counts"] = {
        key: len(value) for key, value in sorted(result.items()) if isinstance(value, list)
    }
    return result


__all__ = [
    "DELIVERY_ONLY_SCENARIOS",
    "DELIVERY_STATES",
    "FRESHNESS_STATES",
    "MAX_DELIVERY_DELAY_SECONDS",
    "MEASUREMENT_ONLY_SCENARIOS",
    "ORIGIN_STATES",
    "OT_DIAGNOSTIC_KEYS",
    "QUALITY_CLASSES",
    "SCENARIO_AREAS",
    "TIME_QUALITY_FIELDS",
    "simulate",
]
