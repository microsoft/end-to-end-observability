"""Offline real-contract integration. No Azure, Entra, Search or model calls."""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest

from agentic_itops.consumer.contracts import ContractError, Observation, canonical, digest, iso, strict_json
from agentic_itops.consumer.logs import CompleteBatch, QueryRecord
from agentic_itops.consumer.storage_contract import WorkRequest, IntegrityConflict
from agentic_itops.hosting import _scope_configuration
from agentic_itops.investigations import approval, validate_batch_budgets, validate_investigation
from agentic_itops.review import ConsumerWorkAdapter, Principal, SQLiteReviewRepository, create_app
from agentic_itops.review.advancement import RemoteWorkAdvancer, select_work
from agentic_itops.review.contracts import ReviewError
from agentic_itops.service_backends import EvidenceBackends, OVERLAY
from agentic_itops.service_runtime import WorkerService
from tests.test_application_integration import integrated, OID
from tests.test_consumer_processing import setup_batches
from tests.test_consumer_budget import LIMITS


def review_app(repository, queue, source, advancer=None, *, method="bearer", roles=("Analyst", "Reviewer", "Operator")):
    class Auth:
        def authenticate(self, request):
            return Principal(OID, frozenset(roles), method)
    return create_app(
        {"TESTING": True, "PUBLIC_ORIGIN": "https://review.example.test",
         "SECRET_KEY": "offline-only-" * 5, "TEST_AUTHENTICATOR": Auth()},
        repository=repository, work_submitter=queue, work_advancer=advancer,
        source_catalog={(source["producer_key"], source["run_id"]): source},
        entitlements={OID: {(source["producer_key"], source["run_id"])}},
    )


def operator_fixture(tmp_path):
    f = integrated(tmp_path)
    _, _, batches, _, _ = setup_batches(tmp_path)
    for batch in batches:
        records = []
        for record in batch.records:
            row = record.observation.row
            row["Payload"]["value"] = 10.0
            records.append(QueryRecord(Observation.parse(batch.producer, row), record.ingested_at))
        baseline = CompleteBatch(batch.producer, batch.window, tuple(records), 0, len(records))
        reference = f.runtime.evidence.put(canonical(baseline.to_dict()))
        f.store.commit_ingestion(baseline, reference)
    f.jobs.investigation_approval = approval({
        "approval_id": "offline-one-batch", "actor_oid": OID,
        "expires_at_utc": iso(datetime.now(timezone.utc) + timedelta(minutes=30))})
    target = f.candidate["target"]
    f.request = {"source": f.candidate["source"],
                 "window": {"start_utc": iso(f.scope.baseline_start), "end_utc": iso(f.scope.reference_time)},
                 "target": {key: target[key] for key in ("surface", "entity_id", "location_id")},
                 "reason": "Inspect retained observations; no detector incident is claimed."}
    f.queue = ConsumerWorkAdapter(f.store, WorkRequest, conflict_type=IntegrityConflict)
    f.client = review_app(f.repository, f.queue, f.candidate["source"]).test_client()
    response = f.client.post("/investigation-jobs", json=f.request, headers={"Idempotency-Key": "request-one"})
    assert response.status_code == 202, response.json
    f.request_id = response.json["job_id"]
    return f


def prepare(f):
    assert f.worker.process_once("investigation", job_id=f.request_id)
    job = f.store.get_job(f.request_id)
    f.reasoning_id = job["next_job_ids"][0]
    f.operator_candidate, f.operator_reference = f.jobs._candidate(f.store.get_job(f.reasoning_id)["payload"])
    return f


def bind_offline_transport(f):
    original = f.transport.respond
    def respond(*args, **kwargs):
        response = original(*args, **kwargs)
        for output in response["output"]:
            if output["type"] == "message":
                value = json.loads(output["content"][0]["text"])
                if "incident_fingerprint" in value:
                    value["incident_fingerprint"] = f.operator_candidate["incident_fingerprint"]
                value["citations"] = [{"source_id": f.operator_candidate["evidence_refs"][0]["event_id"],
                                      "revision": None, "locator": None}]
                output["content"][0]["text"] = json.dumps(value)
        return response
    f.transport.respond = respond


