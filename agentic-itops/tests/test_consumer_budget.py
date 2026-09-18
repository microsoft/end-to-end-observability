from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

pytest.importorskip("jsonschema", reason="install requirements-consumer.txt")
pytest.importorskip("httpx", reason="install requirements-consumer.txt")

from agentic_itops.consumer.budget import BudgetExceeded, BudgetLedger, BudgetLimits, MonthlyLimitAmendment
from agentic_itops.consumer.contracts import ContractError
from agentic_itops.consumer.storage_contract import IntegrityConflict
from agentic_itops.foundry_contracts import AgentVersionReference

LIMITS = BudgetLimits("fixture-v1", "offline fictional test prices", 1000, 100, 2, 4,
                      4800, 10000, api_calls=10, tool_calls=2)
AGENT = AgentVersionReference("ops-orchestrator", "1")
REQUEST = "a" * 64


def ledger(tmp_path, limits=LIMITS):
    path = tmp_path / "budget.db"
    clock = [datetime(2026, 9, 15, tzinfo=timezone.utc)]
    factory = lambda: sqlite3.connect(path, timeout=10, isolation_level=None)
    store = BudgetLedger(factory, limits, "test-project", dialect="sqlite-local-test",
                         clock=lambda: clock[0])
    store.migrate()
    store.open_workflow("workflow")
    return store, factory, clock


def test_reservation_precedes_attempt_and_unknown_usage_never_releases_it(tmp_path):
    store, factory, _ = ledger(tmp_path)
    store.reserve("workflow", "call-1", AGENT, REQUEST)
    assert store.settle("call-1") == "unknown"
    store.reserve("workflow", "call-2", AGENT, REQUEST)
    with pytest.raises(BudgetExceeded):
        store.reserve("workflow", "call-3", AGENT, REQUEST)
    with factory() as connection:
        assert connection.execute("SELECT accounted_nanos FROM itops_runtime_month").fetchone()[0] == 4800
    with pytest.raises(IntegrityConflict):
        store.settle("call-1", 10, 10)


def test_inspection_reads_expired_unknown_usage_without_resetting_any_record(tmp_path):
    store, factory, clock = ledger(tmp_path)
    store.reserve("workflow", "call-1", AGENT, REQUEST)
    store.settle("call-1")
    clock[0] += timedelta(days=1)
    with factory() as connection:
        before = list(connection.iterdump())
    summary = store.inspect_workflow("workflow")
    assert summary["status"] == "admitted"
    assert summary["api_calls"] == 1 and summary["tool_calls"] == 0
    assert summary["call_states"] == {"unknown": 1}
    assert summary["accounted_nanodollars"] == LIMITS.reserved_nanodollars
    assert summary["policy_hash"] == LIMITS.sha256
    assert store.inspect_workflow("never-admitted") == {"status": "not_admitted"}
    other = BudgetLedger(factory, LIMITS, "another-scope", dialect="sqlite-local-test")
    assert other.inspect_workflow("workflow") == {"status": "not_admitted"}
    with factory() as connection:
        assert list(connection.iterdump()) == before
    with pytest.raises(BudgetExceeded):
        store.open_workflow("workflow")


def test_inspection_reports_historical_policy_not_current_pricing(tmp_path):
    store, factory, _ = ledger(tmp_path)
    store.reserve("workflow", "call-1", AGENT, REQUEST)
    changed = BudgetLedger(factory, replace(LIMITS, api_calls=1), "test-project",
                           dialect="sqlite-local-test")
    assert changed.inspect_workflow("workflow")["policy_hash"] == LIMITS.sha256
    with pytest.raises(ContractError):
        changed.inspect_workflow("not a ledger identifier")


