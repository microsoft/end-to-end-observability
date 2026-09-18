"""Initial worker package: ONLY count-proof route, with no inference endpoints."""
from functools import lru_cache

import azure.functions as func

from agentic_itops.access_probe import AccessProofError
from agentic_itops.consumer.contracts import ContractError, canonical
from agentic_itops.review.contracts import ReviewError
from agentic_itops.service_auth import request_document
from agentic_itops.service_preflight import create_worker_access_preflight

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


@lru_cache(maxsize=1)
def preflight_service():
    return create_worker_access_preflight()


@app.route(route="preflight/source-access", methods=["POST"])
def source_access(req: func.HttpRequest):
    try:
        proof = preflight_service().verify(req.headers, request_document(req.get_body(), maximum=8192))
        return func.HttpResponse(canonical(proof), mimetype="application/json")
    except ReviewError as exc:
        return func.HttpResponse(canonical({"error": str(exc)}), status_code=exc.status, mimetype="application/json")
    except AccessProofError as exc:
        return func.HttpResponse(canonical({"error": "source_isolation_not_verified", "reason": str(exc)}),
                                 status_code=412, mimetype="application/json")
    except (ContractError, ValueError, KeyError):
        return func.HttpResponse('{"error":"invalid_count_probe_contract"}', status_code=422,
                                 mimetype="application/json")