def close(f, incident):
    current = f.repository.get_revision(incident, f.repository.current_revision(incident))
    accepted = f.repository.review(OID, incident, current["etag"], "accept-" + str(current["result"]["analysis_revision"]),
                                   {"action": "accept", "reason": "Reviewed exact AI result and real source evidence"})
    return f.repository.review(OID, incident, accepted["etag"], "close-" + str(current["result"]["analysis_revision"]),
                               {"action": "close", "reason": "Administrative investigation closure; no recovery asserted"})


def test_baseline_operator_request_runs_real_runtime_journal_projection_and_all_nine(tmp_path):
    f = prepare(operator_fixture(tmp_path))
    value = f.operator_candidate
    assert value["trigger"]["kind"] == "operator_requested_investigation"
    assert value["trigger"]["requested_by_oid"] == OID
    assert value["trigger"]["detected_incident"] is False
    assert value["finding_membership"] == [] and value["observed_lifecycle"] == "unknown"
    assert value["detector"]["status"] == "not_invoked"
    assert all(row["Payload"]["value"] == 10 for row in value["observations"])
    assert f.transport.calls == []
    bind_offline_transport(f)
    assert f.worker.process_once("reasoning", job_id=f.reasoning_id)
    assert f.store.get_job(f.job_id)["status"] == "queued"  # unrelated detector work is untouched
    projected = f.store.get_job(f.reasoning_id)["next_job_ids"][0]
    assert f.worker.process_once("projection", job_id=projected)
    incident = value["incident_id"]
    result = f.repository.get_revision(incident, 1)
    assert result["result"]["coverage"]["detected_incident"] is False
    assert result["result"]["coverage"]["analysis_authorship"] == {"kind": "ai"}
    assert result["review_state"] == "pending"
    assert all(item["kind"] == "invalidate-workflows" for item in f.repository.pending_outbox())
    for kind in ("postmortem", "hygiene"):
        response = f.client.post(f"/incidents/{incident}/lifecycle-jobs", json={"kind": kind},
                                 headers={"Idempotency-Key": kind})
        assert response.status_code == 409
        close(f, incident)
        response = f.client.post(f"/incidents/{incident}/lifecycle-jobs", json={"kind": kind},
                                 headers={"Idempotency-Key": kind})
        assert response.status_code == 202, response.json
        root = response.json["job_id"]
        assert f.worker.process_once(kind, job_id=root)
        child = f.store.get_job(root)["next_job_ids"][0]
        assert f.worker.process_once(kind, job_id=child)
        projection = f.store.get_job(child)["next_job_ids"][0]
        assert f.worker.process_once("projection", job_id=projection)
    current = f.repository.get_revision(incident, 3)
    assert current["result"]["postmortem_draft"]["authorship"] == {"kind": "ai"}
    assert current["result"]["observed_lifecycle"] == "unknown"
    assert current["review_state"] == "needs_evidence"
    assert len({name for name, _ in f.transport.calls}) == 9
    assert all(item["kind"] == "invalidate-workflows" for item in f.repository.pending_outbox())
    assert f.store.get_job(f.job_id)["status"] == "queued"


def test_operator_requests_cannot_forge_actor_or_claim_detected_episode(tmp_path):
    f = operator_fixture(tmp_path)
    forged = {**f.request, "requested_by_oid": "ffffffff-ffff-4fff-8fff-ffffffffffff"}
    assert f.client.post("/investigation-jobs", json=forged,
                         headers={"Idempotency-Key": "forged"}).status_code == 422
    f = prepare(f)
    candidate = deepcopy(f.operator_candidate)
    candidate["observed_lifecycle"] = "active"
    with pytest.raises(ContractError, match="detector"):
        validate_investigation(candidate)
    candidate = deepcopy(f.operator_candidate)
    candidate["trigger"]["reason"] = "substituted after authorization"
    reference = f.runtime.evidence.put(canonical(candidate))
    payload = {**f.store.get_job(f.reasoning_id)["payload"], "candidate_evidence": asdict(reference)}
    with pytest.raises(ContractError, match="SQL-authorized"):
        f.jobs._candidate(payload)
    assert f.transport.calls == []


