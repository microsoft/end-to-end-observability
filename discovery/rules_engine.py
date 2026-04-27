"""Rules-as-data engine.

A rule matches resources by `resourceType` (case-insensitive) and a list
of `signals`. Each signal is a small declarative check evaluated against
the resource row produced by the ARG sweep.

Supported signals:
  - missing: diagnosticSettings
  - missing: applicationInsights      (web/function apps only)
  - missing: workspaceLink            (resource has diag settings but no LAW sink)
  - tagAbsent: <tagName>
  - propertyEquals: { path: "properties.x.y", value: "..." }

When all signals match, the rule's `recommend` list is added to the
resource's recommendation set, with the rule's severity and id.

Rules live in YAML so they can be shipped, versioned, and extended by
customers without re-releasing the binary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml


@dataclass(frozen=True)
class Rule:
    id: str
    resource_type: str
    signals: tuple[dict, ...]
    recommend: tuple[str, ...]
    severity: str = "medium"
    description: str = ""


@dataclass
class Finding:
    resource_id: str
    resource_type: str
    resource_name: str
    subscription_id: str
    resource_group: str
    location: str
    rule_id: str
    severity: str
    recommendations: list[str] = field(default_factory=list)
    description: str = ""


def load_rules(path: str | Path) -> list[Rule]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    rules: list[Rule] = []
    for item in data:
        rules.append(
            Rule(
                id=item["id"],
                resource_type=item["resourceType"].lower(),
                signals=tuple(item.get("signals", [])),
                recommend=tuple(item.get("recommend", [])),
                severity=item.get("severity", "medium"),
                description=item.get("description", ""),
            )
        )
    return rules


def _get_path(obj: Any, dotted: str) -> Any:
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _matches_signal(resource: dict, signal: dict) -> bool:
    if "missing" in signal:
        kind = signal["missing"]
        if kind == "diagnosticSettings":
            return not bool(resource.get("hasDiagnosticSettings"))
        if kind == "workspaceLink":
            sinks = resource.get("diagnosticSinkWorkspaceIds") or []
            return not any(sinks)
        if kind == "applicationInsights":
            # Heuristic: web/function apps surface APPINSIGHTS_* via siteConfig
            # appSettings; ARG doesn't always project them, so we treat the
            # absence of a linked AI component (set later by the engine) as
            # the signal.
            return not bool(resource.get("_linkedAppInsights"))
        return False

    if "tagAbsent" in signal:
        tag = signal["tagAbsent"]
        return tag not in (resource.get("tags") or {})

    if "propertyEquals" in signal:
        spec = signal["propertyEquals"]
        return _get_path(resource, spec["path"]) == spec.get("value")

    return False


def evaluate(
    resources: Iterable[dict],
    rules: Iterable[Rule],
) -> list[Finding]:
    findings: list[Finding] = []
    rules_by_type: dict[str, list[Rule]] = {}
    for rule in rules:
        rules_by_type.setdefault(rule.resource_type, []).append(rule)

    for res in resources:
        rtype = (res.get("type") or "").lower()
        for rule in rules_by_type.get(rtype, []):
            if all(_matches_signal(res, s) for s in rule.signals):
                findings.append(
                    Finding(
                        resource_id=res.get("id", ""),
                        resource_type=rtype,
                        resource_name=res.get("name", ""),
                        subscription_id=res.get("subscriptionId", ""),
                        resource_group=res.get("resourceGroup", ""),
                        location=res.get("location", ""),
                        rule_id=rule.id,
                        severity=rule.severity,
                        recommendations=list(rule.recommend),
                        description=rule.description,
                    )
                )
    return findings
