# RB-061: IoT telemetry gap or stale sensor data

id: RB-061
symptom_class: data_quality
service: rg-refinery-midstream
severity: high
owner: midstream-ops@contoso.com
last_reviewed: 2026-08-05

## When this applies

Device telemetry stops arriving, or arrives with timestamps materially
behind wall clock. Includes flow-rate and suction-pressure gaps from
midstream pump sensors.

## Why it matters more here than elsewhere

A telemetry gap on an industrial asset is not only a data problem. The
control room may be making decisions on stale readings without knowing
they are stale. Treat a sustained gap as an operational event, not a
monitoring event.

## Diagnostic signature worth knowing

Falling suction pressure alongside a flow-rate drop on the same pump is
the **cavitation signature**. It is a mechanical condition, not an IT
one. Do not attempt an IT remediation — notify the control room
immediately.

## Immediate mitigation

    annotate_incident

Then confirm whether the gap is ingestion-side (IoT Hub, historian) or
device-side. Device-side gaps go to the control room, not to IT.

## Root cause resolution

Ingestion-side: check IoT Hub throttling and historian write
availability (RB-021). Device-side: OT engineering owns it.
