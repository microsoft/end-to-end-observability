from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace

import httpx
import pytest

from agentic_itops.consumer.contracts import ContractError, canonical, digest
from agentic_itops.consumer.storage_contract import EvidenceRef
from agentic_itops.review.advancement import RemoteWorkAdvancer
from agentic_itops.review.contracts import OVERLAY, ReviewError
from agentic_itops.service_runtime import WorkerDependencyReadiness
from tests.test_budget_diagnostics import JOB, worker_fixture
from tests.test_review import SOURCE, OID


def probe_receipt():
    return {
        "status": "verified", "probe_kind": "fixed_vector_contract_only",
        "request_id": "readiness-wire-request", "returned_hit_count": 0,
        "inference_performed": False, "embedding_performed": False,
    }


def projection_receipt():
    return {"status": "verified", "required_role": "Results.Project",
            "probe_kind": "projection_authentication_only",
            "inference_performed": False, "embedding_performed": False,
            "result_written": False}


def readiness_fixture():
    budget, _, job = worker_fixture()
    candidate = {"source": deepcopy(SOURCE), "incident_id": "incident-one", **OVERLAY}
    raw = canonical(candidate)
    reference = EvidenceRef("evidence/" + digest(candidate) + ".json", digest(candidate), len(raw))
    job["payload"].update(candidate_evidence=asdict(reference), incident_id=candidate["incident_id"])
    calls = []
    evidence = SimpleNamespace(read=lambda ref: calls.append(("evidence", ref)) or raw)
    service = WorkerDependencyReadiness(
        budget.scope, budget.store, budget.auth, evidence,
        lambda: calls.append(("search",)) or probe_receipt(),
        lambda: calls.append(("projection",)) or projection_receipt())
    return service, calls, job, candidate


def test_readiness_is_authenticated_read_only_and_returns_no_evidence_content():
    service, calls, _, candidate = readiness_fixture()
    with pytest.raises(ReviewError):
        service.read({}, JOB)
    assert not calls
    receipt = service.read({"Authorization": "unit"}, JOB)
    assert receipt == {
        "job_id": JOB, "status": "verified", "sql_job_readable": True,
        "candidate_evidence_sha256": digest(candidate), "search": probe_receipt(),
        "projection": projection_receipt(), **OVERLAY,
    }
    assert [call[0] for call in calls] == ["evidence", "projection", "search"]


def test_projection_failure_prevents_readiness_success_and_search():
    from agentic_itops.service_projection import ProjectionDependencyError
    service, calls, _, _ = readiness_fixture()
    def reject():
        raise ProjectionDependencyError("review_projection_http_403_caller_not_authorized")
    service.projection_probe = reject
    with pytest.raises(ProjectionDependencyError, match="caller_not_authorized"):
        service.read({"Authorization": "unit"}, JOB)
    assert [call[0] for call in calls] == ["evidence"]


@pytest.mark.parametrize("mutation", [
    lambda job: job["payload"]["source"].update(run_id="foreign"),
    lambda job: job["payload"].update(execution_allowed=True),
    lambda job: job.update(kind="analysis"),
])
def test_readiness_rejects_scope_before_touching_dependencies(mutation):
    service, calls, job, _ = readiness_fixture()
    mutation(job)
    with pytest.raises(ContractError):
        service.read({"Authorization": "unit"}, JOB)
    assert not calls


def test_readiness_rejects_evidence_identity_mismatch_before_search():
    service, calls, job, _ = readiness_fixture()
    job["payload"]["incident_id"] = "other-incident"
    with pytest.raises(ContractError, match="readiness evidence differs"):
        service.read({"Authorization": "unit"}, JOB)
    assert [call[0] for call in calls] == ["evidence"]


def test_readiness_requires_prepared_candidate_without_reading_dependencies():
    service, calls, job, _ = readiness_fixture()
    job["payload"].pop("candidate_evidence")
    with pytest.raises(ContractError, match="prepared candidate"):
        service.read({"Authorization": "unit"}, JOB)
    assert not calls


def test_readiness_does_not_inherit_budget_archive_authorization():
    service, calls, job, _ = readiness_fixture()
    active = deepcopy(service.scope)
    active.run_id = "new-active-run"
    service.scope = active
    with pytest.raises(ContractError, match="source/run"):
        service.read({"Authorization": "unit"}, JOB)
    assert calls == []


