from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from typing import Any, Protocol
from uuid import UUID

import httpx

from .contracts import (
    COLUMNS, SURFACES, ContractError, Observation, ProducerScope, canonical, digest, iso,
    source_validator, strict_json, utc,
)


class IncompleteQuery(RuntimeError):
    """No checkpoint may advance for this query."""


class WindowTooLarge(IncompleteQuery):
    """Subdivide the ingestion window; never silently truncate it."""


@dataclass(frozen=True)
class QueryWindow:
    run_id: str
    surfaces: tuple[str, ...]
    observed_start: datetime
    observed_end: datetime
    ingestion_start: datetime
    ingestion_end: datetime
    service_start: datetime
    service_end: datetime

    def __post_init__(self):
        if (not isinstance(self.run_id, str) or not self.run_id or len(self.run_id) > 256
                or any(ord(char) < 32 for char in self.run_id)):
            raise ContractError("an exact bounded RunId is required")
        if (not isinstance(self.surfaces, tuple) or not self.surfaces
                or len(set(self.surfaces)) != len(self.surfaces)
                or any(surface not in SURFACES for surface in self.surfaces)):
            raise ContractError("select distinct supported surfaces explicitly")
        for name, maximum in (("observed", timedelta(hours=2)),
                              ("ingestion", timedelta(minutes=15)),
                              ("service", timedelta(days=31))):
            start, end = utc(getattr(self, name + "_start")), utc(getattr(self, name + "_end"))
            if start >= end or end - start > maximum:
                raise ContractError(f"{name} window is empty, reversed or exceeds its bound")
            object.__setattr__(self, name + "_start", start)
            object.__setattr__(self, name + "_end", end)
        if (self.service_start > min(self.observed_start, self.ingestion_start)
                or self.service_end < max(self.observed_end, self.ingestion_end)):
            raise ContractError("service scan must cover observation and ingestion bounds")
        object.__setattr__(self, "surfaces", tuple(sorted(self.surfaces)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "surfaces": list(self.surfaces),
            **{name: iso(getattr(self, name)) for name in (
                "observed_start", "observed_end", "ingestion_start", "ingestion_end",
                "service_start", "service_end")},
        }

    @property
    def key(self) -> str:
        return digest(self.to_dict())


def compile_query(window: QueryWindow) -> str:
    surfaces = ", ".join(json.dumps(surface) for surface in window.surfaces)
    return "\n".join((
        "EnergyObservations_CL",
        f"| where TimeGenerated >= datetime({iso(window.service_start)})",
        f"    and TimeGenerated < datetime({iso(window.service_end)})",
        f"| where RunId == {json.dumps(window.run_id)} and Surface in ({surfaces})",
        f"| where isnull(ObservedAt) or (ObservedAt >= datetime({iso(window.observed_start)})",
        f"    and ObservedAt < datetime({iso(window.observed_end)}))",
        "| extend _IngestedAt = ingestion_time()",
        f"| where isnull(_IngestedAt) or (_IngestedAt >= datetime({iso(window.ingestion_start)})",
        f"    and _IngestedAt < datetime({iso(window.ingestion_end)}))",
        "| project " + ", ".join(COLUMNS) + ", _IngestedAt",
        "| order by ObservedAt asc, EventId asc, TimeGenerated asc, _IngestedAt asc",
    ))


@dataclass(frozen=True)
class QueryRecord:
    observation: Observation
    ingested_at: datetime


@dataclass(frozen=True)
class CompleteBatch:
    producer: ProducerScope
    window: QueryWindow
    records: tuple[QueryRecord, ...]
    duplicate_count: int
    received_row_count: int

    @classmethod
    def from_document(cls, content: bytes) -> CompleteBatch:
        value = strict_json(content)
        if not isinstance(value, dict) or not isinstance(value.get("window"), dict):
            raise ContractError("stored evidence batch is malformed")
        try:
            producer = ProducerScope(value["workspace_resource_id"])
            raw_window = dict(value["window"])
            raw_window["surfaces"] = tuple(raw_window["surfaces"])
            window = QueryWindow(**raw_window)
            records = value["records"]
            duplicate_count, received_count = value["duplicate_count"], value["received_row_count"]
            if (not isinstance(records, list) or type(duplicate_count) is not int or duplicate_count < 0
                    or type(received_count) is not int or not 0 <= received_count < 5000
                    or received_count != len(records) + duplicate_count
                    or (not records and duplicate_count)):
                raise ContractError("stored batch row accounting is invalid")
            rows = []
            for item in records:
                observation = Observation.parse(producer, item["observation"])
                if item["sha256"] != observation.sha256 or item["source_sha256"] != observation.source_sha256:
                    raise ContractError("stored source record digest does not match")
                rows.append([observation.row[name] for name in COLUMNS] + [item["ingested_at"]])
            columns = [{"name": name, "type": source_validator().schema["properties"][name]["x-log-analytics-type"]}
                       for name in COLUMNS] + [{"name": "_IngestedAt", "type": "datetime"}]
            decoded = decode_response({"tables": [{"columns": columns, "rows": rows}]}, producer, window)
            batch = cls(producer, window, decoded.records, duplicate_count, received_count)
        except (KeyError, TypeError) as exc:
            raise ContractError("stored evidence batch contract is incomplete") from exc
        if canonical(batch.to_dict()) != canonical(value):
            raise ContractError("stored evidence batch does not match its canonical complete receipt")
        return batch

    @property
    def key(self) -> str:
        return digest([self.producer.key, self.window.key])

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1", "producer_key": self.producer.key,
            "workspace_resource_id": self.producer.workspace_resource_id,
            "window": self.window.to_dict(), "query_complete": True,
            "coverage": "observations_present" if self.records else "no_observations",
            "received_row_count": self.received_row_count, "duplicate_count": self.duplicate_count,
            "records": [{"observation": item.observation.row, "ingested_at": iso(item.ingested_at),
                         "sha256": item.observation.sha256,
                         "source_sha256": item.observation.source_sha256} for item in self.records],
            "IsSynthetic": True, "OperationalUse": "prohibited", "execution_allowed": False,
        }


