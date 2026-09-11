"""Alert Hygiene Analyst -- skill #9, code half.

Batch pass over alert history that turns "most of our alerts are noise"
into a ranked, defensible tuning backlog.

Why it matters commercially: this runs against a customer's existing
alert export with no live incident and no agent touching production. It
can therefore be delivered inside the Architecture Design Review, before
any security gate has been cleared -- which makes it the cheapest real
artifact in the portfolio.

The scoring is code because it is arithmetic over a corpus. The prompt
half (see agents/09-alert-hygiene) turns these findings into customer-
readable recommendations.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Sequence

from .envelope import IncidentEnvelope, Severity


@dataclass
class RuleStats:
    """Aggregate behaviour of one alert rule over the sample window."""

    rule: str
    source: str = ""
    fired: int = 0
    resolved: int = 0
    distinct_resources: set[str] = field(default_factory=set)
    distinct_fingerprints: set[str] = field(default_factory=set)
    severities: list[Severity] = field(default_factory=list)
    timestamps: list[str] = field(default_factory=list)

    @property
    def auto_resolve_rate(self) -> float:
        """Share that resolved on their own.

        A high rate means the condition self-corrected before anyone
        acted -- the textbook signature of a threshold set too tight.
        """
        return round(self.resolved / self.fired, 3) if self.fired else 0.0

    @property
    def flap_ratio(self) -> float:
        """Firings per distinct symptom. High means the same thing re-alerting."""
        distinct = len(self.distinct_fingerprints) or 1
        return round(self.fired / distinct, 2)

    @property
    def dominant_severity(self) -> Severity:
        if not self.severities:
            return Severity.MEDIUM
        return min(self.severities, key=lambda s: s.rank)

    @property
    def noise_score(self) -> float:
        """0-100. Higher is noisier. Weighted, not a single signal.

        Volume alone is a bad proxy -- a rule that fires often and is
        always actionable is a good rule. Noise is volume *combined with*
        self-resolution, flapping, and low severity.
        """
        volume = min(self.fired / 50.0, 1.0) * 30
        auto = self.auto_resolve_rate * 30
        flap = min((self.flap_ratio - 1) / 4.0, 1.0) * 25 if self.flap_ratio > 1 else 0
        low_sev = {
            Severity.CRITICAL: 0.0,
            Severity.HIGH: 0.15,
            Severity.MEDIUM: 0.5,
            Severity.LOW: 0.85,
            Severity.INFO: 1.0,
        }[self.dominant_severity] * 15
        return round(min(volume + auto + flap + low_sev, 100.0), 1)

    @property
    def recommendation(self) -> str:
        if self.noise_score >= 70:
            return "suppress_or_retune"
        if self.auto_resolve_rate >= 0.8 and self.fired >= 5:
            return "raise_threshold_or_add_duration"
        if self.flap_ratio >= 3:
            return "add_dampening_window"
        if self.dominant_severity in (Severity.LOW, Severity.INFO) and self.fired >= 20:
            return "downgrade_to_dashboard_only"
        if self.fired == 1:
            return "keep_insufficient_data"
        return "keep"

    def rationale(self) -> list[str]:
        notes: list[str] = []
        if self.auto_resolve_rate >= 0.8 and self.fired >= 5:
            notes.append(
                f"{int(self.auto_resolve_rate * 100)}% of firings resolved without "
                f"intervention -- the threshold is tighter than the system's normal variance"
            )
        if self.flap_ratio >= 3:
            notes.append(
                f"{self.flap_ratio} firings per distinct symptom -- the same condition "
                f"is re-alerting rather than staying open"
            )
        if self.fired >= 50:
            notes.append(f"high volume: {self.fired} firings in the sample window")
        if self.dominant_severity in (Severity.LOW, Severity.INFO) and self.fired >= 20:
            notes.append(
                f"{self.fired} firings at {self.dominant_severity.value} severity -- "
                f"unlikely to be driving action"
            )
        if len(self.distinct_resources) == 1 and self.fired >= 10:
            notes.append(
                "every firing came from one resource -- likely a resource problem, "
                "not a fleet-wide condition"
            )
        if not notes:
            notes.append("behaviour within normal bounds for the sample window")
        return notes

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "source": self.source,
            "fired": self.fired,
            "resolved": self.resolved,
            "auto_resolve_rate": self.auto_resolve_rate,
            "flap_ratio": self.flap_ratio,
            "distinct_resources": len(self.distinct_resources),
            "distinct_symptoms": len(self.distinct_fingerprints),
            "dominant_severity": self.dominant_severity.value,
            "noise_score": self.noise_score,
            "recommendation": self.recommendation,
            "rationale": self.rationale(),
        }


def analyze(envelopes: Sequence[IncidentEnvelope]) -> dict[str, Any]:
    """Produce the tuning backlog from a batch of normalized envelopes."""
    stats: dict[str, RuleStats] = {}

    for envelope in envelopes:
        key = envelope.title or "(unnamed rule)"
        entry = stats.get(key)
        if entry is None:
            entry = RuleStats(rule=key, source=envelope.source)
            stats[key] = entry
        entry.fired += 1
        if envelope.monitor_condition.value == "resolved":
            entry.resolved += 1
        entry.distinct_resources.add(envelope.resource.id or envelope.resource.name)
        entry.distinct_fingerprints.add(envelope.fingerprint)
        entry.severities.append(envelope.severity)
        if envelope.first_seen_at:
            entry.timestamps.append(envelope.first_seen_at)

    ranked = sorted(stats.values(), key=lambda s: s.noise_score, reverse=True)
    actionable = [s for s in ranked if s.recommendation not in ("keep", "keep_insufficient_data")]
    noisy_firings = sum(s.fired for s in actionable)
    total_firings = sum(s.fired for s in ranked) or 1

    return {
        "window": _window(envelopes),
        "total_firings": total_firings,
        "distinct_rules": len(ranked),
        "rules_flagged": len(actionable),
        "firings_attributable_to_flagged_rules": noisy_firings,
        "potential_volume_reduction_pct": round(100 * noisy_firings / total_firings, 1),
        "by_recommendation": _count_by(ranked, lambda s: s.recommendation),
        "findings": [s.to_dict() for s in ranked],
    }


def top_findings(report: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    return report.get("findings", [])[:limit]


def format_report(report: dict[str, Any], limit: int = 10) -> str:
    """Console rendering. Used by the CLI and the demo."""
    lines = [
        "ALERT HYGIENE ANALYSIS",
        f"  window:            {report['window']}",
        f"  total firings:     {report['total_firings']}",
        f"  distinct rules:    {report['distinct_rules']}",
        f"  rules flagged:     {report['rules_flagged']}",
        f"  potential volume reduction: {report['potential_volume_reduction_pct']}%",
        "",
        f"{'NOISE':>6}  {'FIRED':>5}  {'AUTO':>5}  {'FLAP':>5}  RULE / RECOMMENDATION",
    ]
    for finding in report["findings"][:limit]:
        lines.append(
            f"{finding['noise_score']:>6}  {finding['fired']:>5}  "
            f"{finding['auto_resolve_rate']:>5}  {finding['flap_ratio']:>5}  "
            f"{finding['rule'][:44]:<44} -> {finding['recommendation']}"
        )
    return "\n".join(lines)


def _count_by(items: Sequence[RuleStats], key) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for item in items:
        out[key(item)] += 1
    return dict(out)


def _window(envelopes: Sequence[IncidentEnvelope]) -> str:
    stamps = sorted(e.first_seen_at for e in envelopes if e.first_seen_at)
    if not stamps:
        return "unknown"
    first, last = stamps[0], stamps[-1]
    try:
        days = (
            datetime.fromisoformat(last.replace("Z", "+00:00"))
            - datetime.fromisoformat(first.replace("Z", "+00:00"))
        ).days
        return f"{first[:10]} to {last[:10]} ({days} days)"
    except ValueError:
        return f"{first[:10]} to {last[:10]}"
