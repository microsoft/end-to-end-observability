"""Offline failed-workflow diagnostics never recover outputs or admit model work."""

from copy import deepcopy
from dataclasses import asdict
import sqlite3
from types import SimpleNamespace

import httpx
import pytest

from agentic_itops.consumer.contracts import ContractError, canonical, iso
from agentic_itops.consumer.storage_contract import IntegrityConflict
from agentic_itops.review.contracts import AGENTS, OVERLAY, ReviewError, result_header
from agentic_itops.service_projection import (
    ProjectionDependencyError, RemoteResultProjector, ResultProjectionService,
)
from tests.test_application_integration import OID, integrated


HEADERS = {"Authorization": "unit"}


class Auth:
    def authenticate(self, headers, role):
        assert role == "Results.Project"
        if headers.get("Authorization") != "unit":
            raise ReviewError("service authentication required", 401)


@pytest.fixture
def failed(tmp_path):
    f = integrated(tmp_path)
    lease = f.store.claim("reasoning", "offline-failure")
    f.results.begin(lease, f.candidate, f.reference, base_revision=0,
                    portfolio_hash=f.runtime.portfolio_hash)
    f.store.fail(lease, "AgentFailure", "sensitive-model-payload-must-not-escape")
    source = f.candidate["source"]
    f.sources = {(source["producer_key"], source["run_id"], source["domain"]): {
        "domain": source["domain"], "surfaces": source["surfaces"],
    }}
    f.service = ResultProjectionService(
        Auth(), f.results, f.runtime.evidence, f.repository, f.sources)
    f.body = {"workflow_id": f.job_id}
    return f


def diagnostic(f):
    return f.repository.get_revision(f.candidate["incident_id"], 1)


def project(f):
    return f.service.project_failure(HEADERS, f.body)


def test_truthful_failed_diagnostic_without_model_calls_or_sql_application_mutation(failed, tmp_path):
    f = failed
    with sqlite3.connect(tmp_path / "jobs.db") as connection:
        before = list(connection.iterdump())
    accounting = f.runtime.ledger.inspect_scope()
    original = deepcopy(f.results.get(f.job_id))
    job = deepcopy(f.store.get_job(f.job_id))
    receipt = project(f)
    first = diagnostic(f)
    value = first["result"]
    assert value == result_header(value)
    assert receipt == {"result_id": f.job_id, "analysis_revision": 1, "etag": first["etag"]}
    assert value["schema_version"] == "1.0"
    assert value["result_id"] == value["workflow_id"] == f.job_id
    assert value["status"] == "failed" and value["observed_lifecycle"] == "unknown"
    assert value["source"] == f.candidate["source"]
    assert value["created_at_utc"] == iso(job["created_at"])
    assert value["summary"] == "Failed-workflow diagnostic, not a completed model analysis"
    assert value["versions"] == {"failure_projection": "1", "portfolio": original["portfolio_hash"]}
    assert value["evidence_refs"] == []
    assert set(value["stage_results"]) == set(AGENTS) - {"postmortem-drafter", "alert-hygiene"}
    assert all(stage == {
        "status": "incomplete",
        "error": "No validated final outputs were retained for this failed workflow.",
    } for stage in value["stage_results"].values())
    assert value["coverage"] == {
        "report_kind": "failed_workflow_diagnostic",
        "failed_workflow_id": f.job_id, "candidate_evidence": asdict(f.reference),
        "diagnostic": {"error_code": "AgentFailure"},
    }
    assert all(value[key] == expected for key, expected in OVERLAY.items())
    assert b"sensitive-model-payload" not in canonical(value)
    assert not {"proposal", "gate_receipt", "postmortem_draft"} & value.keys()
    assert first["review_state"] == "pending"
    assert project(f) == receipt
    assert f.results.get(f.job_id) == original and original["result"] is None
    assert f.store.get_job(f.job_id) == job
    with sqlite3.connect(tmp_path / "jobs.db") as connection:
        assert list(connection.iterdump()) == before
    assert f.runtime.ledger.inspect_scope() == accounting
    assert f.transport.calls == []