def test_narrowed_future_policy_preserves_prior_workflow_and_shared_charges(tmp_path):
    original = replace(LIMITS, api_calls=4, monthly_nanodollars=10000)
    store, factory, clock = ledger(tmp_path, original)
    store.reserve("workflow", "old-unknown", AGENT, REQUEST)
    store.settle("old-unknown")
    before = store.inspect_workflow("workflow")
    narrowed = replace(original, api_calls=3, policy_version="fixture-reserve3")
    future = BudgetLedger(factory, narrowed, "test-project", dialect="sqlite-local-test",
                          clock=lambda: clock[0])
    with pytest.raises(IntegrityConflict, match="policy cannot change"):
        future.open_workflow("workflow")
    future.open_workflow("future")
    for number in range(3):
        invocation = f"future-{number}"
        future.reserve("future", invocation, AGENT, REQUEST)
        future.settle(invocation, 10, 10)
    with pytest.raises(BudgetExceeded):
        future.reserve("future", "fourth", AGENT, REQUEST)
    assert future.inspect_workflow("workflow") == before
    summary = future.inspect_scope()
    assert summary["api_calls"] == 4
    assert summary["call_states"] == {"unknown": 1, "settled": 3}
    assert summary["accounted_nanodollars"] == original.reserved_nanodollars + 3 * 60
    with factory() as connection:
        assert connection.execute("SELECT accounted_nanos FROM itops_runtime_month").fetchall() == [
            (original.reserved_nanodollars + 3 * 60,)]


def test_larger_future_policy_does_not_raise_existing_month_limit_or_release_charges(tmp_path):
    store, factory, clock = ledger(tmp_path)
    for number in range(2):
        invocation = f"prior-{number}"
        store.reserve("workflow", invocation, AGENT, REQUEST)
        store.settle(invocation)
    before = store.inspect_workflow("workflow")
    expanded = BudgetLedger(
        factory, replace(LIMITS, monthly_nanodollars=9600, policy_version="fixture-expanded"),
        "test-project", dialect="sqlite-local-test", clock=lambda: clock[0])
    with pytest.raises(IntegrityConflict, match="policy cannot change"):
        expanded.open_workflow("workflow")
    expanded.open_workflow("future")
    with pytest.raises(BudgetExceeded, match="durable budget"):
        expanded.reserve("future", "not-admitted", AGENT, REQUEST)
    assert expanded.inspect_workflow("workflow") == before
    assert expanded.inspect_scope()["accounted_nanodollars"] == 4800
    assert expanded.inspect_scope()["call_states"] == {"unknown": 2}
    with factory() as connection:
        assert connection.execute(
            "SELECT limit_nanos,accounted_nanos FROM itops_runtime_month").fetchall() == [(4800, 4800)]


def amendment_fixture(tmp_path, *, unresolved=False):
    store, factory, clock = ledger(tmp_path)
    for number in range(2):
        invocation = f"prior-{number}"
        store.reserve("workflow", invocation, AGENT, REQUEST)
        if not unresolved or number == 0:
            store.settle(invocation)
    limits = replace(LIMITS, monthly_nanodollars=9600, policy_version="approved-expansion")
    amendment = MonthlyLimitAmendment(
        "approved-expansion", "future", "test-project", "2026-09", limits.sha256,
        4800, 9600, 4800, (clock[0] + timedelta(hours=1)).isoformat())
    return store, factory, clock, limits, amendment


