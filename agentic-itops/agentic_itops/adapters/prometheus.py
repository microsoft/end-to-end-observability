"""Prometheus Alertmanager adapter.

Accepts either a full Alertmanager webhook (``{"alerts": [...]}``, in
which case the first alert is normalized -- the caller should iterate)
or a single alert object.
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


class PrometheusAdapter(Adapter):
    source = "prometheus"
    version = "1.0"
    priority = 30

    def matches(self, payload: dict[str, Any]) -> bool:
        target = self._single(payload)
        if not isinstance(target, dict):
            return False
        return "labels" in target and (
            "alertname" in (target.get("labels") or {}) or "status" in target
        )

    @staticmethod
    def _single(payload: dict[str, Any]) -> dict[str, Any]:
        alerts = payload.get("alerts")
        if isinstance(alerts, list) and alerts:
            return alerts[0]
        return payload

    def normalize(self, payload: dict[str, Any]) -> IncidentEnvelope:
        alert = self._single(payload)
        warnings: list[str] = []
        if payload.get("alerts") and len(payload.get("alerts") or []) > 1:
            warnings.append(
                f"payload carried {len(payload['alerts'])} alerts; only the first was normalized"
            )

        labels: dict[str, str] = {str(k): str(v) for k, v in (alert.get("labels") or {}).items()}
        annotations: dict[str, str] = {
            str(k): str(v) for k, v in (alert.get("annotations") or {}).items()
        }

        alertname = labels.get("alertname", "Prometheus alert")
        summary = annotations.get("summary") or annotations.get("description") or ""
        instance = labels.get("instance") or labels.get("pod") or labels.get("node") or ""
        service = (labels.get("service") or labels.get("job") or labels.get("namespace") or "unknown").lower()

        # The metric label, when present, is what makes this fingerprint
        # line up with the same symptom seen by Azure Monitor or Datadog.
        # Falling back to alertname would break cross-source correlation,
        # because no two tools name the same rule the same way.
        metric_name = labels.get("metric") or alertname

        resource = ResourceRef(
            id=labels.get("arm_id") or instance,
            name=instance,
            type=labels.get("resource_type", ""),
        )

        symptom = classify_symptom(alertname, summary, labels.get("metric", ""))
        status = str(alert.get("status") or payload.get("status") or "").strip().lower()
        condition = MonitorCondition.RESOLVED if status == "resolved" else MonitorCondition.FIRED

        return IncidentEnvelope(
            fingerprint=compute_fingerprint(service, resource.id, symptom.value, metric_name),
            source=self.source,
            source_alert_id=str(alert.get("fingerprint") or "").strip(),
            title=alertname,
            description=summary,
            severity=map_severity(labels.get("severity")),
            symptom_class=symptom,
            monitor_condition=condition,
            signal_type=SignalType.METRIC,
            service=service,
            environment=infer_environment(
                instance, labels.get("namespace", ""), explicit=labels.get("env", "")
            ),
            first_seen_at=str(alert.get("startsAt") or ""),
            received_at=utcnow_iso(),
            resource=resource,
            metric=MetricRef(name=metric_name),
            blast_radius=BlastRadius(
                scope="service" if labels.get("namespace") else "resource",
                estimated_affected=1,
            ),
            normalization=Normalization(self.source, self.version, warnings),
            labels={**labels, **{f"annotation.{k}": v for k, v in annotations.items()}},
            raw=payload,
        )


registry.register(PrometheusAdapter())
