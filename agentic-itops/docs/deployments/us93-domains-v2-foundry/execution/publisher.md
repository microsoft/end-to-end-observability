# Story 93: bounded publisher delivery

This slice implements local publisher delivery contracts, durable SQL-ledger
leases/receipts, and removal reconciliation. Story 93 ("Deploy Foundry agents")
is Active and has no acceptance-criteria field; the explicitly requested
publisher requirements are this slice's contract, not a claim of story completion.

The separate USD250/month services allowance is acknowledged, not consumed or
merged into an inference budget. No Azure writes, model calls, deployments,
role grants, ADO comments, or commits are performed by this slice.

## Implemented boundary

- Only `publish-approved-revision` and `exclude-publication` are claimable.
  `invalidate-workflows` belongs to the consumer and stays pending. Unknown
  kinds also stay pending and remain visible through `pending_outbox()`.
- Independent human publication approval and review eligibility are unchanged.
  An outbox payload, a model response, a Search result, or possession of a lease
  cannot create an approval through the publisher API.
- A canonical, nonzero, lowercase UUID identifies the host worker instance.
  Positive, exact-integer fencing tokens advance on every claim of an outbox
  job. Leases are 1..3600 seconds; renewals and final mutations check the fence.
  SQL Server uses `SYSUTCDATETIME()`, including the final guarded update.
- One active lease per publication serializes publish/delete jobs across
  repository instances. SQL Server retains the review ledger's serializable
  transaction/application-lock convention. No transaction spans Search I/O.
- Each publication/revision has one immutable key:
  `pub_` plus SHA-256 of the canonical `[publication_id, analysis_revision]`.
  Retry uses the same full document and operation ID; a different revision has
  a different key. Delete never needs to reconstruct an oversized document.
- The document is a whitelist of ledger-owned revision/draft/source/evidence
  fields, limited to 1,000,000 UTF-8 bytes. Synthetic markers remain
  `IsSynthetic=true`, `OperationalUse=prohibited`, `execution_allowed=false`.
  No embeddings, endpoint selection, credentials, or authority are model supplied.
- Upload/delete acknowledgement requires one typed, matching per-document
  successful result, a matching operation ID/type, and a bounded request ID.
  Upload accepts status 200/201; delete accepts 200 (including confirmed
  idempotent deletion of a nonexistent key). HTTP 202, generic request success,
  missing results, mixed batches, duplicates, and failed items are not success.

## SQL authority versus physical Search state

The publisher checks current review/lineage/expiry eligibility before sending
an upload **and again inside accepted completion**. A revoke, evidence
invalidation, superseding revision, or expiry during upload rejects acceptance
and durably queues deletion. Revocation remains irreversible for that
publication; corrected knowledge needs a separately reviewed new revision.

`publication_for_retrieval(publication_id, document_id, content_hash)` is the
new **per-hit SQL authority gate**. It checks current eligibility, exact immutable
identity/content, and an accepted publisher delivery, then returns ledger
content, not Search-supplied text. The calling retrieval layer must also enforce
its server-controlled producer/run entitlements. The result is a point-in-time
check, not a reusable authorization token; consumers must recheck at use and
retain existing dependency invalidation. Never cache an approval indefinitely.

`get_publication().searchable` now indicates an eligible, accepted SQL
projection. It is **not** a probe of physical index visibility, an authorization
for a different tenant/run, or a substitute for the per-hit gate.
Search remains an eventually consistent projection, not the authority.
This slice does not bind the gate to existing consumer/host retrieval paths.
Serving the new projection must remain disabled until that binding is complete.

An expired lease cannot fence an already-dispatched Search request remotely.
In particular, a timed-out upload can finish **after** a later delete. SQL
acceptance is still denied for revoked knowledge, but physical absence cannot
be promised during that interval:

1. Before I/O, an `inflight` attempt is committed. A failed/ambiguous response
   records `uncertain` and leaves the job `failed`, available for retry.
   Reclaim preserves uncertainty for older in-flight attempts.
2. Removal tracks a publication generation and unresolved upload attempts.
   A validated delete may complete its own outbox operation as `processed`
   while its receipt is `removal-pending`, with a separate durable deletion
   job still pending. This is not a claim of completed reconciliation.
3. A late validated upload outcome can be recorded through
   `observe_late_publisher`; it never acknowledges the old job or changes human
   eligibility. It advances the generation and expedites a fresh delete for
   an ineligible publication. A delete started before that generation change
   cannot confirm removal.
4. Unknown/crashed calls remain unresolved even after a successful retry.
   Repeated deletion is rate-limited by a persisted 60-second due time, so an
   unresolved call does not create a tight deletion loop. The host must drain
   other due work within its bounded dispatch cadence rather than process only
   one exclusion per minute. The maintenance sweep rotates bounded batches fairly.
   A subsequently observed late upload makes pending reconciliation due now.

`removal_state=confirmed` means a validated delete completed at the current
generation with no unresolved upload attempts known to the ledger. It is not
an independent Search read-after-write/replica-propagation attestation.
Permanently unknowable calls conservatively leave `removal_state=pending`.
There is intentionally no "assume dead", automatic-success, or operator
clear-uncertainty shortcut. A future live recovery procedure must establish
writer quiescence and repeat/verify deletion before claiming physical cleanup.

## Host-binding APIs

Import from `agentic_itops.review.publisher`:

