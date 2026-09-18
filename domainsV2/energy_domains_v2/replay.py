"""Bounded authored replay: hosted ticks never import or run domain simulators.

Normal means the authored baseline, not universal health or threshold compliance.
Stock quantities below ten remain valid normal opening balances.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
from typing import Any

from .contracts import SURFACES, Observation, Scenario


FIXTURE_ROOT = Path(__file__).parent / "replay_data"
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_PACKAGE_BYTES = 24 * 1024 * 1024
AUTHORED_SEED = 42
BASELINE_DEFINITION = (
    "Normal means the authored baseline, not universal health or threshold compliance. "
    "Inventory quantities are unchanged source opening balances; values below ten can be normal."
)
TIMESTAMP_FIELDS = frozenset({
    "at", "event_time_utc", "source_time_utc", "ingest_time_utc",
    "generated_at_utc", "simulated_at", "simulated_time_utc", "published_at_utc",
    "scheduled_at", "start_utc", "end_utc", "window_start_utc", "window_end_utc",
    "period_start_utc", "period_end_utc", "selected_at_utc", "onset_utc",
})


def _encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _utc(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Scheduled time must be UTC ISO text")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Scheduled time must be UTC ISO text") from error
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        raise ValueError("Scheduled time must use UTC")
    return result


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _identity(row: dict) -> str:
    payload = row["payload"]
    return _encoded([row["record_type"], row["entity_id"], row["location_id"],
                     payload.get("measurement"), payload.get("tag_id"),
                     payload.get("phase")]).decode()


def _shift(value: Any, delta: timedelta) -> Any:
    if isinstance(value, list):
        return [_shift(item, delta) for item in value]
    if isinstance(value, dict):
        return {
            key: _stamp(_utc(item) + delta)
            if key in TIMESTAMP_FIELDS and isinstance(item, str) else _shift(item, delta)
            for key, item in value.items()
        }
    return value


@lru_cache(maxsize=1)
def _manifest() -> dict:
    path = FIXTURE_ROOT / "manifest.json"
    if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Packaged replay manifest is missing or exceeds its bound")
    document = json.loads(path.read_bytes())
    version = document.get("fixture_version")
    material = {k: v for k, v in document.items() if k != "fixture_version"}
    if version != hashlib.sha256(_encoded(material)).hexdigest():
        raise ValueError("Replay manifest checksum mismatch")
    if (document.get("format_version") != 1 or document.get("seed") != AUTHORED_SEED
            or set(document.get("surfaces", {})) != set(SURFACES)):
        raise ValueError("Unsupported or incomplete replay manifest")
    entries = []
    for surface in SURFACES:
        spec = document["surfaces"][surface]
        count = len(spec["identities"])
        if not 1 <= count <= 10 or len(set(spec["identities"])) != count:
            raise ValueError("Replay identities are not bounded and unique")
        if type(spec["phase_count"]) is not int or not 1 <= spec["phase_count"] <= 12:
            raise ValueError("Replay cycle is not bounded")
        if set(spec["snapshots"]) != {"normal", *(s["id"] for s in spec["scenarios"])}:
            raise ValueError("Replay scenario inventory differs")
        entries.append(spec["observations"])
        for frames in spec["snapshots"].values():
            if len(frames) != spec["phase_count"]:
                raise ValueError("Replay snapshot cycle differs")
            entries.extend(frames)
    if sum(entry["compressed_bytes"] for entry in entries) > MAX_PACKAGE_BYTES:
        raise ValueError("Replay package exceeds its size bound")
    return document


def _spec(surface: str) -> dict:
    if surface not in SURFACES:
        raise ValueError("Unknown replay surface")
    return _manifest()["surfaces"][surface]


def __getattr__(name: str):
    # Lazy module export allows the offline builder to import helpers before
    # the first manifest exists. Hosted VERSION access always verifies it.
    if name == "VERSION":
        return _manifest()["fixture_version"]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _load(entry: dict) -> dict:
    relative = PurePosixPath(entry["path"])
    if (relative.is_absolute() or ".." in relative.parts or "\\" in entry["path"]
            or ":" in entry["path"]):
        raise ValueError("Unsafe replay fixture path")
    path = FIXTURE_ROOT / Path(*relative.parts)
    if not path.is_file() or path.stat().st_size != entry["compressed_bytes"]:
        raise ValueError("Replay fixture is missing or has the wrong size")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Replay fixture exceeds compressed size bound")
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != entry["sha256"]:
        raise ValueError("Replay fixture checksum mismatch")
    with gzip.GzipFile(fileobj=io.BytesIO(payload), mode="rb") as stream:
        decoded = stream.read(MAX_FILE_BYTES + 1)
    if len(decoded) > MAX_FILE_BYTES or len(decoded) != entry["decoded_bytes"]:
        raise ValueError("Replay fixture exceeds decoded size bound")
    return json.loads(decoded)


@lru_cache(maxsize=9)
def _observation_fixture(surface: str) -> dict:
    spec = _spec(surface)
    fixture = _load(spec["observations"])
    if set(fixture["sequences"]) != set(spec["snapshots"]):
        raise ValueError("Replay observation scenarios differ")
    for frames in fixture["sequences"].values():
        if len(frames) != spec["phase_count"]:
            raise ValueError("Replay observation cycle differs")
        for frame in frames:
            if [_identity(row) for row in frame["observations"]] != spec["identities"]:
                raise ValueError("Replay observation identities differ")
    return fixture


@lru_cache(maxsize=2)
def _snapshot_fixture(surface: str, scenario: str, phase: int) -> dict:
    return _load(_spec(surface)["snapshots"][scenario][phase])


def scenarios(surface: str) -> list[Scenario]:
    return deepcopy(_spec(surface)["scenarios"])


def initial_model(surface: str, seed: int = AUTHORED_SEED) -> dict:
    _spec(surface)
    if type(seed) is not int or seed != AUTHORED_SEED:
        raise ValueError("Packaged replay uses authored seed 42 only")
    return {"surface": surface, "seed": AUTHORED_SEED,
            "fixture_version": _manifest()["fixture_version"], "scenario": "normal",
            "last_queued": None}


def _validate(surface: str, model: dict) -> dict:
    spec = _spec(surface)
    if (not isinstance(model, dict) or model.get("surface") != surface
            or set(model) != {"surface", "seed", "fixture_version", "scenario", "last_queued"}
            or type(model.get("seed")) is not int or model["seed"] != AUTHORED_SEED
            or model.get("fixture_version") != _manifest()["fixture_version"]):
        raise ValueError("Replay model surface, seed or fixture version differs")
    if model.get("scenario") not in spec["snapshots"]:
        raise ValueError("Unknown replay scenario")
    queued = model.get("last_queued")
    if queued is not None:
        if (not isinstance(queued, dict) or queued.get("scenario") not in spec["snapshots"]
                or type(queued.get("phase")) is not int
                or not 0 <= queued["phase"] < spec["phase_count"]):
            raise ValueError("Invalid last queued replay position")
        _utc(queued.get("at"))
    return spec


def apply_action(surface: str, model: dict, action: str, scenario: str | None = None) -> dict:
    spec = _validate(surface, model)
    if action == "inject":
        if not isinstance(scenario, str) or scenario not in {s["id"] for s in spec["scenarios"]}:
            raise ValueError("Unsupported replay anomaly")
        selected = scenario
    elif action in {"recover", "reset"} and scenario is None:
        selected = "normal"
    else:
        raise ValueError("Unsupported replay action")
    result = deepcopy(model)
    result["scenario"] = selected
    # A queued intervention changes only the NEXT sample. GET continues to
    # show last_queued until core durably commits sample and outbox together.
    return result


def sample(surface: str, model: dict, step: int, at: str) -> dict:
    spec = _validate(surface, model)
    if type(step) is not int or step < 0:
        raise ValueError("Replay step must be a nonnegative integer")
    moment = _utc(at)
    phase = step % spec["phase_count"]
    authored = _observation_fixture(surface)["sequences"][model["scenario"]][phase]
    observations: list[Observation] = _shift(authored["observations"], moment - _utc(authored["anchor"]))
    result = deepcopy(model)
    result["last_queued"] = {"scenario": model["scenario"], "phase": phase, "at": _stamp(moment)}
    return {"model": result, "observations": observations}


def observation_view(surface: str, model: dict) -> dict:
    """Reconstruct selected observations without advancing or publishing replay."""
    _validate(surface, model)
    queued = model["last_queued"]
    scenario, phase = (queued["scenario"], queued["phase"]) if queued else ("normal", 0)
    authored = _observation_fixture(surface)["sequences"][scenario][phase]
    at = queued["at"] if queued else authored["anchor"]
    selected = deepcopy(model)
    selected["scenario"] = scenario
    return sample(surface, selected, phase, at)


def snapshot(surface: str, model: dict) -> dict:
    _validate(surface, model)
    queued = model["last_queued"]
    scenario, phase = (queued["scenario"], queued["phase"]) if queued else ("normal", 0)
    authored = _snapshot_fixture(surface, scenario, phase)
    at = queued["at"] if queued else authored["anchor"]
    result = _shift(authored["snapshot"], _utc(at) - _utc(authored["anchor"]))
    result["replay"] = {
        "fixture_version": model["fixture_version"], "authored_seed": AUTHORED_SEED,
        "scenario": scenario, "phase": phase, "bounded_replay": True,
        "last_queued_at": queued["at"] if queued else None,
        "view_state": "last_queued" if queued else "preview_not_queued",
        "delivery_claim": "queued_is_not_accepted_or_queryable",
        "baseline_definition": BASELINE_DEFINITION,
    }
    return result
