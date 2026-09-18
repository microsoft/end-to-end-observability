from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("jsonschema", reason="install requirements-consumer.txt")

from agentic_itops.access_probe import (
    AccessProofError, count_result, operator_witness, query_contract, verify_managed_identity_access,
)


NOW = datetime(2026, 9, 15, 20, 0, tzinfo=timezone.utc)


def count(value):
    return {"tables": [{"name": "PrimaryResult", "columns": [{"name": "rows", "type": "long"}], "rows": [[value]]}]}


@pytest.fixture
def contract():
    return query_contract("11111111-1111-1111-1111-111111111111", 'Opaque-"Run"', "2026-09-14T00:00:00Z",
                          "2026-09-16T00:00:00Z")


@pytest.fixture
def witness(contract):
    return operator_witness(contract, lambda workspace, query: (200, count(4 if "Audit_CL" in query else 10530)),
                            now=NOW)


@pytest.mark.parametrize("status,result", [
    (403, {"error": {"code": "InsufficientAccessError"}}), (200, count(0)),
])
def test_known_nonempty_audit_denial_and_observation_access(contract, witness, status, result):
    calls = []
    def query(workspace, text):
        calls.append(text)
        return (status, result) if "Audit_CL" in text else (200, count(10530))
    receipt = verify_managed_identity_access(contract, witness, query, now=NOW)
    assert receipt["audit_access"] == "denied"
    assert receipt["independent_audit_rows"] == 4 and receipt["observation_rows"] == 10530
    assert receipt["inference_input"] is False and len(calls) == 2


@pytest.mark.parametrize("status,result", [
    (200, count(4)), (200, {"tables": []}),
    (200, {**count(0), "error": {"code": "PartialError"}}),
    (403, {"error": {"code": "UnknownError"}}), (401, {"error": {"code": "InsufficientAccessError"}}),
    (429, {}), (500, {}), (400, {"error": {"code": "TableNotFound"}}),
])
def test_audit_errors_or_readable_rows_are_not_denial(contract, witness, status, result):
    def query(workspace, text):
        return (status, result) if "Audit_CL" in text else (200, count(10530))
    with pytest.raises(AccessProofError):
        verify_managed_identity_access(contract, witness, query, now=NOW)


@pytest.mark.parametrize("change", [
    {"contract_sha256": "wrong"}, {"audit_rows": 0}, {"observation_rows": True},
    {"inference_input": True}, {"witness_kind": "model_claim"},
    {"observed_at": (NOW - timedelta(minutes=6)).isoformat()},
    {"observed_at": (NOW + timedelta(minutes=1)).isoformat()},
])
def test_bad_witness_fails_before_mi_queries(contract, witness, change):
    def forbidden(*args):
        raise AssertionError("should reject before querying")
    with pytest.raises(AccessProofError):
        verify_managed_identity_access(contract, {**witness, **change}, forbidden, now=NOW)


@pytest.mark.parametrize("bad", [True, -1, "0", 1.0, None])
def test_invalid_counts_are_not_success(bad):
    with pytest.raises(AccessProofError):
        count_result(200, count(bad))


def test_operator_witness_requires_nonempty_audit(contract):
    with pytest.raises(AccessProofError):
        operator_witness(contract, lambda *args: (200, count(0)), now=NOW)


def test_opaque_run_is_escaped_and_queries_are_count_only(contract):
    assert '| where RunId == "Opaque-\\"Run\\""' in contract["queries"]["observations"]
    assert all(query.endswith("| summarize rows=count()") for query in contract["queries"].values())
    assert all("TimeGenerated" in query for query in contract["queries"].values())


def test_modified_query_cannot_reuse_an_existing_witness_digest(contract, witness):
    contract["queries"]["audit"] = "print rows=0"
    with pytest.raises(AccessProofError, match="queries or digest"):
        verify_managed_identity_access(contract, witness, lambda *args: (200, count(0)), now=NOW)
