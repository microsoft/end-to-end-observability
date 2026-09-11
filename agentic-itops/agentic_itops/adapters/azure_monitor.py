"""Azure Monitor adapter.

Handles three shapes seen in the wild and in this repo's `test-data`:

1. Common Alert Schema webhook  -- ``{"data": {"essentials": {...}}}``
2. Alerts Management API record -- ``{"properties": {"essentials": {...}}}``
   (this is what ``test-data/output/amba-alerts.jsonl`` contains)
3. Flat essentials               -- ``{"essentials": {...}}``
"""

from __future__ import annotations

import re
from typing import Any

from ..classify import classify_symptom, infer_environment, map_severity
from ..envelope import (
    BlastRadius,
    IncidentEnvelope,
    MetricRef,
    MonitorCondition,
    Normalization,
    ResourceRef,
    SignalType,
    compute_fingerprint,
    utcnow_iso,
)
from .base import Adapter, registry

_ARM_ID = re.compile(
    r"^/subscriptions/(?P<sub>[^/]+)"
    r"(?:/resourceGroups/(?P<rg>[^/]+))?"
    r"(?:/providers/(?P<provider>[^/]+)/(?P<type>.+?)/(?P<name>[^/]+))?/?$",
    re.IGNORECASE,
)

_SIGNAL_TYPES = {
    "metric": SignalType.METRIC,
    "log": SignalType.LOG,
    "activitylog": SignalType.EVENT,
    "health": SignalType.EVENT,
    "unknown": SignalType.UNKNOWN,
}


def parse_arm_id(resource_id: str) -> ResourceRef:
    """Split an ARM resource id into its parts.

    Returns a best-effort ResourceRef rather than raising: a malformed id
    should degrade the envelope, not drop the alert.
    """
    ref = ResourceRef(id=resource_id or "")
    if not resource_id:
        return ref
    match = _ARM_ID.match(resource_id.strip())
    if not match:
        ref.name = resource_id.rstrip("/").split("/")[-1]
        return ref
    ref.subscription_id = match.group("sub") or ""
    ref.resource_group = match.group("rg") or ""
    ref.provider = match.group("provider") or ""
    resource_type = match.group("type") or ""
    ref.type = f"{ref.provider}/{resource_type}".strip("/") if resource_type else ref.provider
    ref.name = match.group("name") or ""
    return ref


class AzureMonitorAdapter(Adapter):
    source = "azure_monitor"
    version = "1.0"
    priority = 10

    def matches(self, payload: dict[str, Any]) -> bool:
        return self._essentials(payload) is not None

    def _essentials(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        for path in ("data.essentials", "properties.essentials", "essentials"):
            node = self._get(payload, path)
            if isinstance(node, dict) and (
                "alertRule" in node or "targetResource" in node or "monitorCondition" in node
            ):
                return node
        return None

    def normalize(self, payload: dict[str, Any]) -> IncidentEnvelope:
        essentials = self._essentials(payload) or {}
        warnings: list[str] = []

        rule = str(essentials.get("alertRule") or essentials.get("alertRuleName") or "").strip()
        description = str(essentials.get("description") or "").strip()
        resource_id = str(
            essentials.get("targetResource")
            or (essentials.get("alertTargetIDs") or [""])[0]
            or ""
        ).strip()

        resource = parse_arm_id(resource_id)
        resource.region = str(essentials.get("targetResourceRegion") or "").strip()
        if not resource.type:
            resource.type = str(essentials.get("targetResourceType") or "").strip()

        # Metric name: the alert context carries it when the alert is
        # metric-based; otherwise fall back to the rule name, which in
        # AMBA conventions is "<Metric> - AMBA".
        metric_name = str(
            self._get(payload, "data.alertContext.condition.allOf.0.metricName")
            or self._get(payload, "properties.context.condition.allOf.0.metricName")
            or ""
        ).strip()
        if not metric_name and " - " in rule:
            metric_name = rule.split(" - ")[0].strip()
        elif not metric_name:
            metric_name = rule

        metric = MetricRef(name=metric_name)
        condition = (
            self._get(payload, "data.alertContext.condition.allOf.0")
            or self._get(payload, "properties.context.condition.allOf.0")
            or {}
        )
        if isinstance(condition, dict):
            metric.value = _to_float(condition.get("metricValue"))
            metric.threshold = _to_float(condition.get("threshold"))
            metric.operator = str(condition.get("operator") or "").strip()

        symptom = classify_symptom(rule, metric_name, description, resource.type)
        if symptom.value == "unknown":
            warnings.append("symptom_class could not be inferred from rule or metric name")

        monitor_condition = MonitorCondition.UNKNOWN
        raw_condition = str(essentials.get("monitorCondition") or "").strip().lower()
        if raw_condition == "fired":
            monitor_condition = MonitorCondition.FIRED
        elif raw_condition == "resolved":
            monitor_condition = MonitorCondition.RESOLVED

        signal_type = _SIGNAL_TYPES.get(
            str(essentials.get("signalType") or "").strip().lower(), SignalType.UNKNOWN
        )

        environment = infer_environment(
            resource.resource_group, resource.name, rule,
            explicit=str(essentials.get("environment") or ""),
        )
        if environment == "unknown":
            warnings.append("environment not tagged; defaulted to unknown")

        service = _infer_service(resource, rule)
        first_seen = str(essentials.get("startDateTime") or essentials.get("firedDateTime") or "")

        return IncidentEnvelope(
            fingerprint=compute_fingerprint(service, resource.id, symptom.value, metric_name),
            source=self.source,
            source_alert_id=str(payload.get("id") or essentials.get("alertId") or "").strip(),
            title=rule or description or "Azure Monitor alert",
            description=description,
            severity=map_severity(essentials.get("severity")),
            symptom_class=symptom,
            monitor_condition=monitor_condition,
            signal_type=signal_type,
            service=service,
            environment=environment,
            first_seen_at=first_seen,
            received_at=utcnow_iso(),
            resource=resource,
            metric=metric,
            blast_radius=BlastRadius(scope="resource", estimated_affected=1),
            normalization=Normalization(self.source, self.version, warnings),
            labels={
                "monitorService": str(essentials.get("monitorService") or ""),
                "alertState": str(essentials.get("alertState") or ""),
            },
            raw=payload,
        )


def _infer_service(resource: ResourceRef, rule: str) -> str:
    """Best-effort logical service name.

    Real deployments should map this from a CMDB or a `service` tag. The
    resource group is a decent proxy and is almost always present, which
    matters more than being clever.
    """
    if resource.resource_group:
        return resource.resource_group.lower()
    if resource.name:
        return resource.name.lower()
    return "unknown"


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


registry.register(AzureMonitorAdapter())
