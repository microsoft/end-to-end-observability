"""Continuous in-memory live device feed for the midstream portal.

Midstream physics is genuinely sequential: tank inventory is integrated step by
step, bearing temperature carries thermal lag, and ``communications_failure``
replays store-and-forward values from earlier samples. Unlike the downstream
feed, samples therefore cannot be evaluated at an arbitrary absolute timestamp.

This feed instead precomputes one bounded horizon with the batch simulator and
plays it back against the wall clock, so the physics shown is exactly the batch
physics with no duplicated model code. The horizon repeats, which is disclosed
in the payload as ``playback.looped``.

No file I/O happens here, so committed artifact determinism is untouched.
"""

from __future__ import annotations

import copy
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ..catalog import build_catalog
from ..config import SCENARIOS, validate_config
from ..simulation import simulate
from .service import SCENARIO_DESCRIPTIONS, MidstreamControlError, MidstreamDashboardError

LIVE_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
LIVE_RUN_ID = "midstream-live"
DEFAULT_INTERVAL_SECONDS = 15
DEFAULT_HORIZON_SECONDS = 3600
DEFAULT_HISTORY_SECONDS = 900
DEFAULT_SPARKLINE_POINTS = 30
MIN_HORIZON_SECONDS = 600
MAX_HORIZON_SECONDS = 6 * 3600
MAX_HISTORY_SECONDS = 3600
MAX_EVENTS = 200

HEALTH_BY_VALIDITY = {
    "good": "nominal",
    "questionable": "degraded",
    "stale": "degraded",
    "missing": "impaired",
}
DEVIATION_WARNING = 0.04
DEVIATION_CRITICAL = 0.08

DEFAULT_SITE = {
    "site_id": "fictional-gulf-transfer-001",
    "display_name": "Gulf Transfer Demonstration",
    "time_zone": "America/Chicago",
    "ambient_temperature_c": 31.0,
}


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _round(value: float) -> float:
    return round(value, 6)


def _live_config(
    scenario: str,
    seed: int,
    interval_seconds: int,
    horizon_seconds: int,
    site: dict[str, Any],
) -> dict[str, Any]:
    end = LIVE_EPOCH + timedelta(seconds=horizon_seconds)
    return {
        "schema_version": "1.0",
        "seed": seed,
        "scenario": scenario,
        "site": copy.deepcopy(site),
        "simulation": {
            "start_utc": _stamp(LIVE_EPOCH),
            "end_utc": _stamp(end),
            "interval_seconds": interval_seconds,
        },
        "fluid": {
            "density_kg_m3": 842.0,
            "viscosity_pa_s": 0.018,
            "temperature_c": 29.0,
        },
        "pipeline": {
            "length_m": 18000.0,
            "diameter_m": 0.508,
            "roughness_m": 0.000045,
            "reference_pressure_kpa": 620.0,
            "effective_bulk_modulus_kpa": 850000.0,
        },
        "tank": {
            "diameter_m": 42.0,
            "height_m": 18.0,
            "initial_volume_m3": 12500.0,
        },
        "pump": {
            "rated_flow_m3_s": 0.22,
            "rated_head_m": 155.0,
            "rated_speed_rpm": 1780.0,
            "efficiency": 0.78,
            "thermal_time_constant_s": 3600.0,
            "coast_down_time_constant_s": 20.0,
        },
        "localization": {
            "enabled": True,
            "monitoring_node_count": 7,
            "hidden_release_route_fraction": 0.62,
            "pressure_measurement_uncertainty_kpa": 0.25,
            "flow_measurement_relative_uncertainty": 0.0015,
            "clock_offset_ms": 8.0,
            "clock_uncertainty_ms": 2.0,
            "confirmation_delay_seconds": 900,
        },
        "output": {"include_csv": False},
    }


