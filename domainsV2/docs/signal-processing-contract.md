# Signal-processing integration contract

**Status: producer-owned contract v1.1.0; consumer acceptance pending.**
This documents `SchemaVersion = "1"` observations with the optional
`Context.details_profile = "domain-details-v1"` enrichment profile.
The 16-column envelope remains unchanged; the nested context and selected
measurement set are richer than the original compact replay. Legacy retained
rows can lack the profile marker. This does not create alerts or claim that the external
signal-processing implementation already supports Log Analytics.

Give the signal-processing owner this guide, the
[JSON Schema](../contracts/energy-observations.v1.schema.json), the
[JSONL examples](../contracts/energy-observations.v1.examples.jsonl), and the
[observation-only KQL](signal-processing.kql). The examples are generated
offline from the same packaged replay fixtures as the portals, not exported
from a customer's workspace. They are shape/edge-case examples with separate
fictional run IDs, not a complete time series or an anomaly-accuracy benchmark.
Use them locally; do not upload these static example timestamps to the live
workspace as a new demo.

See [rich domain observations](rich-observations.md) for populated detail groups,
measurement-selection constraints, source limitations and deployment migration.
Consumers must review the profile and selected quantity meanings before use;
an unchanged envelope is not proof of semantic compatibility. Continue preserving
the entire typed `Context` object. Do not use profile/fixture versions as anomaly
features or combine a new run's changed selection with an old run's baseline.

Every domain portal includes **Inspect selected log details**. Before a queued
frame exists, this is explicitly a read-only source projection without an
EventId or upload claim. After publication it reconstructs the exact last-queued
16-field envelopes, including their original identities/times. This is useful
for inspection but is not a replacement for Log Analytics query evidence.
The read-only endpoint is `/api/v2/surfaces/<surface>/observations`; opening it
does not generate, enqueue, retry or publish logs.

## Producer/consumer boundary

The producer writes neutral observations through the Logs Ingestion API:

- Table: **`EnergyObservations_CL`**.
- DCR input/output stream: **`Custom-EnergyObservations_CL`**.
- Transformation: `source`, preserving the declared columns.
- Encoding: JSON objects with dynamic `Payload` and `Context`; not log-message
  strings, escaped JSON inside strings, Common Alert Schema, or incident envelopes.
- Delivery: at least once. SDK acceptance is not confirmation of queryability.

The owner must add a **Log Analytics observation reader**, followed by their
signal detection/classification. Do not feed these rows directly into an
adapter expecting fired Azure Monitor alerts. Do not label every observation
as an incident or turn a portal button click into an alert.

An incident, severity, confidence, finding, alert, or dispatch is a downstream
derived result, stored separately with references to source `EventId` values.
There is no producer `severity`, alert rule ID, or confirmed root cause to map.
Do not manufacture an ARM resource ID for a fictional field asset.

## Envelope

All 16 columns are present in the producer envelope. Project them explicitly
from query results: Log Analytics can add service columns outside this schema.
The JSON Schema checks the envelope structure, current payload-family types
and domain/surface mapping; it does not prove physical validity, domain semantics, or absence of
semantic label leakage in arbitrary text.

| Field | Log Analytics type | Meaning |
| --- | --- | --- |
| `TimeGenerated` | datetime | Observation time on upload; Azure can replace an out-of-window timestamp |
| `ObservedAt` | datetime | Preserved observation timeline for windows and ordering |
| `SimulatedAt` | datetime | Synthetic source timeline; not ingestion time |
| `SchemaVersion` | string | Literal `"1"`, not numeric `1` |
| `EventId` | string | Opaque 64-character lowercase hexadecimal ID, stable across delivery retries |
| `RunId` | string | Opaque producer run ID; use the emitted value, not an inferred browser-session ID |
| `Domain` | string | Owning domain |
| `Surface` | string | Domain-specific monitoring or inventory surface |
| `Portal` | string | `monitoring` or `inventory` |
| `RecordType` | string | Domain-owned record family, not an anomaly label |
| `EntityId` | string | Domain-local entity, not necessarily an equipment asset |
| `LocationId` | string | Domain-local location; never join across namespaces by name alone |
| `Payload` | dynamic | Domain-native values, units, quality and source-time information |
| `Context` | dynamic | Fictional asset, location, catalog or part context |
| `IsSynthetic` | boolean | Always `true`; constant in both normal and abnormal observations |
| `OperationalUse` | string | Always `"prohibited"` |

