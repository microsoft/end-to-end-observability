"""ServiceNow incident adapter.

Included because in most enterprises a meaningful share of "alerts"
arrive as tickets a human already opened. Those belong in the same
incident stream -- otherwise the agent sees half the picture.
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


class ServiceNowAdapter(Adapter):
    source = "servicenow"
    version = "1.0"
    priority = 50

    def matches(self, payload: dict[str, Any]) -> bool:
        keys = set(payload)
        return "sys_id" in keys and ("short_description" in keys or "number" in keys)

    def normalize(self, payload: dict[str, Any]) -> IncidentEnvelope:
        warnings: list[str] = ["human-reported ticket; symptom inferred from free text"]

        title = str(payload.get("short_description") or "ServiceNow incident").strip()
        description = str(payload.get("description") or "").strip()
        ci = str(payload.get("cmdb_ci") or payload.get("configuration_item") or "").strip()
        service = str(payload.get("business_service") or ci or "unknown").lower()

        resource = ResourceRef(id=str(payload.get("arm_id") or ci), name=ci)
        symptom = classify_symptom(title, description)

        state = str(payload.get("state") or "").strip().lower()
        condition = (
            MonitorCondition.RESOLVED
            if state in ("resolved", "closed", "6", "7")
            else MonitorCondition.FIRED
        )

        return IncidentEnvelope(
            fingerprint=compute_fingerprint(service, resource.id, symptom.value, ""),
            source=self.source,
            source_alert_id=str(payload.get("number") or payload.get("sys_id") or "").strip(),
            title=title,
            description=description,
            severity=map_severity(payload.get("impact") or payload.get("priority")),
            symptom_class=symptom,
            monitor_condition=condition,
            signal_type=SignalType.EVENT,
            service=service,
            environment=infer_environment(ci, title, explicit=str(payload.get("environment") or "")),
            first_seen_at=str(payload.get("opened_at") or ""),
            received_at=utcnow_iso(),
            resource=resource,
            metric=MetricRef(),
            blast_radius=BlastRadius(scope="service", estimated_affected=1),
            normalization=Normalization(self.source, self.version, warnings),
            labels={
                "assignment_group": str(payload.get("assignment_group") or ""),
                "caller": str(payload.get("caller_id") or ""),
            },
            raw=payload,
        )


registry.register(ServiceNowAdapter())
