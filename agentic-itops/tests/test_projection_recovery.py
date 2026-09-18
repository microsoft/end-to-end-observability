from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest

from agentic_itops.consumer.contracts import ContractError
from agentic_itops.consumer.storage_contract import IntegrityConflict, WorkRequest
from agentic_itops.review import ConsumerWorkAdapter
from agentic_itops.review.advancement import RemoteWorkAdvancer
from agentic_itops.review.contracts import OVERLAY, ReviewError
from agentic_itops.service_runtime import ProjectionWorkerService
from tests.test_application_integration import integrated
from tests.test_operator_investigation import review_app


class Auth:
    def authenticate(self, headers, role):
        if headers.get("Authorization") != "unit" or role != "Worker.Invoke":
            raise ReviewError("service authentication required", 401)


def prepared(tmp_path):
    f = integrated(tmp_path)
    f.unrelated_job_id = f.job_id
    f.job_id = f.store.enqueue(WorkRequest(
        "reasoning", "source-scoped-projection-recovery-parent",
        {**f.store.get_job(f.job_id)["payload"], "source": f.candidate["source"]}))
    assert f.worker.process_once("reasoning", job_id=f.job_id)
    f.original_projection = f.store.get_job(f.job_id)["next_job_ids"][0]
    def fail(_):
        raise RuntimeError("offline projection failure")
    f.jobs.projector = fail
    with pytest.raises(RuntimeError):
        f.worker.process_once("projection", job_id=f.original_projection)
    f.jobs.projector = f.repository.register_result
    f.model_calls_before_recovery = len(f.transport.calls)
    f.queue = ConsumerWorkAdapter(f.store, WorkRequest, conflict_type=IntegrityConflict)
    f.model_free = ProjectionWorkerService(f.worker, f.jobs, Auth())
    f.queue_calls = []
    class Advancer:
        def queue_projection(self, workflow_id, key):
            f.queue_calls.append((workflow_id, key))
            return f.model_free.projection(
                {"Authorization": "unit", "Idempotency-Key": key}, {"workflow_id": workflow_id})["job_id"]
        def advance(self, job_id):
            return f.model_free.advance({"Authorization": "unit"}, job_id, {})
    f.advancer = Advancer()
    f.app = review_app(f.repository, f.queue, f.candidate["source"], f.advancer)
    f.client = f.app.test_client()
    f.route = f"/jobs/{f.job_id}/projection-jobs"
    return f


def test_recovery_preserves_failure_and_reasoning_and_projects_pending_review(tmp_path):
    f = prepared(tmp_path)
    original = deepcopy(f.store.get_job(f.original_projection))
    root = deepcopy(f.store.get_job(f.job_id))
    headers = {"Idempotency-Key": "recovery-one"}
    response = f.client.post(f.route, json={}, headers=headers)
    assert response.status_code == 202, response.json
    receipt = response.json
    assert receipt["inference_performed"] is receipt["embedding_performed"] is False
    recovery_id = receipt["job_id"]
    assert recovery_id not in (f.job_id, f.original_projection)
    assert f.client.post(f.route, json={}, headers=headers).json == receipt
    assert len(f.queue_calls) == 1
    looked_up = f.client.get("/jobs/" + recovery_id)
    assert looked_up.status_code == 200 and looked_up.json["status"] == "queued"
    assert looked_up.json["source"] == f.candidate["source"]
    advanced = f.client.post("/jobs/" + recovery_id + "/advance", json={},
                             headers={"Idempotency-Key": "advance-recovery"})
    assert advanced.status_code == 200, advanced.json
    assert advanced.json["advanced"]["result"]["status"] == "review_projected"
    assert f.store.get_job(f.original_projection) == original
    assert f.store.get_job(f.job_id) == root
    assert f.store.get_job(f.unrelated_job_id)["status"] == "queued"
    assert len(f.transport.calls) == f.model_calls_before_recovery
    current = f.repository.get_revision(f.candidate["incident_id"], 1)
    assert current["review_state"] == "pending"


@pytest.mark.parametrize("mutation,status", [
    (lambda parent: parent.update(status="failed"), 409),
    (lambda parent: parent.update(status="queued"), 409),
    (lambda parent: parent.update(kind="projection"), 409),
    (lambda parent: parent["result"].pop("result_evidence"), 409),
    (lambda parent: parent["result"].update(result_id="f" * 64), 409),
    (lambda parent: parent["payload"]["source"].update(domain="foreign"), 403),
])
def test_recovery_requires_completed_source_authority_before_enqueue(tmp_path, mutation, status):
    f = prepared(tmp_path)
    original = f.queue.get_job
    def altered(job_id):
        parent = deepcopy(original(job_id))
        if job_id == f.job_id:
            mutation(parent)
        return parent
    f.queue.get_job = altered
    response = f.client.post(f.route, json={}, headers={"Idempotency-Key": "invalid-parent"})
    assert response.status_code == status
    assert not f.queue_calls
    assert len(f.transport.calls) == f.model_calls_before_recovery


