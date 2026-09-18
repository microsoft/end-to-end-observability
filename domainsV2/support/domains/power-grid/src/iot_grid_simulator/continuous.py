"""Continuous deterministic micro-batch runtime and durable control inbox."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import sqlite3
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .adls import AdlsError, AdlsSink
from .catalog import area_location_ref
from .config import ConfigError, load_config, validate_config
from .incident import (
    IncidentError,
    incident_status_at,
    resolve_incident_template,
    validate_incident_template,
)
from .io import canonical_json, sha256_file
from .model_state import MODEL_STATE_SCHEMA_VERSION, ModelStateError
from .monitor import (
    CommandSource,
    CommandSourceError,
    MonitorError,
    MonitorPublisher,
    MonitorPublisherError,
    ReceivedQueueMessage,
)
from .simulator import generate_dataset

SinkFactory = Callable[[int, str], AdlsSink | None]


class ContinuousError(RuntimeError):
    """Raised when the continuous runtime cannot proceed safely."""


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(value: str) -> datetime:
    if not value.endswith("Z"):
        raise ContinuousError("continuous timestamps must end with Z")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ContinuousError(f"invalid UTC timestamp: {value}") from exc


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    payload = (canonical_json(value) + "\n").encode("utf-8")
    with temporary.open("wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _connect(runtime_directory: Path) -> sqlite3.Connection:
    runtime_directory.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        runtime_directory / "runtime.sqlite3",
        timeout=30,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS runtime_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS commands (
            command_id TEXT PRIMARY KEY,
            command_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at_utc TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'applied')),
            applied_batch_sequence INTEGER
        );
        CREATE TABLE IF NOT EXISTS batches (
            batch_sequence INTEGER PRIMARY KEY,
            simulated_start_utc TEXT NOT NULL,
            simulated_end_utc TEXT NOT NULL,
            run_id TEXT NOT NULL,
            local_path TEXT NOT NULL,
            manifest_sha256 TEXT NOT NULL,
            storm_revision INTEGER,
            status TEXT NOT NULL CHECK (status = 'published')
        );
        CREATE TABLE IF NOT EXISTS monitor_outbox (
            event_id TEXT PRIMARY KEY,
            command_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'sent')),
            created_at_utc TEXT NOT NULL,
            sent_at_utc TEXT
        );
        """
    )
    return connection


def _meta(connection: sqlite3.Connection) -> dict[str, str]:
    return {
        row["key"]: row["value"]
        for row in connection.execute("SELECT key, value FROM runtime_meta")
    }


def _set_meta(connection: sqlite3.Connection, key: str, value: object) -> None:
    connection.execute(
        """
        INSERT INTO runtime_meta(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """,
        (key, str(value)),
    )


def _rounded_now(interval_minutes: int) -> datetime:
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    minute = now.minute - now.minute % interval_minutes
    return now.replace(minute=minute)


def _prepare_runtime(
    config_path: Path,
    runtime_directory: Path,
    simulation_interval_minutes: int,
    start_utc: str | None,
) -> tuple[dict[str, Any], dict[str, str]]:
    if not 1 <= simulation_interval_minutes <= 60:
        raise ContinuousError(
            "simulation interval minutes must be between 1 and 60"
        )
    base_config = load_config(config_path)
    if base_config["scenario"] != "normal":
        raise ContinuousError(
            "continuous mode requires a base config with scenario normal"
        )
    base_config = copy.deepcopy(base_config)
    configured_faults = base_config.get("environment", {}).get("sensor_faults")
    if configured_faults:
        base_config["environment"] = {"sensor_faults": configured_faults}
    else:
        base_config.pop("environment", None)
    base_text = canonical_json(base_config)
    base_digest = hashlib.sha256(base_text.encode("utf-8")).hexdigest()

    connection = _connect(runtime_directory)
    try:
        meta = _meta(connection)
        if not meta:
            start = (
                _parse_utc(start_utc)
                if start_utc is not None
                else _rounded_now(simulation_interval_minutes)
            )
            runtime_material = (
                f"{base_digest}|{_timestamp(start)}|"
                f"{simulation_interval_minutes}"
            ).encode("utf-8")
            runtime_id = (
                "continuous-"
                + hashlib.sha256(runtime_material).hexdigest()[:16]
            )
            with connection:
                _set_meta(connection, "base_config_sha256", base_digest)
                _set_meta(connection, "runtime_id", runtime_id)
                _set_meta(connection, "next_simulated_time_utc", _timestamp(start))
                _set_meta(connection, "next_batch_sequence", 1)
                _set_meta(
                    connection,
                    "simulation_interval_minutes",
                    simulation_interval_minutes,
                )
                _set_meta(connection, "active_storm_json", "null")
                _set_meta(connection, "active_incident_json", "null")
                _set_meta(connection, "model_state_json", "null")
                _set_meta(connection, "status", "initialized")
            _atomic_json(runtime_directory / "base-config.json", base_config)
            meta = _meta(connection)
        else:
            if meta.get("base_config_sha256") != base_digest:
                raise ContinuousError(
                    "runtime directory belongs to a different base configuration"
                )
            if int(meta["simulation_interval_minutes"]) != simulation_interval_minutes:
                raise ContinuousError(
                    "simulation interval cannot change for an existing runtime"
                )
            persisted = json.loads(
                (runtime_directory / "base-config.json").read_text(
                    encoding="utf-8"
                )
            )
            if canonical_json(persisted) != base_text:
                raise ContinuousError("persisted base configuration is inconsistent")
        return base_config, meta
    finally:
        connection.close()


