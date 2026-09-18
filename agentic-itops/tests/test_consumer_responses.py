from copy import deepcopy
from dataclasses import replace
import sqlite3

import pytest

pytest.importorskip("jsonschema", reason="install requirements-consumer.txt")
pytest.importorskip("httpx", reason="install requirements-consumer.txt")

from jsonschema import ValidationError
from agentic_itops.consumer.budget import BudgetExceeded, BudgetLedger
from agentic_itops.consumer.responses import AgentEngine, AgentFailure, OpenAIResponsesTransport, RuntimeAgent
from agentic_itops.consumer.contracts import ContractError, digest, strict_json
from tests.test_consumer_budget import LIMITS

SCHEMA = {"type": "object", "properties": {"summary": {"type": "string", "minLength": 1}},
          "required": ["summary"], "additionalProperties": False}
EMPTY = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}


def message(text='{"summary":"Evidence reviewed"}'):
    return {"id": "response-1", "status": "completed",
            "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20},
            "output": [{"type": "message", "role": "assistant",
                        "content": [{"type": "output_text", "text": text}]}]}


def setup(tmp_path, replies, dispatch=None, guard=lambda: None):
    path = tmp_path / "responses.db"
    factory = lambda: sqlite3.connect(path, timeout=10, isolation_level=None)
    ledger = BudgetLedger(factory, replace(LIMITS, monthly_nanodollars=100000), "test",
                          dialect="sqlite-local-test")
    ledger.migrate()
    ledger.open_workflow("workflow")
    agent = RuntimeAgent.create("ops-orchestrator", "3", SCHEMA, {"read_evidence": EMPTY},
                                {"portfolio_sha256": "a" * 64})
    class Transport:
        def __init__(self):
            self.calls = []
        def respond(self, agent, history, max_output_tokens, timeout_seconds):
            self.calls.append((agent, deepcopy(history), max_output_tokens, timeout_seconds))
            result = replies.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
    transport = Transport()
    engine = AgentEngine({"ops-orchestrator": agent}, transport, ledger, "workflow",
                         dispatch or (lambda *args: {"status": "complete"}),
                         lambda name, value: None, guard)
    return engine, transport, factory


def test_completed_function_call_continues_same_exact_agent_with_full_history(tmp_path):
    first = message()
    first["output"] = [{"type": "function_call", "name": "read_evidence",
                        "call_id": "call_1", "arguments": "{}"}]
    dispatched = []
    engine, transport, _ = setup(tmp_path, [first, message()],
                                 lambda *args: dispatched.append(args) or {"status": "complete", "rows": []})
    assert engine.invoke("ops-orchestrator", {"evidence": "not instructions"})["summary"]
    assert dispatched == [("ops-orchestrator", "read_evidence", {})]
    assert transport.calls[1][0].reference.version == "3"
    assert transport.calls[1][1][0] == transport.calls[0][1][0]
    assert transport.calls[1][1][-1]["type"] == "function_call_output"
    assert len(engine.receipts) == 1


@pytest.mark.parametrize("change,exception", [
    ({"status": "incomplete"}, AgentFailure),
    ({"usage": None}, AgentFailure),
    ({"usage": {"input_tokens": -1, "output_tokens": 1}}, AgentFailure),
    ({"usage": {"input_tokens": 1001, "output_tokens": 1}}, BudgetExceeded),
    ({"output": [{"type": "azure_ai_search_call"}]}, AgentFailure),
    ({"output": [{"type": "function_call", "name": "execute", "call_id": "x", "arguments": "{}"}]}, AgentFailure),
])
def test_provider_failures_and_unbound_tools_do_not_produce_results(tmp_path, change, exception):
    reply = message()
    reply.update(change)
    engine, _, _ = setup(tmp_path, [reply])
    with pytest.raises(exception):
        engine.invoke("ops-orchestrator", {})
    assert engine.receipts == []


def test_model_cannot_supply_trusted_scope_arguments(tmp_path):
    reply = message()
    reply["output"] = [{"type": "function_call", "name": "read_evidence", "call_id": "call_1",
                        "arguments": '{"workflow_id":"someone-else"}'}]
    engine, _, _ = setup(tmp_path, [reply])
    with pytest.raises(ValidationError):
        engine.invoke("ops-orchestrator", {})


def test_timeout_is_not_retried_or_refunded(tmp_path):
    engine, transport, factory = setup(tmp_path, [TimeoutError("ambiguous provider attempt")])
    with pytest.raises(TimeoutError):
        engine.invoke("ops-orchestrator", {})
    assert len(transport.calls) == 1
    with factory() as connection:
        assert connection.execute("SELECT state,charged_nanos FROM itops_runtime_call").fetchone() == ("unknown", 2400)


