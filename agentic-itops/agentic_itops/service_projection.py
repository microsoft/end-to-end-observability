"""Separate app-only review projection boundary: workers cannot approve in SQL."""
from __future__ import annotations

import os
import re

import httpx

from .application_results import ApplicationResults
from .consumer.contracts import ContractError, canonical, iso, strict_json
from .consumer.evidence import validate_reference, verify
from .consumer.sql_connect import managed_identity_sql_factory
from .consumer.storage import SqlServerWorkflowStore
from .consumer.storage_contract import EvidenceRef, IntegrityConflict
from .hosting import _required, _scope_configuration, create_review_app
from .portfolio import _url
from .review import SqlServerReviewRepository
from .review.contracts import AGENTS, OVERLAY, ReviewError, result_header, safe_overlay, source as source_header
from .service_auth import ServiceAuthenticator, application_token_scope, request_document


class ProjectionDependencyError(RuntimeError):
    """Fixed diagnostic codes only; never include response bodies or tokens."""


def _projection_response(response):
    if response.is_success:
        return
    reason = "unclassified"
    if len(response.content) <= 4096:
        try:
            value = strict_json(response.content)
        except ContractError:
            value = None
        known = {
            "service authentication required": "authentication_required",
            "invalid service authentication": "invalid_authentication",
            "service caller is not authorized": "caller_not_authorized",
            "invalid_projection_request": "invalid_request",
            "unavailable_or_conflicting_result": "conflicting_result",
            "unknown_result": "unknown_result",
        }
        if isinstance(value, dict) and isinstance(value.get("error"), str):
            reason = known.get(value["error"], reason)
    raise ProjectionDependencyError(f"review_projection_http_{response.status_code}_{reason}")


class RemoteResultProjector:
    def __init__(self, origin, audience, credential, client, *, token_scope):
        _url(origin, "review origin")
        self.origin, self.audience, self.credential, self.client = origin.rstrip("/"), audience, credential, client
        self.token_scope = application_token_scope(token_scope)

    def __call__(self, result):
        response = self.client.post(
            self.origin + "/internal/results/project",
            headers={"Authorization": "Bearer " + self.credential.get_token(
                self.token_scope).token},
            json={"workflow_id": result["workflow_id"]}, timeout=30, follow_redirects=False)
        _projection_response(response)
        if len(response.content) > 4096:
            raise ContractError("projection response exceeds its bounded contract")
        value = strict_json(response.content)
        if (not isinstance(value, dict) or set(value) != {"result_id", "analysis_revision", "etag"}
                or value["result_id"] != result["result_id"]
                or type(value["analysis_revision"]) is not int
                or value["analysis_revision"] != result["analysis_revision"]
                or not isinstance(value["etag"], str)):
            raise IntegrityConflict("remote projection did not acknowledge the exact durable result")
        return value

    def project_failure(self, workflow_id):
        if not isinstance(workflow_id, str) or not re.fullmatch(r"[a-f0-9]{64}", workflow_id):
            raise ContractError("an exact durable workflow digest is required")
        response = self.client.post(
            self.origin + "/internal/results/project-failure",
            headers={"Authorization": "Bearer " + self.credential.get_token(
                self.token_scope).token},
            json={"workflow_id": workflow_id}, timeout=30, follow_redirects=False)
        _projection_response(response)
        value = request_document(response.content, maximum=4096)
        if (set(value) != {"result_id", "analysis_revision", "etag"}
                or value["result_id"] != workflow_id
                or type(value["analysis_revision"]) is not int or value["analysis_revision"] <= 0
                or not isinstance(value["etag"], str) or not 0 < len(value["etag"]) <= 256):
            raise IntegrityConflict("remote failure projection did not acknowledge the durable workflow")
        return value

    def probe(self):
        response = self.client.get(
            self.origin + "/internal/results/readiness",
            headers={"Authorization": "Bearer " + self.credential.get_token(
                self.token_scope).token},
            timeout=10, follow_redirects=False)
        _projection_response(response)
        value = request_document(response.content, maximum=4096)
        expected = {"status": "verified", "required_role": "Results.Project",
                    "probe_kind": "projection_authentication_only",
                    "inference_performed": False, "embedding_performed": False,
                    "result_written": False}
        if value != expected or any(value[key] is not False for key in (
                "inference_performed", "embedding_performed", "result_written")):
            raise ContractError("projection readiness differs from its read-only contract")
        return value


