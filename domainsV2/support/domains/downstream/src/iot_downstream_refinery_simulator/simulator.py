"""Generate deterministic downstream refinery artifacts and manifests."""

from __future__ import annotations

import copy
import hashlib
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .catalog import AREA_DEFINITIONS, CADENCE_ALLOCATION, MONITORING_RECORD_TYPES, NORMAL_MONITORING_COUNTS, build_catalog, project_scheduled_telemetry_count
from .config import validate_config
from .io import canonical_json, sha256_file, write_jsonl
from .parts import PARTS_RECORD_TYPES, build_parts
from .simulation import simulate

GENERATOR_VERSION = "0.1.0"
RECORD_TYPES = ("assets", "devices", "tags", "topology", "telemetry", *MONITORING_RECORD_TYPES, "ground_truth")
PUBLIC_RECORD_TYPES = RECORD_TYPES[:-1]
#: Parts, equipment and inventory families. Written locally for review, never
#: handed to a sink and never listed in the published manifest: they are not
#: part of the published run contract.
LOCAL_ONLY_RECORD_TYPES = PARTS_RECORD_TYPES
ALL_RECORD_TYPES = (*RECORD_TYPES, *LOCAL_ONLY_RECORD_TYPES)


def _clear_generated_artifacts(output: Path) -> None:
    for record_type in ALL_RECORD_TYPES:
        path = output / f"{record_type}.jsonl"
        if path.is_file() or path.is_symlink():
            path.unlink()
    for filename in ("manifest.json", "logs-manifest.json", "local_only_manifest.json"):
        path = output / filename
        if path.is_file() or path.is_symlink():
            path.unlink()


def _inventory_entry(path: Path, record_type: str, record_count: int) -> dict[str, Any]:
    return {"path": path.name, "record_type": record_type, "format": "jsonl", "record_count": record_count, "sha256": sha256_file(path)}


