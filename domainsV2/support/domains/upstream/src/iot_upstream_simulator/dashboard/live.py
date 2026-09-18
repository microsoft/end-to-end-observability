"""Continuous in-memory live device feed for the upstream wellpad portal.

``simulate()`` is a batch function over one whole window: pressures, lift
behaviour, tank inventory and the sparse workflow evidence are all produced
together and are genuinely sequential, so samples cannot be evaluated at an
arbitrary absolute timestamp.

This feed therefore precomputes one bounded horizon with the batch simulator
and plays it back against the wall clock, so the physics shown is exactly the
batch physics with no duplicated model code. The horizon repeats, which is
disclosed in the payload as ``playback.looped``.

No file I/O happens here, so committed artifact determinism is untouched. The
private ``ground_truth`` family returned by ``simulate()`` is discarded on
arrival and is never stored on this object.
"""

from __future__ import annotations

import copy
import threading
from collections import OrderedDict, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ..catalog import build_catalog
from ..config import (
    INTERVAL_SECONDS_CHOICES,
    MAX_DURATION_SECONDS,
    MAX_TICKS,
    SCENARIOS,
    validate_config,
)
from ..simulation import simulate
from .service import (
    SCENARIO_DESCRIPTIONS,
    UpstreamControlError,
    UpstreamDashboardError,
)

LIVE_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
LIVE_RUN_ID = "upstream-live"
BASELINE_SCENARIO = "normal_production"
# The baseline plus two non-baseline horizons. Enough that flipping between a
# scenario and the baseline stays free, small enough that all 19 scenarios can
# never be retained at once on the shared instance.
MAX_CACHED_HORIZONS = 3
DEFAULT_INTERVAL_SECONDS = 15
DEFAULT_HORIZON_SECONDS = 3600
DEFAULT_HISTORY_SECONDS = 900
DEFAULT_SPARKLINE_POINTS = 30
MIN_HORIZON_SECONDS = 600
MAX_HORIZON_SECONDS = MAX_DURATION_SECONDS
MAX_HISTORY_SECONDS = 3600
MAX_EVENTS = 200

HEALTH_BY_QUALITY = {
    "good": "nominal",
    "uncertain": "degraded",
    "bad": "impaired",
}
DEGRADED_DELIVERY_STATES = frozenset(
    {"late", "stale", "duplicate", "out_of_order", "store_and_forward"}
)
IMPAIRED_DELIVERY_STATES = frozenset({"missing"})
HEALTH_RANK = {"unknown": 0, "nominal": 1, "degraded": 2, "impaired": 3}
DEVIATION_WARNING = 0.04
DEVIATION_CRITICAL = 0.08

DEFAULT_SITE = {
    "site_id": "fictional-onshore-upstream-field-001",
    "display_name": "Fictional Onshore Upstream Field",
    "time_zone": "America/Chicago",
}
DEFAULT_FACILITY = {
    "facility_id": "fictional-central-production-001",
    "display_name": "Fictional Central Production Facility",
}
#: Bounded live fleet. The shared gateway runs on one small instance, so the
#: live horizon is deliberately smaller than the committed sample profile.
LIVE_FLEET = {
    "oil_well_pads": 1,
    "gas_well_pads": 1,
    "multiwell_pads": 1,
    "wells_per_multiwell_pad": 2,
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
    facility: dict[str, Any],
) -> dict[str, Any]:
    end = LIVE_EPOCH + timedelta(seconds=horizon_seconds)
    return {
        "schema_version": "1.0",
        "seed": seed,
        "scenario": scenario,
        "site": copy.deepcopy(site),
        "facility": copy.deepcopy(facility),
        "simulation": {
            "start_utc": _stamp(LIVE_EPOCH),
            "end_utc": _stamp(end),
            "interval_seconds": interval_seconds,
        },
        "fleet": copy.deepcopy(LIVE_FLEET),
        "fidelity": {
            "measurement_effects": True,
            "delivery_effects": True,
            "sparse_workflows": True,
        },
        "monitoring": {
            "max_records_per_type": 20000,
            "max_total_records": 120000,
        },
    }


