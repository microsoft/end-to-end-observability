"""Standalone V2 Flask gateway; never imports or initializes the V1 gateway."""

import atexit
import hmac
import os
from pathlib import Path
import secrets
from urllib.parse import urlsplit

from flask import Flask, jsonify, request, session
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

from .contracts import SURFACES
from .runtime import Runtime, Backpressure, CommandConflict
from .demo_runtime import DemoRuntime
from .paths import KIT_ROOT
from .transport import (
    BlobStateStore, LocalStateStore, IngestionSender, LocalCaptureSender,
    StoreConflict, StoreUnavailable,
)
from .ui import selector


def _required(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required V2 setting is missing: {name}")
    return value


def configured_runtime():
    from . import inventory, monitoring, replay, readiness
    hosted = bool(os.getenv("WEBSITE_SITE_NAME") or os.getenv("WEBSITE_INSTANCE_ID"))
    mode = os.getenv("V2_MODE", "azure" if hosted else "local")
    if hosted and mode != "azure":
        raise RuntimeError("Hosted V2 must use durable Azure storage and ingestion")
    if mode == "azure":
        store = BlobStateStore(_required("V2_STORAGE_ACCOUNT_URL"), _required("V2_STORAGE_CONTAINER"))
        sender = IngestionSender(_required("V2_LOGS_ENDPOINT"), _required("V2_DCR_IMMUTABLE_ID"))
        delivery_mode = "azure"
    elif mode == "local":
        root = Path(os.getenv("V2_LOCAL_STATE", str(KIT_ROOT / ".local")))
        store, sender = LocalStateStore(root / "state"), LocalCaptureSender(root / "capture")
        delivery_mode = "local_capture"
    else:
        raise RuntimeError("V2_MODE must be azure or local")
    checker = (readiness.AzureReadinessChecker(_required("V2_LOG_ANALYTICS_WORKSPACE_ID"))
               if delivery_mode == "azure" else readiness.LocalReadinessChecker(root / "capture"))
    return DemoRuntime(store, sender, {"inventory": inventory, "monitoring": monitoring},
                       replay=replay, readiness=checker, delivery_mode=delivery_mode)


def create_app(runtime=None, *, worker=None):
    app = Flask(__name__, static_folder="static", static_url_path="/v2/static")
    hosted = bool(os.getenv("WEBSITE_SITE_NAME") or os.getenv("WEBSITE_INSTANCE_ID"))
    origin = os.getenv("V2_PUBLIC_ORIGIN", "http://127.0.0.1:8880").rstrip("/")
    parsed_origin = urlsplit(origin)
    if parsed_origin.scheme not in {"http", "https"} or not parsed_origin.netloc or parsed_origin.path:
        raise RuntimeError("V2_PUBLIC_ORIGIN must be an origin without a path")
    if hosted and parsed_origin.scheme != "https":
        raise RuntimeError("Hosted V2 requires an HTTPS public origin")
    app.config.update(
        SECRET_KEY=secrets.token_bytes(32), MAX_CONTENT_LENGTH=16_384,
        SESSION_COOKIE_NAME="__Host-v2-session" if hosted else "v2-session",
        SESSION_COOKIE_SECURE=hosted, SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        TRUSTED_HOSTS=[parsed_origin.hostname, "localhost", "127.0.0.1"],
    )
    runtime = runtime or configured_runtime()
    app.extensions["v2_runtime"] = runtime
    maintenance_value = os.getenv("V2_MAINTENANCE_MODE", "0")
    if maintenance_value not in {"0", "1"}:
        raise RuntimeError("V2_MAINTENANCE_MODE must be 0 or 1")
    maintenance = maintenance_value == "1"
    enabled = not maintenance and (
        worker if worker is not None else os.getenv("V2_WORKER_ENABLED", "1") == "1")

    @app.before_request
    def maintenance_barrier():
        if maintenance and request.method not in {"GET", "HEAD", "OPTIONS"}:
            return jsonify(error="maintenance", message="V2 state maintenance: mutations are disabled"), 503

    @app.after_request
    def response_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(ValueError)
    def invalid(error):
        code = 429 if isinstance(error, Backpressure) else 409 if isinstance(error, CommandConflict) else 400
        return jsonify(error="invalid_command", message=str(error)), code

    @app.errorhandler(StoreConflict)
    def conflict(_error):
        return jsonify(error="state_conflict", message="State changed concurrently; retry the same idempotency key"), 409

    @app.errorhandler(StoreUnavailable)
    def unavailable(_error):
        return jsonify(error="storage_unavailable", message="Durable V2 storage is unavailable; no change accepted"), 503

    @app.errorhandler(BadRequest)
    @app.errorhandler(RequestEntityTooLarge)
    def bad_request(error):
        return jsonify(error="invalid_request", message="Send a bounded, valid JSON request"), error.code

    @app.get("/")
    def index():
        return selector()

    @app.get("/api/health/live")
    def live():
        return jsonify(status="live", application="energy-domains-v2",
                       maintenance=maintenance, worker_enabled=enabled,
                       fixture_version=getattr(runtime, "fixture_version", None))

    @app.get("/api/health/ready")
    def ready():
        runtime.store.keys()
        healthy = not enabled or runtime.worker_alive
        return jsonify(status="ready" if healthy else "worker_failed"), 200 if healthy else 503

    @app.get("/api/v2/session")
    def csrf_session():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return jsonify(csrf_token=session["csrf"])

    @app.get("/api/v2/surfaces/<surface>")
    def surface_status(surface):
        return jsonify(runtime.status(surface))

    @app.get("/api/v2/surfaces/<surface>/observations")
    def surface_observations(surface):
        if not isinstance(runtime, DemoRuntime):
            return jsonify(error="demo_runtime_required"), 404
        return jsonify(runtime.log_details(surface))

    def authorize_command():
        if request.headers.get("Origin", "").rstrip("/") != origin:
            return jsonify(error="origin_rejected", message="Commands require the configured same-origin portal"), 403
        token = request.headers.get("X-CSRF-Token", "")
        expected = session.get("csrf", "")
        if not expected or not hmac.compare_digest(token, expected):
            return jsonify(error="csrf_rejected", message="Refresh the portal before submitting a command"), 403

    @app.get("/api/v2/demo")
    def demo_status():
        if not isinstance(runtime, DemoRuntime):
            return jsonify(error="demo_runtime_required"), 404
        return jsonify(runtime.session_status())

    @app.post("/api/v2/demo/commands")
    def demo_command():
        denied = authorize_command()
        if denied:
            return denied
        if not isinstance(runtime, DemoRuntime):
            return jsonify(error="demo_runtime_required"), 404
        body = request.get_json()
        if not isinstance(body, dict) or set(body) - {"action", "surfaces", "duration_seconds"}:
            raise ValueError("Unsupported demo command fields")
        if "surfaces" in body and not isinstance(body["surfaces"], list):
            raise ValueError("Surfaces must be an explicit list")
        if body.get("action") != "prepare" and set(body) - {"action"}:
            raise ValueError("Only prepare accepts surfaces or duration_seconds")
        return jsonify(runtime.session_command(
            body.get("action"), key=request.headers.get("Idempotency-Key"),
            surfaces=body.get("surfaces"), duration_seconds=body.get("duration_seconds", 7200))), 202

    @app.post("/api/v2/surfaces/<surface>/commands")
    def command(surface):
        denied = authorize_command()
        if denied:
            return denied
        body = request.get_json()
        demo = isinstance(runtime, DemoRuntime)
        duration_field = "anomaly_duration_seconds" if demo else "duration_seconds"
        if not isinstance(body, dict) or set(body) - {"action", "scenario", duration_field, "seed"}:
            raise ValueError("Unsupported command fields")
        result = runtime.command(
            surface, body.get("action"), body.get("scenario"),
            request.headers.get("Idempotency-Key"), body.get(duration_field, 300 if demo else 900),
            body.get("seed", 42),
        )
        return jsonify(result), 202

    runtime.adapters["monitoring"].register_routes(app, runtime)
    runtime.adapters["inventory"].register_routes(app, runtime)
    if enabled:
        runtime.start_worker()
        atexit.register(runtime.close)
    return app
