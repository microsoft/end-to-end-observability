"""Checksum-verified snapshots for the fictional compact refinery portfolio."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


class PortfolioDashboardError(RuntimeError):
    """Raised when a complete compact-portfolio dataset cannot be loaded."""


def _jsonl(paths: Iterable[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        with path.open(encoding="utf-8") as stream:
            records.extend(json.loads(line) for line in stream if line.strip())
    return records


class PortfolioSnapshotService:
    """Read one finalized portfolio without opening private ground truth."""

    _REQUIRED_TYPES = {
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
    }

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def get_snapshot(self) -> dict[str, Any]:
        manifest_path = self.root / "manifest.json"
        if not manifest_path.is_file():
            raise PortfolioDashboardError("portfolio manifest is unavailable")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        inventory = manifest.get("files")
        if not isinstance(inventory, list):
            raise PortfolioDashboardError("portfolio manifest file inventory is invalid")

        entries_by_type: dict[str, list[dict[str, Any]]] = {}
        for entry in inventory:
            record_type = entry.get("record_type")
            if isinstance(record_type, str):
                entries_by_type.setdefault(record_type, []).append(entry)
        missing = self._REQUIRED_TYPES - set(entries_by_type)
        if missing:
            raise PortfolioDashboardError(
                f"required portfolio artifacts are unavailable: {sorted(missing)}"
            )

        verified_paths: dict[str, list[Path]] = {}
        root = self.root.resolve()
        for record_type, entries in entries_by_type.items():
            if record_type == "ground_truth":
                continue
            paths: list[Path] = []
            for entry in entries:
                raw_path = entry.get("path")
                if (
                    not isinstance(raw_path, str)
                    or not raw_path
                    or "\\" in raw_path
                ):
                    raise PortfolioDashboardError("portfolio artifact path is unsafe")
                relative = PurePosixPath(raw_path)
                if (
                    relative.is_absolute()
                    or ".." in relative.parts
                    or any(":" in part for part in relative.parts)
                ):
                    raise PortfolioDashboardError("portfolio artifact path is unsafe")
                path = (root / Path(*relative.parts)).resolve()
                try:
                    path.relative_to(root)
                except ValueError as error:
                    raise PortfolioDashboardError(
                        "portfolio artifact path is unsafe"
                    ) from error
                if not path.is_file():
                    raise PortfolioDashboardError(
                        f"required portfolio artifact is unavailable: {relative}"
                    )
                if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                    raise PortfolioDashboardError(
                        f"portfolio artifact checksum mismatch: {relative}"
                    )
                paths.append(path)
            verified_paths[record_type] = paths

        assets = _jsonl(verified_paths["assets"])
        devices = _jsonl(verified_paths["devices"])
        tags = _jsonl(verified_paths["tags"])
        topology = _jsonl(verified_paths["topology"])
        summaries = _jsonl(verified_paths["product_summaries"])
        events = _jsonl(verified_paths["events"])
        logs = _jsonl(verified_paths["device_logs"])
        detectors = _jsonl(verified_paths["detector_outputs"])
        confirmations = _jsonl(verified_paths["physical_confirmation_events"])

        latest_by_tag: dict[str, dict[str, Any]] = {}
        telemetry_count = 0
        for path in verified_paths["telemetry"]:
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    item = json.loads(line)
                    telemetry_count += 1
                    tag_id = item["tag_id"]
                    previous = latest_by_tag.get(tag_id)
                    if previous is None or (
                        item["event_time_utc"],
                        item.get("ingest_time_utc", ""),
                        item.get("telemetry_id", ""),
                    ) > (
                        previous["event_time_utc"],
                        previous.get("ingest_time_utc", ""),
                        previous.get("telemetry_id", ""),
                    ):
                        latest_by_tag[tag_id] = item
        latest = list(latest_by_tag.values())
        latest.sort(
            key=lambda item: (
                item.get("site_id", ""),
                item.get("module_id", ""),
                item.get("tag_id", ""),
            )
        )
        if not latest:
            raise PortfolioDashboardError("portfolio telemetry is empty")

        tag_by_id = {item["tag_id"]: item for item in tags}
        asset_by_id = {item["asset_id"]: item for item in assets}
        device_by_id = {item["device_id"]: item for item in devices}
        site_ids = sorted(
            {
                item.get("site_id")
                for item in [*assets, *devices, *tags]
                if item.get("site_id")
            }
        )
        module_ids = sorted(
            {
                item.get("module_id")
                for item in [*assets, *devices, *tags]
                if item.get("module_id")
            }
        )

        def validity(item: dict[str, Any]) -> str:
            return item.get("quality", {}).get("validity", "unknown")

        quality_counts: dict[str, int] = {}
        delivery_counts: dict[str, int] = {}
        clock_counts: dict[str, int] = {}
        for item in latest:
            quality_counts[validity(item)] = quality_counts.get(validity(item), 0) + 1
            delivery = item.get("delivery_class", "scheduled")
            delivery_counts[delivery] = delivery_counts.get(delivery, 0) + 1
            clock = item.get("clock_state", "unavailable")
            clock_counts[clock] = clock_counts.get(clock, 0) + 1

        assets_by_module: dict[str, list[dict[str, Any]]] = {}
        devices_by_module: dict[str, list[dict[str, Any]]] = {}
        tags_by_module: dict[str, list[dict[str, Any]]] = {}
        latest_by_module: dict[str, list[dict[str, Any]]] = {}
        for item in assets:
            assets_by_module.setdefault(item.get("module_id", "unassigned"), []).append(item)
        for item in devices:
            devices_by_module.setdefault(item.get("module_id", "unassigned"), []).append(item)
        for item in tags:
            tags_by_module.setdefault(item.get("module_id", "unassigned"), []).append(item)
        for item in latest:
            latest_by_module.setdefault(item.get("module_id", "unassigned"), []).append(item)

        manifest_modules = {
            item.get("module_id"): item
            for item in manifest.get("portfolio", {}).get("modules", [])
            if isinstance(item, dict) and item.get("module_id")
        }
        if not manifest_modules:
            manifest_modules = {
                item.get("module_id"): item
                for item in manifest.get("module_allocation", [])
                if isinstance(item, dict) and item.get("module_id")
            }
        modules: list[dict[str, Any]] = []
        for module_id in module_ids:
            module_tags = tags_by_module.get(module_id, [])
            module_latest = latest_by_module.get(module_id, [])
            module_validity: dict[str, int] = {}
            for item in module_latest:
                state = validity(item)
                module_validity[state] = module_validity.get(state, 0) + 1
            product_families = sorted(
                {
                    family
                    for tag in module_tags
                    for family in (
                        tag.get("product_families")
                        or ([tag["product_family"]] if tag.get("product_family") else [])
                    )
                }
            )
            metadata = manifest_modules.get(module_id, {})
            module_type = metadata.get("module_type") or next(
                (
                    item.get("module_type")
                    for item in [*module_tags, *assets_by_module.get(module_id, [])]
                    if item.get("module_type")
                ),
                module_id,
            )
            module_site = metadata.get("site_id") or next(
                (
                    item.get("site_id")
                    for item in [*module_tags, *assets_by_module.get(module_id, [])]
                    if item.get("site_id")
                ),
                "unassigned",
            )
            good = module_validity.get("good", 0)
            modules.append(
                {
                    "module_id": module_id,
                    "module_type": module_type,
                    "site_id": module_site,
                    "display_name": metadata.get("display_name")
                    or str(module_type).replace("_", " ").title(),
                    "product_families": product_families,
                    "equipment_count": len(assets_by_module.get(module_id, [])),
                    "device_count": len(devices_by_module.get(module_id, [])),
                    "tag_count": len(module_tags),
                    "latest_telemetry_count": len(module_latest),
                    "quality": {
                        "validity_counts": module_validity,
                        "good_fraction": good / len(module_latest) if module_latest else 0.0,
                    },
                    "fidelity": metadata.get(
                        "fidelity",
                        next(
                            (
                                tag.get("fidelity")
                                for tag in module_tags
                                if tag.get("fidelity")
                            ),
                            "synthetic_causal_aggregate",
                        ),
                    ),
                    "state": (
                        "unavailable"
                        if module_validity.get("missing", 0)
                        else "degraded"
                        if sum(
                            module_validity.get(key, 0)
                            for key in ("stale", "questionable", "bad")
                        )
                        else "observed"
                    ),
                }
            )

        modules_by_site: dict[str, list[dict[str, Any]]] = {}
        for module in modules:
            modules_by_site.setdefault(module["site_id"], []).append(module)
        manifest_sites = {
            item.get("site_id"): item
            for item in manifest.get("portfolio", {}).get("sites", [])
            if isinstance(item, dict) and item.get("site_id")
        }
        sites = []
        for site_id in site_ids:
            site_modules = modules_by_site.get(site_id, [])
            metadata = manifest_sites.get(site_id, {})
            sites.append(
                {
                    "site_id": site_id,
                    "display_name": metadata.get("display_name")
                    or site_id.replace("-", " ").title(),
                    "module_ids": [item["module_id"] for item in site_modules],
                    "module_count": len(site_modules),
                    "equipment_count": sum(item["equipment_count"] for item in site_modules),
                    "device_count": sum(item["device_count"] for item in site_modules),
                    "tag_count": sum(item["tag_count"] for item in site_modules),
                    "state": (
                        "unavailable"
                        if any(item["state"] == "unavailable" for item in site_modules)
                        else "degraded"
                        if any(item["state"] == "degraded" for item in site_modules)
                        else "observed"
                    ),
                }
            )

        latest_summaries: dict[str, dict[str, Any]] = {}
        for item in summaries:
            family = item.get("product_family", "unassigned")
            previous = latest_summaries.get(family)
            item_time = item.get("event_time_utc") or item.get("window_end_utc", "")
            previous_time = (
                previous.get("event_time_utc") or previous.get("window_end_utc", "")
                if previous
                else ""
            )
            if previous is None or item_time > previous_time:
                latest_summaries[family] = item
        product_families = [
            {"product_family": family, "latest_summary": item}
            for family, item in sorted(latest_summaries.items())
        ]

        product_flows = [
            item
            for item in topology
            if item.get("relationship") in {"flows_to", "hands_off_to"}
            or item.get("relationship_type") in {"flows_to", "hands_off_to"}
        ]
        latest_detector = detectors[-1] if detectors else None
        latest_confirmation = confirmations[-1] if confirmations else None
        confirmation = {
            "available": latest_confirmation is not None,
            "result": (
                latest_confirmation.get("result")
                if latest_confirmation
                else "not_required"
            ),
            "dispatch_time_utc": (
                latest_confirmation.get("dispatch_time_utc")
                if latest_confirmation
                else None
            ),
            "confirmation_time_utc": (
                latest_confirmation.get("confirmation_time_utc")
                if latest_confirmation
                else None
            ),
            "available_after_utc": (
                latest_confirmation.get("available_after_utc")
                if latest_confirmation
                else None
            ),
            "fidelity": (
                latest_confirmation.get("fidelity")
                if latest_confirmation
                else "synthetic_confirmation_not_field_evidence"
            ),
        }
        event_time = max(item["event_time_utc"] for item in latest)
        good = quality_counts.get("good", 0)
        cadence_counts = manifest.get("cadence_tag_counts") or manifest.get(
            "cadence_allocation", {}
        )
        allocation = manifest.get("allocation_summary") or manifest.get("counts", {})
        policy = manifest.get("policy", {})
        projections = manifest.get("projected_telemetry_counts") or manifest.get(
            "projections", {}
        )

        watch = sorted(
            latest,
            key=lambda item: (
                validity(item) == "good",
                item.get("event_time_utc", ""),
                item.get("tag_id", ""),
            ),
            reverse=True,
        )[:40]
        watch = [
            {
                **item,
                "tag": tag_by_id.get(item["tag_id"]),
                "asset": asset_by_id.get(item.get("asset_id")),
                "device": device_by_id.get(item.get("device_id")),
            }
            for item in watch
        ]

        return {
            "source": {
                "run_id": manifest["run_id"],
                "scenario_id": manifest.get("scenario_id"),
                "event_time_utc": event_time,
                "generator": manifest["generator"],
                "schema_version": manifest["schema_version"],
                "config_digest_sha256": manifest["config_digest_sha256"],
                "display_name": manifest.get("portfolio", {}).get("display_name")
                or manifest.get("normalized_config", {})
                .get("enterprise", {})
                .get("display_name", "Compact Refinery Portfolio"),
            },
            "policy": {
                "is_synthetic": policy.get(
                    "is_synthetic", manifest.get("is_synthetic", True)
                ),
                "operational_use": policy.get(
                    "operational_use",
                    manifest.get("operational_use", "prohibited"),
                ),
                "custody_transfer_use": policy.get(
                    "custody_transfer_use",
                    manifest.get("custody_transfer_use", "prohibited"),
                ),
                "detector_truth_access": policy.get(
                    "ground_truth_detector_input",
                    manifest.get("detector_truth_access", "prohibited"),
                ),
                "exact_location_claim": manifest.get(
                    "localization_exact_location_claim", "prohibited"
                ),
                "physical_confirmation": manifest.get(
                    "physical_confirmation",
                    "synthetic_delayed_record_not_field_evidence",
                ),
                "fidelity_tiers": manifest.get("fidelity_tiers", []),
            },
            "portfolio": {
                "site_count": allocation.get("site_count", len(sites)),
                "module_count": allocation.get("module_count", len(modules)),
                "product_family_count": len(product_families),
                "equipment_count": allocation.get("equipment_count", len(assets)),
                "device_count": allocation.get("device_count", len(devices)),
                "tag_count": allocation.get("tag_count", len(tags)),
                "cadence_counts": cadence_counts,
                "emitted_telemetry_count": telemetry_count,
                "projected_telemetry_counts": projections,
            },
            "sites": sites,
            "modules": modules,
            "product_families": product_families,
            "product_flows": product_flows,
            "telemetry_watch": watch,
            "quality": {
                "latest_record_count": len(latest),
                "validity_counts": quality_counts,
                "good_fraction": good / len(latest) if latest else 0.0,
            },
            "communications": {
                "delivery_class_counts": delivery_counts,
                "clock_state_counts": clock_counts,
                "max_latency_ms": max(
                    (float(item.get("communications_latency_ms", 0.0)) for item in logs),
                    default=0.0,
                ),
                "max_queue_depth": max(
                    (int(item.get("queue_depth", 0)) for item in logs),
                    default=0,
                ),
                "max_abs_clock_offset_ms": max(
                    (abs(float(item.get("clock_offset_ms", 0.0))) for item in latest),
                    default=0.0,
                ),
                "max_clock_uncertainty_ms": max(
                    (float(item.get("clock_uncertainty_ms", 0.0)) for item in latest),
                    default=0.0,
                ),
            },
            "localization": {
                "detector": latest_detector,
                "confirmation": confirmation,
                "ground_truth_exposed": False,
            },
            "events": events[-40:],
            "device_logs": logs[-40:],
            "provenance": {
                "artifact_count": len(inventory),
                "record_counts": manifest.get("record_counts", {}),
                "files": [
                    {
                        "path": item["path"],
                        "record_type": item["record_type"],
                        "record_count": item["record_count"],
                    }
                    for item in inventory
                    if item.get("record_type") != "ground_truth"
                ],
                "public_checksums_verified": True,
                "ground_truth_excluded_from_api": True,
            },
        }
