"""Bounded public-run browsing and controls for the midstream simulator."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from ..config import SCENARIOS
from ..simulator import generate_dataset
from .data import MidstreamDashboardError

# Mirrors AdlsSyntheticLogSink.LOG_TYPES. Midstream publishes observable
# evidence logs only: assets, topology, telemetry, and ground truth stay local.
PUBLIC_RECORD_TYPES = frozenset(
    {
        "events",
        "device_logs",
        "detector_outputs",
        "physical_confirmation_events",
    }
)
PRIVATE_RECORD_TYPES = frozenset(
    {"assets", "topology", "telemetry", "ground_truth"}
)
RUN_ID_PREFIX = "midstream-"
MAX_PAGE_SIZE = 200
MAX_MANIFEST_BYTES = 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024

SCENARIO_DESCRIPTIONS = {
    "normal_transfer": "Steady synthetic custody transfer along the fictional route.",
    "pump_degradation": "Synthetic pump efficiency decay after onset.",
    "valve_restriction": "Synthetic valve throttling that restricts line flow.",
    "pipeline_withdrawal": "Synthetic unaccounted withdrawal along the route.",
    "tank_sensor_drift": "Synthetic tank level sensor drift.",
    "communications_failure": "Synthetic missing and store-and-forward telemetry.",
    "power_loss_shutdown": "Synthetic pump coast-down after power loss.",
    "batch_property_change": "Synthetic fluid density step between batches.",
}

# Scenarios whose events describe a normal, expected transfer.
QUIET_SCENARIOS = frozenset({"normal_transfer"})


class MidstreamControlError(RuntimeError):
    """Raised when a midstream simulation cannot be started."""


def _safe_run_id(run_id: str) -> str:
    if (
        not isinstance(run_id, str)
        or not run_id.startswith(RUN_ID_PREFIX)
        or len(run_id) > 96
        or not all(character.isalnum() or character in "-_" for character in run_id)
    ):
        raise MidstreamDashboardError("invalid midstream run identifier")
    return run_id


def _safe_record_type(record_type: str) -> str:
    if record_type not in PUBLIC_RECORD_TYPES:
        raise MidstreamDashboardError("unsupported public record type")
    return record_type


def _public_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    sanitized = copy.deepcopy(manifest)
    sanitized["files"] = [
        entry
        for entry in sanitized.get("files", [])
        if entry.get("record_type") in PUBLIC_RECORD_TYPES
    ]
    counts = sanitized.get("record_counts")
    if isinstance(counts, dict):
        for record_type in PRIVATE_RECORD_TYPES:
            counts.pop(record_type, None)
    else:
        # The published logs manifest carries per-file counts instead of a
        # record_counts map, so derive the public counts from the file list.
        sanitized["record_counts"] = {
            entry["record_type"]: entry.get("record_count", 0)
            for entry in sanitized["files"]
            if entry.get("format", "jsonl") == "jsonl"
        }
    sanitized.pop("logs_manifest", None)
    # Defence in depth. Local-only metadata is written to its own file and is
    # not supposed to reach the manifest at all; if a future change puts it
    # back, it still must not reach a client from here.
    for key in [
        key
        for key in sanitized
        if key.startswith("local_only_") and key != "local_only_record_types"
    ]:
        sanitized.pop(key)
    sanitized.pop("parts_inventory_scope", None)
    normalized = sanitized.get("normalized_config")
    if isinstance(normalized, dict):
        localization = normalized.get("localization")
        if isinstance(localization, dict):
            localization.pop("hidden_release_route_fraction", None)
    policy = sanitized.setdefault("policy", {})
    policy["is_synthetic"] = True
    policy["operational_use"] = "prohibited"
    policy["private_truth_publication"] = "excluded"
    policy["ground_truth_exposed"] = False
    return sanitized


def _manifest_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    normalized = manifest.get("normalized_config", {})
    return {
        "run_id": manifest["run_id"],
        "scenario_id": manifest.get("scenario_id"),
        "scenario": normalized.get("scenario"),
        "site": normalized.get("site", {}),
        "simulation": normalized.get("simulation", {}),
        "record_counts": {
            key: value
            for key, value in manifest.get("record_counts", {}).items()
            if key in PUBLIC_RECORD_TYPES
        },
        "generator": manifest.get("generator", {}),
        "status": "complete",
        "is_synthetic": True,
        "operational_use": "prohibited",
    }


class LocalMidstreamRunStore:
    """Read checksum-verified public artifacts from finalized local runs."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _directories(self) -> list[Path]:
        if (self.root / "manifest.json").is_file():
            return [self.root]
        if not self.root.is_dir():
            return []
        return sorted(
            (
                path
                for path in self.root.iterdir()
                if path.is_dir() and (path / "manifest.json").is_file()
            ),
            key=lambda path: path.name,
            reverse=True,
        )

    @staticmethod
    def _load_manifest(directory: Path) -> dict[str, Any]:
        manifest_path = directory / "manifest.json"
        try:
            if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
                raise MidstreamDashboardError("midstream manifest is too large")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise MidstreamDashboardError(
                "midstream manifest is unavailable"
            ) from error
        if not isinstance(manifest, dict) or not isinstance(
            manifest.get("run_id"), str
        ):
            raise MidstreamDashboardError("midstream manifest is invalid")
        return manifest

    def _find(self, run_id: str) -> tuple[Path, dict[str, Any]]:
        resolved = _safe_run_id(run_id)
        for directory in self._directories():
            manifest = self._load_manifest(directory)
            if manifest["run_id"] == resolved:
                return directory, manifest
        raise MidstreamDashboardError("midstream run is unavailable")

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        runs = [
            _manifest_summary(self._load_manifest(directory))
            for directory in self._directories()
        ]
        runs.sort(
            key=lambda item: item.get("simulation", {}).get("start_utc", ""),
            reverse=True,
        )
        return runs[: max(1, min(limit, 100))]

    def get_manifest(self, run_id: str) -> dict[str, Any]:
        _, manifest = self._find(run_id)
        return _public_manifest(manifest)

    def read_records(
        self,
        run_id: str,
        record_type: str,
        cursor: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        directory, manifest = self._find(run_id)
        resolved_type = _safe_record_type(record_type)
        page_size = max(1, min(limit, MAX_PAGE_SIZE))
        if cursor < 0:
            raise MidstreamDashboardError("record cursor must be non-negative")
        entry = next(
            (
                item
                for item in manifest.get("files", [])
                if item.get("record_type") == resolved_type
                and item.get("format") == "jsonl"
            ),
            None,
        )
        if entry is None:
            raise MidstreamDashboardError("public artifact is unavailable")
        relative = PurePosixPath(entry["path"])
        if relative.is_absolute() or ".." in relative.parts or "\\" in entry["path"]:
            raise MidstreamDashboardError("public artifact path is unsafe")
        path = (directory / Path(*relative.parts)).resolve()
        try:
            path.relative_to(directory.resolve())
        except ValueError as error:
            raise MidstreamDashboardError(
                "public artifact path is unsafe"
            ) from error
        try:
            if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                raise MidstreamDashboardError("public artifact checksum mismatch")
            records: list[dict[str, Any]] = []
            with path.open("rb") as stream:
                stream.seek(cursor)
                while len(records) < page_size:
                    line = stream.readline()
                    if not line:
                        break
                    records.append(json.loads(line))
                next_cursor = stream.tell()
                complete = not stream.read(1)
        except MidstreamDashboardError:
            raise
        except (OSError, json.JSONDecodeError) as error:
            raise MidstreamDashboardError(
                "public artifact could not be read"
            ) from error
        return {
            "run_id": run_id,
            "record_type": resolved_type,
            "records": records,
            "next_cursor": None if complete else next_cursor,
            "count": len(records),
        }


class AdlsMidstreamRunStore:
    """Read finalized public run artifacts from ADLS Gen2 with managed identity."""

    def __init__(
        self,
        account_url: str,
        file_system: str,
        path_prefix: str,
        credential: Any,
        service_client: Any | None = None,
    ) -> None:
        try:
            from azure.core.exceptions import AzureError, ResourceNotFoundError
        except ImportError:
            AzureError = OSError
            ResourceNotFoundError = FileNotFoundError
        self._storage_errors = (AzureError, OSError)
        self._not_found_errors = (ResourceNotFoundError, FileNotFoundError)
        if service_client is None:
            try:
                from azure.storage.filedatalake import DataLakeServiceClient
            except ImportError as error:
                raise MidstreamDashboardError(
                    "ADLS dashboard support requires the Azure dependencies"
                ) from error
            service_client = DataLakeServiceClient(account_url, credential=credential)
        self.file_system = service_client.get_file_system_client(file_system)
        self.path_prefix = path_prefix.strip("/")

    def _run_prefix(self, run_id: str) -> str:
        resolved = _safe_run_id(run_id)
        suffix = f"run_id={resolved}"
        return f"{self.path_prefix}/{suffix}" if self.path_prefix else suffix

    def _download_json(self, path: str) -> dict[str, Any]:
        try:
            client = self.file_system.get_file_client(path)
            properties = client.get_file_properties()
            if properties.size > MAX_MANIFEST_BYTES:
                raise MidstreamDashboardError("midstream manifest is too large")
            payload = client.download_file().readall()
            value = json.loads(payload)
        except MidstreamDashboardError:
            raise
        except self._storage_errors as error:
            raise MidstreamDashboardError("ADLS manifest is unavailable") from error
        if not isinstance(value, dict):
            raise MidstreamDashboardError("ADLS manifest is invalid")
        return value

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        try:
            paths = self.file_system.get_paths(path=self.path_prefix, recursive=True)
            file_paths = {item.name: item for item in paths if not item.is_directory}
            manifest_paths = [
                path
                for path in file_paths
                if path.endswith("/logs-manifest.json")
                and f"{path.rsplit('/', 1)[0]}/_SUCCESS.json" in file_paths
            ]
            manifest_paths.sort(
                key=lambda path: (
                    str(getattr(file_paths[path], "last_modified", "")),
                    path,
                ),
                reverse=True,
            )
        except self._not_found_errors:
            return []
        except self._storage_errors as error:
            raise MidstreamDashboardError(
                "ADLS run inventory is unavailable"
            ) from error
        runs = [
            _manifest_summary(_public_manifest(self._download_json(path)))
            for path in manifest_paths[: max(1, min(limit, 100))]
        ]
        runs.sort(
            key=lambda item: item.get("simulation", {}).get("start_utc", ""),
            reverse=True,
        )
        return runs

    def get_manifest(self, run_id: str) -> dict[str, Any]:
        prefix = self._run_prefix(run_id)
        marker = self._download_json(f"{prefix}/_SUCCESS.json")
        if marker.get("status") != "complete" or marker.get("run_id") != run_id:
            raise MidstreamDashboardError("ADLS run is not finalized")
        manifest = self._download_json(f"{prefix}/logs-manifest.json")
        if manifest.get("run_id") != run_id:
            raise MidstreamDashboardError(
                "ADLS run manifest does not match its path"
            )
        if manifest.get("ground_truth_included") is not False:
            raise MidstreamDashboardError(
                "ADLS run manifest does not exclude private ground truth"
            )
        return _public_manifest(manifest)

    def read_records(
        self,
        run_id: str,
        record_type: str,
        cursor: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        resolved_type = _safe_record_type(record_type)
        page_size = max(1, min(limit, MAX_PAGE_SIZE))
        if cursor < 0:
            raise MidstreamDashboardError("record cursor must be non-negative")
        try:
            client = self.file_system.get_file_client(
                f"{self._run_prefix(run_id)}/{resolved_type}.jsonl"
            )
            size = client.get_file_properties().size
            position = cursor
            next_boundary = cursor
            pending = b""
            records: list[dict[str, Any]] = []
            while position < size and len(records) < page_size:
                length = min(READ_CHUNK_BYTES, size - position)
                chunk = client.download_file(offset=position, length=length).readall()
                if not chunk:
                    break
                position += len(chunk)
                pending += chunk
                while len(records) < page_size:
                    newline = pending.find(b"\n")
                    if newline < 0:
                        break
                    line = pending[:newline]
                    pending = pending[newline + 1 :]
                    next_boundary += newline + 1
                    if line.strip():
                        records.append(json.loads(line))
                if len(records) >= page_size:
                    break
                if position >= size:
                    # The object is fully consumed. Advance the boundary even
                    # when the residual bytes are not a record (for example a
                    # whitespace tail with no trailing newline), otherwise the
                    # cursor would not move and the caller's paging loop would
                    # re-request the same range forever.
                    if pending.strip():
                        records.append(json.loads(pending))
                    next_boundary = size
                    pending = b""
            next_cursor = None if next_boundary >= size else next_boundary
            if next_cursor is not None and next_cursor <= cursor:
                # Defensive stop: never hand back a cursor that cannot make
                # progress on the next call.
                next_cursor = None
        except (json.JSONDecodeError, OSError) as error:
            raise MidstreamDashboardError(
                "ADLS public artifact is invalid"
            ) from error
        except self._storage_errors as error:
            raise MidstreamDashboardError(
                "ADLS public artifact is unavailable"
            ) from error
        return {
            "run_id": run_id,
            "record_type": resolved_type,
            "records": records,
            "next_cursor": next_cursor,
            "count": len(records),
        }


def build_control_config(scenario: str, seed: int, moment: datetime) -> dict[str, Any]:
    """Build a bounded, validated configuration for one on-demand run."""
    aligned = moment.replace(microsecond=0)
    aligned -= timedelta(seconds=aligned.second % 60)
    return {
        "schema_version": "1.0",
        "seed": seed,
        "scenario": scenario,
        "site": {
            "site_id": "fictional-gulf-transfer-001",
            "display_name": "Gulf Transfer Demonstration",
            "time_zone": "America/Chicago",
            "ambient_temperature_c": 31.0,
        },
        "simulation": {
            "start_utc": aligned.isoformat().replace("+00:00", "Z"),
            "end_utc": (aligned + timedelta(hours=2))
            .isoformat()
            .replace("+00:00", "Z"),
            "interval_seconds": 60,
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


class MidstreamSimulationController:
    """Run one bounded simulation at a time and emit instance-rich alerts."""

    def __init__(
        self,
        output_root: str | Path,
        public_sink_factory: Callable[[], Any | None] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.output_root = Path(output_root)
        self.public_sink_factory = public_sink_factory or (lambda: None)
        self.logger = logger or logging.getLogger("iot.midstream")
        self._lock = threading.Lock()
        self._status: dict[str, Any] = {"state": "idle"}

    def status(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._status)

    def start(self, scenario: str, operator: str | None) -> dict[str, Any]:
        if not isinstance(scenario, str) or scenario not in SCENARIOS:
            raise MidstreamControlError("unsupported midstream scenario")
        with self._lock:
            if self._status.get("state") in {"queued", "running"}:
                raise MidstreamControlError(
                    "a midstream simulation is already active"
                )
            job_id = f"job-{uuid.uuid4().hex}"
            self._status = {
                "job_id": job_id,
                "state": "queued",
                "scenario": scenario,
                "operator": operator,
                "submitted_at_utc": datetime.now(timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z"),
            }
        worker = threading.Thread(
            target=self._run,
            args=(job_id, scenario),
            name=f"midstream-{job_id}",
            daemon=True,
        )
        worker.start()
        return self.status()

    def _set_status(self, job_id: str, **values: Any) -> None:
        with self._lock:
            if self._status.get("job_id") == job_id:
                self._status.update(values)

    def _run(self, job_id: str, scenario: str) -> None:
        started = datetime.now(timezone.utc).replace(microsecond=0)
        config = build_control_config(
            scenario,
            int(uuid.UUID(job_id[4:]).int % 2_147_483_647),
            started,
        )
        output = self.output_root / job_id
        self._set_status(
            job_id,
            state="running",
            started_at_utc=started.isoformat().replace("+00:00", "Z"),
        )
        try:
            manifest = generate_dataset(
                config,
                output,
                log_sink=self.public_sink_factory(),
            )
            alert_count = self._emit_alerts(output, manifest, scenario)
            completed = datetime.now(timezone.utc).replace(microsecond=0)
            self._set_status(
                job_id,
                state="succeeded",
                run_id=manifest["run_id"],
                alert_instance_count=alert_count,
                completed_at_utc=completed.isoformat().replace("+00:00", "Z"),
            )
        except Exception:
            self.logger.exception(
                "Midstream simulation failed",
                extra={
                    "event_name": "midstream_simulation_failed",
                    "job_id": job_id,
                },
            )
            self._set_status(
                job_id,
                state="failed",
                error="simulation_failed",
                completed_at_utc=datetime.now(timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z"),
            )

    def _emit_alerts(
        self,
        output: Path,
        manifest: dict[str, Any],
        scenario: str,
    ) -> int:
        if scenario in QUIET_SCENARIOS:
            return 0
        site_id = manifest.get("normalized_config", {}).get("site", {}).get(
            "site_id", "unknown"
        )
        count = 0
        with (output / "events.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                event = json.loads(line)
                if event.get("severity") == "information":
                    continue
                transition = event.get("state_transition") or {}
                dimensions = {
                    "event_name": "midstream_simulation_alert",
                    "run_id": manifest["run_id"],
                    "scenario_id": event["scenario_id"],
                    "scenario": scenario,
                    "site_id": site_id,
                    "asset_id": event.get("asset_id", ""),
                    "event_id": event["event_id"],
                    "event_type": event.get("event_type", ""),
                    "severity": event["severity"],
                    "state_from": transition.get("from", ""),
                    "state_to": transition.get("to", ""),
                    "event_time_utc": event["event_time_utc"],
                    "related_detector_output_id": event.get(
                        "related_detector_output_id"
                    )
                    or "",
                    "is_synthetic": True,
                    "operational_use": "prohibited",
                }
                self.logger.warning(
                    "Synthetic midstream simulation alert instance",
                    extra=dimensions,
                )
                count += 1
        return count


__all__ = [
    "AdlsMidstreamRunStore",
    "LocalMidstreamRunStore",
    "MidstreamControlError",
    "MidstreamDashboardError",
    "MidstreamSimulationController",
    "PUBLIC_RECORD_TYPES",
    "SCENARIO_DESCRIPTIONS",
    "build_control_config",
]