def test_failed_diagnostic_cannot_be_accepted_or_used_as_a_final_result(failed):
    f = failed
    receipt = project(f)
    with pytest.raises(ReviewError, match="only current complete eligible analysis"):
        f.repository.review(OID, f.candidate["incident_id"], receipt["etag"], "no-failed-accept",
                            {"action": "accept", "reason": "A diagnostic is not completed analysis"})
    with pytest.raises(IntegrityConflict, match="incomplete"):
        f.service.project(HEADERS, f.body)
    assert f.results.get(f.job_id)["result"] is None
    assert f.transport.calls == []


def test_original_receipt_is_immutable_after_later_review_revision(failed):
    f = failed
    receipt = project(f)
    first = diagnostic(f)
    next_result = deepcopy(first["result"])
    next_result.update(result_id="b" * 64, workflow_id="b" * 64, analysis_revision=2)
    f.repository.register_result(next_result)
    later = diagnostic(f)
    assert later["current"] is False and later["etag"] != first["etag"]
    assert later["result_hash"] == first["result_hash"]
    assert project(f) == receipt
    assert f.repository.current_revision(f.candidate["incident_id"]) == 2
    assert f.results.get(f.job_id)["result"] is None


def test_failure_projection_never_overwrites_existing_first_review(failed):
    f = failed
    project(f)
    original = diagnostic(f)
    row = f.results.get(f.job_id)
    row["portfolio_hash"] = "f" * 64
    f.results.get = lambda _: deepcopy(row)
    with pytest.raises(ReviewError, match="immutable revision conflict"):
        project(f)
    assert diagnostic(f)["result_hash"] == original["result_hash"]


@pytest.mark.parametrize("change", [
    {"kind": "postmortem"}, {"status": "queued"}, {"status": "leased"},
    {"status": "completed"}, {"status": "blocked"}, {"error_code": None},
    {"error_code": "ContractError"}, {"error_code": "AgentFailure: secret"},
    {"finished_at": None}, {"expires_at": "2026-01-01T00:00:00Z"},
    {"result": {}}, {"result_sha256": "f" * 64}, {"next_job_ids": ("f" * 64,)},
    {"job_id": "f" * 64},
])
def test_rejects_any_job_without_exact_terminal_agent_failure(failed, change):
    f = failed
    job = f.store.get_job(f.job_id)
    job.update(change)
    f.store.get_job = lambda _: deepcopy(job)
    with pytest.raises(IntegrityConflict):
        project(f)
    with pytest.raises(ReviewError, match="not found"):
        f.repository.current_revision(f.candidate["incident_id"])
    assert f.transport.calls == []


@pytest.mark.parametrize("change", [
    {"job_kind": "hygiene"}, {"revision": 2}, {"revision": 0}, {"revision": True},
    {"result": {}}, {"projected": 1}, {"workflow_id": "f" * 64},
    {"incident_id": "foreign"}, {"producer_key": "foreign"}, {"run_id": "foreign"},
    {"portfolio_hash": "not-a-digest"}, {"candidate": {}},
])
def test_rejects_changed_or_finished_application_attempt(failed, change):
    f = failed
    row = f.results.get(f.job_id)
    row.update(change)
    f.results.get = lambda _: deepcopy(row)
    with pytest.raises((IntegrityConflict, ReviewError, ContractError)):
        project(f)
    assert f.transport.calls == []


@pytest.mark.parametrize("change", [
    {"incident_id": "foreign"}, {"producer_key": "foreign"}, {"run_id": "foreign"},
    {"candidate_evidence": {}}, {"source": {}},
    {"trigger": {"kind": "operator_requested_investigation"}},
    {"replacement_of": "f" * 64}, {"IsSynthetic": False},
    {"execution_allowed": True}, {"OperationalUse": "allowed"},
    {"nested": {"execution_allowed": True}},
])
def test_rejects_payload_source_reference_trigger_and_overlay_drift(failed, change):
    f = failed
    job = f.store.get_job(f.job_id)
    job["payload"].update(change)
    f.store.get_job = lambda _: deepcopy(job)
    with pytest.raises((IntegrityConflict, ReviewError)):
        project(f)


