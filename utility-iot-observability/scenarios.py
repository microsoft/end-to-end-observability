"""
Utility IoT Scenario Engine — Tier 2

Injects labeled, correlated, multi-asset failure scenarios into the
synthetic telemetry produced by `generate_synthetic_data.py`. The
engine is a stateless set of pure functions of (scenario, time) ->
ScenarioEffects that device `_step` methods consult while walking
their AR(1) signals.

Design contracts (see `plan.md`):
  * scenarios are scheduled once at run start using an independent
    scenario RNG seeded from the root seed, so adding/removing
    scenarios never shifts the per-device RNG streams.
  * handlers are deterministic given the scenario params + current
    time; they do not draw from any RNG during the sampling loop.
  * effects are composed via a typed ScenarioEffects dataclass with
    explicit merge semantics.
  * telemetry records that belong to an active-scenario device carry
    `scenario_ids` (active presence).  Only events whose `event_type`
    is listed in the caused_event_types of an active scenario are
    attributed to it (causation, not presence).

Tier 2 launch scenario catalogue:
  storm.outage_cluster         — correlated voltage sag + meter outages
  transformer.incipient_dga    — slow hydrogen/methane/acetylene ramp
  regulator.hunting            — outlet psi oscillation + relief valve
  ami.tamper_campaign          — cluster tamper + demand drop
  compressor.degradation       — slow vibration + discharge temp ramp
"""

from __future__ import annotations

import json
import math
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class Scenario:
    scenario_id: str
    scenario_type: str
    severity: str  # LOW | MEDIUM | HIGH | CRITICAL
    start_time: datetime
    end_time: datetime
    affected_device_ids: List[str]
    params: Dict[str, Any] = field(default_factory=dict)
    label: Dict[str, Any] = field(default_factory=dict)
    # bookkeeping for clipped scenarios that physically extended outside
    # the run window; the effective [start_time, end_time] is clipped.
    original_start_time: Optional[datetime] = None
    original_end_time: Optional[datetime] = None
    truncated: bool = False

    def is_active(self, t: datetime) -> bool:
        return self.start_time <= t <= self.end_time

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "scenario_type": self.scenario_type,
            "severity": self.severity,
            "start_time": _iso_utc(self.start_time),
            "end_time": _iso_utc(self.end_time),
            "original_start_time": _iso_utc(self.original_start_time) if self.original_start_time else None,
            "original_end_time": _iso_utc(self.original_end_time) if self.original_end_time else None,
            "truncated": self.truncated,
            "affected_device_ids": list(self.affected_device_ids),
            "params": self.params,
            "label": self.label,
        }


@dataclass
class ScenarioEffects:
    """Typed, composable scenario effects applied to a single device/sample.

    Merge semantics (applied in scenario-id-sorted order):
      * target_overrides:  last-writer-wins per key
      * sigma_multipliers: multiplied together per key
      * additive_deltas:   summed per key
      * forced_flags:      last-writer-wins per key
      * caused_event_types: union
      * active_scenario_ids: concatenated (unique, preserving order)
    """

    target_overrides: Dict[str, float] = field(default_factory=dict)
    sigma_multipliers: Dict[str, float] = field(default_factory=dict)
    additive_deltas: Dict[str, float] = field(default_factory=dict)
    forced_flags: Dict[str, Any] = field(default_factory=dict)
    caused_event_types: Set[str] = field(default_factory=set)
    active_scenario_ids: List[str] = field(default_factory=list)

    def target(self, key: str, default: float) -> float:
        if key in self.target_overrides:
            return self.target_overrides[key]
        return default + self.additive_deltas.get(key, 0.0)

    def sigma_mult(self, key: str, default_sigma: float) -> float:
        return default_sigma * self.sigma_multipliers.get(key, 1.0)

    def forced(self, key: str) -> Tuple[bool, Any]:
        if key in self.forced_flags:
            return True, self.forced_flags[key]
        return False, None

    def caused(self, event_type: str) -> bool:
        return event_type in self.caused_event_types

    def has_scenarios(self) -> bool:
        return bool(self.active_scenario_ids)


