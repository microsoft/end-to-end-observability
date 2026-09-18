"""Provider failures remain terminal, accounted, and safe to show to operators."""
from dataclasses import asdict
import json

import pytest

from agentic_itops.consumer.budget import BudgetExceeded
from agentic_itops.consumer.responses import (
    AgentFailure, OpenAIResponsesTransport, ProviderFailureDetails, RuntimeAgent,
    PROVIDER_ERROR_CODES, PROVIDER_INCOMPLETE_REASONS, PROVIDER_STATUSES,
)
from agentic_itops.review.diagnostics import failure_diagnostic
from agentic_itops.runtime import AGENTS
from tests.test_consumer_responses import EMPTY, SCHEMA, message, setup


def stored(error):
    return {"status": "failed", "error_code": type(error).__name__,
            "error_message": f"{type(error).__name__}: {error}"}


def tool_output():
    return [{"type": "function_call", "name": "read_evidence",
             "call_id": "must-not-dispatch", "arguments": "{}"}]


def assert_settled(engine, transport, factory, *, count=1, tools=0):
    assert len(transport.calls) == count
    assert engine.active == [] and engine.receipts == []
    inspected = engine.ledger.inspect_workflow("workflow")
    assert inspected["api_calls"] == count and inspected["tool_calls"] == tools
    assert inspected["call_states"] == {"settled": count}
    assert inspected["accounted_nanodollars"] == 60 * count
    with factory() as connection:
        assert connection.execute(
            "SELECT state, charged_nanos FROM itops_runtime_call").fetchall() == [("settled", 60)] * count


@pytest.mark.parametrize("status,reason,error,expected", [
    ("incomplete", "max_output_tokens", None, "unclassified"),
    ("incomplete", "content_filter", None, "unclassified"),
    ("incomplete", "new_unrecognized_reason", None, "unclassified"),
    ("failed", None, {"code": "server_error", "message": "secret"}, "server_error"),
    ("failed", None, {"code": "rate_limit_exceeded"}, "rate_limit_exceeded"),
    ("failed", None, {"code": "new_provider_code", "message": "secret"}, "unclassified"),
    ("cancelled", None, None, "unclassified"),
    ("in_progress", None, None, "unclassified"),
    ("queued", None, None, "unclassified"),
    ("completed", None, {"code": "invalid_prompt", "message": "secret"}, "invalid_prompt"),
    ("unknown_status", None, None, "unclassified"),
])
def test_provider_failures_preserve_only_safe_fields_without_dispatch_or_retry(
        tmp_path, status, reason, error, expected):
    reply = {**message(), "status": status, "incomplete_details": {"reason": reason},
             "error": error, "output": tool_output()}
    dispatched = []
    replies = [reply, message()]
    engine, transport, factory = setup(tmp_path, replies, lambda *args: dispatched.append(args))
    with pytest.raises(AgentFailure) as caught:
        engine.invoke("ops-orchestrator", {})
    details = ProviderFailureDetails(
        "ops-orchestrator", status, reason, error.get("code") if isinstance(error, dict) else None)
    assert str(caught.value) == details.message()
    assert details.provider_error_code == expected
    assert details.provider_status == (status if status in PROVIDER_STATUSES else "unclassified")
    assert details.incomplete_reason == (reason if reason in PROVIDER_INCOMPLETE_REASONS else "unclassified")
    assert failure_diagnostic(stored(caught.value)) == {
        "status": "classified", "error_code": "AgentFailure",
        "reason": "provider_response_failed", **asdict(details),
    }
    assert not dispatched and replies == [message()]
    assert "secret" not in str(caught.value)
    assert_settled(engine, transport, factory)


