"""Read models for native incident journeys, never an execution engine."""

import re
from datetime import datetime

from dashboard import context as deployment
from dashboard.queries import RULE_IDS

CTX = deployment.load()

PROFILE_IDS = ("disk", "patch-installation", "alert-recurrence", "capacity-growth", "backup-job")
PROFILE_NAMES = ("Filesystem pressure", "Patch installation", "Alert overlap", "Capacity growth", "Backup failure")
PRIMARY_SKILLS = ("asre-linux-rca", "asre-patch-backup-recovery", "asre-proactive-analysis",
                  "asre-proactive-analysis", "asre-patch-backup-recovery")
FOCUS = (
    "Inspect the exact filesystem, service observations and alert window. Explain cause and missing evidence.",
    "Verify a terminal failed installation, operation ID, failed patch count and provider errors. Never retry or reboot.",
    "Compare actual alert IDs and times, not Perf sample counts. Explain overlap without claiming deduplication.",
    "Check the baseline and forecast. Report an early capacity risk, not an outage or proven prevention.",
    "Verify an actual Failed Backup job for the exact VM, vault and protected item. Never start a replacement backup.",
)
SOURCES = ("filesystem", "patch", "alerts", "filesystem", "backup")
_ORIGIN = CTX.servicenow.origin.replace(".", r"\.") if CTX.servicenow else "(?!)"
CASE_LINK = re.compile(r"\[(INC\d{7})\]\((" + _ORIGIN + r"/incident\.do\?sys_id=([0-9a-f]{32}))\)")
STAGE_NAMES = {
    "signal": "Alert received", "route": "Plan & profile matched",
    "investigate": "Evidence & investigation", "case": "Case & findings",
    "verify": "Delivery verification",
}


def catalog() -> list:
    return [{
        "id": identity, "name": name, "rule_id": rule,
        "skills": [skill, "asre-case-and-verification"], "focus": focus,
        "source": source, "evidence_url": "/evidence#" + {
            "disk": "disk", "patch-installation": "patch", "alert-recurrence": "overlap",
            "capacity-growth": "capacity", "backup-job": "backup",
        }[identity],
        "qualification": "Automatic case delivery accepted historically; not a current case-state claim",
    } for identity, name, rule, skill, focus, source in zip(
        PROFILE_IDS, PROFILE_NAMES, RULE_IDS, PRIMARY_SKILLS, FOCUS, SOURCES)]


def profile_for(rule_id: str) -> dict:
    matches = [profile for profile in catalog() if profile["rule_id"].lower() == rule_id.lower()]
    if len(matches) != 1:
        raise ValueError("The incident does not belong to one of the five fixed ASRE profiles.")
    return matches[0]


def safe_text(text: str) -> str:
    text = re.sub(r"eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[redacted token]", text)
    text = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [redacted]", text)
    text = re.sub(r'(?im)(["\']?(?:client_secret|access_token|refresh_token|password|authorization)["\']?\s*[:=]\s*)[^\r\n]+',
                  r"\1[redacted]", text)
    return text[:2400]


def message_events(rows: list) -> tuple[list, list]:
    events, cases = [], {}
    for row in rows:
        author = row.get("author")
        if not isinstance(author, dict) or author.get("role") != "SREAgent":
            continue
        if row.get("messageType") is not None:
            continue
        stamp = row.get("timeStamp")
        if not isinstance(stamp, str):
            raise ValueError("A native message is missing its source timestamp.")
        try:
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("A native message timestamp is invalid.") from error
        if parsed.tzinfo is None:
            raise ValueError("A native message timestamp has no timezone.")
        hook = row.get("hookExecution")
        text = row.get("text") or ""
        if not isinstance(text, str):
            raise ValueError("A native update has an invalid text field.")
        event = {"id": row.get("id"), "at": stamp, "source": "Native SRE messages"}
        if isinstance(hook, dict):
            status = hook.get("status")
            event.update({
                "kind": "tool_audit", "stage": "investigate", "title": "Post-tool audit",
                "state": status if isinstance(status, str) else "Unknown",
                "text": "Native audit hook reported " + str(status or "unknown")
                        + ". This is hook execution status, not proof that a named tool succeeded.",
                "duration_ms": hook.get("durationMs"),
            })
        elif text.lstrip().startswith("```incident-alert"):
            event.update({"kind": "signal", "stage": "signal", "title": "Native alert context",
                          "state": "Observed", "text": "Native Azure Monitor alert context received."})
        elif text:
            event.update({"kind": "agent_update", "stage": "investigate", "title": "Agent update",
                          "state": "Reported", "text": safe_text(text)})
            for match in CASE_LINK.finditer(text):
                number, url, sys_id = match.groups()
                cases[sys_id] = {"number": number, "url": url, "sys_id": sys_id,
                                 "source_message_id": row.get("id"), "reported_at": stamp,
                                 "verification": "agent_reported"}
                event["stage"] = "case"
                event["title"] = "Agent-reported case reference"
        else:
            continue
        events.append(event)
    events.sort(key=lambda item: datetime.fromisoformat(item["at"].replace("Z", "+00:00")))
    return events[-250:], list(cases.values())


def build_journey(thread: dict, messages: dict, approvals: dict) -> dict:
    profile = profile_for(thread["rule_id"])
    events, cases = ([], [])
    if messages["status"] in ("ok", "empty"):
        events, cases = message_events(messages["data"])
    run = thread.get("execution_state")
    run_state = {
        "complete": "completed", "completed": "completed", "inprogress": "running",
        "running": "running", "investigating": "running", "failed": "failed",
        "error": "failed", "pending": "waiting", "queued": "waiting",
        "notstarted": "waiting",
    }.get(str(run or "").replace(" ", "").lower(), "not_observed")
    pending = approvals.get("data", [])
    if approvals["status"] in ("ok", "empty") and pending:
        run_state = "waiting"
    route_matched = thread.get("plan_id") == "asre-azure-monitor-review"
    stage_states = {
        "signal": ("completed", "Native alert binding observed", thread["created_at"]),
        "route": ("completed" if route_matched else "not_observed",
                  "Native response-plan binding observed" if route_matched else "Native plan binding not supplied",
                  thread["created_at"] if route_matched else None),
        "investigate": (run_state, "Native investigation: " + str(run or "not supplied"), None),
        "case": ("reported" if cases else "not_observed",
                 "Agent-reported reference; not independently verified" if cases else "No case reference observed in available messages",
                 cases[-1]["reported_at"] if cases else None),
        "verify": ("not_observed", "Independent case and findings receipts are not connected", None),
    }
    return {
        "thread": thread, "profile": profile, "execution_state": run,
        "stages": [{"id": key, "name": STAGE_NAMES[key], "state": state,
                    "summary": summary, "at": at} for key, (state, summary, at) in stage_states.items()],
        "events": events, "case_references": cases,
        "approvals": {"status": approvals["status"],
                      "pending_count": len(pending) if approvals["status"] in ("ok", "empty") else None,
                      "error": approvals.get("error")},
        "sources": {"messages": {key: value for key, value in messages.items() if key != "data"},
                    "approvals": {key: value for key, value in approvals.items() if key != "data"}},
        "event_limit": 250, "message_rows": len(messages.get("data", [])),
        "message": "Native execution state, source alert state and ServiceNow case state are separate.",
    }
