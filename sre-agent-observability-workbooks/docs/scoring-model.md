# Recommendation Scoring Model

The **Top 10 SRE Agent Recommendations** section ranks by `RecommendationScore`
(descending), then `Severity`, then `LastUpdated`. The SRE Agent computes
`RecommendationScore` when it **writes** a recommendation, so ranking is stable,
auditable, and independent of who is viewing the workbook.

## Conceptual formula

```
RecommendationScore =
    BusinessCriticality
  + Severity
  + UserImpact
  + Confidence
  + Recency
  + BlastRadius
  + ComplianceRisk
  + AutomationReadiness
  - RemediationRisk
```

Every term is normalized to a documented band so no single factor dominates.
`RemediationRisk` is **subtracted** — a risky fix is ranked lower than a safe fix
of equal benefit.

## Suggested term bands

| Term | Band | Rationale |
| --- | --- | --- |
| BusinessCriticality | 0–25 | Mission critical=25, High=18, Medium=10, Low=4. |
| Severity | 0–25 | Sev0=25, Sev1=20, Sev2=12, Sev3=6, Sev4=2. |
| UserImpact | 0–20 | Share of users/journeys affected. |
| Confidence | 0–15 | `ConfidenceScore` (0..1) × 15. |
| Recency | 0–10 | Exponential decay, ~7-day half-life. |
| BlastRadius | 0–15 | Apps/regions/subscriptions affected. |
| ComplianceRisk | 0–15 | Policy/regulatory exposure. |
| AutomationReadiness | 0–10 | Higher when a safe, rollback-able automation exists. |
| RemediationRisk (−) | 0–20 | High=20, Medium=10, Low=3 (subtracted). |

Tune the bands to your environment; keep them documented and versioned so ranking
changes are reviewable.

## Sample KQL

A runnable implementation that recomputes the score from raw inputs (useful to
validate the agent's output or backfill existing rows) is in
[`queries/kql/recommendation-score.kql`](../queries/kql/recommendation-score.kql).
It maps the categorical inputs to the bands above, applies recency decay, and
emits a `Drift` column comparing the stored `RecommendationScore` to the
recomputed value.

## Why compute at write time

- **Consistency** — every reader sees the same ranking.
- **Auditability** — the score and its inputs are stored on the row.
- **Performance** — the workbook query just orders by a column; no per-view math.
- **Governance** — changing the model is a reviewable change to the agent and the
  documented bands, not an invisible query tweak.
