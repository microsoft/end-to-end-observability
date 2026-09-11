# PM-2026-07-19: Customer portal 5xx spike after release 2026.07.19.2

id: PM-2026-07-19
symptom_class: error_rate
service: rg-app-portal
severity: critical
duration_minutes: 41
customer_impact: ~14,000 failed requests
related_runbooks: RB-008

## Summary

Release 2026.07.19.2 shipped a connection-string change that pointed the
portal at a read replica for writes. Write operations returned HTTP 500.
Approximately 14,000 requests failed over 41 minutes before rollback.

## Timeline

- **13:04** — release deploys
- **13:06** — 5xx rate begins climbing
- **13:11** — Splunk alert fires
- **13:19** — on-call engineer begins investigating, starts with
  application logs rather than the deployment record
- **13:33** — deployment identified as the trigger
- **13:45** — rollback complete
- **13:47** — error rate normal

## What went wrong

A configuration transform substituted the replica endpoint into the
write connection string. The transform was correct for the staging
topology and wrong for production; production is the only environment
with a read replica.

## What made it take 41 minutes rather than 15

The investigation started with application logs. The deployment record
was checked fourteen minutes in. **Checking what changed first is worth
more than any amount of log analysis**, and this is the second
postmortem in three months to make that observation.

## Actions taken

1. Config transforms now validated against a production topology
   fixture in CI. **Done.**
2. Deployment annotations surfaced directly on the 5xx dashboard.
   **Done.**
3. Investigation should begin from the change record automatically.
   **Open.**