@pytest.mark.parametrize("change", [
    {"incident_id": "foreign"}, {"trigger": {"kind": "operator_requested_investigation"}},
    {"IsSynthetic": 1}, {"OperationalUse": "allowed"}, {"execution_allowed": True},
    {"nested": {"IsSynthetic": False}},
])
def test_rejects_candidate_identity_and_recursive_overlay_drift(failed, change):
    f = failed
    candidate = deepcopy(f.candidate)
    candidate.update(change)
    reference = asdict(f.runtime.evidence.put(canonical(candidate)))
    row, job = f.results.get(f.job_id), f.store.get_job(f.job_id)
    row["candidate"] = reference
    job["payload"]["candidate_evidence"] = reference
    f.results.get, f.store.get_job = lambda _: deepcopy(row), lambda _: deepcopy(job)
    with pytest.raises((IntegrityConflict, ReviewError)):
        project(f)


def test_independently_verifies_candidate_hash_even_if_evidence_reader_does_not(failed):
    f = failed
    altered = deepcopy(f.candidate)
    altered["incident_id"] = "tampered"
    f.service.evidence = SimpleNamespace(read=lambda _: canonical(altered))
    with pytest.raises(IntegrityConflict, match="immutable reference"):
        project(f)


@pytest.mark.parametrize("scope", ["absent", "domain", "surface"])
def test_requires_configured_exact_source_domain_and_surface(failed, scope):
    f = failed
    if scope == "absent":
        f.sources.clear()
    else:
        configured = next(iter(f.sources.values()))
        configured["domain" if scope == "domain" else "surfaces"] = (
            "foreign" if scope == "domain" else ["foreign"])
    with pytest.raises(IntegrityConflict, match="server-held source"):
        project(f)


def test_pair_keyed_scope_and_consistent_nested_payload_source_are_supported(failed):
    f = failed
    key, configured = next(iter(f.sources.items()))
    f.service.sources = {key[:2]: configured}
    job = f.store.get_job(f.job_id)
    job["payload"]["source"] = deepcopy(f.candidate["source"])
    f.store.get_job = lambda _: deepcopy(job)
    assert project(f)["analysis_revision"] == 1


@pytest.mark.parametrize("body", [
    None, [], {}, {"workflow_id": "f" * 64, "result": {}},
    {"workflow_id": "f" * 64, "status": "completed"},
    {"workflow_id": "f" * 64, "actor": OID}, {"workflow_id": "f" * 64, "evidence": {}},
    {"workflow_id": "../candidate"}, {"workflow_id": "F" * 64}, {"workflow_id": True},
])
def test_request_accepts_only_an_exact_immutable_workflow_identity(failed, body):
    with pytest.raises(ContractError):
        failed.service.project_failure(HEADERS, body)


def test_authentication_precedes_durable_reads(failed):
    def forbidden(*args):
        raise AssertionError("unauthenticated request must not read SQL")
    failed.results.get = forbidden
    with pytest.raises(ReviewError) as exc:
        failed.service.project_failure({}, failed.body)
    assert exc.value.status == 401


def remote(handler):
    scopes = []
    def token(scope):
        scopes.append(scope)
        return SimpleNamespace(token="unit-token")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    projector = RemoteResultProjector(
        "https://unit-review.azurewebsites.net", "api://projection", SimpleNamespace(get_token=token),
        client, token_scope="api://projection/.default")
    return projector, scopes


def test_remote_failure_projection_is_one_bounded_identity_only_post():
    seen = []
    expected = {"result_id": "a" * 64, "analysis_revision": 1, "etag": '"immutable"'}
    def handle(request):
        seen.append(request)
        return httpx.Response(200, json=expected)
    projector, scopes = remote(handle)
    assert projector.project_failure("a" * 64) == expected
    assert scopes == ["api://projection/.default"]
    assert len(seen) == 1
    assert seen[0].method == "POST"
    assert str(seen[0].url) == "https://unit-review.azurewebsites.net/internal/results/project-failure"
    assert seen[0].content == b'{"workflow_id":"' + b"a" * 64 + b'"}'
    assert all(value == 30 for value in seen[0].extensions["timeout"].values())


@pytest.mark.parametrize("change", [
    {"result_id": "b" * 64}, {"analysis_revision": 0}, {"analysis_revision": True},
    {"analysis_revision": -1}, {"analysis_revision": "1"}, {"etag": None},
    {"etag": ""}, {"etag": "a" * 257}, {"status": "completed"},
])
def test_remote_rejects_unbound_or_malformed_receipts(change):
    receipt = {"result_id": "a" * 64, "analysis_revision": 1, "etag": '"immutable"'}
    receipt.update(change)
    projector, _ = remote(lambda _: httpx.Response(200, json=receipt))
    with pytest.raises(IntegrityConflict):
        projector.project_failure("a" * 64)