def generate_dataset(
    config: dict[str, Any],
    output_directory: str | Path,
    force: bool = False,
    log_sink: Any | None = None,
    public_sink: Any | None = None,
) -> dict[str, Any]:
    normalized = validate_config(copy.deepcopy(config))
    config_text = canonical_json(normalized)
    config_digest = hashlib.sha256(config_text.encode("utf-8")).hexdigest()
    run_id = f"downstream-{hashlib.sha256(f'{GENERATOR_VERSION}|{config_digest}'.encode('utf-8')).hexdigest()[:20]}"
    output = Path(output_directory)
    if output.exists() and any(output.iterdir()) and not force:
        raise FileExistsError(f"output directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    if force:
        _clear_generated_artifacts(output)

    assets, devices, tags, topology = build_catalog(normalized)
    simulated = simulate(normalized, run_id, assets, devices, tags)
    scenario_id = simulated["scenario_id"]
    for record_set in (assets, devices, tags, topology):
        for record in record_set:
            record["run_id"] = run_id
            record["scenario_id"] = scenario_id
    records = {"assets": assets, "devices": devices, "tags": tags, "topology": topology, "telemetry": simulated["telemetry"], **{record_type: simulated[record_type] for record_type in MONITORING_RECORD_TYPES}, "ground_truth": simulated["ground_truth"]}
    records.update(build_parts(normalized, run_id, scenario_id, assets))

    monitoring_limits = normalized["monitoring"]
    for record_type in MONITORING_RECORD_TYPES:
        if len(records[record_type]) > monitoring_limits["max_records_per_type"]:
            raise ValueError(f"{record_type} exceeds monitoring.max_records_per_type")
    monitoring_total = sum(len(records[record_type]) for record_type in MONITORING_RECORD_TYPES)
    if monitoring_total > monitoring_limits["max_total_records"]:
        raise ValueError("monitoring records exceed monitoring.max_total_records")

    inventory: list[dict[str, Any]] = []
    monitoring_entries: list[dict[str, Any]] = []
    local_only_inventory: list[dict[str, Any]] = []
    for record_type in ALL_RECORD_TYPES:
        path = output / f"{record_type}.jsonl"
        write_jsonl(path, records[record_type])
        entry = _inventory_entry(path, record_type, len(records[record_type]))
        if record_type in LOCAL_ONLY_RECORD_TYPES:
            # Local-only families stay out of the published contract and are
            # never handed to a sink.
            local_only_inventory.append(entry)
            continue
        inventory.append(entry)
        if record_type in PUBLIC_RECORD_TYPES and public_sink is not None:
            public_sink.artifact_ready(path, path.name, run_id, entry)
        if record_type in MONITORING_RECORD_TYPES:
            monitoring_entries.append(entry)
            if log_sink is not None:
                log_sink.artifact_ready(path, path.name, run_id, entry)

    inventory.sort(key=lambda item: item["path"])
    monitoring_entries.sort(key=lambda item: item["path"])
    local_only_inventory.sort(key=lambda item: item["path"])
    logs_manifest = {
        "schema_version": "1.0",
        "generator": {"name": "iot-downstream-refinery-simulator", "version": GENERATOR_VERSION},
        "run_id": run_id,
        "scenario_id": scenario_id,
        "site_id": normalized["site"]["site_id"],
        "record_counts": {record_type: len(records[record_type]) for record_type in MONITORING_RECORD_TYPES},
        "total_records": monitoring_total,
        "files": monitoring_entries,
        "private_truth_included": False,
        "policy": {"monitoring_only": True, "private_truth_excluded": True, "telemetry_excluded": True, "structural_catalog_excluded": True, "operational_use": "prohibited"},
        "fidelity_tiers": ["F0", "F2", "F3", "FH"],
        "is_synthetic": True,
        "operational_use": "prohibited",
    }
    logs_manifest_path = output / "logs-manifest.json"
    logs_manifest_path.write_text(
        canonical_json(logs_manifest) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    duration_seconds = int((datetime.fromisoformat(normalized["simulation"]["end_utc"][:-1] + "+00:00") - datetime.fromisoformat(normalized["simulation"]["start_utc"][:-1] + "+00:00")).total_seconds())
    manifest = {
        "schema_version": "1.0",
        "generator": {"name": "iot-downstream-refinery-simulator", "version": GENERATOR_VERSION},
        "run_id": run_id,
        "scenario_id": scenario_id,
        "normalized_config": normalized,
        "config_digest_sha256": config_digest,
        "seed": normalized["seed"],
        "fixed_profile": {"site_count": 1, "area_count": len(AREA_DEFINITIONS), "asset_count": len(assets), "device_count": len(devices), "tag_count": len(tags), "cadence_allocation": dict(CADENCE_ALLOCATION), "smoke_window_seconds": 300, "smoke_scheduled_telemetry_records": project_scheduled_telemetry_count(300), "one_hour_scheduled_telemetry_records": project_scheduled_telemetry_count(3600)},
        "record_counts": {record_type: len(records[record_type]) for record_type in RECORD_TYPES},
        "monitoring_caps": dict(monitoring_limits),
        "normal_monitoring_record_counts": dict(NORMAL_MONITORING_COUNTS),
        "files": inventory,
        "logs_manifest": {"path": logs_manifest_path.name, "sha256": sha256_file(logs_manifest_path), "record_type_count": len(MONITORING_RECORD_TYPES), "total_records": monitoring_total},
        "policy": {"is_synthetic": True, "operational_use": "prohibited", "public_telemetry_layer": "delivered_observation_only", "private_truth_publication": "prohibited", "monitoring_destination_scope": list(MONITORING_RECORD_TYPES), "output_format": "jsonl_only"},
        "fidelity_tiers": ["F0", "F1", "F2", "F3", "FH"],
        "determinism": {"window_semantics": "half_open_[start,end)", "encoding": "UTF-8", "line_endings": "LF", "float_policy": "finite_values_rounded_to_six_decimals", "duration_seconds": duration_seconds},
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_text(
        canonical_json(manifest) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    local_only_manifest = {
        "schema_version": "1.0",
        "run_id": run_id,
        "scenario_id": scenario_id,
        "publication": "never_published_local_only",
        "parts_inventory_scope": "catalog_and_opening_stock_only_no_consumption",
        "record_types": list(LOCAL_ONLY_RECORD_TYPES),
        "record_counts": {key: len(records[key]) for key in LOCAL_ONLY_RECORD_TYPES},
        "files": local_only_inventory,
        "is_synthetic": True,
        "operational_use": "prohibited",
    }
    (output / "local_only_manifest.json").write_text(
        canonical_json(local_only_manifest) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    if log_sink is not None:
        log_sink.finalize(output, logs_manifest_path, logs_manifest)
    if public_sink is not None:
        public_manifest = copy.deepcopy(manifest)
        public_manifest["files"] = [
            entry
            for entry in public_manifest["files"]
            if entry["record_type"] in PUBLIC_RECORD_TYPES
        ]
        public_manifest["record_counts"].pop("ground_truth", None)
        public_manifest["policy"]["private_truth_publication"] = "excluded"
        with tempfile.TemporaryDirectory() as temporary:
            public_manifest_path = Path(temporary) / "manifest.json"
            public_manifest_path.write_text(
                canonical_json(public_manifest) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            public_sink.finalize(output, public_manifest_path, public_manifest)
    return manifest
