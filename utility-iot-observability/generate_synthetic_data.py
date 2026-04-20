"""
Utility IoT Synthetic Data Generator — Tier 2

Produces realistic fake telemetry for electric, gas, and oil/gas utilities
in NY State. Data ready for Azure IoT Hub / Event Hub ingestion.

Tier 2 upgrades vs. Tier 1:
  6. Scenario engine with ground-truth labels (see scenarios.py).
     Correlated multi-asset failure scenarios (storms, incipient DGA,
     regulator hunting, AMI tamper, compressor degradation) inject
     labeled signal perturbations that the AR(1) walks smooth out.
  7. Fifth output stream: scenarios_<ts>.jsonl (labels).
  8. Per-device BAD_FIELD — BAD quality nulls a semantically meaningful
     field per device type instead of schema-order-first.
  9. Deterministic run_id + timestamp stamp when --seed is supplied.

Tier 1 features (retained):
  1. Stateful AR(1) walks per signal (autocorrelated, not i.i.d.).
  2. Monotonic cumulative registers for meters (kWh, CCF, MCF).
  3. Quality codes + source/received timestamps on every record.
  4. Three output streams: telemetry, events, audit.
  5. Deterministic seeded CLI with configurable hours/start/inventory.

Backward compatibility:
  `python generate_synthetic_data.py <output_dir>` still works.
  `--scenarios off` disables the scenario engine; telemetry records
  are free of `scenario_ids` fields in that mode for consumers that
  tracked the Tier 1 schema.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from scenarios import (
    Scenario,
    ScenarioEffects,
    apply_scenarios,
    load_schedule,
    schedule_scenarios,
)
from protocols import to_protocol_view
from bad_data import BadDataConfig, apply_bad_data
from twins import build_hierarchy

# ---------------------------------------------------------------------------
# Embedded default inventory (NY State). Can be overridden via --inventory.
# ---------------------------------------------------------------------------
DEFAULT_LOCATIONS: Dict[str, List[Dict[str, Any]]] = {
    "substations": [
        {"id": "SS_ALB_001", "name": "Albany Colonie", "lat": 42.7284, "lon": -73.7893},
        {"id": "SS_POK_001", "name": "Poughkeepsie Main", "lat": 41.7004, "lon": -73.9209},
        {"id": "SS_KIN_001", "name": "Kingston", "lat": 41.9270, "lon": -74.0234},
        {"id": "SS_BIN_001", "name": "Binghamton Central", "lat": 42.0987, "lon": -75.9180},
        {"id": "SS_NEW_001", "name": "Newburgh", "lat": 41.5034, "lon": -74.0104},
    ],
    "transformers": [
        {"id": "TX_ALB_001", "substation": "SS_ALB_001", "lat": 42.7290, "lon": -73.7900, "rating_kva": 75},
        {"id": "TX_ALB_002", "substation": "SS_ALB_001", "lat": 42.7280, "lon": -73.7890, "rating_kva": 100},
        {"id": "TX_POK_001", "substation": "SS_POK_001", "lat": 41.7010, "lon": -73.9210, "rating_kva": 150},
        {"id": "TX_POK_002", "substation": "SS_POK_001", "lat": 41.7000, "lon": -73.9205, "rating_kva": 200},
        {"id": "TX_KIN_001", "substation": "SS_KIN_001", "lat": 41.9275, "lon": -74.0235, "rating_kva": 75},
    ],
    "meters": [
        {"id": "MTR_ALB_001001", "lat": 42.7300, "lon": -73.7900, "type": "residential"},
        {"id": "MTR_ALB_001002", "lat": 42.7305, "lon": -73.7895, "type": "residential"},
        {"id": "MTR_ALB_002001", "lat": 42.7310, "lon": -73.7905, "type": "commercial"},
        {"id": "MTR_POK_001001", "lat": 41.7010, "lon": -73.9215, "type": "residential"},
        {"id": "MTR_POK_001002", "lat": 41.7015, "lon": -73.9210, "type": "residential"},
        {"id": "MTR_POK_003001", "lat": 41.7020, "lon": -73.9205, "type": "industrial"},
    ],
    "gas_meters": [
        {"id": "GMR_ALB_001001", "lat": 42.7300, "lon": -73.7900, "type": "residential"},
        {"id": "GMR_ALB_001002", "lat": 42.7305, "lon": -73.7895, "type": "residential"},
        {"id": "GMR_POK_001001", "lat": 41.7010, "lon": -73.9215, "type": "residential"},
    ],
    "regulators": [
        {"id": "REG_ALB_001", "lat": 42.7250, "lon": -73.7850, "inlet_psi": 300},
        {"id": "REG_POK_001", "lat": 41.6950, "lon": -73.9150, "inlet_psi": 280},
        {"id": "REG_KIN_001", "lat": 41.9220, "lon": -74.0200, "inlet_psi": 320},
    ],
    "cp_stations": [
        {"id": "CP_GAS_001", "lat": 42.7100, "lon": -73.8000},
        {"id": "CP_GAS_002", "lat": 41.7050, "lon": -73.9300},
        {"id": "CP_GAS_003", "lat": 41.9200, "lon": -74.0300},
    ],
    "pipeline_stations": [
        {"id": "PIPE_COMP_001", "name": "Middletown Compressor", "lat": 41.4459, "lon": -74.4224, "type": "compressor"},
        {"id": "PIPE_VALVE_001", "name": "Hudson Valve Station", "lat": 41.7900, "lon": -73.9300, "type": "valve"},
        {"id": "PIPE_METER_001", "name": "Selkirk Meter Station", "lat": 42.5384, "lon": -73.8569, "type": "metering"},
    ],
}

# Quality distribution — sum must be <= 1.0; remainder implicitly GOOD.
QUALITY_LEVELS = ("GOOD", "UNCERTAIN", "STALE", "BAD", "MAINT")
DEFAULT_QUALITY_WEIGHTS = {"GOOD": 0.985, "UNCERTAIN": 0.010, "STALE": 0.004, "BAD": 0.001}


# ---------------------------------------------------------------------------
# Utility math
# ---------------------------------------------------------------------------
def ar1_step(prev: float, target: float, alpha: float, sigma: float,
             rng: random.Random, lo: float, hi: float) -> float:
    """One step of an AR(1)-style mean-reverting random walk.

    x_t = x_{t-1} + alpha*(target - x_{t-1}) + N(0, sigma), clamped to [lo, hi].
    alpha small → slow reversion (thermal mass, DGA drift);
    alpha large → stiff signal (frequency, voltage).
    """
    nxt = prev + alpha * (target - prev) + rng.gauss(0.0, sigma)
    return max(lo, min(hi, nxt))


def time_of_day_electric_mult(hour: int) -> float:
    """Residential-ish load shape: morning + evening peaks."""
    if 6 <= hour < 9:
        return 1.3
    if 17 <= hour < 21:
        return 1.8
    if 22 <= hour or hour < 5:
        return 0.55
    return 0.9


def season_gas_mult(month: int) -> float:
    """Gas heating season shape."""
    if month in (12, 1, 2):
        return 2.2
    if month in (11, 3):
        return 1.6
    if month in (10, 4):
        return 1.0
    return 0.3


def pick_quality(rng: random.Random, weights: Dict[str, float]) -> str:
    r = rng.random()
    cum = 0.0
    for level in ("BAD", "STALE", "UNCERTAIN"):
        cum += weights.get(level, 0.0)
        if r < cum:
            return level
    return "GOOD"


def iso_utc(dt: datetime) -> str:
    """Emit ISO-8601 with explicit Z suffix and no microseconds."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Event / audit payload helpers