def _acquire_lock(runtime_directory: Path, recover: bool) -> Path:
    lock_path = runtime_directory / "runtime.lock"
    if lock_path.exists():
        if not recover:
            raise ContinuousError(
                "continuous runtime is locked; use --recover only after "
                "confirming no runner is active"
            )
        lock_path.unlink()
    try:
        descriptor = os.open(
            lock_path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        )
    except FileExistsError as exc:
        raise ContinuousError("another continuous runner holds the lock") from exc
    with os.fdopen(descriptor, "w", encoding="ascii") as stream:
        stream.write(str(os.getpid()))
        stream.flush()
        os.fsync(stream.fileno())
    return lock_path


def _validate_storm_template(
    template: dict[str, Any],
    configured_areas: set[str],
) -> dict[str, Any]:
    expected = {
        "storm_id",
        "revision",
        "status",
        "is_synthetic",
        "public_safety",
        "route",
    }
    if set(template) != expected:
        raise ContinuousError(
            "storm template contains unknown or missing keys"
        )
    if (
        not isinstance(template["storm_id"], str)
        or not template["storm_id"].startswith("synthetic-")
    ):
        raise ContinuousError("storm_id must start with synthetic-")
    if (
        not isinstance(template["revision"], int)
        or isinstance(template["revision"], bool)
        or template["revision"] < 1
    ):
        raise ContinuousError("storm revision must be a positive integer")
    if template["status"] not in {"Test", "Exercise"}:
        raise ContinuousError("storm status must be Test or Exercise")
    if template["is_synthetic"] is not True:
        raise ContinuousError("storm is_synthetic must be true")
    if template["public_safety"] is not False:
        raise ContinuousError("storm public_safety must be false")
    route = template["route"]
    if not isinstance(route, list) or not route or len(route) > 100:
        raise ContinuousError(
            "storm template route must contain between 1 and 100 points"
        )
    normalized = copy.deepcopy(template)
    for index, point in enumerate(normalized["route"], start=1):
        fields = {
            "sequence",
            "area",
            "arrival_offset_minutes",
            "duration_minutes",
            "wind_speed_mps",
            "precipitation_mm",
            "flood_state",
            "certainty",
        }
        if not isinstance(point, dict) or set(point) != fields:
            raise ContinuousError(
                f"storm template route point {index} has invalid fields"
            )
        if point["sequence"] != index:
            raise ContinuousError(
                "storm template route sequence must start at 1 and be contiguous"
            )
        if point["area"] not in configured_areas:
            raise ContinuousError(
                f"storm template area is not configured: {point['area']}"
            )
        for field, low, high in (
            ("arrival_offset_minutes", 0, 31 * 24 * 60),
            ("duration_minutes", 1, 7 * 24 * 60),
            ("wind_speed_mps", 0, 75),
            ("precipitation_mm", 0, 100),
        ):
            value = point[field]
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not low <= float(value) <= high
            ):
                raise ContinuousError(
                    f"storm template {field} must be between {low} and {high}"
                )
        if point["flood_state"] not in {"none", "watch", "warning"}:
            raise ContinuousError(
                "storm template flood_state must be none, watch, or warning"
            )
        if point["certainty"] not in {"Possible", "Likely"}:
            raise ContinuousError(
                "storm template certainty must be Possible or Likely"
            )
    return normalized


def _enqueue(
    runtime_directory: Path,
    command_type: str,
    payload: dict[str, Any],
) -> str:
    if not (runtime_directory / "runtime.sqlite3").is_file():
        raise ContinuousError(
            "runtime is not initialized; start the continuous runner first"
        )
    command_id = f"command-{uuid.uuid4().hex}"
    connection = _connect(runtime_directory)
    try:
        with connection:
            connection.execute(
                """
                INSERT INTO commands(
                    command_id, command_type, payload_json,
                    created_at_utc, status
                ) VALUES (?, ?, ?, ?, 'pending')
                """,
                (
                    command_id,
                    command_type,
                    canonical_json(payload),
                    _timestamp(datetime.now(timezone.utc)),
                ),
            )
    finally:
        connection.close()
    return command_id


