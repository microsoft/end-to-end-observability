"""Bounded public-run browsing and controls for the downstream simulator."""

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
from ..simulator import PUBLIC_RECORD_TYPES as SIMULATOR_PUBLIC_RECORD_TYPES
from ..simulator import generate_dataset

PUBLIC_RECORD_TYPES = frozenset(SIMULATOR_PUBLIC_RECORD_TYPES)
RUN_ID_PREFIX = "downstream-"
MAX_PAGE_SIZE = 200
MAX_MANIFEST_BYTES = 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024

SCENARIO_DESCRIPTIONS = {
    "normal_run": "Steady synthetic refinery monitoring window.",
    "receipt_distillation_shift": "Synthetic receipt and distillation transition.",
    "conversion_treating_degradation": "Synthetic conversion and treating degradation.",
    "blending_loading_shift": "Synthetic blending and loading shift.",
    "utility_sag": "Synthetic utility voltage and power sag.",
    "environmental_workflow": "Synthetic environmental review workflow.",
    "analyzer_delay": "Synthetic analyzer result delay.",
    "communications_degradation": "Synthetic delayed, stale, and missing communications.",
}


class DownstreamDashboardError(RuntimeError):
    """Raised when a public downstream run cannot be read safely."""


class DownstreamControlError(RuntimeError):
    """Raised when a downstream simulation cannot be started."""


def _safe_run_id(run_id: str) -> str:
    if (
        not isinstance(run_id, str)
        or not run_id.startswith(RUN_ID_PREFIX)
        or len(run_id) > 96
        or not all(character.isalnum() or character in "-_" for character in run_id)
    ):
        raise DownstreamDashboardError("invalid downstream run identifier")
    return run_id


def _safe_record_type(record_type: str) -> str:
    if record_type not in PUBLIC_RECORD_TYPES:
        raise DownstreamDashboardError("unsupported public record type")
    return record_type


def _public_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    sanitized = copy.deepcopy(manifest)
    sanitized["files"] = [
        entry
        for entry in sanitized.get("files", [])
        if entry.get("record_type") in PUBLIC_RECORD_TYPES
    ]
    if isinstance(sanitized.get("record_counts"), dict):
        sanitized["record_counts"].pop("ground_truth", None)
    sanitized.pop("logs_manifest", None)
    policy = sanitized.setdefault("policy", {})
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


class LocalDownstreamRunStore:
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
                raise DownstreamDashboardError("downstream manifest is too large")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise DownstreamDashboardError("downstream manifest is unavailable") from error
        if not isinstance(manifest, dict) or not isinstance(manifest.get("run_id"), str):
            raise DownstreamDashboardError("downstream manifest is invalid")
        return manifest

    def _find(self, run_id: str) -> tuple[Path, dict[str, Any]]:
        resolved = _safe_run_id(run_id)
        for directory in self._directories():
            manifest = self._load_manifest(directory)
            if manifest["run_id"] == resolved:
                return directory, manifest
        raise DownstreamDashboardError("downstream run is unavailable")

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
            raise DownstreamDashboardError("record cursor must be non-negative")
        entry = next(
            (
                item
                for item in manifest.get("files", [])
                if item.get("record_type") == resolved_type
            ),
            None,
        )
        if entry is None:
            raise DownstreamDashboardError("public artifact is unavailable")
        relative = PurePosixPath(entry["path"])
        if relative.is_absolute() or ".." in relative.parts or "\\" in entry["path"]:
            raise DownstreamDashboardError("public artifact path is unsafe")
        path = (directory / Path(*relative.parts)).resolve()
        try:
            path.relative_to(directory.resolve())
        except ValueError as error:
            raise DownstreamDashboardError("public artifact path is unsafe") from error
        try:
            if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                raise DownstreamDashboardError("public artifact checksum mismatch")
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
        except (OSError, json.JSONDecodeError) as error:
            raise DownstreamDashboardError("public artifact could not be read") from error
        return {
            "run_id": run_id,
            "record_type": resolved_type,
            "records": records,
            "next_cursor": None if complete else next_cursor,
            "count": len(records),
        }