@pytest.mark.parametrize("content", [b"x" * 4097, b"[]", b'{"result_id":1,"result_id":2}'])
def test_remote_rejects_unbounded_or_invalid_json_receipts(content):
    projector, _ = remote(lambda _: httpx.Response(200, content=content))
    with pytest.raises(ContractError):
        projector.project_failure("a" * 64)


@pytest.mark.parametrize("status", [302, 403, 500])
def test_remote_errors_are_bounded_and_never_echo_secrets_or_follow_redirects(status):
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(status, json={"error": "sensitive-model-payload"},
                              headers={"Location": "https://untrusted.example"})
    projector, _ = remote(handle)
    with pytest.raises(ProjectionDependencyError) as exc:
        projector.project_failure("a" * 64)
    assert str(exc.value) == f"review_projection_http_{status}_unclassified"
    assert len(seen) == 1


def test_remote_rejects_malicious_workflow_before_token_or_http():
    def forbidden(*args):
        raise AssertionError("invalid identity must not request a token or send HTTP")
    projector, scopes = remote(forbidden)
    for value in ("../workflow", {}, None, "F" * 64):
        with pytest.raises(ContractError):
            projector.project_failure(value)
    assert scopes == []


def test_wsgi_mount_authenticates_and_registers_only_a_truthful_diagnostic(failed, monkeypatch):
    from flask import Flask
    from werkzeug.test import Client
    from agentic_itops import service_projection as module, service_runtime

    f = failed
    human = Flask("human-failure-projection-test")
    @human.before_request
    def require_human():
        return {"error": "human authentication required"}, 401

    monkeypatch.setattr(module, "create_review_app", lambda *args, **kwargs: human)
    monkeypatch.setattr(module, "ServiceAuthenticator", lambda *args: Auth())
    monkeypatch.setattr(module, "managed_identity_sql_factory", lambda *args: object())
    monkeypatch.setattr(module, "SqlServerWorkflowStore", lambda _: f.store)
    monkeypatch.setattr(module, "ApplicationResults", lambda _: f.results)
    monkeypatch.setattr(module, "SqlServerReviewRepository", lambda _: f.repository)
    monkeypatch.setattr(module, "_scope_configuration", lambda _: (f.sources, {}))
    monkeypatch.setattr(service_runtime, "_identity", lambda _: object())
    monkeypatch.setattr(service_runtime, "_evidence", lambda *args: f.runtime.evidence)
    environment = {key: "unit" for key in (
        "ITOPS_SQL_SERVER", "ITOPS_SQL_DATABASE", "AZURE_CLIENT_ID", "ENTRA_TENANT_ID",
        "ITOPS_PROJECTION_AUDIENCE", "ITOPS_REVIEW_SCOPES_JSON")}
    environment.update(
        ITOPS_WORKER_ORIGIN="https://worker.example", ITOPS_PUBLISHER_ORIGIN="https://publisher.example",
        ITOPS_WORKER_TOKEN_SCOPE="api://services/.default",
        ITOPS_PUBLISHER_TOKEN_SCOPE="api://services/.default", ITOPS_PROJECTION_CALLERS_JSON="[]")
    client = Client(module.create_review_service(environment))
    route = "/internal/results/project-failure"
    assert client.post(route, json=f.body).status_code == 401
    assert client.post(route, json={**f.body, "result": {}}, headers=HEADERS).status_code == 422
    assert client.post(route, data=b"x" * 4097, headers=HEADERS).status_code == 413
    assert client.post(route, json={"workflow_id": "f" * 64}, headers=HEADERS).status_code == 404
    response = client.post(route, json=f.body, headers=HEADERS)
    assert response.status_code == 200, response.json
    assert response.json == project(f)
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert client.get("/", headers=HEADERS).status_code == 401
    job = f.store.get_job(f.job_id)
    job["status"] = "completed"
    f.store.get_job = lambda _: deepcopy(job)
    rejected = client.post(route, json=f.body, headers=HEADERS)
    assert rejected.status_code == 409
    assert rejected.json == {"error": "unavailable_or_conflicting_result"}
    assert f.results.get(f.job_id)["result"] is None
    assert f.transport.calls == []