# ---------------------------------------------------------------------------
@dataclass
class Event:
    event_type: str
    severity: str          # INFO | WARNING | CRITICAL
    device_type: str
    device_id: str
    timestamp: datetime
    message: str
    previous_value: Any = None
    new_value: Any = None
    scenario_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "event_type": self.event_type,
            "severity": self.severity,
            "device_type": self.device_type,
            "device_id": self.device_id,
            "timestamp": iso_utc(self.timestamp),
            "previous_value": self.previous_value,
            "new_value": self.new_value,
            "message": self.message,
        }
        if self.scenario_ids:
            d["scenario_ids"] = list(self.scenario_ids)
        return d


@dataclass
class AuditRecord:
    audit_type: str
    timestamp: datetime
    actor: str
    target: str
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "audit_type": self.audit_type,
            "timestamp": iso_utc(self.timestamp),
            "actor": self.actor,
            "target": self.target,
            "details": self.details,
        }


# ---------------------------------------------------------------------------
# Device state base class
# ---------------------------------------------------------------------------
class DeviceState:
    """Holds last-emitted values and advances them each sample."""

    device_type: str = "generic"
    interval_minutes: int = 5  # default cadence
    # BAD quality nulls this field (per-device semantic choice, not schema order).
    # Subclasses override.  None falls back to the Tier 1 "first numeric" behavior.
    BAD_FIELD: Optional[str] = None

    def __init__(self, asset: Dict[str, Any], rng: random.Random,
                 quality_weights: Dict[str, float]):
        self.asset = asset
        self.device_id: str = asset["id"]
        self.rng = rng
        self.quality_weights = quality_weights
        self.state: Dict[str, Any] = {}
        self._last_record: Optional[Dict[str, Any]] = None
        # Scenario context — reset each sample by the main loop when
        # scenarios are enabled.  Default = no-op empty effects.
        self.active_scenarios: List[Scenario] = []
        self.scenario_effects: ScenarioEffects = ScenarioEffects()
        self.scenarios_enabled: bool = False
        self._init_state()

    # ------------------------------------------------------------------
    def _init_state(self) -> None:
        raise NotImplementedError

    def _step(self, t: datetime) -> Tuple[Dict[str, Any], List[Event]]:
        """Advance one step. Returns (telemetry_fields, events_emitted)."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Scenario helpers for use inside _step
    def _target(self, key: str, default: float) -> float:
        return self.scenario_effects.target(key, default)

    def _sigma(self, key: str, default_sigma: float) -> float:
        return self.scenario_effects.sigma_mult(key, default_sigma)

    def _biased(self, key: str, base: float) -> float:
        return base + self.scenario_effects.additive_deltas.get(key, 0.0)

    def _forced(self, key: str) -> Tuple[bool, Any]:
        return self.scenario_effects.forced(key)

    # ------------------------------------------------------------------
    def sample(self, t: datetime) -> Tuple[Optional[Dict[str, Any]], List[Event]]:
        """Produce a record at time t if this device fires at that minute.

        Returns (record_or_None, events).  Record is None if the device
        doesn't sample at this cadence.
        """
        if t.minute % self.interval_minutes != 0:
            return None, []

        # Decide quality up front so STALE can short-circuit the walk.
        quality = pick_quality(self.rng, self.quality_weights)

        if quality == "STALE" and self._last_record is not None:
            record = dict(self._last_record)
            record["timestamp"] = iso_utc(t)
            record["source_timestamp"] = self._last_record.get("source_timestamp")
            record["received_timestamp"] = iso_utc(t)
            record["quality"] = "STALE"
            # Scenario presence is still tagged on STALE records when enabled.
            if self.scenarios_enabled:
                record["scenario_ids"] = list(self.scenario_effects.active_scenario_ids)
            self._last_record = record
            return record, []

        fields, events = self._step(t)
        record: Dict[str, Any] = {
            "device_type": self.device_type,
            "device_id": self.device_id,
            "timestamp": iso_utc(t),
            "source_timestamp": iso_utc(t),
            "received_timestamp": iso_utc(t),
            "quality": quality,
        }
        record.update(fields)
        if self.scenarios_enabled:
            record["scenario_ids"] = list(self.scenario_effects.active_scenario_ids)

        if quality == "BAD":
            self._apply_bad_corruption(record)

        # Stamp scenario_ids on any caused events before returning.
        if self.scenarios_enabled and events and self.scenario_effects.has_scenarios():
            for e in events:
                if self.scenario_effects.caused(e.event_type):
                    e.scenario_ids = list(self.scenario_effects.active_scenario_ids)

        self._last_record = record
        return record, events

    # ------------------------------------------------------------------
    def _apply_bad_corruption(self, record: Dict[str, Any]) -> None:
        """Null a semantically meaningful field for BAD quality samples."""
        if self.BAD_FIELD and self.BAD_FIELD in record:
            record[self.BAD_FIELD] = None
            return
        # Fallback (Tier 1 behavior): first numeric field outside metadata.
        for k, v in list(record.items()):
            if isinstance(v, (int, float)) and k not in {
                "timestamp", "source_timestamp", "received_timestamp",
            }:
                record[k] = None
                break

    # ------------------------------------------------------------------
    def _transition_event(self, t: datetime, field_name: str,
                          prev: Any, new: Any, event_type: str,
                          severity: str, message: str) -> Optional[Event]:
        if prev == new:
            return None
        return Event(
            event_type=event_type,
            severity=severity,
            device_type=self.device_type,
            device_id=self.device_id,
            timestamp=t,
            previous_value=prev,
            new_value=new,
            message=message,
        )


# ---------------------------------------------------------------------------
# Smart electric meter (15-minute cadence, cumulative kWh)
# ---------------------------------------------------------------------------
class SmartMeterState(DeviceState):
    device_type = "smart_meter"
    interval_minutes = 15
    BAD_FIELD = "demand_kw"

    BASE_KW = {"residential": 1.2, "commercial": 4.5, "industrial": 25.0}

    def _init_state(self) -> None:
        self.state.update({
            "demand_kw": self.BASE_KW.get(self.asset.get("type", "residential"), 1.2),
            "voltage_a": 120.0, "voltage_b": 120.0,
            "voltage_c": 120.0 if self.rng.random() > 0.3 else None,
            "power_factor": 0.96,
            "frequency_hz": 60.0,
            "rssi_dbm": -75.0,
            "kwh_delivered_cumulative": self.rng.uniform(10_000, 80_000),
            "kwh_received_cumulative": self.rng.uniform(0, 2_000),
            "tamper_flag": False,
            "outage_flag": False,
            "reverse_flow": False,
        })

    def _step(self, t: datetime) -> Tuple[Dict[str, Any], List[Event]]:
        s, rng = self.state, self.rng
        events: List[Event] = []

        base = self.BASE_KW.get(self.asset.get("type", "residential"), 1.2)
        tod_mult = time_of_day_electric_mult(t.hour)
        demand_target = self._target("demand_kw", base * tod_mult)
        reverse_forced, reverse_val = self._forced("reverse_flow")
        reverse_flow = bool(reverse_forced and reverse_val)
        demand_lo = -base * 4.0 if (reverse_flow or demand_target < 0.0) else 0.0
        s["demand_kw"] = ar1_step(s["demand_kw"], demand_target, alpha=0.35,
                                  sigma=self._sigma("demand_kw", base * 0.08),
                                  rng=rng, lo=demand_lo, hi=base * 4.0)

        v_target = self._target("voltage_a", 120.5)
        s["voltage_a"] = ar1_step(s["voltage_a"], v_target, 0.55,
                                  self._sigma("voltage_a", 0.8), rng, 108, 130)
        s["voltage_b"] = ar1_step(s["voltage_b"], self._target("voltage_b", 120.5),
                                  0.55, self._sigma("voltage_b", 0.8), rng, 108, 130)
        if s["voltage_c"] is not None:
            s["voltage_c"] = ar1_step(s["voltage_c"],
                                      self._target("voltage_c", 120.5),
                                      0.55, self._sigma("voltage_c", 0.8), rng, 108, 130)

        s["power_factor"] = ar1_step(s["power_factor"], 0.96, 0.3, 0.015, rng, 0.80, 1.00)
        s["frequency_hz"] = ar1_step(s["frequency_hz"], 60.0, 0.85,
                                     self._sigma("frequency_hz", 0.008),
                                     rng, 59.80, 60.20)
        s["rssi_dbm"] = ar1_step(s["rssi_dbm"], -75.0, 0.1, 2.0, rng, -115, -40)

        # Current derived from P = V*I*pf (single phase approximation).
        current_a = (abs(s["demand_kw"]) * 1000.0) / max(s["voltage_a"], 90.0) / s["power_factor"]

        # Cumulative energy register advances by demand * interval_hours.
        interval_hours = self.interval_minutes / 60.0
        delivered_delta = max(0.0, s["demand_kw"]) * interval_hours
        if reverse_flow and s["demand_kw"] < 0.0:
            # Export: energy flows back to the grid, accumulates in kwh_received.
            received_delta = (-s["demand_kw"]) * interval_hours
        else:
            received_delta = rng.uniform(0.0, 0.4) if self.asset.get("type") == "residential" else 0.0
        s["kwh_delivered_cumulative"] += delivered_delta
        s["kwh_received_cumulative"] += received_delta

        # Rare state-change events.
        prev_tamper = s["tamper_flag"]
        forced, val = self._forced("tamper_flag")
        s["tamper_flag"] = val if forced else (rng.random() < 0.0005)
        ev = self._transition_event(t, "tamper_flag", prev_tamper, s["tamper_flag"],
                                    "ami.tamper_detected", "CRITICAL",
                                    "Smart meter tamper switch activated")
        if ev and s["tamper_flag"]:
            events.append(ev)

        prev_outage = s["outage_flag"]
        forced, val = self._forced("outage_flag")
        s["outage_flag"] = val if forced else (rng.random() < 0.00005)
        ev = self._transition_event(t, "outage_flag", prev_outage, s["outage_flag"],
                                    "ami.last_gasp" if s["outage_flag"] else "ami.power_restored",
                                    "CRITICAL" if s["outage_flag"] else "INFO",
                                    "Service outage detected" if s["outage_flag"] else "Service restored")
        if ev:
            events.append(ev)

        prev_reverse = s["reverse_flow"]
        s["reverse_flow"] = reverse_flow
        ev = self._transition_event(t, "reverse_flow", prev_reverse, s["reverse_flow"],
                                    "ami.reverse_flow_detected", "INFO",
                                    "Reverse power flow detected (DER export)")
        if ev and s["reverse_flow"]:
            events.append(ev)

        fields = {
            "kWh_delivered_interval": round(delivered_delta, 4),
            "kWh_received_interval": round(received_delta, 4),
            "kWh_delivered_cumulative": round(s["kwh_delivered_cumulative"], 3),
            "kWh_received_cumulative": round(s["kwh_received_cumulative"], 3),
            "voltage_a": round(s["voltage_a"], 2),
            "voltage_b": round(s["voltage_b"], 2),
            "voltage_c": round(s["voltage_c"], 2) if s["voltage_c"] is not None else None,
            "current_a": round(current_a, 2),
            "power_factor": round(s["power_factor"], 3),
            "frequency_hz": round(s["frequency_hz"], 3),
            "demand_kw": round(s["demand_kw"], 3),
            "tamper_flag": s["tamper_flag"],
            "outage_flag": s["outage_flag"],
            "rssi_dbm": int(round(s["rssi_dbm"])),
        }
        if s["reverse_flow"]:
            fields["reverse_flow"] = True
        return fields, events


# ---------------------------------------------------------------------------
# Distribution transformer (5-min cadence, thermal lag, slow DGA drift)
# ---------------------------------------------------------------------------
class TransformerState(DeviceState):
    device_type = "transformer"
    interval_minutes = 5
    BAD_FIELD = "oil_temp_top_c"

    def _init_state(self) -> None:
        self.state.update({
            "load_pct": 45.0,
            "ambient_temp_c": 12.0,
            "oil_temp_top_c": 55.0,
            "oil_temp_bottom_c": 45.0,
            "voltage_primary_kv": 13.8,
            "voltage_secondary_v": 120.5,
            "current_a": 80.0,
            "tap_position": 17,
            "dga_h2": self.rng.uniform(60, 200),
            "dga_ch4": self.rng.uniform(30, 120),
            "dga_c2h2": self.rng.uniform(0.2, 3.0),
            "dga_c2h4": self.rng.uniform(10, 70),
            "dga_c2h6": self.rng.uniform(10, 60),
            "dga_co": self.rng.uniform(150, 600),
            "dga_co2": self.rng.uniform(1200, 4000),
            "moisture_ppm": self.rng.uniform(10, 30),
            "bushing_cap_pf": self.rng.uniform(0.004, 0.008),
            "partial_discharge_pc": self.rng.uniform(10, 150),
            "fault_detected": False,
        })

    def _step(self, t: datetime) -> Tuple[Dict[str, Any], List[Event]]:
        s, rng = self.state, self.rng
        events: List[Event] = []

        # Load follows the residential-ish shape too (transformers aggregate).
        load_target = self._target("load_pct",
                                   self._biased("load_pct",
                                                50.0 * time_of_day_electric_mult(t.hour) / 1.3))
        s["load_pct"] = ar1_step(s["load_pct"], load_target, 0.25,
                                 self._sigma("load_pct", 3.0), rng, 5, 140)

        # Ambient walks on a slow daily sinusoid + noise.
        daily_phase = (t.hour + t.minute / 60.0) / 24.0 * 2 * math.pi
        ambient_target = 12.0 + 10.0 * math.sin(daily_phase - math.pi / 2)
        s["ambient_temp_c"] = ar1_step(s["ambient_temp_c"], ambient_target,
                                       0.15, 0.6, rng, -25, 40)

        # Oil-top temp thermal model (slow reversion). DGA scenarios bias it up.
        oil_target = s["ambient_temp_c"] + 40.0 * ((s["load_pct"] / 100.0) ** 0.8)
        oil_target = self._biased("oil_temp_bias_c", oil_target)
        s["oil_temp_top_c"] = ar1_step(s["oil_temp_top_c"], oil_target,
                                       0.08, 0.7, rng, 0, 110)
        s["oil_temp_bottom_c"] = ar1_step(s["oil_temp_bottom_c"],
                                          s["oil_temp_top_c"] - 12.0,
                                          0.10, 0.5, rng, -5, 100)

        s["voltage_primary_kv"] = ar1_step(s["voltage_primary_kv"], 13.8,
                                           0.6, self._sigma("voltage_primary_kv", 0.12),
                                           rng, 12.5, 15.2)
        s["voltage_secondary_v"] = ar1_step(s["voltage_secondary_v"],
                                            self._target("voltage_secondary_v", 120.5),
                                            0.6, self._sigma("voltage_secondary_v", 0.8),
                                            rng, 108, 130)

        rating = self.asset.get("rating_kva", 75)
        nominal_i = rating * 1000.0 / (s["voltage_secondary_v"] * math.sqrt(3))
        current_target = nominal_i * (s["load_pct"] / 100.0)
        s["current_a"] = ar1_step(s["current_a"], current_target, 0.35, 4.0, rng, 0, 1200)

        # Tap position changes rarely on voltage excursions.
        prev_tap = s["tap_position"]
        if s["voltage_secondary_v"] < 115 and s["tap_position"] < 33:
            s["tap_position"] += 1
        elif s["voltage_secondary_v"] > 126 and s["tap_position"] > 1:
            s["tap_position"] -= 1
        if s["tap_position"] != prev_tap:
            events.append(Event(
                event_type="xfmr.tap_change",
                severity="INFO",
                device_type=self.device_type,
                device_id=self.device_id,
                timestamp=t,
                previous_value=prev_tap,
                new_value=s["tap_position"],
                message=f"LTC tap changed {prev_tap}->{s['tap_position']}",
            ))

        # DGA: very slow drift (alpha ~= 0.02). Scenario may override targets.
        for key, default_target, sigma, lo, hi in [
            ("dga_h2", 120.0, 4.0, 0, 800),
            ("dga_ch4", 70.0, 2.5, 0, 400),
            ("dga_c2h2", 1.5, 0.08, 0, 60),
            ("dga_c2h4", 40.0, 2.0, 0, 250),
            ("dga_c2h6", 35.0, 1.5, 0, 200),
            ("dga_co", 350.0, 8.0, 0, 1500),
            ("dga_co2", 2500.0, 40.0, 0, 12000),
        ]:
            tgt = self._target(key, default_target)
            s[key] = ar1_step(s[key], tgt, 0.02, sigma, rng, lo, hi)

        s["moisture_ppm"] = ar1_step(s["moisture_ppm"], 18.0, 0.05, 0.4, rng, 0, 60)
        s["bushing_cap_pf"] = ar1_step(s["bushing_cap_pf"], 0.006, 0.05, 0.0003, rng, 0.002, 0.02)
        s["partial_discharge_pc"] = ar1_step(s["partial_discharge_pc"], 80.0,
                                             0.15,
                                             self._sigma("partial_discharge_pc", 8.0),
                                             rng, 0, 500)

        prev_fault = s["fault_detected"]
        s["fault_detected"] = (s["dga_h2"] > 400 or s["dga_c2h2"] > 20 or s["dga_ch4"] > 200)
        if s["fault_detected"] and not prev_fault:
            events.append(Event(
                event_type="xfmr.fault_detected",
                severity="CRITICAL",
                device_type=self.device_type,
                device_id=self.device_id,
                timestamp=t,
                previous_value=False,
                new_value=True,
                message="DGA thresholds exceeded (H2/CH4/C2H2)",
            ))
        elif not s["fault_detected"] and prev_fault:
            events.append(Event(
                event_type="xfmr.fault_cleared",
                severity="INFO",
                device_type=self.device_type,
                device_id=self.device_id,
                timestamp=t,
                previous_value=True,
                new_value=False,
                message="DGA thresholds back within limits",
            ))

        load_kw = rating * (s["load_pct"] / 100.0) * 0.85
        fields = {
            "oil_temp_top_c": round(s["oil_temp_top_c"], 1),
            "oil_temp_bottom_c": round(s["oil_temp_bottom_c"], 1),
            "ambient_temp_c": round(s["ambient_temp_c"], 1),
            "load_kw": round(load_kw, 1),
            "load_pct": round(s["load_pct"], 1),
            "voltage_primary_kv": round(s["voltage_primary_kv"], 2),
            "voltage_secondary_v": round(s["voltage_secondary_v"], 1),
            "current_a_phase_a": round(s["current_a"] + rng.gauss(0, 1.0), 1),
            "current_a_phase_b": round(s["current_a"] + rng.gauss(0, 1.0), 1),
            "current_a_phase_c": round(s["current_a"] + rng.gauss(0, 1.0), 1),
            "tap_position": s["tap_position"],
            "dga_h2_ppm": round(s["dga_h2"], 1),
            "dga_ch4_ppm": round(s["dga_ch4"], 1),
            "dga_c2h2_ppm": round(s["dga_c2h2"], 2),
            "dga_c2h4_ppm": round(s["dga_c2h4"], 1),
            "dga_c2h6_ppm": round(s["dga_c2h6"], 1),
            "dga_co_ppm": round(s["dga_co"], 1),
            "dga_co2_ppm": round(s["dga_co2"], 1),
            "moisture_ppm": round(s["moisture_ppm"], 1),
            "bushing_cap_pf": round(s["bushing_cap_pf"], 4),
            "partial_discharge_pc": round(s["partial_discharge_pc"], 1),
            "fault_detected": s["fault_detected"],
        }
        return fields, events


# ---------------------------------------------------------------------------
# Gas meter (hourly, cumulative CCF and therms)
# ---------------------------------------------------------------------------
class GasMeterState(DeviceState):
    device_type = "gas_meter"
    interval_minutes = 60
    BAD_FIELD = "pressure_psig"

    BASE_CCF = {"residential": 0.8, "commercial": 2.5}

    def _init_state(self) -> None:
        self.state.update({
            "pressure": 12.0,
            "temperature_f": 60.0,
            "battery_v": self.rng.uniform(3.1, 3.5),
            "volume_ccf_cumulative": self.rng.uniform(2_000, 50_000),
            "therms_cumulative": 0.0,
            "tamper_flag": False,
            "leak_alarm": False,
        })
        self.state["therms_cumulative"] = self.state["volume_ccf_cumulative"] * 0.97

    def _step(self, t: datetime) -> Tuple[Dict[str, Any], List[Event]]:
        s, rng = self.state, self.rng
        events: List[Event] = []

        base = self.BASE_CCF.get(self.asset.get("type", "residential"), 0.5)
        ccf = base * season_gas_mult(t.month) * rng.uniform(0.75, 1.25)
        s["volume_ccf_cumulative"] += ccf
        s["therms_cumulative"] += ccf * 0.97

        s["pressure"] = ar1_step(s["pressure"], 15.0, 0.35, 0.8, rng, 0.2, 60)
        s["temperature_f"] = ar1_step(s["temperature_f"], 55.0, 0.15, 1.2, rng, -10, 120)
        # Battery creeps down over the run.
        s["battery_v"] = max(2.5, s["battery_v"] - 0.0001 + rng.gauss(0, 0.003))

        prev_tamper = s["tamper_flag"]
        s["tamper_flag"] = rng.random() < 0.0003
        if s["tamper_flag"] and not prev_tamper:
            events.append(Event("ami.gas_tamper", "CRITICAL", self.device_type,
                                self.device_id, t, False, True,
                                "Gas meter tamper switch activated"))

        prev_leak = s["leak_alarm"]
        s["leak_alarm"] = rng.random() < 0.0001
        if s["leak_alarm"] and not prev_leak:
            events.append(Event("gas.leak_alarm", "CRITICAL", self.device_type,
                                self.device_id, t, False, True,
                                "Methane leak alarm triggered"))

        fields = {
            "volume_ccf_interval": round(ccf, 3),
            "volume_ccf_cumulative": round(s["volume_ccf_cumulative"], 3),
            "corrected_volume_therms_interval": round(ccf * 0.97, 3),
            "corrected_volume_therms_cumulative": round(s["therms_cumulative"], 3),
            "pressure_psig": round(s["pressure"], 2),
            "temperature_f": round(s["temperature_f"], 1),
            "battery_voltage": round(s["battery_v"], 2),
            "tamper_flag": s["tamper_flag"],
            "leak_alarm": s["leak_alarm"],
        }
        return fields, events


# ---------------------------------------------------------------------------
# Gas pressure regulator (hourly here; real is minute-level)
# ---------------------------------------------------------------------------
class RegulatorState(DeviceState):
    device_type = "gas_regulator"
    interval_minutes = 60
    BAD_FIELD = "outlet_pressure_psig"

    def _init_state(self) -> None:
        self.state.update({
            "inlet": float(self.asset.get("inlet_psi", 300)),
            "outlet": 15.0,
            "flow": 800.0,
            "gas_temp_f": 55.0,
            "reg_position": 55.0,
            "relief_valve_status": "NORMAL",
            "slam_shut_status": "NORMAL",
            "odorizer_level_pct": self.rng.uniform(40, 95),
        })

    def _step(self, t: datetime) -> Tuple[Dict[str, Any], List[Event]]:
        s, rng = self.state, self.rng
        events: List[Event] = []

        inlet_target = float(self.asset.get("inlet_psi", 300))
        s["inlet"] = ar1_step(s["inlet"], inlet_target, 0.4, 2.5, rng, 50, 1000)
        s["outlet"] = ar1_step(s["outlet"],
                               self._target("outlet", 15.0),
                               0.5, self._sigma("outlet", 0.6), rng, 0.3, 60)
        flow_target = 900.0 * season_gas_mult(t.month) / 1.0
        s["flow"] = ar1_step(s["flow"], flow_target, 0.3, 60.0, rng, 0, 5000)
        s["gas_temp_f"] = ar1_step(s["gas_temp_f"], 55.0, 0.2, 1.5, rng, 10, 110)
        s["reg_position"] = ar1_step(s["reg_position"], 55.0, 0.35,
                                     self._sigma("reg_position", 3.0),
                                     rng, 5, 100)
        s["odorizer_level_pct"] = max(5.0, s["odorizer_level_pct"] - 0.02 + rng.gauss(0, 0.1))

        prev_relief = s["relief_valve_status"]
        forced, val = self._forced("relief_valve_status")
        if forced:
            s["relief_valve_status"] = val
        else:
            s["relief_valve_status"] = "OPEN" if rng.random() < 0.005 else "NORMAL"
        if s["relief_valve_status"] != prev_relief:
            events.append(Event(
                "gas.relief_valve_state_change",
                "WARNING" if s["relief_valve_status"] == "OPEN" else "INFO",
                self.device_type, self.device_id, t,
                prev_relief, s["relief_valve_status"],
                f"Relief valve {prev_relief}->{s['relief_valve_status']}",
            ))

        prev_slam = s["slam_shut_status"]
        s["slam_shut_status"] = "TRIPPED" if rng.random() < 0.0005 else "NORMAL"
        if s["slam_shut_status"] != prev_slam:
            events.append(Event(
                "gas.slam_shut_state_change",
                "CRITICAL" if s["slam_shut_status"] == "TRIPPED" else "INFO",
                self.device_type, self.device_id, t,
                prev_slam, s["slam_shut_status"],
                f"Slam-shut valve {prev_slam}->{s['slam_shut_status']}",
            ))

        fields = {
            "inlet_pressure_psig": round(s["inlet"], 1),
            "outlet_pressure_psig": round(s["outlet"], 2),
            "flow_rate_mcfh": round(s["flow"], 1),
            "gas_temperature_f": round(s["gas_temp_f"], 1),
            "regulator_position_pct": round(s["reg_position"], 1),
            "relief_valve_status": s["relief_valve_status"],
            "slam_shut_status": s["slam_shut_status"],
            "odorizer_level_pct": round(s["odorizer_level_pct"], 1),
        }
        return fields, events


# ---------------------------------------------------------------------------
# Cathodic protection station (hourly)
# ---------------------------------------------------------------------------
class CPStationState(DeviceState):
    device_type = "cathodic_protection"
    interval_minutes = 60
    BAD_FIELD = "pipe_to_soil_mv"

    def _init_state(self) -> None:
        self.state.update({
            "pipe_to_soil_mv": -1050.0,
            "rectifier_voltage": 40.0,
            "rectifier_current": 20.0,
            "ac_supply_voltage": 120.0,
            "rectifier_status": "ON",
        })

    def _step(self, t: datetime) -> Tuple[Dict[str, Any], List[Event]]:
        s, rng = self.state, self.rng
        events: List[Event] = []

        s["pipe_to_soil_mv"] = ar1_step(s["pipe_to_soil_mv"], -1050.0, 0.1, 8.0, rng, -1500, -700)
        s["rectifier_voltage"] = ar1_step(s["rectifier_voltage"], 40.0, 0.2, 1.5, rng, 5, 100)
        s["rectifier_current"] = ar1_step(s["rectifier_current"], 20.0, 0.2, 1.0, rng, 0, 50)
        s["ac_supply_voltage"] = ar1_step(s["ac_supply_voltage"], 120.0, 0.5, 0.8, rng, 100, 135)

        prev_status = s["rectifier_status"]
        r = rng.random()
        if r < 0.002:
            s["rectifier_status"] = "FAULT"
        elif r < 0.004:
            s["rectifier_status"] = "OFF"
        else:
            s["rectifier_status"] = "ON"
        if s["rectifier_status"] != prev_status:
            sev = "CRITICAL" if s["rectifier_status"] == "FAULT" else "WARNING"
            events.append(Event(
                "cp.rectifier_state_change", sev,
                self.device_type, self.device_id, t,
                prev_status, s["rectifier_status"],
                f"Rectifier {prev_status}->{s['rectifier_status']}",
            ))

        fields = {
            "pipe_to_soil_mv": round(s["pipe_to_soil_mv"], 1),
            "rectifier_voltage": round(s["rectifier_voltage"], 1),
            "rectifier_current": round(s["rectifier_current"], 1),
            "ac_supply_voltage": round(s["ac_supply_voltage"], 1),
            "rectifier_status": s["rectifier_status"],
        }
        return fields, events


# ---------------------------------------------------------------------------
# Pipeline stations (hourly here; real is 1-10s)
# ---------------------------------------------------------------------------
class PipelineCompressorState(DeviceState):
    device_type = "pipeline_compressor"
    interval_minutes = 60
    BAD_FIELD = "pressure_psig"

    def _init_state(self) -> None:
        self.state.update({
            "pressure": 1100.0,
            "temperature_f": 100.0,
            "flow": 9000.0,
            "discharge_psi": 1250.0,
            "suction_psi": 350.0,
            "vibration": 5.0,
            "h2s_ppm": 2.0,
            "pump_status": "RUN",
        })

    def _step(self, t: datetime) -> Tuple[Dict[str, Any], List[Event]]:
        s, rng = self.state, self.rng
        events: List[Event] = []

        s["pressure"] = ar1_step(s["pressure"], 1100.0, 0.35, 15.0, rng, 600, 1500)
        s["temperature_f"] = ar1_step(s["temperature_f"],
                                      self._biased("temperature_f", 100.0),
                                      0.2, 1.5, rng, 70, 140)
        s["flow"] = ar1_step(s["flow"], 9000.0, 0.3, 250.0, rng, 3000, 16000)
        s["discharge_psi"] = ar1_step(s["discharge_psi"], 1250.0, 0.35, 20.0, rng, 700, 1700)
        s["suction_psi"] = ar1_step(s["suction_psi"], 350.0, 0.35, 8.0, rng, 100, 600)
        s["vibration"] = ar1_step(s["vibration"],
                                  self._target("vibration", 5.0),
                                  0.1, self._sigma("vibration", 0.6),
                                  rng, 0, 25)
        s["h2s_ppm"] = ar1_step(s["h2s_ppm"], 2.0, 0.15, 0.5, rng, 0, 40)

        prev_pump = s["pump_status"]
        forced, val = self._forced("pump_status")
        if forced:
            s["pump_status"] = val
        else:
            s["pump_status"] = "STOP" if rng.random() < 0.002 else "RUN"
        if s["pump_status"] != prev_pump:
            events.append(Event(
                "pipeline.pump_state_change",
                "WARNING" if s["pump_status"] == "STOP" else "INFO",
                self.device_type, self.device_id, t,
                prev_pump, s["pump_status"],
                f"Compressor pump {prev_pump}->{s['pump_status']}",
            ))

        fields = {
            "pressure_psig": round(s["pressure"], 1),
            "temperature_f": round(s["temperature_f"], 1),
            "flow_rate_mcfh": round(s["flow"], 1),
            "pump_status": s["pump_status"],
            "pump_discharge_psi": round(s["discharge_psi"], 1),
            "pump_suction_psi": round(s["suction_psi"], 1),
            "vibration_mms": round(s["vibration"], 2),
            "h2s_ppm": round(s["h2s_ppm"], 1),
        }
        return fields, events


class PipelineValveState(DeviceState):
    device_type = "pipeline_valve"
    interval_minutes = 60
    BAD_FIELD = "pressure_psig"

    def _init_state(self) -> None:
        self.state.update({
            "pressure": 900.0,
            "temperature_f": 80.0,
            "valve_position": 100.0,
            "flow": 7000.0,
            "leak_detection_flag": False,
        })

    def _step(self, t: datetime) -> Tuple[Dict[str, Any], List[Event]]:
        s, rng = self.state, self.rng
        events: List[Event] = []

        s["pressure"] = ar1_step(s["pressure"], 900.0, 0.35, 10.0, rng, 500, 1200)
        s["temperature_f"] = ar1_step(s["temperature_f"], 80.0, 0.2, 1.0, rng, 40, 120)
        s["valve_position"] = ar1_step(s["valve_position"], 100.0, 0.1, 1.5, rng, 0, 100)
        s["flow"] = ar1_step(s["flow"], 7000.0, 0.3, 180.0, rng, 2500, 12000)

        prev_leak = s["leak_detection_flag"]
        s["leak_detection_flag"] = rng.random() < 0.00008
        if s["leak_detection_flag"] and not prev_leak:
            events.append(Event(
                "pipeline.leak_detected", "CRITICAL",
                self.device_type, self.device_id, t,
                False, True, "Pipeline leak detector triggered",
            ))

        fields = {
            "pressure_psig": round(s["pressure"], 1),
            "temperature_f": round(s["temperature_f"], 1),
            "valve_position_pct": round(s["valve_position"], 1),
            "flow_rate_mcfh": round(s["flow"], 1),
            "leak_detection_flag": s["leak_detection_flag"],
        }
        return fields, events


class PipelineMeteringState(DeviceState):
    device_type = "pipeline_metering"
    interval_minutes = 60
    BAD_FIELD = "pressure_psig"

    def _init_state(self) -> None:
        self.state.update({
            "pressure": 700.0,
            "temperature_f": 70.0,
            "flow": 6000.0,
            "volume_mcf_cumulative": self.rng.uniform(500_000, 2_000_000),
            "pressure_ratio": 0.9,
        })

    def _step(self, t: datetime) -> Tuple[Dict[str, Any], List[Event]]:
        s, rng = self.state, self.rng
        events: List[Event] = []

        s["pressure"] = ar1_step(s["pressure"], 700.0, 0.35, 8.0, rng, 400, 1000)
        s["temperature_f"] = ar1_step(s["temperature_f"], 70.0, 0.2, 0.8, rng, 35, 110)
        s["flow"] = ar1_step(s["flow"], 6000.0, 0.3, 150.0, rng, 1500, 11000)
        s["pressure_ratio"] = ar1_step(s["pressure_ratio"], 0.9, 0.2, 0.01, rng, 0.6, 1.0)

        # Hour's worth of MCF (flow is MCF/h).
        s["volume_mcf_cumulative"] += s["flow"]

        fields = {
            "pressure_psig": round(s["pressure"], 1),
            "temperature_f": round(s["temperature_f"], 1),
            "flow_rate_mcfh": round(s["flow"], 1),
            "volume_mcf_interval": round(s["flow"], 1),
            "volume_mcf_cumulative": round(s["volume_mcf_cumulative"], 1),
            "pressure_ratio": round(s["pressure_ratio"], 3),
        }
        return fields, events


# ---------------------------------------------------------------------------
# Device registry / orchestration
# ---------------------------------------------------------------------------
PIPELINE_CLASSES: Dict[str, type] = {
    "compressor": PipelineCompressorState,
    "valve": PipelineValveState,
    "metering": PipelineMeteringState,
}


def build_devices(inventory: Dict[str, List[Dict[str, Any]]],
                  rng: random.Random,
                  quality_weights: Dict[str, float]) -> List[DeviceState]:
    def sub_rng() -> random.Random:
        return random.Random(rng.random())

    devices: List[DeviceState] = []
    for asset in inventory.get("meters", []):
        devices.append(SmartMeterState(asset, sub_rng(), quality_weights))
    for asset in inventory.get("transformers", []):
        devices.append(TransformerState(asset, sub_rng(), quality_weights))
    for asset in inventory.get("gas_meters", []):
        devices.append(GasMeterState(asset, sub_rng(), quality_weights))
    for asset in inventory.get("regulators", []):
        devices.append(RegulatorState(asset, sub_rng(), quality_weights))
    for asset in inventory.get("cp_stations", []):
        devices.append(CPStationState(asset, sub_rng(), quality_weights))
    for asset in inventory.get("pipeline_stations", []):
        cls = PIPELINE_CLASSES.get(asset.get("type", "compressor"),
                                   PipelineCompressorState)
        devices.append(cls(asset, sub_rng(), quality_weights))
    return devices


# ---------------------------------------------------------------------------
# Dataset generation
# ---------------------------------------------------------------------------
def _deterministic_run_id(seed: int, start_time: datetime, hours: int,
                          inventory_sig: str, scenarios_mode: str) -> str:
    h = hashlib.sha256(
        f"seed={seed}|start={start_time.isoformat()}|hours={hours}|"
        f"inv={inventory_sig}|scn={scenarios_mode}".encode("utf-8")
    ).digest()
    return str(uuid.UUID(bytes=h[:16], version=4))


def _inventory_signature(inventory: Dict[str, Any]) -> str:
    # Stable hash of inventory asset IDs.
    ids: List[str] = []
    for cat in sorted(inventory.keys()):
        for item in inventory.get(cat, []) or []:
            if isinstance(item, dict) and "id" in item:
                ids.append(f"{cat}:{item['id']}")
    return hashlib.sha256("|".join(sorted(ids)).encode("utf-8")).hexdigest()[:16]


def generate_dataset(output_dir: str = ".",
                     hours: int = 24,
                     start_time: Optional[datetime] = None,
                     seed: Optional[int] = None,
                     inventory: Optional[Dict[str, Any]] = None,
                     quality_weights: Optional[Dict[str, float]] = None,
                     scenarios_mode: str = "default",
                     scenario_config_path: Optional[str] = None,
                     protocol_views: str = "emit",
                     bad_data_mode: str = "realistic",
                     bad_data_config_path: Optional[str] = None,
                     asset_hierarchy: str = "emit",
                     ) -> Dict[str, Any]:
    """Generate telemetry + events + audit + scenarios + (optionally) protocol files."""
    inventory = inventory or DEFAULT_LOCATIONS
    quality_weights = quality_weights or DEFAULT_QUALITY_WEIGHTS

    rng = random.Random(seed)

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    wall_now = datetime.now(timezone.utc).replace(microsecond=0)
    if start_time is None:
        if seed is not None:
            # Anchor to a deterministic time so filename stamp + run_id are
            # reproducible across invocations with the same seed.
            start_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
        else:
            start_time = wall_now - timedelta(hours=hours)
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=timezone.utc)
    end_time = start_time + timedelta(hours=hours)

    # Deterministic run_id + generated_at + filename stamp when seed is supplied.
    # This gives reproducible outputs across invocations for labeling + hashing.
    inv_sig = _inventory_signature(inventory)
    if seed is not None:
        run_id = _deterministic_run_id(seed, start_time, hours, inv_sig, scenarios_mode)
        generated_at = start_time
    else:
        run_id = str(uuid.uuid4())
        generated_at = wall_now

    devices = build_devices(inventory, rng, quality_weights)

    # Schedule scenarios using an INDEPENDENT RNG so device streams are
    # unaffected whether scenarios are on or off.
    scenarios: List[Scenario] = []
    if scenario_config_path:
        scenarios = load_schedule(scenario_config_path)
    elif scenarios_mode != "off":
        scenario_rng = random.Random(f"scenarios:{seed}")
        scenarios = schedule_scenarios(scenarios_mode, inventory,
                                       start_time, end_time, scenario_rng)
    scenarios_enabled = scenarios_mode != "off" or bool(scenarios)
    for d in devices:
        d.scenarios_enabled = scenarios_enabled

    # Index scenarios by affected device for fast per-minute lookup.
    device_scenarios: Dict[str, List[Scenario]] = {}
    for s in scenarios:
        for did in s.affected_device_ids:
            device_scenarios.setdefault(did, []).append(s)

    telemetry: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    audit: List[Dict[str, Any]] = []

    audit.append(AuditRecord(
        audit_type="synthesizer_run_started",
        timestamp=generated_at,
        actor="system:generate_synthetic_data",
        target=f"dataset:{run_id}",
        details={
            "run_id": run_id,
            "seed": seed,
            "hours": hours,
            "start_time": iso_utc(start_time),
            "end_time": iso_utc(end_time),
            "device_count": len(devices),
            "quality_weights": quality_weights,
            "scenarios_mode": scenarios_mode,
            "scenario_count": len(scenarios),
        },
    ).to_dict())
    for d in devices:
        audit.append(AuditRecord(
            audit_type="device_registered",
            timestamp=generated_at,
            actor="system:generate_synthetic_data",
            target=f"device:{d.device_id}",
            details={"device_type": d.device_type,
                     "interval_minutes": d.interval_minutes,
                     "asset": d.asset},
        ).to_dict())

    current = start_time
    while current <= end_time:
        # Compute active scenario effects once per minute per affected device.
        if scenarios_enabled and device_scenarios:
            for d in devices:
                ds = device_scenarios.get(d.device_id, [])
                d.active_scenarios = ds
                if ds:
                    d.scenario_effects = apply_scenarios(
                        ds, d.device_id, d.device_type, d.asset, current)
                else:
                    d.scenario_effects = ScenarioEffects()
        for d in devices:
            record, evs = d.sample(current)
            if record is not None:
                telemetry.append(record)
            for e in evs:
                events.append(e.to_dict())
        current += timedelta(minutes=1)

    # ------------------------------------------------------------- Tier 4
    # Apply realistic bad-data pipeline (clock skew, loss, delay, flatline,
    # drift, maintenance windows). Pure function of (telemetry, events,
    # seed); does not use or perturb the device/scenario RNGs.
    bad_cfg = BadDataConfig.from_mode(bad_data_mode)
    if bad_data_config_path:
        with open(bad_data_config_path, "r", encoding="utf-8") as f:
            bad_cfg = bad_cfg.merge(json.load(f))
    bd_seed = seed if seed is not None else 0
    telemetry, events, bad_summary = apply_bad_data(
        telemetry, events, bad_cfg, bd_seed, start_time, hours)
    audit.append(AuditRecord(
        audit_type="bad_data_applied",
        timestamp=generated_at,
        actor="system:generate_synthetic_data",
        target=f"dataset:{run_id}",
        details=bad_summary.to_dict(),
    ).to_dict())

    # ------------------------------------------------------------------ write
    stamp = generated_at.strftime("%Y%m%d_%H%M%S")
    telemetry_jsonl = out_path / f"utility_telemetry_{stamp}.jsonl"
    telemetry_csv = out_path / f"utility_telemetry_{stamp}.csv"
    events_jsonl = out_path / f"events_{stamp}.jsonl"
    audit_jsonl = out_path / f"audit_{stamp}.jsonl"
    scenarios_jsonl = out_path / f"scenarios_{stamp}.jsonl"
    meta_file = out_path / f"metadata_{stamp}.json"

    with telemetry_jsonl.open("w", encoding="utf-8") as f:
        for r in telemetry:
            f.write(json.dumps(r) + "\n")

    if telemetry:
        # Union of keys so CSV doesn't drop device-specific columns.
        all_keys: List[str] = []
        seen: set = set()
        for r in telemetry:
            for k in r.keys():
                if k not in seen:
                    seen.add(k)
                    all_keys.append(k)
        with telemetry_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=all_keys, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(telemetry)

    with events_jsonl.open("w", encoding="utf-8") as f:
        for r in events:
            f.write(json.dumps(r) + "\n")

    with scenarios_jsonl.open("w", encoding="utf-8") as f:
        for s in scenarios:
            f.write(json.dumps(s.to_dict()) + "\n")

    # Tier 3: protocol-aware payload views. Pure function of canonical
    # telemetry; toggling does not shift any other output file.
    protocol_records: List[Dict[str, Any]] = []
    protocol_jsonl: Optional[Path] = None
    if protocol_views == "emit":
        protocol_jsonl = out_path / f"telemetry_protocol_{stamp}.jsonl"
        with protocol_jsonl.open("w", encoding="utf-8") as f:
            for r in telemetry:
                view = to_protocol_view(r)
                if view is None:
                    continue
                protocol_records.append(view)
                f.write(json.dumps(view) + "\n")

    audit.append(AuditRecord(
        audit_type="synthesizer_run_completed",
        timestamp=generated_at + timedelta(hours=hours),
        actor="system:generate_synthetic_data",
        target=f"dataset:{run_id}",
        details={
            "run_id": run_id,
            "telemetry_records": len(telemetry),
            "event_records": len(events),
            "scenario_records": len(scenarios),
            "protocol_records": len(protocol_records),
        },
    ).to_dict())

    with audit_jsonl.open("w", encoding="utf-8") as f:
        for r in audit:
            f.write(json.dumps(r) + "\n")

    record_counts = {
        "telemetry": len(telemetry),
        "events": len(events),
        "audit": len(audit),
        "scenarios": len(scenarios),
    }
    output_files = {
        "telemetry_jsonl": str(telemetry_jsonl),
        "telemetry_csv": str(telemetry_csv),
        "events_jsonl": str(events_jsonl),
        "audit_jsonl": str(audit_jsonl),
        "scenarios_jsonl": str(scenarios_jsonl),
    }
    if protocol_jsonl is not None:
        record_counts["telemetry_protocol"] = len(protocol_records)
        output_files["telemetry_protocol_jsonl"] = str(protocol_jsonl)

    hierarchy_file: Optional[Path] = None
    hierarchy_doc: Optional[Dict[str, Any]] = None
    if asset_hierarchy == "emit":
        hierarchy_doc = build_hierarchy(inventory)
        hierarchy_file = out_path / f"asset_hierarchy_{stamp}.json"
        with hierarchy_file.open("w", encoding="utf-8") as f:
            json.dump(hierarchy_doc, f, indent=2)
        record_counts["asset_twins"] = len(hierarchy_doc["twin_instances"])
        record_counts["dtdl_interfaces"] = len(hierarchy_doc["dtdl_interfaces"])
        record_counts["asset_relationships"] = len(hierarchy_doc["relationships"])
        output_files["asset_hierarchy_json"] = str(hierarchy_file)

    metadata = {
        "run_id": run_id,
        "generated_at": iso_utc(generated_at),
        "start_time": iso_utc(start_time),
        "end_time": iso_utc(end_time),
        "hours_generated": hours,
        "seed": seed,
        "quality_weights": quality_weights,
        "scenarios_mode": scenarios_mode,
        "protocol_views": protocol_views,
        "bad_data_mode": bad_data_mode,
        "asset_hierarchy": asset_hierarchy,
        "device_counts": {
            "smart_meters": len(inventory.get("meters", [])),
            "transformers": len(inventory.get("transformers", [])),
            "gas_meters": len(inventory.get("gas_meters", [])),
            "regulators": len(inventory.get("regulators", [])),
            "cathodic_protection": len(inventory.get("cp_stations", [])),
            "pipeline_stations": len(inventory.get("pipeline_stations", [])),
        },
        "record_counts": record_counts,
        "output_files": output_files,
    }
    with meta_file.open("w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    print("\n[OK] Synthetic data generated (Tier 5)")
    print(f"   Run ID:     {run_id}")
    print(f"   Telemetry:  {len(telemetry):>6} records -> {telemetry_jsonl.name}")
    print(f"   Events:     {len(events):>6} records -> {events_jsonl.name}")
    print(f"   Audit:      {len(audit):>6} records -> {audit_jsonl.name}")
    print(f"   Scenarios:  {len(scenarios):>6} records -> {scenarios_jsonl.name}")
    if protocol_jsonl is not None:
        print(f"   Protocol:   {len(protocol_records):>6} records -> {protocol_jsonl.name}")
    if bad_data_mode != "off":
        print(f"   BadData:    mode={bad_data_mode} loss={bad_summary.loss_count} "
              f"delayed={bad_summary.delay_count} flatlines={bad_summary.flatline_runs} "
              f"maint_windows={bad_summary.maintenance_windows}")
    if hierarchy_file is not None and hierarchy_doc is not None:
        print(f"   Hierarchy:  {len(hierarchy_doc['twin_instances']):>6} twins    -> {hierarchy_file.name}")
    print(f"   Metadata:   {meta_file.name}")
    return metadata


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_start_time(value: str) -> datetime:
    v = value.rstrip("Z")
    try:
        dt = datetime.fromisoformat(v)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"--start-time must be ISO 8601 (e.g. 2026-04-15T00:00:00Z): {exc}"
        )
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate synthetic utility IoT telemetry (Tier 1).",
    )
    parser.add_argument("output_dir", nargs="?", default=".",
                        help="Directory to write output files (default: .)")
    parser.add_argument("--output-dir", dest="output_dir_flag",
                        help="Same as positional output_dir; flag form.")
    parser.add_argument("--hours", type=int, default=24,
                        help="Hours of telemetry to synthesize (default: 24).")
    parser.add_argument("--start-time", type=_parse_start_time, default=None,
                        help="ISO 8601 start timestamp UTC (default: now - hours).")
    parser.add_argument("--seed", type=int, default=None,
                        help="Deterministic seed for reproducible runs.")
    parser.add_argument("--inventory", type=str, default=None,
                        help="Path to JSON inventory file overriding embedded defaults.")
    parser.add_argument("--good-quality-prob", type=float, default=0.985,
                        help="Probability of GOOD quality samples (default: 0.985).")
    parser.add_argument("--scenarios", choices=["off", "default", "heavy"],
                        default="default",
                        help="Scenario engine mode (default: default). "
                             "'off' disables the scenario engine and omits "
                             "scenario_ids fields for Tier 1 compatibility.")
    parser.add_argument("--scenario-config", type=str, default=None,
                        help="Path to a JSON file explicitly listing scenarios; "
                             "overrides --scenarios mode scheduling.")
    parser.add_argument("--protocol-views", choices=["off", "emit"],
                        default="emit",
                        help="Emit protocol-aware payload views alongside "
                             "canonical telemetry (default: emit). Produces "
                             "telemetry_protocol_<ts>.jsonl with DLMS/COSEM, "
                             "IEC 61850, DNP3, and OPC UA envelopes per device.")
    parser.add_argument("--bad-data", choices=["off", "realistic", "heavy"],
                        default="realistic",
                        help="Dirty-data realism layer (default: realistic). "
                             "Applies clock skew/drift, packet loss, delivery "
                             "delay, flatlining, calibration drift, and "
                             "maintenance windows. 'off' produces clean "
                             "Tier 1-3 output.")
    parser.add_argument("--bad-data-config", type=str, default=None,
                        help="Path to a JSON file with bad-data overrides "
                             "(keys matching BadDataConfig fields) applied on "
                             "top of the --bad-data preset.")
    parser.add_argument("--asset-hierarchy", choices=["off", "emit"],
                        default="emit",
                        help="Emit a DTDL v3 asset hierarchy document "
                             "(default: emit). Produces "
                             "asset_hierarchy_<ts>.json containing DTDL "
                             "interfaces, twin instances, and relationships "
                             "derived from the inventory. Set 'off' for "
                             "byte-identical parity with Tier 4 output.")

    args = parser.parse_args(argv)
    output_dir = args.output_dir_flag or args.output_dir

    inventory: Optional[Dict[str, Any]] = None
    if args.inventory:
        with open(args.inventory, "r", encoding="utf-8") as f:
            inventory = json.load(f)

    good = max(0.0, min(1.0, args.good_quality_prob))
    remainder = 1.0 - good
    quality_weights = {
        "GOOD": good,
        "UNCERTAIN": remainder * (10 / 15) if remainder > 0 else 0.0,
        "STALE": remainder * (4 / 15) if remainder > 0 else 0.0,
        "BAD": remainder * (1 / 15) if remainder > 0 else 0.0,
    }

    generate_dataset(
        output_dir=output_dir,
        hours=args.hours,
        start_time=args.start_time,
        seed=args.seed,
        inventory=inventory,
        quality_weights=quality_weights,
        scenarios_mode=args.scenarios,
        scenario_config_path=args.scenario_config,
        protocol_views=args.protocol_views,
        bad_data_mode=args.bad_data,
        bad_data_config_path=args.bad_data_config,
        asset_hierarchy=args.asset_hierarchy,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
