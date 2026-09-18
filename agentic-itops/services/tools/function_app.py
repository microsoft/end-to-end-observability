"""App-only authenticated original OpenAPI and bounded Monitor MCP operations."""
from functools import lru_cache

import azure.functions as func
from jsonschema import ValidationError

from agentic_itops.consumer.contracts import ContractError, canonical
from agentic_itops.consumer.storage_contract import IntegrityConflict
from agentic_itops.review.contracts import ReviewError
from agentic_itops.service_auth import request_document
from agentic_itops.service_backends import HTTP_OPERATIONS
from agentic_itops.service_runtime import create_tools_service

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


@lru_cache(maxsize=1)
def service():
    return create_tools_service()


def response(operation):
    try:
        value = operation()
        if value is None:
            return func.HttpResponse(status_code=202)
        return func.HttpResponse(canonical(value), status_code=200, mimetype="application/json")
    except ReviewError as exc:
        return func.HttpResponse(canonical({"error": str(exc)}), status_code=exc.status, mimetype="application/json")
    except (ContractError, ValidationError, ValueError) as exc:
        return func.HttpResponse(canonical({"error": type(exc).__name__}), status_code=422, mimetype="application/json")
    except IntegrityConflict:
        return func.HttpResponse('{"error":"unavailable_or_stale_context"}', status_code=409, mimetype="application/json")


@app.route(route="dispatch", methods=["POST"])
def dispatch(req: func.HttpRequest):
    return response(lambda: service().dispatch(req.headers, request_document(req.get_body())))


@app.route(route="mcp", methods=["POST"])
def mcp(req: func.HttpRequest):
    return response(lambda: service().mcp(req.headers, request_document(req.get_body())))


def _register_operation(operation, method, route):
    def invoke(req: func.HttpRequest):
        request = dict(req.params) if method == "GET" else request_document(req.get_body(), maximum=1_000_000)
        return response(lambda: service().openapi(req.headers, operation, request))
    invoke.__name__ = operation
    app.route(route=route, methods=[method])(invoke)


for _operation, (_method, _route) in HTTP_OPERATIONS.items():
    _register_operation(_operation, _method, _route)
