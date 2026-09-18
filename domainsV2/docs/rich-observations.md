# Rich domain observations

`Context.details_profile = "domain-details-v1"` identifies curated source-backed
details in the existing `SchemaVersion = "1"` envelope. Older retained rows can
lack this marker. See the [consumer contract](signal-processing-contract.md)
and [JSON examples](../contracts/energy-observations.v1.examples.jsonl).

The producer still ships JSON objects, not prose alerts or JSON encoded inside
strings. No new Log Analytics columns, DCR transformation, permissions, alert
rules or downstream detector implementation are required for this profile.
The external signal-processing owner must still accept the new meanings and
selection. Preserve entire typed Payload and Context objects.

## Boundaries

- All equipment, ratings, positions and applicability remain fictional,
  synthetic and prohibited for operational use.
- Source catalog facts are not approved engineering limits, calibration,
  physical condition, reservations, or available-to-promise stock.
- Part applicability describes the source catalog's fictional configurations;
  it does not establish an installed-asset/BOM join or real interchange.
- Power Grid's monitoring and inventory use different fictional areas.
  Upstream's selected parts must not be assumed to fit a selected lift.
- Portfolio indices and equivalent-volume units are not physical pressure,
  temperature, energy or barrels. Derived measurements are not independent
  corroborating sensors.
- Static details do not change between normal and intervention profiles.
  Actual readings and quality remain in Payload; extra sensors are not hidden
  in Context to evade the ten-record limit.
- Unknown facts stay absent/null as appropriate, never plausible industrial
  defaults. Operator audit, scenario names and inferred causes are not inputs.

## Populated profile

Monitoring adds `Context.asset`, `Context.measurement` and `Context.topology`.
The first contains curated catalog configuration, the second literal quantity
meaning and available source cadence/signal/device definitions, and the third
at most four typed fictional links with available/omitted counts. Source
`signal_id` remains descriptive metadata, not a silent replacement of the
existing `Payload.tag_id` identity.

| Surface | Selected measurements and useful added context |
| --- | --- |
| Power Grid | Same-meter P/Q/S/I/V; transformer condition, cooling, loading and top-oil temperature; air temperature. Nominal voltage/phases and synthetic ratings are qualified model parameters, not approved limits. |
| Upstream | Well tubing pressure/temperature, lift current/run/cycle state, separator gas rate/pressure, treater oil and FWKO water rates, compressor discharge pressure. Well/lift/completion context is joined explicitly where present; these do not all represent one well's production. |
| Midstream | Inlet/outlet flow, pump power/temperature/speed/vibration, valve command/feedback, tank level and derived mass flow. Source pipeline/pump/tank parameters remain fictional; density is available but not selected within the cap. |
| Downstream refinery | Pressure, temperature, power, throughput, availability and condition indices with aggregate equipment/tag/device context. The source still lacks physical flow/vibration channels; none are fabricated. |
| Downstream portfolio | Corridor inlet/outlet flow, target/actual throughput, yield share, inventory/balance and power indices. Literal equivalent-volume/index semantics and asset primary-product versus observed-material distinctions are retained. |

Inventory adds `Context.part_details`, `Context.applicability`,
`Context.location_details` and `Context.inventory_provenance` on all four
surfaces. These preserve populated classification/tracking/storage facts,
up to 12 applicability entries, location hierarchy/storage capabilities,
and SHA256s of the four exact input JSONL files. Bounded arrays include
total/included counts, a partial flag and full projected-content hash.
The grid's populated specification text is explicitly fictional; absent
specifications elsewhere remain absent. Source applicability dates are retained
as source dates, not evaluated as dispatch eligibility or shifted replay times.
No absolute local paths, source user identities or current usable-stock
quantities are added to Context.

## Inspection and delivery

Open **Inspect selected log details** on any monitoring or inventory portal.
The read-only `/api/v2/surfaces/<surface>/observations` endpoint returns:

