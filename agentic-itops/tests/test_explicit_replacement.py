"""Offline proof of a single replacement; fixture outcomes are not live evidence."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from agentic_itops.application_results import IndeterminateAttempt
from agentic_itops.consumer.budget import BudgetLedger
from agentic_itops.consumer.contracts import ContractError, iso
from agentic_itops.consumer.responses import AgentFailure
from agentic_itops.consumer.storage_contract import IntegrityConflict, WorkRequest
from agentic_itops.investigations import approval, replacement_authorization
from agentic_itops.replacement import request, require_capacity
from agentic_itops.review import ConsumerWorkAdapter
from agentic_itops.review.contracts import ReviewError
from agentic_itops.service_projection import ResultProjectionService
from agentic_itops.service_runtime import WorkerService
from tests.test_consumer_budget import AGENT, LIMITS, REQUEST
from tests.test_detector_admission import fixture
from tests.test_operator_investigation import review_app
from tests.test_application_integration import OID


def failed(tmp_path, *, observe_only=False):
    f = fixture(tmp_path)
    respond = f.transport.respond

    def incomplete(*args, **kwargs):
        value = respond(*args, **kwargs)
        value["status"] = "incomplete"
        value["incomplete_details"] = {"reason": "max_output_tokens"}
        return value

    f.transport.respond = incomplete
    with pytest.raises(AgentFailure):
        f.worker.process_once("reasoning", job_id=f.admitted_id)
    f.transport.respond = respond
    f.original = deepcopy(f.store.get_job(f.admitted_id))
    f.original_attempt = deepcopy(f.results.get(f.admitted_id))
    f.original_accounting = deepcopy(f.runtime.ledger.inspect_workflow(f.admitted_id))
    assert f.original_accounting["call_states"] == {"settled": 1}
    f.runtime.ledger.limits = replace(
        f.runtime.ledger.limits, policy_version="offline-replacement", max_output_tokens=8192,
        model_context_tokens=10000, workflow_tokens=1000000, api_calls=23, tool_calls=16)
    f.embedding = BudgetLedger(f.runtime.ledger.factory, replace(LIMITS, api_calls=3),
                               "offline-embedding", dialect="sqlite-local-test")
    f.approval = {
        **f.jobs.investigation_approval, "approval_id": "offline-one-replacement",
        "replacement_authorization": {"failed_workflow_id": f.admitted_id, "max_output_tokens": 8192}}
    if observe_only:
        from agentic_itops.investigations import usage_authorization
        from tests.test_usage_policy import approved_usage
        f.runtime.ledger.limits = replace(f.runtime.ledger.limits, usage_mode="observe_only", deadline_seconds=180)
        f.embedding.limits = replace(f.embedding.limits, usage_mode="observe_only", deadline_seconds=180)
        mode = approved_usage(f.runtime.ledger.limits, f.embedding.limits)
        f.approval.update(limits=mode["limits"], usage_policy=mode["usage_policy"])
        f.approval["limits"].update(max_total_nanodollars=1, max_total_model_calls=1,
                                   max_total_dispatches=1, max_total_embedding_calls=1)
        f.runtime.ledger.usage_authorization = usage_authorization(f.runtime.ledger.limits, f.approval, "chat")
        f.embedding.usage_authorization = usage_authorization(f.embedding.limits, f.approval, "embedding")
    f.jobs.investigation_approval = approval(f.approval)
    f.jobs.replacement_accounting = lambda workflow_id, configured: require_capacity(
        f.runtime.ledger, f.embedding, workflow_id, configured)
    auth = SimpleNamespace(authenticate=lambda headers, role: None)
    f.projection = ResultProjectionService(
        auth, f.results, f.runtime.evidence, f.repository,
        {(f.scope.producer.key, f.scope.run_id): f.candidate["source"]})

    class Projector:
        def __call__(self, result):
            return f.projection.project({}, {"workflow_id": result["workflow_id"]})

        def project_failure(self, workflow_id):
            return f.projection.project_failure({}, {"workflow_id": workflow_id})

    f.jobs.projector = Projector()
    f.service = WorkerService(f.worker, f.jobs, auth)
    f.queue = ConsumerWorkAdapter(f.store, WorkRequest, conflict_type=IntegrityConflict)
    f.advancer = SimpleNamespace(
        queue_replacement=lambda workflow_id, actor: f.service.replacement(
            {}, {"workflow_id": workflow_id, "actor_oid": actor})["job_id"],
        advance=lambda job_id: f.service.advance({}, job_id, {}))
    f.app = review_app(f.repository, f.queue, f.candidate["source"], f.advancer)
    # The fixture's failed legacy child has no source; use the actual candidate source
    # in this adapter only. Separate descendant tests prove root graph authorization.
    original_get = f.queue.get_job

    def scoped(job_id):
        value = original_get(job_id)
        if job_id == f.admitted_id:
            value = {**value, "source": f.candidate["source"]}
        return value

    f.queue.get_job = scoped
    f.client = f.app.test_client()
    f.route = f"/jobs/{f.admitted_id}/replacement-jobs"
    return f


@pytest.mark.parametrize("observe_only", [False, True])
def test_failed_history_survives_preparation_and_exactly_one_revision_two_attempt(tmp_path, observe_only):
    f = failed(tmp_path, observe_only=observe_only)
    calls = len(f.transport.calls)
    first = f.jobs.enqueue_replacement(f.admitted_id, OID)
    assert f.jobs.enqueue_replacement(f.admitted_id, OID) == first
    assert len(f.transport.calls) == calls
    assert f.worker.process_once("replacement", job_id=first)
    assert len(f.transport.calls) == calls
    diagnostic = f.repository.get_revision(f.candidate["incident_id"], 1)
    assert diagnostic["result"]["status"] == "failed"
    with pytest.raises(ReviewError):
        f.repository.review(OID, f.candidate["incident_id"], diagnostic["etag"], "no-fake-acceptance",
                            {"action": "accept", "reason": "Cannot accept absent outputs"})
    assert f.results.get(f.admitted_id) == f.original_attempt
    replacement_id, = f.store.get_job(first)["next_job_ids"]
    assert f.worker.process_once("reasoning", job_id=replacement_id)
    assert f.worker.process_once("projection")
    second = f.repository.get_revision(f.candidate["incident_id"], 2)
    assert second["result"]["workflow_id"] == replacement_id
    assert second["result"]["status"] == "completed"
    assert second["review_state"] == "pending"
    assert f.store.get_job(f.admitted_id) == f.original
    assert f.results.get(f.admitted_id) == f.original_attempt
    assert f.runtime.ledger.inspect_workflow(f.admitted_id) == f.original_accounting
    assert f.jobs.projector.project_failure(f.admitted_id)["analysis_revision"] == 1
    paid_calls = len(f.transport.calls)
    new = f.store.enqueue(WorkRequest("reasoning", "attempt-to-bypass-dedup",
                                     f.store.get_job(replacement_id)["payload"]))
    with pytest.raises(IntegrityConflict):
        f.worker.process_once("reasoning", job_id=new)
    assert len(f.transport.calls) == paid_calls


def test_queue_api_is_operator_scoped_idempotent_and_does_not_infer(tmp_path):
    f = failed(tmp_path)
    before = len(f.transport.calls)
    response = f.client.post(f.route, json={}, headers={"Idempotency-Key": "one"})
    assert response.status_code == 202, response.json
    assert f.client.post(f.route, json={}, headers={"Idempotency-Key": "one"}).json == response.json
    assert f.client.post(f.route, json={}, headers={"Idempotency-Key": "two"}).json["job_id"] == response.json["job_id"]
    assert response.json["inference_performed"] is response.json["embedding_performed"] is False
    assert f.client.get("/jobs/" + response.json["job_id"]).status_code == 200
    assert len(f.transport.calls) == before
    assert f.client.post(f.route, json={"attempts": 2}, headers={"Idempotency-Key": "bad"}).status_code == 422
    analyst = review_app(f.repository, f.queue, f.candidate["source"], f.advancer,
                         roles=("Analyst",)).test_client()
    assert analyst.post(f.route, json={}, headers={"Idempotency-Key": "analyst"}).status_code == 403
    cookie = review_app(f.repository, f.queue, f.candidate["source"], f.advancer,
                        method="easyauth").test_client()
    assert cookie.get("/").status_code == 200
    assert cookie.post(f.route, json={}, headers={"Idempotency-Key": "csrf"}).status_code == 403


@pytest.mark.parametrize("change", [
    lambda f: f.jobs.investigation_approval.update(expires_at_utc=iso(datetime.now(timezone.utc) - timedelta(seconds=1))),
    lambda f: f.jobs.investigation_approval["replacement_authorization"].update(failed_workflow_id="f" * 64),
    lambda f: f.jobs.investigation_approval["detector_authorization"].update(candidate_sha256="f" * 64),
    lambda f: f.jobs.investigation_approval.update(actor_oid="ffffffff-ffff-ffff-ffff-ffffffffffff"),
    lambda f: f.jobs.investigation_approval.pop("replacement_authorization"),
])
def test_bad_authorization_cannot_prepare_or_make_paid_calls(tmp_path, change):
    f = failed(tmp_path)
    change(f)
    before = len(f.transport.calls)
    with pytest.raises((ContractError, ReviewError)):
        f.jobs.enqueue_replacement(f.admitted_id, OID)
    assert len(f.transport.calls) == before


@pytest.mark.parametrize("state", ["reserved", "unknown"])
def test_unsettled_failed_predecessor_is_not_replaceable(tmp_path, state):
    f = failed(tmp_path)
    # Extra call intentionally uses the original policy for this historical workflow.
    old = replace(f.runtime.ledger.limits, **{
        "policy_version": "fixture-v1", "model_context_tokens": 1000, "max_output_tokens": 100,
        "workflow_tokens": 10000, "api_calls": 32, "tool_calls": 32})
    historical = BudgetLedger(f.runtime.ledger.factory, old, f.runtime.ledger.scope, dialect="sqlite-local-test")
    historical.reserve(f.admitted_id, "unsettled-historical-call", AGENT, REQUEST)
    if state == "unknown":
        historical.settle("unsettled-historical-call")
    with pytest.raises(IntegrityConflict, match="settled"):
        f.jobs.enqueue_replacement(f.admitted_id, OID)


def test_historical_unknown_other_workflow_is_retained_not_reset(tmp_path):
    f = failed(tmp_path)
    f.runtime.ledger.open_workflow("other-historical-workflow")
    f.runtime.ledger.reserve("other-historical-workflow", "unknown-prior-call", AGENT, REQUEST)
    f.runtime.ledger.settle("unknown-prior-call")
    before = f.runtime.ledger.inspect_scope()
    f.jobs.enqueue_replacement(f.admitted_id, OID)
    assert f.runtime.ledger.inspect_scope() == before
    assert before["call_states"]["unknown"] == 1


def test_observe_only_capacity_keeps_all_history_unknowns_and_candidate_guards(tmp_path):
    f = failed(tmp_path, observe_only=True)
    f.runtime.ledger.open_workflow("other-historical")
    f.runtime.ledger.reserve("other-historical", "other-unknown", AGENT, REQUEST)
    f.runtime.ledger.settle("other-unknown")
    before = f.runtime.ledger.inspect_scope()
    totals = require_capacity(f.runtime.ledger, f.embedding, f.admitted_id, f.approval)
    assert totals["chat"] == before
    assert before["accounted_nanodollars"] > f.approval["limits"]["max_total_nanodollars"]
    assert before["api_calls"] > f.approval["limits"]["max_total_model_calls"]
    assert before["call_states"]["unknown"] == 1
    f.jobs.enqueue_replacement(f.admitted_id, OID)
    assert f.runtime.ledger.inspect_scope() == before
    assert f.results.get(f.admitted_id) == f.original_attempt
    f.jobs.investigation_approval["detector_authorization"]["candidate_sha256"] = "f" * 64
    with pytest.raises(ContractError):
        f.jobs.enqueue_replacement(f.admitted_id, OID)


@pytest.mark.parametrize("kind,value", [
    ("max_total_model_calls", 23), ("max_total_dispatches", 15), ("max_total_embedding_calls", 2),
    ("max_total_nanodollars", 1),
])
def test_replacement_capacity_uses_all_history_and_full_per_workflow_allowance(tmp_path, kind, value):
    from agentic_itops.investigations import DEFAULT_BATCH_LIMITS
    f = failed(tmp_path)
    f.jobs.investigation_approval["limits"] = {**DEFAULT_BATCH_LIMITS, kind: value}
    with pytest.raises(ContractError, match="remaining"):
        f.jobs.enqueue_replacement(f.admitted_id, OID)


def test_atomic_second_attempt_guard_survives_alternate_queue_identity(tmp_path):
    f = failed(tmp_path)
    work = request(f.approval, f.admitted_id, f.candidate, f.reference, kind="reasoning")
    first = f.store.enqueue(work)
    lease = f.store.claim("reasoning", "first", job_id=first)
    row, fresh = f.results.begin(lease, f.candidate, f.reference, base_revision=1,
        portfolio_hash=f.runtime.portfolio_hash, single_model_batch=True, replacement_of=f.admitted_id)
    assert fresh and row["revision"] == 2
    with pytest.raises(IndeterminateAttempt):
        f.jobs.reasoning(lease, lambda: None)
    other = f.store.enqueue(WorkRequest("reasoning", "other-replacement-key", work.payload))
    second = f.store.claim("reasoning", "second", job_id=other)
    with pytest.raises(IntegrityConflict, match="already"):
        f.results.begin(second, f.candidate, f.reference, base_revision=1,
            portfolio_hash=f.runtime.portfolio_hash, single_model_batch=True, replacement_of=f.admitted_id)
    assert f.results.get(f.admitted_id) == f.original_attempt


def test_concurrent_admission_can_reserve_only_one_replacement(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    f = failed(tmp_path)
    work = request(f.approval, f.admitted_id, f.candidate, f.reference, kind="reasoning")
    leases = []
    for index in range(2):
        job_id = f.store.enqueue(WorkRequest("reasoning", f"concurrent-{index}", work.payload))
        leases.append(f.store.claim("reasoning", f"owner-{index}", job_id=job_id))

    def reserve(lease):
        try:
            return f.results.begin(lease, f.candidate, f.reference, base_revision=1,
                portfolio_hash=f.runtime.portfolio_hash, single_model_batch=True,
                replacement_of=f.admitted_id)[1]
        except IntegrityConflict:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve, leases)) == [False, True]
    assert f.results.get(f.admitted_id) == f.original_attempt


@pytest.mark.parametrize("mutation", [
    lambda row, job: row.update(revision=2),
    lambda row, job: row.update(projected=1),
    lambda row, job: row.update(result=row["candidate"]),
    lambda row, job: job.update(status="completed"),
    lambda row, job: job.update(status="leased"),
    lambda row, job: job.update(error_code="IndeterminateAttempt"),
    lambda row, job: job["payload"].update(replacement_of="f" * 64),
])
def test_missing_or_ineligible_terminal_failure_cannot_prepare(tmp_path, monkeypatch, mutation):
    f = failed(tmp_path)
    row, job = deepcopy(f.original_attempt), deepcopy(f.original)
    mutation(row, job)
    monkeypatch.setattr(f.results, "get", lambda _: row)
    monkeypatch.setattr(f.store, "get_job", lambda _: job)
    with pytest.raises(IntegrityConflict):
        f.jobs.enqueue_replacement(f.admitted_id, OID)


def test_unbound_private_acknowledgment_is_not_accepted(tmp_path):
    f = failed(tmp_path)
    f.advancer.queue_replacement = lambda *_: f.admitted_id
    response = f.client.post(f.route, json={}, headers={"Idempotency-Key": "wrong-private-job"})
    assert response.status_code == 503
    assert len(f.transport.calls) == 1


def test_unapproved_runtime_output_ceiling_is_rejected(tmp_path):
    f = failed(tmp_path)
    f.runtime.ledger.limits = replace(f.runtime.ledger.limits, max_output_tokens=2048)
    with pytest.raises(ContractError, match="ceiling"):
        f.jobs.enqueue_replacement(f.admitted_id, OID)


@pytest.mark.parametrize("value", [None, {}, {"failed_workflow_id": "a" * 64, "max_output_tokens": 16384},
                                  {"failed_workflow_id": "a" * 64, "max_output_tokens": True},
                                  {"failed_workflow_id": "A" * 64, "max_output_tokens": 8192}])
def test_replacement_contract_is_not_an_unbounded_retry_flag(value):
    with pytest.raises(ContractError):
        replacement_authorization(value)