def test_operator_requires_approval_real_target_and_complete_coverage(tmp_path):
    f = operator_fixture(tmp_path)
    f.jobs.investigation_approval = None
    with pytest.raises(ContractError, match="approval"):
        f.worker.process_once("investigation", job_id=f.request_id)
    assert f.transport.calls == []
    request = deepcopy(f.request)
    request["target"]["entity_id"] = "invented-entity"
    f.jobs.investigation_approval = approval({"approval_id": "offline-one-batch", "actor_oid": OID,
        "expires_at_utc": iso(datetime.now(timezone.utc) + timedelta(minutes=30))})
    response = f.client.post("/investigation-jobs", json=request, headers={"Idempotency-Key": "absent-target"})
    with pytest.raises(ContractError, match="real retained"):
        f.worker.process_once("investigation", job_id=response.json["job_id"])
    assert f.transport.calls == []


def test_one_batch_cannot_start_another_reasoning_workflow(tmp_path):
    f = prepare(operator_fixture(tmp_path))
    bind_offline_transport(f)
    f.worker.process_once("reasoning", job_id=f.reasoning_id)
    f.worker.process_once("projection")
    payload = f.store.get_job(f.reasoning_id)["payload"]
    extra = f.store.enqueue(WorkRequest("reasoning", "another-unapproved-model-attempt", payload))
    calls = len(f.transport.calls)
    with pytest.raises(IntegrityConflict, match="already reserved"):
        f.worker.process_once("reasoning", job_id=extra)
    assert len(f.transport.calls) == calls


def test_final_operator_receipt_replays_without_new_inference_after_approval_expiry(tmp_path):
    f = prepare(operator_fixture(tmp_path))
    bind_offline_transport(f)
    lease = f.store.claim("reasoning", "crashed-before-queue-ack", job_id=f.reasoning_id)
    original = f.jobs.reasoning(lease, lambda: None)
    calls = len(f.transport.calls)
    f.jobs.investigation_approval["expires_at_utc"] = iso(datetime.now(timezone.utc) - timedelta(seconds=1))
    assert f.jobs.reasoning(lease, lambda: None) == original
    assert len(f.transport.calls) == calls
    f.store.complete(lease, original.result, original.next_work)
    assert f.worker.process_once("projection")


def test_operator_batch_cannot_process_unrelated_detector_candidate(tmp_path):
    f = operator_fixture(tmp_path)
    with pytest.raises(ContractError, match="unrelated detector"):
        f.worker.process_once("reasoning", job_id=f.job_id)
    assert f.transport.calls == []


def test_four_domain_catalog_requires_specific_entitlements_and_filters_reads():
    sources = [{"producer_key": "p", "run_id": "r", "domain": domain, "surfaces": [domain]}
               for domain in ("power-grid", "upstream", "midstream", "downstream")]
    value = {"sources": sources, "entitlements": [{"oid": OID, "producer_key": "p", "run_id": "r",
                                                 "domain": "upstream"}]}
    catalog, scopes = _scope_configuration(json.dumps(value))
    assert len(catalog) == 4 and scopes[OID] == {("p", "r", "upstream")}
    legacy = {"sources": [sources[1]], "entitlements": [{"oid": OID, "producer_key": "p", "run_id": "r"}]}
    assert ("p", "r") in _scope_configuration(json.dumps(legacy))[0]
    value["entitlements"][0].pop("domain")
    with pytest.raises(ContractError, match="domain"):
        _scope_configuration(json.dumps(value))