def enqueue_storm(runtime_directory: str | Path, template_path: str | Path) -> str:
    """Validate and enqueue a synthetic storm-route update."""
    runtime = Path(runtime_directory)
    base_config = json.loads(
        (runtime / "base-config.json").read_text(encoding="utf-8")
    )
    areas = set(base_config["location"].get("areas", []))
    if not areas:
        raise ContinuousError(
            "storm injection requires configured location areas"
        )
    try:
        template = json.loads(Path(template_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContinuousError(f"unable to load storm template: {exc}") from exc
    normalized = _validate_storm_template(template, areas)
    return _enqueue(runtime, "storm_upsert", normalized)


def enqueue_incident(
    runtime_directory: str | Path,
    template_path: str | Path,
) -> str:
    """Validate and enqueue a synthetic operational incident."""
    runtime = Path(runtime_directory)
    base_config = json.loads(
        (runtime / "base-config.json").read_text(encoding="utf-8")
    )
    areas = set(base_config["location"].get("areas", []))
    if not areas:
        raise ContinuousError(
            "incident injection requires configured location areas"
        )
    try:
        template = json.loads(Path(template_path).read_text(encoding="utf-8"))
        normalized = validate_incident_template(template, areas)
    except (OSError, json.JSONDecodeError, IncidentError) as exc:
        raise ContinuousError(f"unable to load incident template: {exc}") from exc
    return _enqueue(runtime, "incident_upsert", normalized)


def enqueue_stop(runtime_directory: str | Path) -> str:
    """Request a graceful stop between micro-batches."""
    return _enqueue(Path(runtime_directory), "stop", {})


def enqueue_clear_storm(runtime_directory: str | Path) -> str:
    """Clear the active synthetic storm at the next tick boundary."""
    return _enqueue(Path(runtime_directory), "storm_clear", {})


def enqueue_clear_incident(runtime_directory: str | Path) -> str:
    """Clear the active synthetic incident at the next tick boundary."""
    return _enqueue(Path(runtime_directory), "incident_clear", {})


def runtime_status(runtime_directory: str | Path) -> dict[str, Any]:
    """Return runtime/checkpoint state without changing it."""
    runtime = Path(runtime_directory)
    connection = _connect(runtime)
    try:
        meta = _meta(connection)
        pending = connection.execute(
            "SELECT COUNT(*) AS count FROM commands WHERE status='pending'"
        ).fetchone()["count"]
        published = connection.execute(
            "SELECT COUNT(*) AS count FROM batches"
        ).fetchone()["count"]
        return {
            **meta,
            "pending_commands": pending,
            "published_batches": published,
            "locked": (runtime / "runtime.lock").exists(),
        }
    finally:
        connection.close()


def _resolve_storm(
    template: dict[str, Any],
    simulated_start: datetime,
) -> dict[str, Any]:
    route = []
    for point in template["route"]:
        arrival = simulated_start + timedelta(
            minutes=float(point["arrival_offset_minutes"])
        )
        departure = arrival + timedelta(
            minutes=float(point["duration_minutes"])
        )
        route.append(
            {
                "sequence": point["sequence"],
                "area": point["area"],
                "arrival_utc": _timestamp(arrival),
                "departure_utc": _timestamp(departure),
                "wind_speed_mps": float(point["wind_speed_mps"]),
                "precipitation_mm": float(point["precipitation_mm"]),
                "flood_state": point["flood_state"],
                "certainty": point["certainty"],
            }
        )
    return {
        "storm_id": template["storm_id"],
        "revision": template["revision"],
        "issued_at_utc": _timestamp(simulated_start),
        "status": template["status"],
        "is_synthetic": True,
        "public_safety": False,
        "route": route,
    }


def _pending_commands(
    connection: sqlite3.Connection,
) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT command_id, command_type, payload_json
            FROM commands
            WHERE status='pending'
            ORDER BY rowid
            """
        )
    )


def _validate_queue_command(
    message: ReceivedQueueMessage,
    configured_areas: set[str],
) -> tuple[str, str, dict[str, Any], str]:
    if not isinstance(message.body, str):
        raise ContinuousError("queue command body must be text")
    try:
        envelope = json.loads(message.body)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ContinuousError(
            f"queue command must contain valid JSON: {exc}"
        ) from exc
    expected = {
        "schema_version",
        "command_id",
        "command_type",
        "requested_at_utc",
        "payload",
    }
    if not isinstance(envelope, dict) or set(envelope) != expected:
        raise ContinuousError(
            "queue command contains unknown or missing keys"
        )
    if envelope["schema_version"] != "1.0":
        raise ContinuousError("queue command schema_version must be 1.0")
    command_id = envelope["command_id"]
    if not isinstance(command_id, str) or not command_id.strip():
        raise ContinuousError("queue command_id must be a non-empty string")
    command_type = envelope["command_type"]
    if command_type not in {"storm_upsert", "storm_clear"}:
        raise ContinuousError(
            "queue command_type must be storm_upsert or storm_clear"
        )
    requested_at = envelope["requested_at_utc"]
    if not isinstance(requested_at, str):
        raise ContinuousError("queue requested_at_utc must be a UTC timestamp")
    _parse_utc(requested_at)
    if not isinstance(envelope["payload"], dict):
        raise ContinuousError("queue command payload must be an object")
    if command_type == "storm_upsert":
        payload = _validate_storm_template(
            envelope["payload"],
            configured_areas,
        )
    else:
        payload = envelope["payload"]
        if payload:
            raise ContinuousError("storm_clear payload must be empty")
    return command_id, command_type, payload, requested_at


def _persist_queue_commands(
    connection: sqlite3.Connection,
    command_source: CommandSource,
    configured_areas: set[str],
) -> None:
    try:
        messages = command_source.receive()
    except CommandSourceError as exc:
        raise ContinuousError(str(exc)) from exc
    except Exception as exc:
        raise ContinuousError(
            f"unable to receive queue commands: {exc}"
        ) from exc
    for message in messages:
        command_id, command_type, payload, requested_at = _validate_queue_command(
            message,
            configured_areas,
        )
        payload_json = canonical_json(payload)
        with connection:
            existing = connection.execute(
                """
                SELECT command_type, payload_json
                FROM commands
                WHERE command_id=?
                """,
                (command_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO commands(
                        command_id, command_type, payload_json,
                        created_at_utc, status
                    ) VALUES (?, ?, ?, ?, 'pending')
                    """,
                    (command_id, command_type, payload_json, requested_at),
                )
            elif (
                existing["command_type"] != command_type
                or existing["payload_json"] != payload_json
            ):
                raise ContinuousError(
                    "queue command_id conflicts with an existing command: "
                    f"{command_id}"
                )
        try:
            command_source.delete(message)
        except CommandSourceError as exc:
            raise ContinuousError(str(exc)) from exc
        except Exception as exc:
            raise ContinuousError(
                f"unable to delete persisted queue command: {exc}"
            ) from exc


def _storm_state_event_id(command_id: str, state: str) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"iot-grid-simulator:storm_{state}:{command_id}",
        )
    )


