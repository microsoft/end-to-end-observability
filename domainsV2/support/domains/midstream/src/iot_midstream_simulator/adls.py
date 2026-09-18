"""ADLS sinks for full datasets and a separate synthetic-log container."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from iot_grid_simulator.adls import AdlsError, AdlsSink

from .io import canonical_json


class AdlsSyntheticLogSink:
    """Publish synthetic evidence logs without hidden ground truth."""

    LOG_TYPES = {
        "events",
        "device_logs",
        "detector_outputs",
        "physical_confirmation_events",
    }

    def __init__(self, sink: AdlsSink) -> None:
        self.sink = sink

    def artifact_ready(
        self,
        path: Path,
        relative_path: str,
        run_id: str,
        metadata: dict[str, Any],
    ) -> None:
        if metadata["record_type"] in self.LOG_TYPES:
            self.sink.artifact_ready(path, relative_path, run_id, metadata)

    def finalize(
        self,
        output_directory: Path,
        manifest_path: Path,
        manifest: dict[str, Any],
    ) -> None:
        files = [
            item for item in manifest["files"] if item["record_type"] in self.LOG_TYPES
        ]
        log_manifest = {
            "schema_version": "1.0",
            "generator": manifest["generator"],
            "run_id": manifest["run_id"],
            "scenario_id": manifest["scenario_id"],
            "content_policy": "synthetic_observable_and_confirmation_logs_only",
            "ground_truth_included": False,
            "files": files,
        }
        path = output_directory / "logs-manifest.json"
        path.write_text(canonical_json(log_manifest) + "\n", encoding="utf-8", newline="\n")
        self.sink.finalize(output_directory, path, log_manifest)


__all__ = ["AdlsError", "AdlsSink", "AdlsSyntheticLogSink"]
