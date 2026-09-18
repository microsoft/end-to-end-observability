"""HTTP-only bounded publisher; JWT authentication is enforced in application code."""
from __future__ import annotations

import json
import logging
import os
from threading import Lock
from uuid import uuid4

import azure.functions as func
import jwt

from agentic_itops.consumer.budget import BudgetLedger, BudgetLimits
from agentic_itops.consumer.contracts import strict_json
from agentic_itops.investigations import approval, usage_authorization
from agentic_itops.consumer.sql_connect import managed_identity_sql_factory
from agentic_itops.review.contracts import OVERLAY, ReviewError, canonical, fields, require
from agentic_itops.review.publisher import PublisherDispatcher, canonical_uuid
from agentic_itops.review.repository import SqlServerReviewRepository
from agentic_itops.review.search_delivery import (
    AzureSearchDelivery, BudgetLedgerEmbeddingGate, ManagedIdentityEmbedding, SearchServiceSettings, _required,
)

PUBLISHER_INVOKE_ROLE = "Publisher.Invoke"


class ServiceCallerAuthenticator:
    def __init__(self, tenant_id, audience, caller_oids, *, key_resolver=None):
        self.tenant_id = canonical_uuid(tenant_id)
        require(isinstance(audience, str) and 1 <= len(audience) <= 256, "explicit publisher audience required")
        require(type(caller_oids) is frozenset and 1 <= len(caller_oids) <= 20,
                "explicit publisher service caller OID allowlist required")
        for oid in caller_oids:
            canonical_uuid(oid)
        self.audience, self.caller_oids = audience, caller_oids
        self.issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        self.jwks = jwt.PyJWKClient(
            f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys", timeout=5,
        ) if key_resolver is None else None
        self.key_resolver = key_resolver or (lambda token: self.jwks.get_signing_key_from_jwt(token).key)

    def authenticate(self, request):
        authorization = request.headers.get("Authorization", "")
        require(isinstance(authorization, str) and len(authorization) <= 16384,
                "publisher service authentication required", 401)
        parts = authorization.split()
        require(len(parts) == 2 and parts[0].casefold() == "bearer", "publisher bearer token required", 401)
        token = parts[1]
        try:
            require(jwt.get_unverified_header(token).get("alg") == "RS256", "RS256 service token required", 401)
            claims = jwt.decode(
                token, self.key_resolver(token), algorithms=["RS256"], audience=self.audience,
                issuer=self.issuer, leeway=0, options={
                    "strict_aud": True, "require": ["exp", "iat", "nbf", "iss", "aud", "tid", "oid", "idtyp"],
                },
            )
            oid = canonical_uuid(claims["oid"])
            require(claims["tid"] == self.tenant_id and claims["idtyp"] == "app" and "scp" not in claims,
                    "application-only service token required", 401)
            roles = claims.get("roles", [])
            require(type(roles) is list and len(roles) <= 32
                    and all(isinstance(role, str) and 1 <= len(role) <= 128 for role in roles),
                    "typed bounded service application roles required", 401)
        except (jwt.PyJWTError, ReviewError, ValueError, TypeError, KeyError) as exc:
            raise ReviewError("invalid publisher service authentication", 401) from exc
        require(oid in self.caller_oids, "service caller is not allowed to invoke publisher", 403)
        require(PUBLISHER_INVOKE_ROLE in roles, "Publisher.Invoke application role required", 403)
        return oid


