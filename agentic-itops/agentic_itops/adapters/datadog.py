"""Datadog webhook adapter.

Datadog payloads are user-templated, so this adapter targets the common
convention (``$EVENT_TITLE``, ``$ALERT_TYPE``, ``$ALERT_TRANSITION``,
tag list) rather than a fixed schema. Unmapped fields land in `labels`.
"""

from __future__ import annotations

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


class DatadogAdapter(Adapter):
    source = "datadog"
    version = "1.0"
    priority = 20

    def matches(self, payload: dict[str, Any]) -> bool:
        if str(payload.get("source") or "").lower() == "datadog":
            return True
        keys = set(payload)
        return "alert_type" in keys and ("event_title" in keys or "title" in keys)

    def normalize(self, payload: dict[str, Any]) -> IncidentEnvelope:
        warnings: list[str] = []
        tags = _parse_tags(payload.get("tags"))

        title = str(payload.get("event_title") or payload.get("title") or "Datadog alert").strip()
        body = str(payload.get("event_msg") or payload.get("body") or "").strip()
        metric_name = str(payload.get("metric") or tags.get("metric") or "").strip()

        host = str(payload.get("hostname") or payload.get("host") or tags.get("host") or "").strip()
        resource_id = str(tags.get("arm_id") or tags.get("resource_id") or host).strip()
        resource = ResourceRef(
            id=resource_id,
            name=host or resource_id,
            type=tags.get("resource_type", ""),
            region=tags.get("region", ""),
        )
        if not resource_id:
            warnings.append("no resource identifier on payload; fingerprint uses host only")

        symptom = classify_symptom(title, metric_name, body)
        service = (tags.get("service") or tags.get("kube_service") or host or "unknown").lower()

        transition = str(payload.get("alert_transition") or "").strip().lower()
        condition = MonitorCondition.FIRED
        if transition in ("recovered", "resolved", "ok"):
            condition = MonitorCondition.RESOLVED
        elif not transition:
            condition = MonitorCondition.UNKNOWN

        return IncidentEnvelope(
            fingerprint=compute_fingerprint(service, resource.id, symptom.value, metric_name),
            source=self.source,
            source_alert_id=str(payload.get("alert_id") or payload.get("id") or "").strip(),
            title=title,
            description=body,
            severity=map_severity(payload.get("alert_type") or payload.get("priority")),
            symptom_class=symptom,
            monitor_condition=condition,
            signal_type=SignalType.METRIC if metric_name else SignalType.EVENT,
            service=service,
            environment=infer_environment(host, title, explicit=tags.get("env", "")),
            first_seen_at=str(payload.get("date") or payload.get("last_updated") or ""),
            received_at=utcnow_iso(),
            resource=resource,
            metric=MetricRef(name=metric_name, value=_to_float(payload.get("value"))),
            blast_radius=BlastRadius(scope="resource", estimated_affected=1),
            normalization=Normalization(self.source, self.version, warnings),
            labels=tags,
            raw=payload,
        )


def _parse_tags(tags: Any) -> dict[str, str]:
    """Datadog tags arrive as ``["env:prod", "service:api"]`` or a CSV string."""
    out: dict[str, str] = {}
    if isinstance(tags, str):
        tags = [part for part in tags.split(",") if part.strip()]
    if not isinstance(tags, (list, tuple)):
        return out
    for tag in tags:
        text = str(tag).strip()
        if ":" in text:
            key, _, value = text.partition(":")
            out[key.strip()] = value.strip()
        elif text:
            out[text] = "true"
    return out


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


registry.register(DatadogAdapter())