def test_four_domain_http_catalog_projection_and_cross_domain_denial(tmp_path):
    from tests.test_review import result
    from agentic_itops.service_projection import ResultProjectionService
    from agentic_itops.consumer.evidence import LocalEvidenceStore
    from types import SimpleNamespace
    sources = [{"producer_key": "p", "run_id": "r", "domain": domain, "surfaces": [domain]}
               for domain in ("power-grid", "upstream", "midstream", "downstream")]
    catalog, scopes = _scope_configuration(json.dumps({
        "sources": sources, "entitlements": [{"oid": OID, "producer_key": "p", "run_id": "r", "domain": "upstream"}]}))
    repository = SQLiteReviewRepository()
    evidence = LocalEvidenceStore(tmp_path / "projection-evidence")
    class Auth:
        def authenticate(self, request):
            return Principal(OID, frozenset({"Analyst", "Reviewer"}), "bearer")
    class AppAuth:
        def authenticate(self, headers, role):
            assert role == "Results.Project"
    class Queue:
        def enqueue(self, request):
            return digest([request.kind, request.logical_key])
    app = create_app({"TESTING": True, "PUBLIC_ORIGIN": "https://review.example.test",
                      "SECRET_KEY": "offline-only-" * 5, "TEST_AUTHENTICATOR": Auth()},
                     repository=repository, entitlements=scopes, source_catalog=catalog, work_submitter=Queue())
    for index, source in enumerate(sources):
        output = result("case-" + source["domain"])
        output.update(source=source, source_hash=digest(source), result_id=str(index) * 64, workflow_id=str(index) * 64)
        ref = evidence.put(canonical(output))
        row = {"workflow_id": output["workflow_id"], "incident_id": output["incident_id"], "revision": 1,
               "producer_key": "p", "run_id": "r", "result": asdict(ref)}
        service = ResultProjectionService(AppAuth(), SimpleNamespace(get=lambda _: row),
                                          evidence, repository, catalog)
        assert service.project({}, {"workflow_id": output["workflow_id"]})["result_id"] == output["result_id"]
    client = app.test_client()
    response = client.get("/incidents")
    assert response.status_code == 200
    assert [item["result"]["source"]["domain"] for item in response.json["incidents"]] == ["upstream"]
    assert client.get("/incidents/case-midstream/revisions/1").status_code == 403
    html = client.get("/").get_data(as_text=True)
    assert "case-upstream" in html and "case-midstream" not in html
    payload = {"source": sources[2], "window": {
        "start_utc": "2026-01-01T00:00:00Z", "end_utc": "2026-01-01T01:00:00Z"}}
    assert client.post("/analysis-jobs", json=payload, headers={"Idempotency-Key": "foreign"}).status_code == 403


def test_operator_incomplete_capture_never_calls_model(tmp_path, monkeypatch):
    f = operator_fixture(tmp_path)
    receipts = f.store.lookup_evidence(f.scope.producer, f.scope.run_id, f.scope.baseline_start, f.scope.reference_time)
    monkeypatch.setattr(f.store, "lookup_evidence", lambda *args: receipts[:1])
    with pytest.raises(ContractError, match="complete committed"):
        f.worker.process_once("investigation", job_id=f.request_id)
    assert f.transport.calls == []


def test_exact_worker_advance_is_authenticated_and_does_not_drain_kind(tmp_path):
    f = integrated(tmp_path)
    wanted = f.store.enqueue(WorkRequest("analysis", "wanted", {
        "source": f.candidate["source"], "window": {
            "start_utc": iso(f.scope.baseline_start), "end_utc": iso(f.scope.reference_time)}, **OVERLAY}))
    calls = []
    class Auth:
        def authenticate(self, headers, role):
            calls.append(role)
            if headers.get("token") != "approved":
                raise ReviewError("forbidden", 403)
    service = WorkerService(f.worker, f.jobs, Auth())
    with pytest.raises(ReviewError):
        service.advance({}, wanted, {})
    with pytest.raises(ContractError):
        service.advance({"token": "approved"}, wanted, {"kind": "reasoning"})
    value = service.advance({"token": "approved"}, wanted, {})
    assert value["job_id"] == wanted and value["status"] == "completed"
    assert f.store.get_job(f.job_id)["status"] == "queued"
    assert service.advance({"token": "approved"}, wanted, {})["status"] == "completed"
    assert calls and set(calls) == {"Worker.Invoke"}


def test_ui_has_explicit_actions_and_no_polling(tmp_path):
    from pathlib import Path
    f = integrated(tmp_path)
    class Advancer:
        def advance(self, job_id):
            raise AssertionError("rendering must never advance work")
    client = review_app(f.repository, ConsumerWorkAdapter(f.store, WorkRequest),
                        f.candidate["source"], Advancer()).test_client()
    html = client.get("/").get_data(as_text=True)
    assert 'class="advance-form card"' in html and 'class="investigation-form card"' in html
    assert "not a detected incident" in html
    script = (Path(__file__).resolve().parents[1] / "agentic_itops" / "review" / "static" / "review.js").read_text()
    assert "setInterval" not in script and "setTimeout" not in script
    assert ".advance-form" in script and "/advance`" in script


