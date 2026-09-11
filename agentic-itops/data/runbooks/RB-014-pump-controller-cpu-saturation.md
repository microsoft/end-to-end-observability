# RB-014: CPU saturation on midstream pump controllers

id: RB-014
symptom_class: saturation
service: rg-refinery-midstream
severity: high
owner: midstream-ops@contoso.com
last_reviewed: 2026-07-02

## When this applies

Sustained CPU above 85% on any `vm-pump-ctrl-*` host in the midstream
control group, for more than five minutes. Typically surfaces as
`Percentage CPU - AMBA` (Azure Monitor), `system.cpu.user` (Datadog) or
`HighCpuUsage` (Prometheus) — frequently all three at once, because all
three monitor these hosts.

## Why it happens

In order of observed frequency:

1. **Polling extension regression.** The `CustomScriptExtension` that
   drives Modbus polling has shipped two regressions that raised poll
   frequency. Both times CPU stepped up within ten minutes of the
   extension write. *This is the first thing to check.*
2. **Downstream historian backpressure.** When `vm-scada-01` stops
   accepting writes, the controller buffers in memory and spins.
3. **Genuine load increase.** New sensor groups added to the poll list
   without resizing the host.

## Immediate mitigation

Restart the polling service to shed the buffer and return the host to a
serving state:

    restart

This is a **mitigation, not a fix**. It buys roughly 40 minutes before
the condition returns if the underlying cause is 1 or 2.

Blast radius: one controller. Pumps continue on their last setpoint
during the restart — they do not stop. Setpoint changes are unavailable
for the ~30 seconds the service takes to come back.

## Root cause resolution

- **If cause 1** — roll the extension back to the previous version.
  Check Activity Log for `extensions/write` in the preceding 30 minutes
  and compare the version stamp.
- **If cause 2** — resolve the historian first. Restarting the
  controller repeatedly while the historian is down makes things worse.
  See RB-021.
- **If cause 3** — resize the host. Do not keep restarting.

## Rollback

The restart is not reversible and does not need to be. If the service
does not return within 90 seconds, escalate to midstream-ops and do not
attempt a second restart.

## Do not

- Do not restart the **host**. Restart the service only.
- Do not touch `vm-scada-01` as part of this runbook. It is in the OT
  zone and has a separate change process. See RB-021.
