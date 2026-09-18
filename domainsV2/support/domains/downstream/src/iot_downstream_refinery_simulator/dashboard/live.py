"""Continuous live device telemetry for the fictional downstream refinery.

The live feed is a wall-clock *view* over the same synthetic physics used by
:mod:`iot_downstream_refinery_simulator.simulation`. It never writes artifacts
and therefore cannot affect the byte-for-byte determinism of generated runs.

Every sample is a pure function of the tag, the scheduled UTC instant, the feed
seed, and the active scenario onset, so a restarted process resumes the same
series and every reader observes identical values for the same instant.
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from ..catalog import build_catalog
from ..config import SCENARIOS
from ..simulation import (
    AREA_INDEX,
    _base_state,
    _delivery_adjustment,
    _hash_float,
    _measurement_value,
    _round,
    _scenario_physical_delta,
    _stable_id,
    _stamp,
)
from .service import (
    SCENARIO_DESCRIPTIONS,
    DownstreamControlError,
    DownstreamDashboardError,
)

LIVE_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
BASE_TICK_SECONDS = 5
DEFAULT_SEED = 20260101
DEFAULT_CYCLE_SECONDS = 900
DEFAULT_HISTORY_SECONDS = 300
DEFAULT_SPARKLINE_POINTS = 30
MAX_HISTORY_SECONDS = 1800
MAX_EVENTS = 200
DEVIATION_WARNING = 0.04
DEVIATION_CRITICAL = 0.08

DEFAULT_SITE = {
    "site_id": "fictional-downstream-refinery-001",
    "display_name": "Fictional Downstream Refinery",
    "time_zone": "America/Chicago",
}

HEALTH_BY_VALIDITY = {
    "good": "nominal",
    "uncertain": "degraded",
    "bad": "impaired",
}
HEALTH_RANK = {"nominal": 0, "degraded": 1, "impaired": 2}
DELIVERY_EVENT_SEVERITY = {
    "missing": "critical",
    "stale": "warning",
    "late": "warning",
    "store_and_forward": "info",
}


def _align(value: datetime) -> datetime:
    aligned = value.replace(microsecond=0)
    return aligned - timedelta(seconds=aligned.second % BASE_TICK_SECONDS)


def _parse_stamp(value: str) -> datetime:
    return datetime.fromisoformat(value[:-1] + "+00:00")


class DownstreamLiveFeed:
    """Serve a continuously advancing per-device view of synthetic telemetry."""

    def __init__(
        self,
        site: dict[str, Any] | None = None,
        scenario: str = "normal_run",
        seed: int = DEFAULT_SEED,
        cycle_seconds: int = DEFAULT_CYCLE_SECONDS,
        history_seconds: int = DEFAULT_HISTORY_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if scenario not in SCENARIOS:
            raise DownstreamControlError("unsupported live scenario")
        if cycle_seconds < 60:
            raise ValueError("cycle_seconds must be at least 60")
        self.site = dict(site or DEFAULT_SITE)
        self.seed = int(seed)
        self.cycle_seconds = int(cycle_seconds)
        self.history_seconds = max(60, min(int(history_seconds), MAX_HISTORY_SECONDS))
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()

        assets, devices, tags, _topology = build_catalog({"site": self.site})
        self._assets = {item["asset_id"]: item for item in assets}
        self._devices = sorted(devices, key=lambda item: (item["area_id"], item["device_index"]))
        self._tags_by_device: dict[str, list[dict[str, Any]]] = {}
        for tag in sorted(tags, key=lambda item: (item["area_id"], item["tag_index"])):
            self._tags_by_device.setdefault(tag["device_id"], []).append(tag)

        self._series: dict[str, deque[dict[str, Any]]] = {
            tag["tag_id"]: deque() for tag in tags
        }
        self._events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
        self._scenario = scenario
        self._scenario_onset = LIVE_EPOCH
        self._scenario_selected_at: datetime | None = None
        self._cursor: datetime | None = None

    # ------------------------------------------------------------------
    # Sample evaluation
    # ------------------------------------------------------------------

    def _fraction(self, moment: datetime) -> float:
        elapsed = (moment - LIVE_EPOCH).total_seconds()
        return (elapsed % self.cycle_seconds) / self.cycle_seconds

    @staticmethod
    def _sequence(moment: datetime, cadence_seconds: int) -> int:
        return int((moment - LIVE_EPOCH).total_seconds() // cadence_seconds) + 1

    def _sample(
        self,
        tag: dict[str, Any],
        scheduled_time: datetime,
        scenario: str,
        onset: datetime,
        previous: dict[str, Any] | None,
    ) -> dict[str, Any]:
        area_id = tag["area_id"]
        measurement = tag["measurement"]
        fraction = self._fraction(scheduled_time)
        sequence = self._sequence(scheduled_time, tag["cadence_seconds"])
        noise = _hash_float(self.seed, tag["tag_id"], str(sequence), low=-0.006, high=0.006)

        nominal_state = _base_state(AREA_INDEX[area_id], fraction)
        nominal_value = _measurement_value(measurement, nominal_state) + noise

        active = scheduled_time >= onset
        delta = _scenario_physical_delta(scenario, area_id, active)
        state = {key: nominal_state[key] + delta[key] for key in nominal_state}
        measured_value = _measurement_value(measurement, state) + noise

        (
            delivered_value,
            source_time_utc,
            ingest_time,
            delivery_class,
            quality,
            clock_state,
            clock_offset_ms,
            clock_uncertainty_ms,
        ) = _delivery_adjustment(
            {"seed": self.seed},
            scenario,
            area_id,
            measurement,
            scheduled_time,
            onset,
            sequence,
            measured_value,
            previous,
        )

        deviation_ratio = 0.0
        if delivered_value is not None:
            scale = max(abs(nominal_value), 1e-6)
            deviation_ratio = (delivered_value - nominal_value) / scale

        return {
            "telemetry_id": _stable_id("live", tag["tag_id"], _stamp(scheduled_time)),
            "tag_id": tag["tag_id"],
            "device_id": tag["device_id"],
            "asset_id": tag["asset_id"],
            "area_id": area_id,
            "measurement": measurement,
            "unit": tag["unit"],
            "cadence_class": tag["cadence_class"],
            "cadence_seconds": tag["cadence_seconds"],
            "fidelity": tag["fidelity"],
            "event_time_utc": _stamp(scheduled_time),
            "source_time_utc": source_time_utc,
            "ingest_time_utc": _stamp(ingest_time),
            "value": delivered_value,
            "nominal_value": _round(nominal_value),
            "deviation_ratio": _round(deviation_ratio),
            "delivery_class": delivery_class,
            "quality": quality,
            "clock_state": clock_state,
            "clock_offset_ms": clock_offset_ms,
            "clock_uncertainty_ms": clock_uncertainty_ms,
            "sequence": sequence,
            "scenario_active": active,
        }

    # ------------------------------------------------------------------
    # Advancing the feed
    # ------------------------------------------------------------------

    def _scheduled_times(
        self,
        tag: dict[str, Any],
        window_start: datetime,
        now: datetime,
    ) -> Iterable[datetime]:
        cadence = tag["cadence_seconds"]
        series = self._series[tag["tag_id"]]
        if series:
            first = _parse_stamp(series[-1]["event_time_utc"]) + timedelta(seconds=cadence)
        else:
            offset = int((window_start - LIVE_EPOCH).total_seconds())
            ticks = -(-offset // cadence)
            first = LIVE_EPOCH + timedelta(seconds=ticks * cadence)
        current = first
        while current <= now:
            yield current
            current += timedelta(seconds=cadence)

    def _advance(self) -> datetime:
        now = _align(self._clock())
        window_start = now - timedelta(seconds=self.history_seconds)
        scenario = self._scenario
        onset = self._scenario_onset
        for device in self._devices:
            for tag in self._tags_by_device[device["device_id"]]:
                series = self._series[tag["tag_id"]]
                if series:
                    last = _parse_stamp(series[-1]["event_time_utc"])
                    if last < window_start or last > now:
                        series.clear()
                for scheduled_time in self._scheduled_times(tag, window_start, now):
                    previous = series[-1] if series else None
                    sample = self._sample(tag, scheduled_time, scenario, onset, previous)
                    series.append(sample)
                    self._record_events(device, tag, sample)
                while series and _parse_stamp(series[0]["event_time_utc"]) < window_start:
                    series.popleft()
        self._cursor = now
        return now

    def _record_events(
        self,
        device: dict[str, Any],
        tag: dict[str, Any],
        sample: dict[str, Any],
    ) -> None:
        severity = DELIVERY_EVENT_SEVERITY.get(sample["delivery_class"])
        if severity is not None:
            reasons = sample["quality"]["reasons"] or [sample["delivery_class"]]
            self._append_event(device, tag, sample, severity, reasons[0], "delivery")
            return
        magnitude = abs(sample["deviation_ratio"])
        if magnitude >= DEVIATION_CRITICAL:
            self._append_event(device, tag, sample, "critical", "process_deviation", "process")
        elif magnitude >= DEVIATION_WARNING:
            self._append_event(device, tag, sample, "warning", "process_drift", "process")

    def _append_event(
        self,
        device: dict[str, Any],
        tag: dict[str, Any],
        sample: dict[str, Any],
        severity: str,
        reason: str,
        category: str,
    ) -> None:
        self._events.append(
            {
                "event_id": _stable_id("liveevent", sample["telemetry_id"], reason),
                "event_time_utc": sample["event_time_utc"],
                "severity": severity,
                "category": category,
                "reason": reason,
                "site_id": self.site["site_id"],
                "area_id": tag["area_id"],
                "device_id": device["device_id"],
                "device_name": device["display_name"],
                "asset_id": tag["asset_id"],
                "tag_id": tag["tag_id"],
                "measurement": tag["measurement"],
                "value": sample["value"],
                "unit": tag["unit"],
                "deviation_ratio": sample["deviation_ratio"],
                "delivery_class": sample["delivery_class"],
                "scenario": self._scenario,
                "is_synthetic": True,
                "operational_use": "prohibited",
            }
        )

    # ------------------------------------------------------------------
    # Projections
    # ------------------------------------------------------------------

    @staticmethod
    def _sparkline(series: Iterable[dict[str, Any]], limit: int) -> list[float | None]:
        values = [item["value"] for item in series]
        if len(values) <= limit:
            return values
        step = len(values) / limit
        return [values[min(len(values) - 1, int(index * step))] for index in range(limit)]

    @staticmethod
    def _tag_health(
        series: list[dict[str, Any]],
        quality: dict[str, Any],
        deviation_ratio: float | None,
        age_seconds: int | None,
        tag: dict[str, Any],
    ) -> str:
        health = HEALTH_BY_VALIDITY.get(quality["validity"], "impaired")
        for item in series[-6:]:
            candidate = HEALTH_BY_VALIDITY.get(item["quality"]["validity"], "impaired")
            if candidate == "impaired":
                candidate = "degraded"
            if HEALTH_RANK[candidate] > HEALTH_RANK[health]:
                health = candidate
        magnitude = abs(deviation_ratio or 0.0)
        if magnitude >= DEVIATION_CRITICAL:
            escalated = "impaired"
        elif magnitude >= DEVIATION_WARNING:
            escalated = "degraded"
        else:
            escalated = "nominal"
        if HEALTH_RANK[escalated] > HEALTH_RANK[health]:
            health = escalated
        if age_seconds is not None and age_seconds > tag["cadence_seconds"] * 3:
            if HEALTH_RANK["degraded"] > HEALTH_RANK[health]:
                health = "degraded"
        return health

    def _tag_view(
        self,
        tag: dict[str, Any],
        now: datetime,
        sparkline_points: int,
        with_history: bool,
    ) -> dict[str, Any]:
        series = list(self._series[tag["tag_id"]])
        latest = series[-1] if series else None
        delivered = [item for item in series if item["value"] is not None]
        previous_value = delivered[-2]["value"] if len(delivered) >= 2 else None
        current_value = delivered[-1]["value"] if delivered else None
        trend = "flat"
        if current_value is not None and previous_value is not None:
            if current_value > previous_value:
                trend = "rising"
            elif current_value < previous_value:
                trend = "falling"
        values = [item["value"] for item in delivered]
        quality = latest["quality"] if latest else {"validity": "bad", "reasons": ["no_samples"]}
        deviation_ratio = latest["deviation_ratio"] if latest else None
        age_seconds = (
            int((now - _parse_stamp(latest["source_time_utc"])).total_seconds())
            if latest
            else None
        )
        health = self._tag_health(series, quality, deviation_ratio, age_seconds, tag)
        view = {
            "tag_id": tag["tag_id"],
            "measurement": tag["measurement"],
            "unit": tag["unit"],
            "cadence_class": tag["cadence_class"],
            "cadence_seconds": tag["cadence_seconds"],
            "fidelity": tag["fidelity"],
            "value": latest["value"] if latest else None,
            "last_delivered_value": current_value,
            "previous_value": previous_value,
            "trend": trend,
            "window_min": _round(min(values)) if values else None,
            "window_max": _round(max(values)) if values else None,
            "nominal_value": latest["nominal_value"] if latest else None,
            "deviation_ratio": deviation_ratio,
            "delivery_class": latest["delivery_class"] if latest else "missing",
            "quality": quality,
            "health": health,
            "clock_state": latest["clock_state"] if latest else "unknown",
            "event_time_utc": latest["event_time_utc"] if latest else None,
            "source_time_utc": latest["source_time_utc"] if latest else None,
            "age_seconds": age_seconds,
            "sample_count": len(series),
            "sparkline": self._sparkline(series, sparkline_points),
        }
        if with_history:
            view["history"] = [
                {
                    "event_time_utc": item["event_time_utc"],
                    "source_time_utc": item["source_time_utc"],
                    "value": item["value"],
                    "nominal_value": item["nominal_value"],
                    "delivery_class": item["delivery_class"],
                    "validity": item["quality"]["validity"],
                }
                for item in series
            ]
        return view

    def _device_view(
        self,
        device: dict[str, Any],
        now: datetime,
        sparkline_points: int,
        with_history: bool = False,
    ) -> dict[str, Any]:
        tags = [
            self._tag_view(tag, now, sparkline_points, with_history)
            for tag in self._tags_by_device[device["device_id"]]
        ]
        health = "nominal"
        for tag in tags:
            if HEALTH_RANK[tag["health"]] > HEALTH_RANK[health]:
                health = tag["health"]
        ages = [tag["age_seconds"] for tag in tags if tag["age_seconds"] is not None]
        asset = self._assets[device["asset_id"]]
        return {
            "device_id": device["device_id"],
            "display_name": device["display_name"],
            "device_role": device["device_role"],
            "transport_class": device["transport_class"],
            "area_id": device["area_id"],
            "area_name": asset["area_name"],
            "asset_id": device["asset_id"],
            "asset_name": asset["display_name"],
            "health": health,
            "tag_count": len(tags),
            "max_age_seconds": max(ages) if ages else None,
            "deviation_ratio": max(
                (abs(tag["deviation_ratio"] or 0.0) for tag in tags),
                default=0.0,
            ),
            "tags": tags,
            "is_synthetic": True,
            "operational_use": "prohibited",
        }

    def _scenario_view(self) -> dict[str, Any]:
        return {
            "id": self._scenario,
            "description": SCENARIO_DESCRIPTIONS[self._scenario],
            "onset_utc": _stamp(self._scenario_onset),
            "selected_at_utc": (
                _stamp(self._scenario_selected_at)
                if self._scenario_selected_at is not None
                else None
            ),
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def snapshot(self, sparkline_points: int = DEFAULT_SPARKLINE_POINTS) -> dict[str, Any]:
        """Return the current device wall without per-sample history."""
        points = max(2, min(int(sparkline_points), 120))
        with self._lock:
            now = self._advance()
            devices = [
                self._device_view(device, now, points) for device in self._devices
            ]
            events = list(self._events)
            scenario = self._scenario_view()
        health_counts = {"nominal": 0, "degraded": 0, "impaired": 0}
        areas: dict[str, dict[str, Any]] = {}
        for device in devices:
            health_counts[device["health"]] += 1
            area = areas.setdefault(
                device["area_id"],
                {
                    "area_id": device["area_id"],
                    "area_name": device["area_name"],
                    "device_count": 0,
                    "health": "nominal",
                    "max_deviation_ratio": 0.0,
                },
            )
            area["device_count"] += 1
            if HEALTH_RANK[device["health"]] > HEALTH_RANK[area["health"]]:
                area["health"] = device["health"]
            area["max_deviation_ratio"] = _round(
                max(area["max_deviation_ratio"], device["deviation_ratio"])
            )
        recent = sorted(
            (
                item
                for item in events
                if (now - _parse_stamp(item["event_time_utc"])).total_seconds()
                <= self.history_seconds
            ),
            key=lambda item: _parse_stamp(item["event_time_utc"]),
            reverse=True,
        )
        return {
            "generated_at_utc": _stamp(now),
            "tick_seconds": BASE_TICK_SECONDS,
            "cycle_seconds": self.cycle_seconds,
            "history_seconds": self.history_seconds,
            "site": dict(self.site),
            "scenario": scenario,
            "scenarios": [
                {"id": name, "description": SCENARIO_DESCRIPTIONS[name]}
                for name in sorted(SCENARIOS)
            ],
            "summary": {
                "device_count": len(devices),
                "tag_count": sum(device["tag_count"] for device in devices),
                "nominal": health_counts["nominal"],
                "degraded": health_counts["degraded"],
                "impaired": health_counts["impaired"],
                "samples_in_window": sum(
                    tag["sample_count"] for device in devices for tag in device["tags"]
                ),
                "event_count": len(recent),
                "critical_event_count": sum(
                    1 for item in recent if item["severity"] == "critical"
                ),
            },
            "areas": sorted(areas.values(), key=lambda item: item["area_id"]),
            "devices": devices,
            "events": recent[:50],
            "is_synthetic": True,
            "operational_use": "prohibited",
            "private_truth_publication": "excluded",
        }

    def device_detail(self, device_id: str) -> dict[str, Any]:
        """Return one device with the full retained history for each tag."""
        with self._lock:
            device = next(
                (item for item in self._devices if item["device_id"] == device_id),
                None,
            )
            if device is None:
                raise DownstreamDashboardError("live device is unavailable")
            now = self._advance()
            view = self._device_view(device, now, DEFAULT_SPARKLINE_POINTS, True)
            events = sorted(
                (
                    item
                    for item in self._events
                    if item["device_id"] == device_id
                    and (now - _parse_stamp(item["event_time_utc"])).total_seconds()
                    <= self.history_seconds
                ),
                key=lambda item: _parse_stamp(item["event_time_utc"]),
                reverse=True,
            )
            scenario = self._scenario_view()
        return {
            "generated_at_utc": _stamp(now),
            "history_seconds": self.history_seconds,
            "site": dict(self.site),
            "scenario": scenario,
            "device": view,
            "events": events[:50],
            "is_synthetic": True,
            "operational_use": "prohibited",
        }

    def select_scenario(self, scenario: str) -> dict[str, Any]:
        """Switch the live scenario; the onset applies from the next tick."""
        if not isinstance(scenario, str) or scenario not in SCENARIOS:
            raise DownstreamControlError("unsupported live scenario")
        with self._lock:
            now = _align(self._clock())
            self._scenario = scenario
            self._scenario_onset = (
                LIVE_EPOCH
                if scenario == "normal_run"
                else now + timedelta(seconds=BASE_TICK_SECONDS)
            )
            self._scenario_selected_at = now
            self._events.clear()
            return self._scenario_view()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": True,
                "site": dict(self.site),
                "scenario": self._scenario_view(),
                "cycle_seconds": self.cycle_seconds,
                "history_seconds": self.history_seconds,
                "tick_seconds": BASE_TICK_SECONDS,
                "cursor_utc": _stamp(self._cursor) if self._cursor else None,
                "device_count": len(self._devices),
                "is_synthetic": True,
                "operational_use": "prohibited",
            }
