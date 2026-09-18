from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("jsonschema", reason="install requirements-consumer.txt")
httpx = pytest.importorskip("httpx", reason="install requirements-consumer.txt")

from agentic_itops.consumer.contracts import (
    COLUMNS, SCHEMA_PATH, SURFACES, ContractError, Observation, ProducerScope,
    canonical, source_validator, strict_json, utc,
)
from agentic_itops.consumer.logs import (
    AzureLogsTransport, IncompleteQuery, QueryWindow, WindowTooLarge, compile_query, decode_response,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.parent / "domainsV2" / "contracts"
ROWS = [strict_json(line) for line in
        (SOURCE / "energy-observations.v1.examples.jsonl").read_bytes().splitlines() if line.strip()]
SCOPE = ProducerScope("/subscriptions/00000000-0000-0000-0000-000000000001/"
                      "resourceGroups/test/providers/Microsoft.OperationalInsights/workspaces/source")


def window(row):
    at = utc(row["ObservedAt"])
    return QueryWindow(row["RunId"], (row["Surface"],), at, at + timedelta(minutes=2),
                       at, at + timedelta(minutes=1), at, at + timedelta(minutes=3))


def response(rows, *, dynamic_text=False):
    columns = [{"name": key, "type": source_validator().schema["properties"][key]["x-log-analytics-type"]}
               for key in COLUMNS] + [{"name": "_IngestedAt", "type": "datetime"}]
    values = []
    for row in rows:
        values.append([json.dumps(row[key]) if dynamic_text and key in ("Payload", "Context")
                       else row[key] for key in COLUMNS] + [utc(row["ObservedAt"]) + timedelta(seconds=5)])
    return {"tables": [{"name": "PrimaryResult", "columns": columns, "rows": values}]}


def test_vendored_contract_matches_producer_authority():
    assert SCHEMA_PATH.read_bytes() == (SOURCE / SCHEMA_PATH.name).read_bytes()


@pytest.mark.parametrize("surface", SURFACES)
def test_all_nine_surfaces_preserve_enriched_and_legacy_fields(surface):
    rows = [row for row in ROWS if row["Surface"] == surface]
    assert rows
    for original in rows:
        parsed = Observation.parse(SCOPE, original)
        assert parsed.row["Payload"] == original["Payload"]
        assert parsed.row["Context"] == original["Context"]
        assert parsed.signal_identity[:6] == tuple(original[key] for key in
            ("Domain", "Surface", "RunId", "RecordType", "EntityId", "LocationId"))
        legacy = deepcopy(original)
        legacy["Context"].pop("details_profile", None)
        assert Observation.parse(SCOPE, legacy).row["Context"] == legacy["Context"]


@pytest.mark.parametrize("record_type,field", [("telemetry", "value"), ("inventory_position", "quantity")])
def test_null_and_zero_have_distinct_meaning(record_type, field):
    row = deepcopy(next(row for row in ROWS if row["RecordType"] == record_type))
    row["Payload"][field] = None
    missing = Observation.parse(SCOPE, row)
    row["Payload"][field] = 0
    zero = Observation.parse(SCOPE, row)
    assert missing.row["Payload"][field] is None
    assert zero.row["Payload"][field] == 0
    assert missing.source_sha256 != zero.source_sha256


@pytest.mark.parametrize("key,value", [
    ("SchemaVersion", 1), ("IsSynthetic", False), ("OperationalUse", "allowed"),
    ("Surface", "unknown"), ("EventId", "bad"), ("Domain", "wrong"),
    ("ObservedAt", "2026-01-01T12:00:00"), ("Payload", "not an object"),
])
def test_bad_source_contract_rejected(key, value):
    row = deepcopy(ROWS[0])
    row[key] = value
    with pytest.raises(ContractError):
        Observation.parse(SCOPE, row)


@pytest.mark.parametrize("key", ["Scenario", "ground_truth", "isAnomaly", "detector_verdict"])
def test_recursive_control_labels_never_enter_evidence(key):
    row = deepcopy(ROWS[0])
    row["Context"]["extra"] = [{"nested": {key: "hidden"}}]
    with pytest.raises(ContractError, match="labels"):
        Observation.parse(SCOPE, row)


def test_missing_source_time_stays_unknown_and_clock_offset_is_preserved():
    row = deepcopy(next(row for row in ROWS if row["RecordType"] == "telemetry"))
    row["Payload"]["source_time_utc"] = None
    assert Observation.parse(SCOPE, row).source_age_seconds is None
    row["Payload"]["source_time_utc"] = utc(row["SimulatedAt"]) - timedelta(seconds=300)
    assert Observation.parse(SCOPE, row).source_age_seconds == 300
    row["Payload"]["source_time_utc"] = utc(row["SimulatedAt"]) + timedelta(seconds=1)
    assert Observation.parse(SCOPE, row).source_age_seconds == -1


def test_structured_identity_does_not_collapse_delimiters_or_namespaces():
    first, second = deepcopy(ROWS[0]), deepcopy(ROWS[0])
    first["EntityId"], first["LocationId"] = "a|b", "c"
    second["EntityId"], second["LocationId"] = "a", "b|c"
    assert Observation.parse(SCOPE, first).signal_key != Observation.parse(SCOPE, second).signal_key
    other = ProducerScope(SCOPE.workspace_resource_id.replace("/source", "/other"))
    assert Observation.parse(SCOPE, first).event_key != Observation.parse(other, first).event_key


def test_native_and_text_dynamic_responses_decode_identically():
    original = deepcopy(ROWS[0])
    expected = decode_response(response([original]), SCOPE, window(original))
    text = decode_response(response([original], dynamic_text=True), SCOPE, window(original))
    assert canonical(expected.to_dict()) == canonical(text.to_dict())


def test_column_order_comes_from_metadata():
    payload = response([ROWS[0]])
    table = payload["tables"][0]
    table["columns"].reverse()
    table["rows"][0].reverse()
    assert decode_response(payload, SCOPE, window(ROWS[0])).records[0].observation.row["EventId"] == ROWS[0]["EventId"]


@pytest.mark.parametrize("defect", ["partial", "missing_column", "duplicate_column", "bad_type",
                                  "short_row", "missing_ingestion", "outside_window", "outside_run"])
def test_incomplete_results_cannot_be_complete_batches(defect):
    payload = response([ROWS[0]])
    table = payload["tables"][0]
    if defect == "partial":
        payload["error"] = {"code": "PartialError"}
    elif defect == "missing_column":
        table["columns"].pop()
        table["rows"][0].pop()
    elif defect == "duplicate_column":
        table["columns"][-1] = table["columns"][0]
    elif defect == "bad_type":
        table["columns"][0]["type"] = "string"
    elif defect == "short_row":
        table["rows"][0].pop()
    elif defect == "missing_ingestion":
        table["rows"][0][-1] = None
    elif defect == "outside_window":
        table["rows"][0][-1] = utc(ROWS[0]["ObservedAt"]) + timedelta(days=1)
    else:
        table["rows"][0][COLUMNS.index("RunId")] = "different"
    with pytest.raises(IncompleteQuery):
        decode_response(payload, SCOPE, window(ROWS[0]))


def test_empty_is_not_healthy_and_row_limit_is_not_truncation():
    batch = decode_response(response([]), SCOPE, window(ROWS[0]))
    assert batch.to_dict()["coverage"] == "no_observations"
    with pytest.raises(WindowTooLarge):
        decode_response(response([ROWS[0]]), SCOPE, window(ROWS[0]), row_limit=1)


def test_duplicates_allow_only_service_time_replacement():
    first, duplicate = deepcopy(ROWS[0]), deepcopy(ROWS[0])
    duplicate["TimeGenerated"] = utc(first["ObservedAt"]) + timedelta(seconds=1)
    batch = decode_response(response([duplicate, first]), SCOPE, window(first))
    assert len(batch.records) == 1 and batch.duplicate_count == 1
    duplicate["Payload"]["value"] = 345
    with pytest.raises(IncompleteQuery, match="conflicting"):
        decode_response(response([first, duplicate]), SCOPE, window(first))


def test_complete_source_projection_and_escaped_run_id():
    query_window = replace(window(ROWS[0]), run_id='opaque" | union OtherTable //')
    query = compile_query(query_window)
    assert f"RunId == {json.dumps(query_window.run_id)}" in query
    assert "| project " + ", ".join(COLUMNS) + ", _IngestedAt" in query
    assert "| take" not in query and "| limit" not in query
    assert "isnull(_IngestedAt)" in query and "ingestion_time()" in query


@pytest.mark.parametrize("text", ['{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}'])
def test_ambiguous_json_is_rejected(text):
    with pytest.raises(ContractError):
        strict_json(text)


def test_actual_http_serialization_and_bounded_response():
    calls = []
    credential = SimpleNamespace(get_token=lambda scope: SimpleNamespace(token="unit-test-token"))
    def send(request):
        calls.append(request)
        return httpx.Response(200, json={"tables": []})
    with httpx.Client(transport=httpx.MockTransport(send)) as client:
        transport = AzureLogsTransport(credential, client)
        assert transport.query("00000000-0000-0000-0000-000000000002", "bounded query") == {"tables": []}
        with pytest.raises(WindowTooLarge):
            AzureLogsTransport(credential, client, max_bytes=2).query(
                "00000000-0000-0000-0000-000000000002", "bounded query")
    assert calls[0].url.host == "api.loganalytics.azure.com"
    assert json.loads(calls[0].content)["query"] == "bounded query"


@pytest.mark.parametrize("status", [206, 301, 403, 429, 500])
def test_http_failure_or_redirect_cannot_be_success(status):
    credential = SimpleNamespace(get_token=lambda scope: SimpleNamespace(token="unit-test-token"))
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(status, headers={"Location": "https://untrusted.invalid"})
    )) as client:
        with pytest.raises(IncompleteQuery):
            AzureLogsTransport(credential, client).query(
                "00000000-0000-0000-0000-000000000002", "bounded query")
