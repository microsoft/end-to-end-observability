"""Change-Safety Gate -- skill #6, code-hosted.

Evaluates a proposed remediation against policy and returns
allow / deny / escalate with an audit record.

Why this is code and not a prompt
---------------------------------
This is an authorization boundary. A prompt can be argued out of its
instructions by the text it is reasoning over -- and the text it is
reasoning over here is attacker-influenceable (alert descriptions, log
lines, ticket bodies). If the component deciding "may this agent restart
that production cluster" is a model, there is no defensible answer for
an AI Workload Security Assessment and no regulated-industry CISO will
sign off.

The planner agent proposes. This gate disposes. The model never sees the
policy and cannot alter the decision.
"""

from __future__ import annotations

import fnmatch
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Sequence

import yaml

DEFAULT_POLICY_PATH = Path(__file__).parent / "policies" / "default.yaml"


class Effect(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ESCALATE = "escalate"


@dataclass
class ChangeRequest:
    """A proposed action. Produced by the Remediation Planner agent."""

    action: str                      # e.g. "restart", "scale_out", "failover"
    resource_id: str
    environment: str = "unknown"
    service: str = "unknown"
    blast_radius_scope: str = "resource"
    estimated_affected: int = 1
    requested_by: str = "agent:remediation-planner"
    incident_fingerprint: str = ""
    justification: str = ""
    dry_run: bool = False
    at: datetime | None = None       # evaluation time; defaults to now

    def audit_key(self) -> str:
        payload = "|".join(
            [
                self.action,
                self.resource_id,
                self.environment,
                str(self.estimated_affected),
                self.incident_fingerprint,
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass
class Decision:
    """The gate's verdict. Always includes reasons -- silent denials are unusable."""

    effect: Effect
    approval_tier: str = "none"
    reasons: list[str] = field(default_factory=list)
    matched_rules: list[str] = field(default_factory=list)
    audit_id: str = ""
    evaluated_at: str = ""
    policy_version: str = ""

    @property
    def allowed(self) -> bool:
        return self.effect is Effect.ALLOW

    def to_dict(self) -> dict[str, Any]:
        return {
            "effect": self.effect.value,
            "approval_tier": self.approval_tier,
            "reasons": self.reasons,
            "matched_rules": self.matched_rules,
            "audit_id": self.audit_id,
            "evaluated_at": self.evaluated_at,
            "policy_version": self.policy_version,
        }

    def explain(self) -> str:
        lines = [f"DECISION: {self.effect.value.upper()}"]
        if self.approval_tier != "none":
            lines.append(f"  approval required: {self.approval_tier}")
        for reason in self.reasons:
            lines.append(f"  - {reason}")
        if self.matched_rules:
            lines.append(f"  rules: {', '.join(self.matched_rules)}")
        lines.append(f"  audit id: {self.audit_id}")
        return "\n".join(lines)


class ChangeSafetyGate:
    """Deterministic policy evaluation.

    Evaluation order is fixed and deny-biased:
      1. Read-only mode        -> deny everything that mutates
      2. Explicit deny rules   -> deny
      3. Action allow-list     -> deny if absent
      4. Resource scope        -> deny if outside allowed patterns
      5. Blast radius ceiling  -> deny above the hard limit
      6. Change freeze window  -> escalate
      7. Approval tier lookup  -> allow or escalate

    Anything not explicitly permitted is denied. That default matters
    more than any individual rule.

    Step 5 precedes step 6 deliberately: a hard ceiling is an absolute
    refusal, and it must not be downgraded to "escalate to the change
    advisory board" merely because a freeze window happens to be active.
    """

    def __init__(self, policy: dict[str, Any] | None = None) -> None:
        # `policy or load_policy()` would be wrong here: an explicitly
        # supplied empty policy -- which is what a failed or empty YAML
        # load produces -- would silently fall back to the bundled sample
        # policy and start authorizing actions against rules the operator
        # never configured. Distinguish "not supplied" from "empty", and
        # let an empty policy deny everything.
        self.policy = load_policy() if policy is None else policy
        self.version = str(self.policy.get("version", "unversioned"))
        if not self.policy.get("allowed_actions"):
            # Not an error: an empty policy is a valid, maximally safe
            # posture. But it is worth being loud about, because it is
            # also what a misconfiguration looks like.
            self.version = f"{self.version} (no allowed_actions: denying all change)"

    # -- public API ---------------------------------------------------

    def evaluate(self, request: ChangeRequest) -> Decision:
        now = request.at or datetime.now(timezone.utc)
        reasons: list[str] = []
        matched: list[str] = []
        decision = Decision(
            effect=Effect.DENY,
            audit_id=request.audit_key(),
            evaluated_at=now.isoformat(),
            policy_version=self.version,
        )

        action = (request.action or "").strip().lower()
        environment = (request.environment or "unknown").strip().lower()

        # 1. read-only mode -- the posture before AIWSA #2 has passed
        if bool(self.policy.get("read_only", False)) and not request.dry_run:
            decision.reasons = [
                "policy is in read-only mode: agents may recommend but not act",
                "clear this only after the AI Workload Security Assessment "
                "for production change authority has passed",
            ]
            decision.matched_rules = ["read_only"]
            return decision

        # 2. explicit deny rules
        for rule in self.policy.get("deny", []) or []:
            if self._rule_matches(rule, request, action, environment):
                decision.reasons = [
                    str(rule.get("reason") or f"denied by rule {rule.get('id', 'deny')}")
                ]
                decision.matched_rules = [str(rule.get("id", "deny"))]
                return decision

        # 3. action allow-list
        allowed_actions = {
            str(a).lower() for a in (self.policy.get("allowed_actions") or [])
        }
        if action not in allowed_actions:
            decision.reasons = [
                f"action '{action}' is not in the allowed action list",
                f"allowed: {', '.join(sorted(allowed_actions)) or '(none)'}",
            ]
            decision.matched_rules = ["allowed_actions"]
            return decision
        matched.append("allowed_actions")

        # 4. resource scope
        scopes = self.policy.get("resource_scopes") or {}
        allow_patterns = [str(p) for p in (scopes.get("allow") or [])]
        deny_patterns = [str(p) for p in (scopes.get("deny") or [])]
        resource_id = request.resource_id or ""

        for pattern in deny_patterns:
            if _glob(resource_id, pattern):
                decision.reasons = [
                    f"resource matches denied scope pattern '{pattern}'"
                ]
                decision.matched_rules = ["resource_scopes.deny"]
                return decision

        if allow_patterns and not any(_glob(resource_id, p) for p in allow_patterns):
            decision.reasons = [
                "resource is outside every allowed scope pattern",
                f"resource: {resource_id or '(empty)'}",
            ]
            decision.matched_rules = ["resource_scopes.allow"]
            return decision
        matched.append("resource_scopes")

        # 5. blast radius hard ceiling
        #
        # Evaluated BEFORE the change-freeze check on purpose. A hard
        # ceiling means no approval tier can authorize the action at all;
        # a freeze means it needs different approval. If the freeze check
        # ran first it would return ESCALATE, which implies a change
        # advisory board could approve something policy says is never
        # permitted. Deny-biased ordering: the strongest refusal wins.
        ceilings = self.policy.get("blast_radius") or {}
        max_affected = ceilings.get("max_affected_auto")
        hard_limit = ceilings.get("hard_deny_above")
        if hard_limit is not None and request.estimated_affected > int(hard_limit):
            decision.reasons = [
                f"estimated blast radius {request.estimated_affected} exceeds "
                f"the hard limit of {hard_limit}",
                "no approval tier can authorize this automatically",
            ]
            decision.matched_rules = matched + ["blast_radius.hard_deny_above"]
            return decision

        # 6. change freeze
        freeze = self._active_freeze(now, environment)
        if freeze:
            decision.effect = Effect.ESCALATE
            decision.approval_tier = str(
                self.policy.get("freeze_approval_tier", "change_advisory_board")
            )
            decision.reasons = [
                f"change freeze active: {freeze}",
                "requires out-of-band approval to proceed",
            ]
            decision.matched_rules = matched + ["change_freeze"]
            return decision

        escalate_on_radius = (
            max_affected is not None and request.estimated_affected > int(max_affected)
        )

        # 7. approval tier
        tier = self._approval_tier(action, environment)
        if escalate_on_radius or tier not in ("none", "auto"):
            decision.effect = Effect.ESCALATE
            decision.approval_tier = tier if tier not in ("none", "auto") else "on_call_engineer"
            if escalate_on_radius:
                reasons.append(
                    f"blast radius {request.estimated_affected} exceeds the "
                    f"auto-approval ceiling of {max_affected}"
                )
            reasons.append(
                f"action '{action}' in environment '{environment}' requires "
                f"approval tier '{decision.approval_tier}'"
            )
            decision.reasons = reasons
            decision.matched_rules = matched + ["approval_tiers"]
            return decision

        decision.effect = Effect.ALLOW
        decision.approval_tier = "none"
        decision.reasons = [
            f"action '{action}' permitted in environment '{environment}'",
            f"blast radius {request.estimated_affected} within auto-approval ceiling",
        ]
        decision.matched_rules = matched + ["approval_tiers"]
        return decision

    # -- internals ----------------------------------------------------

    def _rule_matches(
        self,
        rule: dict[str, Any],
        request: ChangeRequest,
        action: str,
        environment: str,
    ) -> bool:
        if "action" in rule and not _glob(action, str(rule["action"]).lower()):
            return False
        if "environment" in rule and str(rule["environment"]).lower() != environment:
            return False
        if "resource_pattern" in rule and not _glob(
            request.resource_id, str(rule["resource_pattern"])
        ):
            return False
        if "service_pattern" in rule and not _glob(
            request.service, str(rule["service_pattern"])
        ):
            return False
        if "min_affected" in rule and request.estimated_affected < int(rule["min_affected"]):
            return False
        # A rule with no predicates matches nothing, rather than everything.
        return any(
            key in rule
            for key in ("action", "environment", "resource_pattern", "service_pattern", "min_affected")
        )

    def _active_freeze(self, now: datetime, environment: str) -> str | None:
        for window in self.policy.get("change_freezes", []) or []:
            envs = [str(e).lower() for e in (window.get("environments") or ["prod"])]
            if environment not in envs:
                continue
            if _in_freeze_window(now, window):
                return str(window.get("name") or "unnamed freeze window")
        return None

    def _approval_tier(self, action: str, environment: str) -> str:
        tiers = self.policy.get("approval_tiers") or {}
        env_tiers = tiers.get(environment) or tiers.get("default") or {}
        if action in env_tiers:
            return str(env_tiers[action])
        for pattern, tier in env_tiers.items():
            if pattern != "*" and _glob(action, str(pattern)):
                return str(tier)
        return str(env_tiers.get("*", "on_call_engineer"))


# --- helpers ----------------------------------------------------------


def _glob(value: str, pattern: str) -> bool:
    return fnmatch.fnmatch((value or "").lower(), (pattern or "").lower())


_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _in_freeze_window(now: datetime, window: dict[str, Any]) -> bool:
    """Support both recurring weekly windows and fixed date ranges."""
    start_date = window.get("start")
    end_date = window.get("end")
    if start_date and end_date:
        try:
            start = datetime.fromisoformat(str(start_date))
            end = datetime.fromisoformat(str(end_date))
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            return start <= now <= end
        except ValueError:
            return False

    days = [str(d).lower() for d in (window.get("days") or [])]
    if days and _DAYS[now.weekday()] not in days:
        return False
    start_time = _parse_time(window.get("start_time"))
    end_time = _parse_time(window.get("end_time"))
    if start_time and end_time:
        current = now.timetz().replace(tzinfo=None)
        if start_time <= end_time:
            return start_time <= current <= end_time
        return current >= start_time or current <= end_time  # window crosses midnight
    return bool(days)


def _parse_time(value: Any) -> time | None:
    if not value:
        return None
    match = re.match(r"^(\d{1,2}):(\d{2})$", str(value).strip())
    if not match:
        return None
    return time(int(match.group(1)), int(match.group(2)))


def load_policy(path: str | Path | None = None) -> dict[str, Any]:
    policy_path = Path(path) if path else DEFAULT_POLICY_PATH
    return yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