class MidstreamLiveFeed:
    """Play a precomputed synthetic horizon back against the wall clock."""

    def __init__(
        self,
        scenario: str = "normal_transfer",
        seed: int = 20260801,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        horizon_seconds: int = DEFAULT_HORIZON_SECONDS,
        history_seconds: int = DEFAULT_HISTORY_SECONDS,
        site: dict[str, Any] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if scenario not in SCENARIOS:
            raise MidstreamControlError("unsupported live scenario")
        self.seed = int(seed)
        self.interval_seconds = max(5, min(int(interval_seconds), 3600))
        horizon = max(
            MIN_HORIZON_SECONDS, min(int(horizon_seconds), MAX_HORIZON_SECONDS)
        )
        # The batch contract requires the horizon to align to the interval.
        # Never let alignment collapse the horizon to zero: an interval larger
        # than the horizon would otherwise produce end_utc == start_utc and
        # raise out of construction, which happens during create_app() and
        # would take the whole shared gateway down.
        self.horizon_seconds = max(
            self.interval_seconds, horizon - (horizon % self.interval_seconds)
        )
        self.history_seconds = max(
            60, min(int(history_seconds), MAX_HISTORY_SECONDS)
        )
        self.site = dict(site or DEFAULT_SITE)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._scenario = scenario
        self._selected_at: datetime | None = None
        # Built horizons are deterministic for a fixed seed and config, so they
        # are cached per scenario. There are only eight scenarios, and a repeat
        # selection then costs nothing instead of two simulate() calls.
        self._cache: dict[str, dict[str, Any]] = {}
        self._apply(self._horizon_for(scenario))

    # ------------------------------------------------------------------
    # Horizon construction
    # ------------------------------------------------------------------

    def _simulate(
        self, scenario: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], Any]:
        config = validate_config(
            _live_config(
                scenario,
                self.seed,
                self.interval_seconds,
                self.horizon_seconds,
                self.site,
            )
        )
        assets, topology = build_catalog(config)
        # simulate() returns private ground truth as its fourth value. It is
        # deliberately discarded here and never stored on this feed.
        telemetry, events, device_logs, _ground_truth = simulate(
            config, LIVE_RUN_ID, assets
        )
        return telemetry, events, assets, topology

    def _horizon_for(self, scenario: str) -> dict[str, Any]:
        cached = self._cache.get(scenario)
        if cached is not None:
            return cached
        computed = self._compute(scenario)
        self._cache[scenario] = computed
        return computed

    def _apply(self, horizon: dict[str, Any]) -> None:
        self._series = horizon["series"]
        self._offsets = horizon["offsets"]
        self._events = horizon["events"]
        self._measurements_by_asset = horizon["measurements_by_asset"]
        self._assets = horizon["assets"]
        self._topology = horizon["topology"]

    def _compute(self, scenario: str) -> dict[str, Any]:
        telemetry, events, assets, topology = self._simulate(scenario)
        if scenario != "normal_transfer":
            baseline, _, _, _ = self._simulate("normal_transfer")
        else:
            baseline = telemetry

        nominal: dict[tuple[str, str, str], float] = {}
        for record in baseline:
            if record["value"] is not None:
                key = (record["asset_id"], record["measurement"], record["event_time_utc"])
                nominal[key] = record["value"]

        series: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for record in telemetry:
            key = (record["asset_id"], record["measurement"])
            reference = nominal.get(
                (record["asset_id"], record["measurement"], record["event_time_utc"])
            )
            deviation = 0.0
            if (
                record["value"] is not None
                and reference is not None
                and abs(reference) > 1e-9
            ):
                deviation = (record["value"] - reference) / abs(reference)
            series[key].append(
                {
                    "event_time_utc": record["event_time_utc"],
                    "source_time_utc": record["source_time_utc"],
                    "value": record["value"],
                    "unit": record["unit"],
                    "validity": record["quality"]["validity"],
                    "reasons": list(record["quality"]["reasons"]),
                    "clock_state": record["clock_state"],
                    "sequence": record["sequence"],
                    "deviation_ratio": _round(deviation),
                    "nominal_value": reference,
                }
            )
        for samples in series.values():
            samples.sort(key=lambda item: item["event_time_utc"])
        built_series = dict(series)
        built_offsets = {
            key: [
                (_parse(item["event_time_utc"]) - LIVE_EPOCH).total_seconds()
                for item in samples
            ]
            for key, samples in built_series.items()
        }
        built_events = sorted(
            (
                {
                    "event_id": event["event_id"],
                    "event_time_utc": event["event_time_utc"],
                    "offset_seconds": (
                        _parse(event["event_time_utc"]) - LIVE_EPOCH
                    ).total_seconds(),
                    "asset_id": event.get("asset_id", ""),
                    "event_type": event.get("event_type", ""),
                    "severity": event.get("severity", ""),
                    "state_from": (event.get("state_transition") or {}).get("from", ""),
                    "state_to": (event.get("state_transition") or {}).get("to", ""),
                }
                for event in events
            ),
            key=lambda item: item["offset_seconds"],
        )
        measurements_by_asset: dict[str, list[str]] = defaultdict(list)
        for asset_id, measurement in sorted(built_series):
            measurements_by_asset[asset_id].append(measurement)
        return {
            "series": built_series,
            "offsets": built_offsets,
            "events": built_events,
            "measurements_by_asset": measurements_by_asset,
            "assets": {asset["asset_id"]: asset for asset in assets},
            "topology": topology,
        }

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    def _position(self, now: datetime) -> tuple[float, int]:
        elapsed = (now - LIVE_EPOCH).total_seconds()
        if self.horizon_seconds <= 0:
            return 0.0, 0
        return elapsed % self.horizon_seconds, int(elapsed // self.horizon_seconds)

    @staticmethod
    def _index_at(offsets: list[float], position: float) -> int:
        low, high = 0, len(offsets)
        while low < high:
            middle = (low + high) // 2
            if offsets[middle] <= position:
                low = middle + 1
            else:
                high = middle
        return low - 1

    def _window(
        self, key: tuple[str, str], position: float
    ) -> tuple[list[dict[str, Any]], int]:
        offsets = self._offsets[key]
        index = self._index_at(offsets, position)
        if index < 0:
            return [], -1
        floor = position - self.history_seconds
        start = index
        while start > 0 and offsets[start - 1] >= floor:
            start -= 1
        return self._series[key][start : index + 1], index

    def _health(self, window: list[dict[str, Any]]) -> tuple[str, str]:
        if not window:
            return "unknown", "no_samples"
        latest = window[-1]
        health = HEALTH_BY_VALIDITY.get(latest["validity"], "degraded")
        reason = latest["reasons"][0] if latest["reasons"] else latest["validity"]
        recent = window[-6:]
        if health == "nominal" and any(
            item["validity"] != "good" for item in recent
        ):
            health, reason = "degraded", "recent_delivery_fault"
        magnitude = abs(latest["deviation_ratio"])
        if magnitude >= DEVIATION_CRITICAL:
            health, reason = "impaired", "process_deviation"
        elif magnitude >= DEVIATION_WARNING and health == "nominal":
            health, reason = "degraded", "process_deviation"
        return health, reason

    @staticmethod
    def _sparkline(window: list[dict[str, Any]], points: int) -> list[float | None]:
        values = [item["value"] for item in window]
        if len(values) <= points:
            return values
        step = (len(values) - 1) / (points - 1)
        return [values[round(index * step)] for index in range(points)]

    def _measurement_view(
        self,
        asset_id: str,
        measurement: str,
        position: float,
        points: int,
        include_history: bool = False,
    ) -> dict[str, Any]:
        window, index = self._window((asset_id, measurement), position)
        latest = window[-1] if window else None
        health, reason = self._health(window)
        view = {
            "asset_id": asset_id,
            "measurement": measurement,
            "value": latest["value"] if latest else None,
            "unit": latest["unit"] if latest else None,
            "validity": latest["validity"] if latest else "unknown",
            "reasons": latest["reasons"] if latest else [],
            "clock_state": latest["clock_state"] if latest else None,
            "event_time_utc": latest["event_time_utc"] if latest else None,
            "source_time_utc": latest["source_time_utc"] if latest else None,
            "deviation_ratio": latest["deviation_ratio"] if latest else 0.0,
            "health": health,
            "health_reason": reason,
            "sample_count": len(window),
            "sparkline": self._sparkline(window, points),
        }
        if include_history:
            view["history"] = [
                {
                    "event_time_utc": item["event_time_utc"],
                    "value": item["value"],
                    "validity": item["validity"],
                    "deviation_ratio": item["deviation_ratio"],
                }
                for item in window
            ]
        return view

    def _asset_view(
        self,
        asset_id: str,
        position: float,
        points: int,
        include_history: bool = False,
    ) -> dict[str, Any]:
        asset = self._assets[asset_id]
        measurements = [
            self._measurement_view(
                asset_id, measurement, position, points, include_history
            )
            for measurement in self._measurements_by_asset[asset_id]
        ]
        ranking = {"nominal": 0, "degraded": 1, "impaired": 2, "unknown": 1}
        health = "nominal"
        for item in measurements:
            if ranking.get(item["health"], 0) > ranking.get(health, 0):
                health = item["health"]
        deviation = max(
            (abs(item["deviation_ratio"]) for item in measurements), default=0.0
        )
        return {
            "asset_id": asset_id,
            "asset_type": asset["asset_type"],
            "generic_model": asset.get("generic_model"),
            "route_id": asset.get("route_id"),
            "route_distance_m": asset.get("route_distance_m"),
            "health": health,
            "deviation_ratio": _round(deviation),
            "measurement_count": len(measurements),
            "measurements": measurements,
        }

    def _scenario_view(self) -> dict[str, Any]:
        return {
            "id": self._scenario,
            "description": SCENARIO_DESCRIPTIONS[self._scenario],
            "selected_at_utc": _stamp(self._selected_at) if self._selected_at else None,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def snapshot(self, sparkline_points: int = DEFAULT_SPARKLINE_POINTS) -> dict[str, Any]:
        points = max(2, min(int(sparkline_points), 120))
        with self._lock:
            now = self._clock()
            position, cycle = self._position(now)
            assets = [
                self._asset_view(asset_id, position, points)
                for asset_id in sorted(self._measurements_by_asset)
            ]
            events = [
                {
                    key: value
                    for key, value in event.items()
                    if key != "offset_seconds"
                }
                for event in self._events
                if event["offset_seconds"] <= position
            ]
            scenario = self._scenario_view()
        health_counts = {"nominal": 0, "degraded": 0, "impaired": 0, "unknown": 0}
        for asset in assets:
            health_counts[asset["health"]] += 1
        events.reverse()
        return {
            "generated_at_utc": _stamp(now),
            "site": dict(self.site),
            "scenario": scenario,
            "scenarios": [
                {"id": name, "description": SCENARIO_DESCRIPTIONS[name]}
                for name in sorted(SCENARIOS)
            ],
            "playback": {
                "looped": True,
                "horizon_seconds": self.horizon_seconds,
                "interval_seconds": self.interval_seconds,
                "history_seconds": self.history_seconds,
                "position_seconds": _round(position),
                "cycle": cycle,
            },
            "summary": {
                "asset_count": len(assets),
                "measurement_count": sum(
                    asset["measurement_count"] for asset in assets
                ),
                "samples_in_window": sum(
                    item["sample_count"]
                    for asset in assets
                    for item in asset["measurements"]
                ),
                "nominal": health_counts["nominal"],
                "degraded": health_counts["degraded"],
                "impaired": health_counts["impaired"],
                "event_count": len(events),
            },
            "assets": assets,
            "events": events[:MAX_EVENTS],
            "is_synthetic": True,
            "operational_use": "prohibited",
            "private_truth_publication": "excluded",
        }

    def asset_detail(self, asset_id: str) -> dict[str, Any]:
        with self._lock:
            if asset_id not in self._measurements_by_asset:
                raise MidstreamDashboardError("live asset is unavailable")
            now = self._clock()
            position, cycle = self._position(now)
            view = self._asset_view(
                asset_id, position, DEFAULT_SPARKLINE_POINTS, True
            )
            events = [
                {
                    key: value
                    for key, value in event.items()
                    if key != "offset_seconds"
                }
                for event in self._events
                if event["asset_id"] == asset_id
                and event["offset_seconds"] <= position
            ]
            scenario = self._scenario_view()
        events.reverse()
        return {
            "generated_at_utc": _stamp(now),
            "history_seconds": self.history_seconds,
            "site": dict(self.site),
            "scenario": scenario,
            "playback": {"looped": True, "cycle": cycle},
            "asset": view,
            "events": events[:50],
            "is_synthetic": True,
            "operational_use": "prohibited",
        }

    def select_scenario(self, scenario: str) -> dict[str, Any]:
        if not isinstance(scenario, str) or scenario not in SCENARIOS:
            raise MidstreamControlError("unsupported live scenario")
        # Build outside the lock. Rebuilding runs simulate() twice, which is
        # hundreds of times more expensive than a snapshot read, so holding the
        # lock across it would stall every concurrent reader on this instance.
        # _horizon_for is deterministic, so a concurrent duplicate build is
        # wasteful but never inconsistent.
        horizon = self._horizon_for(scenario)
        with self._lock:
            if scenario != self._scenario:
                self._apply(horizon)
                self._scenario = scenario
            self._selected_at = self._clock()
            return self._scenario_view()

    def status(self) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            position, cycle = self._position(now)
            return {
                "enabled": True,
                "site": dict(self.site),
                "scenario": self._scenario_view(),
                "playback": {
                    "looped": True,
                    "horizon_seconds": self.horizon_seconds,
                    "interval_seconds": self.interval_seconds,
                    "history_seconds": self.history_seconds,
                    "position_seconds": _round(position),
                    "cycle": cycle,
                },
                "asset_count": len(self._measurements_by_asset),
                "is_synthetic": True,
                "operational_use": "prohibited",
            }


__all__ = ["LIVE_EPOCH", "MidstreamLiveFeed"]