| API | Contract |
| --- | --- |
| `PublisherDispatcher(repository, delivery, owner=..., lease_seconds=120)` | Explicit injected dependencies; no default adapter or credentials. |
| `dispatcher.run_once()` | One bounded attempt; returns receipt, cancellation, or `None` when no due job; raises on unconfirmed delivery/invalid receipts/lost lease. |
| `SearchDelivery.upload(document, *, operation_id)` | Full idempotent upsert of `SearchDocument`, not merge; returns `SearchDeliveryReceipt`. |
| `SearchDelivery.delete(document_id, *, operation_id)` | Idempotent key deletion; returns actual per-item outcomes, not an enqueue receipt. |
| `PublisherLease(outbox_id, owner, fencing_token)` | Only fencing coordinates; publication/kind/content are read from SQL, not supplied by the worker. |
| `SearchDeliveryReceipt(operation_id, operation, request_id, results)` | `results` is a one-element tuple of `IndexingResult(document_id, succeeded, status_code, error_message=None)`. |

The future adapter must map the `SearchDocument` envelope into its explicitly
configured index schema: persist `document_id` as the key and
`publication_id`, `analysis_revision`, and `content_hash` as retrievable
metadata, alongside the immutable `content_json` projection. The SQL hit gate
uses that metadata and returns its own ledger content; it does not trust
Search text. The adapter must not choose its target index from document text.

Both review repositories additionally provide:

| Repository API | Host responsibility |
| --- | --- |
| `claim_publisher(owner, lease_seconds=120)` | Internal trusted-worker API; never expose as an end-user/model tool. |
| `renew_publisher(lease, lease_seconds=120)` | Renew before expiry when using the split prepare/deliver/ack lifecycle. |
| `prepare_publisher(lease)` | Commit external intent and return a `DeliveryRequest`; `None` means ineligible publication was cancelled with removal queued. Call once per claim. |
| `ack_publisher(lease, receipt)` | Validate actual delivery and recheck authority/fence; inspect returned `accepted`, `rejected`, `removed`, or `removal-pending` status. |
| `fail_publisher(lease, code, message)` | Persist bounded failure details; an external-intent attempt is uncertain, not successful. |
| `observe_late_publisher(lease, receipt)` | Record the exact old attempt's validated outcome; never substitute this for acknowledgement. |
| `publisher_attempts(outbox_id, after_token=0, limit=100)` | Durable receipt inspection, paginated by fencing token (limit 1..1000). Inspect this after response loss; do not manufacture a new success receipt. |
| `reconcile_publisher(limit=100)` | Recurring bounded expiry/removal sweep (1..1000), not consumer workflow processing. |
| `publication_for_retrieval(publication_id, document_id, content_hash)` | Gate each Search hit and return authoritative ledger content. |

The dispatcher does not start a scheduler or background renewal thread. The
host must enforce finite adapter network timeouts below its lease, or use the
split APIs with renewal. It must apply failure backoff, run recurring
reconciliation/expiry maintenance, and alert on failed/uncertain attempts and
unknown kinds. Exceptions are propagated after durable failure recording;
provider exception text is not copied into SQL receipts. A storage failure
also propagates; the already-committed in-flight intent remains recoverable.

## Migration and release gates

`agentic_itops\review\migrations\002_publisher.sql` is an explicit additive,
one-time migration **after 001**, run with the migration identity while services
are stopped. It adds publisher state, job leases, and attempt receipts,
backfills existing publisher outbox IDs, and rejects orphan publication
references. It changes no existing approval payloads or consumer jobs. It is
not auto-executed by the SQL Server repository. SQLite performs equivalent
local bootstrap/backfill for offline testing only.

**The direct-table repository alone is not a SQL authorization boundary.**
Deployment-owned `deployment\sql\001_runtime_boundaries.sql` and
`deployment\sql-grants.json` now provide revocation-only publisher restrictions
and the runtime grant inventory. The old runtime-grant comment in migration 001
is not the deployment grant plan. Live SQL verification remains required:
prove the effective identity, column grants, enabled immutable/boundary triggers,
and rejection of approval/reactivation and consumer-job completion.
The deployment insertion trigger restricts publisher-created outbox rows to
exclusion/invalidation kinds with initial `pending` status. Publisher updates
are restricted to publish/exclusion kinds and supported processing statuses.
These restrictions still require positive/negative live SQL verification.
No runtime grants are made or certified here. Retain separate
web/reviewer/publisher identities and least-privilege Search access.
Never grant an inference identity publication-writer or human-reviewer powers.

The real Search adapter/schema, embedding budget integration, SQL-guarded
retrieval, and authenticated HTTP publisher host are implemented in the follow-on
[Search service slice](search-service.md). Remaining gates are live SQL Server
migration/driver/clock/locking/concurrency and authorization verification; live
Search per-item receipts and deletion/visibility behavior; approved embedding
finance settings; effective Entra/managed-identity permissions; and operational
invocation/recovery and end-to-end dependency invalidation. No live SQL, Search,
Azure identity, embedding, or deployment proof is provided by the local tests.

From `agentic-itops`, run the single combined local runner:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_publisher.py tests\test_review.py tests\test_review_identity_regressions.py -q -o addopts=
```

The publisher tests explicitly exercise SQLite restarts/concurrency, expired
and stolen leases, final-clock fencing, ambiguous external completion, malformed
and mixed receipts, eligibility loss before/during upload, deletion failure and
success, late upload after deletion, reconciliation, payload limits, independent
approval, and untouched consumer/unknown outbox kinds. SQL migration/clock
checks are static/recording-cursor checks, not SQL Server integration.
