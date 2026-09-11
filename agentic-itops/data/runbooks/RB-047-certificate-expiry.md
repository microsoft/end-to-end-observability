# RB-047: Certificate approaching expiry

id: RB-047
symptom_class: security
service: shared
severity: medium
owner: security-ops@contoso.com
last_reviewed: 2026-09-01

## When this applies

Any certificate within 30 days of expiry.

## Action

Certificate rotation is **not** an agent-authorized action. `rotate_secret`
is deliberately absent from the allowed action list, and Key Vault is in
the denied resource scope.

An agent should: identify every endpoint presenting the certificate,
confirm the renewal owner, open the change record, and notify. Then stop.

## Why this is not automated

A failed rotation takes down every consumer of the certificate at once,
and the failure mode is silent until traffic arrives. The blast radius
is wrong for automation and the detection lag makes it worse.
