# PM-2026-08-22: Missed disk-capacity incident during alert storm

id: PM-2026-08-22
symptom_class: capacity
service: rg-data-01
severity: high
duration_minutes: 220
customer_impact: delayed batch processing
related_runbooks: RB-003, RB-052

## Summary

A genuine disk-capacity alert on `rg-data-01` went unacknowledged for
3 hours 40 minutes. It arrived during a period in which the on-call
engineer received 71 alerts in two hours, 64 of them from
`Network In - AMBA`, all of which self-resolved.

## What went wrong

Nothing about the detection failed. The alert fired correctly, at the
correct severity, to the correct rotation. It was not seen because it
was indistinguishable from noise.

## The numbers

Over the preceding 30 days:

- `Network In - AMBA` fired 140 times; **93% self-resolved** with no
  action taken
- `Disk Read Bytes - AMBA` fired 47 times; **97% self-resolved**
- `Available Memory Bytes - AMBA` fired 58 times; **88% self-resolved**

Three rules produced 245 firings and, on the evidence, almost no
action. They are the reason a real alert was missed.

## Actions taken

1. `Network In - AMBA` threshold raised and a 15-minute duration
   condition added. **Done** — firings dropped by roughly 80%.
2. `Disk Read Bytes - AMBA` downgraded to dashboard-only. **Done.**
3. Standing quarterly alert-hygiene review. **Open** — has not yet run.

## Lesson

Alert fatigue is not a soft problem or a cultural one. It has a
measurable signature — high volume combined with a high self-resolve
rate — and it caused a real 220-minute incident here. The signature is
computable from alert history alone, without a live incident and
without touching production.