def test_readiness_factory_never_initializes_models_or_budget_ledgers(monkeypatch):
    from agentic_itops import service_runtime as module
    from agentic_itops import service_projection as projection_module
    from agentic_itops.review import search_delivery as search_module

    service, _, _, _ = readiness_fixture()
    def forbidden(*args, **kwargs):
        raise AssertionError("readiness must not construct models or budgets")

    monkeypatch.setattr(module, "_common", lambda *args: (
        SimpleNamespace(scope=service.scope), None, object(), forbidden,
        service.store, service.auth, service.evidence))
    monkeypatch.setattr(module, "BudgetLedger", forbidden)
    monkeypatch.setattr(module, "OpenAIResponsesTransport", forbidden)
    monkeypatch.setattr(module, "create_worker_service", forbidden)
    monkeypatch.setattr(search_module, "ManagedIdentityEmbedding", forbidden)
    settings = search_module.SearchServiceSettings(
        OID, "snapshot-one", frozenset({(SOURCE["producer_key"], SOURCE["run_id"])}),
        search_endpoint="https://unit.search.windows.net", openai_endpoint="https://unit.openai.azure.com/")
    monkeypatch.setattr(search_module.SearchServiceSettings, "from_environment", lambda _: settings)
    calls = []
    class Retriever:
        def __init__(self, settings, embedding, repository, **kwargs):
            assert embedding is None and repository is None and settings.timeout_seconds == 10
            self.client = SimpleNamespace(close=lambda: calls.append("closed"))
        def probe(self, **kwargs):
            calls.append(kwargs)
            return probe_receipt()
    monkeypatch.setattr(search_module, "ApprovedKnowledgeRetriever", Retriever)
    class Projector:
        def __init__(self, origin, audience, credential, client, *, token_scope):
            assert origin == "https://review.example" and audience == OID
            assert token_scope == "api://" + OID + "/.default"
        def probe(self):
            calls.append("projection")
            return projection_receipt()
    monkeypatch.setattr(projection_module, "RemoteResultProjector", Projector)
    created = module.create_worker_dependency_readiness({
        "ITOPS_OPERATOR_INVESTIGATION_APPROVAL_JSON": '{"expires_at_utc":"2000-01-01T00:00:00Z"}',
        "ITOPS_REVIEW_ORIGIN": "https://review.example", "ITOPS_PROJECTION_AUDIENCE": OID,
        "ITOPS_PROJECTION_TOKEN_SCOPE": "api://" + OID + "/.default"})
    assert created.read({"Authorization": "unit"}, JOB)["search"] == probe_receipt()
    assert calls == ["projection", {"producer_key": SOURCE["producer_key"], "run_id": SOURCE["run_id"],
                      "snapshot_id": "snapshot-one"}, "closed"]


@pytest.mark.parametrize("mutation", [
    None,
    lambda receipt: receipt.update(job_id="b" * 64),
    lambda receipt: receipt.update(execution_allowed=True),
    lambda receipt: receipt["search"].update(embedding_performed=True),
    lambda receipt: receipt["search"].update(returned_hit_count=4),
    lambda receipt: receipt["search"].update(documents=["unwanted-content"]),
    lambda receipt: receipt.pop("projection"),
    lambda receipt: receipt["projection"].update(result_written=True),
    lambda receipt: receipt["projection"].update(result_written=0),
    lambda receipt: receipt["projection"].update(required_role="Worker.Invoke"),
])
def test_private_readiness_bridge_validates_exact_model_free_receipt(mutation):
    receipt = {
        "job_id": JOB, "status": "verified", "sql_job_readable": True,
        "candidate_evidence_sha256": "c" * 64, "search": probe_receipt(),
        "projection": projection_receipt(), **OVERLAY,
    }
    if mutation:
        mutation(receipt)
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=receipt)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        bridge = RemoteWorkAdvancer(
            "https://worker.example", SimpleNamespace(get_token=lambda _: SimpleNamespace(token="unit")),
            client, token_scope="api://00112233-4455-6677-8899-aabbccddeeff/.default")
        if mutation:
            with pytest.raises(ReviewError):
                bridge.readiness(JOB)
        else:
            assert bridge.readiness(JOB) == receipt
    assert len(requests) == 1 and requests[0].method == "GET"
    assert str(requests[0].url) == "https://worker.example/api/jobs/" + JOB + "/readiness"


@pytest.mark.parametrize("mutation", [
    None,
    lambda receipt: receipt.pop("scope_totals"),
    lambda receipt: receipt["scope_totals"]["chat"].update(api_calls=1),
    lambda receipt: receipt["scope_totals"]["embedding"].update(accounted_nanodollars=-1),
    lambda receipt: receipt["scope_totals"]["chat"].update(call_states={"invented": 1}, api_calls=1),
    lambda receipt: receipt.pop("monthly_limits"),
    lambda receipt: receipt["monthly_limits"]["chat"].update(effective_limit_nanodollars=200),
    lambda receipt: receipt["monthly_limits"]["chat"].update(stored_limit_nanodollars=True),
    lambda receipt: receipt["monthly_limits"]["embedding"].update(month_key="2026-13"),
    lambda receipt: receipt["monthly_limits"]["embedding"].update(stored_limit_nanodollars=None),
])
def test_private_budget_bridge_validates_all_history_totals(mutation):
    total = {
        "scope_basis": "all_history_for_configured_scope", "workflow_count": 0,
        "api_calls": 0, "tool_calls": 0, "call_states": {}, "accounted_nanodollars": 0,
        "accounting_basis": "conservative_reservations_not_a_billing_invoice",
    }
    month = {
        "month_key": "2026-09", "configured_limit_nanodollars": 200, "stored_limit_nanodollars": 100,
        "effective_limit_nanodollars": 100, "accounted_nanodollars": 10,
    }
    receipt = {
        "job_id": JOB, "chat": {"status": "not_admitted"}, "embedding": {"status": "not_admitted"},
        "scope_totals": {"chat": deepcopy(total), "embedding": deepcopy(total)}, **OVERLAY,
        "monthly_limits": {"chat": deepcopy(month), "embedding": deepcopy(month)},
    }
    if mutation:
        mutation(receipt)
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=receipt))) as client:
        bridge = RemoteWorkAdvancer(
            "https://worker.example", SimpleNamespace(get_token=lambda _: SimpleNamespace(token="unit")),
            client, token_scope="api://00112233-4455-6677-8899-aabbccddeeff/.default")
        if mutation:
            with pytest.raises(ReviewError):
                bridge.budget(JOB)
        else:
            assert bridge.budget(JOB) == receipt
