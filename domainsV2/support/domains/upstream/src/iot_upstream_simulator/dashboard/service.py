"""Bounded public-run browsing and controls for the upstream simulator.

The public surface is the sparse monitoring/workflow evidence layer only. The
structural catalog, the continuous telemetry layer, and the private
``ground_truth`` family are all unreachable through every store here, exactly
as they are unreachable through the ADLS monitoring sink.
"""

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

from ..catalog import (
    CATALOG_RECORD_TYPES,
    LOCAL_ONLY_RECORD_TYPES,
    PRIVATE_RECORD_TYPES as CORE_PRIVATE_RECORD_TYPES,
    TELEMETRY_RECORD_TYPES,
    WORKFLOW_RECORD_TYPES,
    sanitize_public_manifest,
)
from ..config import SCENARIOS
from ..simulator import generate_dataset
from .data import UpstreamDashboardError

# Mirrors AdlsMonitoringLogSink.LOG_TYPES exactly. Upstream publishes sparse
# observable evidence only: the structural catalog, continuous telemetry and
# private ground truth stay local and are never reachable through the portal.
PUBLIC_RECORD_TYPES = frozenset(WORKFLOW_RECORD_TYPES)
PRIVATE_RECORD_TYPES = frozenset(
    (
        *CATALOG_RECORD_TYPES,
        *TELEMETRY_RECORD_TYPES,
        *LOCAL_ONLY_RECORD_TYPES,
        *CORE_PRIVATE_RECORD_TYPES,
    )
)
RUN_ID_PREFIX = "upstream-"
MAX_PAGE_SIZE = 200
MAX_MANIFEST_BYTES = 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024

#: Short, non-operational descriptions of every approved Release 1 scenario.
SCENARIO_DESCRIPTIONS = {
    "normal_production": (
        "Steady synthetic production across the fictional pads and field facility."
    ),
    "deliverability_decline": (
        "Synthetic gradual decline in fictional well deliverability after onset."
    ),
    "liquid_loading": (
        "Synthetic liquid accumulation signature on a fictional gas well."
    ),
    "slugging": (
        "Synthetic intermittent slug pattern in the fictional flowline network."
    ),
    "flowline_restriction": (
        "Synthetic restriction signature between a fictional pad and the facility."
    ),
    "artificial_lift_degradation": (
        "Synthetic gradual weakening of fictional artificial lift performance."
    ),
    "esp_trip_restart": (
        "Synthetic electric submersible pump trip and restart on a fictional well."
    ),
    "separator_upset": (
        "Synthetic upset in the fictional primary separation train."
    ),
    "heater_treater_weakness": (
        "Synthetic thermal weakness signature on the fictional heater treater."
    ),
    "compressor_degradation": (
        "Synthetic degradation of fictional field compression performance."
    ),
    "produced_water_constraint": (
        "Synthetic constraint in the fictional produced water handling path."
    ),
    "tank_transfer_imbalance": (
        "Synthetic imbalance between fictional tank inventory and transfer context."
    ),
    "corrosion_erosion_context": (
        "Synthetic corrosion and erosion context evidence, never an integrity verdict."
    ),
    "gas_release_observation": (
        "Synthetic qualitative gas detection observation with no quantified release."
    ),
    "utility_power_loss": (
        "Synthetic loss of fictional field power and the resulting process-off states."
    ),
    "instrument_drift_stuck": (
        "Synthetic instrument drift and frozen-value behaviour in the measurement layer."
    ),
    "communications_degradation": (
        "Synthetic late, stale, duplicate and missing delivery of otherwise normal data."
    ),
    "maintenance_inspection_workflow": (
        "Synthetic maintenance and inspection context activity around fictional assets."
    ),
    "flare_vent_environment_workflow": (
        "Synthetic flare, vent and environmental observation workflow evidence."
    ),
}

#: Scenarios whose evidence describes an ordinary, expected fictional day.
QUIET_SCENARIOS = frozenset({"normal_production"})


class UpstreamControlError(RuntimeError):
    """Raised when an upstream simulation cannot be started."""


