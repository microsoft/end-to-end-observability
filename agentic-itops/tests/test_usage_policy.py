"""Offline proof that explicit test accounting mode is not a retry/safety bypass."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from types import SimpleNamespace

import httpx
import pytest

from agentic_itops.consumer.budget import BudgetExceeded, BudgetLedger, BudgetLimits, UsageAuthorization
from agentic_itops.consumer.contracts import ContractError, digest, iso
from agentic_itops.consumer.storage_contract import IntegrityConflict
from agentic_itops.investigations import (
    DEFAULT_BATCH_LIMITS, approval, batch_limits, usage_authorization, validate_batch_budgets,
)
from agentic_itops.review.advancement import RemoteWorkAdvancer
from agentic_itops.review.contracts import OVERLAY, ReviewError
from agentic_itops.review.search_delivery import BudgetLedgerEmbeddingGate
from tests.test_consumer_budget import AGENT, LIMITS, REQUEST, ledger


ACTOR = "11111111-1111-4111-8111-111111111111"


def approved_usage(chat, embedding):
    now = datetime.now(timezone.utc)
    month_end = (now.replace(day=28) + timedelta(days=4)).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0)
    return {
        "approval_id": "offline-observe-only", "actor_oid": ACTOR,
        "expires_at_utc": iso(min(now + timedelta(minutes=10), month_end - timedelta(microseconds=1))),
        "limits": {**DEFAULT_BATCH_LIMITS, "usage_mode": "observe_only", "max_workflow_seconds": 180},
        "usage_policy": {"mode": "observe_only", "chat_policy_sha256": chat.sha256,
                         "embedding_policy_sha256": embedding.sha256},
    }


def observed(tmp_path, *, policy=None):
    policy = policy or replace(
        LIMITS, usage_mode="observe_only", policy_version="offline-observe-only",
        monthly_nanodollars=1, workflow_tokens=1, api_calls=1, tool_calls=1, deadline_seconds=180)
    clock = [datetime.now(timezone.utc)]
    auth = UsageAuthorization("offline-observe-only", ACTOR, iso(clock[0] + timedelta(hours=1)), policy.sha256)
    factory = lambda: sqlite3.connect(tmp_path / "observe.db", isolation_level=None, timeout=10)
    store = BudgetLedger(factory, policy, "same-scope", dialect="sqlite-local-test",
                         clock=lambda: clock[0], usage_authorization=auth)
    store.migrate()
    store.open_workflow("future")
    return store, factory, clock


def test_legacy_hashes_and_configuration_are_unchanged_and_mode_is_hash_bound():
    legacy = asdict(LIMITS)
    legacy.pop("usage_mode")
    assert BudgetLimits.parse(legacy) == LIMITS
    assert BudgetLimits.parse({**legacy, "usage_mode": "enforced"}).sha256 == digest(legacy)
    changed = replace(LIMITS, usage_mode="observe_only")
    assert changed.sha256 == digest(asdict(changed))
    assert changed.sha256 != LIMITS.sha256
    for bad in ("unlimited", "", None, True, 0):
        with pytest.raises(ContractError):
            BudgetLimits.parse({**legacy, "usage_mode": bad})
    with pytest.raises(ContractError):
        BudgetLimits.parse({key: value for key, value in legacy.items() if key != "api_calls"})


def test_observe_only_exceeds_tiny_caps_but_preserves_unknowns_and_settlement(tmp_path):
    store, factory, _ = observed(tmp_path)
    for index in range(4):
        store.reserve("future", f"call-{index}", AGENT, REQUEST)
        store.record_tool("future", f"tool-{index}", "read_evidence")
    store.settle("call-0")
    store.settle("call-1", 10, 20)
    store.settle("call-2", 20, 10)
    store.settle("call-2", 20, 10)
    totals = store.inspect_scope()
    assert totals["api_calls"] == totals["tool_calls"] == 4
    assert totals["call_states"] == {"unknown": 1, "settled": 2, "reserved": 1}
    assert totals["accounted_nanodollars"] == 2 * LIMITS.reserved_nanodollars + 100 + 80
    assert store.inspect_workflow("future")["tokens_accounted"] == 2 * LIMITS.reserved_tokens + 60
    month = store.inspect_month()
    assert month["accounted_nanodollars"] == totals["accounted_nanodollars"]
    assert month["effective_limit_nanodollars"] is None
    assert month["configured_limit_nanodollars"] == month["stored_limit_nanodollars"] == 1
    assert month["usage_mode"] == "observe_only" and month["caps_enforced"] is False
    with factory() as connection:
        before = list(connection.iterdump())
    for attempt in (lambda: store.reserve("future", "call-0", AGENT, REQUEST),
                    lambda: store.record_tool("future", "tool-0", "read_evidence"),
                    lambda: store.settle("call-0", 1, 1)):
        with pytest.raises(IntegrityConflict):
            attempt()
    with factory() as connection:
        assert list(connection.iterdump()) == before


@pytest.mark.parametrize("field", ["monthly_nanodollars", "workflow_tokens", "api_calls", "tool_calls"])
def test_default_enforced_caps_still_deny(tmp_path, field):
    policy = replace(LIMITS, monthly_nanodollars=100000, **({field: 1} if field != "monthly_nanodollars" else {}))
    if field == "monthly_nanodollars":
        policy = replace(policy, monthly_nanodollars=1)
    store, _, _ = ledger(tmp_path, policy)
    if field == "api_calls":
        store.reserve("workflow", "first", AGENT, REQUEST)
        store.settle("first", 1, 1)
    if field == "tool_calls":
        store.record_tool("workflow", "first", "read_evidence")
    with pytest.raises(BudgetExceeded):
        if field == "tool_calls":
            store.record_tool("workflow", "second", "read_evidence")
        else:
            store.reserve("workflow", "second", AGENT, REQUEST)


def test_historical_workflows_never_reclassify_or_reset_shared_months(tmp_path):
    old, factory, clock = ledger(tmp_path)
    old.reserve("workflow", "historic-unknown", AGENT, REQUEST)
    old.settle("historic-unknown")
    before = old.inspect_workflow("workflow")
    limits = replace(LIMITS, usage_mode="observe_only", monthly_nanodollars=1,
                     workflow_tokens=1, api_calls=1, tool_calls=1)
    auth = UsageAuthorization("offline", ACTOR, iso(clock[0] + timedelta(hours=1)), limits.sha256)
    future = BudgetLedger(factory, limits, old.scope, dialect="sqlite-local-test",
                          clock=lambda: clock[0], usage_authorization=auth)
    with pytest.raises(IntegrityConflict):
        future.open_workflow("workflow")
    with pytest.raises(IntegrityConflict):
        future.settle("historic-unknown")
    future.open_workflow("new-workflow")
    for number in range(3):
        future.reserve("new-workflow", f"new-{number}", AGENT, REQUEST)
        future.settle(f"new-{number}")
    assert future.inspect_scope()["accounted_nanodollars"] == 4 * LIMITS.reserved_nanodollars
    assert future.inspect_scope()["call_states"] == {"unknown": 4}
    assert future.inspect_month()["stored_limit_nanodollars"] == 4800
    assert old.inspect_month()["effective_limit_nanodollars"] == 4800
    assert future.inspect_workflow("workflow") == before
    with pytest.raises(BudgetExceeded):
        old.reserve("workflow", "old-cannot-exceed", AGENT, REQUEST)
    clock[0] += timedelta(days=32)
    with pytest.raises(ContractError, match="authorization"):
        future.open_workflow("expired-approval")
    assert future.inspect_scope()["api_calls"] == 4
    assert future.inspect_month()["accounted_nanodollars"] == 0
    assert future.inspect_workflow("workflow") == before


def test_observe_only_concurrent_reservations_and_atomic_failure(tmp_path):
    store, factory, _ = observed(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda index: store.reserve("future", f"concurrent-{index}", AGENT, REQUEST), range(16)))
    totals = store.inspect_scope()
    assert totals["api_calls"] == 16
    assert totals["accounted_nanodollars"] == 16 * LIMITS.reserved_nanodollars
    with factory() as connection:
        connection.execute(
            "CREATE TRIGGER reject_count BEFORE UPDATE ON itops_runtime_workflow "
            "BEGIN SELECT RAISE(ABORT,'offline failure'); END")
        before = list(connection.iterdump())
    with pytest.raises(sqlite3.IntegrityError, match="offline failure"):
        store.reserve("future", "failed-atomic", AGENT, REQUEST)
    with factory() as connection:
        assert list(connection.iterdump()) == before


@pytest.mark.parametrize("expired", [False, True])
def test_missing_or_expired_authorization_cannot_admit_or_dispatch(tmp_path, expired):
    store, factory, clock = observed(tmp_path)
    if expired:
        clock[0] += timedelta(hours=1)
    else:
        store.usage_authorization = None
    with factory() as connection:
        before = list(connection.iterdump())
    for attempt in (lambda: store.open_workflow("new"),
                    lambda: store.reserve("future", "blocked", AGENT, REQUEST),
                    lambda: store.record_tool("future", "blocked", "read_evidence")):
        with pytest.raises(ContractError, match="authorization"):
            attempt()
    with factory() as connection:
        assert list(connection.iterdump()) == before
    assert store.inspect_month()["caps_enforced"] is False


def test_deadline_overrun_and_settlement_after_expiry_remain_independent_of_caps(tmp_path):
    store, _, clock = observed(tmp_path)
    store.reserve("future", "valid", AGENT, REQUEST)
    clock[0] += timedelta(seconds=180)
    for attempt in (lambda: store.open_workflow("future"),
                    lambda: store.reserve("future", "late", AGENT, REQUEST),
                    lambda: store.record_tool("future", "late", "read_evidence")):
        with pytest.raises(BudgetExceeded, match="deadline"):
            attempt()
    clock[0] += timedelta(hours=1)
    assert store.settle("valid", 10, 20) == "settled"
    assert store.inspect_scope()["accounted_nanodollars"] == 100


@pytest.mark.parametrize("input_tokens,output_tokens", [(1001, 1), (1, 101)])
def test_observe_only_keeps_per_call_token_overruns_terminal(tmp_path, input_tokens, output_tokens):
    store, _, _ = observed(tmp_path)
    store.reserve("future", "overrun", AGENT, REQUEST)
    assert store.settle("overrun", input_tokens, output_tokens) == "overrun"
    with pytest.raises(BudgetExceeded, match="blocked"):
        store.reserve("future", "after-overrun", AGENT, REQUEST)
    assert store.inspect_scope()["accounted_nanodollars"] == input_tokens * 2 + output_tokens * 4


@pytest.mark.parametrize("mode", ["enforced", "observe_only"])
def test_embedding_gate_accounts_repeated_calls_and_retains_parent_deadline(tmp_path, mode):
    limits = replace(LIMITS, usage_mode=mode, monthly_nanodollars=100000, api_calls=1,
                     deadline_seconds=180)
    if mode == "observe_only":
        limits = replace(limits, monthly_nanodollars=1, workflow_tokens=1)
        store, _, _ = observed(tmp_path, policy=limits)
    else:
        store, _, _ = ledger(tmp_path, limits)
    remaining = [42]
    gate = BudgetLedgerEmbeddingGate(store, workflow_id="embedding-parent",
                                    parent_remaining_seconds=lambda: remaining[0])
    first = gate.reserve(operation_id="first", request_sha256=REQUEST, input_tokens_upper_bound=100)
    assert first.timeout_seconds == 42
    gate.settle(first, input_tokens=None)
    if mode == "enforced":
        with pytest.raises(BudgetExceeded):
            gate.reserve(operation_id="second", request_sha256=REQUEST, input_tokens_upper_bound=100)
    else:
        second = gate.reserve(operation_id="second", request_sha256=REQUEST, input_tokens_upper_bound=100)
        gate.settle(second, input_tokens=10)
        assert store.inspect_scope()["accounted_nanodollars"] == LIMITS.reserved_nanodollars + 20
        assert store.inspect_scope()["call_states"] == {"unknown": 1, "settled": 1}
    with pytest.raises(ReviewError, match="input"):
        gate.reserve(operation_id="too-large", request_sha256=REQUEST, input_tokens_upper_bound=1001)
    before = store.inspect_scope()
    remaining[0] = 0
    with pytest.raises(ReviewError, match="deadline"):
        gate.reserve(operation_id="late", request_sha256=REQUEST, input_tokens_upper_bound=100)
    assert store.inspect_scope() == before


def test_worker_and_publisher_share_accounting_not_policies_or_deadlines(tmp_path):
    factory = lambda: sqlite3.connect(tmp_path / "shared-embedding.db", isolation_level=None)
    clock = [datetime.now(timezone.utc)]
    scope = "us93-integration-embeddings-20260916-v1"
    worker_limits = replace(LIMITS, api_calls=3, deadline_seconds=180)
    publisher_limits = replace(LIMITS, api_calls=4, deadline_seconds=120)
    def build(limits, authorization=None):
        return BudgetLedger(factory, limits, scope, dialect="sqlite-local-test",
                            clock=lambda: clock[0], usage_authorization=authorization)
    old_worker, old_publisher = build(worker_limits), build(publisher_limits)
    old_worker.migrate()
    for store, name in ((old_worker, "worker"), (old_publisher, "publisher")):
        store.open_workflow("old-" + name)
        store.reserve("old-" + name, "old-call-" + name, AGENT, REQUEST)
        store.settle("old-call-" + name, *(() if name == "worker" else (10, 0)))
    historical = {name: store.inspect_workflow("old-" + name)
                  for name, store in (("worker", old_worker), ("publisher", old_publisher))}
    chat = replace(LIMITS, usage_mode="observe_only", deadline_seconds=180)
    current = {}
    for name, old in (("worker", worker_limits), ("publisher", publisher_limits)):
        limits = replace(old, usage_mode="observe_only", policy_version="observe-" + name)
        authorization = usage_authorization(limits, approved_usage(chat, limits), "embedding")
        store = current[name] = build(limits, authorization)
        assert store.inspect_workflow("old-" + name) == historical[name]
        with pytest.raises(IntegrityConflict):
            store.open_workflow("old-" + name)
        store.open_workflow("new-" + name)
        for number in range(5):
            invocation = f"new-{name}-{number}"
            store.reserve("new-" + name, invocation, AGENT, REQUEST)
            store.settle(invocation)
    assert current["worker"].inspect_scope() == current["publisher"].inspect_scope()
    assert current["worker"].inspect_scope()["accounted_nanodollars"] == 11 * LIMITS.reserved_nanodollars + 20
    assert current["worker"].limits.sha256 != current["publisher"].limits.sha256
    assert current["worker"].inspect_month()["stored_limit_nanodollars"] == LIMITS.monthly_nanodollars
    clock[0] += timedelta(seconds=121)
    with pytest.raises(BudgetExceeded, match="deadline"):
        current["publisher"].reserve("new-publisher", "late-publisher", AGENT, REQUEST)
    assert current["worker"].remaining_seconds("new-worker") == 59
    for name in ("worker", "publisher"):
        assert current[name].inspect_workflow("old-" + name) == historical[name]


def policies():
    chat = replace(LIMITS, usage_mode="observe_only", deadline_seconds=180)
    return chat, replace(chat, max_output_tokens=1)


def test_batch_envelope_requires_explicit_consistent_modes_hashes_and_approval():
    chat, embedding = policies()
    configured = approved_usage(chat, embedding)
    configured["limits"].update(
        max_total_nanodollars=1, max_total_model_calls=1, max_total_dispatches=1, max_total_embedding_calls=1)
    validate_batch_budgets(chat, embedding, configured["limits"], configured=configured)
    assert usage_authorization(chat, configured, "chat").policy_hash == chat.sha256
    assert usage_authorization(embedding, configured, "embedding").policy_hash == embedding.sha256
    with pytest.raises(ContractError):
        validate_batch_budgets(chat, embedding, configured["limits"])
    with pytest.raises(ContractError):
        validate_batch_budgets(chat, replace(embedding, usage_mode="enforced"),
                               configured["limits"], configured=configured)
    with pytest.raises(ContractError):
        validate_batch_budgets(chat, embedding, DEFAULT_BATCH_LIMITS, configured=configured)


@pytest.mark.parametrize("mutation", [
    lambda a: a.pop("usage_policy"),
    lambda a: a.pop("limits"),
    lambda a: a["limits"].pop("usage_mode"),
    lambda a: a["usage_policy"].update(chat_policy_sha256="f" * 64),
    lambda a: a["usage_policy"].update(embedding_policy_sha256="f" * 64),
    lambda a: a["usage_policy"].update(mode="enforced"),
    lambda a: a["usage_policy"].update(extra=True),
    lambda a: a.update(expires_at_utc="2000-01-01T00:00:00Z"),
    lambda a: a.update(actor_oid="not-an-actor"),
    lambda a: a["limits"].update(max_workflow_seconds=179),
])
def test_batch_authorization_mismatches_fail_closed(mutation):
    chat, embedding = policies()
    configured = approved_usage(chat, embedding)
    mutation(configured)
    with pytest.raises((ContractError, ReviewError)):
        checked = approval(configured)
        validate_batch_budgets(chat, embedding, checked.get("limits"), configured=checked)


def test_observe_only_approval_cannot_be_silently_used_with_enforced_policies():
    chat, embedding = policies()
    configured = approved_usage(chat, embedding)
    with pytest.raises(ContractError):
        validate_batch_budgets(replace(chat, usage_mode="enforced"), replace(embedding, usage_mode="enforced"),
                               configured=configured)
    for invalid in ({**DEFAULT_BATCH_LIMITS, "usage_mode": True},
                    {**DEFAULT_BATCH_LIMITS, "max_workflow_seconds": 181}):
        with pytest.raises(ContractError):
            batch_limits(invalid)


@pytest.mark.parametrize("mode", ["enforced", "observe_only"])
def test_remote_budget_contract_truthfully_reports_effective_enforcement(tmp_path, mode):
    store = observed(tmp_path)[0] if mode == "observe_only" else ledger(tmp_path)[0]
    receipt = {"job_id": "a" * 64, "chat": {"status": "not_admitted"},
               "embedding": {"status": "not_admitted"},
               "scope_totals": {"chat": store.inspect_scope(), "embedding": store.inspect_scope()},
               "monthly_limits": {"chat": store.inspect_month(), "embedding": store.inspect_month()}, **OVERLAY}
    def read(value):
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=value))) as client:
            remote = RemoteWorkAdvancer("https://offline.example", SimpleNamespace(
                get_token=lambda _: SimpleNamespace(token="offline")), client, token_scope="api://offline/.default")
            return remote.budget("a" * 64)
    assert read(receipt) == receipt
    for mutation in (
            lambda m: m.update(caps_enforced=mode == "observe_only"),
            lambda m: m.update(policy_hash="wrong"),
            lambda m: m.update(effective_limit_nanodollars=1 if mode == "observe_only" else None),
            lambda m: m.pop("usage_mode")):
        changed = deepcopy(receipt)
        mutation(changed["monthly_limits"]["chat"])
        with pytest.raises(ReviewError):
            read(changed)


def test_runtime_parse_retains_deadline_output_and_legacy_budget_fields(tmp_path):
    from agentic_itops.service_runtime import RuntimeConfiguration
    from tests.test_application_integration import configuration, integrated
    document = configuration(integrated(tmp_path))
    document["budget"].pop("usage_mode")
    assert RuntimeConfiguration.parse(document).budget.caps_enforced
    document["budget"].update(usage_mode="observe_only", max_output_tokens=8192, model_context_tokens=16000,
                               deadline_seconds=180)
    parsed = RuntimeConfiguration.parse(document)
    assert not parsed.budget.caps_enforced and parsed.budget.max_output_tokens == 8192
    for field, value in (("deadline_seconds", 181), ("max_output_tokens", 8193)):
        bad = deepcopy(document)
        bad["budget"][field] = value
        with pytest.raises(ContractError):
            RuntimeConfiguration.parse(bad)


def test_embedding_factory_binds_both_authorizations_before_any_provider_or_sql(tmp_path, monkeypatch):
    import agentic_itops.service_search as module
    chat, embedding = policies()
    @dataclass
    class Settings:
        snapshot_id: str = "offline-snapshot"
        allowed_sources: frozenset = frozenset({("producer", "run")})
        timeout_seconds: int = 20
    monkeypatch.setattr(module, "SearchServiceSettings",
                        SimpleNamespace(from_environment=lambda _: Settings()))
    def unopened(*args):
        def forbidden():
            raise AssertionError("factory cannot access SQL")
        return forbidden
    monkeypatch.setattr(module, "managed_identity_sql_factory", unopened)
    config = SimpleNamespace(budget=chat, scope=SimpleNamespace(
        producer=SimpleNamespace(key="producer"), run_id="run"))
    configured = approved_usage(chat, embedding)
    environment = {
        "ITOPS_EMBEDDING_BUDGET_JSON": json.dumps(asdict(embedding)),
        "ITOPS_OPERATOR_INVESTIGATION_APPROVAL_JSON": json.dumps(configured),
        "ITOPS_EMBEDDING_BUDGET_SCOPE": "unchanged-embedding",
        "ITOPS_PROJECT_ENDPOINT": "https://offline.services.ai.azure.com/api/projects/itops",
        "ITOPS_SQL_SERVER": "offline.database.windows.net", "ITOPS_SQL_DATABASE": "offline",
        "AZURE_CLIENT_ID": ACTOR,
    }
    binding = module.approved_search_binding("offline-snapshot")
    reader = module.create_workflow_knowledge(environment, object(), object(), config, binding)
    assert reader.ledger.usage_authorization.policy_hash == embedding.sha256
    assert reader.parent_ledger.usage_authorization.policy_hash == chat.sha256
    del environment["ITOPS_OPERATOR_INVESTIGATION_APPROVAL_JSON"]
    with pytest.raises(ContractError, match="approval"):
        module.create_workflow_knowledge(environment, object(), object(), config, binding)


@pytest.mark.parametrize("mode", ["enforced", "observe_only"])
def test_complete_chat_portfolio_exceeds_tiny_caps_only_when_authorized(tmp_path, mode):
    from tests.test_portfolio_runtime import analyze, setup
    runtime, transport, reference, scope, _ = setup(tmp_path)
    limits = replace(runtime.ledger.limits, usage_mode=mode, policy_version="offline-usage-v1",
                     monthly_nanodollars=1, workflow_tokens=1, api_calls=1, tool_calls=1,
                     deadline_seconds=180, model_context_tokens=16000, max_output_tokens=8192)
    runtime.ledger.limits = limits
    if mode == "enforced":
        with pytest.raises(BudgetExceeded):
            analyze(runtime, reference, scope)
        assert not transport.calls
        return
    embedding = replace(limits, max_output_tokens=1)
    runtime.ledger.usage_authorization = usage_authorization(
        limits, approved_usage(limits, embedding), "chat")
    seen = []
    original = transport.respond
    def respond(agent, history, max_output_tokens, timeout_seconds):
        seen.append((max_output_tokens, timeout_seconds))
        return original(agent, history, max_output_tokens, timeout_seconds)
    transport.respond = respond
    result = analyze(runtime, reference, scope)
    total = runtime.ledger.inspect_scope()
    assert total["api_calls"] == len(transport.calls) > limits.api_calls
    assert total["tool_calls"] == 4 > limits.tool_calls
    assert total["accounted_nanodollars"] == len(transport.calls) * 60 > limits.monthly_nanodollars
    assert total["call_states"] == {"settled": len(transport.calls)}
    assert result["versions"]["budget_policy_sha256"] == limits.sha256
    assert result["execution_allowed"] is False
    assert all(tokens == 8192 and 0 < seconds <= 60 for tokens, seconds in seen)
    assert all(transport.citation_scopes)
