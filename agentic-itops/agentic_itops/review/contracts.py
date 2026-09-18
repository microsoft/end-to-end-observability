"""Strict contracts for the independent, synthetic-only review projection."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
from uuid import UUID


AGENTS = (
    "ops-orchestrator", "triage-dedup", "rca-investigator", "runbook-memory",
    "remediation-planner", "incident-comms", "postmortem-drafter", "alert-hygiene",
    "industry-signal-pack",
)
PROCESSING = {"queued", "running", "blocked", "incomplete", "completed", "failed", "cancelled"}
LIFECYCLE = {"candidate", "active", "recovery_observed", "resolved", "unknown"}
OVERLAY = {"IsSynthetic": True, "OperationalUse": "prohibited", "execution_allowed": False}
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,179}\Z")


class ReviewError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def require(condition, message: str, status: int = 422):
    if not condition:
        raise ReviewError(message, status)


def canonical(value) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ReviewError("invalid JSON value") from exc


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def utc(value: str) -> datetime:
    require(isinstance(value, str), "UTC timestamp required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReviewError("invalid UTC timestamp") from exc
    require(parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0,
            "explicit absolute UTC timestamp required")
    return parsed.astimezone(timezone.utc)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def identifier(value, name="identifier"):
    require(isinstance(value, str) and TOKEN.fullmatch(value), f"invalid {name}")
    return value


def identity_oid(value):
    require(isinstance(value, str), "author/actor oid must be a UUID")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise ReviewError("author/actor oid must be a UUID") from exc
    require(parsed.int != 0, "author/actor oid cannot be a placeholder")
    return str(parsed)


def run_identifier(value):
    require(isinstance(value, str) and 0 < len(value) <= 256
            and all(ord(character) >= 32 for character in value), "invalid run_id")
    return value


def fields(value, required, optional=()):
    require(isinstance(value, dict), "object required")
    require(set(required) <= value.keys() and value.keys() <= set(required) | set(optional),
            "missing or unknown fields")


def text(value, name="reason", maximum=4000):
    require(isinstance(value, str) and 0 < len(value.strip()) <= maximum, f"nonempty {name} required")
    return value


def source(value):
    fields(value, {"producer_key", "run_id", "domain", "surfaces"})
    for name in ("producer_key", "domain"):
        identifier(value[name], name)
    run_identifier(value["run_id"])
    require(isinstance(value["surfaces"], list) and 0 < len(value["surfaces"]) <= 32,
            "bounded surfaces required")
    for surface in value["surfaces"]:
        identifier(surface, "surface")
    require(len(set(value["surfaces"])) == len(value["surfaces"]), "duplicate surfaces")
    return value


def safe_overlay(value, depth=0):
    require(depth <= 24, "result nesting exceeds limit")
    if isinstance(value, dict):
        for key, item in value.items():
            if key in OVERLAY:
                require(type(item) is type(OVERLAY[key]) and item == OVERLAY[key],
                        "synthetic safety overlay cannot be overridden")
            if key == "execution_status":
                require(item == "not_performed", "execution must remain not_performed")
            safe_overlay(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            safe_overlay(item, depth + 1)


def result_header(value):
    required = {
        "schema_version", "result_id", "incident_id", "analysis_revision", "workflow_id",
        "status", "source", "evidence_refs", "stage_results", "versions", "created_at_utc",
        *OVERLAY,
    }
    fields(value, required, {"observed_lifecycle", "summary", "timeline", "findings",
                            "coverage", "proposal", "gate_receipt", "postmortem_draft",
                            "knowledge_dependencies", "source_hash"})
    safe_overlay(value)
    require(len(canonical(value).encode("utf-8")) <= 2_000_000, "result too large")
    value = deepcopy(value)
    for name in ("schema_version", "result_id", "incident_id", "workflow_id"):
        identifier(value[name], name)
    require(type(value["analysis_revision"]) is int and value["analysis_revision"] > 0,
            "positive integer analysis_revision required")
    require(isinstance(value["status"], str) and value["status"] in PROCESSING, "invalid processing status")
    lifecycle = value.get("observed_lifecycle", "unknown")
    require(isinstance(lifecycle, str) and lifecycle in LIFECYCLE, "invalid observed lifecycle")
    source(value["source"])
    if "source_hash" in value:
        require(value["source_hash"] == digest(value["source"]), "source hash mismatch")
    utc(value["created_at_utc"])
    require(isinstance(value["versions"], dict) and bool(value["versions"]), "versions required")
    require(all(isinstance(k, str) and isinstance(v, str) and v.strip()
                for k, v in value["versions"].items()), "versions must be named strings")
    refs = value["evidence_refs"]
    require(isinstance(refs, list), "evidence_refs must be a list")
    for ref in refs:
        fields(ref, {"evidence_id", "sha256", "retained_until_utc", "verified"})
        identifier(ref["evidence_id"], "evidence_id")
        require(isinstance(ref["sha256"], str) and re.fullmatch(r"[a-f0-9]{64}", ref["sha256"]),
                "evidence sha256 required")
        require(type(ref["verified"]) is bool, "verified must be boolean")
        utc(ref["retained_until_utc"])
    require(len({ref["evidence_id"] for ref in refs}) == len(refs), "duplicate evidence")
    stages = value["stage_results"]
    require(isinstance(stages, dict) and stages.keys() <= set(AGENTS), "unknown agent stage")
    for stage in stages.values():
        fields(stage, {"status"}, {"output", "error", "evidence_refs", "invocation_id",
                                  "agent_version", "tool_calls", "triggered"})
        require(isinstance(stage["status"], str)
                and stage["status"] in PROCESSING | {"not-triggered", "pending"}, "invalid stage status")
        if "triggered" in stage:
            require(type(stage["triggered"]) is bool, "triggered must be boolean")
        if "evidence_refs" in stage:
            require(isinstance(stage["evidence_refs"], list)
                    and all(isinstance(ref, str) and ref in {r["evidence_id"] for r in refs}
                            for ref in stage["evidence_refs"]), "stage evidence is not bound")
        if "tool_calls" in stage:
            require(isinstance(stage["tool_calls"], list), "tool_calls must be a list")
        if "error" in stage:
            text(stage["error"], "stage error")
    if "postmortem_draft" in value:
        draft = value["postmortem_draft"]
        fields(draft, {"draft_id", "text", "sha256", "authorship", "applicability"})
        identifier(draft["draft_id"], "draft_id")
        text(draft["text"], "draft", 100_000)
        require(draft["sha256"] == digest(draft["text"]), "draft hash mismatch")
        fields(draft["authorship"], {"kind"}, {"oid"})
        require(isinstance(draft["authorship"]["kind"], str)
                and draft["authorship"]["kind"] in {"ai", "human"}, "explicit draft authorship required")
        if draft["authorship"]["kind"] == "human":
            draft["authorship"]["oid"] = identity_oid(draft["authorship"].get("oid"))
        else:
            require("oid" not in draft["authorship"], "AI draft cannot claim human authorship")
        text(draft["applicability"], "applicability")
    dependencies = value.get("knowledge_dependencies", [])
    require(isinstance(dependencies, list) and len(dependencies) <= 100, "bounded dependencies required")
    for dependency in dependencies:
        identifier(dependency, "publication dependency")
    for key in ("coverage", "proposal", "gate_receipt"):
        if key in value:
            require(isinstance(value[key], dict), f"{key} must be an object")
    for key in ("timeline", "findings"):
        if key in value:
            require(isinstance(value[key], list), f"{key} must be a list")
    return json.loads(canonical(value))


def work_payload(value, max_window_seconds=86400 * 31):
    fields(value, {"source", "window"})
    source(value["source"])
    fields(value["window"], {"start_utc", "end_utc"})
    start, end = (utc(value["window"][name]) for name in ("start_utc", "end_utc"))
    require(0 < (end - start).total_seconds() <= max_window_seconds, "invalid or excessive window")
    require(end <= datetime.now(timezone.utc), "retained-data window cannot end in the future")
    return value


def investigation_payload(value, max_window_seconds=7200):
    fields(value, {"source", "window", "target", "reason"})
    work_payload({key: value[key] for key in ("source", "window")}, min(max_window_seconds, 7200))
    fields(value["target"], {"surface", "entity_id", "location_id"})
    require(value["target"]["surface"] in value["source"]["surfaces"], "target surface outside source")
    for key, item in value["target"].items():
        text(item, key, 256)
    text(value["reason"], "investigation reason", 2000)
    return value
