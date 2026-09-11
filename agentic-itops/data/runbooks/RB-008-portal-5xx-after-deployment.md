# RB-008: Customer portal 5xx spike following a deployment

id: RB-008
symptom_class: error_rate
service: rg-app-portal
severity: high
owner: portal-team@contoso.com
last_reviewed: 2026-08-30

## When this applies

`app-customer-portal` HTTP 500 rate rises above baseline within 30
minutes of a release. Surfaces from Splunk (`Customer portal 5xx error
rate above baseline`) and Application Insights.

## First question

**What shipped?** Check deployments in the last hour before anything
else. In 7 of the last 9 occurrences the answer was the most recent
release, and the median time-to-identify when the deploy was checked
first was under four minutes.

## Immediate mitigation

If a release landed within 30 minutes of onset, roll it back. If not,
recycle the app pool to clear a suspected connection-pool exhaustion:

    recycle_app_pool

Blast radius: one site, in-flight requests are dropped. Do not recycle
more than twice — if the second recycle does not hold, the cause is
downstream (SQL or Redis), not the app.

## Root cause resolution

- **Post-deploy** — roll back, then diff the release against the last
  known good. Connection-string and feature-flag changes are the usual
  culprits.
- **No deploy** — check `sql-portal-prod` DTU and `redis-portal-prod`
  evictions before touching the app tier.

## Rollback

Redeploy the previous release tag. Portal releases are immutable and
rollback is safe at any time.