def test_explicit_month_amendment_preserves_charges_and_old_policies(tmp_path, caplog):
    store, factory, clock, limits, amendment = amendment_fixture(tmp_path)
    prior = store.inspect_workflow("workflow")
    future = BudgetLedger(factory, limits, "test-project", dialect="sqlite-local-test",
                          clock=lambda: clock[0], monthly_limit_amendment=amendment)
    assert future.inspect_month()["effective_limit_nanodollars"] == 4800
    with caplog.at_level("INFO"):
        future.open_workflow("future")
    assert "Approved monthly limit amendment committed" in caplog.text
    assert future.inspect_workflow("workflow") == prior
    assert future.inspect_scope()["accounted_nanodollars"] == 4800
    assert future.inspect_month()["stored_limit_nanodollars"] == 9600
    assert future.inspect_month()["effective_limit_nanodollars"] == 9600
    assert store.inspect_month()["effective_limit_nanodollars"] == 4800
    with factory() as connection:
        before = list(connection.iterdump())
    future.open_workflow("future")
    with factory() as connection:
        assert list(connection.iterdump()) == before
    with pytest.raises(BudgetExceeded):
        store.reserve("workflow", "old-policy-cannot-use-increase", AGENT, REQUEST)
    for number in range(2):
        invocation = f"future-{number}"
        future.reserve("future", invocation, AGENT, REQUEST)
        future.settle(invocation)
    with pytest.raises(BudgetExceeded):
        future.reserve("future", "above-new-limit", AGENT, REQUEST)
    assert future.inspect_scope()["call_states"] == {"unknown": 4}
    assert future.inspect_scope()["accounted_nanodollars"] == 9600
    clock[0] += timedelta(seconds=limits.deadline_seconds)
    with pytest.raises(BudgetExceeded, match="cannot reset"):
        future.open_workflow("future")
    assert future.inspect_workflow("workflow") == prior


@pytest.mark.parametrize("mutation", [
    {"workflow_id": "another"}, {"month_key": "2026-08"},
    {"expires_at_utc": "2026-09-14T00:00:00Z"},
    {"expected_limit_nanodollars": 4900}, {"expected_accounted_nanodollars": 4700},
])
def test_amendment_rejects_wrong_workflow_month_expiry_or_baseline_without_writes(tmp_path, mutation):
    _, factory, clock, limits, amendment = amendment_fixture(tmp_path)
    future = BudgetLedger(factory, limits, "test-project", dialect="sqlite-local-test",
                          clock=lambda: clock[0], monthly_limit_amendment=replace(amendment, **mutation))
    with factory() as connection:
        before = list(connection.iterdump())
    with pytest.raises(IntegrityConflict):
        future.open_workflow("future")
    with factory() as connection:
        assert list(connection.iterdump()) == before


@pytest.mark.parametrize("mutation", [
    {"approval_id": "different-policy"}, {"scope_key": "other-scope"},
    {"policy_hash": "b" * 64}, {"new_limit_nanodollars": 9700},
])
def test_amendment_cannot_override_configured_scope_or_policy(tmp_path, mutation):
    _, factory, clock, limits, amendment = amendment_fixture(tmp_path)
    with pytest.raises(ContractError):
        BudgetLedger(factory, limits, "test-project", dialect="sqlite-local-test",
                     clock=lambda: clock[0], monthly_limit_amendment=replace(amendment, **mutation))


def test_amendment_does_not_accept_unreconciled_reservations(tmp_path):
    _, factory, clock, limits, amendment = amendment_fixture(tmp_path, unresolved=True)
    future = BudgetLedger(factory, limits, "test-project", dialect="sqlite-local-test",
                          clock=lambda: clock[0], monthly_limit_amendment=amendment)
    with factory() as connection:
        before = list(connection.iterdump())
    with pytest.raises(IntegrityConflict, match="reconciled"):
        future.open_workflow("future")
    with factory() as connection:
        assert list(connection.iterdump()) == before


def test_month_amendment_and_new_workflow_commit_atomically(tmp_path):
    _, factory, clock, limits, amendment = amendment_fixture(tmp_path)
    with factory() as connection:
        connection.execute(
            "CREATE TRIGGER reject_future BEFORE INSERT ON itops_runtime_workflow "
            "WHEN NEW.workflow_id='future' BEGIN SELECT RAISE(ABORT,'unit failure'); END")
        before = list(connection.iterdump())
    future = BudgetLedger(factory, limits, "test-project", dialect="sqlite-local-test",
                          clock=lambda: clock[0], monthly_limit_amendment=amendment)
    with pytest.raises(sqlite3.IntegrityError, match="unit failure"):
        future.open_workflow("future")
    with factory() as connection:
        assert list(connection.iterdump()) == before


