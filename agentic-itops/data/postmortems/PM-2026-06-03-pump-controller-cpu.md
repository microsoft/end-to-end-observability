# PM-2026-06-03: Pump controller CPU saturation after polling extension upgrade

id: PM-2026-06-03
symptom_class: saturation
service: rg-refinery-midstream
severity: high
duration_minutes: 96
customer_impact: none
related_runbooks: RB-014

## Summary

`vm-pump-ctrl-07` saturated CPU for 96 minutes following an automated
upgrade of the Modbus polling extension from v1.10.18 to v1.10.21. The
new version reduced the poll interval from 5s to 1s without a
corresponding host resize. Setpoint changes from the control room lagged
by up to 40 seconds. No process upset occurred.

## Timeline

- **08:14** — extension upgrade lands via automated pipeline
- **08:22** — CPU steps from ~45% to ~80%
- **08:31** — Azure Monitor, Datadog and Prometheus all alert
- **08:31–09:05** — three separate engineers investigate the same
  problem, each believing they were looking at a different one
- **09:07** — control room opens a ticket about setpoint lag; this is
  the point at which the machine view and human view are joined, by
  hand
- **09:12** — polling service restarted; CPU recovers, then climbs again
- **09:38** — extension rolled back to v1.10.18
- **09:50** — resolved

## What went wrong

The upgrade was not gated on a host-capacity check. The poll interval
change was in the extension release notes but nobody read them, because
the pipeline is fully automated and nobody expected to need to.

## What made it take 96 minutes rather than 20

**Three tools alerted and nobody knew it was one problem.** Two engineers
duplicated the same investigation for 34 minutes. The control-room
ticket sat in a separate queue and was not associated with the technical
alerts until an engineer happened to notice it.

The restart at 09:12 was the right immediate action but was applied
before the cause was understood, so it masked the problem for eight
minutes and cost another cycle.

## Actions taken

1. Poll interval pinned in configuration rather than inherited from the
   extension default. **Done.**
2. Host capacity check added to the extension pipeline. **Done.**
3. Deduplicate alerts across Azure Monitor, Datadog and Prometheus
   before they reach an engineer. **Open** — this is the item that would
   have saved the most time, and it is still open.

## Lesson

"What changed?" answered at 08:31 instead of 09:38 would have closed
this in twenty minutes. The evidence was in the Activity Log the entire
time.
