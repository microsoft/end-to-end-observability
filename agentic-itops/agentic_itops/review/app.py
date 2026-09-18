"""Flask app factory; production construction fails closed without explicit trust.

create_app(config, repository=..., work_submitter=..., entitlements={oid:
{(producer_key, run_id)}}, source_catalog={(producer_key, run_id): source},
authenticator=EntraAuthenticator(...)).

Required production config: PUBLIC_ORIGIN (HTTPS, no path), SECRET_KEY (>=32
characters), ENTRA_TENANT_ID and ENTRA_AUDIENCE unless an EntraAuthenticator
is injected. ALLOW_EASYAUTH defaults False. TEST_AUTHENTICATOR is permitted
only with explicit TESTING=True. Nothing reads an environment dev bypass.
No public route registers results or dispatches operational actions.
"""
from __future__ import annotations

from functools import wraps
import hmac
import json
import re
import secrets
from urllib.parse import urlsplit

from flask import Flask, g, jsonify, render_template, request, session
from werkzeug.exceptions import HTTPException

from .auth import EntraAuthenticator, Principal, ROLES
from .contracts import (
    AGENTS, OVERLAY, ReviewError, identifier, require, source, work_payload, investigation_payload,
)
from .repository import ReviewRepository
from .work import WorkSubmitter