- `preview_not_queued`: selected source projections without fabricated EventIds.
- `last_queued`: the exact original 16-field envelopes reconstructed from the
  atomically saved replay position and envelope identity.
- `legacy_frame_without_envelope`: an older queued projection whose original
  envelope was not saved; no EventId or exact-envelope claim is invented.

Queued is not SDK acceptance or queryability. Verify the owned RunId in
`EnergyObservations_CL`, retaining Context as dynamic data. No inspector GET
generates, queues or retries records. An injection/recovery command affects the
next one-minute sample, not the last-queued inspector view.

## Selection and operational limits

Offline selection retains fixed identities across all advertised interventions
and their three authored phases. Domain-aware companion selection improves the
original affected-reading filler, subject to the same ten-row cap and coverage
proof. This is a deliberately authored demonstration, not full-fleet monitoring
or an independent anomaly-accuracy benchmark.

The original 60-second publication interval, ten-minute warm-up, two-hour
maximum, outbox/daily budgets and at-least-once delivery remain unchanged.
Native sampling cadence can differ from publication cadence. Timestamp rebasing
does not recompute weather, solar or process physics for the current wall clock.
Larger records consume the pending-outbox allowance sooner during failed delivery.

One extra 1,000 serialized bytes per observation adds 10.8 MB to a maximum
10,800-observation run, excluding audits, duplicates and billing normalization.
Do not enlarge field/batch bounds or rely on service-side field truncation.

The compiled profile's 1,620 authored observations measure 2,146-5,535 canonical
bytes per full envelope. The normal all-nine-surface SDK-style array is about
319 KB per tick, or 38.32 MB across 120 ticks before audits, duplicates, state
and billing normalization. This is a serialization estimate, not an Azure cost.

## Rebuild and deployment

Rebuild through `scripts\build_replay_fixtures.py` using explicit reviewed
inventory roots. Regenerate the contract examples with
`scripts\export_signal_examples.py --write`. The builder verifies stable
identity/coverage and size constraints; the tests additionally cover profile,
neutrality, recovery, preservation and transport semantics.

A rebuild changes replay identity. Existing state must not be reset
automatically. A version transition is an explicit operator action after
generation stops and all pending/legacy records drain. Preserve the original
state in a verified archive and use conditional writes; an active worker or
concurrent change must block transition. Follow the Azure deployment plan and
the guarded transition tool, not manual blob deletion.

For an approved private-network rollout, `V2_MAINTENANCE_MODE=1` prevents the
worker from starting and rejects HTTP mutation methods. Verify
`/api/health/live` reports that barrier and the accepted fixture version, then
execute the packaged transition script through an authorized app-container
session using the existing managed identity. The CLI is dry-run by default;
apply additionally requires `--expected-old-version`, `--apply` and
`--writers-stopped`. The acknowledgement is not a lock: every writer must
actually remain quiescent. Remove the temporary setting after the verified
transition, restore the existing worker/startup configuration and confirm
the new session is idle before preparing a demo. No public migration endpoint
or network/permission exception is added.

## Research basis

Domain-specific decisions follow the 2026-09-12 source/replay comparison.
Measurement/time semantics draw on
[OPC DataValue](https://reference.opcfoundation.org/specs/OPC-10000-4/7.11),
[OPC analog units/ranges](https://reference.opcfoundation.org/specs/OPC-10000-8/5.3.2)
and [OpenTelemetry logs](https://opentelemetry.io/docs/specs/otel/logs/data-model/).
Domain context draws on [NIST grid timing](https://www.nist.gov/programs-projects/precision-timing-smart-grid-systems),
[Energistics PRODML](https://docs.energistics.org/PRODML/PRODML_TOPICS/PRO-PFM-000-003-0-C-sv2000.html)
and [OPC ISA-95 concepts](https://reference.opcfoundation.org/specs/OPC-10030/4.2).
These guide structure, not field values or standards-compliance claims.