class PublisherFunctionService:
    def __init__(self, authenticator, dispatcher, repository):
        self.authenticator, self.dispatcher, self.repository = authenticator, dispatcher, repository

    def handle(self, request):
        try:
            require(request.method == "POST", "POST required", 405)
            self.authenticator.authenticate(request)
            raw = request.get_body()
            require(len(raw) <= 128, "publisher request exceeds bound", 413)
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeDecodeError) as exc:
                raise ReviewError("publisher JSON object required", 400) from exc
            fields(body, {"outbox_id"} if isinstance(body, dict) and "outbox_id" in body else set())
            selected = body.get("outbox_id")
            if "outbox_id" in body:
                canonical_uuid(selected)
                reconciled = []
                receipt = self.dispatcher.run_once(outbox_id=selected)
            else:
                reconciled = self.repository.reconcile_publisher(limit=10)
                receipt = self.dispatcher.run_once()
            return func.HttpResponse(canonical({
                "status": ("not_claimed" if selected is not None else "idle") if receipt is None else "attempted",
                **({"outbox_id": selected} if selected is not None else {}),
                "reconciliation_jobs": len(reconciled), "receipt": receipt, **OVERLAY,
            }), status_code=200, mimetype="application/json")
        except ReviewError as exc:
            return func.HttpResponse(canonical({
                "status": "rejected", "error": str(exc), **OVERLAY,
            }), status_code=exc.status, mimetype="application/json")
        except Exception:
            # Provider errors are never success, but credential/SQL/provider text
            # must not be copied into the HTTP response or logs.
            logging.error("Publisher invocation failed; inspect durable publisher/budget attempt receipts")
            return func.HttpResponse(canonical({
                "status": "failed", "error": "publisher delivery unavailable; inspect durable receipts", **OVERLAY,
            }), status_code=503, mimetype="application/json")


def create_publisher_service(environment=None):
    environment = os.environ if environment is None else environment
    settings = SearchServiceSettings.from_environment(environment)
    require(_required(environment, "ITOPS_PUBLISHER_ENABLED") == "true", "publisher service is not enabled", 503)
    try:
        caller_oids = json.loads(_required(environment, "ITOPS_PUBLISHER_CALLER_OIDS_JSON"))
        policy = json.loads(_required(environment, "ITOPS_EMBEDDING_BUDGET_JSON"))
    except ValueError as exc:
        raise ReviewError("publisher caller/budget settings must be JSON", 503) from exc
    require(type(caller_oids) is list and len(set(caller_oids)) == len(caller_oids),
            "publisher caller OIDs must be a unique array")
    require(isinstance(policy, dict), "explicit embedding budget policy required", 503)
    limits = BudgetLimits(**policy)
    configured = approval(strict_json(environment["ITOPS_OPERATOR_INVESTIGATION_APPROVAL_JSON"])) \
        if "ITOPS_OPERATOR_INVESTIGATION_APPROVAL_JSON" in environment else None
    authorization = usage_authorization(limits, configured, "embedding")
    require(limits.model_context_tokens >= 8001 and limits.max_output_tokens == 1,
            "embedding policy requires at least 8001 input tokens and a one-token output reserve", 503)
    authenticator = ServiceCallerAuthenticator(
        _required(environment, "ENTRA_TENANT_ID"), _required(environment, "ITOPS_PUBLISHER_AUDIENCE"),
        frozenset(caller_oids),
    )
    factory = managed_identity_sql_factory(
        _required(environment, "ITOPS_SQL_SERVER"), _required(environment, "ITOPS_SQL_DATABASE"), settings.client_id,
    )
    repository = SqlServerReviewRepository(factory)
    ledger = BudgetLedger(factory, limits, _required(environment, "ITOPS_EMBEDDING_BUDGET_SCOPE"),
                          usage_authorization=authorization)
    embedding = ManagedIdentityEmbedding(settings, BudgetLedgerEmbeddingGate(ledger))
    delivery = AzureSearchDelivery(settings, embedding, repository)
    dispatcher = PublisherDispatcher(repository, delivery, owner=str(uuid4()), lease_seconds=180)
    return PublisherFunctionService(authenticator, dispatcher, repository)


app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)
_service = None
_service_lock = Lock()


@app.route(route="publisher/run-once", methods=["POST"])
def publisher_run_once(req: func.HttpRequest) -> func.HttpResponse:
    global _service
    try:
        with _service_lock:
            if _service is None:
                _service = create_publisher_service()
        return _service.handle(req)
    except Exception:
        logging.error("Publisher configuration unavailable; no delivery attempted")
        return func.HttpResponse(canonical({
            "status": "failed", "error": "publisher configuration unavailable", **OVERLAY,
        }), status_code=503, mimetype="application/json")
