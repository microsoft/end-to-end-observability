"""Strict private tool requests authenticated at the dedicated Azure queue."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from operations.lifecycle import canonical_uuid, normalized_target

COMMANDS = {"inspect", "telemetry", "patch_jobs", "backup_jobs"}
MAX_REQUEST_BYTES = 16384


def parse_request(raw: str, now: datetime | None = None) -> dict:
    if not isinstance(raw, str) or len(raw.encode()) > MAX_REQUEST_BYTES:
        raise ValueError("A bounded UTF-8 JSON request is required.")
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {"request_id", "command", "resource_id", "sent_at"}:
        raise ValueError("Unexpected or missing tool request fields.")
    canonical_uuid(value["request_id"])
    value["resource_id"] = normalized_target(value["resource_id"])
    if not isinstance(value["command"], str) or value["command"] not in COMMANDS:
        raise PermissionError("Only the deployed review-only evidence commands are admitted.")
    sent = datetime.fromisoformat(value["sent_at"])
    if sent.tzinfo is None:
        raise ValueError("The request needs an aware timestamp.")
    age = ((now or datetime.now(timezone.utc)) - sent).total_seconds()
    if not -30 <= age <= 600:
        raise ValueError("The tool request is expired or dated in the future.")
    return value


def fingerprint(request: dict) -> str:
    return hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
