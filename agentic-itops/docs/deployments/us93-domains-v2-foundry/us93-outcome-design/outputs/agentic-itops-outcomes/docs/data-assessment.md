# Data assessment: observations, evidence and learning

## Required Data Sources

The sole Domains V2 inference feed is `EnergyObservations_CL`. Its authoritative local documentation is `domainsV2\docs\signal-processing-contract.md`, its JSON Schema and `rich-observations.md`. The wire envelope has 16 fields, SchemaVersion string 1, two record families, four domains and nine surfaces. Observation records are not fired alerts and supply no severity or confirmed diagnosis.

The consumer must project each field explicitly, decode dynamic columns with native types, preserve source timestamps and Context, and deduplicate EventId within producer identity. It must not treat the read-only portal inspector's preview or last-queued view as proof of Log Analytics queryability.

The optional domain-details-v1 profile adds qualified asset, measurement, topology and inventory context. Its bounded topology can omit relationships; inventory applicability can be partial. Preserve included/omitted counts and hashes. A missing relationship is unknown, not proof of independence. Inventory provenance explicitly says the monitoring-asset join is not established. Historical source applicability dates are not dispatch eligibility.

The source cadence is up to ten selected records per surface every 60 seconds, with a ten-minute warm-up and at most two hours per run. At maximum this is 90 observations/minute, 5,400/hour and 10,800/run. The rich-profile documentation estimates about 38.32 MB per all-surface run in SDK-style serialization, before duplicates/audits/billing normalization; actual deployed profile and consumption are unverified. Never price Logs directly from that serialization estimate.

Additional inputs are reviewed synthetic runbooks/postmortems, exact detector-generated lifecycle events, operator review records and independent evaluation labels. Labels belong exclusively to a separate evaluator. The starter corpus has eight runbooks and three postmortems and does not cover every domain case.

## Data Quality Prerequisites

Approve a per-surface policy for cadence, required baseline, quality codes, source age, missing gaps, late-arrival horizon, anomaly persistence and recovery persistence. Do not use universal stock, zero-value or quality thresholds. Treat approved values as versioned configuration, not mutable model output.

Proposed data acceptance requires every supported surface to demonstrate a ready baseline and all normal/anomaly/recovery/insufficient-evidence paths. The producer's historical 900-row acceptance establishes earlier decoder/ingestion evidence, not anomaly accuracy, current retention or current profile deployment.

Distinguish source freshness using SimulatedAt versus populated source_time_utc, observation-window order using ObservedAt, service TimeGenerated behavior and actual query availability. Historical replay uses an explicit reference clock. A source timestamp missing during communication loss stays unknown; fresh ingestion does not make it fresh.

## Privacy and Compliance

All field assets, catalogs and scenarios remain fictional and operational use is prohibited. This is not proof that operator identities, review notes, URLs or infrastructure metadata contain no personal or sensitive data. Limit free text, redact telemetry, restrict exports and separate operator identity from model context unless necessary.

No compliance certification, physical-safety assurance or regulatory use is claimed. Privacy/security owners must approve data location, retention and erasure processes before a live pilot. Review evidence, Search copies, traces, backups and provider conversations need separate retention accounting. Only synthetic reviewed knowledge is eligible for the initial index.

## Integration Points

The observation reader accesses the existing workspace with its own constrained identity. Tools receive evidence references and approved query parameters, not audit labels or unrestricted table access. Foundry receives bounded, complete typed evidence plus explicit missingness. Raw evidence remains available through authorized drill-down; token budgeting cannot truncate JSON or silently remove contradictory records.

The SQL result ledger connects findings, episodes, agent attempts, gate receipts, reviews and knowledge revisions. Blob is the evidence/artifact store, Search a rebuildable projection. Operator corrections are review data, not automatic truth or training data.

## Data Readiness Risks

The primary risks are unsupported domain thresholds, warm-up data without anomalies, missing real topology, lexical corpus bias, mixed context-profile baselines, late delivery, evaluation leakage and knowledge feedback contamination. Address them through explicit abstention, immutable versions, held-out cases and a domain-owner review.

For enriched Context, do not use fixture hashes, profile markers or authored scenario identifiers as anomaly predictors. Curated static catalog details may ground explanations but cannot establish approved engineering limits. Native cadence can differ from publication cadence. Derived measurements must not inflate independent evidence counts.

Document recovery as an observed supported transition, not proof that a suggested action worked. Domains V2 returns to normal independently of an agent proposal; causal attribution to a proposed remediation is invalid. A simulated response assessment, if later added, must be a separate labeled result and never manipulate the producer as proof of successful remediation.
