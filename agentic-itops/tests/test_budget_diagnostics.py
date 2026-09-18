from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from agentic_itops.consumer.contracts import ContractError
from agentic_itops.review import Principal, SQLiteReviewRepository, create_app
from agentic_itops.review.contracts import OVERLAY, ReviewError
from agentic_itops.review.search_delivery import BudgetLedgerEmbeddingGate
from agentic_itops.service_runtime import WorkerBudgetDiagnostics
from tests.test_consumer_budget import LIMITS
from tests.test_review import OID, ORIGIN, SOURCE, FakeQueue

JOB = "a" * 64


def worker_fixture():
    calls = []
    job = {"kind": "reasoning", "payload": {"source": deepcopy(SOURCE), **OVERLAY}, "status": "failed"}

    class Auth:
        def authenticate(self, headers, role):
            assert role == "Worker.Invoke"
            if headers != {"Authorization": "unit"}:
                raise ReviewError("authentication required", 401)

    def inspect(name, workflow):
        calls.append((name, workflow))
        return {"status": "not_admitted"}

    chat = SimpleNamespace(inspect_workflow=lambda key, **kwargs: inspect("chat", key),
                           inspect_scope=lambda: {"api_calls": 3}, inspect_month=lambda: {"unit": "chat"})
    embedding = SimpleNamespace(scope="shared-embedding",
                                inspect_workflow=lambda key: inspect("embedding", key),
                                inspect_scope=lambda: {"api_calls": 1}, inspect_month=lambda: {"unit": "embedding"})
    service = WorkerBudgetDiagnostics(
        SimpleNamespace(producer=SimpleNamespace(key=SOURCE["producer_key"]), run_id=SOURCE["run_id"]),
        SimpleNamespace(get_job=lambda _: job), Auth(), chat, embedding)
    return service, calls, job


def test_worker_inspection_uses_existing_auth_and_distinct_embedding_identity():
    service, calls, _ = worker_fixture()
    with pytest.raises(ReviewError):
        service.read({}, JOB)
    assert calls == []
    result = service.read({"Authorization": "unit"}, JOB)
    assert result["job_id"] == JOB and result["execution_allowed"] is False
    assert result["scope_totals"] == {"chat": {"api_calls": 3}, "embedding": {"api_calls": 1}}
    assert result["monthly_limits"] == {"chat": {"unit": "chat"}, "embedding": {"unit": "embedding"}}
    assert calls == [
        ("chat", JOB),
        ("embedding", BudgetLedgerEmbeddingGate(service.embedding, workflow_id=JOB).workflow_id),
    ]


def test_archive_budget_inspection_keeps_the_same_ledger_identities_and_totals():
    service, calls, job = worker_fixture()
    archived_scope = service.scope
    service.scope = SimpleNamespace(producer=archived_scope.producer, run_id="active-run")
    service.archives = (SimpleNamespace(
        scope=archived_scope, retained_until=datetime.now(timezone.utc) + timedelta(days=1)),)
    chat, embedding = service.chat, service.embedding
    result = service.read({"Authorization": "unit"}, JOB)
    assert service.chat is chat and service.embedding is embedding
    assert calls == [
        ("chat", JOB), ("embedding", BudgetLedgerEmbeddingGate(embedding, workflow_id=JOB).workflow_id)]
    assert result["scope_totals"] == {"chat": {"api_calls": 3}, "embedding": {"api_calls": 1}}
    assert job["status"] == "failed"
    service.archives[0].retained_until = datetime(2000, 1, 1, tzinfo=timezone.utc)
    calls.clear()
    with pytest.raises(ContractError, match="archived evidence retention"):
        service.read({"Authorization": "unit"}, JOB)
    assert not calls


@pytest.mark.parametrize("mutation", [
    lambda job: job["payload"]["source"].update(run_id="foreign"),
    lambda job: job["payload"]["source"].update(producer_key="foreign"),
    lambda job: job["payload"].update(execution_allowed=True),
    lambda job: job.update(kind="analysis"),
])
def test_worker_inspection_rejects_foreign_or_non_model_work(mutation):
    service, calls, job = worker_fixture()
    mutation(job)
    with pytest.raises(ContractError):
        service.read({"Authorization": "unit"}, JOB)
    assert calls == []