class ResultProjectionService:
    def __init__(self, auth, results, evidence, repository, sources):
        self.auth, self.results, self.evidence, self.repository = auth, results, evidence, repository
        self.sources = sources

    def readiness(self, headers):
        self.auth.authenticate(headers, "Results.Project")
        return {"status": "verified", "required_role": "Results.Project",
                "probe_kind": "projection_authentication_only",
                "inference_performed": False, "embedding_performed": False,
                "result_written": False}

    def project(self, headers, body):
        self.auth.authenticate(headers, "Results.Project")
        if set(body) != {"workflow_id"}:
            raise ContractError("only an immutable SQL workflow identity may be submitted for projection")
        row = self.results.get(body["workflow_id"])
        if row["result"] is None:
            raise IntegrityConflict("indeterminate or incomplete work cannot enter the review projection")
        result = result_header(strict_json(self.evidence.read(EvidenceRef(**row["result"]))))
        source = result["source"]
        configured = self.sources.get((source["producer_key"], source["run_id"], source["domain"]))
        if configured is None:
            configured = self.sources.get((source["producer_key"], source["run_id"]))
        if (configured is None or source["domain"] != configured["domain"]
                or not set(source["surfaces"]).issubset(configured["surfaces"])
                or result["workflow_id"] != row["workflow_id"] or result["result_id"] != row["workflow_id"]
                or result["incident_id"] != row["incident_id"] or result["analysis_revision"] != row["revision"]
                or source["producer_key"] != row["producer_key"] or source["run_id"] != row["run_id"]):
            raise IntegrityConflict("projection differs from the server-held source and SQL result receipt")
        revision = self.repository.register_result(result)
        return {"result_id": result["result_id"], "analysis_revision": result["analysis_revision"],
                "etag": revision["etag"]}

    def project_failure(self, headers, body):
        self.auth.authenticate(headers, "Results.Project")
        if not isinstance(body, dict) or set(body) != {"workflow_id"}:
            raise ContractError("only an immutable SQL workflow identity may be submitted for projection")
        workflow_id = body["workflow_id"]
        row = self.results.get(workflow_id)
        job = self.results.store.get_job(workflow_id)
        if (row["workflow_id"] != workflow_id or job["job_id"] != workflow_id
                or row["job_kind"] != "reasoning" or job["kind"] != "reasoning"
                or type(row["revision"]) is not int or row["revision"] != 1
                or row["result"] is not None or row["projected"]
                or job["status"] != "failed" or job["error_code"] != "AgentFailure"
                or job["finished_at"] is None or job["expires_at"] is not None
                or job["result"] is not None or job["result_sha256"] is not None or job["next_job_ids"]):
            raise IntegrityConflict("failure projection requires a terminal failed first reasoning attempt without a result")
        try:
            reference = EvidenceRef(**row["candidate"])
        except (TypeError, KeyError):
            raise IntegrityConflict("invalid immutable candidate reference") from None
        validate_reference(reference)
        candidate = strict_json(verify(self.evidence.read(reference), reference))
        payload = job["payload"]
        if not isinstance(candidate, dict) or not isinstance(payload, dict):
            raise IntegrityConflict("failure projection requires an immutable synthetic candidate")
        for value in (candidate, payload):
            safe_overlay(value)
            if (any(type(value.get(key)) is not type(expected) or value.get(key) != expected
                    for key, expected in OVERLAY.items())
                    or "trigger" in value or "replacement_of" in value):
                raise IntegrityConflict("failure projection requires an original synthetic detector workflow")
        source = source_header(candidate["source"])
        configured = self.sources.get((source["producer_key"], source["run_id"], source["domain"]))
        if configured is None:
            configured = self.sources.get((source["producer_key"], source["run_id"]))
        if (configured is None or source["domain"] != configured["domain"]
                or not set(source["surfaces"]).issubset(configured["surfaces"])
                or candidate["incident_id"] != row["incident_id"]
                or payload.get("incident_id") != row["incident_id"]
                or payload.get("candidate_evidence") != row["candidate"]
                or any(row[key] != source[key] or (key in payload and payload[key] != source[key])
                       for key in ("producer_key", "run_id"))
                or ("source" in payload and payload["source"] != source)):
            raise IntegrityConflict("failure projection differs from the server-held source and candidate")
        if (not isinstance(row["portfolio_hash"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", row["portfolio_hash"])):
            raise IntegrityConflict("failure projection requires the immutable portfolio digest")
        result = result_header({
            "schema_version": "1.0", "result_id": workflow_id, "workflow_id": workflow_id,
            "incident_id": row["incident_id"], "analysis_revision": row["revision"],
            "status": "failed", "observed_lifecycle": "unknown", "source": source,
            "evidence_refs": [],
            "stage_results": {
                name: {"status": "incomplete",
                       "error": "No validated final outputs were retained for this failed workflow."}
                for name in AGENTS if name not in {"postmortem-drafter", "alert-hygiene"}
            },
            "versions": {"failure_projection": "1", "portfolio": row["portfolio_hash"]},
            "created_at_utc": iso(job["created_at"]),
            "summary": "Failed-workflow diagnostic, not a completed model analysis",
            "coverage": {
                "report_kind": "failed_workflow_diagnostic", "failed_workflow_id": workflow_id,
                "candidate_evidence": row["candidate"],
                "diagnostic": {"error_code": "AgentFailure"},
            },
            **OVERLAY,
        })
        revision = self.repository.register_result(result)
        # A projection receipt pins the initial revision, not the mutable review CAS.
        # Replaying after review or revision 2 must not mint a new acknowledgement.
        return {"result_id": workflow_id, "analysis_revision": row["revision"],
                "etag": f'"1:1:{revision["result_hash"]}"'}


def create_review_service(environment=None):
    """Gunicorn WSGI factory; separate middleware mount bypasses no user auth."""
    from flask import Flask, request
    from werkzeug.middleware.dispatcher import DispatcherMiddleware
    from .service_runtime import _evidence, _identity
    from .review.advancement import RemotePublisherAdvancer, RemoteWorkAdvancer

    environment = os.environ if environment is None else environment
    credential = _identity(environment)
    review = create_review_app(environment, work_advancer=RemoteWorkAdvancer(
        _required(environment, "ITOPS_WORKER_ORIGIN"), credential,
        httpx.Client(transport=httpx.HTTPTransport(retries=0)),
        token_scope=_required(environment, "ITOPS_WORKER_TOKEN_SCOPE")),
        publication_advancer=RemotePublisherAdvancer(
            _required(environment, "ITOPS_PUBLISHER_ORIGIN"), credential,
            httpx.Client(transport=httpx.HTTPTransport(retries=0)),
            token_scope=_required(environment, "ITOPS_PUBLISHER_TOKEN_SCOPE")))
    factory = managed_identity_sql_factory(
        _required(environment, "ITOPS_SQL_SERVER"), _required(environment, "ITOPS_SQL_DATABASE"),
        _required(environment, "AZURE_CLIENT_ID"))
    auth = ServiceAuthenticator(
        _required(environment, "ENTRA_TENANT_ID"), _required(environment, "ITOPS_PROJECTION_AUDIENCE"),
        strict_json(_required(environment, "ITOPS_PROJECTION_CALLERS_JSON")))
    sources, _ = _scope_configuration(_required(environment, "ITOPS_REVIEW_SCOPES_JSON"))
    service = ResultProjectionService(
        auth, ApplicationResults(SqlServerWorkflowStore(factory)), _evidence(environment, credential),
        SqlServerReviewRepository(factory), sources)
    projection = Flask("immutable-result-projection")
    projection.config["MAX_CONTENT_LENGTH"] = 4096

    @projection.get("/readiness")
    def readiness():
        try:
            return projection.response_class(canonical(service.readiness(request.headers)),
                                             mimetype="application/json")
        except ReviewError as exc:
            return {"error": str(exc)}, exc.status

    @projection.post("/project")
    def project():
        try:
            value = service.project(request.headers, request_document(request.get_data(), maximum=4096))
            return projection.response_class(canonical(value), mimetype="application/json")
        except ReviewError as exc:
            return {"error": str(exc)}, exc.status
        except ContractError:
            return {"error": "invalid_projection_request"}, 422
        except IntegrityConflict:
            return {"error": "unavailable_or_conflicting_result"}, 409
        except KeyError:
            return {"error": "unknown_result"}, 404

    @projection.post("/project-failure")
    def project_failure():
        try:
            value = service.project_failure(
                request.headers, request_document(request.get_data(), maximum=4096))
            return projection.response_class(canonical(value), mimetype="application/json")
        except ReviewError as exc:
            return {"error": str(exc)}, exc.status
        except ContractError:
            return {"error": "invalid_projection_request"}, 422
        except IntegrityConflict:
            return {"error": "unavailable_or_conflicting_result"}, 409
        except KeyError:
            return {"error": "unknown_result"}, 404

    @projection.after_request
    def protect(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    return DispatcherMiddleware(review, {"/internal/results": projection})
