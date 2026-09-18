"""ADLS monitoring-log sink for downstream refinery simulator outputs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from iot_grid_simulator.adls import AdlsError, AdlsSink as _BaseAdlsSink


class AdlsSink(_BaseAdlsSink):
    """ADLS sink with the downstream monitoring namespace as its safe default."""

    def __init__(
        self,
        account_url: str,
        file_system: str,
        path_prefix: str = "downstream-refinery/logs",
        mode: Literal["stream", "upload"] = "upload",
        auth_mode: Literal["default", "managed-identity"] = "default",
        managed_identity_client_id: str | None = None,
        overwrite: bool = False,
        chunk_size: int = 4 * 1024 * 1024,
        service_client: Any | None = None,
    ) -> None:
        super().__init__(
            account_url=account_url,
            file_system=file_system,
            path_prefix=path_prefix,
            mode=mode,
            auth_mode=auth_mode,
            managed_identity_client_id=managed_identity_client_id,
            overwrite=overwrite,
            chunk_size=chunk_size,
            service_client=service_client,
        )


class AdlsMonitoringLogSink:
    """Publish only allowlisted public monitoring logs to ADLS."""

    LOG_TYPES = frozenset(
        {
            "events",
            "alarm_journal",
            "operator_actions",
            "device_logs",
            "audit_logs",
            "delivery_logs",
            "analyzer_results",
            "condition_monitoring",
            "electrical_monitoring",
            "environmental_observations",
            "lab_results",
            "maintenance_context",
            "shipment_context",
        }
    )
    LOCAL_ONLY_TYPES = frozenset(
        {
            "assets",
            "devices",
            "tags",
            "topology",
            "telemetry",
            "ground_truth",
        }
    )

    def __init__(self, sink: AdlsSink) -> None:
        self.sink = sink

    def artifact_ready(
        self,
        path: Path,
        relative_path: str,
        run_id: str,
        metadata: dict[str, Any],
    ) -> None:
        record_type = metadata.get("record_type")
        if not isinstance(record_type, str) or not record_type:
            raise AdlsError("artifact metadata must include a non-empty record_type")
        if record_type in self.LOG_TYPES:
            expected_path = f"{record_type}.jsonl"
            if relative_path != expected_path or path.name != expected_path:
                raise AdlsError(
                    f"monitoring artifact path must be {expected_path} "
                    f"for record_type {record_type}"
                )
            self.sink.artifact_ready(path, relative_path, run_id, metadata)
            return
        if record_type in self.LOCAL_ONLY_TYPES:
            return
        raise AdlsError(f"unsupported record_type for monitoring sink: {record_type}")

    def finalize(
        self,
        output_directory: Path,
        logs_manifest_path: Path,
        logs_manifest: dict[str, Any],
    ) -> None:
        self._validate_logs_manifest(logs_manifest_path, logs_manifest)
        self.sink.finalize(output_directory, logs_manifest_path, logs_manifest)

    @classmethod
    def _validate_logs_manifest(
        cls,
        logs_manifest_path: Path,
        logs_manifest: dict[str, Any],
    ) -> None:
        if not isinstance(logs_manifest, dict):
            raise AdlsError("logs manifest must be a JSON object")
        if logs_manifest_path.name != "logs-manifest.json":
            raise AdlsError("logs manifest path must be logs-manifest.json")
        if logs_manifest.get("private_truth_included") is not False:
            raise AdlsError("logs manifest must set private_truth_included to false")

        files = logs_manifest.get("files")
        if not isinstance(files, list):
            raise AdlsError("logs manifest must include a files list")

        for index, entry in enumerate(files):
            if not isinstance(entry, dict):
                raise AdlsError(f"logs manifest file entry {index} must be an object")
            path = entry.get("path")
            if not isinstance(path, str) or not path:
                raise AdlsError(
                    f"logs manifest file entry {index} must include a non-empty path"
                )
            record_type = entry.get("record_type")
            if not isinstance(record_type, str) or not record_type:
                raise AdlsError(
                    f"logs manifest file entry {index} must include a non-empty record_type"
                )
            if record_type not in cls.LOG_TYPES:
                raise AdlsError(
                    f"logs manifest contains unsupported record_type: {record_type}"
                )
            expected_path = f"{record_type}.jsonl"
            if path != expected_path:
                raise AdlsError(
                    f"logs manifest path must be {expected_path} "
                    f"for record_type {record_type}"
                )


__all__ = ["AdlsError", "AdlsMonitoringLogSink", "AdlsSink"]
