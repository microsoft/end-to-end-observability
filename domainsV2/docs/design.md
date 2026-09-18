# V2 log-first design and evidence

Research date: 2026-09-10. Scope: all five monitoring and four inventory
surfaces; only log production/transport in this phase. Three independent
research passes and an architecture critic informed the design. Conflicting
claims were checked against primary sources rather than accepted as facts.

## Bounded demo redesign

The user clarified that normal sequences may repeat and a presentation lasts
about one hour, with manual stop or a full two-hour maximum. The initial hosted
full-fleet implementation produced queryable logs across all nine surfaces,
but also encountered visible outbox backpressure. Increasing compute or adding
a streaming service would solve a broader problem than the demo requires.

Use offline-compiled, domain-specific fixtures with a fixed monitored subset.
Every exposed anomaly must observably change that same subset versus normal;
changing identities only when an anomaly is selected is prohibited. Preserve
units, causal measurement relationships and unavailable/null versus physical
zero. Scenario labels stay in control state/audit, not observations.

A session-wide durable deadline begins at Prepare and includes warm-up.
The baseline window is immutable and belongs to the current RunId. Readiness
uses deduplicated observations for every selected surface; elapsed time or a
successful SDK upload alone is insufficient. Queries need observation-only
permissions, with an explicit negative audit-table access check.