Timestamp strings in JSON use ISO 8601 with an explicit timezone; normalize
to UTC without relabeling local clock text as `Z`. SDKs may deserialize query
datetimes as datetime objects: serialize them consistently before JSON Schema
validation. Decode `dynamic` fields as objects, not as text to search.
The query REST response can carry dynamic cells as JSON text. Decode those
cells according to their column metadata before applying this producer schema;
that response encoding does not mean the producer stringified its payload.
At query time Azure can represent a populated nested `source_time_utc` as a
dynamic datetime. Normalize native datetime values to timezone-aware ISO strings
when materializing JSON; do not treat this service representation as a changed
producer type. Map response column names to row positions with matching lengths,
preserving case and all 16 projected columns.

## Nine surfaces, four owning domains

| Surface | Domain | Portal |
| --- | --- | --- |
| `power-grid` | `power-grid` | monitoring |
| `upstream` | `upstream` | monitoring |
| `midstream` | `midstream` | monitoring |
| `downstream` | `downstream` | monitoring |
| `downstream-portfolio` | `downstream` | monitoring |
| `power-grid-inventory` | `power-grid` | inventory |
| `upstream-inventory` | `upstream` | inventory |
| `midstream-inventory` | `midstream` | inventory |
| `downstream-inventory` | `downstream` | inventory |

Downstream portfolio is not a fifth domain. The UI retains full catalogs/maps;
the replay emits a small representative selection, not every displayed asset.
Do not infer an outage or missing inventory from an unselected entity.

## Series identity and value semantics

The compact replay currently emits these record families:

| `RecordType` | Required payload features | Domain-specific optional fields |
| --- | --- | --- |
| `telemetry` | `measurement` string, `value` number or null, `unit` string, `quality` string | `source_time_utc`, `phase`, `tag_id`, `device_id`, delivery/freshness/origin/clock state, uncertainty |
| `inventory_position` | `quantity` integer or null, `uom` string, `quality` string | Part IDs/keys/stock codes and catalog-specific descriptors |

Do not rename inventory `quantity`/`uom` into monitoring `value`/`unit` in the
raw envelope. A consumer can derive unified features separately while retaining
the original fields. Quality is an opaque domain string, not a universal alert
severity enumeration. Optional fields and context differ by surface.

Keep the following identity components, in order:

```text
[Domain, Surface, RunId, RecordType, EntityId, LocationId,
 Payload.measurement, Payload.tag_id, Payload.phase]
```

Missing optional measurement/tag/phase components remain null. Serialize this
tuple as an array or use an unambiguous structured key; do not concatenate with
an unescaped delimiter. Grouping by `EntityId` alone mixes multiple quantities,
phases, tags and potentially different domain namespaces.

- Keep `Payload` and `Context` intact alongside any derived numeric features.
- Do not coalesce null to zero. Physical zero and unavailable data mean different
  things. Preserve non-good quality, source age and delivery state.
- Read units and measurement meaning from the domain payload/context. Do not
  average incompatible measurements or sum unlike inventory SKUs/units.
- Preserve stale source timestamps; an observation arriving now can describe
  old data. Compare `SimulatedAt` with `Payload.source_time_utc` when available;
  `ObservedAt` alone cannot establish source freshness or transport latency.
  Source time can be missing or explicitly null during unavailable communications.
  Keep that unknown; do not substitute the observation time and claim freshness.
- Inventory state, quantity and delivery state are observations, not simulator
  ground-truth labels. Inventory and monitoring need different feature adapters.
- Inventory catalogs/fitment are fictional. A telemetry-to-part/equipment join
  must be explicitly established; shared-looking names do not prove compatibility.
- Handle an unsupported `RecordType` or absent required feature explicitly:
  report unsupported/insufficient evidence, not healthy or zero.

Normal inventory can already be below ten. A low opening balance alone is not
proof of an intervention; use domain context and baseline changes, not a
universal stock threshold. Likewise, telemetry zero can be normal (for example,
PV at night). Neither zero nor non-good quality is itself a fired alert.

## Polling, duplicates and bounded demonstrations

