"""Dataset orchestration and content-derived manifest generation."""

from __future__ import annotations

import copy
import hashlib
import json
from importlib.resources import files
from pathlib import Path
from typing import Any

from .catalog import build_catalog
from .config import validate_config
from .io import canonical_json, sha256_file, write_csv, write_jsonl
from .model_state import MODEL_STATE_SCHEMA_VERSION
from .simulation import build_device_logs, build_schedule, simulate_telemetry
from .sinks import DatasetSink

GENERATOR_VERSION = "0.6.0"
MANIFEST_SCHEMA_VERSION = "1.0"
RECORD_TYPES = (
    "assets",
    "topology",
    "telemetry",
    "events",
    "device_logs",
    "alerts",
    "incidents",
    "ground_truth",
)
EMPTY_CSV_FIELDS = {
    "device_logs": sorted(
        {
            "schema_version",
            "log_id",
            "run_id",
            "scenario_id",
            "asset_id",
            "event_time_utc",
            "source_time_utc",
            "time_zone",
            "is_synthetic",
            "level",
            "category",
            "code",
            "message",
            "attributes",
            "related_event_id",
            "correlation_id",
            "quality",
        }
    ),
    "events": sorted(
        {
            "schema_version",
            "event_id",
            "run_id",
            "scenario_id",
            "asset_id",
            "event_time_utc",
            "time_zone",
            "event_type",
            "severity",
            "state_transition",
            "correlation_id",
            "causation_id",
            "quality",
        }
    ),
    "alerts": sorted(
        {
            "schema_version",
            "alert_id",
            "run_id",
            "scenario_id",
            "storm_id",
            "storm_revision",
            "is_synthetic",
            "public_safety",
            "status",
            "message_type",
            "scope",
            "event_time_utc",
            "effective_utc",
            "onset_utc",
            "expires_utc",
            "area_ref",
            "area_name",
            "event",
            "urgency",
            "severity",
            "certainty",
            "headline",
            "description",
            "instruction",
            "quality",
        }
    ),
    "incidents": sorted(
        {
            "schema_version",
            "incident_record_id",
            "incident_id",
            "run_id",
            "scenario_id",
            "is_synthetic",
            "public_safety",
            "exercise_status",
            "scope",
            "title",
            "incident_type",
            "severity",
            "status",
            "area_ref",
            "area_name",
            "owner_team",
            "detection_source",
            "root_cause",
            "caused_by",
            "correlation_id",
            "detected_at_utc",
            "updated_at_utc",
            "resolved_at_utc",
            "target_asset_id",
            "affected_asset_ids",
            "affected_service_points",
            "service_state",
            "lifecycle",
            "quality",
        }
    ),
}


def _reference_catalog() -> list[dict[str, Any]]:
    resource = files("iot_grid_simulator").joinpath("data/reference_catalog.json")
    return json.loads(resource.read_text(encoding="utf-8"))


