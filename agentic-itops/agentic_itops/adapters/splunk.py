"""Splunk alert action adapter.

Splunk's webhook wraps the triggering row in ``result`` and the saved
search metadata at the top level.
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


class SplunkAdapter(Adapter):
    source = "splunk"
    version = "1.0"
    priority = 40

    def matches(self, payload: dict[str, Any]) -> bool:
        keys = set(payload)
        return "search_name" in keys or ("result" in keys and "sid" in keys)

    def normalize(self, payload: dict[str, Any]) -> IncidentEnvelope:
        warnings: list[str] = []
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}

        search_name = str(payload.get("search_name") or "Splunk alert").strip()
        host = str(result.get("host") or result.get("source") or "").strip()
        service = str(result.get("service") or result.get("sourcetype") or host or "unknown").lower()

        resource_id = str(result.get("arm_id") or result.get("resource_id") or host).strip()
        if not resource_id:
            warnings.append("no host or resource id in Splunk result row")

        resource = ResourceRef(id=resource_id, name=host or resource_id)
        metric_name = str(result.get("metric") or result.get("field") or "").strip()

        symptom = classify_symptom(search_name, metric_name, str(result.get("_raw") or ""))

        return IncidentEnvelope(
            fingerprint=compute_fingerprint(service, resource.id, symptom.value, metric_name),
            source=self.source,
            source_alert_id=str(payload.get("sid") or "").strip(),
            title=search_name,
            description=str(result.get("_raw") or "")[:500],
            severity=map_severity(
                result.get("severity") or payload.get("severity") or result.get("urgency")
            ),
            symptom_class=symptom,
            monitor_condition=MonitorCondition.FIRED,
            signal_type=SignalType.LOG,
            service=service,
            environment=infer_environment(host, search_name, explicit=str(result.get("env") or "")),
            first_seen_at=str(result.get("_time") or ""),
            received_at=utcnow_iso(),
            resource=resource,
            metric=MetricRef(name=metric_name, value=_to_float(result.get("count"))),
            blast_radius=BlastRadius(scope="resource", estimated_affected=1),
            normalization=Normalization(self.source, self.version, warnings),
            labels={
                "search_name": search_name,
                "owner": str(payload.get("owner") or ""),
                "app": str(payload.get("app") or ""),
            },
            raw=payload,
        )


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


registry.register(SplunkAdapter())
