"""Signal Normalizer -- skill #1, code-hosted.

Turns heterogeneous alert payloads into canonical envelopes, then groups
them by fingerprint so that N alerts about one problem become one
incident.

Why this is code and not a prompt
---------------------------------
Schema translation must be deterministic. If the same payload normalizes
differently on two runs, the fingerprint changes, deduplication breaks,
and every downstream agent reasons about a different incident. A model
is the wrong tool for a mapping that has exactly one correct answer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from .adapters import registry
from .envelope import IncidentEnvelope, Severity, utcnow_iso


class NormalizationError(ValueError):
    """Raised when no adapter claims a payload."""


@dataclass
class Incident:
    """One or more envelopes that share a fingerprint.

    This is what the Ops Orchestrator actually receives. `sources` is the
    part worth looking at in a demo -- when it has more than one entry,
    cross-source correlation just happened.
    """

    fingerprint: str
    envelopes: list[IncidentEnvelope] = field(default_factory=list)

    @property
    def primary(self) -> IncidentEnvelope:
        """Highest-severity, earliest-seen envelope represents the group."""
        return min(
            self.envelopes,
            key=lambda e: (e.severity.rank, e.first_seen_at or "9999"),
        )

    @property
    def sources(self) -> list[str]:
        return sorted({e.source for e in self.envelopes})

    @property
    def severity(self) -> Severity:
        return self.primary.severity

    @property
    def correlated(self) -> bool:
        return len(self.sources) > 1

    @property
    def is_resolved(self) -> bool:
        """Resolved only when the most recent signal says so."""
        latest = max(self.envelopes, key=lambda e: e.first_seen_at or "")
        return latest.monitor_condition.value == "resolved"

    def to_dict(self, include_raw: bool = False) -> dict[str, Any]:
        primary = self.primary
        return {
            "fingerprint": self.fingerprint,
            "title": primary.title,
            "severity": primary.severity.value,
            "symptom_class": primary.symptom_class.value,
            "service": primary.service,
            "environment": primary.environment,
            "resource": primary.resource.__dict__,
            "signal_count": len(self.envelopes),
            "sources": self.sources,
            "correlated_across_sources": self.correlated,
            "resolved": self.is_resolved,
            "first_seen_at": min((e.first_seen_at for e in self.envelopes if e.first_seen_at), default=""),
            "envelopes": [e.to_dict(include_raw=include_raw) for e in self.envelopes],
        }

    def agent_context(self) -> str:
        """Compact text handed to prompt-hosted agents.

        Kept small on purpose. Sending five full envelopes as JSON burns
        context and measurably degrades the RCA hypotheses.
        """
        primary = self.primary
        lines = [
            f"INCIDENT {self.fingerprint[:12]}",
            f"  title:       {primary.title}",
            f"  severity:    {primary.severity.value}",
            f"  symptom:     {primary.symptom_class.value}",
            f"  service:     {primary.service}",
            f"  environment: {primary.environment}",
            f"  resource:    {primary.resource.id or primary.resource.name or 'unknown'}",
            f"  type:        {primary.resource.type or 'unknown'}",
            f"  first seen:  {primary.first_seen_at or 'unknown'}",
            f"  signals:     {len(self.envelopes)} from {', '.join(self.sources)}",
        ]
        if primary.metric.name:
            metric = f"  metric:      {primary.metric.name}"
            if primary.metric.value is not None:
                metric += f" = {primary.metric.value}"
            if primary.metric.threshold is not None:
                metric += f" (threshold {primary.metric.operator} {primary.metric.threshold})"
            lines.append(metric)
        if self.correlated:
            lines.append(
                f"  NOTE: correlated across {len(self.sources)} tools "
                f"({', '.join(self.sources)}) -- treat as ONE problem."
            )
        warnings = sorted({w for e in self.envelopes for w in e.normalization.warnings})
        for warning in warnings:
            lines.append(f"  warning:     {warning}")
        return "\n".join(lines)


def normalize_one(payload: dict[str, Any]) -> IncidentEnvelope:
    """Normalize a single payload. Raises if no adapter matches."""
    adapter = registry.resolve(payload)
    if adapter is None:
        raise NormalizationError(
            f"no adapter matched payload with keys: {sorted(payload)[:10]}"
        )
    return adapter.normalize(payload)


def normalize_many(
    payloads: Iterable[dict[str, Any]],
    *,
    strict: bool = False,
) -> tuple[list[IncidentEnvelope], list[dict[str, Any]]]:
    """Normalize a batch.

    Returns ``(envelopes, failures)``. Unless ``strict``, an unmatched
    payload is collected rather than raised -- one malformed webhook
    should not take down a batch of five hundred.
    """
    envelopes: list[IncidentEnvelope] = []
    failures: list[dict[str, Any]] = []
    for payload in payloads:
        try:
            envelopes.append(normalize_one(payload))
        except Exception as exc:
            if strict:
                raise
            failures.append({"error": str(exc), "payload": payload})
    return envelopes, failures


def correlate(envelopes: Sequence[IncidentEnvelope]) -> list[Incident]:
    """Group envelopes into incidents.

    Two passes:

    1. **Exact fingerprint.** Signals describing the same symptom, on the
       same resource, measuring the same underlying metric collapse
       together regardless of which tool emitted them.
    2. **Metric-free attachment.** A signal carrying no metric at all --
       in practice almost always a human-opened ticket -- is attached to
       an existing incident that shares its coarse key. Without this the
       agent sees the machine's view and the human's view as two separate
       problems, which is exactly the fragmentation this layer exists to
       remove.

    Pass 2 never merges two metric-bearing signals. CPU saturation and
    memory saturation on one host share a coarse key but are different
    problems.
    """
    grouped: dict[str, Incident] = {}
    deferred: list[IncidentEnvelope] = []

    for envelope in envelopes:
        if envelope.has_metric:
            incident = grouped.setdefault(
                envelope.fingerprint, Incident(fingerprint=envelope.fingerprint)
            )
            incident.envelopes.append(envelope)
        else:
            deferred.append(envelope)

    # Index the metric-bearing incidents by coarse key for pass 2.
    by_coarse: dict[str, Incident] = {}
    for incident in grouped.values():
        by_coarse.setdefault(incident.primary.coarse_key, incident)

    for envelope in deferred:
        host = by_coarse.get(envelope.coarse_key)
        if host is not None:
            envelope.normalization.warnings.append(
                "attached to a machine-detected incident by coarse key (no metric on this signal)"
            )
            host.envelopes.append(envelope)
        else:
            incident = grouped.setdefault(
                envelope.fingerprint, Incident(fingerprint=envelope.fingerprint)
            )
            incident.envelopes.append(envelope)

    return sorted(
        grouped.values(),
        key=lambda inc: (inc.severity.rank, -len(inc.envelopes)),
    )


def normalize_and_correlate(
    payloads: Iterable[dict[str, Any]],
) -> tuple[list[Incident], list[dict[str, Any]]]:
    envelopes, failures = normalize_many(payloads)
    return correlate(envelopes), failures


# --- IO helpers -------------------------------------------------------


def load_payloads(path: str | Path) -> list[dict[str, Any]]:
    """Load payloads from ``.jsonl``, or from ``.json`` holding a list or object."""
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    if file_path.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    data = json.loads(text)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("alerts"), list):
        # An Alertmanager batch: expand so each alert becomes a payload.
        return [{**data, "alerts": [alert]} for alert in data["alerts"]]
    return [data]


def load_directory(path: str | Path) -> list[dict[str, Any]]:
    """Load every ``.json``/``.jsonl`` file in a directory."""
    directory = Path(path)
    payloads: list[dict[str, Any]] = []
    for file_path in sorted(directory.glob("*")):
        if file_path.suffix in (".json", ".jsonl"):
            payloads.extend(load_payloads(file_path))
    return payloads


def normalization_report(
    incidents: Sequence[Incident], failures: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    """Summary suitable for logging or for the hackathon demo console."""
    total_signals = sum(len(i.envelopes) for i in incidents)
    correlated = [i for i in incidents if i.correlated]
    return {
        "generated_at": utcnow_iso(),
        "signals_in": total_signals + len(failures),
        "signals_normalized": total_signals,
        "signals_failed": len(failures),
        "incidents_out": len(incidents),
        "reduction_ratio": (
            round(1 - (len(incidents) / total_signals), 3) if total_signals else 0.0
        ),
        "cross_source_incidents": len(correlated),
        "by_severity": {
            severity.value: sum(1 for i in incidents if i.severity is severity)
            for severity in Severity
        },
    }
