from copy import deepcopy
import json

from jsonschema import ValidationError
import pytest

from agentic_itops.consumer.responses import RuntimeAgent
from agentic_itops.consumer.schema_diagnostics import (
    MAX_STORED_LENGTH, PREFIX, from_stored_message, validate_at_boundary,
    schema_fields,
)
from agentic_itops.review.diagnostics import failure_diagnostic
from tests.test_consumer_responses import EMPTY, SCHEMA, message, setup


def stored(error):
    return json.dumps(f"ValidationError: {error}", ensure_ascii=True)[1:-1]


def inspect(error):
    return failure_diagnostic({"status": "failed", "error_code": "ValidationError",
                               "error_message": stored(error)})


def test_large_enum_and_rejected_value_cannot_displace_safe_header():
    schema = {"properties": {"citations": {"items": {"properties": {
        "source_id": {"enum": ["private-trusted-" + str(n) for n in range(2000)]}}}}}}
    value = {"citations": [{"source_id": "private-rejected-" * 2000}]}
    with pytest.raises(ValidationError) as caught:
        validate_at_boundary(schema, value, agent="remediation-planner", boundary="trusted_citations")
    result = inspect(caught.value)
    assert result["diagnostic_basis"] == "validation_boundary_metadata"
    assert result["agent"] == "remediation-planner"
    assert result["boundary"] == "trusted_citations"
    assert result["schema_keyword"] == "enum"
    assert result["schema_path"] == ["properties", "citations", "items", "properties", "source_id"]
    assert result["instance_path"] == ["citations", 0, "source_id"]
    assert result["actual_type"] == "string"
    assert len(stored(caught.value)) <= MAX_STORED_LENGTH
    assert "private" not in str(caught.value) + json.dumps(result)
    assert caught.value.__suppress_context__
    assert caught.value.__context__ is None


def test_missing_property_comes_from_trusted_schema_not_message_parsing():
    with pytest.raises(ValidationError) as caught:
        validate_at_boundary(SCHEMA, {}, agent="ops-orchestrator", boundary="agent_output")
    result = inspect(caught.value)
    assert result["schema_keyword"] == "required"
    assert result["missing_property"] == "summary"


def test_longest_allowed_paths_are_bounded_before_worker_serialization():
    name = max(schema_fields(), key=len)
    schema, value = {"type": "integer"}, "private"
    for _ in range(24):
        schema, value = {"properties": {name: schema}}, {name: value}
    with pytest.raises(ValidationError) as caught:
        validate_at_boundary(schema, value, agent="remediation-planner", boundary="function_output")
    assert len(stored(caught.value)) <= MAX_STORED_LENGTH
    assert inspect(caught.value)["diagnostic_basis"] == "validation_boundary_metadata"


def test_union_retains_bounded_branch_causes_not_expected_or_rejected_values():
    schema = {"properties": {"plan": {"anyOf": [
        {"properties": {"proposed_action": {"enum": ["private-action"]}}, "required": ["proposed_action"]},
        {"properties": {"proposed_action": {"enum": ["none"]}}, "required": ["proposed_action"]},
    ]}}}
    with pytest.raises(ValidationError) as caught:
        validate_at_boundary(schema, {"plan": {"proposed_action": "private-rejected"}},
                             agent="remediation-planner", boundary="agent_output")
    result = inspect(caught.value)
    assert result["schema_keyword"] == "anyOf"
    assert result["schema_path"] == ["properties", "plan"]
    assert result["causes"] == [
        {"schema_keyword": "enum",
         "schema_path": ["properties", "plan", "anyOf", n, "properties", "proposed_action"]}
        for n in (0, 1)
    ]
    assert result["causes_omitted"] is False
    assert "private" not in str(caught.value)


def test_deep_unknown_paths_and_many_union_failures_stay_bounded():
    schema = {"anyOf": [{"type": "integer"} for _ in range(40)]}
    value = "private"
    for _ in range(30):
        schema = {"properties": {"private": schema}}
        value = {"private": value}
    with pytest.raises(ValidationError) as caught:
        validate_at_boundary(schema, value, agent="private-agent", boundary="private-boundary")
    result = inspect(caught.value)
    assert result["agent"] == result["boundary"] == "unclassified"
    assert result["causes_omitted"]
    assert len(result["schema_path"]) <= 13
    assert len(result["instance_path"]) <= 13
    assert len(stored(caught.value)) <= MAX_STORED_LENGTH
    assert "private" not in str(caught.value) + json.dumps(result)