@pytest.mark.parametrize("field,value", [
    ("status", None), ("status", []), ("status", {}), ("status", True),
    ("incomplete_details", None), ("incomplete_details", []), ("incomplete_details", "secret"),
    ("incomplete_details", {"reason": []}), ("incomplete_details", {"reason": {"secret": "value"}}),
    ("incomplete_details", {"reason": True}), ("incomplete_details", {"reason": 8192}),
    ("error", []), ("error", "secret"), ("error", {"code": []}),
    ("error", {"code": {"secret": "value"}}), ("error", {"code": False}),
    ("error", {"code": 500}), ("error", {"message": "secret", "url": "https://secret.invalid"}),
])
def test_malformed_provider_metadata_is_not_stringified_or_dispatched(tmp_path, field, value):
    reply = {**message(), "status": "failed", "output": tool_output(), field: value}
    calls = []
    engine, transport, factory = setup(tmp_path, [reply], lambda *args: calls.append(args))
    with pytest.raises(AgentFailure) as caught:
        engine.invoke("ops-orchestrator", {})
    diagnostic = failure_diagnostic(stored(caught.value))
    assert diagnostic["status"] == "classified"
    assert diagnostic["provider_error_code"] == diagnostic["incomplete_reason"] == "unclassified"
    assert diagnostic["provider_status"] == ("unclassified" if field == "status" else "failed")
    assert not calls
    assert "secret" not in json.dumps(diagnostic) and "secret" not in str(caught.value)
    assert_settled(engine, transport, factory)


def test_huge_secret_metadata_and_partial_model_text_never_enter_the_exception_or_diagnostic(tmp_path):
    secret = "https://secret.invalid/?token=private-token\n" * 30_000
    reply = {**message(secret), "status": secret, "incomplete_details": {"reason": secret},
             "error": {"code": secret, "message": secret, "url": secret},
             "unknown": {"secret": secret}}
    engine, transport, factory = setup(tmp_path, [reply])
    with pytest.raises(AgentFailure) as caught:
        engine.invoke("ops-orchestrator", {})
    expected = ProviderFailureDetails("ops-orchestrator", None, None, None)
    assert str(caught.value) == expected.message()
    assert len(str(caught.value)) < 256
    assert "secret" not in json.dumps(failure_diagnostic(stored(caught.value)))
    assert_settled(engine, transport, factory)


@pytest.mark.parametrize("agent", AGENTS)
def test_every_allowlisted_agent_roundtrips_without_version_or_response_identity(agent):
    details = ProviderFailureDetails.from_response(agent, {
        "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
        "id": "secret-response-id", "agent": "secret-provider-agent", "version": "secret-version",
    })
    diagnostic = failure_diagnostic(stored(AgentFailure(details.message())))
    assert diagnostic["agent"] == agent and diagnostic["provider_status"] == "incomplete"
    assert "secret" not in json.dumps(diagnostic)


@pytest.mark.parametrize("unknown", [None, True, [], {}, "unbound-agent", "private-token" * 100_000],
                         ids=["missing", "boolean", "array", "object", "unbound", "oversized"])
def test_typed_fields_cannot_hold_unknown_values(unknown):
    details = ProviderFailureDetails(unknown, unknown, unknown, unknown)
    assert set(asdict(details).values()) == {"unclassified"}
    assert ProviderFailureDetails.from_stored_message("AgentFailure: " + details.message()) == details


@pytest.mark.parametrize("value", sorted(PROVIDER_ERROR_CODES))
def test_explicit_provider_error_code_allowlist_roundtrips(value):
    details = ProviderFailureDetails("rca-investigator", "failed", None, value)
    result = failure_diagnostic(stored(AgentFailure(details.message())))
    assert result["provider_error_code"] == value


@pytest.mark.parametrize("value", sorted(PROVIDER_INCOMPLETE_REASONS))
def test_explicit_incomplete_reason_allowlist_roundtrips(value):
    details = ProviderFailureDetails("rca-investigator", "incomplete", value, None)
    result = failure_diagnostic(stored(AgentFailure(details.message())))
    assert result["incomplete_reason"] == value


