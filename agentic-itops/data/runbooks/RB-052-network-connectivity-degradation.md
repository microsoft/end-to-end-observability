# RB-052: Network connectivity degradation

id: RB-052
symptom_class: connectivity
service: shared
severity: medium
owner: network-ops@contoso.com
last_reviewed: 2026-05-20

## When this applies

Elevated packet loss, connection timeouts, or `Network In`/`Network Out`
deviation from baseline.

## Note on alert quality

`Network In - AMBA` is the single noisiest rule in the estate. Before
treating a firing as an incident, confirm it has persisted beyond one
evaluation window. The overwhelming majority self-resolve.

This rule is a standing candidate for threshold retuning — see the
alert hygiene backlog.

## Immediate mitigation

There is rarely a safe automated mitigation for connectivity. Gather
evidence, correlate against recent network changes, and escalate.

    annotate_incident

## Root cause resolution

Check, in order: recent NSG or route-table changes, upstream provider
status, then host-level NIC saturation.