def test_recovery_rejects_content_injection_and_requires_operator_and_csrf(tmp_path):
    f = prepared(tmp_path)
    assert f.client.post(f.route, json={"review_state": "accepted"},
                         headers={"Idempotency-Key": "forgery"}).status_code == 422
    analyst = review_app(f.repository, f.queue, f.candidate["source"], f.advancer,
                         roles=("Analyst",)).test_client()
    assert analyst.post(f.route, json={}, headers={"Idempotency-Key": "analyst"}).status_code == 403
    cookie = review_app(f.repository, f.queue, f.candidate["source"], f.advancer,
                        method="easyauth").test_client()
    assert cookie.get("/").status_code == 200
    assert cookie.post(f.route, json={}, headers={"Idempotency-Key": "csrf"}).status_code == 403
    with cookie.session_transaction() as session:
        csrf = session["csrf"]
    response = cookie.post(f.route, json={}, headers={
        "Idempotency-Key": "csrf", "Origin": "https://review.example.test", "X-CSRF-Token": csrf})
    assert response.status_code == 202


def test_recovery_does_not_trust_an_unbound_remote_job_acknowledgement(tmp_path):
    f = prepared(tmp_path)
    f.advancer.queue_projection = lambda *_: f.job_id
    response = f.client.post(f.route, json={}, headers={"Idempotency-Key": "wrong-ack"})
    assert response.status_code == 503
    assert "differs from the completed source/result" in response.json["error"]
    assert len(f.transport.calls) == f.model_calls_before_recovery


def test_unknown_enqueue_outcome_recovers_the_same_projection_without_requeueing_failure(tmp_path):
    f = prepared(tmp_path)
    original = f.advancer.queue_projection
    first = []
    def uncertain(workflow_id, key):
        job_id = original(workflow_id, key)
        if not first:
            first.append(job_id)
            raise httpx.ReadTimeout("offline unknown reply")
        assert job_id == first[0]
        return job_id
    f.advancer.queue_projection = uncertain
    headers = {"Idempotency-Key": "same-request"}
    assert f.client.post(f.route, json={}, headers=headers).status_code == 503
    recovered = f.client.post(f.route, json={}, headers=headers)
    assert recovered.status_code == 202 and recovered.json["job_id"] == first[0]
    assert f.store.get_job(f.original_projection)["status"] == "failed"
    assert len(f.transport.calls) == f.model_calls_before_recovery


def test_model_free_factory_ignores_expired_model_approval_and_cannot_run_models(tmp_path, monkeypatch):
    from agentic_itops import service_runtime as module, service_projection
    f = prepared(tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError("projection recovery must not construct model or budget services")
    config = SimpleNamespace(
        scope=f.scope, workspace_id="00112233-4455-6677-8899-aabbccddeeff",
        retained_until=f.jobs.retained_until.isoformat())
    monkeypatch.setattr(module, "_common", lambda *args: (
        config, None, object(), forbidden, f.store, Auth(), f.runtime.evidence))
    monkeypatch.setattr(module, "SqlServerReviewRepository", lambda _: f.repository)
    for name in ("BudgetLedger", "OpenAIResponsesTransport", "PortfolioRuntime", "create_worker_service"):
        monkeypatch.setattr(module, name, forbidden)
    class Projector:
        def __init__(self, *args, **kwargs):
            pass
        def __call__(self, result):
            return f.repository.register_result(result)
    monkeypatch.setattr(service_projection, "RemoteResultProjector", Projector)
    created = module.create_worker_projection_service({
        "ITOPS_REVIEW_ORIGIN": "https://unit-review.azurewebsites.net",
        "ITOPS_PROJECTION_AUDIENCE": "unit", "ITOPS_PROJECTION_TOKEN_SCOPE": "api://unit/.default",
        "ITOPS_OPERATOR_INVESTIGATION_APPROVAL_JSON": '{"expires_at_utc":"2000-01-01T00:00:00Z"}',
        "ITOPS_CHAT_LIMIT_AMENDMENT_JSON": "intentionally-invalid-model-only-setting"})
    assert created.jobs.runtime is None and created.jobs.investigation_approval is None
    assert set(created.worker.handlers) == {"projection"}
    with pytest.raises(ReviewError):
        created.job({}, f.job_id)
    for action in (
        lambda: created.invoke({"Authorization": "unit"}, "reasoning", {}),
        lambda: created.advance({"Authorization": "unit"}, f.job_id, {}),
    ):
        with pytest.raises(ContractError, match="only executes projection"):
            action()
    recovery = created.projection(
        {"Authorization": "unit", "Idempotency-Key": "expired-approval-recovery"},
        {"workflow_id": f.job_id})
    assert created.advance({"Authorization": "unit"}, recovery["job_id"], {})["status"] == "completed"
    assert len(f.transport.calls) == f.model_calls_before_recovery


@pytest.mark.parametrize("mutation", [
    None,
    lambda value: value.update(job_id="not-a-digest"),
    lambda value: value.update(execution_allowed=True),
    lambda value: value.update(review_state="accepted"),
])
def test_private_enqueue_bridge_is_bounded_and_sends_no_claimed_result(mutation):
    result = {"job_id": "b" * 64, "status": "queued", **OVERLAY}
    if mutation:
        mutation(result)
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=result)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        bridge = RemoteWorkAdvancer(
            "https://worker.example", SimpleNamespace(get_token=lambda _: SimpleNamespace(token="unit")),
            client, token_scope="api://unit/.default")
        if mutation:
            with pytest.raises(ReviewError):
                bridge.queue_projection("a" * 64, "c" * 64)
        else:
            assert bridge.queue_projection("a" * 64, "c" * 64) == "b" * 64
    assert len(requests) == 1
    assert requests[0].url.path == "/api/results/project"
    assert requests[0].content == b'{"workflow_id":"' + b"a" * 64 + b'"}'
    assert requests[0].headers["Idempotency-Key"] == "c" * 64