def _storm_started_command_id(
    active_storm: dict[str, Any] | None,
    candidate_storm: dict[str, Any] | None,
    commands: list[sqlite3.Row],
    applied_commands: list[str],
) -> str | None:
    if active_storm is not None or candidate_storm is None:
        return None
    applied = set(applied_commands)
    result = None
    for command in commands:
        if (
            command["command_id"] in applied
            and command["command_type"] == "storm_upsert"
        ):
            result = command["command_id"]
    return result


def _storm_cleared_command_id(
    active_storm: dict[str, Any] | None,
    candidate_storm: dict[str, Any] | None,
    commands: list[sqlite3.Row],
    applied_commands: list[str],
) -> str | None:
    if active_storm is None or candidate_storm is not None:
        return None
    applied = set(applied_commands)
    result = None
    for command in commands:
        if (
            command["command_id"] in applied
            and command["command_type"] == "storm_clear"
        ):
            result = command["command_id"]
    return result


def _load_monitor_assets(batch_directory: Path) -> list[dict[str, Any]]:
    path = batch_directory / "assets.jsonl"
    assets: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ContinuousError(
                        f"monitor asset record {line_number} must be an object"
                    )
                assets.append(record)
    except (OSError, json.JSONDecodeError) as exc:
        raise ContinuousError(
            f"unable to load published assets for monitor context: {exc}"
        ) from exc
    return assets


def _storm_monitor_context(
    batch_directory: Path,
    storm: dict[str, Any],
    run_id: str,
    scenario: str,
    state: str,
) -> dict[str, Any]:
    route = sorted(storm["route"], key=lambda point: point["sequence"])
    route_area_refs = [area_location_ref(point["area"]) for point in route]
    route_area_set = set(route_area_refs)
    affected_assets = sorted(
        (
            asset
            for asset in _load_monitor_assets(batch_directory)
            if asset.get("location_ref") in route_area_set
        ),
        key=lambda asset: asset["asset_id"],
    )
    if not affected_assets:
        raise ContinuousError(
            "published batch contains no assets for the storm route"
        )

    primary_point = route[0]
    primary_area_ref = route_area_refs[0]
    primary_assets = [
        asset
        for asset in affected_assets
        if asset["location_ref"] == primary_area_ref
    ]
    primary_asset = next(
        (
            asset
            for asset in primary_assets
            if asset["asset_type"] == "feeder_relay"
        ),
        primary_assets[0] if primary_assets else affected_assets[0],
    )
    affected_asset_ids = [asset["asset_id"] for asset in affected_assets]
    last_impact_at = max(point["departure_utc"] for point in route)
    highest_flood_state = max(
        (point["flood_state"] for point in route),
        key={"none": 0, "watch": 1, "warning": 2}.__getitem__,
    )
    peak_wind = max(float(point["wind_speed_mps"]) for point in route)
    peak_precipitation = max(float(point["precipitation_mm"]) for point in route)
    if state == "started":
        condition_summary = (
            f"Synthetic storm route started; {primary_point['area']} is the "
            f"first impact with flood {primary_point['flood_state']}, "
            f"{float(primary_point['wind_speed_mps']):.1f} m/s wind, "
            f"{float(primary_point['precipitation_mm']):.1f} mm precipitation, "
            f"and {primary_point['certainty'].lower()} certainty."
        )
        impact_summary = (
            f"{len(affected_asset_ids)} fictional devices across "
            f"{len(route_area_refs)} areas may show deterministic voltage or "
            "telemetry-delivery effects during active route intervals."
        )
    else:
        condition_summary = (
            f"Synthetic storm route cleared after {len(route_area_refs)} "
            f"planned areas; the last scheduled impact ended at {last_impact_at}."
        )
        impact_summary = (
            f"{len(affected_asset_ids)} fictional route-associated devices are "
            "no longer under the active storm overlay; verify recovery in the "
            "cleared batch."
        )

    return {
        "ContextSchemaVersion": "1.0",
        "CorrelationId": storm["storm_id"],
        "CorrelationKey": f"{storm['storm_id']}|{run_id}",
        "Scenario": scenario,
        "PrimaryAssetId": primary_asset["asset_id"],
        "PrimaryAssetType": primary_asset["asset_type"],
        "PrimaryAssetModel": primary_asset["generic_model"],
        "PrimaryAreaRef": primary_area_ref,
        "PrimaryAreaName": primary_point["area"],
        "AffectedAssetCount": len(affected_asset_ids),
        "AffectedAssetIds": canonical_json(affected_asset_ids),
        "RouteAreaCount": len(route_area_refs),
        "RouteAreas": canonical_json([point["area"] for point in route]),
        "StormIssuedAtUtc": storm["issued_at_utc"],
        "FirstImpactAtUtc": primary_point["arrival_utc"],
        "LastImpactAtUtc": last_impact_at,
        "PeakWindSpeedMps": peak_wind,
        "PeakPrecipitationMm": peak_precipitation,
        "HighestFloodState": highest_flood_state,
        "ConditionSummary": condition_summary,
        "ImpactSummary": impact_summary,
        "InvestigationHint": (
            f"Use RunId={run_id} and CorrelationId={storm['storm_id']} to join "
            "assets, alerts, events, telemetry, and ground_truth; begin with "
            f"PrimaryAssetId={primary_asset['asset_id']}."
        ),
    }


