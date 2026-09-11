# RB-030: Memory pressure and OOM kills

id: RB-030
symptom_class: saturation
service: rg-app-portal
severity: high
owner: portal-team@contoso.com
last_reviewed: 2026-07-28

## When this applies

Available memory below threshold, or container restarts with reason
`OOMKilled`.

## Immediate mitigation

    scale_out

Adding an instance relieves pressure without dropping in-flight work,
which is why it is preferred over a restart here.

## Root cause resolution

Look for a leak before assuming under-provisioning: if memory climbs
monotonically between restarts and never plateaus, it is a leak and
scaling out only buys time. Capture a heap dump before the next
restart, or the evidence is gone.

## Do not

Do not raise the memory limit repeatedly without capturing a dump. Three
consecutive limit raises without a dump is how a leak survives a quarter.