class UpstreamLiveFeed:
    """Play a precomputed synthetic horizon back against the wall clock."""

    def __init__(
        self,
        scenario: str = BASELINE_SCENARIO,
        seed: int = 20260819,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
        horizon_seconds: int = DEFAULT_HORIZON_SECONDS,
        history_seconds: int = DEFAULT_HISTORY_SECONDS,
        site: dict[str, Any] | None = None,
        facility: dict[str, Any] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if scenario not in SCENARIOS:
            raise UpstreamControlError("unsupported live scenario")
        self.seed = int(seed)
        self.interval_seconds = self._clamp_interval(interval_seconds)
        self.horizon_seconds = self._clamp_horizon(
            horizon_seconds, self.interval_seconds
        )
        self.history_seconds = max(
            self.interval_seconds,
            min(int(history_seconds), MAX_HISTORY_SECONDS, self.horizon_seconds),
        )
        self.site = dict(site or DEFAULT_SITE)
        self.facility = dict(facility or DEFAULT_FACILITY)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._scenario = scenario
        self._selected_at: datetime | None = None
        # Built horizons are deterministic for a fixed seed and config, so they
        # are cached per scenario. A repeat selection then costs nothing instead
        # of two more simulate() calls. The cache is bounded: with 19 scenarios
        # an unbounded cache would retain far more than this shared B1 instance
        # can spare, and starving it degrades every other domain and the health
        # probes, not just this feed.
        self._cache: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
        self._cache_lock = threading.Lock()
        self._apply(self._horizon_for(scenario))

    # ------------------------------------------------------------------
    # Bounds
    # ------------------------------------------------------------------

    @staticmethod
    def _clamp_interval(interval_seconds: Any) -> int:
        try:
            requested = int(interval_seconds)
        except (TypeError, ValueError):
            requested = DEFAULT_INTERVAL_SECONDS
        # The batch contract only accepts a fixed set of cadences, so snap to
        # the nearest legal choice instead of letting validate_config raise
        # out of the constructor. This runs inside create_app() on the shared
        # gateway, where a raise would take every other domain down with it.
        return min(
            INTERVAL_SECONDS_CHOICES,
            key=lambda choice: (abs(choice - requested), choice),
        )

    @staticmethod
    def _clamp_horizon(horizon_seconds: Any, interval_seconds: int) -> int:
        try:
            requested = int(horizon_seconds)
        except (TypeError, ValueError):
            requested = DEFAULT_HORIZON_SECONDS
        ceiling = min(MAX_HORIZON_SECONDS, interval_seconds * MAX_TICKS)
        floor = max(interval_seconds, min(MIN_HORIZON_SECONDS, ceiling))
        bounded = max(floor, min(requested, ceiling))
        aligned = bounded - (bounded % interval_seconds)
        # Alignment must never collapse the horizon to zero: an interval larger
        # than the requested horizon would otherwise produce end_utc ==
        # start_utc and raise ConfigError out of construction.
        return max(interval_seconds, aligned)

    # ------------------------------------------------------------------
    # Horizon construction
    # ------------------------------------------------------------------

    def _simulate(
        self, scenario: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        config = validate_config(
            _live_config(
                scenario,
                self.seed,
                self.interval_seconds,
                self.horizon_seconds,
                self.site,
                self.facility,
            )
        )
        catalog = build_catalog(config)
        result = simulate(config, LIVE_RUN_ID, catalog)
        # simulate() also returns the private ground truth family. It is
        # deliberately dropped here and never stored on this feed.
        telemetry = list(result["telemetry"])
        events = list(result["events"])
        return telemetry, events, catalog

    def _horizon_for(self, scenario: str) -> dict[str, Any]:
        with self._cache_lock:
            cached = self._cache.get(scenario)
            if cached is not None:
                self._cache.move_to_end(scenario)
                return cached
        # Computed outside the cache lock; _compute is deterministic, so a
        # concurrent duplicate build is wasteful but never inconsistent.
        computed = self._compute(scenario)
        with self._cache_lock:
            self._cache[scenario] = computed
            self._cache.move_to_end(scenario)
            self._evict_locked()
        return computed

    def _evict_locked(self) -> None:
        """Drop least-recently-used horizons, always keeping the baseline.

        The caller must hold ``_cache_lock``. The baseline is exempt because
        every non-baseline horizon is scored against it, so evicting it would
        force an immediate rebuild.
        """
        while len(self._cache) > MAX_CACHED_HORIZONS:
            for candidate in self._cache:
                if candidate != BASELINE_SCENARIO:
                    del self._cache[candidate]
                    break
            else:  # pragma: no cover - only the baseline remains
                return

    def _apply(self, horizon: dict[str, Any]) -> None:
        self._series = horizon["series"]
        self._offsets = horizon["offsets"]
        self._events = horizon["events"]
        self._measurements_by_asset = horizon["measurements_by_asset"]
        self._assets = horizon["assets"]
        self._wells = horizon["wells"]

    def _compute(self, scenario: str) -> dict[str, Any]:
        telemetry, events, catalog = self._simulate(scenario)
        if scenario != BASELINE_SCENARIO:
            baseline, _, _ = self._simulate(BASELINE_SCENARIO)
        else:
            baseline = telemetry

        nominal: dict[tuple[str, str, str], float] = {}
        for record in baseline:
            if record["value"] is not None:
                nominal[
                    (record["asset_id"], record["measurement"], record["event_time_utc"])
                ] = record["value"]

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
                    "quality_class": record["quality_class"],
                    "quality_reason": record["quality_reason"],
                    "freshness_state": record["freshness_state"],
                    "origin_state": record["origin_state"],
                    "delivery_state": record["delivery_state"],
                    "sequence": record["sequence"],
                    "fidelity": record["fidelity"],
                    "deviation_ratio": _round(deviation),
                    "nominal_value": reference,
                }
            )
        for samples in series.values():
            samples.sort(key=lambda item: (item["event_time_utc"], item["sequence"]))
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
                    "asset_id": event.get("asset_id") or "",
                    "area_id": event.get("area_id", ""),
                    "well_id": event.get("well_id") or "",
                    "event_type": event.get("event_type", ""),
                    "event_class": event.get("event_class", ""),
                    "severity": event.get("severity", ""),
                    "interpretation": event.get("interpretation", ""),
                    "summary": event.get("summary", ""),
                    "state_from": (event.get("state_transition") or {}).get("from", ""),
                    "state_to": (event.get("state_transition") or {}).get("to", ""),
                }
                for event in events
            ),
            key=lambda item: (item["offset_seconds"], item["event_id"]),
        )
        measurements_by_asset: dict[str, list[str]] = defaultdict(list)
        for asset_id, measurement in sorted(built_series):
            measurements_by_asset[asset_id].append(measurement)
        return {
            "series": built_series,
            "offsets": built_offsets,
            "events": built_events,
            "measurements_by_asset": dict(measurements_by_asset),
            "assets": {asset["asset_id"]: asset for asset in catalog["assets"]},
            "wells": {well["well_id"]: well for well in catalog["wells"]},
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
        health = HEALTH_BY_QUALITY.get(latest["quality_class"], "degraded")
        reason = latest["quality_reason"] or latest["quality_class"]
        if latest["delivery_state"] in IMPAIRED_DELIVERY_STATES:
            health, reason = "impaired", "delivery_unavailable"
        elif latest["delivery_state"] in DEGRADED_DELIVERY_STATES and health == "nominal":
            health, reason = "degraded", "delivery_degraded"
        recent = window[-6:]
        # Baseline delivery jitter puts a fault in roughly one sample in ten, so
        # a single blip must not degrade an asset. Only a sustained pattern in
        # the recent window escalates.
        faulted = sum(
            1
            for item in recent
            if item["quality_class"] != "good"
            or item["delivery_state"] in DEGRADED_DELIVERY_STATES
            or item["delivery_state"] in IMPAIRED_DELIVERY_STATES
        )
        if health == "nominal" and faulted >= 2:
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
        window, _index = self._window((asset_id, measurement), position)
        latest = window[-1] if window else None
        health, reason = self._health(window)
        view = {
            "asset_id": asset_id,
            "measurement": measurement,
            "value": latest["value"] if latest else None,
            "unit": latest["unit"] if latest else None,
            "quality_class": latest["quality_class"] if latest else "unknown",
            "quality_reason": latest["quality_reason"] if latest else "no_samples",
            "freshness_state": latest["freshness_state"] if latest else "unknown",
            "origin_state": latest["origin_state"] if latest else None,
            "delivery_state": latest["delivery_state"] if latest else None,
            "fidelity": latest["fidelity"] if latest else None,
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
                    "quality_class": item["quality_class"],
                    "delivery_state": item["delivery_state"],
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
        health = "nominal"
        for item in measurements:
            if HEALTH_RANK.get(item["health"], 0) > HEALTH_RANK.get(health, 0):
                health = item["health"]
        deviation = max(
            (abs(item["deviation_ratio"]) for item in measurements), default=0.0
        )
        well = self._wells.get(asset.get("well_id") or "")
        return {
            "asset_id": asset_id,
            "asset_type": asset["asset_type"],
            "generic_model": asset.get("generic_model"),
            "display_name": asset.get("display_name"),
            "archetype": asset.get("archetype"),
            "area_id": asset.get("area_id"),
            "area_name": asset.get("area_name"),
            "well_id": asset.get("well_id"),
            "well_display_name": well.get("display_name") if well else None,
            "lift_type": well.get("lift_type") if well else None,
            "pad_id": asset.get("pad_id"),
            "fidelity": asset.get("fidelity"),
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
            "facility": dict(self.facility),
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
                "unknown": health_counts["unknown"],
                "event_count": len(events),
                "attention_event_count": sum(
                    1 for event in events if event["severity"] == "attention"
                ),
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
                raise UpstreamDashboardError("live asset is unavailable")
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
            "facility": dict(self.facility),
            "scenario": scenario,
            "playback": {"looped": True, "cycle": cycle},
            "asset": view,
            "events": events[:50],
            "is_synthetic": True,
            "operational_use": "prohibited",
        }

    def select_scenario(self, scenario: str) -> dict[str, Any]:
        if not isinstance(scenario, str) or scenario not in SCENARIOS:
            raise UpstreamControlError("unsupported live scenario")
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
                "facility": dict(self.facility),
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


__all__ = ["LIVE_EPOCH", "UpstreamLiveFeed"]
