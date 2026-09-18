"""Deterministic V2 views over the existing fictional domain physics.

Only immutable, bounded simulation horizons are cached. No legacy controller,
publisher, Flask application, or live-feed instance is constructed.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from importlib import import_module
from pathlib import Path
from types import FunctionType, SimpleNamespace
from typing import Any

from .contracts import Frame, MONITORING_SURFACES, Scenario
from .monitoring_details import Projection
from .paths import source_root

ROOT = source_root()
PACKAGES = {
    "power-grid": "iot_grid_simulator",
    "upstream": "iot_upstream_simulator",
    "midstream": "iot_midstream_simulator",
    "downstream": "iot_downstream_refinery_simulator",
    "downstream-portfolio": "iot_compact_portfolio_simulator",
}
CONFIGS = {
    "power-grid": ("power-grid", "nyc-five-boroughs-10-day.json"),
    "upstream": ("upstream", "fictional-onshore-upstream-small.json"),
    "midstream": ("midstream", "fictional-liquid-midstream-small.json"),
    "downstream": ("downstream", "downstream-smoke.json"),
    "downstream-portfolio": ("downstream", "fictional-compact-portfolio-smoke.json"),
}
NORMAL = {
    "power-grid": "normal", "upstream": "normal_production",
    "midstream": "normal_transfer", "downstream": "normal_run",
    "downstream-portfolio": "normal_portfolio",
}
# Workflow-only choices are deliberately not advertised as telemetry injections.
UPSTREAM_CHOICES = (
    "deliverability_decline", "liquid_loading", "slugging", "flowline_restriction",
    "artificial_lift_degradation", "esp_trip_restart", "compressor_degradation",
    "utility_power_loss", "communications_degradation", "instrument_drift_stuck",
)
PAGES = {
    "power-grid": ("/power-grid", "index.html"),
    "upstream": ("/upstream", "wellpad.html"),
    "midstream": ("/midstream", "refinery.html"),
    "downstream": ("/downstream", "downstream.html"),
    "downstream-portfolio": ("/downstream/portfolio", "portfolio.html"),
}


def _module(surface: str, module: str):
    if surface not in MONITORING_SURFACES:
        raise ValueError("Unknown monitoring surface")
    return import_module(f"{PACKAGES[surface]}.{module}")


def scenarios(surface: str) -> list[Scenario]:
    names = _module(surface, "config").SCENARIOS
    if surface == "upstream":
        names = UPSTREAM_CHOICES
    return [
        {"id": name, "label": name.replace("_", " ").title(),
         "description": f"Fictional {name.replace('_', ' ')} using the owning domain simulator."}
        for name in sorted(names) if name != NORMAL[surface]
    ]


def initial_model(surface: str, seed: int) -> dict:
    if surface not in MONITORING_SURFACES:
        raise ValueError("Unknown monitoring surface")
    if type(seed) is not int or not 0 <= seed <= 2**31 - 1:
        raise ValueError("Seed must be an integer between 0 and 2147483647")
    return {"surface": surface, "seed": seed, "scenario": NORMAL[surface], "cursor": 0}


def _validate_model(surface: str, model: dict) -> None:
    if not isinstance(model, dict):
        raise ValueError("Model must be a JSON object")
    initial_model(surface, model.get("seed"))
    if model.get("surface") != surface:
        raise ValueError("Model belongs to another surface")
    choices = {item["id"] for item in scenarios(surface)} | {NORMAL[surface]}
    if model.get("scenario") not in choices:
        raise ValueError("Unsupported scenario")
    if type(model.get("cursor")) is not int or model["cursor"] < 0:
        raise ValueError("Invalid model cursor")


def apply_action(surface, model, action, scenario=None) -> dict:
    _validate_model(surface, model)
    if action not in {"inject", "recover", "reset"}:
        raise ValueError("Unsupported monitoring action")
    if action == "inject":
        if not isinstance(scenario, str) or scenario not in {s["id"] for s in scenarios(surface)}:
            raise ValueError("Unsupported scenario")
    elif scenario is not None:
        raise ValueError("Recovery and reset do not accept a scenario")
    result = copy.deepcopy(model)
    result["scenario"] = scenario if action == "inject" else NORMAL[surface]
    result["cursor"] = 0
    return result


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Scheduled time must be UTC ISO text")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Scheduled time must be UTC ISO text") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("Scheduled time must have UTC offset")
    return parsed


class _MemoryPath:
    """Read-only file protocol for the existing checksum snapshot assemblers."""

    def __init__(self, files, name=""):
        self.files, self.name = files, name

    def __truediv__(self, other):
        suffix = str(other).replace("\\", "/")
        return _MemoryPath(self.files, "/".join(filter(None, (self.name, suffix))))

    def resolve(self):
        return self

    def relative_to(self, root):
        if self.files is not root.files or ".." in self.name.split("/"):
            raise ValueError("Outside in-memory snapshot")
        return self.name

    def is_file(self):
        return self.name in self.files

    def read_bytes(self):
        return self.files[self.name]

    def read_text(self, encoding="utf-8"):
        return self.read_bytes().decode(encoding)

    def open(self, mode="r", encoding="utf-8"):
        if mode != "r":
            raise ValueError("Snapshot view is read-only")
        return io.StringIO(self.read_text(encoding))

    def stat(self):
        return SimpleNamespace(st_size=len(self.read_bytes()))


def _manifest(config, families):
    files = {}
    entries = []
    for name, records in families.items():
        data = "".join(json.dumps(r, sort_keys=True, allow_nan=False) + "\n" for r in records).encode()
        path = f"{name}.jsonl"
        files[path] = data
        entries.append({"path": path, "record_type": name, "format": "jsonl",
                        "record_count": len(records), "sha256": hashlib.sha256(data).hexdigest()})
    manifest = {
        "run_id": "v2-monitoring", "scenario_id": config["scenario"],
        "schema_version": "1.0", "normalized_config": config,
        "config_digest_sha256": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
        "generator": {"name": "owning-domain-simulator-v2-view", "version": "1.0"},
        "files": entries, "record_counts": {k: len(v) for k, v in families.items()},
        "is_synthetic": True, "operational_use": "prohibited",
        "custody_transfer_use": "prohibited", "leak_model": "synthetic_physics_lite",
    }
    files["manifest.json"] = json.dumps(manifest, allow_nan=False).encode()
    return manifest, _MemoryPath(files)


@lru_cache(maxsize=6)
def _horizon(surface: str, seed: int, scenario: str):
    domain, filename = CONFIGS[surface]
    config = json.loads((ROOT / "domains" / domain / "configs" / filename).read_text(encoding="utf-8"))
    config.update(seed=seed, scenario=scenario)
    start = _parse(config["simulation"]["start_utc"])
    duration = 1200 if surface in {"power-grid", "upstream", "midstream"} else 120
    config["simulation"]["end_utc"] = _stamp(start + timedelta(seconds=duration))
    if surface == "power-grid":
        config["simulation"]["interval_minutes"] = 1
    elif surface in {"upstream", "midstream"}:
        config["simulation"]["interval_seconds"] = 30
    config = _module(surface, "config").validate_config(config)
    catalog = _module(surface, "catalog").build_catalog(config)
    engine = _module(surface, "simulation")
    if surface == "power-grid":
        assets, topology, context = catalog
        schedule, *_ = engine.build_schedule(config, "v2-monitoring", context)
        records = engine.simulate_telemetry(config, "v2-monitoring", assets, context, schedule)
        families = {"assets": assets, "topology": topology, "telemetry": records}
    elif surface == "midstream":
        assets, topology = catalog
        telemetry, _events, _logs, _truth = engine.simulate(config, "v2-monitoring", assets)
        families = {"assets": assets, "topology": topology, "telemetry": telemetry}
    elif surface == "upstream":
        result = engine.simulate(config, "v2-monitoring", catalog)
        families = {**catalog, "telemetry": result["telemetry"]}
        for name in ("production_summaries", "handoff_context", "condition_monitoring",
                     "environmental_observations"):
            families[name] = result.get(name, [])
    else:
        assets, devices, tags, topology = catalog
        if surface == "downstream-portfolio":
            records = []

            class MemoryWriter:
                def __init__(self, root, target_records):
                    pass

                def write(self, record):
                    records.append(record)

                def close(self):
                    return []

            # Bind only the writer dependency in a private globals mapping;
            # never monkeypatch the shared simulator module or write artifacts.
            pure = FunctionType(engine.simulate.__code__,
                                {**engine.simulate.__globals__, "JsonlShardWriter": MemoryWriter})
            result = pure(config, "v2-monitoring", assets, devices, tags, Path("."))
            telemetry = records
        else:
            result = engine.simulate(config, "v2-monitoring", assets, devices, tags)
            telemetry = result["telemetry"]
        families = {"assets": assets, "devices": devices, "tags": tags,
                    "topology": topology, "telemetry": telemetry}
        if surface == "downstream-portfolio":
            # Window-end summaries are not represented as current observations.
            families["product_summaries"] = []
    for name in ("events", "device_logs", "detector_outputs", "physical_confirmation_events"):
        families[name] = []
    times = sorted({r["event_time_utc"] for r in families["telemetry"]})
    return config, families, times


def _neutral(record, asset, surface, projection=None):
    validity = record.get("quality_class", record.get("quality", {}).get("validity", "unknown"))
    reasons = record.get("quality", {}).get("reasons", [])
    if "deenergized" in reasons:
        validity = "deenergized"
    payload = {key: record[key] for key in (
        "measurement", "value", "unit", "source_time_utc", "phase", "aggregation",
        "interval_seconds", "delivery_class", "delivery_state", "freshness_state", "origin_state",
        "clock_state", "clock_offset_ms", "clock_uncertainty_ms", "tag_id", "device_id",
        "measurement_uncertainty",
    ) if key in record}
    payload["quality"] = validity
    location = (asset.get("location_ref") or asset.get("pad_id") or asset.get("area_id")
                or asset.get("module_id") or asset.get("site_id") or f"fictional-{surface}")
    context = {key: asset[key] for key in (
        "asset_type", "display_name", "generic_model", "site_id", "area_id", "area_name",
        "pad_id", "well_id", "module_id", "route_id", "route_distance_m", "location_ref",
    ) if key in asset}
    context.update(is_synthetic=True, operational_use="prohibited", location_is_fictional=True)
    if projection is not None:
        context.update(projection.context(record, asset))
    return {"record_type": "telemetry", "entity_id": record["asset_id"],
            "location_id": str(location), "simulated_at": record["event_time_utc"],
            "payload": payload, "context": context}


def _live_view(surface, config, families, at, scenario):
    assets = {a["asset_id"]: a for a in families["assets"]}
    by_asset = defaultdict(lambda: defaultdict(list))
    for record in families["telemetry"]:
        by_asset[record["asset_id"]][record["measurement"]].append(record)
    views = []
    for asset_id, measurements in sorted(by_asset.items()):
        asset = assets[asset_id]
        rows = []
        for measurement, history in sorted(measurements.items()):
            record = history[-1]
            quality = record.get("quality_class", record.get("quality", {}).get("validity", "unknown"))
            deviation = record.get("deviation_ratio", 0)
            health = ("impaired" if record["value"] is None or abs(deviation) >= 0.08 else
                      "degraded" if quality != "good" or abs(deviation) >= 0.04 else "nominal")
            row = {**record, "validity": quality, "quality_class": quality,
                   "quality_reason": "", "reasons": [], "deviation_ratio": deviation,
                   "health": health,
                   "health_reason": quality, "sample_count": len(history),
                   "sparkline": [r["value"] for r in history[-30:]],
                   "history": [{k: r.get(k) for k in ("event_time_utc", "source_time_utc", "value")}
                               for r in history[-30:]]}
            rows.append(row)
        health = max((r["health"] for r in rows), key={"nominal": 0, "degraded": 1, "impaired": 2}.get)
        views.append({**asset, "health": health, "deviation_ratio": max(abs(r["deviation_ratio"]) for r in rows),
                      "measurement_count": len(rows), "measurements": rows})
    counts = Counter(a["health"] for a in views)
    return {
        "generated_at_utc": at, "site": config.get("site", {}),
        "facility": config.get("facility", {}), "scenario": {"id": scenario, "description": scenario.replace("_", " ")},
        "scenarios": scenarios(surface),
        "playback": {"looped": True, "horizon_seconds": 1200, "interval_seconds": 30,
                     "history_seconds": 900, "position_seconds": 0, "cycle": 0},
        "summary": {"asset_count": len(views), "measurement_count": sum(len(a["measurements"]) for a in views),
                    "samples_in_window": len(families["telemetry"]), "nominal": counts["nominal"],
                    "degraded": counts["degraded"], "impaired": counts["impaired"], "unknown": 0, "event_count": 0},
        "assets": views, "events": [], "is_synthetic": True, "operational_use": "prohibited",
    }


def _snapshot(surface, config, families, at, scenario):
    if surface == "power-grid":
        data = _module(surface, "dashboard.data")
        manifest, _ = _manifest(config, families)
        batch = data.LoadedBatch(data.BatchReference("v2", 0, at), manifest, families["assets"],
                                 families["telemetry"], [], [], [], [], [], None)
        # The assembler's wall-clock feed metadata is replaced immediately;
        # electrical charts and fictional map positions are reused unchanged.
        by_time = defaultdict(list)
        for record in families["telemetry"]:
            by_time[record["event_time_utc"]].append(record)
        history = []
        for moment, records in sorted(by_time.items()):
            historical_manifest = {**manifest, "normalized_config": {
                **config, "simulation": {**config["simulation"], "start_utc": moment}}}
            history.append(data.LoadedBatch(batch.reference, historical_manifest, families["assets"],
                                           records, [], [], [], [], [], None))
        snapshot = data.DashboardSnapshotService.__new__(data.DashboardSnapshotService)._assemble(batch, history, [])
        snapshot.update(generated_at_utc=at, feed={"state": "paused", "message": "V2 scheduled snapshot; use shared controls",
                                                "age_seconds": 0, "skipped_batches": []})
        snapshot["scenario"] = {"id": scenario}
        snapshot["ground_truth"] = []
        return snapshot
    live = _live_view(surface, config, families, at, scenario) if surface != "downstream-portfolio" else None
    if surface == "downstream":
        by_device = defaultdict(list)
        by_tag = defaultdict(list)
        for record in families["telemetry"]:
            by_tag[record["tag_id"]].append(record)
        for tag in families["tags"]:
            history = by_tag[tag["tag_id"]]
            if not history:
                continue
            r = history[-1]
            deviation = r.get("deviation_ratio", 0)
            health = ("impaired" if r["value"] is None or abs(deviation) >= 0.08 else
                      "degraded" if r["quality"]["validity"] != "good" or abs(deviation) >= 0.04 else "nominal")
            by_device[tag["device_id"]].append({
                **tag, **r, "health": health,
                "last_delivered_value": next((x["value"] for x in reversed(history) if x["value"] is not None), None),
                "previous_value": history[-2]["value"] if len(history) > 1 else None,
                "trend": "flat", "age_seconds": max(0, (_parse(r["event_time_utc"]) - _parse(r["source_time_utc"])).total_seconds()),
                "sample_count": len(history), "sparkline": [x["value"] for x in history[-30:]],
                "history": history[-30:], "window_min": None, "window_max": None,
            })
        assets = {a["asset_id"]: a for a in families["assets"]}
        devices = []
        for device in families["devices"]:
            tags = by_device[device["device_id"]]
            asset = assets[device["asset_id"]]
            devices.append({**device, "area_name": asset["area_name"], "asset_name": asset["display_name"],
                            "tags": tags, "tag_count": len(tags), "max_age_seconds": 0, "deviation_ratio": 0,
                            "health": max((t["health"] for t in tags), default="nominal",
                                          key={"nominal": 0, "degraded": 1, "impaired": 2}.get)})
        counts = Counter(d["health"] for d in devices)
        live.update(devices=devices, areas=[], tick_seconds=5, cycle_seconds=120, history_seconds=120)
        live["summary"].update(device_count=len(devices), tag_count=sum(len(d["tags"]) for d in devices),
                               nominal=counts["nominal"], degraded=counts["degraded"], impaired=counts["impaired"],
                               critical_event_count=0)
        return live
    service_name = {"upstream": "UpstreamSnapshotService", "midstream": "MidstreamSnapshotService",
                    "downstream-portfolio": "PortfolioSnapshotService"}[surface]
    _, root = _manifest(config, families)
    service = getattr(_module(surface, "dashboard.data"), service_name)(".")
    service.root = root
    snapshot = service.get_snapshot()
    if live is not None:
        snapshot["live"] = live
    snapshot["generated_at_utc"] = at
    snapshot["scenario"] = {"id": scenario}
    snapshot["provenance"]["view"] = "in_memory_simulation_not_published_artifact"
    return snapshot


def frame(surface, model, step: int, at: str) -> Frame:
    _validate_model(surface, model)
    _parse(at)
    if type(step) is not int or step < 0:
        raise ValueError("Step must be a nonnegative integer")
    config, source, times = _horizon(surface, model["seed"], model["scenario"])
    # Play a bounded post-onset window so injection is observable immediately,
    # not a normal prefix; baseline uses the same phase for comparison.
    first, stop = len(times) // 2, max(len(times) // 2 + 1, len(times) * 2 // 3)
    index = first + model["cursor"] % (stop - first)
    simulated = times[index]
    families = {name: [r for r in records if not r.get("event_time_utc") or r["event_time_utc"] <= simulated]
                for name, records in source.items()}
    assets = {a["asset_id"]: a for a in families["assets"]}
    latest = {}
    for r in families["telemetry"]:
        latest[(r["asset_id"], r["measurement"], r.get("tag_id"))] = r
    selected = list(latest.values())
    if surface == "downstream-portfolio":
        # One stable representative per module/measurement/product, not all
        # 7,140 repeated tags. The full catalog remains visible in the portal.
        representatives = {}
        for r in sorted(selected, key=lambda item: item["tag_id"]):
            a = assets[r["asset_id"]]
            key = (a["module_id"], r["measurement"], r.get("product_family"))
            representatives.setdefault(key, r)
        selected = list(representatives.values())
        families["product_summaries"] = [
            {"product_family": r["product_family"], "value": r["value"], "unit": r["unit"],
             "event_time_utc": r["event_time_utc"], "state": "observed_yield_share"}
            for r in selected if r["measurement"] == "yield_share" and r.get("product_family")
        ]
    projection = Projection(surface, families, config)
    observations = [_neutral(r, assets[r["asset_id"]], surface, projection) for r in selected]
    if surface in {"upstream", "midstream", "downstream"}:
        _, baseline, _ = _horizon(surface, model["seed"], NORMAL[surface])
        references = {(r["asset_id"], r["measurement"], r.get("tag_id"), r["event_time_utc"]): r["value"]
                      for r in baseline["telemetry"]}
        families["telemetry"] = [dict(r) for r in families["telemetry"]]
        for r in families["telemetry"]:
            nominal = references.get((r["asset_id"], r["measurement"], r.get("tag_id"), r["event_time_utc"]))
            r["nominal_value"] = nominal
            r["deviation_ratio"] = ((r["value"] - nominal) / max(abs(nominal), 1e-6)
                                    if r["value"] is not None and nominal is not None else 0)
    snapshot = _snapshot(surface, config, families, at, model["scenario"])
    cadence = 60 if surface == "power-grid" else 30 if surface in {"upstream", "midstream"} else 5
    replay_seconds = (stop - first) * cadence
    if "live" in snapshot:
        snapshot["live"]["playback"].update(
            horizon_seconds=replay_seconds, position_seconds=(index - first) * cadence,
            cycle=model["cursor"] // (stop - first), source_horizon_seconds=1200,
        )
    snapshot["v2_playback"] = {"simulated_at": simulated, "bounded_replay": True,
                               "position": index, "step": step, "scheduled_at": at,
                               "replay_window_seconds": replay_seconds}
    if surface == "downstream-portfolio":
        snapshot["v2_playback"]["observation_selection"] = "first_tag_per_module_measurement_product"
    result_model = copy.deepcopy(model)
    result_model["cursor"] += 1
    result = {"model": result_model, "snapshot": snapshot, "observations": observations}
    # Reject nonfinite output instead of silently converting it to unavailable.
    return json.loads(json.dumps(result, allow_nan=False))


def register_routes(app, runtime):
    from flask import abort, jsonify, send_file
    from .ui import render_portal

    def portal(surface):
        package = _module(surface, "dashboard")
        source = Path(package.__file__).parent / "static" / PAGES[surface][1]
        html = source.read_text(encoding="utf-8")
        # A separate V2 script disables obsolete controls without rewriting
        # source bundles. Source assets remain byte-for-byte untouched.
        html = html.replace("</head>", '<script defer src="/v2/monitoring-view.js"></script></head>')
        return render_portal(html, surface)

    def read(surface, live=False):
        snapshot = runtime.snapshot(surface)
        return jsonify(snapshot.get("live", snapshot) if live else snapshot)

    def detail(surface, entity):
        snapshot = runtime.snapshot(surface)
        live = snapshot.get("live", snapshot)
        key, id_key = ("devices", "device_id") if surface == "downstream" else ("assets", "asset_id")
        item = next((a for a in live[key] if a[id_key] == entity), None)
        if item is None:
            abort(404)
        return jsonify({**{k: live[k] for k in ("generated_at_utc", "site", "scenario", "events")},
                        "history_seconds": 120, "device" if surface == "downstream" else "asset": item})

    for surface in MONITORING_SURFACES:
        path = PAGES[surface][0]
        app.add_url_rule(path, f"v2_monitoring_page_{surface}", lambda s=surface: portal(s))
        app.add_url_rule(f"/api{path}", f"v2_monitoring_data_{surface}", lambda s=surface: read(s))
        if surface in {"upstream", "midstream", "downstream"}:
            app.add_url_rule(f"/api{path}/live", f"v2_monitoring_live_{surface}", lambda s=surface: read(s, True))
            segment = "devices" if surface == "downstream" else "assets"
            app.add_url_rule(f"/api{path}/live/{segment}/<entity>", f"v2_monitoring_detail_{surface}",
                             lambda entity, s=surface: detail(s, entity))
        # Read-only compatibility metadata. Never expose legacy POST handlers.
        app.add_url_rule(f"/api{path}/control/status", f"v2_monitoring_disabled_{surface}",
                         lambda: jsonify(enabled=False, record_types=[], job=None))
        app.add_url_rule(f"/api{path}/runs", f"v2_monitoring_runs_{surface}", lambda: jsonify(runs=[]))
        app.add_url_rule(f"/api{path}/scenarios", f"v2_monitoring_choices_{surface}",
                         lambda s=surface: jsonify(scenarios=scenarios(s)))
    app.add_url_rule("/v2/monitoring-view.js", "v2_monitoring_view_asset",
                     lambda: send_file(Path(__file__).parent / "static" / "monitoring-view.js", mimetype="text/javascript"))
