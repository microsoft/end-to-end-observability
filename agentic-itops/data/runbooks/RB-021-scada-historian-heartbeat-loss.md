# RB-021: SCADA historian heartbeat loss

id: RB-021
symptom_class: availability
service: rg-refinery-midstream
severity: critical
owner: ot-engineering@contoso.com
last_reviewed: 2026-08-19

## When this applies

`vm-scada-01` stops emitting heartbeat for more than 10 minutes.
Surfaces as `Heartbeat missing - SCADA historian`.

## Critical context before you do anything

`vm-scada-01` sits inside the **OT zone**. It is classified
safety-critical. Downstream of it are `vm-pump-ctrl-07` and
`vm-pump-ctrl-08`, which buffer and then degrade when the historian is
unavailable.

**No automated system has change authority on this host.** That includes
agents, runbook automation, and scheduled jobs. This is not a
configuration choice that can be waived during an incident — it is the
OT/IT boundary agreed with plant safety, and it is enforced in policy by
the Change-Safety Gate.

## Immediate mitigation

The documented fix is to restart the historian collector service:

    restart

**This must be performed by a named OT engineer**, through the OT change
process, with the control room notified in advance. An agent may
recommend this action and prepare the change record. An agent may not
perform it, and the gate will deny the attempt.

Expect the gate to return DENY with rule `ot-boundary` if this action is
proposed by an agent. That denial is correct behaviour, not a defect.

## What an agent should do instead

1. Confirm the scope — is this the historian only, or the whole OT
   network segment? Query `plc-network-segment-a` reachability.
2. Assess downstream impact on `vm-pump-ctrl-07` and `-08`. Buffered
   controllers typically show rising CPU within 15 minutes (see RB-014).
3. Draft the OT change record with evidence attached.
4. Page `ot-engineering@contoso.com` and the control room.
5. Stop. Wait for a human.

## Escalation

Control room first, then OT engineering on-call. If the historian has
been down more than 30 minutes, plant operations must be informed
regardless of whether pumps are still holding setpoint.
