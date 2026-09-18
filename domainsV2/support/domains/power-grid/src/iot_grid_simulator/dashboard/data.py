"""Read complete simulator micro-batches and assemble dashboard snapshots."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol


class DashboardDataError(RuntimeError):
    """Raised when no trustworthy dashboard snapshot can be assembled."""


@dataclass(frozen=True)
class BatchReference:
    root: str
    sequence: int
    published_at_utc: str


class BatchStore(Protocol):
    def list_complete_batches(self) -> list[BatchReference]:
        """Return candidate completed batches."""

    def read_bytes(self, batch: BatchReference, relative_path: str) -> bytes:
        """Read one file from a batch."""


def _utc_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _json_lines(payload: bytes) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in payload.decode("utf-8").splitlines()
        if line.strip()
    ]


class LocalBatchStore:
    """Read completed batches from a local continuous-run output directory."""

    def __init__(self, output_root: str | Path) -> None:
        self.output_root = Path(output_root)

    def list_complete_batches(self) -> list[BatchReference]:
        batches: list[BatchReference] = []
        for directory in sorted((self.output_root / "batches").glob("batch=*")):
            marker = directory / "_SUCCESS.json"
            if not marker.is_file():
                continue
            match = re.fullmatch(r"batch=(\d+)", directory.name)
            if match is None:
                continue
            batches.append(
                BatchReference(
                    root=str(directory),
                    sequence=int(match.group(1)),
                    published_at_utc=_utc_timestamp(
                        datetime.fromtimestamp(
                            marker.stat().st_mtime,
                            tz=timezone.utc,
                        )
                    ),
                )
            )
        return batches

    def read_bytes(self, batch: BatchReference, relative_path: str) -> bytes:
        path = Path(batch.root) / relative_path
        if not path.is_file():
            raise DashboardDataError(
                f"completed local batch is missing {relative_path}"
            )
        return path.read_bytes()


class AdlsBatchStore:
    """Read completed batches from ADLS Gen2 using an injected credential."""

    def __init__(
        self,
        account_url: str,
        file_system: str,
        path_prefix: str,
        credential: Any,
    ) -> None:
        from azure.storage.filedatalake import DataLakeServiceClient

        service = DataLakeServiceClient(
            account_url=account_url,
            credential=credential,
        )
        self.file_system = service.get_file_system_client(file_system)
        self.path_prefix = path_prefix.strip("/")

    def list_complete_batches(self) -> list[BatchReference]:
        batches: list[BatchReference] = []
        for path in self.file_system.get_paths(
            path=self.path_prefix,
            recursive=True,
        ):
            if path.is_directory or not path.name.endswith("/_SUCCESS.json"):
                continue
            root = path.name.rsplit("/", 1)[0]
            match = re.search(r"/batch=(\d+)/run_id=[^/]+$", root)
            if match is None:
                continue
            modified = path.last_modified or datetime.now(timezone.utc)
            batches.append(
                BatchReference(
                    root=root,
                    sequence=int(match.group(1)),
                    published_at_utc=_utc_timestamp(modified),
                )
            )
        return batches

    def read_bytes(self, batch: BatchReference, relative_path: str) -> bytes:
        return (
            self.file_system.get_file_client(
                f"{batch.root}/{relative_path}"
            )
            .download_file()
            .readall()
        )


AREA_LAYOUT = {
    "fictional-area-bronx": (625, 120, 42, 32),
    "fictional-area-brooklyn": (530, 405, 50, 58),
    "fictional-area-manhattan": (510, 210, 14, 78),
    "fictional-area-queens": (665, 325, 76, 58),
    "fictional-area-staten-island": (315, 510, 62, 52),
    "fictional-region-new-york-city": (515, 340, 28, 24),
}


def _position(asset_id: str, location_ref: str) -> dict[str, float]:
    center_x, center_y, spread_x, spread_y = AREA_LAYOUT.get(
        location_ref,
        (515, 340, 28, 24),
    )
    digest = hashlib.sha256(asset_id.encode("utf-8")).digest()
    jitter_x = (digest[0] / 255.0 - 0.5) * spread_x * 2
    jitter_y = (digest[1] / 255.0 - 0.5) * spread_y * 2
    return {
        "x": round(center_x + jitter_x, 2),
        "y": round(center_y + jitter_y, 2),
    }


def _status_rank(status: str) -> int:
    return {"blue": 0, "yellow": 1, "red": 2}[status]


def _promote(
    current: tuple[str, str],
    candidate: tuple[str, str],
) -> tuple[str, str]:
    return candidate if _status_rank(candidate[0]) > _status_rank(current[0]) else current


def _route_phase(point: dict[str, Any], simulated_at: datetime) -> dict[str, Any]:
    arrival = _parse_utc(point["arrival_utc"])
    departure = _parse_utc(point["departure_utc"])
    if arrival <= simulated_at < departure:
        phase = "active"
    elif simulated_at < arrival:
        phase = "approaching" if arrival - simulated_at <= timedelta(hours=1) else "forecast"
    else:
        phase = "cleared"
    return {
        "phase": phase,
        "minutes_to_arrival": (
            max(0, math.ceil((arrival - simulated_at).total_seconds() / 60))
            if simulated_at < arrival
            else 0
        ),
        "minutes_remaining": (
            max(0, math.ceil((departure - simulated_at).total_seconds() / 60))
            if simulated_at < departure
            else 0
        ),
    }


def _storm_intensity(point: dict[str, Any]) -> int:
    flood = {"none": 5, "watch": 25, "warning": 40}[point["flood_state"]]
    wind = min(30.0, float(point["wind_speed_mps"]) / 25.0 * 30.0)
    rain = min(30.0, float(point["precipitation_mm"]) / 20.0 * 30.0)
    return min(100, round(flood + wind + rain))


@dataclass
class LoadedBatch:
    reference: BatchReference
    manifest: dict[str, Any]
    assets: list[dict[str, Any]]
    telemetry: list[dict[str, Any]]
    events: list[dict[str, Any]]
    alerts: list[dict[str, Any]]
    incidents: list[dict[str, Any]]
    device_logs: list[dict[str, Any]]
    ground_truth: list[dict[str, Any]]
    environment: dict[str, Any] | None


class DashboardSnapshotService:
    """Cache and assemble a verified dashboard snapshot."""

    def __init__(
        self,
        store: BatchStore,
        cache_seconds: float = 15.0,
        history_limit: int = 12,
    ) -> None:
        self.store = store
        self.cache_seconds = cache_seconds
        self.history_limit = history_limit
        self._lock = threading.Lock()
        self._cached_at = 0.0
        self._last_good: dict[str, Any] | None = None

    def get_snapshot(self, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if (
            not force
            and self._last_good is not None
            and now - self._cached_at < self.cache_seconds
        ):
            return copy.deepcopy(self._last_good)
        with self._lock:
            now = time.monotonic()
            if (
                not force
                and self._last_good is not None
                and now - self._cached_at < self.cache_seconds
            ):
                return copy.deepcopy(self._last_good)
            try:
                snapshot = self._build_snapshot()
            except Exception as exc:
                if self._last_good is None:
                    if isinstance(exc, DashboardDataError):
                        raise
                    raise DashboardDataError(
                        "unable to load dashboard data"
                    ) from exc
                stale = copy.deepcopy(self._last_good)
                stale["feed"]["state"] = "stale"
                stale["feed"]["message"] = "Serving last known-good snapshot"
                stale["feed"]["error"] = type(exc).__name__
                return stale
            self._last_good = snapshot
            self._cached_at = time.monotonic()
            return copy.deepcopy(snapshot)

    def has_snapshot(self) -> bool:
        return self._last_good is not None

    def _load_batch(self, reference: BatchReference) -> LoadedBatch:
        marker = json.loads(
            self.store.read_bytes(reference, "_SUCCESS.json")
        )
        manifest_payload = self.store.read_bytes(reference, "manifest.json")
        manifest_sha = hashlib.sha256(manifest_payload).hexdigest()
        if marker.get("status") != "complete":
            raise DashboardDataError("batch completion marker is not complete")
        if marker.get("manifest_sha256") != manifest_sha:
            raise DashboardDataError("batch manifest checksum mismatch")
        manifest = json.loads(manifest_payload)
        if marker.get("run_id") != manifest.get("run_id"):
            raise DashboardDataError("batch completion run ID mismatch")
        files = {item["path"]: item for item in manifest["files"]}

        def verified(path: str, required: bool = True) -> bytes | None:
            entry = files.get(path)
            if entry is None:
                if required:
                    raise DashboardDataError(
                        f"manifest does not contain {path}"
                    )
                return None
            payload = self.store.read_bytes(reference, path)
            if hashlib.sha256(payload).hexdigest() != entry["sha256"]:
                raise DashboardDataError(f"artifact checksum mismatch: {path}")
            return payload

        assets_payload = verified("assets.jsonl")
        telemetry_payload = verified("telemetry.jsonl")
        events_payload = verified("events.jsonl")
        alerts_payload = verified("alerts.jsonl")
        incidents_payload = verified("incidents.jsonl", required=False)
        device_logs_payload = verified("device_logs.jsonl", required=False)
        ground_truth_payload = verified("ground_truth.jsonl", required=False)
        environment_payload = verified("environment.json", required=False)
        assert assets_payload is not None
        assert telemetry_payload is not None
        assert events_payload is not None
        assert alerts_payload is not None
        return LoadedBatch(
            reference=reference,
            manifest=manifest,
            assets=_json_lines(assets_payload),
            telemetry=_json_lines(telemetry_payload),
            events=_json_lines(events_payload),
            alerts=_json_lines(alerts_payload),
            incidents=(
                _json_lines(incidents_payload)
                if incidents_payload is not None
                else []
            ),
            device_logs=(
                _json_lines(device_logs_payload)
                if device_logs_payload is not None
                else []
            ),
            ground_truth=(
                _json_lines(ground_truth_payload)
                if ground_truth_payload is not None
                else []
            ),
            environment=(
                json.loads(environment_payload)
                if environment_payload is not None
                else None
            ),
        )

    def _build_snapshot(self) -> dict[str, Any]:
        references = sorted(
            self.store.list_complete_batches(),
            key=lambda item: (item.published_at_utc, item.sequence),
            reverse=True,
        )
        if not references:
            raise DashboardDataError("no completed simulator batches found")
        loaded: list[LoadedBatch] = []
        failures: list[str] = []
        for reference in references:
            try:
                loaded.append(self._load_batch(reference))
            except Exception as exc:
                failures.append(f"{reference.root}: {type(exc).__name__}")
            if len(loaded) >= self.history_limit:
                break
        if not loaded:
            raise DashboardDataError("no valid completed simulator batch found")
        loaded.sort(
            key=lambda item: (
                item.manifest["normalized_config"]["simulation"]["start_utc"],
                item.reference.sequence,
            )
        )
        latest = loaded[-1]
        return self._assemble(latest, loaded, failures)

    def _assemble(
        self,
        latest: LoadedBatch,
        history: list[LoadedBatch],
        failures: list[str],
    ) -> dict[str, Any]:
        assets_by_id = {
            item["asset_id"]: item for item in latest.assets
        }
        latest_telemetry: dict[tuple[str, str], dict[str, Any]] = {}
        for record in latest.telemetry:
            latest_telemetry[(record["asset_id"], record["measurement"])] = record
        simulated_time = max(
            record["event_time_utc"] for record in latest.telemetry
        )
        simulated_at = _parse_utc(simulated_time)

        active_alerts: dict[str, list[dict[str, Any]]] = {}
        for alert in latest.alerts:
            if (
                _parse_utc(alert["onset_utc"])
                <= simulated_at
                < _parse_utc(alert["expires_utc"])
            ):
                active_alerts.setdefault(alert["area_ref"], []).append(alert)
        events_by_asset: dict[str, list[dict[str, Any]]] = {}
        for event in latest.events:
            if event["asset_id"] is not None:
                events_by_asset.setdefault(event["asset_id"], []).append(event)

        route = (
            latest.environment.get("storm", {}).get("route", [])
            if latest.environment is not None
            else []
        )
        route_by_location: dict[str, dict[str, Any]] = {}
        for point in route:
            location_ref = f"fictional-area-{'-'.join(point['area'].casefold().split())}"
            route_by_location[location_ref] = {
                **point,
                **_route_phase(point, simulated_at),
                "intensity_score": _storm_intensity(point),
                "area_ref": location_ref,
            }
        incident_candidates = [
            incident
            for batch in history
            for incident in batch.incidents
        ]
        incident_record = (
            max(
                incident_candidates,
                key=lambda item: (
                    item["updated_at_utc"],
                    item["incident_record_id"],
                ),
            )
            if incident_candidates
            else None
        )
        active_incident = (
            incident_record
            if incident_record is not None
            and incident_record["status"] != "resolved"
            else None
        )
        incident_affected = (
            set(active_incident["affected_asset_ids"])
            if active_incident is not None
            else set()
        )

        devices: list[dict[str, Any]] = []
        for asset in latest.assets:
            asset_id = asset["asset_id"]
            status = ("blue", "Normal")
            risk_score = 0
            observed_attention = False
            observed_watch = False
            risk_reasons: list[dict[str, str]] = []
            measurements: dict[str, dict[str, Any]] = {}
            for (record_asset_id, measurement), record in latest_telemetry.items():
                if record_asset_id != asset_id:
                    continue
                measurements[measurement] = {
                    "value": record["value"],
                    "unit": record["unit"],
                    "quality": record["quality"]["validity"],
                    "reasons": record["quality"]["reasons"],
                    "event_time_utc": record.get("event_time_utc"),
                    "source_time_utc": record.get("source_time_utc"),
                    "phase": record.get("phase"),
                    "aggregation": record.get("aggregation"),
                    "interval_seconds": record.get("interval_seconds"),
                    "sequence": record.get("sequence"),
                    "related_event_id": record.get("related_event_id"),
                    "fault_ids": sorted(
                        reason
                        for reason in record["quality"]["reasons"]
                        if reason.startswith("synthetic-")
                    ),
                }
                validity = record["quality"]["validity"]
                if "deenergized" in record["quality"]["reasons"]:
                    observed_attention = True
                    risk_score = max(risk_score, 100)
                    risk_reasons.append(
                        {
                            "code": "deenergized",
                            "label": "Device is de-energized",
                            "detail": "Simulator telemetry reports an electrical zero state",
                            "source": "telemetry",
                        }
                    )
                elif validity == "missing":
                    observed_attention = True
                    risk_score = max(risk_score, 80)
                    risk_reasons.append(
                        {
                            "code": "telemetry_missing",
                            "label": "Telemetry is missing",
                            "detail": "The latest measurement was not delivered",
                            "source": "telemetry",
                        }
                    )
                elif validity in {"stale", "late"}:
                    observed_watch = True
                    risk_score = max(risk_score, 35 if validity == "stale" else 25)
                    risk_reasons.append(
                        {
                            "code": f"telemetry_{validity}",
                            "label": f"Telemetry is {validity}",
                            "detail": "The latest measurement did not arrive on schedule",
                            "source": "telemetry",
                        }
                    )
            for event in events_by_asset.get(asset_id, []):
                if event["severity"] == "critical":
                    observed_attention = True
                    risk_score = max(risk_score, 85)
                    risk_reasons.append(
                        {
                            "code": "critical_event",
                            "label": "Critical simulator event",
                            "detail": event["event_type"].replace("_", " "),
                            "source": "event",
                        }
                    )
                elif event["severity"] == "warning":
                    observed_watch = True
                    risk_score = max(risk_score, 40)
                    risk_reasons.append(
                        {
                            "code": "warning_event",
                            "label": "Warning simulator event",
                            "detail": event["event_type"].replace("_", " "),
                            "source": "event",
                        }
                    )

            voltage = measurements.get("voltage", {}).get("value")
            nominal_voltage = float(asset["nominal_voltage_v"])
            voltage_percent: float | None = None
            if voltage is not None and nominal_voltage > 0:
                voltage_percent = float(voltage) / nominal_voltage * 100.0
                if voltage_percent < 90.0:
                    observed_attention = True
                    risk_score = max(risk_score, 85)
                    risk_reasons.append(
                        {
                            "code": "voltage_critical",
                            "label": "Severe voltage deviation",
                            "detail": f"{voltage_percent:.1f}% of nominal voltage",
                            "source": "telemetry",
                        }
                    )
                elif voltage_percent < 95.0:
                    observed_watch = True
                    risk_score = max(risk_score, 45)
                    risk_reasons.append(
                        {
                            "code": "voltage_low",
                            "label": "Low voltage condition",
                            "detail": f"{voltage_percent:.1f}% of nominal voltage",
                            "source": "telemetry",
                        }
                    )

            loading = measurements.get("loading", {}).get("value")
            if loading is not None:
                if float(loading) >= 100.0:
                    observed_attention = True
                    risk_score = max(risk_score, 75)
                    risk_reasons.append(
                        {
                            "code": "transformer_overload",
                            "label": "Transformer loading needs attention",
                            "detail": f"{float(loading):.1f}% of nameplate rating",
                            "source": "telemetry",
                        }
                    )
                elif float(loading) >= 80.0:
                    observed_watch = True
                    risk_score = max(risk_score, 40)
                    risk_reasons.append(
                        {
                            "code": "transformer_loading",
                            "label": "Elevated transformer loading",
                            "detail": f"{float(loading):.1f}% of nameplate rating",
                            "source": "telemetry",
                        }
                    )

            condition = measurements.get("condition_index", {}).get("value")
            if condition is not None:
                if float(condition) >= 0.8:
                    observed_attention = True
                    risk_score = max(risk_score, 75)
                    risk_reasons.append(
                        {
                            "code": "condition_critical",
                            "label": "Condition index needs attention",
                            "detail": f"Synthetic index {float(condition):.2f}",
                            "source": "telemetry",
                        }
                    )
                elif float(condition) >= 0.55:
                    observed_watch = True
                    risk_score = max(risk_score, 40)
                    risk_reasons.append(
                        {
                            "code": "condition_watch",
                            "label": "Condition index is elevated",
                            "detail": f"Synthetic index {float(condition):.2f}",
                            "source": "telemetry",
                        }
                    )

            exposure = route_by_location.get(asset["location_ref"])
            exposure_risk = False
            if exposure is not None and exposure["phase"] in {"active", "approaching"}:
                exposure_risk = True
                risk_score = max(risk_score, exposure["intensity_score"])
                timing = (
                    "Active now"
                    if exposure["phase"] == "active"
                    else f"Arrives in {exposure['minutes_to_arrival']} min"
                )
                risk_reasons.append(
                    {
                        "code": f"storm_{exposure['phase']}",
                        "label": (
                            f"{timing}: synthetic {exposure['flood_state']} exposure"
                        ),
                        "detail": (
                            f"{exposure['wind_speed_mps']:.1f} m/s wind · "
                            f"{exposure['precipitation_mm']:.1f} mm rain"
                        ),
                        "source": "storm",
                    }
                )

            if asset_id in incident_affected:
                incident_outage = active_incident["service_state"] == "interrupted"
                if incident_outage:
                    observed_attention = True
                    risk_score = max(risk_score, 95)
                else:
                    observed_watch = True
                    risk_score = max(risk_score, 55)
                risk_reasons.append(
                    {
                        "code": "active_incident",
                        "label": (
                            f"Incident {active_incident['status']}: "
                            f"{active_incident['title']}"
                        ),
                        "detail": (
                            f"{active_incident['area_name']} · "
                            f"{active_incident['service_state'].replace('_', ' ')}"
                        ),
                        "source": "incident",
                    }
                )

            if observed_attention:
                risk_level = "needs_attention"
                status = ("red", "Needs attention")
            elif observed_watch or exposure_risk:
                risk_level = "at_risk"
                status = ("yellow", "At risk")
            else:
                risk_level = "normal"
                status = ("blue", "Normal")

            devices.append(
                {
                    "asset_id": asset_id,
                    "asset_type": asset["asset_type"],
                    "generic_model": asset["generic_model"],
                    "location_ref": asset["location_ref"],
                    "status": status[0],
                    "status_label": status[1],
                    "position": _position(asset_id, asset["location_ref"]),
                    "measurements": measurements,
                    "ratings": asset["ratings"],
                    "phases": asset["phases"],
                    "nominal_voltage_v": asset["nominal_voltage_v"],
                    "risk": {
                        "level": risk_level,
                        "label": status[1],
                        "score": min(100, risk_score),
                        "reasons": risk_reasons,
                        "exposure": exposure,
                        "observed": {
                            "voltage_percent_of_nominal": (
                                round(voltage_percent, 1)
                                if voltage_percent is not None
                                else None
                            ),
                            "loading_percent": loading,
                            "condition_index": condition,
                        },
                    },
                }
            )
        devices.sort(key=lambda item: item["asset_id"])

        weather_history: list[dict[str, Any]] = []
        load_history: list[dict[str, Any]] = []
        for batch in history:
            batch_assets = {
                item["asset_id"]: item for item in batch.assets
            }
            weather_id = next(
                item["asset_id"]
                for item in batch.assets
                if item["asset_type"] == "weather_station"
            )
            grid_id = next(
                item["asset_id"]
                for item in batch.assets
                if item["asset_type"] == "grid_source"
            )
            weather: dict[str, Any] = {
                "event_time_utc": batch.manifest["normalized_config"][
                    "simulation"
                ]["start_utc"]
            }
            grid_load: dict[str, Any] = {
                "event_time_utc": weather["event_time_utc"]
            }
            for record in batch.telemetry:
                if record["asset_id"] == weather_id:
                    weather[record["measurement"]] = record["value"]
                elif (
                    record["asset_id"] == grid_id
                    and record["measurement"] == "active_power"
                ):
                    grid_load["active_power_kw"] = record["value"]
            if weather_id in batch_assets:
                weather_history.append(weather)
            if "active_power_kw" in grid_load:
                load_history.append(grid_load)

        alert_keys: set[tuple[Any, ...]] = set()
        alerts: list[dict[str, Any]] = []
        for batch in reversed(history):
            for alert in batch.alerts:
                key = (
                    alert["storm_id"],
                    alert["storm_revision"],
                    alert["area_ref"],
                    alert["event"],
                    alert["onset_utc"],
                )
                if key not in alert_keys:
                    alert_keys.add(key)
                    alerts.append(alert)
        alerts.sort(key=lambda item: item["onset_utc"])

        events = sorted(
            latest.events,
            key=lambda item: (item["event_time_utc"], item["event_id"]),
            reverse=True,
        )[:20]
        device_log_limit = 250
        sorted_device_logs = sorted(
            latest.device_logs,
            key=lambda item: (
                item.get("event_time_utc", ""),
                item.get("asset_id", ""),
                item.get("log_id", ""),
            ),
            reverse=True,
        )
        device_logs = sorted_device_logs[:device_log_limit]
        ground_truth = sorted(
            latest.ground_truth,
            key=lambda item: item.get("ground_truth_id", ""),
        )
        sensor_faults = sorted(
            (
                latest.environment.get("sensor_faults", [])
                if latest.environment is not None
                else []
            ),
            key=lambda item: item.get("fault_id", ""),
        )
        incident_history: list[dict[str, Any]] = []
        incident_keys: set[tuple[str, str, str]] = set()
        for batch in history:
            for incident in batch.incidents:
                key = (
                    incident["incident_id"],
                    incident["status"],
                    incident["updated_at_utc"],
                )
                if key not in incident_keys:
                    incident_keys.add(key)
                    incident_history.append(incident)
        incident_history.sort(
            key=lambda item: (
                item["updated_at_utc"],
                item["incident_record_id"],
            ),
            reverse=True,
        )
        counts = {"blue": 0, "yellow": 0, "red": 0}
        for device in devices:
            counts[device["status"]] += 1

        area_impacts: list[dict[str, Any]] = []
        for exposure in sorted(
            route_by_location.values(),
            key=lambda item: item["sequence"],
        ):
            area_devices = [
                device
                for device in devices
                if device["location_ref"] == exposure["area_ref"]
            ]
            level_counts = {
                level: sum(
                    device["risk"]["level"] == level for device in area_devices
                )
                for level in ("normal", "at_risk", "needs_attention")
            }
            area_impacts.append(
                {
                    **exposure,
                    "device_count": len(area_devices),
                    "at_risk_count": level_counts["at_risk"],
                    "needs_attention_count": level_counts["needs_attention"],
                    "normal_count": level_counts["normal"],
                    "affected_device_count": (
                        level_counts["at_risk"]
                        + level_counts["needs_attention"]
                    ),
                }
            )

        storm_overview: dict[str, Any] | None = None
        if route:
            route_start = min(_parse_utc(point["arrival_utc"]) for point in route)
            route_end = max(_parse_utc(point["departure_utc"]) for point in route)
            duration = max(1.0, (route_end - route_start).total_seconds())
            progress = (
                (simulated_at - route_start).total_seconds() / duration * 100.0
            )
            active_areas = [
                item["area"] for item in area_impacts if item["phase"] == "active"
            ]
            next_area = next(
                (
                    item["area"]
                    for item in area_impacts
                    if item["phase"] in {"approaching", "forecast"}
                ),
                None,
            )
            storm_overview = {
                "storm_id": latest.environment["storm"]["storm_id"],
                "revision": latest.environment["storm"]["revision"],
                "status": latest.environment["storm"]["status"],
                "is_synthetic": True,
                "public_safety": False,
                "phase": (
                    "active"
                    if active_areas
                    else "approaching"
                    if simulated_at < route_start
                    else "cleared"
                ),
                "progress_percent": round(min(100.0, max(0.0, progress)), 1),
                "active_areas": active_areas,
                "next_area": next_area,
                "route_start_utc": _utc_timestamp(route_start),
                "route_end_utc": _utc_timestamp(route_end),
                "peak_wind_speed_mps": max(
                    float(point["wind_speed_mps"]) for point in route
                ),
                "peak_precipitation_mm": max(
                    float(point["precipitation_mm"]) for point in route
                ),
                "area_impacts": area_impacts,
                "affected_device_count": counts["yellow"] + counts["red"],
                "at_risk_count": counts["yellow"],
                "needs_attention_count": counts["red"],
            }

        published_at = _parse_utc(latest.reference.published_at_utc)
        age_seconds = max(
            0,
            int((datetime.now(timezone.utc) - published_at).total_seconds()),
        )
        if age_seconds <= 180:
            feed_state = "live"
            feed_message = "Receiving completed simulator batches"
        elif age_seconds <= 900:
            feed_state = "delayed"
            feed_message = "Feed has not published recently"
        else:
            feed_state = "paused"
            feed_message = "Simulator feed appears paused"
        runtime_match = re.search(r"instance_id=([^/]+)", latest.reference.root)
        return {
            "schema_version": "1.0",
            "generated_at_utc": _utc_timestamp(datetime.now(timezone.utc)),
            "source": {
                "runtime_id": (
                    runtime_match.group(1) if runtime_match else "local"
                ),
                "batch_sequence": latest.reference.sequence,
                "run_id": latest.manifest["run_id"],
                "simulated_time_utc": simulated_time,
                "published_at_utc": latest.reference.published_at_utc,
            },
            "feed": {
                "state": feed_state,
                "message": feed_message,
                "age_seconds": age_seconds,
                "skipped_batches": failures,
            },
            "summary": {
                "devices": len(devices),
                "normal": counts["blue"],
                "watch": counts["yellow"],
                "critical": counts["red"],
                "active_alerts": sum(len(value) for value in active_alerts.values()),
                "active_incidents": 1 if active_incident is not None else 0,
                "at_risk": counts["yellow"],
                "needs_attention": counts["red"],
                "device_logs": len(sorted_device_logs),
                "ground_truth_records": len(ground_truth),
                "sensor_faults": len(sensor_faults),
            },
            "devices": devices,
            "weather_history": weather_history,
            "load_history": load_history,
            "alerts": alerts,
            "events": events,
            "device_logs": device_logs,
            "device_logs_truncated": max(0, len(sorted_device_logs) - len(device_logs)),
            "ground_truth": ground_truth,
            "sensor_faults": sensor_faults,
            "incidents": incident_history,
            "incident": incident_record,
            "environment": latest.environment,
            "storm": storm_overview,
            "map": {
                "coordinate_system": "fictional-screen-space",
                "width": 1000,
                "height": 650,
            },
            "disclaimer": (
                "Synthetic exercise data only. Not an operational utility or "
                "public-safety system."
            ),
        }