def _insert_storm_state_event(
    connection: sqlite3.Connection,
    command_id: str,
    runtime_id: str,
    run_id: str,
    storm: dict[str, Any],
    batch_directory: Path,
    scenario: str,
    azure_resource_id: str,
    completed_at: datetime,
    state: str,
) -> None:
    event_id = _storm_state_event_id(command_id, state)
    payload = {
        "TimeGenerated": _timestamp(completed_at),
        "EventId": event_id,
        "CommandId": command_id,
        "RuntimeId": runtime_id,
        "RunId": run_id,
        "StormId": storm["storm_id"],
        "StormRevision": int(storm["revision"]),
        "State": state,
        "Severity": "Sev2",
        "IsSynthetic": True,
        "ExerciseStatus": storm["status"],
        "AzureResourceId": azure_resource_id,
        **_storm_monitor_context(
            batch_directory,
            storm,
            run_id,
            scenario,
            state,
        ),
    }
    connection.execute(
        """
        INSERT INTO monitor_outbox(
            event_id, command_id, payload_json, status, created_at_utc
        ) VALUES (?, ?, ?, 'pending', ?)
        ON CONFLICT(event_id) DO NOTHING
        """,
        (
            event_id,
            command_id,
            canonical_json(payload),
            payload["TimeGenerated"],
        ),
    )


def _drain_monitor_outbox(
    connection: sqlite3.Connection,
    publisher: MonitorPublisher,
) -> None:
    rows = list(
        connection.execute(
            """
            SELECT event_id, payload_json
            FROM monitor_outbox
            WHERE status='pending'
            ORDER BY rowid
            """
        )
    )
    if not rows:
        return
    events = [json.loads(row["payload_json"]) for row in rows]
    try:
        publisher.publish(events)
    except MonitorPublisherError as exc:
        raise ContinuousError(str(exc)) from exc
    except Exception as exc:
        raise ContinuousError(
            f"unable to publish monitor outbox: {exc}"
        ) from exc
    sent_at = _timestamp(datetime.now(timezone.utc))
    with connection:
        connection.executemany(
            """
            UPDATE monitor_outbox
            SET status='sent', sent_at_utc=?
            WHERE event_id=? AND status='pending'
            """,
            [(sent_at, row["event_id"]) for row in rows],
        )


def _candidate_environment(
    active_storm: dict[str, Any] | None,
    active_incident: dict[str, Any] | None,
    commands: list[sqlite3.Row],
    simulated_start: datetime,
) -> tuple[
    dict[str, Any] | None,
    dict[str, Any] | None,
    list[str],
    str | None,
]:
    candidate_storm = copy.deepcopy(active_storm)
    candidate_incident = copy.deepcopy(active_incident)
    applied: list[str] = []
    for command in commands:
        if command["command_type"] == "stop":
            return (
                candidate_storm,
                candidate_incident,
                [],
                command["command_id"],
            )
        if command["command_type"] == "storm_upsert":
            candidate_storm = _resolve_storm(
                json.loads(command["payload_json"]),
                simulated_start,
            )
        elif command["command_type"] == "storm_clear":
            candidate_storm = None
        elif command["command_type"] == "incident_upsert":
            candidate_incident = resolve_incident_template(
                json.loads(command["payload_json"]),
                simulated_start,
            )
        elif command["command_type"] == "incident_clear":
            candidate_incident = None
        else:
            raise ContinuousError(
                f"unsupported runtime command: {command['command_type']}"
            )
        applied.append(command["command_id"])
    if candidate_storm is not None:
        last_departure = max(
            _parse_utc(point["departure_utc"])
            for point in candidate_storm["route"]
        )
        if simulated_start >= last_departure:
            candidate_storm = None
    if candidate_incident is not None:
        resolved_at = _parse_utc(
            candidate_incident["lifecycle"][-1]["at_utc"]
        )
        if simulated_start > resolved_at:
            candidate_incident = None
    return candidate_storm, candidate_incident, applied, None


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContinuousError(f"unable to read batch manifest: {exc}") from exc


