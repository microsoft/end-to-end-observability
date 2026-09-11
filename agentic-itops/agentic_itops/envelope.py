"""Canonical incident envelope.

Every alert, from every source, is normalized into one shape before any
agent reasons about it. This is the contract that makes the rest of the
portfolio source-agnostic: the RCA Investigator does not know or care
whether a signal arrived from Azure Monitor, Datadog, Splunk or a
Prometheus Alertmanager webhook.

Design notes
------------
* The envelope is **deterministic**. The same input always produces the
  same output, including the fingerprint. Normalization is schema
  translation, not judgement, so it is code and not a prompt.
* `fingerprint` is intentionally source-independent. Two alerts about the
  same symptom on the same resource collapse to one fingerprint even if
  one came from Azure Monitor and one from Datadog. That cross-source
  dedup is the single most valuable property of this layer.
* `raw` is always retained. Agents occasionally need a field we did not
  model, and auditors always need the original.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

SCHEMA_VERSION = "1.0"


class Severity(str, Enum):
    """Canonical severity. Source-specific scales map onto this."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def rank(self) -> int:
        return {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}[self.value]


class SymptomClass(str, Enum):
    """What is wrong, independent of which tool noticed.

    Deliberately small. A large taxonomy looks thorough and then nobody
    maps to it consistently. Nine buckets is enough to route an
    investigation and to group alerts that belong together.
    """

    AVAILABILITY = "availability"
    LATENCY = "latency"
    SATURATION = "saturation"
    ERROR_RATE = "error_rate"
    CAPACITY = "capacity"
    CONFIGURATION = "configuration"
    SECURITY = "security"
    DATA_QUALITY = "data_quality"
    CONNECTIVITY = "connectivity"
    UNKNOWN = "unknown"


class MonitorCondition(str, Enum):
    FIRED = "fired"
    RESOLVED = "resolved"
    UNKNOWN = "unknown"


class SignalType(str, Enum):
    METRIC = "metric"
    LOG = "log"
    TRACE = "trace"
    SYNTHETIC = "synthetic"
    EVENT = "event"
    UNKNOWN = "unknown"


@dataclass
class ResourceRef:
    """The thing that is unhealthy."""

    id: str = ""
    name: str = ""
    type: str = ""
    provider: str = ""
    region: str = ""
    subscription_id: str = ""
    resource_group: str = ""


@dataclass
class MetricRef:
    """Present when the signal is metric-derived."""

    name: str = ""
    value: float | None = None
    unit: str = ""
    threshold: float | None = None
    operator: str = ""


@dataclass
class BlastRadius:
    """How much is affected. Feeds the Change-Safety Gate approval tier."""

    scope: str = "resource"  # resource | service | environment | tenant
    estimated_affected: int = 1


@dataclass
class Normalization:
    """Provenance of the translation itself, so failures are debuggable."""

    adapter: str = ""
    adapter_version: str = "1.0"
    warnings: list[str] = field(default_factory=list)


@dataclass
class IncidentEnvelope:
    """The canonical shape. Everything downstream consumes this."""

    fingerprint: str
    source: str
    title: str
    severity: Severity = Severity.MEDIUM
    symptom_class: SymptomClass = SymptomClass.UNKNOWN
    monitor_condition: MonitorCondition = MonitorCondition.FIRED
    signal_type: SignalType = SignalType.UNKNOWN

    source_alert_id: str = ""
    description: str = ""
    service: str = "unknown"
    environment: str = "unknown"

    first_seen_at: str = ""
    received_at: str = ""

    resource: ResourceRef = field(default_factory=ResourceRef)
    metric: MetricRef = field(default_factory=MetricRef)
    blast_radius: BlastRadius = field(default_factory=BlastRadius)
    normalization: Normalization = field(default_factory=Normalization)

    labels: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    @property
    def coarse_key(self) -> str:
        """Metric-free grouping key. See ``compute_coarse_key``."""
        return compute_coarse_key(self.service, self.resource.id, self.symptom_class.value)

    @property
    def has_metric(self) -> bool:
        return bool(self.metric.name)

    def to_dict(self, include_raw: bool = True) -> dict[str, Any]:
        out = asdict(self)
        for key in ("severity", "symptom_class", "monitor_condition", "signal_type"):
            value = out[key]
            out[key] = value.value if isinstance(value, Enum) else value
        if not include_raw:
            out.pop("raw", None)
        return out

    def to_json(self, include_raw: bool = True, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(include_raw=include_raw), indent=indent, default=str)

    def summary_line(self) -> str:
        """One-line form used in agent prompts and console output."""
        return (
            f"[{self.severity.value.upper()}] {self.title} "
            f"| service={self.service} env={self.environment} "
            f"| symptom={self.symptom_class.value} "
            f"| resource={self.resource.name or self.resource.id or 'unknown'} "
            f"| fp={self.fingerprint[:12]}"
        )


def compute_fingerprint(
    service: str,
    resource_id: str,
    symptom_class: str,
    metric_name: str = "",
) -> str:
    """Stable, source-independent identity for a symptom.

    Deliberately excludes: source, timestamp, severity, alert rule name,
    and the alert's own id. Two tools observing the same problem on the
    same resource must collapse to one fingerprint -- that is the whole
    point. Including the rule name would defeat it, because Datadog and
    Azure Monitor never name the same rule the same way.
    """
    parts = [
        (service or "unknown").strip().lower(),
        _canonical_resource_key(resource_id),
        (symptom_class or "unknown").strip().lower(),
        _canonical_metric_key(metric_name),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def compute_coarse_key(service: str, resource_id: str, symptom_class: str) -> str:
    """Metric-free identity: *something of this kind is wrong, here*.

    Used only to attach signals that carry no metric at all -- most
    often human-opened tickets -- to an existing machine-detected
    incident. Deliberately NOT used to merge two metric-bearing signals:
    CPU saturation and memory saturation on one host share a coarse key
    but are different problems, and collapsing them would be wrong.
    """
    parts = [
        (service or "unknown").strip().lower(),
        _canonical_resource_key(resource_id),
        (symptom_class or "unknown").strip().lower(),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _canonical_resource_key(resource_id: str) -> str:
    """Lowercase an ARM id, or fall back to the bare string."""
    if not resource_id:
        return "unknown"
    return resource_id.strip().lower().rstrip("/")


def _canonical_metric_key(metric_name: str) -> str:
    """Collapse vendor spellings of the same metric.

    `Percentage CPU`, `system.cpu.user` and `node_cpu_seconds_total` are
    the same underlying concept. Without this, cross-source dedup fails
    on exactly the cases it exists to catch.
    """
    if not metric_name:
        return ""
    key = metric_name.strip().lower().replace("_", " ").replace(".", " ")
    aliases = {
        "cpu": ("cpu", "percentage cpu", "processor time", "system cpu user", "node cpu seconds total"),
        "memory": ("memory", "available memory bytes", "system mem used", "memory working set", "node memory"),
        "disk": ("disk", "disk read bytes", "disk write bytes", "system disk used", "node filesystem"),
        "network_in": ("network in", "network in total", "system net bytes rcvd"),
        "network_out": ("network out", "network out total", "system net bytes sent"),
        "http_5xx": ("http 5xx", "http server errors", "5xx", "requests failed"),
        "latency": ("latency", "response time", "duration", "request duration seconds"),
        "availability": ("availability", "health", "heartbeat", "up"),
        "queue_depth": ("queue depth", "queue length", "messages", "active messages"),
    }
    for canonical, needles in aliases.items():
        for needle in needles:
            if needle in key:
                return canonical
    return key


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