class AdlsDownstreamRunStore:
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
                raise DownstreamDashboardError(
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
        client = self.file_system.get_file_client(path)
        try:
            properties = client.get_file_properties()
            if properties.size > MAX_MANIFEST_BYTES:
                raise DownstreamDashboardError("downstream manifest is too large")
            payload = client.download_file().readall()
            value = json.loads(payload)
        except DownstreamDashboardError:
            raise
        except self._storage_errors as error:
            raise DownstreamDashboardError("ADLS manifest is unavailable") from error
        if not isinstance(value, dict):
            raise DownstreamDashboardError("ADLS manifest is invalid")
        return value

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        try:
            paths = self.file_system.get_paths(path=self.path_prefix, recursive=True)
            file_paths = {
                item.name: item for item in paths if not item.is_directory
            }
            manifest_paths = [
                path
                for path in file_paths
                if path.endswith("/manifest.json")
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
            raise DownstreamDashboardError("ADLS run inventory is unavailable") from error
        runs = [
            _manifest_summary(self._download_json(path))
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
            raise DownstreamDashboardError("ADLS run is not finalized")
        manifest = self._download_json(f"{prefix}/manifest.json")
        if manifest.get("run_id") != run_id:
            raise DownstreamDashboardError("ADLS run manifest does not match its path")
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
            raise DownstreamDashboardError("record cursor must be non-negative")
        client = self.file_system.get_file_client(
            f"{self._run_prefix(run_id)}/{resolved_type}.jsonl"
        )
        try:
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
                if position >= size and pending.strip():
                    records.append(json.loads(pending))
                    next_boundary = size
                    pending = b""
            next_cursor = None if next_boundary >= size else next_boundary
        except (json.JSONDecodeError, OSError) as error:
            raise DownstreamDashboardError("ADLS public artifact is invalid") from error
        except self._storage_errors as error:
            raise DownstreamDashboardError("ADLS public artifact is unavailable") from error
        return {
            "run_id": run_id,
            "record_type": resolved_type,
            "records": records,
            "next_cursor": next_cursor,
            "count": len(records),
        }


class DownstreamSimulationController:
    """Run one bounded simulation at a time and emit instance-rich alerts."""

    def __init__(
        self,
        output_root: str | Path,
        public_sink_factory: Callable[[], Any | None] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.output_root = Path(output_root)
        self.public_sink_factory = public_sink_factory or (lambda: None)
        self.logger = logger or logging.getLogger("iot.downstream")
        self._lock = threading.Lock()
        self._status: dict[str, Any] = {"state": "idle"}

    def status(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._status)

    def start(self, scenario: str, operator: str | None) -> dict[str, Any]:
        if not isinstance(scenario, str) or scenario not in SCENARIOS:
            raise DownstreamControlError("unsupported downstream scenario")
        with self._lock:
            if self._status.get("state") in {"queued", "running"}:
                raise DownstreamControlError("a downstream simulation is already active")
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
            name=f"downstream-{job_id}",
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
        aligned = started - timedelta(seconds=started.second % 5)
        config = {
            "schema_version": "1.0",
            "seed": int(uuid.UUID(job_id[4:]).int % 2_147_483_647),
            "scenario": scenario,
            "site": {
                "site_id": "fictional-downstream-refinery-001",
                "display_name": "Fictional Downstream Refinery",
                "time_zone": "America/Chicago",
            },
            "simulation": {
                "start_utc": aligned.isoformat().replace("+00:00", "Z"),
                "end_utc": (aligned + timedelta(minutes=5))
                .isoformat()
                .replace("+00:00", "Z"),
            },
            "monitoring": {
                "max_records_per_type": 1000,
                "max_total_records": 10000,
            },
        }
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
                public_sink=self.public_sink_factory(),
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
        except Exception as error:
            self.logger.exception(
                "Downstream simulation failed",
                extra={"event_name": "downstream_simulation_failed", "job_id": job_id},
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
        if scenario == "normal_run":
            return 0
        count = 0
        with (output / "events.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                event = json.loads(line)
                transition = event.get("state_transition", {})
                if (
                    event.get("severity") == "info"
                    and transition.get("to") != "attention_required"
                ):
                    continue
                dimensions = {
                    "event_name": "downstream_simulation_alert",
                    "run_id": manifest["run_id"],
                    "scenario_id": event["scenario_id"],
                    "scenario": scenario,
                    "site_id": event["site_id"],
                    "area_id": event["area_id"],
                    "asset_id": event.get("asset_id", ""),
                    "event_id": event["event_id"],
                    "severity": event["severity"],
                    "state_from": transition.get("from", ""),
                    "state_to": transition.get("to", ""),
                    "event_time_utc": event["event_time_utc"],
                    "source_time_utc": event["source_time_utc"],
                    "ingest_time_utc": event["ingest_time_utc"],
                    "evidence_telemetry_ids": json.dumps(
                        event.get("evidence_telemetry_ids", []),
                        separators=(",", ":"),
                    ),
                    "is_synthetic": True,
                    "operational_use": "prohibited",
                }
                self.logger.warning(
                    "Synthetic downstream simulation alert instance",
                    extra=dimensions,
                )
                count += 1
        return count