def create_app(config=None, *, repository: ReviewRepository, work_submitter: WorkSubmitter | None = None, entitlements=None,
               source_catalog=None, authenticator=None, work_advancer=None, publication_advancer=None):
    app = Flask(__name__)
    app.config.update(
        MAX_CONTENT_LENGTH=2_000_000, SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
        ALLOW_EASYAUTH=False, TESTING=False,
    )
    app.config.update(config or {})
    origin = app.config.get("PUBLIC_ORIGIN", "")
    parsed = urlsplit(origin)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or origin != f"https://{parsed.netloc}" or parsed.query or parsed.fragment):
        raise ValueError("PUBLIC_ORIGIN must be an explicit HTTPS origin without a path")
    secret = app.config.get("SECRET_KEY")
    if not isinstance(secret, (str, bytes)) or len(secret) < 32:
        raise ValueError("an explicit high-entropy SECRET_KEY of at least 32 characters is required")
    test_authenticator = app.config.get("TEST_AUTHENTICATOR")
    if test_authenticator is not None and not app.testing:
        raise ValueError("test authenticator is prohibited outside app.testing")
    if authenticator is not None and not isinstance(authenticator, EntraAuthenticator):
        raise ValueError("inject EntraAuthenticator, or TEST_AUTHENTICATOR only in app.testing")
    auth = test_authenticator if test_authenticator is not None else authenticator
    if auth is None:
        auth = EntraAuthenticator(app.config.get("ENTRA_TENANT_ID"), app.config.get("ENTRA_AUDIENCE"),
                                  allow_easyauth=app.config["ALLOW_EASYAUTH"])
    scopes = {}
    for oid, pairs in (entitlements or {}).items():
        pairs = frozenset(tuple(pair) for pair in pairs)
        require(all(len(pair) in (2, 3) and "*" not in pair for pair in pairs), "explicit source scopes required")
        scopes[oid] = pairs
    catalog = {key: source(json.loads(json.dumps(value))) for key, value in (source_catalog or {}).items()}
    require(all(key in ((value["producer_key"], value["run_id"]),
                        (value["producer_key"], value["run_id"], value["domain"])) for key, value in catalog.items()),
            "catalog key/source mismatch")
    catalog = {(v["producer_key"], v["run_id"], v["domain"]): v for v in catalog.values()}
    for pairs in scopes.values():
        for pair in pairs:
            require(len(pair) == 3 or sum(key[:2] == pair for key in catalog) <= 1,
                    "multi-domain catalog requires domain-specific entitlements")
    app.extensions["review_repository"] = repository
    app.extensions["review_work_submitter"] = work_submitter

    @app.template_filter("pretty")
    def pretty(value):
        return value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False)

    def allowed(value):
        pair = (value.get("producer_key"), value.get("run_id"))
        key = (*pair, value.get("domain"))
        if pair not in g.scopes and key not in g.scopes:
            return False
        if catalog:
            permitted = catalog.get(key)
            return permitted is not None and set(value.get("surfaces", ())).issubset(permitted["surfaces"])
        return True

    def scope_check(value):
        require(allowed(value), "scope forbidden", 403)

    def scoped_job_source(job_id, value):
        from .advancement import descendant_source
        contexts = request.args.getlist("root_job_id")
        require(len(contexts) <= 1, "only one root job context is accepted")
        resolved = value.get("source") or (value.get("payload") or {}).get("source") or {}
        if contexts:
            root_id = contexts[0]
            require(re.fullmatch(r"[a-f0-9]{64}", root_id), "exact root job digest required")
            root = work_submitter.get_job(root_id)
            require(isinstance(root, dict), "root job not found", 404)
            scope_check(root.get("source") or (root.get("payload") or {}).get("source") or {})
            resolved = descendant_source(work_submitter, root_id, job_id, root)
        scope_check(resolved)
        return resolved

    def visible_incidents():
        return [item for item in repository.list_incidents({pair[:2] for pair in g.scopes})
                if allowed(item["result"]["source"])]

    def role_check(*roles):
        require(bool(g.principal.roles & set(roles)), "role forbidden", 403)

    def authorized(*roles):
        def decorator(function):
            @wraps(function)
            def wrapper(*args, **kwargs):
                role_check(*roles)
                return function(*args, **kwargs)
            return wrapper
        return decorator

    @app.before_request
    def authenticate():
        g.csp_nonce = secrets.token_urlsafe(24)
        if request.endpoint == "health":
            return None
        require(test_authenticator is None or app.testing, "test authentication is disabled", 401)
        g.principal = auth.authenticate(request)
        require(isinstance(g.principal, Principal) and g.principal.method in {"bearer", "easyauth"},
                "invalid authentication", 401)
        role_check(*ROLES)
        g.scopes = scopes.get(g.principal.oid, frozenset())
        if request.method not in {"GET", "HEAD", "OPTIONS"} and g.principal.method != "bearer":
            require(request.headers.get("Origin") == origin, "origin forbidden", 403)
            require(session.get("csrf_oid") == g.principal.oid, "CSRF identity mismatch", 403)
            supplied, expected = request.headers.get("X-CSRF-Token", ""), session.get("csrf", "")
            require(bool(supplied) and bool(expected) and hmac.compare_digest(supplied, expected),
                    "CSRF token required", 403)

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self' 'nonce-" + g.csp_nonce + "'; "
            "style-src 'self' 'nonce-" + g.csp_nonce + "'; img-src 'self'; "
            "connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
        )
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.errorhandler(ReviewError)
    def review_error(error):
        return jsonify({"error": str(error), **OVERLAY}), error.status

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify({"error": error.name, **OVERLAY}), error.code

    @app.errorhandler(Exception)
    def dependency_error(error):
        # The exception may contain a SQL connection string or a bearer token.
        return jsonify({"error": "review dependency unavailable", **OVERLAY}), 503

    def body():
        require(request.is_json, "application/json required")
        try:
            def pairs(items):
                result = {}
                for key, value in items:
                    require(key not in result, "duplicate JSON field")
                    result[key] = value
                return result
            value = json.loads(request.get_data(), object_pairs_hook=pairs,
                               parse_constant=lambda _: (_ for _ in ()).throw(ReviewError("nonfinite JSON")))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ReviewError("invalid JSON") from exc
        require(isinstance(value, dict), "JSON object required")
        return value

    def write_headers(cas=True):
        key = identifier(request.headers.get("Idempotency-Key"), "Idempotency-Key")
        expected = request.headers.get("If-Match")
        if cas:
            require(isinstance(expected, str) and len(expected) <= 150, "exact If-Match required")
        return key, expected

    def scoped_revision(incident_id, revision=None):
        identifier(incident_id, "incident_id")
        revision = repository.current_revision(incident_id) if revision is None else revision
        value = repository.get_revision(incident_id, revision)
        scope_check(value["result"]["source"])
        return value

    @app.get("/health")
    def health():
        return jsonify({"status": "alive"})

    @app.get("/csrf")
    def csrf():
        token = secrets.token_urlsafe(32)
        session["csrf"], session["csrf_oid"] = token, g.principal.oid
        return jsonify({"csrf_token": token, **OVERLAY})

    @app.get("/")
    def dashboard():
        require(set(request.args) <= {"incident_id", "revision", "scoutTheme"}, "unknown query field")
        detail = None
        if request.args.get("incident_id"):
            revision = request.args.get("revision")
            require(revision is None or revision.isdecimal(), "invalid revision")
            detail = scoped_revision(request.args["incident_id"], int(revision) if revision else None)
        session["csrf"], session["csrf_oid"] = secrets.token_urlsafe(32), g.principal.oid
        return render_template(
            "dashboard.html", incidents=visible_incidents(), detail=detail,
            agents=AGENTS, roles=g.principal.roles, csrf_token=session["csrf"],
            sources=[value for value in catalog.values() if allowed(value)],
            work_available=work_submitter is not None, advance_available=work_advancer is not None,
            publication_advance_available=publication_advancer is not None,
            nonce=g.csp_nonce,
        )

    @app.get("/incidents")
    def incidents():
        require(set(request.args) <= {"cursor", "limit", "producer_key", "run_id", "domain", "surface"},
                "unknown query field")
        limit = request.args.get("limit", "50")
        require(limit.isdecimal() and 1 <= int(limit) <= 100, "limit must be 1..100")
        values = visible_incidents()
        cursor = request.args.get("cursor", "")
        values = [value for value in values if value["result"]["incident_id"] > cursor]
        for key in ("producer_key", "run_id", "domain", "surface"):
            if key in request.args:
                values = [value for value in values if (
                    request.args[key] in value["result"]["source"]["surfaces"] if key == "surface"
                    else request.args[key] == value["result"]["source"][key]
                )]
        page = values[:int(limit)]
        return jsonify({
            "incidents": page,
            "next_cursor": page[-1]["result"]["incident_id"] if len(values) > len(page) else None, **OVERLAY,
        })

    @app.get("/incidents/<incident_id>/revisions/<int:revision>")
    def incident_revision(incident_id, revision):
        value = scoped_revision(incident_id, revision)
        response = jsonify(value)
        response.headers["ETag"] = value["etag"]
        return response

    @app.get("/incidents/<incident_id>/revisions/<int:revision>/export")
    @authorized("Analyst", "Reviewer", "KnowledgeReviewer", "Operator")
    def export(incident_id, revision):
        value = scoped_revision(incident_id, revision)
        response = jsonify({"banner": "SYNTHETIC — OperationalUse=prohibited — no execution authorized",
                            **value, **OVERLAY})
        response.headers["Content-Disposition"] = 'attachment; filename="synthetic-review.json"'
        return response

    @app.post("/incidents/<incident_id>/reviews")
    @authorized("Reviewer")
    def review(incident_id):
        scoped_revision(incident_id)
        key, expected = write_headers()
        value = repository.review(g.principal.oid, incident_id, expected, key, body())
        response = jsonify(value)
        response.headers["ETag"] = value["etag"]
        return response

    @app.post("/publications")
    @authorized("KnowledgeReviewer")
    def publication():
        value = body()
        require("incident_id" in value, "incident_id required")
        scoped_revision(value["incident_id"])
        key, expected = write_headers()
        return jsonify(repository.approve_publication(g.principal.oid, expected, key, value)), 202

    @app.get("/publications/<publication_id>")
    def publication_detail(publication_id):
        identifier(publication_id, "publication_id")
        value = repository.get_publication(publication_id)
        scope_check(value["source"])
        response = jsonify(value)
        response.headers["ETag"] = value["etag"]
        return response

    @app.post("/publications/<publication_id>/advance")
    @authorized("KnowledgeReviewer")
    def advance_publication(publication_id):
        value = repository.get_publication(publication_id)
        scope_check(value["source"])
        require(body() == {}, "publication advancement accepts only an empty body")
        key, _ = write_headers(cas=False)
        require(publication_advancer is not None, "private publisher advancement unavailable", 503)
        selected = repository.reserve_publication_advance(
            g.principal.oid, request.path, key, publication_id)
        result = publication_advancer.advance(selected["outbox_id"]) if selected["status"] == "selected" else None
        return jsonify({**selected, "advanced": result, **OVERLAY})

    @app.post("/publications/<publication_id>/revoke")
    @authorized("KnowledgeReviewer")
    def revoke(publication_id):
        identifier(publication_id, "publication_id")
        value = repository.get_publication(publication_id)
        scope_check(value["source"])
        key, expected = write_headers()
        return jsonify(repository.revoke_publication(g.principal.oid, publication_id, expected, key, body()))

    def submit_work():
        key, _ = write_headers(cas=False)
        validator = investigation_payload if request.path == "/investigation-jobs" else work_payload
        value = validator(body(), app.config.get("MAX_WORK_WINDOW_SECONDS", 86400 * 31))
        scope_check(value["source"])
        permitted = catalog.get((value["source"]["producer_key"], value["source"]["run_id"],
                                 value["source"]["domain"]))
        require(permitted is not None, "source is not registered for work", 403)
        require(value["source"]["domain"] == permitted["domain"]
                and set(value["source"]["surfaces"]) <= set(permitted["surfaces"]),
                "source domain/surfaces forbidden", 403)
        require(work_submitter is not None, "durable work submitter is not configured", 503)
        receipt = repository.submit_work(work_submitter, g.principal.oid, request.path, key, value)
        response = jsonify(receipt)
        response.headers["Location"] = f"/jobs/{receipt['job_id']}"
        return response, 202

    @app.post("/analysis-jobs")
    @authorized("Analyst", "Operator")
    def analysis_jobs():
        return submit_work()

    @app.post("/investigation-jobs")
    @authorized("Analyst", "Operator")
    def investigation_jobs():
        return submit_work()

    @app.post("/jobs/<job_id>/advance")
    @authorized("Analyst", "Operator")
    def advance(job_id):
        identifier(job_id, "job_id")
        require(len(job_id) == 64 and body() == {}, "only an exact job ID and empty body are accepted")
        key, _ = write_headers(cas=False)
        require(work_submitter is not None and work_advancer is not None, "private advancement unavailable", 503)
        root = work_submitter.get_job(job_id)
        require(root is not None, "job not found", 404)
        scoped_job_source(job_id, root)
        selected = repository.reserve_work_advance(
            work_submitter, g.principal.oid, request.path, key, job_id)
        result = work_advancer.advance(selected["job_id"]) if selected["status"] == "selected" else None
        return jsonify({**selected, "advanced": result, **OVERLAY})

    @app.post("/jobs/<job_id>/projection-jobs")
    @authorized("Operator")
    def projection_jobs(job_id):
        require(re.fullmatch(r"[a-f0-9]{64}", job_id), "exact completed workflow digest required")
        require(body() == {}, "projection recovery accepts only an empty body")
        key, _ = write_headers(cas=False)
        require(work_submitter is not None and work_advancer is not None,
                "private projection recovery unavailable", 503)
        parent = work_submitter.get_job(job_id)
        require(isinstance(parent, dict), "job not found", 404)
        parent_source = scoped_job_source(job_id, parent)
        result = parent.get("result") or {}
        require(parent.get("kind") in {"reasoning", "postmortem", "hygiene"}
                and parent.get("status") == "completed" and isinstance(result, dict)
                and result.get("status") == "result_persisted" and result.get("result_id") == job_id
                and isinstance(result.get("result_evidence"), dict),
                "projection recovery requires a completed persisted model result", 409)
        return jsonify(repository.submit_projection(
            work_submitter, work_advancer, g.principal.oid, request.path, key,
            {"workflow_id": job_id, "source": parent_source,
             "result_evidence": result["result_evidence"]})), 202

    @app.post("/jobs/<job_id>/replacement-jobs")
    @authorized("Operator")
    def replacement_jobs(job_id):
        require(re.fullmatch(r"[a-f0-9]{64}", job_id), "exact failed workflow digest required")
        require(body() == {}, "replacement preparation accepts only an empty body")
        key, _ = write_headers(cas=False)
        require(work_submitter is not None and work_advancer is not None,
                "private replacement preparation unavailable", 503)
        parent = work_submitter.get_job(job_id)
        require(isinstance(parent, dict), "job not found", 404)
        parent_source = scoped_job_source(job_id, parent)
        payload = parent.get("payload") or {}
        require(parent.get("kind") == "reasoning" and parent.get("status") == "failed"
                and parent.get("error_code") == "AgentFailure" and parent.get("result") is None
                and isinstance(payload.get("candidate_evidence"), dict)
                and isinstance(payload.get("incident_id"), str) and "replacement_of" not in payload,
                "replacement requires the explicitly approved failed first reasoning attempt", 409)
        return jsonify(repository.submit_replacement(
            work_submitter, work_advancer, g.principal.oid, request.path, key,
            {"workflow_id": job_id, "source": parent_source, "incident_id": payload["incident_id"],
             "candidate_evidence": payload["candidate_evidence"]})), 202

    @app.post("/incidents/<incident_id>/lifecycle-jobs")
    @authorized("Analyst", "Operator")
    def lifecycle_job(incident_id):
        key, _ = write_headers(cas=False)
        value = body()
        require(set(value) == {"kind"} and value["kind"] in {"postmortem", "hygiene"}, "explicit lifecycle kind required")
        current = scoped_revision(incident_id)
        require(current["review_state"] == "closed" and current["eligible"], "current human closure required", 409)
        require(work_submitter is not None, "durable queue unavailable", 503)
        payload = {"source": current["result"]["source"], "incident_id": incident_id,
                   "analysis_revision": current["result"]["analysis_revision"],
                   "review_etag": current["etag"], "base_result_hash": current["result_hash"],
                   "lifecycle_request": value["kind"], **OVERLAY}
        return jsonify(repository.submit_lifecycle(
            work_submitter, g.principal.oid, request.path, key, payload)), 202

    @app.post("/hygiene-jobs")
    @authorized("Analyst", "Operator")
    def hygiene_jobs():
        return submit_work()

    @app.get("/jobs/<job_id>")
    def job(job_id):
        from .advancement import descendants
        identifier(job_id, "job_id")
        require(len(job_id) <= 64, "job_id exceeds queue contract")
        require(work_submitter is not None, "durable work submitter is not configured", 503)
        value = work_submitter.get_job(job_id)
        require(value is not None, "job not found", 404)
        require(isinstance(value, dict), "invalid job projection", 503)
        payload = value.get("payload") or {}
        job_source = scoped_job_source(job_id, value)
        processing = "running" if value.get("status") == "leased" else value.get("status", "unknown")
        result = value.get("result") if isinstance(value.get("result"), dict) else {}
        # Consumer/model errors are not rendered verbatim: they may contain secrets.
        return jsonify({
            "job_id": job_id, "source": job_source, "status": processing,
            "queue_state": value.get("status", "unknown"),
            "status_scope": "this_SQL_job_only_not_its_descendants",
            "next_job_ids": descendants(value),
            "window": value.get("window", payload.get("window")),
            "result_id": value.get("result_id") or result.get("result_id"),
            "failure": "Processing failed; consult authorized redacted telemetry."
            if value.get("status") == "failed" else None, **OVERLAY,
        })

    def scoped_diagnostic_job(job_id):
        identifier(job_id, "job_id")
        require(len(job_id) == 64, "exact model workflow job ID required")
        require(work_submitter is not None and work_advancer is not None,
                "private budget diagnostics unavailable", 503)
        value = work_submitter.get_job(job_id)
        require(value is not None, "job not found", 404)
        scoped_job_source(job_id, value)
        require(value.get("kind") in {"reasoning", "postmortem", "hygiene"},
                "budget diagnostics require a model workflow job", 409)
        return value

    @app.get("/job-budgets/<job_id>")
    @authorized("Operator")
    def job_budget(job_id):
        from .diagnostics import failure_diagnostic
        value = scoped_diagnostic_job(job_id)
        return jsonify({**work_advancer.budget(job_id),
                        "failure_diagnostic": failure_diagnostic(value)})

    @app.get("/job-readiness/<job_id>")
    @authorized("Operator")
    def job_readiness(job_id):
        scoped_diagnostic_job(job_id)
        return jsonify(work_advancer.readiness(job_id))

    return app