# ---------------------------------------------------------------------------
# Handler registry
# ---------------------------------------------------------------------------
# A handler takes (scenario, device_id, device_type, asset, t, effects)
# and mutates effects in place.  Handlers must be deterministic given
# (scenario.params, t) — they do not draw from any RNG.
Handler = Callable[[Scenario, str, str, Dict[str, Any], datetime, ScenarioEffects], None]

HANDLERS: Dict[str, Handler] = {}


def register(scenario_type: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[scenario_type] = fn
        return fn
    return deco


def apply_scenarios(scenarios: Iterable[Scenario], device_id: str,
                    device_type: str, asset: Dict[str, Any],
                    t: datetime) -> ScenarioEffects:
    """Compose effects from all scenarios active on this device at time t."""
    effects = ScenarioEffects()
    # Sort for deterministic last-writer-wins ordering.
    for s in sorted(scenarios, key=lambda sc: sc.scenario_id):
        if device_id not in s.affected_device_ids:
            continue
        if not s.is_active(t):
            continue
        effects.active_scenario_ids.append(s.scenario_id)
        handler = HANDLERS.get(s.scenario_type)
        if handler is None:
            continue
        handler(s, device_id, device_type, asset, t, effects)
    return effects


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _iso_utc(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _progress(s: Scenario, t: datetime) -> float:
    """Linear [0, 1] progress of a scenario at time t (clamped)."""
    total = (s.end_time - s.start_time).total_seconds()
    if total <= 0:
        return 1.0
    elapsed = (t - s.start_time).total_seconds()
    return max(0.0, min(1.0, elapsed / total))


def _device_region(device_id: str) -> str:
    """Extract the 3-letter region code from a device id like MTR_ALB_001."""
    parts = device_id.split("_")
    if len(parts) >= 2 and len(parts[1]) == 3:
        return parts[1]
    return "UNK"


# ---------------------------------------------------------------------------
# Handler: storm.outage_cluster
# ---------------------------------------------------------------------------
@register("storm.outage_cluster")
def _storm_handler(scenario: Scenario, device_id: str, device_type: str,
                   asset: Dict[str, Any], t: datetime,
                   effects: ScenarioEffects) -> None:
    p = _progress(scenario, t)
    sag_peak = scenario.params.get("sag_peak_pct", 0.10)
    # Triangular profile: 0 -> peak at 0.5 -> 0 at end.
    sag = sag_peak * (1.0 - abs(2.0 * p - 1.0))

    nominal_v = 120.5
    sagged = nominal_v * (1.0 - sag)

    if device_type == "smart_meter":
        effects.target_overrides["voltage_a"] = sagged
        effects.target_overrides["voltage_b"] = sagged
        effects.target_overrides["voltage_c"] = sagged
        effects.sigma_multipliers["voltage_a"] = 3.0
        effects.sigma_multipliers["voltage_b"] = 3.0
        effects.sigma_multipliers["voltage_c"] = 3.0
        effects.sigma_multipliers["frequency_hz"] = 2.5
        # During deep sag, meter fires outage flag.
        if sag >= sag_peak * 0.7 and device_id in scenario.params.get("outage_meters", []):
            effects.forced_flags["outage_flag"] = True
        effects.caused_event_types.update({"ami.last_gasp", "ami.power_restored"})
    elif device_type == "transformer":
        effects.target_overrides["voltage_secondary_v"] = sagged
        effects.sigma_multipliers["voltage_secondary_v"] = 3.0
        effects.sigma_multipliers["voltage_primary_kv"] = 2.0
        # Load spike when lights come back on (end of scenario).
        if p > 0.7:
            effects.additive_deltas["load_pct"] = 15.0
        effects.caused_event_types.update({"xfmr.tap_change"})


# ---------------------------------------------------------------------------
# Handler: transformer.incipient_dga
# ---------------------------------------------------------------------------
@register("transformer.incipient_dga")
def _dga_handler(scenario: Scenario, device_id: str, device_type: str,
                 asset: Dict[str, Any], t: datetime,
                 effects: ScenarioEffects) -> None:
    if device_type != "transformer":
        return
    p = _progress(scenario, t)

    # Hydrogen ramps slowly from baseline to a params-tunable peak.
    h2_peak = scenario.params.get("h2_peak_ppm", 600.0)
    ch4_peak = scenario.params.get("ch4_peak_ppm", 240.0)
    c2h2_peak = scenario.params.get("c2h2_peak_ppm", 12.0)
    # Smooth-in curve (so early samples don't jump); ease-in.
    eased = p * p * (3 - 2 * p)

    effects.target_overrides["dga_h2"] = 80.0 + eased * (h2_peak - 80.0)
    effects.target_overrides["dga_ch4"] = 60.0 + eased * (ch4_peak - 60.0)
    effects.target_overrides["dga_c2h2"] = 1.0 + eased * (c2h2_peak - 1.0)

    # Oil-top temp creeps up 5-8C over the scenario.
    effects.additive_deltas["oil_temp_bias_c"] = eased * scenario.params.get("oil_temp_bias_c", 6.0)
    # Partial discharge climbs moderately.
    effects.sigma_multipliers["partial_discharge_pc"] = 1.5 + eased

    effects.caused_event_types.update({"xfmr.fault_detected", "xfmr.fault_cleared"})


# ---------------------------------------------------------------------------
# Handler: regulator.hunting
# ---------------------------------------------------------------------------
@register("regulator.hunting")
def _regulator_handler(scenario: Scenario, device_id: str, device_type: str,
                       asset: Dict[str, Any], t: datetime,
                       effects: ScenarioEffects) -> None:
    if device_type != "gas_regulator":
        return
    period_min = scenario.params.get("period_minutes", 15.0)
    amp_psi = scenario.params.get("amp_psi", 4.5)
    phase = (t - scenario.start_time).total_seconds() / 60.0 / period_min * 2.0 * math.pi
    offset = amp_psi * math.sin(phase)

    effects.target_overrides["outlet"] = 15.0 + offset
    effects.sigma_multipliers["outlet"] = 3.0
    effects.sigma_multipliers["reg_position"] = 2.0
    # When the oscillation swings wide, cause relief valve to open.
    if abs(offset) >= amp_psi * 0.85:
        effects.forced_flags["relief_valve_status"] = "OPEN"
    effects.caused_event_types.update({"gas.relief_valve_state_change"})


# ---------------------------------------------------------------------------
# Handler: ami.tamper_campaign
# ---------------------------------------------------------------------------
@register("ami.tamper_campaign")
def _tamper_handler(scenario: Scenario, device_id: str, device_type: str,
                    asset: Dict[str, Any], t: datetime,
                    effects: ScenarioEffects) -> None:
    if device_type != "smart_meter":
        return
    p = _progress(scenario, t)
    drop_pct = scenario.params.get("demand_drop_pct", 0.65)
    base_kw = asset.get("base_kw") or {"residential": 1.2, "commercial": 4.5,
                                       "industrial": 25.0}.get(
        asset.get("type", "residential"), 1.2)
    # Bypass diversion: demand drops sharply during middle of window.
    envelope = 1.0 - drop_pct * (1.0 - abs(2.0 * p - 1.0))
    effects.target_overrides["demand_kw"] = base_kw * envelope

    # Force tamper flag true throughout the window for this meter.
    effects.forced_flags["tamper_flag"] = True
    effects.caused_event_types.update({"ami.tamper_detected"})


# ---------------------------------------------------------------------------
# Handler: compressor.degradation
# ---------------------------------------------------------------------------
@register("compressor.degradation")
def _compressor_handler(scenario: Scenario, device_id: str, device_type: str,
                        asset: Dict[str, Any], t: datetime,
                        effects: ScenarioEffects) -> None:
    if device_type != "pipeline_compressor":
        return
    p = _progress(scenario, t)
    eased = p * p

    vib_peak = scenario.params.get("vibration_peak_mms", 14.0)
    temp_rise = scenario.params.get("discharge_temp_rise_f", 18.0)
    effects.target_overrides["vibration"] = 5.0 + eased * (vib_peak - 5.0)
    effects.additive_deltas["temperature_f"] = eased * temp_rise
    # Pump may trip near peak degradation.
    if p >= 0.95:
        effects.forced_flags["pump_status"] = "STOP"
    effects.caused_event_types.update({"pipeline.pump_state_change"})


# ---------------------------------------------------------------------------
# Handler: der.reverse_power_flow
# ---------------------------------------------------------------------------
@register("der.reverse_power_flow")
def _der_reverse_flow_handler(scenario: Scenario, device_id: str,
                              device_type: str, asset: Dict[str, Any],
                              t: datetime, effects: ScenarioEffects) -> None:
    """Solar-DER export exceeds consumption on a residential feeder.

    Triangular profile within the scenario window peaks mid-scenario. The
    meter's `demand_kw` is pushed negative (back-feed), voltages bias
    upward (solar hosting-capacity excursion), and the upstream transformer
    sees reduced secondary load.
    """
    p = _progress(scenario, t)
    shape = 1.0 - abs(2.0 * p - 1.0)  # triangular peak at mid-scenario

    export_peak_kw = scenario.params.get("export_peak_kw", 3.0)
    voltage_bias_peak_pct = scenario.params.get("voltage_bias_peak_pct", 0.025)
    nominal_v = 120.5

    if device_type == "smart_meter":
        export_kw = export_peak_kw * shape
        # Force the AR1 target negative (export > consumption).
        effects.target_overrides["demand_kw"] = -export_kw
        # Voltage rise from reverse flow.
        v_target = nominal_v * (1.0 + voltage_bias_peak_pct * shape)
        effects.target_overrides["voltage_a"] = v_target
        effects.target_overrides["voltage_b"] = v_target
        effects.target_overrides["voltage_c"] = v_target
        effects.sigma_multipliers["voltage_a"] = 1.4
        effects.sigma_multipliers["voltage_b"] = 1.4
        effects.sigma_multipliers["voltage_c"] = 1.4
        # Flag field so downstream analytics can detect export.
        effects.forced_flags["reverse_flow"] = shape >= 0.25
        effects.caused_event_types.update({"ami.reverse_flow_detected"})
    elif device_type == "transformer":
        # Secondary sees reduced (or slightly negative) net load.
        load_reduction = scenario.params.get("xfmr_load_drop_pct", 18.0)
        effects.additive_deltas["load_pct"] = -load_reduction * shape
        # Voltage rise on the secondary bus.
        v_target = nominal_v * (1.0 + voltage_bias_peak_pct * shape)
        effects.target_overrides["voltage_secondary_v"] = v_target
        effects.sigma_multipliers["voltage_secondary_v"] = 1.6
        effects.caused_event_types.update({"xfmr.tap_change",
                                           "xfmr.voltage_excursion"})


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------
_MODE_COUNTS = {
    "off":     {},
    "default": {
        "storm.outage_cluster":      1,
        "transformer.incipient_dga": 1,
        "regulator.hunting":         1,
        "ami.tamper_campaign":       1,
        "compressor.degradation":    1,
        "der.reverse_power_flow":    1,
    },
    "heavy": {
        "storm.outage_cluster":      2,
        "transformer.incipient_dga": 2,
        "regulator.hunting":         2,
        "ami.tamper_campaign":       2,
        "compressor.degradation":    1,
        "der.reverse_power_flow":    2,
    },
}


def _new_id(rng: random.Random, prefix: str) -> str:
    # Deterministic uuid4-looking id derived from the scenario rng.
    b = bytes(rng.randrange(0, 256) for _ in range(16))
    u = uuid.UUID(bytes=b, version=4)
    return f"{prefix}-{u}"


def _clip(start: datetime, end: datetime,
          run_start: datetime, run_end: datetime
          ) -> Tuple[datetime, datetime, datetime, datetime, bool]:
    orig_start, orig_end = start, end
    clipped_start = max(start, run_start)
    clipped_end = min(end, run_end)
    truncated = (clipped_start != orig_start) or (clipped_end != orig_end)
    return clipped_start, clipped_end, orig_start, orig_end, truncated


def _pick_one(rng: random.Random, items: List[Any]) -> Optional[Any]:
    if not items:
        return None
    return items[rng.randrange(len(items))]


def _meter_ids_for_region(inventory: Dict[str, Any], region: str) -> List[str]:
    return [m["id"] for m in inventory.get("meters", [])
            if _device_region(m["id"]) == region]


def _transformer_ids_for_region(inventory: Dict[str, Any], region: str) -> List[str]:
    return [m["id"] for m in inventory.get("transformers", [])
            if _device_region(m["id"]) == region]


def schedule_scenarios(mode: str,
                       inventory: Dict[str, Any],
                       run_start: datetime,
                       run_end: datetime,
                       rng: random.Random) -> List[Scenario]:
    """Build a reproducible schedule of scenarios for a run.

    `rng` MUST be independent of the per-device RNG tree so this function
    can be called (or not) without perturbing device output.
    """
    if mode == "off":
        return []

    counts = _MODE_COUNTS.get(mode, _MODE_COUNTS["default"])
    run_seconds = int((run_end - run_start).total_seconds())
    scenarios: List[Scenario] = []

    def place_window(nominal_min: int, nominal_max: int,
                     long_running: bool = False) -> Tuple[datetime, datetime, bool]:
        """Pick a window inside the run (with 20% overlap guarantee for long ones)."""
        duration_s = rng.randrange(nominal_min * 60, nominal_max * 60 + 1)
        if long_running and duration_s > run_seconds * 0.5:
            # Bias so >=20% of the nominal duration lands in-window.
            min_overlap_s = int(0.2 * duration_s)
            earliest_start = run_start - timedelta(seconds=(duration_s - min_overlap_s))
            latest_start = run_end - timedelta(seconds=min_overlap_s)
        else:
            earliest_start = run_start
            latest_start = run_end - timedelta(seconds=max(60, duration_s))
            if latest_start < earliest_start:
                latest_start = earliest_start
        offset_range = int((latest_start - earliest_start).total_seconds())
        offset = rng.randrange(0, offset_range + 1) if offset_range > 0 else 0
        start = earliest_start + timedelta(seconds=offset)
        end = start + timedelta(seconds=duration_s)
        clipped_start, clipped_end, orig_start, orig_end, truncated = _clip(
            start, end, run_start, run_end)
        if (clipped_end - clipped_start).total_seconds() < 60:
            # Degenerate: shift inside window.
            clipped_start = run_start
            clipped_end = min(run_start + timedelta(seconds=duration_s), run_end)
            truncated = True
        return clipped_start, clipped_end, truncated

    # ---- storm.outage_cluster ------------------------------------------
    for _ in range(counts.get("storm.outage_cluster", 0)):
        regions = sorted({_device_region(m["id"])
                          for m in inventory.get("meters", [])})
        region = _pick_one(rng, regions) or "ALB"
        meters = _meter_ids_for_region(inventory, region)
        xfmrs = _transformer_ids_for_region(inventory, region)
        affected = list(sorted(meters + xfmrs))
        outage_meters = sorted(rng.sample(meters, min(max(1, len(meters) // 2), len(meters)))) if meters else []
        start, end, truncated = place_window(15, 120)
        scenarios.append(Scenario(
            scenario_id=_new_id(rng, "storm"),
            scenario_type="storm.outage_cluster",
            severity="HIGH",
            start_time=start,
            end_time=end,
            affected_device_ids=affected,
            params={
                "region": region,
                "sag_peak_pct": round(rng.uniform(0.07, 0.18), 3),
                "outage_meters": outage_meters,
            },
            label={
                "category": "electric.outage",
                "expected_signals": [
                    "voltage sag on transformers and meters",
                    "outage_flag true on subset of meters",
                    "tap_change events possible",
                ],
            },
            original_start_time=start,
            original_end_time=end,
            truncated=truncated,
        ))

    # ---- transformer.incipient_dga -------------------------------------
    for _ in range(counts.get("transformer.incipient_dga", 0)):
        xfmrs = [x["id"] for x in inventory.get("transformers", [])]
        if not xfmrs:
            continue
        tgt = _pick_one(rng, xfmrs)
        start, end, truncated = place_window(
            6 * 60, 24 * 60, long_running=True)
        scenarios.append(Scenario(
            scenario_id=_new_id(rng, "dga"),
            scenario_type="transformer.incipient_dga",
            severity="HIGH",
            start_time=start,
            end_time=end,
            affected_device_ids=[tgt],
            params={
                "h2_peak_ppm": round(rng.uniform(500, 800), 1),
                "ch4_peak_ppm": round(rng.uniform(200, 320), 1),
                "c2h2_peak_ppm": round(rng.uniform(8, 20), 1),
                "oil_temp_bias_c": round(rng.uniform(4, 9), 1),
            },
            label={
                "category": "transformer.insulation_fault",
                "expected_signals": [
                    "hydrogen_ppm rises well above baseline",
                    "methane_ppm rises",
                    "oil_temp_top_c elevated",
                    "xfmr.fault_detected event fires",
                ],
            },
            original_start_time=start,
            original_end_time=end,
            truncated=truncated,
        ))

    # ---- regulator.hunting ---------------------------------------------
    for _ in range(counts.get("regulator.hunting", 0)):
        regs = [r["id"] for r in inventory.get("regulators", [])]
        if not regs:
            continue
        tgt = _pick_one(rng, regs)
        start, end, truncated = place_window(30, 120)
        scenarios.append(Scenario(
            scenario_id=_new_id(rng, "reg"),
            scenario_type="regulator.hunting",
            severity="MEDIUM",
            start_time=start,
            end_time=end,
            affected_device_ids=[tgt],
            params={
                "period_minutes": round(rng.uniform(8, 20), 1),
                "amp_psi": round(rng.uniform(3.0, 6.5), 2),
            },
            label={
                "category": "gas.regulator_instability",
                "expected_signals": [
                    "outlet_pressure_psig oscillates",
                    "relief_valve_status toggles",
                ],
            },
            original_start_time=start,
            original_end_time=end,
            truncated=truncated,
        ))

    # ---- ami.tamper_campaign -------------------------------------------
    for _ in range(counts.get("ami.tamper_campaign", 0)):
        regions = sorted({_device_region(m["id"])
                          for m in inventory.get("meters", [])})
        region = _pick_one(rng, regions) or "ALB"
        meters = _meter_ids_for_region(inventory, region)
        if not meters:
            continue
        cluster_n = min(max(2, len(meters) // 2), len(meters), 4)
        cluster = sorted(rng.sample(meters, cluster_n))
        start, end, truncated = place_window(30, 180)
        scenarios.append(Scenario(
            scenario_id=_new_id(rng, "tamper"),
            scenario_type="ami.tamper_campaign",
            severity="CRITICAL",
            start_time=start,
            end_time=end,
            affected_device_ids=cluster,
            params={
                "region": region,
                "demand_drop_pct": round(rng.uniform(0.5, 0.8), 2),
            },
            label={
                "category": "ami.non_technical_loss",
                "expected_signals": [
                    "tamper_flag true on multiple adjacent meters",
                    "demand_kw drops significantly during window",
                ],
            },
            original_start_time=start,
            original_end_time=end,
            truncated=truncated,
        ))

    # ---- compressor.degradation ----------------------------------------
    for _ in range(counts.get("compressor.degradation", 0)):
        comps = [p["id"] for p in inventory.get("pipeline_stations", [])
                 if p.get("type") == "compressor"]
        if not comps:
            continue
        tgt = _pick_one(rng, comps)
        start, end, truncated = place_window(
            8 * 60, 36 * 60, long_running=True)
        scenarios.append(Scenario(
            scenario_id=_new_id(rng, "comp"),
            scenario_type="compressor.degradation",
            severity="HIGH",
            start_time=start,
            end_time=end,
            affected_device_ids=[tgt],
            params={
                "vibration_peak_mms": round(rng.uniform(10, 18), 1),
                "discharge_temp_rise_f": round(rng.uniform(12, 24), 1),
            },
            label={
                "category": "pipeline.compressor_degradation",
                "expected_signals": [
                    "vibration_mms rises well above baseline",
                    "discharge_temperature_f elevated",
                    "pump_state_change event near peak",
                ],
            },
            original_start_time=start,
            original_end_time=end,
            truncated=truncated,
        ))

    # ---- der.reverse_power_flow ----------------------------------------
    for _ in range(counts.get("der.reverse_power_flow", 0)):
        regions = sorted({_device_region(m["id"])
                          for m in inventory.get("meters", [])
                          if m.get("type") == "residential"})
        region = _pick_one(rng, regions) or "ALB"
        residential_meters = sorted(
            m["id"] for m in inventory.get("meters", [])
            if _device_region(m["id"]) == region
            and m.get("type") == "residential"
        )
        if not residential_meters:
            continue
        # DER cluster: 2-5 neighbouring residential meters with rooftop PV.
        cluster_n = min(len(residential_meters), rng.randrange(2, 6))
        cluster = sorted(rng.sample(residential_meters, cluster_n))
        xfmr_ids = _transformer_ids_for_region(inventory, region)
        upstream_xfmr = _pick_one(rng, xfmr_ids) if xfmr_ids else None
        affected = list(cluster)
        if upstream_xfmr:
            affected = sorted(set(affected) | {upstream_xfmr})

        # Place a 3-5h daytime window (10:00-15:00 UTC) clipped to the run.
        duration_s = rng.randrange(3 * 3600, 5 * 3600 + 1)
        daytime_start = run_start.replace(hour=10, minute=0, second=0, microsecond=0)
        if daytime_start < run_start:
            daytime_start = run_start
        daytime_end = daytime_start.replace(hour=15)
        if daytime_end > run_end:
            daytime_end = run_end
        if daytime_end <= daytime_start:
            # Run doesn't overlap daytime; fall back to run start.
            start, end, truncated = place_window(3 * 60, 5 * 60)
        else:
            latest_start = daytime_end - timedelta(seconds=min(60, duration_s))
            if latest_start <= daytime_start:
                start = daytime_start
            else:
                offset_range = int((latest_start - daytime_start).total_seconds())
                offset = rng.randrange(0, offset_range + 1)
                start = daytime_start + timedelta(seconds=offset)
            end = start + timedelta(seconds=duration_s)
            orig_start, orig_end = start, end
            start = max(start, run_start)
            end = min(end, run_end)
            truncated = (start != orig_start) or (end != orig_end)

        scenarios.append(Scenario(
            scenario_id=_new_id(rng, "der"),
            scenario_type="der.reverse_power_flow",
            severity="MEDIUM",
            start_time=start,
            end_time=end,
            affected_device_ids=affected,
            params={
                "region": region,
                "export_peak_kw": round(rng.uniform(2.5, 4.5), 2),
                "voltage_bias_peak_pct": round(rng.uniform(0.015, 0.035), 4),
                "xfmr_load_drop_pct": round(rng.uniform(12.0, 25.0), 1),
                "upstream_transformer": upstream_xfmr,
            },
            label={
                "category": "electric.der_export",
                "expected_signals": [
                    "demand_kw goes negative on residential meters",
                    "voltage rises 1.5-3.5% above nominal on affected feeder",
                    "upstream transformer load_pct drops during peak solar",
                    "ami.reverse_flow_detected events fire",
                ],
            },
            original_start_time=start,
            original_end_time=end,
            truncated=truncated,
        ))

    # Stable sort so scenario ordering is deterministic even if handlers
    # sort by scenario_id later.
    scenarios.sort(key=lambda s: (s.start_time, s.scenario_id))
    return scenarios


def load_schedule(path: str) -> List[Scenario]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    out: List[Scenario] = []
    for r in raw:
        out.append(Scenario(
            scenario_id=r["scenario_id"],
            scenario_type=r["scenario_type"],
            severity=r.get("severity", "MEDIUM"),
            start_time=datetime.fromisoformat(r["start_time"].rstrip("Z")).replace(tzinfo=timezone.utc),
            end_time=datetime.fromisoformat(r["end_time"].rstrip("Z")).replace(tzinfo=timezone.utc),
            affected_device_ids=list(r["affected_device_ids"]),
            params=r.get("params", {}),
            label=r.get("label", {}),
            original_start_time=(datetime.fromisoformat(r["original_start_time"].rstrip("Z")).replace(tzinfo=timezone.utc)
                                 if r.get("original_start_time") else None),
            original_end_time=(datetime.fromisoformat(r["original_end_time"].rstrip("Z")).replace(tzinfo=timezone.utc)
                               if r.get("original_end_time") else None),
            truncated=bool(r.get("truncated", False)),
        ))
    return out