def test_advance_retries_exact_selected_job_not_next_job_after_unknown_response(tmp_path):
    f = integrated(tmp_path)
    queue = ConsumerWorkAdapter(f.store, WorkRequest)
    root = f.store.enqueue(WorkRequest("analysis", "root", {"source": f.candidate["source"], **OVERLAY}))
    child = f.store.enqueue(WorkRequest("analysis", "second", {"source": f.candidate["source"], **OVERLAY}))
    calls = []
    class Advancer:
        def advance(self, job_id):
            calls.append(job_id)
            if len(calls) == 1:
                lease = f.store.claim("analysis", "test", job_id=root)
                f.store.complete(lease, {"child_job_ids": [child], **OVERLAY})
                raise httpx.ReadTimeout("unknown response")
            return {"job_id": job_id, "status": f.store.get_job(job_id)["status"]}
    app = review_app(f.repository, queue, f.candidate["source"], Advancer())
    client = app.test_client()
    url = f"/jobs/{root}/advance"
    assert client.post(url, json={}, headers={"Idempotency-Key": "one-click"}).status_code == 503
    assert client.post(url, json={}, headers={"Idempotency-Key": "one-click"}).status_code == 200
    assert calls == [root, root] and f.store.get_job(child)["status"] == "queued"
    assert client.post(url, json={}, headers={"Idempotency-Key": "second-click"}).status_code == 200
    assert calls[-1] == child
    assert client.post(url, json={"kind": "reasoning"}, headers={"Idempotency-Key": "forged"}).status_code == 422


def test_advance_requires_csrf_role_and_authorized_root(tmp_path):
    f = integrated(tmp_path)
    class Advancer:
        def advance(self, job_id):
            raise AssertionError("unauthorized caller reached worker")
    queue = ConsumerWorkAdapter(f.store, WorkRequest)
    root = f.store.enqueue(WorkRequest("analysis", "root", {"source": f.candidate["source"], **OVERLAY}))
    url = f"/jobs/{root}/advance"
    for options in ({"roles": ("Viewer",)}, {"method": "easyauth"}):
        client = review_app(f.repository, queue, f.candidate["source"], Advancer(), **options).test_client()
        assert client.post(url, json={}, headers={"Idempotency-Key": "unauthorized"}).status_code == 403
    other = {**f.candidate["source"], "run_id": "other"}
    client = review_app(f.repository, queue, other, Advancer()).test_client()
    assert client.post(url, json={}, headers={"Idempotency-Key": "foreign"}).status_code == 403


def test_expired_lease_can_be_explicitly_selected_again(tmp_path):
    f = integrated(tmp_path)
    f.store.clock = lambda: datetime.now(timezone.utc) - timedelta(minutes=10)
    f.store.claim("reasoning", "stopped-host", lease_seconds=1, job_id=f.job_id)
    selected = select_work(ConsumerWorkAdapter(f.store, WorkRequest), f.job_id)
    assert selected["status"] == "selected" and selected["job_id"] == f.job_id


def test_proxy_uses_service_credential_not_browser_token():
    calls = []
    class Credential:
        def get_token(self, scope):
            calls.append(scope)
            return type("Token", (), {"token": "review-managed-identity"})()
    job = "a" * 64
    def send(request):
        assert request.headers["Authorization"] == "Bearer review-managed-identity"
        assert request.url.path == f"/api/jobs/{job}/advance"
        assert json.loads(request.content) == {}
        assert request.extensions["timeout"]["read"] == 210
        return httpx.Response(200, json={"job_id": job, "status": "completed"})
    proxy = RemoteWorkAdvancer("https://worker.example.test", Credential(),
                              httpx.Client(transport=httpx.MockTransport(send)),
                              token_scope="api://33333333-3333-3333-3333-333333333333/.default")
    assert proxy.advance(job)["status"] == "completed"
    assert calls == ["api://33333333-3333-3333-3333-333333333333/.default"]


