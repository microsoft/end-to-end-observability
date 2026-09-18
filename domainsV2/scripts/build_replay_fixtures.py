"""Compile and prove fixed-identity replay offline; never run on the web worker.

Run from the copied kit root after installing requirements.txt:
    python scripts\\build_replay_fixtures.py

The kit includes the reviewed stock inputs under support. Explicit
ENERGY_V2_*_INVENTORY_ROOT overrides are for deliberate re-authoring only.
Missing inputs fail; no fallback stock is generated.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))

from energy_domains_v2 import events, inventory, monitoring, replay
from energy_domains_v2 import inventory_details, monitoring_details, monitoring_selection
from energy_domains_v2.contracts import SURFACES, domain_for

PHASES = 3
# The refinery communications model has two fully delivered slots after its
# stale/store-forward slots. Author only the impaired slots, for ALL scenarios,
# so even a minimum-duration injection has observable delivery evidence.
SOURCE_POSITIONS = {"downstream": (0, 1, 0)}
AT = "2026-09-10T12:00:00Z"
OUTPUT = KIT / "energy_domains_v2" / "replay_data"


def signature(row):
    payload = row["payload"]
    return replay._encoded({key: payload.get(key) for key in (
        "value", "quantity", "quality", "origin_state", "delivery_state", "delivery_class",
    )})


def write_fixture(relative, document):
    decoded = replay._encoded(document)
    if len(decoded) > replay.MAX_FILE_BYTES:
        raise ValueError(f"Decoded fixture exceeds bound: {relative}")
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", filename="", compresslevel=9, mtime=0) as stream:
        stream.write(decoded)
    compressed = buffer.getvalue()
    path = OUTPUT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(compressed)
    return {"path": relative, "sha256": hashlib.sha256(compressed).hexdigest(),
            "compressed_bytes": len(compressed), "decoded_bytes": len(decoded)}


def selected_identities(surface, sequences, focus):
    common = None
    for frames in sequences.values():
        for frame in frames:
            keys = set(frame["rows"])
            common = keys if common is None else common & keys
    if not common:
        raise ValueError(f"{surface}: no stable observation identities")
    if surface.endswith("-inventory"):
        common = {key for key in common if all(
            type(frame["rows"][key]["payload"].get("quantity")) is int
            and frame["rows"][key]["payload"]["quantity"] > 0
            and frame["rows"][key]["payload"].get("quality") == "good"
            for frame in sequences["normal"])}
        if not common:
            raise ValueError(f"{surface}: no known positive opening positions")
    anomalies = set(sequences) - {"normal"}
    covers = {
        key: {(scenario, phase) for scenario in anomalies for phase in range(PHASES)
              if signature(sequences[scenario][phase]["rows"][key])
              != signature(sequences["normal"][phase]["rows"][key])}
        for key in common
    }
    selected = []
    if focus:
        for key in sorted(common):
            row = sequences["normal"][0]["rows"][key]
            if [row["entity_id"], row["location_id"]] == focus:
                selected.append(key)
                break
        if not selected:
            raise ValueError(f"{surface}: inventory focus is absent")
    remaining = {(scenario, phase) for scenario in anomalies for phase in range(PHASES)}
    remaining -= set().union(*(covers[k] for k in selected))
    while remaining:
        key = max(sorted(common), key=lambda item: (
            len(covers[item] & remaining),
            monitoring_selection.utility(
                surface, sequences["normal"][0]["rows"][item],
                [sequences["normal"][0]["rows"][k] for k in selected])
            if not surface.endswith("-inventory") else (),
        ))
        if not covers[key] & remaining:
            raise ValueError(f"{surface}: anomalies without changed values/quality: {sorted(remaining)}")
        selected.append(key)
        remaining -= covers[key]
    coverage_minimum = len(selected)
    # Retain unavailable-vs-zero evidence when the source cycle contains it.
    null_keys = sorted(k for k in common if any(
        frame["rows"][k]["payload"].get("value", frame["rows"][k]["payload"].get("quantity")) is None
        for frames in sequences.values() for frame in frames))
    if null_keys and not any(k in selected for k in null_keys):
        selected.append(null_keys[0])
    # Prefer additional affected, varying readings; never change identity sets
    # in response to the selected anomaly.
    if surface.endswith("-inventory"):
        ranked = sorted(common - set(selected), key=lambda key: (
            -len(covers[key]),
            -len({signature(frame["rows"][key]) for frame in sequences["normal"]}),
            key,
        ))
        selected.extend(ranked[:max(0, 10 - len(selected))])
    else:
        # Preserve physical outage zero separately from unavailable null.
        zeros = sorted(k for k in common if any(
            frame["rows"][k]["payload"].get("value") == 0
            and frame["rows"][k]["payload"].get("quality") == "deenergized"
            for frames in sequences.values() for frame in frames))
        if zeros and not set(zeros).intersection(selected):
            selected.append(zeros[0])
        varying = sorted(k for k in common if len({
            signature(frame["rows"][k]) for frame in sequences["normal"]}) > 1)
        if varying and not set(varying).intersection(selected):
            selected.append(varying[0])
        while len(selected) < min(10, len(common)):
            chosen_rows = [sequences["normal"][0]["rows"][k] for k in selected]
            key = max(sorted(common - set(selected)), key=lambda item: (
                monitoring_selection.utility(surface, sequences["normal"][0]["rows"][item], chosen_rows),
                len({signature(frame["rows"][item]) for frame in sequences["normal"]}),
                len(covers[item]),
            ))
            selected.append(key)
    if not 1 <= len(selected) <= 10:
        raise ValueError(f"{surface}: anomaly coverage needs more than ten identities")
    selected.sort()
    proof = {scenario: [[key for key in selected if (scenario, phase) in covers[key]]
                        for phase in range(PHASES)] for scenario in sorted(anomalies)}
    if any(not keys for phases in proof.values() for keys in phases):
        raise ValueError(f"{surface}: incomplete advertised anomaly coverage")
    return selected, proof, coverage_minimum


@contextmanager
def downstream_source():
    """Require the reviewed source, without copying or modifying its files."""
    if not inventory.dataset_root("downstream-inventory").is_dir():
        raise ValueError("Set ENERGY_V2_DOWNSTREAM_INVENTORY_ROOT to the reviewed inventory dataset")
    yield


def build():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    manifest = {"format_version": 1, "seed": 42, "phase_count": PHASES,
                "normal_id": "normal", "surfaces": {}, "provenance": {},
                "baseline_definition": replay.BASELINE_DEFINITION,
                "builder_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    for surface in SURFACES:
        adapter = inventory if surface.endswith("-inventory") else monitoring
        opening = adapter.initial_model(surface, 42)
        choices = adapter.scenarios(surface)
        sequences = {}
        snapshots = {}
        for scenario in ["normal", *(item["id"] for item in choices)]:
            model = opening if scenario == "normal" else adapter.apply_action(surface, opening, "inject", scenario)
            frames = []
            entries = []
            for phase in range(PHASES):
                if not surface.endswith("-inventory"):
                    model = {**model, "cursor": SOURCE_POSITIONS.get(surface, (0, 1, 2))[phase]}
                frame = adapter.frame(surface, model, phase, AT)
                model = frame["model"]
                anchor = max(row["simulated_at"] for row in frame["observations"])
                snapshot = frame["snapshot"]
                # Generated/scheduled clocks belong to the replay frame, not
                # the fixed authoring wall-clock supplied to legacy adapters.
                def normalize(value):
                    if isinstance(value, dict):
                        return {key: anchor if key in {"generated_at_utc", "published_at_utc", "scheduled_at"}
                                and isinstance(item, str) else normalize(item) for key, item in value.items()}
                    if isinstance(value, list):
                        return [normalize(item) for item in value]
                    return value
                snapshot = normalize(snapshot)
                if surface.endswith("-inventory"):
                    snapshot["inventory"]["stock_note"] += " " + replay.BASELINE_DEFINITION
                entries.append(write_fixture(
                    f"snapshots/{surface}/{scenario}-{phase}.json.gz",
                    {"anchor": anchor, "snapshot": snapshot}))
                rows = {replay._identity(row): row for row in frame["observations"]}
                if len(rows) != len(frame["observations"]):
                    raise ValueError(f"{surface}: ambiguous observation identities")
                frames.append({"anchor": anchor, "rows": rows})
            sequences[scenario] = frames
            snapshots[scenario] = entries
        identities, proof, minimum = selected_identities(surface, sequences, opening.get("focus"))
        if not surface.endswith("-inventory"):
            contexts = {key: sequences["normal"][0]["rows"][key]["context"] for key in identities}
            for scenario, frames in sequences.items():
                for phase, source_frame in enumerate(frames):
                    if any(source_frame["rows"][key]["context"] != contexts[key] for key in identities):
                        raise ValueError(f"{surface}: nonstatic details in {scenario} phase {phase}")
        data = {"sequences": {}}
        for scenario, frames in sequences.items():
            selected_frames = []
            for phase, frame in enumerate(frames):
                rows = [deepcopy(frame["rows"][key]) for key in identities]
                events.observations({"observations": rows},
                                    {"run_id": "fixture-validation", "surface": surface, "sequence": phase},
                                    domain_for(surface), frame["anchor"])
                selected_frames.append({"anchor": frame["anchor"], "observations": rows})
            data["sequences"][scenario] = selected_frames
        varying = [key for key in identities if len({
            signature(frame["rows"][key]) for frame in sequences["normal"]}) > 1]
        if not surface.endswith("-inventory") and not varying:
            raise ValueError(f"{surface}: normal cycle has no changing selected reading")
        observation_entry = write_fixture(f"observations/{surface}.json.gz", data)
        manifest["surfaces"][surface] = {
            "phase_count": PHASES, "scenarios": choices, "identities": identities,
            "observations": observation_entry, "snapshots": snapshots,
            "coverage": proof, "coverage_minimum": minimum,
            "normal_varying_identities": varying, "inventory_focus": opening.get("focus"),
            "source_positions": list(SOURCE_POSITIONS.get(surface, (0, 1, 2))),
            "selection_policy": ("positive_authored_openings_focus_required" if surface.endswith("-inventory")
                                 else "fixed_cross_scenario_phase_coverage"),
        }
        if not surface.endswith("-inventory"):
            baseline_rows = sequences["normal"][0]["rows"]
            manifest["surfaces"][surface]["selection_details"] = monitoring_selection.report(
                surface, [baseline_rows[key] for key in identities], list(baseline_rows.values()))
            manifest["surfaces"][surface]["selection_policy"] = (
                "fixed_coverage_then_source_backed_companions_v1")
        sources = [Path(adapter.__file__)]
        if not surface.endswith("-inventory"):
            sources.extend([Path(monitoring_details.__file__), Path(monitoring_selection.__file__)])
            source_domain, source_config = monitoring.CONFIGS[surface]
            sources.append(monitoring.ROOT / "domains" / source_domain / "configs" / source_config)
            for module in ("catalog", "simulation", "config"):
                sources.append(Path(monitoring._module(surface, module).__file__))
        else:
            sources.append(Path(inventory_details.__file__))
            sources.extend(sorted(inventory.dataset_root(surface).glob("*.jsonl")))
        manifest["provenance"][surface] = [
            {"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in sources
        ]
        print(f"{surface}: {len(identities)} stable identities; {len(choices)} anomalies covered; {minimum} coverage targets")
    entries = [spec["observations"] for spec in manifest["surfaces"].values()]
    entries.extend(entry for spec in manifest["surfaces"].values()
                   for frames in spec["snapshots"].values() for entry in frames)
    total = sum(entry["compressed_bytes"] for entry in entries)
    if total > replay.MAX_PACKAGE_BYTES:
        raise ValueError(f"Fixture package exceeds {replay.MAX_PACKAGE_BYTES} bytes")
    manifest["total_compressed_bytes"] = total
    manifest["fixture_version"] = hashlib.sha256(replay._encoded(manifest)).hexdigest()
    (OUTPUT / "manifest.json").write_bytes(replay._encoded(manifest) + b"\n")
    print(f"Fixture version {manifest['fixture_version']}; compressed bytes {total}")


if __name__ == "__main__":
    with downstream_source():
        build()
