"""Package this directory plus agentic_itops, agents, tools and runtime artifacts."""
from functools import lru_cache

import azure.functions as func
from jsonschema import ValidationError

from agentic_itops.consumer.contracts import ContractError, canonical
from agentic_itops.consumer.storage_contract import IntegrityConflict, LostLease
from agentic_itops.review.contracts import ReviewError
from agentic_itops.service_auth import request_document
from agentic_itops.service_runtime import (
    create_worker_service, create_worker_budget_diagnostics, create_worker_dependency_readiness,
    create_worker_model_free_service, MODEL_FREE_WORK_KINDS, PAID_WORK_KINDS,
)
from agentic_itops.service_preflight import create_worker_access_preflight
from agentic_itops.access_probe import AccessProofError

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


@lru_cache(maxsize=1)
def service():
    return create_worker_service()


@lru_cache(maxsize=1)
def projection_service():
    return create_worker_model_free_service()


@lru_cache(maxsize=1)
def preflight_service():
    return create_worker_access_preflight()


@lru_cache(maxsize=1)
def budget_diagnostics():
    return create_worker_budget_diagnostics()


@lru_cache(maxsize=1)
def dependency_readiness():
    return create_worker_dependency_readiness()


def response(operation):
    try:
        return func.HttpResponse(canonical(operation()), status_code=200, mimetype="application/json")
    except ReviewError as exc:
        return func.HttpResponse(canonical({"error": str(exc)}), status_code=exc.status, mimetype="application/json")
    except (ContractError, ValidationError, ValueError) as exc:
        return func.HttpResponse(canonical({"error": type(exc).__name__}), status_code=422, mimetype="application/json")
    except (IntegrityConflict, LostLease) as exc:
        return func.HttpResponse(canonical({"error": type(exc).__name__}), status_code=409, mimetype="application/json")
    except KeyError:
        return func.HttpResponse('{"error":"unknown_job_or_result"}', status_code=404, mimetype="application/json")
    except AccessProofError as exc:
        return func.HttpResponse(canonical({"error": "source_isolation_not_verified", "reason": str(exc)}),
                                 status_code=412, mimetype="application/json")


@app.route(route="work/{kind}", methods=["POST"])
def work(req: func.HttpRequest):
    def operation():
        kind = req.route_params["kind"]
        body = request_document(req.get_body())
        if body != {} or kind not in MODEL_FREE_WORK_KINDS | PAID_WORK_KINDS:
            raise ContractError("work calls only select a bounded preconfigured job kind")
        selected = projection_service() if kind in MODEL_FREE_WORK_KINDS else service()
        return selected.invoke(req.headers, kind, body)
    return response(operation)


@app.route(route="lifecycle/postmortem", methods=["POST"])
def postmortem(req: func.HttpRequest):
    return response(lambda: service().postmortem(req.headers, request_document(req.get_body())))


@app.route(route="jobs/{job_id}", methods=["GET"])
def job(req: func.HttpRequest):
    return response(lambda: projection_service().job(req.headers, req.route_params["job_id"]))


@app.route(route="jobs/{job_id}/advance", methods=["POST"])
def advance(req: func.HttpRequest):
    def operation():
        body = request_document(req.get_body())
        if body != {}:
            raise ContractError("exact job advancement accepts only an empty body")
        job_id = req.route_params["job_id"]
        model_free = projection_service()
        kind = model_free.advance_kind(req.headers, job_id)
        selected = model_free if kind in MODEL_FREE_WORK_KINDS else service()
        return selected.advance(req.headers, job_id, body)
    return response(operation)


@app.route(route="jobs/{job_id}/budget", methods=["GET"])
def budget(req: func.HttpRequest):
    return response(lambda: budget_diagnostics().read(req.headers, req.route_params["job_id"]))


@app.route(route="jobs/{job_id}/readiness", methods=["GET"])
def readiness(req: func.HttpRequest):
    return response(lambda: dependency_readiness().read(req.headers, req.route_params["job_id"]))


@app.route(route="results/project", methods=["POST"])
def projection(req: func.HttpRequest):
    return response(lambda: projection_service().projection(req.headers, request_document(req.get_body())))


@app.route(route="results/replacement", methods=["POST"])
def replacement(req: func.HttpRequest):
    return response(lambda: projection_service().replacement(req.headers, request_document(req.get_body())))


@app.route(route="preflight/source-access", methods=["POST"])
def source_access(req: func.HttpRequest):
    return response(lambda: preflight_service().verify(req.headers, request_document(req.get_body(), maximum=8192)))