def _load_model_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContinuousError(f"unable to read batch model state: {exc}") from exc
    if not isinstance(value, dict):
        raise ContinuousError("batch model state must be an object")
    return value


def _legacy_model_state(
    output_root: Path,
    previous_sequence: int,
) -> dict[str, Any] | None:
    """Recover transformer memory from a pre-state-artifact batch."""
    if previous_sequence < 1:
        return None
    telemetry_path = (
        output_root
        / "batches"
        / f"batch={previous_sequence:09d}"
        / "telemetry.jsonl"
    )
    if not telemetry_path.is_file():
        return None
    ambient_by_time: dict[str, float] = {}
    oil_by_asset: dict[str, tuple[str, float]] = {}
    try:
        with telemetry_path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                record = json.loads(line)
                value = record.get("value")
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    continue
                timestamp = record.get("event_time_utc")
                if not isinstance(timestamp, str):
                    continue
                if record.get("measurement") == "air_temperature":
                    ambient_by_time[timestamp] = float(value)
                elif record.get("measurement") == "top_oil_temperature":
                    asset_id = record.get("asset_id")
                    if isinstance(asset_id, str):
                        oil_by_asset[asset_id] = (timestamp, float(value))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContinuousError(
            f"unable to recover legacy model state: {exc}"
        ) from exc
    thermal = {
        asset_id: oil - ambient_by_time[timestamp]
        for asset_id, (timestamp, oil) in oil_by_asset.items()
        if timestamp in ambient_by_time
    }
    if not thermal:
        return None
    return {
        "schema_version": MODEL_STATE_SCHEMA_VERSION,
        "as_of_utc": max(timestamp for timestamp, _ in oil_by_asset.values()),
        "thermal_rise_c": thermal,
    }


def _publish_batch(
    config: dict[str, Any],
    output_root: Path,
    runtime_id: str,
    sequence: int,
    sink_factory: SinkFactory | None,
    model_state: dict[str, Any] | None,
) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    batches_directory = output_root / "batches"
    pending_directory = output_root / "_pending" / f"batch={sequence:09d}"
    final_directory = batches_directory / f"batch={sequence:09d}"
    expected_normalized = validate_config(copy.deepcopy(config))

    if final_directory.is_dir():
        manifest = _load_manifest(final_directory / "manifest.json")
        if manifest["normalized_config"] != expected_normalized:
            raise ContinuousError(
                f"completed batch {sequence} conflicts with checkpoint state"
            )
        batch_directory = final_directory
    else:
        if pending_directory.exists():
            pending_manifest = pending_directory / "manifest.json"
            if pending_manifest.is_file():
                manifest = _load_manifest(pending_manifest)
                if manifest["normalized_config"] != expected_normalized:
                    raise ContinuousError(
                        f"pending batch {sequence} conflicts with checkpoint state"
                    )
            else:
                shutil.rmtree(pending_directory)
                manifest = generate_dataset(
                    config,
                    pending_directory,
                    model_state=model_state,
                )
        else:
            manifest = generate_dataset(
                config,
                pending_directory,
                model_state=model_state,
            )
        batch_directory = pending_directory

    published_state = _load_model_state(batch_directory / "model_state.json")

    sink = sink_factory(sequence, runtime_id) if sink_factory is not None else None
    if sink is not None:
        sink.finalize(
            batch_directory,
            batch_directory / "manifest.json",
            manifest,
        )

    marker = {
        "status": "complete",
        "runtime_id": runtime_id,
        "batch_sequence": sequence,
        "run_id": manifest["run_id"],
        "manifest_path": "manifest.json",
        "manifest_sha256": sha256_file(batch_directory / "manifest.json"),
    }
    _atomic_json(batch_directory / "_SUCCESS.json", marker)
    if batch_directory != final_directory:
        final_directory.parent.mkdir(parents=True, exist_ok=True)
        os.replace(batch_directory, final_directory)
    return manifest, final_directory, published_state


def _stop_is_pending(runtime_directory: Path) -> bool:
    connection = _connect(runtime_directory)
    try:
        return (
            connection.execute(
                """
                SELECT 1 FROM commands
                WHERE status='pending' AND command_type='stop'
                LIMIT 1
                """
            ).fetchone()
            is not None
        )
    finally:
        connection.close()