@pytest.mark.parametrize("boundary", ["agent_output", "function_arguments", "function_output"])
def test_engine_boundaries_remain_terminal_settled_and_do_not_retry(tmp_path, boundary):
    reply = message('{"summary":42}')
    calls = []
    if boundary != "agent_output":
        reply["output"] = [{"type": "function_call", "name": "read_evidence",
                            "call_id": "one-call", "arguments": '{"private":"secret"}'
                            if boundary == "function_arguments" else "{}"}]
    remaining = [reply, message()]
    engine, transport, _ = setup(tmp_path, remaining, lambda *args: calls.append(args) or {"summary": 42})
    if boundary == "function_output":
        engine.agents["ops-orchestrator"] = RuntimeAgent.create(
            "ops-orchestrator", "3", SCHEMA, {"read_evidence": EMPTY},
            {"portfolio_sha256": "a" * 64}, function_outputs={"read_evidence": SCHEMA})
    with pytest.raises(ValidationError) as caught:
        engine.invoke("ops-orchestrator", {})
    assert inspect(caught.value)["boundary"] == boundary
    assert len(transport.calls) == 1 and len(remaining) == 1
    assert len(calls) == (1 if boundary == "function_output" else 0)
    assert engine.receipts == [] and engine.active == []
    assert engine.ledger.inspect_workflow("workflow")["call_states"] == {"settled": 1}
    assert "private" not in str(caught.value) and "secret" not in str(caught.value)


def test_success_does_not_modify_schema_or_value():
    schema, value = deepcopy(SCHEMA), {"summary": "safe"}
    before = deepcopy((schema, value))
    validate_at_boundary(schema, value, agent="ops-orchestrator", boundary="agent_output")
    assert (schema, value) == before


def test_worker_persists_only_safe_metadata_and_cannot_reclaim_failed_job(tmp_path):
    from agentic_itops.consumer.storage_contract import WorkRequest
    from tests.test_consumer_observations import SCOPE
    from tests.test_consumer_worker import setup as setup_worker

    worker, store, _, _, _, scope = setup_worker(tmp_path)

    def fail(lease, guard):
        validate_at_boundary({"type": "integer"}, "private-rejected-" * 2000,
                             agent="remediation-planner", boundary="agent_output")

    worker.handlers["reasoning"] = fail
    job_id = store.enqueue(WorkRequest("reasoning", "bounded-schema-failure", {
        "producer_key": SCOPE.key, "run_id": scope.run_id, "IsSynthetic": True,
        "OperationalUse": "prohibited", "execution_allowed": False,
    }))
    with pytest.raises(ValidationError):
        worker.process_once("reasoning")
    job = store.get_job(job_id)
    assert job["status"] == "failed" and job["error_code"] == "ValidationError"
    assert job["result"] is None and job["next_job_ids"] == ()
    assert "private" not in job["error_message"]
    assert len(job["error_message"]) <= MAX_STORED_LENGTH
    assert failure_diagnostic(job)["agent"] == "remediation-planner"
    assert store.claim("reasoning", "another-worker") is None


def valid_record():
    with pytest.raises(ValidationError) as caught:
        validate_at_boundary({"type": "string"}, None, agent="ops-orchestrator", boundary="agent_output")
    return from_stored_message(stored(caught.value))


@pytest.mark.parametrize("field,value", [
    ("agent", "private"), ("boundary", "private"), ("schema_keyword", []),
    ("schema_path", ["private"]), ("instance_path", [True]), ("actual_type", "private"),
    ("causes", [{"schema_keyword": "enum", "schema_path": ["private"]}]),
    ("causes_omitted", "private"),
])
def test_stored_record_rejects_unrecognized_metadata(field, value):
    record = valid_record()
    record[field] = value
    encoded = json.dumps("ValidationError: " + PREFIX + json.dumps(record))[1:-1]
    assert from_stored_message(encoded) is None


@pytest.mark.parametrize("message", [None, {}, [], True, 42, "", "private", "\\" * 2000])
def test_malformed_legacy_input_is_never_reflected(message):
    assert from_stored_message(message) is None