Run discovery and each query in `signal-processing.kql` separately. Supply an
exact discovered `RunId`; do not assume all surfaces share one run ID. For a
stopped historical run, select an absolute UTC observation window rather than
`now()`. Discovery returns retained history, not active-session status.

The queries preserve the full contract and deduplicate `EventId`. A derived
`SignalIdentity` column in the third example is consumer-side metadata, not an
extra wire field. These queries are bounded analysis examples, **not a complete
lossless incremental reader**.

The consumer still owns:

1. A persistent ingestion/checkpoint strategy with overlapping reads and stable
   EventId deduplication. Keep deduplication state across polls and restarts.
2. Late-arrival handling and explicit catch-up beyond the example's seven-day
   `TimeGenerated` scan horizon. Do not use `ObservedAt > last_seen` alone as an
   ingestion cursor: it can miss delayed/out-of-order deliveries.
3. Response/error handling. Reject partial query results, throttling, malformed
   rows, unsupported schemas, and response-limit truncation as incomplete work;
   do not advance a success checkpoint or infer normal operation.
4. Window subdivision or paging when result limits are reached; the example
   deliberately does not use `take` to silently truncate detector input.
5. Run/surface isolation, baseline readiness, and firing/recovery lifecycle.
   Neither empty query results nor a stopped session prove physical recovery.

V2 samples at most ten fixed record identities per surface every 60 seconds,
with a ten-minute normal warm-up. The original session deadline is at most two
hours, including warm-up; manual Stop can end generation earlier. Normal
observations continue between interventions. An abnormal intervention changes
the observed values/states at the next sample; it does not add a distinctive
burst or an explicit anomaly flag. The initial default intervention is five
minutes, with manual or automatic recovery.

Stopping generation does not discard queued records. Delivery/indexing can
continue later, and retained logs remain queryable. The downstream detector
needs its own approved schedule and stop/expiry policy.

## Prevent control-label leakage

**`EnergyInjectionAudit_CL` is operator/evaluation data, not detector input.**
The producer recursively rejects scenario/ground-truth/detector-verdict control
keys from observation rows. Do not join audit records, inject scenario names,
or obtain hidden expected outcomes from fixture manifests as model features.
Constant `IsSynthetic=true` and `OperationalUse=prohibited` are provenance,
not anomaly labels.

Use a separate consumer execution identity and explicitly scoped observation
query access. The existing producer's table-conditioned query role is an
example, not an automatic grant or guarantee for a future tool/SRE
identity. Avoid broad inherited/custom-table read grants. The owner must prove
observation access and effective audit exclusion for the actual query identity;
an empty audit result alone is not proof of denied access.

An authorized evaluator can use separate labels after inference to assess
results. That evaluator must not supply those labels to the detector.

## Versioning and acceptance

Contract document version `1.0.0` describes the current wire
`SchemaVersion = "1"`. Editorial clarifications can revise this document
without changing wire data. A new required field, changed type/meaning, removed
field, or incompatible payload interpretation requires an agreed migration and
version change; do not silently rewrite a version-1 stream for one consumer.
Future consumers must explicitly accept supported surface/record families.

Before calling the integration compatible, the owner should demonstrate:

- All nine surfaces are decoded without losing raw fields or identity.
- Same-EventId redelivery is processed once; different signals on one entity
  remain distinct, and independent runs never share a baseline.
- Null, zero, stale/unavailable data and inventory delivery state remain distinct.
- Control/audit labels are unavailable to the detector.
- Query failure/partial results and unsupported data produce visible incomplete
  outcomes rather than normal classifications.
- Findings cite source EventIds and preserve domain/surface/run identity.
- Actual consumer input, normal/anomaly/recovery behavior, and latency are
  exercised against queryable V2 observations once its reader exists.

Until then the accurate claim is **producer contract ready; consumer integration
pending**, not end-to-end signal-processing compatibility.

## Maintaining the handoff artifacts

From the repository root:

```powershell
python -m pip install -e ".\domainsV2[validation]"
python domainsV2\scripts\export_signal_examples.py --check
python -m unittest discover -s domainsV2\tests -p test_signal_contract.py -v
```

After an intentionally reviewed fixture change, regenerate only the named
example artifact with `python domainsV2\scripts\export_signal_examples.py --write`.
The generator uses the offline replay/event APIs; it does not start a portal,
open a cloud connection, generate a live session, or modify replay fixtures.
