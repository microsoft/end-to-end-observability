import importlib.util
from pathlib import Path
from types import SimpleNamespace

import azure.functions as func
import pytest

from agentic_itops.review.contracts import ReviewError
from agentic_itops.consumer.contracts import ContractError


def host(monkeypatch, kind="projection", *, authorized=True, archived=False):
    path = Path(__file__).resolve().parents[1] / "services" / "worker" / "function_app.py"
    spec = importlib.util.spec_from_file_location("projection_routing_test_host", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []
    class ModelFree:
        def advance_kind(self, headers, job_id):
            value = self.job(headers, job_id)
            if archived and value["kind"] != "projection":
                raise ContractError("archived work permits only persisted-result projection")
            return value["kind"]
        def job(self, headers, job_id):
            if not authorized:
                raise ReviewError("service authentication required", 401)
            calls.append(("read", job_id))
            return {"job_id": job_id, "kind": kind}
        def advance(self, headers, job_id, body):
            calls.append(("projection-advance", job_id, body))
            return {"status": "completed"}
        def invoke(self, headers, selected_kind, body):
            calls.append(("projection-work", selected_kind, body))
            return {"status": "processed"}
        def projection(self, headers, body):
            calls.append(("projection-enqueue", body))
            return {"status": "queued"}
        def replacement(self, headers, body):
            calls.append(("replacement-enqueue", body))
            return {"status": "queued"}
    def model_service():
        calls.append(("model-construction",))
        return SimpleNamespace(
            advance=lambda headers, job_id, body: {"status": "model-selected"},
            invoke=lambda headers, kind, body: {"status": "model-selected"})
    monkeypatch.setattr(module, "create_worker_model_free_service", ModelFree)
    monkeypatch.setattr(module, "create_worker_service", model_service)
    functions = {f.get_function_name(): f.get_user_function() for f in module.app.get_functions()}
    assert len(functions) == 9
    return functions, calls


def request(route_params, body=b"{}"):
    return func.HttpRequest(method="POST", url="https://worker.example/api/test",
                            headers={"Authorization": "unit"}, route_params=route_params, body=body)


def test_exact_projection_and_read_routes_never_construct_model_service(monkeypatch):
    functions, calls = host(monkeypatch)
    assert functions["advance"](request({"job_id": "a" * 64})).status_code == 200
    assert functions["job"](request({"job_id": "a" * 64})).status_code == 200
    assert functions["projection"](request({}, b'{"workflow_id":"approved"}')).status_code == 200
    assert functions["work"](request({"kind": "projection"})).status_code == 200
    assert all(call[0] != "model-construction" for call in calls)
    assert any(call[0] == "projection-advance" for call in calls)
    assert any(call[0] == "projection-enqueue" for call in calls)


@pytest.mark.parametrize("kind", ["analysis", "ingest", "coverage", "detector", "projection", "replacement"])
def test_active_model_free_routes_never_construct_paid_service(monkeypatch, kind):
    functions, calls = host(monkeypatch, kind)
    assert functions["advance"](request({"job_id": "a" * 64})).status_code == 200
    assert functions["work"](request({"kind": kind})).status_code == 200
    assert all(call[0] != "model-construction" for call in calls)


@pytest.mark.parametrize("kind", ["analysis", "ingest", "coverage", "detector",
                                 "reasoning", "postmortem", "hygiene", "investigation", "replacement"])
def test_archived_nonprojection_advance_cannot_fall_through_to_paid_factory(monkeypatch, kind):
    functions, calls = host(monkeypatch, kind, archived=True)
    assert functions["advance"](request({"job_id": "a" * 64})).status_code == 422
    assert calls == [("read", "a" * 64)]
    assert functions["job"](request({"job_id": "a" * 64})).status_code == 200


@pytest.mark.parametrize("kind,body", [("invented", b"{}"), ("reasoning", b'{"run_id":"old"}')])
def test_invalid_work_never_constructs_paid_service(monkeypatch, kind, body):
    functions, calls = host(monkeypatch)
    assert functions["work"](request({"kind": kind}, body)).status_code == 422
    assert not calls


@pytest.mark.parametrize("kind", ["reasoning", "postmortem", "hygiene", "investigation"])
def test_active_paid_work_retains_normal_factory(monkeypatch, kind):
    functions, calls = host(monkeypatch, kind)
    assert functions["work"](request({"kind": kind})).status_code == 200
    assert calls == [("model-construction",)]
    calls.clear()
    advanced = functions["advance"](request({"job_id": "a" * 64}))
    assert advanced.status_code == 200
    assert b"model-selected" in advanced.get_body()
    assert calls == [("read", "a" * 64)]


def test_model_selection_requires_authenticated_sql_kind_not_request_content(monkeypatch):
    functions, calls = host(monkeypatch, "reasoning")
    assert functions["advance"](request({"job_id": "a" * 64}, b'{"kind":"projection"}')).status_code == 422
    assert calls == []
    assert functions["advance"](request({"job_id": "a" * 64})).status_code == 200
    assert calls == [("read", "a" * 64), ("model-construction",)]


def test_replacement_enqueue_is_model_free(monkeypatch):
    functions, calls = host(monkeypatch, "replacement")
    assert functions["replacement"](request(
        {}, b'{"workflow_id":"approved-failure","actor_oid":"operator"}')).status_code == 200
    assert calls == [("replacement-enqueue", {"workflow_id": "approved-failure", "actor_oid": "operator"})]


@pytest.mark.parametrize("kind", ["reasoning", "projection"])
def test_unauthorized_kind_selection_never_constructs_models_or_advances(monkeypatch, kind):
    functions, calls = host(monkeypatch, kind, authorized=False)
    assert functions["advance"](request({"job_id": "a" * 64})).status_code == 401
    assert calls == []