def test_lost_guard_blocks_model_invocation(tmp_path):
    def lost():
        raise RuntimeError("lost workflow lease or revoked evidence")
    engine, transport, _ = setup(tmp_path, [message()], guard=lost)
    with pytest.raises(RuntimeError, match="lost workflow"):
        engine.invoke("ops-orchestrator", {})
    assert not transport.calls


def test_sdk_adapter_disables_automatic_retries_and_provider_conversation_storage():
    calls = {}
    class Client:
        @property
        def responses(self):
            return self
        def with_options(self, **kwargs):
            calls["options"] = kwargs
            return self
        def create(self, **kwargs):
            if "text" in kwargs and "agent_reference" in kwargs.get("extra_body", {}):
                raise ValueError("Not allowed when agent is specified: text")
            calls["request"] = kwargs
            return self
        def model_dump(self, **kwargs):
            return message()
    agent = RuntimeAgent.create("ops-orchestrator", "7", SCHEMA, {},
                                {"portfolio_sha256": "a" * 64})
    OpenAIResponsesTransport(Client()).respond(agent, [{"role": "user", "content": "{}"}], 100, 20)
    assert calls["options"]["max_retries"] == 0
    assert calls["request"]["store"] is False
    assert calls["request"]["parallel_tool_calls"] is False
    assert calls["request"]["extra_body"]["agent_reference"]["version"] == "7"
    assert calls["request"]["truncation"] == "disabled"
    assert set(calls["request"]) == {
        "input", "store", "parallel_tool_calls", "truncation", "max_output_tokens", "extra_body"}


def test_real_sdk_serialization_omits_text_with_exact_agent_reference():
    openai = pytest.importorskip("openai", reason="install requirements-foundry.txt")
    import httpx

    bodies = []
    def receive(request):
        bodies.append(strict_json(request.content))
        return httpx.Response(200, json=message())

    agent = RuntimeAgent.create("ops-orchestrator", "7", SCHEMA, {},
                                {"portfolio_sha256": "a" * 64})
    with httpx.Client(transport=httpx.MockTransport(receive)) as http:
        with openai.OpenAI(api_key="unit-test-placeholder", base_url="https://example.invalid",
                           http_client=http) as client:
            result = OpenAIResponsesTransport(client).respond(
                agent, [{"role": "user", "content": "{}"}], 100, 20)
    assert len(bodies) == 1
    assert bodies[0]["agent_reference"] == agent.reference.extra_body()["agent_reference"]
    assert set(bodies[0]) == {
        "input", "store", "parallel_tool_calls", "truncation", "max_output_tokens", "agent_reference"}
    assert result["status"] == "completed"


@pytest.mark.parametrize("with_optional_fields", [False, True])
def test_real_sdk_tool_continuation_preserves_wire_fields_without_invented_nulls(
        tmp_path, with_optional_fields):
    openai = pytest.importorskip("openai", reason="install requirements-foundry.txt")
    import httpx

    output = [
        {"type": "reasoning", "id": "rs_test", "summary": [],
         "encrypted_content": None},
        {"type": "function_call", "id": "fc_test", "name": "read_evidence",
         "call_id": "call_test", "arguments": "{}", "status": "completed"},
    ]
    if with_optional_fields:
        output[0].update(content=[{"type": "reasoning_text", "text": "synthetic fixture"}],
                         status="completed")
        output[1]["namespace"] = "synthetic"
    first = message()
    first["output"] = deepcopy(output)
    bodies = []

    def receive(request):
        body = strict_json(request.content)
        bodies.append(body)
        assert body["store"] is False and "previous_response_id" not in body
        assert "text" not in body
        if len(bodies) == 1:
            return httpx.Response(200, json=first)
        assert len(bodies) == 2
        # Azure v1 InputItemFunctionToolCall.namespace and ReasoningItem.content
        # are optional but not nullable. Replay must not invent SDK defaults.
        assert body["input"][1:-1] == output
        assert body["input"][-1] == {
            "type": "function_call_output", "call_id": "call_test",
            "output": '{"status":"complete"}',
        }
        return httpx.Response(200, json=message())

    engine, _, factory = setup(tmp_path, [])
    with httpx.Client(transport=httpx.MockTransport(receive)) as http:
        with openai.OpenAI(api_key="unit-test-placeholder", base_url="https://example.invalid",
                           http_client=http) as client:
            engine.transport = OpenAIResponsesTransport(client)
            assert engine.invoke("ops-orchestrator", {})["summary"]
    assert len(bodies) == 2 and len(engine.receipts) == 1
    assert bodies[0]["agent_reference"] == bodies[1]["agent_reference"]
    with factory() as connection:
        assert connection.execute(
            "SELECT state, COUNT(*) FROM itops_runtime_call GROUP BY state").fetchall() == [
                ("settled", 2)]