def _safe_run_id(run_id: str) -> str:
    if (
        not isinstance(run_id, str)
        or not run_id.startswith(RUN_ID_PREFIX)
        or len(run_id) > 96
        or not all(character.isalnum() or character in "-_" for character in run_id)
    ):
        raise UpstreamDashboardError("invalid upstream run identifier")
    return run_id


def _safe_record_type(record_type: str) -> str:
    if record_type in PRIVATE_RECORD_TYPES or record_type not in PUBLIC_RECORD_TYPES:
        raise UpstreamDashboardError("unsupported public record type")
    return record_type


def _public_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Reduce a run manifest to what may be served publicly.

    Delegates to the single shared allowlist sanitizer rather than maintaining a
    second deny-list here. A deny-list is how the local-only families leaked
    through ``policy`` in the first place.
    """
    sanitized = sanitize_public_manifest(manifest, PUBLIC_RECORD_TYPES)
    sanitized.pop("logs_manifest", None)
    policy = sanitized["policy"]
    policy["ground_truth_exposed"] = False
    policy["telemetry_excluded"] = True
    policy["structural_catalog_excluded"] = True
    return sanitized


def _manifest_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    normalized = manifest.get("normalized_config", {})
    site = normalized.get("site") or {
        "site_id": manifest.get("site_id"),
        "display_name": manifest.get("site_id"),
    }
    return {
        "run_id": manifest["run_id"],
        "scenario_id": manifest.get("scenario_id"),
        "scenario": normalized.get("scenario"),
        "site": site,
        "facility": normalized.get("facility")
        or {"facility_id": manifest.get("facility_id")},
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


class LocalUpstreamRunStore:
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
                raise UpstreamDashboardError("upstream manifest is too large")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except UpstreamDashboardError:
            raise
        except (OSError, json.JSONDecodeError) as error:
            raise UpstreamDashboardError(
                "upstream manifest is unavailable"
            ) from error
        if not isinstance(manifest, dict) or not isinstance(
            manifest.get("run_id"), str
        ):
            raise UpstreamDashboardError("upstream manifest is invalid")
        return manifest

    def _find(self, run_id: str) -> tuple[Path, dict[str, Any]]:
        resolved = _safe_run_id(run_id)
        for directory in self._directories():
            manifest = self._load_manifest(directory)
            if manifest["run_id"] == resolved:
                return directory, manifest
        raise UpstreamDashboardError("upstream run is unavailable")

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
            raise UpstreamDashboardError("record cursor must be non-negative")
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
            raise UpstreamDashboardError("public artifact is unavailable")
        relative = PurePosixPath(entry["path"])
        if relative.is_absolute() or ".." in relative.parts or "\\" in entry["path"]:
            raise UpstreamDashboardError("public artifact path is unsafe")
        path = (directory / Path(*relative.parts)).resolve()
        try:
            path.relative_to(directory.resolve())
        except ValueError as error:
            raise UpstreamDashboardError(
                "public artifact path is unsafe"
            ) from error
        try:
            if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                raise UpstreamDashboardError("public artifact checksum mismatch")
            records: list[dict[str, Any]] = []
            with path.open("rb") as stream:
                stream.seek(cursor)
                while len(records) < page_size:
                    line = stream.readline()
                    if not line:
                        break
                    if line.strip():
                        records.append(json.loads(line))
                next_cursor = stream.tell()
                complete = not stream.read(1)
        except UpstreamDashboardError:
            raise
        except (OSError, json.JSONDecodeError) as error:
            raise UpstreamDashboardError(
                "public artifact could not be read"
            ) from error
        if not complete and next_cursor <= cursor:
            # Defensive stop: never hand back a cursor that cannot progress.
            complete = True
        return {
            "run_id": run_id,
            "record_type": resolved_type,
            "records": records,
            "next_cursor": None if complete else next_cursor,
            "count": len(records),
        }


class AdlsUpstreamRunStore:
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
                raise UpstreamDashboardError(
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
                raise UpstreamDashboardError("upstream manifest is too large")
            payload = client.download_file().readall()
            value = json.loads(payload)
        except UpstreamDashboardError:
            raise
        except self._storage_errors as error:
            raise UpstreamDashboardError("ADLS manifest is unavailable") from error
        except json.JSONDecodeError as error:
            raise UpstreamDashboardError("ADLS manifest is invalid") from error
        if not isinstance(value, dict):
            raise UpstreamDashboardError("ADLS manifest is invalid")
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
            raise UpstreamDashboardError(
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
            raise UpstreamDashboardError("ADLS run is not finalized")
        manifest = self._download_json(f"{prefix}/logs-manifest.json")
        if manifest.get("run_id") != run_id:
            raise UpstreamDashboardError(
                "ADLS run manifest does not match its path"
            )
        if manifest.get("private_truth_included") is not False:
            raise UpstreamDashboardError(
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
            raise UpstreamDashboardError("record cursor must be non-negative")
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
            raise UpstreamDashboardError("ADLS public artifact is invalid") from error
        except self._storage_errors as error:
            raise UpstreamDashboardError(
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
            "site_id": "fictional-onshore-upstream-field-001",
            "display_name": "Fictional Onshore Upstream Field",
            "time_zone": "America/Chicago",
        },
        "facility": {
            "facility_id": "fictional-central-production-001",
            "display_name": "Fictional Central Production Facility",
        },
        "simulation": {
            "start_utc": aligned.isoformat().replace("+00:00", "Z"),
            "end_utc": (aligned + timedelta(minutes=30))
            .isoformat()
            .replace("+00:00", "Z"),
            "interval_seconds": 60,
        },
        "fleet": {
            "oil_well_pads": 1,
            "gas_well_pads": 1,
            "multiwell_pads": 1,
            "wells_per_multiwell_pad": 3,
        },
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


class UpstreamSimulationController:
    """Run one bounded simulation at a time and emit instance-rich alerts."""

    def __init__(
        self,
        output_root: str | Path,
        public_sink_factory: Callable[[], Any | None] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.output_root = Path(output_root)
        self.public_sink_factory = public_sink_factory or (lambda: None)
        self.logger = logger or logging.getLogger("iot.upstream")
        self._lock = threading.Lock()
        self._status: dict[str, Any] = {"state": "idle"}

    def status(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._status)

    def start(self, scenario: str, operator: str | None) -> dict[str, Any]:
        if not isinstance(scenario, str) or scenario not in SCENARIOS:
            raise UpstreamControlError("unsupported upstream scenario")
        with self._lock:
            if self._status.get("state") in {"queued", "running"}:
                raise UpstreamControlError(
                    "an upstream simulation is already active"
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
            name=f"upstream-{job_id}",
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
                "Upstream simulation failed",
                extra={
                    "event_name": "upstream_simulation_failed",
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
        normalized = manifest.get("normalized_config", {})
        site_id = normalized.get("site", {}).get("site_id", "unknown")
        count = 0
        with (output / "events.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                event = json.loads(line)
                if event.get("severity") == "info":
                    continue
                transition = event.get("state_transition") or {}
                dimensions = {
                    "event_name": "upstream_simulation_alert",
                    "run_id": manifest["run_id"],
                    "scenario_id": event["scenario_id"],
                    "scenario": scenario,
                    "site_id": site_id,
                    "asset_id": event.get("asset_id") or "",
                    "area_id": event.get("area_id", ""),
                    "well_id": event.get("well_id") or "",
                    "event_id": event["event_id"],
                    "event_type": event.get("event_type", ""),
                    "event_class": event.get("event_class", ""),
                    "severity": event["severity"],
                    "state_from": transition.get("from", ""),
                    "state_to": transition.get("to", ""),
                    "event_time_utc": event["event_time_utc"],
                    "quality_class": event.get("quality_class", ""),
                    "interpretation": event.get("interpretation", ""),
                    "correlation_id": event.get("correlation_id") or "",
                    "is_synthetic": True,
                    "operational_use": "prohibited",
                }
                self.logger.warning(
                    "Synthetic upstream simulation alert instance",
                    extra=dimensions,
                )
                count += 1
        return count


__all__ = [
    "AdlsUpstreamRunStore",
    "LocalUpstreamRunStore",
    "PUBLIC_RECORD_TYPES",
    "PRIVATE_RECORD_TYPES",
    "RUN_ID_PREFIX",
    "SCENARIO_DESCRIPTIONS",
    "UpstreamControlError",
    "UpstreamDashboardError",
    "UpstreamSimulationController",
    "build_control_config",
]
