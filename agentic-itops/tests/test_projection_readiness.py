from types import SimpleNamespace

import httpx
import pytest

from agentic_itops.consumer.contracts import ContractError
from agentic_itops.review.contracts import ReviewError
from agentic_itops.service_projection import (
    ProjectionDependencyError, RemoteResultProjector, ResultProjectionService,
)


def receipt():
    return {"status": "verified", "required_role": "Results.Project",
            "probe_kind": "projection_authentication_only",
            "inference_performed": False, "embedding_performed": False,
            "result_written": False}


def remote(handler):
    return RemoteResultProjector(
        "https://unit-review.azurewebsites.net", "api://services",
        SimpleNamespace(get_token=lambda scope: SimpleNamespace(token="test-only")),
        httpx.Client(transport=httpx.MockTransport(handler)), token_scope="api://services/.default")


def test_projection_probe_uses_same_role_and_never_touches_results_or_sql():
    class Auth:
        def authenticate(self, headers, role):
            assert role == "Results.Project"
            if headers != {"Authorization": "unit"}:
                raise ReviewError("service caller is not authorized", 403)
    service = ResultProjectionService(Auth(), None, None, None, None)
    with pytest.raises(ReviewError):
        service.readiness({})
    assert service.readiness({"Authorization": "unit"}) == receipt()


def test_remote_probe_is_one_bounded_get_without_workflow_or_result():
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(200, json=receipt())
    assert remote(handle).probe() == receipt()
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "GET" and request.content == b""
    assert str(request.url) == "https://unit-review.azurewebsites.net/internal/results/readiness"
    assert request.headers["Authorization"] == "Bearer test-only"
    assert all(value == 10 for value in request.extensions["timeout"].values())


def test_wsgi_readiness_mount_keeps_machine_and_human_auth_separate(monkeypatch):
    from flask import Flask
    from werkzeug.test import Client
    from agentic_itops import service_projection as module, service_runtime

    human = Flask("human-test")
    @human.before_request
    def deny_machine_as_human():
        return {"error": "human authentication required"}, 401

    def forbidden(*args, **kwargs):
        raise AssertionError("readiness must not open SQL or read/write a result")
    class Auth:
        def __init__(self, *args):
            pass
        def authenticate(self, headers, role):
            assert role == "Results.Project"
            if headers.get("Authorization") != "Bearer test-only":
                raise ReviewError("service authentication required", 401)

    monkeypatch.setattr(module, "create_review_app", lambda *args, **kwargs: human)
    monkeypatch.setattr(module, "ServiceAuthenticator", Auth)
    monkeypatch.setattr(module, "managed_identity_sql_factory", lambda *args: forbidden)
    monkeypatch.setattr(module, "_scope_configuration", lambda _: ({}, {}))
    monkeypatch.setattr(service_runtime, "_identity", lambda _: object())
    monkeypatch.setattr(service_runtime, "_evidence", lambda *args: None)
    environment = {key: "unit" for key in (
        "ITOPS_SQL_SERVER", "ITOPS_SQL_DATABASE", "AZURE_CLIENT_ID", "ENTRA_TENANT_ID",
        "ITOPS_PROJECTION_AUDIENCE", "ITOPS_REVIEW_SCOPES_JSON")}
    environment.update(
        ITOPS_WORKER_ORIGIN="https://worker.example", ITOPS_PUBLISHER_ORIGIN="https://publisher.example",
        ITOPS_WORKER_TOKEN_SCOPE="api://services/.default",
        ITOPS_PUBLISHER_TOKEN_SCOPE="api://services/.default",
        ITOPS_PROJECTION_CALLERS_JSON="[]")
    client = Client(module.create_review_service(environment))
    url = "/internal/results/readiness"
    assert client.get(url).status_code == 401
    response = client.get(url, headers={"Authorization": "Bearer test-only"})
    assert response.status_code == 200 and response.json == receipt()
    assert response.headers["Cache-Control"] == "no-store"
    assert client.get("/", headers={"Authorization": "Bearer test-only"}).status_code == 401


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(result_written=True),
    lambda value: value.update(result_written=0),
    lambda value: value.update(required_role="Worker.Invoke"),
    lambda value: value.update(extra="unwanted"),
    lambda value: value.pop("embedding_performed"),
])
def test_remote_probe_rejects_inexact_or_writing_receipts(mutation):
    value = receipt()
    mutation(value)
    with pytest.raises(ContractError):
        remote(lambda _: httpx.Response(200, json=value)).probe()


@pytest.mark.parametrize("content,reason", [
    (b'{"error":"service caller is not authorized"}', "caller_not_authorized"),
    (b'{"error":"invalid service authentication"}', "invalid_authentication"),
    (b'{"error":"service authentication required"}', "authentication_required"),
    (b'{"error":"secret-token-do-not-log"}', "unclassified"),
    (b'<html>secret-token-do-not-log</html>', "unclassified"),
    (b'{"error":"service caller is not authorized","error":"secret-token-do-not-log"}', "unclassified"),
    (b'{"error":["secret-token-do-not-log"]}', "unclassified"),
    (b"x" * 4097, "unclassified"),
])
def test_projection_errors_report_only_fixed_codes_without_response_secrets(content, reason):
    calls = []
    def reject(request):
        calls.append(request)
        return httpx.Response(403, content=content)
    client = remote(reject)
    for action in (client.probe, lambda: client({"workflow_id": "w"})):
        with pytest.raises(ProjectionDependencyError) as exc:
            action()
        assert str(exc.value) == "review_projection_http_403_" + reason
        assert exc.value.__cause__ is None
    assert len(calls) == 2