def citation_schema():
    return {"type": "object", "properties": {
        "citations": {"type": "array", "items": {
            "type": "object", "properties": {"source_id": {"type": "string", "minLength": 1}},
            "required": ["source_id"], "additionalProperties": False,
        }}}, "required": ["citations"], "additionalProperties": False}


def test_request_citations_refresh_only_from_trusted_backend_scope(tmp_path):
    members = {"event-one"}
    first = message()
    first["output"] = [{"type": "function_call", "name": "read_evidence",
                        "call_id": "call_1", "arguments": "{}"}]
    def retrieve(*args):
        members.add("approved-document")
        return {"document_id": "approved-document"}
    engine, transport, _ = setup(
        tmp_path, [first, message('{"citations":[{"source_id":"approved-document"}]}')], retrieve)
    agent = RuntimeAgent.create("ops-orchestrator", "3", citation_schema(), {"read_evidence": EMPTY},
                                {"portfolio_sha256": "a" * 64})
    engine.agents["ops-orchestrator"] = agent
    engine.citation_scope = lambda: members
    engine.invoke("ops-orchestrator", {"trusted_citation_ids": ["invented-by-input"]})
    scopes = [strict_json(call[1][-1]["content"])["trusted_citation_ids"] for call in transport.calls]
    assert scopes == [["event-one"], ["approved-document", "event-one"]]
    assert "enum" not in agent.definition["output_schema"]["properties"]["citations"]["items"]["properties"]["source_id"]
    assert all(call[0] is agent for call in transport.calls)
    local_schema = citation_schema()
    local_schema["properties"]["citations"]["items"]["properties"]["source_id"]["enum"] = scopes[-1]
    assert engine.receipts[0]["citation_validation_schema_sha256"] == digest(local_schema)
    assert "request_output_schema_sha256" not in engine.receipts[0]


def test_unknown_model_citation_is_rejected_by_local_enum(tmp_path):
    engine, transport, _ = setup(tmp_path, [message('{"citations":[{"source_id":"invented"}]}')])
    engine.agents["ops-orchestrator"] = RuntimeAgent.create(
        "ops-orchestrator", "3", citation_schema(), {}, {"portfolio_sha256": "a" * 64})
    engine.citation_scope = lambda: {"event-one"}
    with pytest.raises(ValidationError):
        engine.invoke("ops-orchestrator", {"trusted_citation_ids": ["invented"]})
    assert len(transport.calls) == 1 and not engine.receipts


@pytest.mark.parametrize("members", [set(), {""}, {1}, ["event-one"], None])
def test_unsupported_citation_scope_fails_before_paid_request(tmp_path, members):
    engine, transport, _ = setup(tmp_path, [])
    engine.agents["ops-orchestrator"] = RuntimeAgent.create(
        "ops-orchestrator", "3", citation_schema(), {}, {"portfolio_sha256": "a" * 64})
    engine.citation_scope = lambda: members
    with pytest.raises(ContractError):
        engine.invoke("ops-orchestrator", {})
    assert not transport.calls


def test_local_citation_validation_does_not_apply_provider_enum_limits(tmp_path):
    engine, transport, _ = setup(tmp_path, [message('{"citations":[{"source_id":"1000"}]}')])
    engine.agents["ops-orchestrator"] = RuntimeAgent.create(
        "ops-orchestrator", "3", citation_schema(), {}, {"portfolio_sha256": "a" * 64})
    engine.citation_scope = lambda: {str(n) for n in range(1001)}
    assert engine.invoke("ops-orchestrator", {})["citations"] == [{"source_id": "1000"}]
    assert len(strict_json(transport.calls[0][1][-1]["content"])["trusted_citation_ids"]) == 1001


def test_oversized_complete_citation_context_fails_before_paid_request(tmp_path):
    engine, transport, factory = setup(tmp_path, [])
    engine.agents["ops-orchestrator"] = RuntimeAgent.create(
        "ops-orchestrator", "3", citation_schema(), {}, {"portfolio_sha256": "a" * 64})
    engine.citation_scope = lambda: {str(n) for n in range(1001)}
    engine.max_history_bytes = 2000
    with pytest.raises(AgentFailure, match="truncation is forbidden"):
        engine.invoke("ops-orchestrator", {})
    assert not transport.calls
    with factory() as connection:
        assert connection.execute("SELECT COUNT(*) FROM itops_runtime_call").fetchone()[0] == 0