@pytest.mark.parametrize("value", [
    None, {}, [], 12,
    "AgentFailure: provider_response_failure_v1 agent=secret",
    "AgentFailure: provider_response_failure_v1 agent=ops-orchestrator status=incomplete "
    "incomplete_reason=max_output_tokens provider_error_code=server_error secret=private-token",
    "AgentFailure: provider_response_failure_v2 agent=ops-orchestrator status=incomplete "
    "incomplete_reason=max_output_tokens provider_error_code=server_error",
    "AgentFailure: provider_response_failure_v1 agent=ops-orchestrator status=incomplete "
    "incomplete_reason=max_output_tokens provider_error_code=https://secret.invalid",
    "AgentFailure: provider_response_failure_v1 agent=ops-orchestrator\nstatus=incomplete "
    "incomplete_reason=max_output_tokens provider_error_code=server_error",
    "AgentFailure: provider_response_failure_v1 " + "private-token" * 100_000,
], ids=lambda value: type(value).__name__)
def test_malformed_or_oversized_stored_messages_are_not_reflected(value):
    assert failure_diagnostic({
        "status": "failed", "error_code": "AgentFailure", "error_message": value,
    }) == {"status": "unclassified_failure", "error_code": "AgentFailure"}


def test_unknown_tokens_in_fixed_format_are_explicitly_unclassified():
    result = failure_diagnostic({
        "status": "failed", "error_code": "AgentFailure",
        "error_message": "AgentFailure: provider_response_failure_v1 agent=secret status=secret "
                         "incomplete_reason=secret provider_error_code=secret",
    })
    assert result == {
        "status": "classified", "error_code": "AgentFailure", "reason": "provider_response_failed",
        "agent": "unclassified", "provider_status": "unclassified",
        "incomplete_reason": "unclassified", "provider_error_code": "unclassified",
    }


def test_legacy_generic_provider_failure_does_not_invent_a_reason_or_agent():
    result = failure_diagnostic(stored(AgentFailure("provider response did not complete successfully")))
    assert result == {
        "status": "classified", "error_code": "AgentFailure", "reason": "provider_response_failed",
        "agent": "unclassified", "provider_status": "unclassified",
        "incomplete_reason": "unclassified", "provider_error_code": "unclassified",
    }


@pytest.mark.parametrize("text", [
    "provider usage is absent; full reservation retained",
    "provider usage is invalid; full reservation retained",
    "native or unknown tool activity has no proven isolation/budget bound",
    "provider response did not complete successfully private-token",
    "private-token",
])
def test_other_generic_agent_failures_remain_safely_unclassified(text):
    assert failure_diagnostic(stored(AgentFailure(text))) == {
        "status": "unclassified_failure", "error_code": "AgentFailure"}


@pytest.mark.parametrize("code", [None, [], {}, True, "private-token"])
def test_unknown_error_classes_do_not_reflect_data_or_break_classification(code):
    assert failure_diagnostic({
        "status": "failed", "error_code": code, "error_message": "private-token",
    }) == {"status": "unclassified_failure", "error_code": "unclassified"}


@pytest.mark.parametrize("status", ["queued", "running", "completed"])
def test_nonfailed_jobs_cannot_be_misclassified_as_provider_failures(status):
    value = stored(AgentFailure(ProviderFailureDetails(
        "ops-orchestrator", "incomplete", "max_output_tokens", None).message()))
    value["status"] = status
    assert failure_diagnostic(value) == {"status": "not_failed"}


@pytest.mark.parametrize("usage,exception,state,reason", [
    (None, AgentFailure, "unknown", "provider usage is absent; full reservation retained"),
    ({"input_tokens": -1, "output_tokens": 10}, AgentFailure, "unknown",
     "provider usage is invalid; full reservation retained"),
    ({"input_tokens": 10, "output_tokens": 10, "total_tokens": 21}, AgentFailure, "unknown",
     "provider usage is invalid; full reservation retained"),
    ({"input_tokens": 1001, "output_tokens": 10}, BudgetExceeded, "overrun",
     "provider usage exceeded its proven bound; workflow stopped"),
])
def test_failure_diagnostics_do_not_override_usage_accounting_or_overrun(
        tmp_path, usage, exception, state, reason):
    reply = {**message(), "status": "incomplete", "usage": usage,
             "incomplete_details": {"reason": "max_output_tokens"}, "output": tool_output()}
    calls = []
    engine, transport, factory = setup(tmp_path, [reply], lambda *args: calls.append(args))
    with pytest.raises(exception) as caught:
        engine.invoke("ops-orchestrator", {})
    assert str(caught.value) == reason
    assert not calls and len(transport.calls) == 1 and not engine.receipts
    inspected = engine.ledger.inspect_workflow("workflow")
    assert inspected["api_calls"] == 1 and inspected["tool_calls"] == 0
    assert inspected["call_states"] == {state: 1}
    expected = (usage["input_tokens"] * engine.ledger.limits.input_nanodollars_per_token
                + usage["output_tokens"] * engine.ledger.limits.output_nanodollars_per_token
                if state == "overrun" else engine.ledger.limits.reserved_nanodollars)
    assert inspected["accounted_nanodollars"] == expected


