"""Generate deterministic compact-portfolio artifacts and content-derived manifests."""

from __future__ import annotations

import copy
import hashlib
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from .catalog import (
    CADENCE_ALLOCATION,
    MODULE_DEFINITIONS,
    PRODUCT_FAMILIES,
    SITE_DEFINITIONS,
    build_catalog,
    project_scheduled_telemetry_count,
)
from .config import validate_config
from .io import canonical_json, sha256_file, write_csv, write_jsonl
from .simulation import simulate

GENERATOR_VERSION = "0.1.0"
RECORD_TYPES = (
    "assets",
    "devices",
    "tags",
    "topology",
    "telemetry",
    "product_summaries",
    "events",
    "device_logs",
    "detector_outputs",
    "physical_confirmation_events",
    "ground_truth",
)


def _clear_generated_artifacts(output: Path) -> None:
    for record_type in RECORD_TYPES:
        for suffix in (".jsonl", ".csv"):
            path = output / f"{record_type}{suffix}"
            if path.is_file() or path.is_symlink():
                path.unlink()
    manifest = output / "manifest.json"
    if manifest.is_file() or manifest.is_symlink():
        manifest.unlink()
    telemetry = output / "telemetry"
    if telemetry.is_symlink() or telemetry.is_file():
        telemetry.unlink()
    elif telemetry.is_dir():
        shutil.rmtree(telemetry)


def _inventory_entry(
    root: Path,
    path: Path,
    record_type: str,
    record_count: int,
) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "record_type": record_type,
        "format": path.suffix.lstrip("."),
        "record_count": record_count,
        "sha256": sha256_file(path),
    }


def generate_dataset(
    config: dict[str, Any],
    output_directory: str | Path,
    force: bool = False,
) -> dict[str, Any]:
    normalized = validate_config(copy.deepcopy(config))
    config_text = canonical_json(normalized)
    config_digest = hashlib.sha256(config_text.encode("utf-8")).hexdigest()
    run_id = f"compact-portfolio-{hashlib.sha256(f'{GENERATOR_VERSION}|{config_digest}'.encode('utf-8')).hexdigest()[:20]}"
    output = Path(output_directory)
    if output.exists() and any(output.iterdir()) and not force:
        raise FileExistsError(f"output directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    if force:
        _clear_generated_artifacts(output)

    assets, devices, tags, topology = build_catalog(normalized)
    simulated = simulate(normalized, run_id, assets, devices, tags, output)

    records = {
        "assets": assets,
        "devices": devices,
        "tags": tags,
        "topology": topology,
        "product_summaries": simulated["product_summaries"],
        "events": simulated["events"],
        "device_logs": simulated["device_logs"],
        "detector_outputs": simulated["detector_outputs"],
        "physical_confirmation_events": simulated["physical_confirmation_events"],
        "ground_truth": simulated["ground_truth"],
    }

    inventory: list[dict[str, Any]] = []
    for record_type, record_values in records.items():
        jsonl_path = output / f"{record_type}.jsonl"
        write_jsonl(jsonl_path, record_values)
        inventory.append(_inventory_entry(output, jsonl_path, record_type, len(record_values)))
        if normalized["output"]["include_csv"]:
            csv_path = output / f"{record_type}.csv"
            write_csv(csv_path, record_values)
            inventory.append(_inventory_entry(output, csv_path, record_type, len(record_values)))

    for entry in simulated["telemetry_files"]:
        inventory.append(entry)

    inventory.sort(key=lambda item: item["path"])
    public_config = copy.deepcopy(normalized)
    public_config["localization"].pop("hidden_withdrawal_route_fraction", None)
    duration_seconds = int(
        (
            datetime.fromisoformat(normalized["simulation"]["end_utc"][:-1] + "+00:00")
            - datetime.fromisoformat(normalized["simulation"]["start_utc"][:-1] + "+00:00")
        ).total_seconds()
    )
    manifest = {
        "schema_version": "1.0",
        "generator": {
            "name": "iot-compact-portfolio-simulator",
            "version": GENERATOR_VERSION,
        },
        "run_id": run_id,
        "scenario_id": simulated["ground_truth"][0]["scenario_id"],
        "normalized_config": public_config,
        "config_digest_sha256": config_digest,
        "seed": normalized["seed"],
        "counts": {
            "sites": len(SITE_DEFINITIONS),
            "modules": len(MODULE_DEFINITIONS),
            "assets": len(assets),
            "devices": len(devices),
            "tags": len(tags),
        },
        "portfolio": {
            "display_name": normalized["enterprise"]["display_name"],
            "sites": [
                {
                    "site_id": site["site_id"],
                    "display_name": site["display_name"],
                    "module_ids": list(site["module_ids"]),
                }
                for site in SITE_DEFINITIONS
            ],
            "modules": [
                {
                    "module_id": module["module_id"],
                    "site_id": module["site_id"],
                    "display_name": module["display_name"],
                    "module_type": module["module_id"],
                    "product_families": [
                        module["primary_product_family"],
                        *module["supporting_product_families"],
                    ],
                    "fidelity": module["fidelity"],
                }
                for module in MODULE_DEFINITIONS
            ],
        },
        "module_allocation": [
            {
                "site_id": module["site_id"],
                "module_id": module["module_id"],
                "equipment_count": module["equipment_count"],
                "device_count": module["device_count"],
                "tag_count": module["tag_count"],
                "cadence_counts": module["cadence_counts"],
                "primary_product_family": module["primary_product_family"],
                "fidelity": module["fidelity"],
            }
            for module in MODULE_DEFINITIONS
        ],
        "cadence_allocation": copy.deepcopy(CADENCE_ALLOCATION),
        "projections": {
            "one_hour_scheduled_records": project_scheduled_telemetry_count(3600),
            "six_hour_scheduled_records": project_scheduled_telemetry_count(21600),
            "twenty_four_hour_scheduled_records": project_scheduled_telemetry_count(86400),
            "window_scheduled_records": project_scheduled_telemetry_count(duration_seconds),
        },
        "emitted_telemetry": {
            "scheduled_record_count": simulated["scheduled_telemetry_record_count"],
            "scenario_added_record_count": simulated["scenario_added_telemetry_record_count"],
            "total_record_count": simulated["scheduled_telemetry_record_count"] + simulated["scenario_added_telemetry_record_count"],
            "shard_count": len(simulated["telemetry_files"]),
        },
        "record_counts": {
            **{name: len(values) for name, values in records.items()},
            "telemetry": simulated["scheduled_telemetry_record_count"] + simulated["scenario_added_telemetry_record_count"],
        },
        "product_families": list(PRODUCT_FAMILIES),
        "files": inventory,
        "fidelity_tiers": [
            "physics_lite_corridor",
            "causal_aggregate",
            "business_summary",
        ],
        "policy": {
            "is_synthetic": True,
            "operational_use": "prohibited",
            "field_calibration": "none",
            "ground_truth_public": False,
            "ground_truth_detector_input": "prohibited",
            "telemetry_csv_support": "jsonl_shards_only",
        },
        "determinism": {
            "window_semantics": "half_open_[start,end)",
            "base_tick_seconds": 5,
            "encoding": "UTF-8",
            "line_endings": "LF",
            "float_policy": "finite_values_rounded_to_six_decimals",
        },
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_text(canonical_json(manifest) + "\n", encoding="utf-8", newline="\n")
    return manifest
