"""Generate deterministic midstream artifacts and content-derived manifests."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

from .catalog import build_catalog
from .config import validate_config
from .io import canonical_json, sha256_file, write_csv, write_jsonl
from .localization import build_synthetic_confirmation, evaluate_localization
from .parts import PARTS_RECORD_TYPES, build_parts
from .simulation import simulate

GENERATOR_VERSION = "0.3.0"
RECORD_TYPES = (
    "assets",
    "topology",
    "telemetry",
    "events",
    "device_logs",
    "detector_outputs",
    "physical_confirmation_events",
    "ground_truth",
)
#: Parts and inventory families. Written locally for review, never handed to a
#: sink: they are not part of the published run contract.
LOCAL_ONLY_RECORD_TYPES = PARTS_RECORD_TYPES
ALL_RECORD_TYPES = (*RECORD_TYPES, *LOCAL_ONLY_RECORD_TYPES)


def generate_dataset(
    config: dict[str, Any],
    output_directory: str | Path,
    force: bool = False,
    sink: Any | None = None,
    log_sink: Any | None = None,
) -> dict[str, Any]:
    normalized = validate_config(copy.deepcopy(config))
    config_text = canonical_json(normalized)
    config_digest = hashlib.sha256(config_text.encode()).hexdigest()
    run_id = f"midstream-{hashlib.sha256(f'{GENERATOR_VERSION}|{config_digest}'.encode()).hexdigest()[:20]}"
    output = Path(output_directory)
    if output.exists() and any(output.iterdir()) and not force:
        raise FileExistsError(f"output directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)

    assets, topology = build_catalog(normalized)
    telemetry, events, device_logs, ground_truth = simulate(normalized, run_id, assets)
    detector_output, detector_event, detector_log = evaluate_localization(
        normalized,
        run_id,
        ground_truth[0]["scenario_id"],
        assets,
        telemetry,
        events[0]["event_id"],
    )
    confirmations, confirmation_events, confirmation_logs = (
        build_synthetic_confirmation(normalized, detector_output, ground_truth[0])
    )
    events.extend([detector_event, *confirmation_events])
    device_logs.extend([detector_log, *confirmation_logs])
    events.sort(key=lambda item: (item["event_time_utc"], item["event_id"]))
    device_logs.sort(
        key=lambda item: (
            item["event_time_utc"],
            item["asset_id"],
            item["code"],
        )
    )
    ground_truth[0]["correlated_event_ids"] = sorted(
        item["event_id"] for item in events
    )
    records = {
        "assets": assets,
        "topology": topology,
        "telemetry": telemetry,
        "events": events,
        "device_logs": device_logs,
        "detector_outputs": [detector_output],
        "physical_confirmation_events": confirmations,
        "ground_truth": ground_truth,
    }
    records.update(
        build_parts(normalized, run_id, ground_truth[0]["scenario_id"], assets)
    )
    inventory: list[dict[str, Any]] = []
    local_only_inventory: list[dict[str, Any]] = []
    for record_type in ALL_RECORD_TYPES:
        formats = ("jsonl", "csv") if normalized["output"]["include_csv"] else ("jsonl",)
        for extension in formats:
            path = output / f"{record_type}.{extension}"
            (write_jsonl if extension == "jsonl" else write_csv)(path, records[record_type])
            entry = {
                "path": path.name,
                "record_type": record_type,
                "format": extension,
                "record_count": len(records[record_type]),
                "sha256": sha256_file(path),
            }
            if record_type in LOCAL_ONLY_RECORD_TYPES:
                # Local-only families stay out of the published contract and
                # are never handed to a sink.
                local_only_inventory.append(entry)
                continue
            inventory.append(entry)
            for target in (sink, log_sink):
                if target is not None:
                    target.artifact_ready(path, path.name, run_id, entry)

    inventory.sort(key=lambda item: item["path"])
    local_only_inventory.sort(key=lambda item: item["path"])
    scenario_id = ground_truth[0]["scenario_id"]
    public_config = copy.deepcopy(normalized)
    public_config["localization"].pop("hidden_release_route_fraction")
    manifest = {
        "schema_version": "1.0",
        "generator": {"name": "iot-midstream-simulator", "version": GENERATOR_VERSION},
        "run_id": run_id,
        "scenario_id": scenario_id,
        "normalized_config": public_config,
        "config_digest_sha256": config_digest,
        "seed": normalized["seed"],
        "record_counts": {
            key: len(value)
            for key, value in records.items()
            if key not in LOCAL_ONLY_RECORD_TYPES
        },
        "local_only_record_types": list(LOCAL_ONLY_RECORD_TYPES),
        "files": inventory,
        "is_synthetic": True,
        "operational_use": "prohibited",
        "custody_transfer_use": "prohibited",
        "field_calibration": "none",
        "elevation_model": "flat_terrain_release_1",
        "leak_model": "quasi_steady_spatial_pressure_gradient_not_transient_wave",
        "localization_model": "sensor_interval_estimate_with_explicit_bounds",
        "localization_exact_location_claim": "prohibited",
        "detector_truth_access": "prohibited",
        "physical_confirmation": "synthetic_delayed_record_not_field_evidence",
        "volume_correction_model": "simplified_synthetic_not_api_mpms_certified",
        "meter_factor_type": "fictional_configuration_constant",
        "vibration_model": "scalar_heuristic_not_rotor_dynamics",
        "pig_operation_scenario": "not_implemented_release_1",
        "fidelity_tiers": ["high_structural", "moderate_physics_lite", "low_synthetic_heuristic"],
        "determinism": {
            "timestamp_policy": "configuration-derived UTC only",
            "encoding": "UTF-8",
            "line_endings": "LF",
            "float_policy": "finite values rounded to six decimal places",
        },
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_text(canonical_json(manifest) + "\n", encoding="utf-8", newline="\n")
    # Local-only metadata lives in its own file, not in the manifest. A sink
    # uploads manifest.json itself, so any local-only key placed there would
    # travel with it however carefully the dict were sanitized first. This file
    # is absent from `files`, so no sink ever sees it.
    local_only_manifest = {
        "schema_version": "1.0",
        "run_id": run_id,
        "scenario_id": scenario_id,
        "publication": "never_published_local_only",
        "parts_inventory_scope": "catalog_and_opening_stock_only_no_consumption",
        "record_types": list(LOCAL_ONLY_RECORD_TYPES),
        "record_counts": {
            key: len(records[key]) for key in LOCAL_ONLY_RECORD_TYPES
        },
        "files": local_only_inventory,
    }
    (output / "local_only_manifest.json").write_text(
        canonical_json(local_only_manifest) + "\n", encoding="utf-8", newline="\n"
    )
    for target in (sink, log_sink):
        if target is not None:
            target.finalize(output, manifest_path, manifest)
    return manifest
