"""Generate deterministic fictional upstream production artifacts and manifests."""

from __future__ import annotations

import copy
import hashlib
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .catalog import (
    ADLS_PUBLISHABLE_LOCAL_ONLY_RECORD_TYPES,
    AREA_DEFINITIONS,
    ARCHETYPES,
    CATALOG_RECORD_TYPES,
    LOCAL_ONLY_RECORD_TYPES,
    PRIVATE_RECORD_TYPES,
    PUBLIC_RECORD_TYPES,
    PUBLISHED_RECORD_TYPES,
    RECORD_FAMILY_OWNER,
    RECORD_TYPES,
    TELEMETRY_RECORD_TYPES,
    WORKFLOW_RECORD_TYPES,
    build_catalog,
    sanitize_public_manifest,
)
from .config import FIDELITY_TIERS, validate_config
from .io import canonical_json, sha256_file, write_jsonl
from .parts import build_parts_records
from .simulation import MAX_DELIVERY_DELAY_SECONDS, simulate

GENERATOR_VERSION = "0.1.0"
GENERATOR_NAME = "iot-upstream-simulator"


def _clear_generated_artifacts(output: Path) -> None:
    for record_type in RECORD_TYPES:
        path = output / f"{record_type}.jsonl"
        if path.is_file() or path.is_symlink():
            path.unlink()
    for filename in ("manifest.json", "logs-manifest.json"):
        path = output / filename
        if path.is_file() or path.is_symlink():
            path.unlink()


def _inventory_entry(path: Path, record_type: str, record_count: int) -> dict[str, Any]:
    return {
        "path": path.name,
        "record_type": record_type,
        "format": "jsonl",
        "record_count": record_count,
        "sha256": sha256_file(path),
    }