def decode_response(
    payload: Any, producer: ProducerScope, window: QueryWindow, *, row_limit: int = 5000,
) -> CompleteBatch:
    if type(row_limit) is not int or row_limit < 1:
        raise ContractError("row limit must be a positive integer")
    if not isinstance(payload, dict) or payload.get("error") is not None:
        raise IncompleteQuery("Log Analytics returned an error or partial result")
    tables = payload.get("tables")
    if not isinstance(tables, list) or len(tables) != 1 or not isinstance(tables[0], dict):
        raise IncompleteQuery("expected one complete result table")
    table = tables[0]
    columns, rows = table.get("columns"), table.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list):
        raise IncompleteQuery("result columns/rows are absent or malformed")
    if len(rows) >= row_limit:
        raise WindowTooLarge("row safety limit reached; subdivide the ingestion window")
    expected = {
        name: source_validator().schema["properties"][name]["x-log-analytics-type"]
        for name in COLUMNS
    }
    expected["_IngestedAt"] = "datetime"
    names = []
    types = {}
    for column in columns:
        if not isinstance(column, dict) or not isinstance(column.get("name"), str):
            raise IncompleteQuery("invalid column metadata")
        name, kind = column["name"], column.get("type")
        if name not in expected or name in types:
            raise IncompleteQuery("unexpected or duplicate result column")
        if kind != expected[name] and not (expected[name] == "boolean" and kind == "bool"):
            raise IncompleteQuery("column type does not match the observation projection")
        names.append(name)
        types[name] = kind
    if set(names) != set(expected):
        raise IncompleteQuery("observation projection is incomplete")
    decoded = []
    for row in rows:
        if not isinstance(row, list) or len(row) != len(names):
            raise IncompleteQuery("row length does not match column metadata")
        record = dict(zip(names, row))
        try:
            ingested_at = utc(record.pop("_IngestedAt"))
            for field in ("Payload", "Context"):
                if isinstance(record[field], str):
                    record[field] = strict_json(record[field])
            observation = Observation.parse(producer, record)
        except ContractError as exc:
            raise IncompleteQuery(f"invalid source record: {exc}") from exc
        source = observation.row
        if (source["RunId"] != window.run_id or source["Surface"] not in window.surfaces
                or not window.observed_start <= utc(source["ObservedAt"]) < window.observed_end
                or not window.ingestion_start <= ingested_at < window.ingestion_end
                or not window.service_start <= utc(source["TimeGenerated"]) < window.service_end):
            raise IncompleteQuery("query returned evidence outside its authorized window/scope")
        decoded.append(QueryRecord(observation, ingested_at))
    def order(item: QueryRecord):
        source = item.observation.row
        return utc(source["ObservedAt"]), source["EventId"], utc(source["TimeGenerated"]), item.ingested_at

    decoded.sort(key=order)
    unique: dict[str, QueryRecord] = {}
    for item in decoded:
        key = item.observation.event_key
        if key in unique:
            if unique[key].observation.source_sha256 != item.observation.source_sha256:
                raise IncompleteQuery("conflicting source content for one producer EventId")
        else:
            unique[key] = item
    return CompleteBatch(producer, window, tuple(unique.values()), len(rows) - len(unique), len(rows))


class QueryTransport(Protocol):
    def query(self, workspace_id: str, query: str) -> dict[str, Any]: ...


class LogsReader:
    def __init__(self, producer: ProducerScope, workspace_id: str, transport: QueryTransport):
        self.producer = producer
        self.workspace_id = str(UUID(workspace_id))
        self.transport = transport

    def read(self, window: QueryWindow) -> CompleteBatch:
        response = self.transport.query(self.workspace_id, compile_query(window))
        return decode_response(response, self.producer, window)


class AccessToken(Protocol):
    token: str


class TokenCredential(Protocol):
    def get_token(self, *scopes: str) -> AccessToken: ...


class AzureLogsTransport:
    """Explicit credential injection; no implicit developer-credential fallback."""

    def __init__(self, credential: TokenCredential, client: httpx.Client, *, max_bytes: int = 16_000_000):
        if type(max_bytes) is not int or max_bytes < 1:
            raise ContractError("response byte limit must be positive")
        self.credential, self.client, self.max_bytes = credential, client, max_bytes

    def query(self, workspace_id: str, query: str) -> dict[str, Any]:
        workspace_id = str(UUID(workspace_id))
        token = self.credential.get_token("https://api.loganalytics.io/.default")
        body = bytearray()
        with self.client.stream(
            "POST", f"https://api.loganalytics.azure.com/v1/workspaces/{workspace_id}/query",
            headers={"Authorization": f"Bearer {token.token}", "Prefer": "wait=60"},
            json={"query": query}, timeout=70, follow_redirects=False,
        ) as response:
            if response.status_code != 200:
                raise IncompleteQuery(f"Log Analytics HTTP {response.status_code}; no checkpoint committed")
            for chunk in response.iter_bytes():
                if len(body) + len(chunk) > self.max_bytes:
                    raise WindowTooLarge("response byte limit reached; subdivide the ingestion window")
                body.extend(chunk)
        try:
            result = strict_json(bytes(body))
        except ContractError as exc:
            raise IncompleteQuery("Log Analytics returned malformed JSON") from exc
        if not isinstance(result, dict):
            raise IncompleteQuery("Log Analytics response must be an object")
        return result