def generate_dataset(
    config: dict[str, Any],
    output_directory: str | Path,
    force: bool = False,
    sink: DatasetSink | None = None,
    model_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate a complete deterministic dataset and return its manifest."""
    normalized = validate_config(copy.deepcopy(config))
    config_text = canonical_json(normalized)
    config_digest = hashlib.sha256(config_text.encode("utf-8")).hexdigest()
    run_material = f"{GENERATOR_VERSION}|{config_digest}".encode("utf-8")
    run_id = f"run-{hashlib.sha256(run_material).hexdigest()[:20]}"

    output = Path(output_directory)
    if output.exists() and any(output.iterdir()) and not force:
        raise FileExistsError(f"output directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)

    assets, topology, context = build_catalog(normalized)
    schedule, events, ground_truth, alerts, incidents = build_schedule(
        normalized, run_id, context
    )
    telemetry = simulate_telemetry(
        normalized,
        run_id,
        assets,
        context,
        schedule,
        model_state=model_state,
    )
    device_logs = build_device_logs(
        normalized,
        run_id,
        assets,
        schedule,
        events,
    )
    records_by_type = {
        "assets": assets,
        "topology": topology,
        "telemetry": telemetry,
        "events": events,
        "device_logs": device_logs,
        "alerts": alerts,
        "incidents": incidents,
        "ground_truth": ground_truth,
    }

    inventory: list[dict[str, Any]] = []
    for record_type in RECORD_TYPES:
        records = records_by_type[record_type]
        for extension in ("jsonl", "csv"):
            path = output / f"{record_type}.{extension}"
            if extension == "jsonl":
                write_jsonl(path, records)
            else:
                write_csv(path, records, EMPTY_CSV_FIELDS.get(record_type))
            entry = {
                "path": path.name,
                "record_type": record_type,
                "format": extension,
                "record_count": len(records),
                "sha256": sha256_file(path),
            }
            inventory.append(entry)
            if sink is not None:
                sink.artifact_ready(path, path.name, run_id, entry)

    if normalized["output"]["include_reference_catalog"]:
        catalog = _reference_catalog()
        catalog_path = output / "reference_catalog.json"
        catalog_path.write_text(canonical_json(catalog) + "\n", encoding="utf-8", newline="\n")
        entry = {
            "path": catalog_path.name,
            "record_type": "reference_catalog",
            "format": "json",
            "record_count": len(catalog),
            "sha256": sha256_file(catalog_path),
        }
        inventory.append(entry)
        if sink is not None:
            sink.artifact_ready(catalog_path, catalog_path.name, run_id, entry)
    elif force:
        stale_catalog = output / "reference_catalog.json"
        if stale_catalog.is_file():
            stale_catalog.unlink()

    if model_state is not None:
        state_path = output / "model_state.json"
        state_path.write_text(
            canonical_json(model_state) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        entry = {
            "path": state_path.name,
            "record_type": "model_state",
            "format": "json",
            "record_count": 1,
            "sha256": sha256_file(state_path),
        }
        inventory.append(entry)
        if sink is not None:
            sink.artifact_ready(state_path, state_path.name, run_id, entry)
    elif force:
        stale_state = output / "model_state.json"
        if stale_state.is_file():
            stale_state.unlink()

    if "environment" in normalized:
        environment_path = output / "environment.json"
        environment_path.write_text(
            canonical_json(normalized["environment"]) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        entry = {
            "path": environment_path.name,
            "record_type": "environment",
            "format": "json",
            "record_count": 1,
            "sha256": sha256_file(environment_path),
        }
        inventory.append(entry)
        if sink is not None:
            sink.artifact_ready(
                environment_path,
                environment_path.name,
                run_id,
                entry,
            )
    elif force:
        stale_environment = output / "environment.json"
        if stale_environment.is_file():
            stale_environment.unlink()

    inventory.sort(key=lambda item: item["path"])
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generator": {"name": "iot-grid-simulator", "version": GENERATOR_VERSION},
        "run_id": run_id,
        "scenario_id": schedule["scenario_id"],
        "normalized_config": normalized,
        "config_digest_sha256": config_digest,
        "seed": normalized["seed"],
        "record_counts": {
            record_type: len(records_by_type[record_type]) for record_type in RECORD_TYPES
        },
        "files": inventory,
        "source_reference_versions": {
            "config_schema": "1.0",
            "record_schema": "1.0",
            "topology": "fictional-v1",
            "reference_catalog": "2026-07-24",
            "model_state": MODEL_STATE_SCHEMA_VERSION,
            "incident": "1.0",
            "device_log": "1.0",
        },
        "determinism": {
            "timestamp_policy": "configuration-derived UTC only",
            "encoding": "UTF-8",
            "line_endings": "LF",
            "float_policy": "finite values rounded to six decimal places",
        },
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_text(canonical_json(manifest) + "\n", encoding="utf-8", newline="\n")
    if sink is not None:
        sink.finalize(output, manifest_path, manifest)
    return manifest