def test_month_inspection_does_not_create_a_period_or_apply_amendment(tmp_path):
    store, factory, _ = ledger(tmp_path)
    with factory() as connection:
        before = list(connection.iterdump())
    assert store.inspect_month() == {
        "month_key": "2026-09", "configured_limit_nanodollars": 4800,
        "stored_limit_nanodollars": None, "effective_limit_nanodollars": 4800,
        "accounted_nanodollars": 0, "usage_mode": "enforced",
        "caps_enforced": True, "policy_hash": LIMITS.sha256}
    with factory() as connection:
        assert list(connection.iterdump()) == before


def test_dispatch_diagnostics_are_read_only_and_scope_bound(tmp_path):
    store, factory, _ = ledger(tmp_path)
    store.reserve("workflow", "unknown", AGENT, REQUEST)
    store.settle("unknown")
    store.record_tool("workflow", "dispatch-one", "delegate_incident_comms")
    with factory() as connection:
        before = list(connection.iterdump())
    summary = store.inspect_workflow("workflow", include_dispatches=True)
    assert summary["dispatch_diagnostic"]["provider_calls_by_agent"] == {"ops-orchestrator": {"unknown": 1}}
    assert summary["dispatch_diagnostic"]["recorded_dispatches_by_tool"] == {"delegate_incident_comms": 1}
    other = BudgetLedger(factory, LIMITS, "foreign", dialect="sqlite-local-test")
    assert other.inspect_workflow("workflow", include_dispatches=True) == {"status": "not_admitted"}
    with factory() as connection:
        assert list(connection.iterdump()) == before


def test_scope_inspection_includes_history_without_reset_or_foreign_scope(tmp_path):
    store, factory, clock = ledger(tmp_path)
    store.reserve("workflow", "unknown", AGENT, REQUEST)
    store.settle("unknown")
    store.record_tool("workflow", "tool-one", "read_evidence")
    clock[0] += timedelta(days=32)
    store.open_workflow("later")
    store.reserve("later", "settled", AGENT, REQUEST)
    store.settle("settled", 10, 20)
    other = BudgetLedger(factory, LIMITS, "another-scope", dialect="sqlite-local-test")
    other.open_workflow("foreign")
    other.reserve("foreign", "foreign-call", AGENT, REQUEST)
    with factory() as connection:
        before = list(connection.iterdump())
    current = BudgetLedger(factory, replace(LIMITS, api_calls=1), "test-project", dialect="sqlite-local-test")
    assert current.inspect_scope() == {
        "scope_basis": "all_history_for_configured_scope", "workflow_count": 2,
        "api_calls": 2, "tool_calls": 1, "call_states": {"settled": 1, "unknown": 1},
        "accounted_nanodollars": LIMITS.reserved_nanodollars + 100,
        "accounting_basis": "conservative_reservations_not_a_billing_invoice",
    }
    with factory() as connection:
        assert list(connection.iterdump()) == before


def test_scope_inspection_handles_no_history_and_rejects_counter_drift(tmp_path):
    store, factory, _ = ledger(tmp_path)
    assert store.inspect_scope()["api_calls"] == 0
    foreign = BudgetLedger(factory, LIMITS, "empty", dialect="sqlite-local-test")
    assert foreign.inspect_scope()["workflow_count"] == 0
    with factory() as connection:
        connection.execute("UPDATE itops_runtime_workflow SET api_calls=1")
    with pytest.raises(IntegrityConflict, match="scope counters"):
        store.inspect_scope()


def test_known_usage_refunds_only_unused_reservation_and_is_idempotent(tmp_path):
    store, factory, _ = ledger(tmp_path)
    store.reserve("workflow", "call-1", AGENT, REQUEST)
    assert store.settle("call-1", 10, 20) == "settled"
    assert store.settle("call-1", 10, 20) == "settled"
    with factory() as connection:
        assert connection.execute("SELECT accounted_nanos FROM itops_runtime_month").fetchone()[0] == 100
        assert connection.execute("SELECT api_calls,tokens_accounted FROM itops_runtime_workflow").fetchone() == (1, 30)
    with pytest.raises(IntegrityConflict):
        store.reserve("workflow", "call-1", AGENT, REQUEST)