def generate_dataset(
    config: dict[str, Any],
    output_directory: str | Path,
    force: bool = False,
    log_sink: Any | None = None,
    public_sink: Any | None = None,
) -> dict[str, Any]:
    """Generate every upstream record family and return the full local manifest."""
    normalized = validate_config(copy.deepcopy(config))
    config_text = canonical_json(normalized)
    config_digest = hashlib.sha256(config_text.encode("utf-8")).hexdigest()
    run_id = f"upstream-{hashlib.sha256(f'{GENERATOR_VERSION}|{config_digest}'.encode('utf-8')).hexdigest()[:20]}"
    output = Path(output_directory)
    if output.exists() and any(output.iterdir()) and not force:
        raise FileExistsError(f"output directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    if force:
        _clear_generated_artifacts(output)

    catalog = build_catalog(normalized)
    simulated = simulate(normalized, run_id, catalog)
    scenario_id = simulated["scenario_id"]
    for record_type in CATALOG_RECORD_TYPES:
        for record in catalog[record_type]:
            record["run_id"] = run_id
            record["scenario_id"] = scenario_id
            record["record_type"] = record_type

    records: dict[str, list[dict[str, Any]]] = {
        record_type: catalog[record_type] for record_type in CATALOG_RECORD_TYPES
    }
    for record_type in (*TELEMETRY_RECORD_TYPES, *WORKFLOW_RECORD_TYPES, *PRIVATE_RECORD_TYPES):
        records[record_type] = simulated[record_type]

    start = datetime.fromisoformat(normalized["simulation"]["start_utc"][:-1] + "+00:00")
    end = datetime.fromisoformat(normalized["simulation"]["end_utc"][:-1] + "+00:00")
    parts = build_parts_records(
        {
            "config": normalized,
            "run_id": run_id,
            "scenario": normalized["scenario"],
            "scenario_id": scenario_id,
            "catalog": catalog,
            "workflow": records,
            "start": start,
            "end": end,
        }
    )
    for record_type in LOCAL_ONLY_RECORD_TYPES:
        records[record_type] = parts[record_type]

    limits = normalized["monitoring"]
    for record_type in WORKFLOW_RECORD_TYPES:
        if len(records[record_type]) > limits["max_records_per_type"]:
            raise ValueError(f"{record_type} exceeds monitoring.max_records_per_type")
    workflow_total = sum(len(records[record_type]) for record_type in WORKFLOW_RECORD_TYPES)
    if workflow_total > limits["max_total_records"]:
        raise ValueError("workflow records exceed monitoring.max_total_records")

    inventory: list[dict[str, Any]] = []
    workflow_entries: list[dict[str, Any]] = []
    for record_type in RECORD_TYPES:
        path = output / f"{record_type}.jsonl"
        write_jsonl(path, records[record_type])
        entry = _inventory_entry(path, record_type, len(records[record_type]))
        inventory.append(entry)
        if record_type in PUBLISHED_RECORD_TYPES and public_sink is not None:
            public_sink.artifact_ready(path, path.name, run_id, entry)
        if record_type in WORKFLOW_RECORD_TYPES:
            workflow_entries.append(entry)
            if log_sink is not None:
                log_sink.artifact_ready(path, path.name, run_id, entry)

    inventory.sort(key=lambda item: item["path"])
    workflow_entries.sort(key=lambda item: item["path"])
    logs_manifest = {
        "schema_version": "1.0",
        "generator": {"name": GENERATOR_NAME, "version": GENERATOR_VERSION},
        "run_id": run_id,
        "scenario_id": scenario_id,
        "site_id": normalized["site"]["site_id"],
        "facility_id": normalized["facility"]["facility_id"],
        "record_counts": {
            record_type: len(records[record_type]) for record_type in WORKFLOW_RECORD_TYPES
        },
        "total_records": workflow_total,
        "files": workflow_entries,
        "private_truth_included": False,
        "policy": {
            "monitoring_only": True,
            "private_truth_excluded": True,
            "telemetry_excluded": True,
            "structural_catalog_excluded": True,
            "operational_use": "prohibited",
        },
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

    duration_seconds = int((end - start).total_seconds())
    manifest = {
        "schema_version": "1.0",
        "generator": {"name": GENERATOR_NAME, "version": GENERATOR_VERSION},
        "run_id": run_id,
        "scenario_id": scenario_id,
        "normalized_config": normalized,
        "config_digest_sha256": config_digest,
        "seed": normalized["seed"],
        "profile": {
            "site_count": 1,
            "area_count": len(AREA_DEFINITIONS),
            "archetypes": list(ARCHETYPES),
            "pad_count": records["sites"][0]["pad_count"],
            "well_count": len(records["wells"]),
            "asset_count": len(records["assets"]),
            "device_count": len(records["devices"]),
            "signal_count": len(records["signals"]),
            "topology_edge_count": len(records["topology"]),
            "tick_count": simulated["tick_count"],
            "interval_seconds": normalized["simulation"]["interval_seconds"],
            "onset_utc": simulated["onset_utc"],
        },
        "record_counts": {record_type: len(records[record_type]) for record_type in RECORD_TYPES},
        "record_family_owner": dict(RECORD_FAMILY_OWNER),
        "monitoring_caps": dict(limits),
        "files": inventory,
        "logs_manifest": {
            "path": logs_manifest_path.name,
            "sha256": sha256_file(logs_manifest_path),
            "record_type_count": len(WORKFLOW_RECORD_TYPES),
            "total_records": workflow_total,
        },
        "policy": {
            "is_synthetic": True,
            "operational_use": "prohibited",
            "public_telemetry_layer": "delivered_observation_only",
            "private_truth_publication": "prohibited",
            "local_only_record_types": list(LOCAL_ONLY_RECORD_TYPES),
            "local_only_publication": (
                "adls_run_publication_approved_portal_and_dashboard_still_excluded"
            ),
            "adls_publishable_local_only_record_types": list(
                ADLS_PUBLISHABLE_LOCAL_ONLY_RECORD_TYPES
            ),
            "monitoring_destination_scope": list(WORKFLOW_RECORD_TYPES),
            "output_format": "jsonl_only",
            "standards_relationship": (
                "inspired_by_not_conformant_with_witsml_prodml_resqml_opcua_isa95"
            ),
            "deferred_profiles": ["drilling_and_completion", "offshore_production"],
        },
        "fidelity_tiers": list(FIDELITY_TIERS),
        "determinism": {
            "window_semantics": "half_open_[start,end)",
            "encoding": "UTF-8",
            "line_endings": "LF",
            "float_policy": "finite_values_rounded_to_six_decimals",
            "identifier_policy": "content_derived_sha256",
            "duration_seconds": duration_seconds,
            "delayed_availability_window_seconds": MAX_DELIVERY_DELAY_SECONDS,
        },
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_text(
        canonical_json(manifest) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    if log_sink is not None:
        log_sink.finalize(output, logs_manifest_path, logs_manifest)
    if public_sink is not None:
        public_manifest = sanitize_public_manifest(
            manifest,
            PUBLISHED_RECORD_TYPES,
            allow_adls_publishable=True,
        )
        with tempfile.TemporaryDirectory() as temporary:
            public_manifest_path = Path(temporary) / "manifest.json"
            public_manifest_path.write_text(
                canonical_json(public_manifest) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            public_sink.finalize(output, public_manifest_path, public_manifest)
    return manifest


__all__ = ["GENERATOR_NAME", "GENERATOR_VERSION", "generate_dataset"]