def test_batch_envelope_rejects_extra_spend_or_requests():
    chat = replace(LIMITS, monthly_nanodollars=4_000_000_000, api_calls=24, tool_calls=16, deadline_seconds=120)
    embedding = replace(chat, monthly_nanodollars=1_000_000_000, api_calls=5)
    validate_batch_budgets(chat, embedding)
    for bad in (replace(chat, api_calls=25), replace(chat, tool_calls=17),
                replace(chat, monthly_nanodollars=4_000_000_001), replace(chat, deadline_seconds=121)):
        with pytest.raises(ContractError):
            validate_batch_budgets(bad, embedding)
    with pytest.raises(ContractError):
        validate_batch_budgets(chat, replace(embedding, api_calls=6))


def test_expanded_batch_requires_explicit_limits_and_retains_workflow_caps():
    from agentic_itops.investigations import batch_limits
    envelope = {
        "max_total_nanodollars": 10_000_000_000, "max_total_model_calls": 144,
        "max_total_dispatches": 96, "max_total_embedding_calls": 20, "max_workflow_seconds": 180,
    }
    chat = replace(LIMITS, monthly_nanodollars=9_900_000_000, api_calls=23, tool_calls=16,
                   deadline_seconds=180)
    embedding = replace(chat, monthly_nanodollars=100_000_000, api_calls=3, deadline_seconds=120)
    with pytest.raises(ContractError):
        validate_batch_budgets(chat, embedding)
    validate_batch_budgets(chat, embedding, envelope)
    for bad in (replace(chat, api_calls=25), replace(chat, tool_calls=17),
                replace(chat, monthly_nanodollars=9_900_000_001), replace(chat, deadline_seconds=181)):
        with pytest.raises(ContractError):
            validate_batch_budgets(bad, embedding, envelope)
    for bad in ({**envelope, "max_workflow_seconds": 181}, {**envelope, "max_total_model_calls": True},
                {**envelope, "extra": 1}, {key: value for key, value in envelope.items() if key != "max_total_dispatches"}):
        with pytest.raises(ContractError):
            batch_limits(bad)


def test_search_never_exposes_other_review_domains_or_rejected_document_ids(tmp_path):
    from types import SimpleNamespace
    from agentic_itops.service_search import WorkflowKnowledge
    from agentic_itops.runtime import WorkflowContext
    f = integrated(tmp_path)
    document = {
        **{key: "offline" for key in ("doc_id", "revision", "publication_id", "snapshot_id", "title", "text")},
        **{key: "a" * 64 for key in ("content_hash", "sha256", "projection_hash")},
        "analysis_revision": 1, "eligible_until": "2098-01-01T00:00:00Z",
        "retained_until_utc": "2098-01-01T00:00:00Z", "source": f.candidate["source"],
        "evidence_refs": [], **OVERLAY}
    foreign = {**document, "doc_id": "foreign-secret", "source": {
        **document["source"], "domain": "midstream", "surfaces": ["midstream"]}}
    guards = []
    class Embedding:
        def __init__(self, *args, **kwargs):
            self.client = SimpleNamespace(close=lambda: None)
    class Retriever(Embedding):
        def handle(self, request):
            return {"status": "complete", "snapshot_id": "offline", "query_type": "vector_semantic_hybrid",
                    "index_name": "agentic-itops-knowledge", "request_id": "offline",
                    "embedding_request_id": "offline", "documents": [document, foreign],
                    "rejected_documents": [{"doc_id": "other-domain-secret", "reason": "sql-ineligible"}], **OVERLAY}
        def guard_documents(self, documents, **scope):
            guards.extend(documents)
    reader = WorkflowKnowledge(SimpleNamespace(snapshot_id="offline"), object(), object(),
                               SimpleNamespace(scope="offline"), f.scope,
                               retriever_type=Retriever, embedding_type=Embedding)
    context = WorkflowContext(f.job_id, f.candidate)
    answer = reader(context)
    assert answer["documents"] == [document] and answer["rejected_documents"] == []
    assert "foreign-secret" not in context.knowledge and guards == [document]
    context.knowledge["foreign-secret"] = foreign
    with pytest.raises(ContractError, match="domain/surface"):
        reader.guard(context)