An HTTP 403 is sufficient but is not assumed to be the only Azure filtering
response. The fixed audit diagnostic queries only `HasAny=count()>0` over
the previous completed UTC hour; it never requests audit fields. HTTP 200
with false remains unverified by itself. A privileged nonempty control over
identical absolute bounds before and after the managed-identity false result,
together with positive observation data and the exact scoped role condition,
can establish effective filtering for known existing data. This is operator
acceptance evidence, not a runtime fallback that treats empty results as denial.
Partial and malformed results remain failures. See the documented
[partial-access behavior](https://learn.microsoft.com/azure/azure-monitor/logs/api/azure-resource-queries#partial-access)
and [query response errors](https://learn.microsoft.com/azure/azure-monitor/logs/api/response-format);
neither provides a universal workspace-context HTTP 403 guarantee.

The worker emits at most ten observations per surface per minute, batches
across selected surfaces, and persists compact replay state rather than full
snapshots. Queued and ingestion-accepted views are distinguished from queryable
data. Anomaly expiry and Stop are enforced server-side; restarts cannot extend
them or cause catch-up bursts. Old accepted outboxes remain deliverable without
running the previous full-fleet generator.

## Ingestion decision

Use the **Logs Ingestion API**, a new Direct DCR, two custom Analytics tables
and a dedicated V2 workspace. Logs Ingestion is an Azure Monitor service,
but invoking it is not the same as creating an Azure Monitor alert. V2 has no
alert rules, action groups, App Insights integration or legacy alert handlers.

| Alternative | Benefit | Reason not selected |
| --- | --- | --- |
| Direct DCR ingestion | Explicit schema, small resource set, managed identity, KQL-ready data | Selected; application owns durable delivery and visible failure handling |
| Workspace-based App Insights / OpenTelemetry | Strong application tracing and standard instrumentation | Synthetic operational records are not web-app request telemetry; implicit properties and instrumentation would add unnecessary coupling |
| Event Hubs plus a consumer | Durable streaming fan-out and independent scaling | Extra service/consumer/checkpoint complexity is unnecessary for this bounded single-instance POC |
| In-process queue only | Simple and low latency | Not durable across a recycle; unacceptable for accepted commands |
| Shared V1 workspace | Fewer resources | Existing or future V1 wildcard queries could match synthetic V2 rows |

The new workspace separates the V2 producer from existing V1 rule scopes. This
is not a universal promise that nobody can write a future cross-workspace
query; authorized administrators retain control over Azure resources.

## Domain fidelity and isolation

The monitoring adapters reuse domain-owned synthetic physics/catalogs rather
than inventing one generic metric generator. Upstream production, midstream
transport and downstream refining retain distinct identities. Downstream
portfolio is a downstream surface, not a fifth industry domain.

Inventory readers remain domain-specific. Opening balances, catalogs, units,
fitment and fictional local-coordinate frames are preserved. V2 mutations
affect only V2 state, never the source ledger or V1 browser playback.

The V2 gateway does not import `dashboard.app`, start V1 queue/ADLS controllers,
or attach V1 Azure logging configuration. All Azure settings use a separate
`V2_` prefix and the deployed identity is scoped only to new V2 resources.

## Observation and audit separation

`EnergyObservations_CL` has a stable typed envelope:

`TimeGenerated`, `ObservedAt`, `SimulatedAt`, `SchemaVersion`, `EventId`, `RunId`,
`Domain`, `Surface`, `Portal`, `RecordType`, `EntityId`, `LocationId`, `Payload`,
`Context`, `IsSynthetic`, `OperationalUse`.

Domain values, units and quality are retained inside `Payload`; fictional
equipment/location metadata is in `Context`. Nulls remain null, physical zeros
remain zeros. Dynamic fields and overall requests are bounded before upload.

`EnergyInjectionAudit_CL` contains the separate command record:

`TimeGenerated`, `SchemaVersion`, `EventId`, `RunId`, `Domain`, `Surface`,
`Action`, `Scenario`, `Outcome`, `IsSynthetic`, `OperationalUse`.

No personal identifiers, secrets, IP addresses or credentials are logged.
`IsSynthetic=true` is retained on **all** records and is not an anomaly label.
Scenario names, explicit anomaly booleans and simulator ground truth are
excluded from observations. A future detector must not join audit labels
into its feature input. Audit access control for the eventual agent must be
designed in phase 2; merely using separate tables is not column-level security.

Original synthetic time is separate from the real observation time. Outbox
retries preserve the original event and identifier. Azure can replace an
out-of-window `TimeGenerated`; `ObservedAt` and `SimulatedAt` preserve source
timelines independently. Arrival order is not observation order.

## Durable state, failure handling and controls

The application commits state changes and pending records in one conditional
state-document write. Azure Blob ETags reject stale writers; local operation
uses a durable local store with equivalent conflict behavior. The hosting
plan is deliberately single-instance/single-worker; the conflict checks are
not a substitute for designing a distributed high-throughput orchestrator.

The delivery cursor advances only after the SDK returns without error.
Multi-record requests remain below the SDK's one-megabyte chunk boundary.
No callback swallows failed chunks. Transient failures are retried with
bounded backoff; permanent or exhausted failures remain visible and parked.
An acknowledgement loss may replay accepted records: KQL deduplication by
stable `EventId` is required.

Same-origin/CSRF checks, UUID idempotency keys, enum/integer validation,
bounded durations, one active demo session and byte/backlog caps protect
the control path. The IP allow-list limits network access but is not named-user
authentication. This access model is a user-approved synthetic-data POC choice,
not a production authorization design.

## Primary-source ledger

| Claim | Primary source | Confidence / qualification |
| --- | --- | --- |
| Direct JSON ingestion routes through a DCR to custom tables; Monitoring Metrics Publisher is scoped to the DCR | [Logs Ingestion API](https://learn.microsoft.com/azure/azure-monitor/logs/logs-ingestion-api-overview) | High |
| A new `kind: Direct` DCR creates its own `endpoints.logsIngestion`; a DCE is not mandatory for this public authenticated path | [DCR structure](https://learn.microsoft.com/azure/azure-monitor/data-collection/data-collection-rule-structure) | High; earlier DCE-only advice was rejected |
| Python upload returns None; lists are chunked at one MB; absent callback raises the first failure | [Python API](https://learn.microsoft.com/python/api/azure-monitor-ingestion/azure.monitor.ingestion.logsingestionclient) | High; REST success is 204, not a body with 200 |
| TimeGenerated outside two days past/one day future can be replaced with received time | [Standard columns](https://learn.microsoft.com/azure/azure-monitor/logs/log-standard-columns) | High; not a claim that such rows are always dropped |
| Acceptance and queryability differ | [Ingestion latency](https://learn.microsoft.com/azure/azure-monitor/logs/data-ingestion-time) | High; no fixed sub-30-second guarantee |
| Custom schemas require supported types and `_CL` table names | [Custom tables](https://learn.microsoft.com/azure/azure-monitor/logs/create-custom-table) | High |
| API requests/fields/throughput have limits | [Monitor limits](https://learn.microsoft.com/azure/azure-monitor/fundamentals/service-limits) | High; implementation uses conservative smaller bounds |
| Workspace design is an isolation/operations decision | [Workspace design](https://learn.microsoft.com/azure/azure-monitor/logs/workspace-design) | High; not immutable isolation from administrators |
| Legacy table-query Actions cannot isolate individual custom log tables | [Table access](https://learn.microsoft.com/azure/azure-monitor/logs/manage-table-access) | High; do not grant `query/Tables.Custom/read`, which would also expose audit data |
| Granular RBAC separates query execution from conditional table-data access | [Minimum permissions](https://learn.microsoft.com/azure/azure-monitor/logs/granular-rbac-log-analytics#role-selection) | Query/read alone grants no table data; an unconditioned tables/data/read grant would be too broad |
| A restrictive assignment condition can select one exact table name | [Condition configuration](https://learn.microsoft.com/azure/azure-monitor/logs/granular-rbac-use-case#build-restrictive-condition) | Require live observation success and audit denial; permissions are additive and propagation may take 15 minutes |
| Central US B2 Linux retail is $0.035/hour; Analytics ingestion paid meter is $2.76/GB | [Retail Prices API](https://prices.azure.com/api/retail/prices) queried 2026-09-10 | Point-in-time USD retail, not a billing quote; free benefits not assumed |

SDK source inspection additionally confirmed use of azure-core RetryPolicy.
It is incorrect to claim the SDK never honors Retry-After. The application
still needs a durable outer retry schedule across process restarts.

## Cost and limitations

At 730 hours/month B2 is approximately $25.55 before tax/agreement adjustments.
Ingestion and storage/network operations vary. The replay limit is 10,800
observations per two-hour session across nine surfaces, roughly 11 MB at an
assumed 1 KB per record before billing overhead. Duplicates and control audit
add overhead. A $40-70/month phase-one planning target at occasional demo usage
is below the user-approved $500/month target, not an invoice guarantee.
Stopping a replay does not stop fixed hosting/network charges.
Workspace daily caps can overshoot
and are not hard spending controls. Retention is 30 days; no paid archive or
commitment tier is provisioned.

No standards compliance, operational calibration, real manufacturer
compatibility, genuine facility coordinates or safe field-work recommendation
is asserted. Phase 2 must add detector evaluation, alert lifecycle, evidence
retrieval, approval and human-reviewed dispatch separately.