def test_failed_continuation_settles_both_calls_but_never_dispatches_again(tmp_path):
    first = {**message(), "output": tool_output()}
    failed = {**message(), "status": "incomplete", "output": tool_output(),
              "incomplete_details": {"reason": "max_output_tokens"}}
    calls = []
    replies = [first, failed, message()]
    engine, transport, factory = setup(
        tmp_path, replies, lambda *args: calls.append(args) or {"status": "complete"})
    with pytest.raises(AgentFailure) as caught:
        engine.invoke("ops-orchestrator", {})
    assert len(calls) == 1 and len(replies) == 1
    assert failure_diagnostic(stored(caught.value))["incomplete_reason"] == "max_output_tokens"
    assert_settled(engine, transport, factory, count=2, tools=1)


def test_nested_agent_failure_reports_the_actual_failing_agent(tmp_path):
    first = {**message(), "output": tool_output()}
    failed = {**message(), "status": "failed", "error": {"code": "server_error", "message": "secret"}}
    engine, transport, factory = setup(tmp_path, [first, failed])
    engine.agents["incident-comms"] = RuntimeAgent.create(
        "incident-comms", "3", SCHEMA, {}, {"portfolio_sha256": "a" * 64})
    engine.dispatch = lambda *args: engine.invoke("incident-comms", {})
    with pytest.raises(AgentFailure) as caught:
        engine.invoke("ops-orchestrator", {})
    assert failure_diagnostic(stored(caught.value))["agent"] == "incident-comms"
    assert_settled(engine, transport, factory, count=2, tools=1)


@pytest.mark.parametrize("output,reason", [
    ([{"type": "azure_ai_search_call"}],
     "native or unknown tool activity has no proven isolation/budget bound"),
    (tool_output() * 2, "parallel function dispatch is not authorized"),
])
def test_native_and_parallel_tools_remain_rejected_after_settlement(tmp_path, output, reason):
    calls = []
    engine, transport, factory = setup(
        tmp_path, [{**message(), "output": output}], lambda *args: calls.append(args))
    with pytest.raises(AgentFailure) as caught:
        engine.invoke("ops-orchestrator", {})
    assert str(caught.value) == reason
    assert not calls
    assert_settled(engine, transport, factory)


def test_sdk_transport_passes_approved_output_cap_without_changing_safety_options():
    recorded = {}

    class Client:
        responses = None

        def with_options(self, **kwargs):
            recorded["options"] = kwargs
            self.responses = self
            return self

        def create(self, **kwargs):
            recorded["request"] = kwargs
            return self

        def model_dump(self, **kwargs):
            return {**message(), "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"}}

    agent = RuntimeAgent.create("ops-orchestrator", "3", SCHEMA, {"read_evidence": EMPTY},
                                {"portfolio_sha256": "a" * 64})
    result = OpenAIResponsesTransport(Client()).respond(agent, [], 8192, 15)
    assert result["incomplete_details"]["reason"] == "max_output_tokens"
    assert recorded["options"] == {"max_retries": 0, "timeout": 15}
    assert recorded["request"] == {
        "input": [], "store": False, "parallel_tool_calls": False, "truncation": "disabled",
        "max_output_tokens": 8192, "extra_body": agent.reference.extra_body(),
    }
