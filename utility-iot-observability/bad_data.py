"""Tier 4 — realistic bad data layer.

Pure(ish) pipeline applied AFTER scenario injection and BEFORE file
writes / protocol projection. Operates on the canonical telemetry and
events lists in place-ish (returns new lists) and records per-dimension
summaries into the audit stream.

Six dimensions (research doc section 6.4):
  1. clock_skew  - per-device persistent offset + linear drift on source_timestamp
  2. loss        - per-device packet drop
  3. delay       - per-device lognormal delay on received_timestamp with final sort
  4. flatline    - per-device rare event holding primary field constant
  5. drift       - per-device persistent calibration bias on primary field
  6. maint       - scheduled maintenance windows with quality=MAINT

All transforms are seeded by a dedicated RNG so enabling/disabling
bad-data does not perturb the upstream device/scenario streams.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
import math
import random


QUALITY_MAINT = "MAINT"


PRIMARY_FIELD: Dict[str, str] = {
    "smart_meter": "demand_kw",
    "gas_meter": "volume_ccf_interval",
    "transformer": "oil_temp_top_c",
    "gas_regulator": "outlet_pressure_psig",
    "cathodic_protection": "pipe_to_soil_mv",
    "pipeline_valve": "pressure_psig",
    "pipeline_compressor": "pressure_psig",
    "pipeline_metering": "pressure_psig",
}

DRIFT_SCALE: Dict[str, Tuple[str, float]] = {
    "smart_meter": ("relative", 0.005),
    "gas_meter": ("relative", 0.005),
    "transformer": ("absolute_c", 2.0),
    "gas_regulator": ("relative", 0.03),
    "cathodic_protection": ("relative", 0.05),
    "pipeline_valve": ("relative", 0.03),
    "pipeline_compressor": ("relative", 0.03),
    "pipeline_metering": ("relative", 0.03),
}


PRESETS: Dict[str, Dict[str, Any]] = {
    "off": {
        "clock_skew_stddev_s": 0.0,
        "clock_drift_ppm_max": 0.0,
        "loss_rate": 0.0,
        "delay_rate": 0.0,
        "delay_median_s": 0.0,
        "flatline_per_device_per_day": 0.0,
        "drift_stddev": 0.0,
        "maint_windows_max": 0,
    },
    "realistic": {
        "clock_skew_stddev_s": 2.0,
        "clock_drift_ppm_max": 60.0,
        "loss_rate": 0.002,
        "delay_rate": 0.01,
        "delay_median_s": 8.0,
        "flatline_per_device_per_day": 0.05,
        "drift_stddev": 0.005,
        "maint_windows_max": 1,
    },
    "heavy": {
        "clock_skew_stddev_s": 10.0,
        "clock_drift_ppm_max": 300.0,
        "loss_rate": 0.02,
        "delay_rate": 0.05,
        "delay_median_s": 60.0,
        "flatline_per_device_per_day": 0.5,
        "drift_stddev": 0.03,
        "maint_windows_max": 3,
    },
}


@dataclass
class BadDataConfig:
    mode: str = "realistic"
    clock_skew_stddev_s: float = 2.0
    clock_drift_ppm_max: float = 60.0
    loss_rate: float = 0.002
    delay_rate: float = 0.01
    delay_median_s: float = 8.0
    flatline_per_device_per_day: float = 0.05
    drift_stddev: float = 0.005
    maint_windows_max: int = 1

    @classmethod
    def from_mode(cls, mode: str) -> "BadDataConfig":
        if mode not in PRESETS:
            raise ValueError(f"unknown bad-data mode: {mode}")
        return cls(mode=mode, **PRESETS[mode])

    def merge(self, overrides: Dict[str, Any]) -> "BadDataConfig":
        data = self.__dict__.copy()
        data.update({k: v for k, v in overrides.items() if k in data})
        return BadDataConfig(**data)


@dataclass
class BadDataSummary:
    mode: str = "off"
    loss_count: int = 0
    delay_count: int = 0
    flatline_runs: int = 0
    drift_devices: int = 0
    skew_devices: int = 0
    maintenance_windows: int = 0
    maint_records: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


def _iso_to_dt(s: str) -> datetime:
    # accept "...Z" form
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _dt_to_iso(d: datetime) -> str:
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _shift_iso(iso: str, delta_s: float) -> str:
    return _dt_to_iso(_iso_to_dt(iso) + timedelta(seconds=delta_s))


def _device_ids(records: Iterable[Dict[str, Any]]) -> List[str]:
    seen: Dict[str, None] = {}
    for r in records:
        did = r.get("device_id")
        if did and did not in seen:
            seen[did] = None
    return list(seen.keys())


def _apply_clock_skew(records: List[Dict[str, Any]], cfg: BadDataConfig,
                     rng: random.Random, run_start: datetime,
                     summary: BadDataSummary) -> None:
    if cfg.clock_skew_stddev_s <= 0 and cfg.clock_drift_ppm_max <= 0:
        return
    # Draw a persistent offset + linear drift per device (deterministic per seed).
    per_device: Dict[str, Tuple[float, float]] = {}
    for did in _device_ids(records):
        offset = rng.gauss(0.0, cfg.clock_skew_stddev_s) if cfg.clock_skew_stddev_s else 0.0
        drift_ppm = rng.uniform(-cfg.clock_drift_ppm_max, cfg.clock_drift_ppm_max) if cfg.clock_drift_ppm_max else 0.0
        per_device[did] = (offset, drift_ppm)
    summary.skew_devices = len(per_device)
    for r in records:
        did = r.get("device_id")
        if did not in per_device:
            continue
        offset, drift_ppm = per_device[did]
        ts = r.get("source_timestamp") or r.get("timestamp")
        if not ts:
            continue
        src_dt = _iso_to_dt(ts)
        elapsed_s = (src_dt - run_start).total_seconds()
        shift = offset + (drift_ppm * 1e-6) * elapsed_s
        if shift == 0:
            continue
        new_iso = _dt_to_iso(src_dt + timedelta(seconds=shift))
        if "source_timestamp" in r:
            r["source_timestamp"] = new_iso
        if "timestamp" in r:
            r["timestamp"] = new_iso


def _apply_loss(records: List[Dict[str, Any]], cfg: BadDataConfig,
                rng: random.Random, summary: BadDataSummary) -> List[Dict[str, Any]]:
    if cfg.loss_rate <= 0:
        return records
    kept: List[Dict[str, Any]] = []
    dropped = 0
    for r in records:
        if rng.random() < cfg.loss_rate:
            dropped += 1
            continue
        kept.append(r)
    summary.loss_count = dropped
    return kept


def _apply_delay(records: List[Dict[str, Any]], cfg: BadDataConfig,
                 rng: random.Random, summary: BadDataSummary) -> None:
    if cfg.delay_rate <= 0 or cfg.delay_median_s <= 0:
        return
    # lognormal with median = delay_median_s (mu = ln(median)), sigma ~ 0.9 for a wide tail
    mu = math.log(cfg.delay_median_s)
    sigma = 0.9
    delayed = 0
    for r in records:
        if rng.random() >= cfg.delay_rate:
            continue
        delay = rng.lognormvariate(mu, sigma)
        rx = r.get("received_timestamp")
        if not rx:
            continue
        r["received_timestamp"] = _shift_iso(rx, delay)
        delayed += 1
    summary.delay_count = delayed


def _apply_flatline(records: List[Dict[str, Any]], cfg: BadDataConfig,
                    rng: random.Random, hours: int,
                    summary: BadDataSummary) -> None:
    if cfg.flatline_per_device_per_day <= 0 or hours <= 0:
        return
    # group indices per device (records are currently in source-order)
    by_device: Dict[str, List[int]] = {}
    for i, r in enumerate(records):
        by_device.setdefault(r.get("device_id", ""), []).append(i)
    expected_per_device = cfg.flatline_per_device_per_day * (hours / 24.0)
    runs = 0
    for did, idxs in by_device.items():
        if not idxs:
            continue
        # Poisson-ish: draw count by repeated Bernoulli against per-record prob
        per_record_p = expected_per_device / max(len(idxs), 1)
        i = 0
        while i < len(idxs):
            if rng.random() < per_record_p:
                run_len = rng.randint(3, 12)
                # pick primary field from first record in run
                rec0 = records[idxs[i]]
                field = PRIMARY_FIELD.get(rec0.get("device_type", ""))
                if field and field in rec0 and rec0.get(field) is not None:
                    held = rec0[field]
                    for k in range(1, run_len):
                        if i + k >= len(idxs):
                            break
                        tgt = records[idxs[i + k]]
                        if field in tgt:
                            tgt[field] = held
                    runs += 1
                    i += run_len
                    continue
            i += 1
    summary.flatline_runs = runs


def _apply_drift(records: List[Dict[str, Any]], cfg: BadDataConfig,
                 rng: random.Random, summary: BadDataSummary) -> None:
    if cfg.drift_stddev <= 0:
        return
    bias_z_by_device: Dict[str, float] = {}
    for did in _device_ids(records):
        bias_z_by_device[did] = rng.gauss(0.0, 1.0)
    drifted = 0
    touched_devices: set = set()
    for r in records:
        did = r.get("device_id")
        dt = r.get("device_type")
        if not did or not dt:
            continue
        field = PRIMARY_FIELD.get(dt)
        kind_scale = DRIFT_SCALE.get(dt)
        if not field or not kind_scale:
            continue
        if field not in r or r[field] is None:
            continue
        kind, scale = kind_scale
        z = bias_z_by_device[did]
        val = r[field]
        if kind == "relative":
            bias = val * (z * cfg.drift_stddev / 0.005) * scale
        elif kind == "absolute_c":
            bias = z * (cfg.drift_stddev / 0.005) * scale
        else:
            bias = 0.0
        r[field] = round(val + bias, 6) if isinstance(val, float) else val + bias
        drifted += 1
        touched_devices.add(did)
    summary.drift_devices = len(touched_devices)


def _schedule_maintenance(device_ids: List[str], cfg: BadDataConfig,
                         rng: random.Random, run_start: datetime,
                         hours: int) -> List[Dict[str, Any]]:
    if cfg.maint_windows_max <= 0 or not device_ids or hours <= 0:
        return []
    n = rng.randint(max(cfg.maint_windows_max - 1, 0), cfg.maint_windows_max)
    windows: List[Dict[str, Any]] = []
    run_seconds = hours * 3600
    for _ in range(n):
        start_off = rng.uniform(0, max(run_seconds - 900, 900))
        dur_min = rng.choice([15, 30, 45, 60, 90, 120])
        dur_s = dur_min * 60
        end_off = min(start_off + dur_s, run_seconds)
        k = rng.randint(1, max(1, min(3, len(device_ids))))
        affected = rng.sample(device_ids, k=k)
        windows.append({
            "start": run_start + timedelta(seconds=start_off),
            "end": run_start + timedelta(seconds=end_off),
            "devices": affected,
            "id": f"maint_{len(windows) + 1}",
        })
    return windows


def _apply_maintenance(records: List[Dict[str, Any]],
                      events: List[Dict[str, Any]],
                      windows: List[Dict[str, Any]],
                      summary: BadDataSummary) -> None:
    if not windows:
        return
    affected = 0
    for r in records:
        did = r.get("device_id")
        ts = r.get("source_timestamp") or r.get("timestamp")
        if not did or not ts:
            continue
        t = _iso_to_dt(ts)
        for w in windows:
            if did in w["devices"] and w["start"] <= t <= w["end"]:
                r["quality"] = QUALITY_MAINT
                # primary field gets a manual-override sentinel (last-known hold)
                field = PRIMARY_FIELD.get(r.get("device_type", ""))
                if field and field in r:
                    # leave value as-is; quality=MAINT flags it as override
                    pass
                r["maintenance_window_id"] = w["id"]
                affected += 1
                break
    for w in windows:
        for did in w["devices"]:
            events.append({
                "event_type": "maintenance.window_started",
                "timestamp": _dt_to_iso(w["start"]),
                "device_id": did,
                "maintenance_window_id": w["id"],
            })
            events.append({
                "event_type": "maintenance.window_ended",
                "timestamp": _dt_to_iso(w["end"]),
                "device_id": did,
                "maintenance_window_id": w["id"],
            })
    summary.maintenance_windows = len(windows)
    summary.maint_records = affected


def apply_bad_data(records: List[Dict[str, Any]],
                   events: List[Dict[str, Any]],
                   cfg: BadDataConfig,
                   seed: int,
                   run_start: datetime,
                   hours: int) -> Tuple[List[Dict[str, Any]],
                                        List[Dict[str, Any]],
                                        BadDataSummary]:
    """Apply Tier 4 dirty-data pipeline.

    Returns (telemetry, events, summary). The input lists may be mutated
    (records edited in place); the telemetry list return value may be a
    new list due to packet loss. Events list is the same list with
    maintenance events appended.
    """
    summary = BadDataSummary(mode=cfg.mode)
    if cfg.mode == "off":
        return records, events, summary

    rng = random.Random(f"baddata|{seed}|{cfg.mode}")

    # Dimension 5: drift FIRST (bias is intrinsic to sensor output before
    # clock skew / timestamps / delivery artifacts).
    _apply_drift(records, cfg, rng, summary)
    # Dimension 4: flatline (stuck sensor over multiple intervals).
    _apply_flatline(records, cfg, rng, hours, summary)
    # Dimension 6: maintenance windows.
    windows = _schedule_maintenance(_device_ids(records), cfg, rng,
                                    run_start, hours)
    _apply_maintenance(records, events, windows, summary)
    # Dimension 1: clock skew/drift on source_timestamp.
    _apply_clock_skew(records, cfg, rng, run_start, summary)
    # Dimension 2: packet loss (drops records entirely).
    records = _apply_loss(records, cfg, rng, summary)
    # Dimension 3: delivery delay on received_timestamp; then sort by arrival.
    _apply_delay(records, cfg, rng, summary)
    records.sort(key=lambda r: (r.get("received_timestamp") or r.get("timestamp") or ""))

    return records, events, summary