def test_restart_and_new_workflow_cannot_reset_monthly_budget(tmp_path):
    store, factory, clock = ledger(tmp_path)
    store.reserve("workflow", "call-1", AGENT, REQUEST)
    store.reserve("workflow", "call-2", AGENT, REQUEST)
    restarted = BudgetLedger(factory, LIMITS, "test-project", dialect="sqlite-local-test",
                             clock=lambda: clock[0])
    restarted.open_workflow("another")
    with pytest.raises(BudgetExceeded):
        restarted.reserve("another", "call-3", AGENT, REQUEST)


def test_concurrent_reservations_do_not_overspend(tmp_path):
    store, factory, _ = ledger(tmp_path)
    def attempt(index):
        try:
            store.reserve("workflow", f"call-{index}", AGENT, REQUEST)
            return True
        except BudgetExceeded:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(12))) == 2
    with factory() as connection:
        assert connection.execute("SELECT COUNT(*) FROM itops_runtime_call").fetchone()[0] == 2


def test_deadline_tool_and_api_limits_are_durable(tmp_path):
    store, _, clock = ledger(tmp_path, replace(LIMITS, api_calls=1))
    store.record_tool("workflow", "tool-1", "delegate_rca_investigator")
    with pytest.raises(IntegrityConflict):
        store.record_tool("workflow", "tool-1", "delegate_rca_investigator")
    store.record_tool("workflow", "tool-2", "delegate_incident_comms")
    with pytest.raises(BudgetExceeded):
        store.record_tool("workflow", "tool-3", "delegate_incident_comms")
    store.reserve("workflow", "call-1", AGENT, REQUEST)
    store.settle("call-1", 1, 1)
    with pytest.raises(BudgetExceeded):
        store.reserve("workflow", "call-2", AGENT, REQUEST)
    clock[0] += timedelta(seconds=601)
    with pytest.raises(BudgetExceeded):
        store.open_workflow("workflow")


def test_overrun_is_recorded_not_rolled_back_and_blocks_more_calls(tmp_path):
    store, factory, _ = ledger(tmp_path)
    store.reserve("workflow", "call-1", AGENT, REQUEST)
    assert store.settle("call-1", 1001, 1) == "overrun"
    with factory() as connection:
        assert connection.execute("SELECT accounted_nanos FROM itops_runtime_month").fetchone()[0] == 2006
    with pytest.raises(BudgetExceeded, match="blocked"):
        store.reserve("workflow", "call-2", AGENT, REQUEST)


def test_changed_pricing_or_scope_cannot_reinterpret_prior_spend(tmp_path):
    store, factory, _ = ledger(tmp_path)
    store.reserve("workflow", "call-1", AGENT, REQUEST)
    changed = BudgetLedger(factory, replace(LIMITS, input_nanodollars_per_token=1),
                           "test-project", dialect="sqlite-local-test")
    with pytest.raises(IntegrityConflict):
        changed.settle("call-1", 10, 10)
    changed_scope = BudgetLedger(factory, LIMITS, "another-project", dialect="sqlite-local-test")
    with pytest.raises(IntegrityConflict):
        changed_scope.open_workflow("workflow")
    with pytest.raises(IntegrityConflict):
        changed_scope.settle("call-1", 10, 10)


def test_invalid_usage_retains_the_full_reservation(tmp_path):
    store, factory, _ = ledger(tmp_path)
    store.reserve("workflow", "call-1", AGENT, REQUEST)
    for values in [(None, 1), (True, 1), (-1, 0)]:
        with pytest.raises(ContractError):
            store.settle("call-1", *values)
    with factory() as connection:
        assert connection.execute("SELECT accounted_nanos FROM itops_runtime_month").fetchone()[0] == 2400