def test_diagnostics_factory_does_not_validate_expired_model_approval_or_construct_models(monkeypatch):
    import json
    from agentic_itops import service_runtime as module

    def forbidden(*args, **kwargs):
        raise AssertionError("diagnostics must not initialize a model or open a budget")

    config = SimpleNamespace(scope=object(), budget=LIMITS)
    monkeypatch.setattr(module, "_common", lambda *args: (config, None, None, forbidden, object(), object(), None))
    monkeypatch.setattr(module, "create_worker_service", forbidden)
    service = module.create_worker_budget_diagnostics({
        "ITOPS_PROJECT_ENDPOINT": "https://unit.services.ai.azure.com/api/projects/itops",
        "ITOPS_EMBEDDING_BUDGET_JSON": json.dumps(asdict(LIMITS)),
        "ITOPS_EMBEDDING_BUDGET_SCOPE": "shared-embedding",
        "ITOPS_OPERATOR_INVESTIGATION_APPROVAL_JSON": '{"expires_at_utc":"2000-01-01T00:00:00Z"}',
        "ITOPS_CHAT_LIMIT_AMENDMENT_JSON": "invalid-and-must-not-be-used-by-read-only-diagnostics",
    })
    assert service.chat.scope != service.embedding.scope
    assert service.chat.monthly_limit_amendment is None


@pytest.mark.parametrize("roles,foreign,kind,status", [
    ({"Operator"}, False, "reasoning", 200),
    ({"Viewer"}, False, "reasoning", 403),
    ({"Analyst"}, False, "reasoning", 403),
    ({"Operator"}, True, "reasoning", 403),
    ({"Operator"}, False, "analysis", 409),
])
@pytest.mark.parametrize("route", ["job-budgets", "job-readiness"])
def test_public_diagnostic_lookup_is_operator_and_source_scoped(roles, foreign, kind, status, route):
    calls = []
    queue = FakeQueue()
    source = deepcopy(SOURCE)
    if foreign:
        source["run_id"] = "foreign"
    queue.jobs[JOB] = {
        "kind": kind, "payload": {"source": source, **OVERLAY}, "status": "failed",
        "error_code": "PortfolioError",
        "error_message": "PortfolioError: industry-signal-pack: citation is not in trusted evidence",
    }
    repo = SQLiteReviewRepository()
    try:
        app = create_app({
            "TESTING": True, "PUBLIC_ORIGIN": ORIGIN, "SECRET_KEY": "offline-test-only-" * 4,
            "TEST_AUTHENTICATOR": SimpleNamespace(
                authenticate=lambda _: Principal(OID, frozenset(roles), "bearer")),
        }, repository=repo, work_submitter=queue,
            work_advancer=SimpleNamespace(budget=lambda key: calls.append(key) or {
                "job_id": key, "chat": {"status": "not_admitted"},
                "embedding": {"status": "not_admitted"}, **OVERLAY},
                readiness=lambda key: calls.append(key) or {"job_id": key, **OVERLAY}),
            entitlements={OID: {(SOURCE["producer_key"], SOURCE["run_id"])}},
            source_catalog={(SOURCE["producer_key"], SOURCE["run_id"]): SOURCE})
        response = app.test_client().get("/" + route + "/" + JOB)
        assert response.status_code == status
        assert calls == ([JOB] if status == 200 else [])
        if status == 200 and route == "job-budgets":
            assert response.json["failure_diagnostic"] == {
                "status": "classified", "error_code": "PortfolioError",
                "agent": "industry-signal-pack", "reason": "citation is not in trusted evidence",
            }
    finally:
        repo.close()


@pytest.mark.parametrize("message", [
    "secret-value",
    "PortfolioError: industry-signal-pack: citation is not in trusted evidence secret-value",
    "PortfolioError: secret-value: citation is not in trusted evidence",
    "PortfolioError: industry-signal-pack: invalid stage output: secret-value",
])
def test_failure_diagnostic_never_echoes_unclassified_exception_text(message):
    from agentic_itops.review.diagnostics import failure_diagnostic
    assert failure_diagnostic({"status": "failed", "error_code": "PortfolioError",
                               "error_message": message}) == {
        "status": "unclassified_failure", "error_code": "PortfolioError",
    }


def test_failure_diagnostic_does_not_echo_unknown_error_class():
    from agentic_itops.review.diagnostics import failure_diagnostic
    assert failure_diagnostic({"status": "failed", "error_code": "secret-value"}) == {
        "status": "unclassified_failure", "error_code": "unclassified",
    }


def test_dashboard_signin_renewal_is_fixed_same_origin_and_does_not_replay_requests():
    from pathlib import Path
    template = (Path(__file__).parents[1] / "agentic_itops" / "review" /
                "templates" / "dashboard.html").read_text("utf-8")
    assert '<a href="/.auth/login/aad?post_login_redirect_uri=%2F">Renew sign-in</a>' in template