def _interruptible_wait(
    runtime_directory: Path,
    seconds: float,
) -> None:
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or _stop_is_pending(runtime_directory):
            return
        time.sleep(min(0.25, remaining))


def build_adls_sink_factory(
    account_url: str,
    file_system: str,
    path_prefix: str,
    auth_mode: str = "default",
    managed_identity_client_id: str | None = None,
) -> SinkFactory:
    """Build an idempotent per-batch ADLS sink factory."""

    def factory(sequence: int, runtime_id: str) -> AdlsSink:
        return AdlsSink(
            account_url=account_url,
            file_system=file_system,
            path_prefix=(
                f"{path_prefix.strip('/')}/instance_id={runtime_id}/"
                f"batch={sequence:09d}"
            ),
            mode="upload",
            auth_mode=auth_mode,
            managed_identity_client_id=managed_identity_client_id,
            overwrite=True,
        )

    return factory


def run_continuous(
    config_path: str | Path,
    output_root: str | Path,
    runtime_directory: str | Path,
    wall_clock_seconds: float = 60.0,
    simulation_interval_minutes: int = 15,
    start_utc: str | None = None,
    max_batches: int = 0,
    recover: bool = False,
    sink_factory: SinkFactory | None = None,
    on_batch: Callable[[dict[str, Any]], None] | None = None,
    command_source: CommandSource | None = None,
    monitor_publisher: MonitorPublisher | None = None,
    monitor_target_resource_id: str | None = None,
) -> list[dict[str, Any]]:
    """Publish complete deterministic micro-batches until stopped."""
    if wall_clock_seconds < 0:
        raise ContinuousError("wall-clock seconds must not be negative")
    if max_batches < 0:
        raise ContinuousError("max batches must not be negative")
    if (monitor_publisher is None) != (monitor_target_resource_id is None):
        raise ContinuousError(
            "monitor publisher and target Azure resource ID must be configured together"
        )
    if monitor_target_resource_id == "":
        raise ContinuousError(
            "monitor target Azure resource ID must not be empty"
        )
    runtime = Path(runtime_directory)
    output = Path(output_root)
    base_config, _ = _prepare_runtime(
        Path(config_path),
        runtime,
        simulation_interval_minutes,
        start_utc,
    )
    lock_path = _acquire_lock(runtime, recover)
    published: list[dict[str, Any]] = []
    stopped_by_command = False
    connection = _connect(runtime)
    try:
        with connection:
            _set_meta(connection, "status", "running")
        while max_batches == 0 or len(published) < max_batches:
            if monitor_publisher is not None:
                _drain_monitor_outbox(connection, monitor_publisher)
            elif connection.execute(
                """
                SELECT 1 FROM monitor_outbox
                WHERE status='pending'
                LIMIT 1
                """
            ).fetchone() is not None:
                raise ContinuousError(
                    "pending monitor events require a configured publisher"
                )
            if command_source is not None:
                configured_areas = set(
                    base_config["location"].get("areas", [])
                )
                if not configured_areas:
                    raise ContinuousError(
                        "queue storm commands require configured location areas"
                    )
                _persist_queue_commands(
                    connection,
                    command_source,
                    configured_areas,
                )
            meta = _meta(connection)
            simulated_start = _parse_utc(meta["next_simulated_time_utc"])
            sequence = int(meta["next_batch_sequence"])
            interval = int(meta["simulation_interval_minutes"])
            commands = _pending_commands(connection)
            active_storm = json.loads(meta["active_storm_json"])
            active_incident = json.loads(
                meta.get("active_incident_json", "null")
            )
            persisted_model_state = json.loads(
                meta.get("model_state_json", "null")
            )
            if persisted_model_state is None:
                persisted_model_state = _legacy_model_state(
                    output,
                    sequence - 1,
                )
            if persisted_model_state is None:
                persisted_model_state = {
                    "schema_version": MODEL_STATE_SCHEMA_VERSION,
                    "as_of_utc": None,
                    "thermal_rise_c": {},
                }
            (
                candidate_storm,
                candidate_incident,
                applied_commands,
                stop_command,
            ) = _candidate_environment(
                active_storm,
                active_incident,
                commands,
                simulated_start,
            )
            if stop_command is not None:
                with connection:
                    connection.execute(
                        """
                        UPDATE commands
                        SET status='applied', applied_batch_sequence=?
                        WHERE command_id=?
                        """,
                        (sequence - 1, stop_command),
                    )
                    _set_meta(connection, "status", "stopped")
                stopped_by_command = True
                break

            simulated_end = simulated_start + timedelta(minutes=interval)
            batch_config = copy.deepcopy(base_config)
            batch_config["simulation"] = {
                "start_utc": _timestamp(simulated_start),
                "end_utc": _timestamp(simulated_end),
                "interval_minutes": interval,
            }
            environment: dict[str, Any] = {}
            configured_faults = base_config.get("environment", {}).get(
                "sensor_faults",
                [],
            )
            active_faults = [
                fault
                for fault in configured_faults
                if _parse_utc(fault["start_utc"]) < simulated_end
                and simulated_start < _parse_utc(fault["end_utc"])
            ]
            if active_faults:
                environment["sensor_faults"] = active_faults
            if candidate_storm is not None:
                environment["storm"] = candidate_storm
            if candidate_incident is not None:
                environment["incident"] = candidate_incident
            if environment:
                batch_config["environment"] = environment
            else:
                batch_config.pop("environment", None)

            manifest, local_path, published_model_state = _publish_batch(
                batch_config,
                output,
                meta["runtime_id"],
                sequence,
                sink_factory,
                copy.deepcopy(persisted_model_state),
            )
            storm_revision = (
                candidate_storm["revision"]
                if candidate_storm is not None
                else None
            )
            storm_started_command = _storm_started_command_id(
                active_storm,
                candidate_storm,
                commands,
                applied_commands,
            )
            storm_cleared_command = _storm_cleared_command_id(
                active_storm,
                candidate_storm,
                commands,
                applied_commands,
            )
            with connection:
                connection.execute(
                    """
                    INSERT INTO batches(
                        batch_sequence, simulated_start_utc,
                        simulated_end_utc, run_id, local_path,
                        manifest_sha256, storm_revision, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'published')
                    ON CONFLICT(batch_sequence) DO UPDATE SET
                        simulated_start_utc=excluded.simulated_start_utc,
                        simulated_end_utc=excluded.simulated_end_utc,
                        run_id=excluded.run_id,
                        local_path=excluded.local_path,
                        manifest_sha256=excluded.manifest_sha256,
                        storm_revision=excluded.storm_revision,
                        status='published'
                    """,
                    (
                        sequence,
                        _timestamp(simulated_start),
                        _timestamp(simulated_end),
                        manifest["run_id"],
                        str(local_path),
                        sha256_file(local_path / "manifest.json"),
                        storm_revision,
                    ),
                )
                for command_id in applied_commands:
                    connection.execute(
                        """
                        UPDATE commands
                        SET status='applied', applied_batch_sequence=?
                        WHERE command_id=?
                        """,
                        (sequence, command_id),
                    )
                if (
                    storm_started_command is not None
                    and monitor_target_resource_id is not None
                    and candidate_storm is not None
                ):
                    _insert_storm_state_event(
                        connection,
                        storm_started_command,
                        meta["runtime_id"],
                        manifest["run_id"],
                        candidate_storm,
                        local_path,
                        batch_config["scenario"],
                        monitor_target_resource_id,
                        datetime.now(timezone.utc),
                        "started",
                    )
                if (
                    storm_cleared_command is not None
                    and monitor_target_resource_id is not None
                    and active_storm is not None
                ):
                    _insert_storm_state_event(
                        connection,
                        storm_cleared_command,
                        meta["runtime_id"],
                        manifest["run_id"],
                        active_storm,
                        local_path,
                        batch_config["scenario"],
                        monitor_target_resource_id,
                        datetime.now(timezone.utc),
                        "cleared",
                    )
                _set_meta(
                    connection,
                    "next_simulated_time_utc",
                    _timestamp(simulated_end),
                )
                _set_meta(connection, "next_batch_sequence", sequence + 1)
                _set_meta(
                    connection,
                    "active_storm_json",
                    canonical_json(candidate_storm),
                )
                _set_meta(
                    connection,
                    "active_incident_json",
                    canonical_json(candidate_incident),
                )
                _set_meta(
                    connection,
                    "model_state_json",
                    canonical_json(published_model_state),
                )
            if monitor_publisher is not None:
                _drain_monitor_outbox(connection, monitor_publisher)
            summary = {
                "runtime_id": meta["runtime_id"],
                "batch_sequence": sequence,
                "simulated_start_utc": _timestamp(simulated_start),
                "simulated_end_utc": _timestamp(simulated_end),
                "run_id": manifest["run_id"],
                "local_path": str(local_path),
                "storm_revision": storm_revision,
                "incident_id": (
                    candidate_incident["incident_id"]
                    if candidate_incident is not None
                    else None
                ),
                "incident_status": (
                    incident_status_at(candidate_incident, simulated_start)
                    if candidate_incident is not None
                    else None
                ),
            }
            published.append(summary)
            if on_batch is not None:
                on_batch(summary)
            if max_batches == 0 or len(published) < max_batches:
                _interruptible_wait(runtime, wall_clock_seconds)
        if not stopped_by_command:
            with connection:
                _set_meta(connection, "status", "idle")
    except KeyboardInterrupt:
        with connection:
            _set_meta(connection, "status", "stopped")
    except ContinuousError:
        with connection:
            _set_meta(connection, "status", "failed")
        raise
    except (
        AdlsError,
        ConfigError,
        IncidentError,
        MonitorError,
        ModelStateError,
        OSError,
        sqlite3.Error,
    ) as exc:
        with connection:
            _set_meta(connection, "status", "failed")
        raise ContinuousError(str(exc)) from exc
    finally:
        connection.close()
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
    return published
