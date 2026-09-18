"""Story 93 independent synthetic review UI and durable projection.

Exports:
  create_app(config, repository=..., work_submitter=None, entitlements=None,
             source_catalog=None, authenticator=None)
  EntraAuthenticator(tenant_id, audience, key_resolver=None, allow_easyauth=False)
  SqlServerReviewRepository(connection_factory)
  SQLiteReviewRepository(path=":memory:")  # LOCAL TEST only
  ConsumerWorkAdapter(storage, request_type, conflict_type=IntegrityConflict)

The SQL factory must return a fresh autocommit=False DB-API connection per call.
Keep the local SQLite review ledger in a DIFFERENT database file from the
consumer queue: cross-ledger enqueue uses an independent queue transaction.
Pass consumer.storage_contract.WorkRequest and IntegrityConflict to the bridge.
Entitlements are an explicit server-owned {oid: {(producer_key,run_id)}} map;
source_catalog is {(producer_key,run_id): {producer_key,run_id,domain,surfaces}}.
An absent scope grants nothing; no wildcard or actor-header authorization exists.
Producer keys and opaque run IDs are case-sensitive Python tuple/string
comparisons. No source entitlement depends on SQL Server's default collation;
SQL identity/CAS key columns separately use Latin1_General_100_BIN2.

HTTP contract:
  GET /health (liveness only), /csrf, /incidents?cursor=&limit=,
      /incidents/<id>/revisions/<rev>[/export], /jobs/<id>,
      /publications/<id>; GET / is the single server-rendered dashboard.
  POST /analysis-jobs and /hygiene-jobs:
      {source,window:{start_utc,end_utc}} -> 202 durable queued receipt.
  POST /incidents/<id>/reviews:
      {action:accept|reject|request_evidence|close,reason}.
  POST /publications:
      {incident_id,analysis_revision,draft_sha256,reason,expires_at_utc}
      -> 202 approval receipt; indexing_state=pending, searchable=false.
  POST /publications/<id>/revoke: {reason} -> durable exclusion intent.
Every POST requires Idempotency-Key; review/publication/revoke additionally
requires the exact quoted ETag from its revision/publication GET in If-Match.
Revision ETags include analysis revision, current CAS and immutable result hash.
Identical retries use the original payload/If-Match/key and return the original
historical receipt; GET again for current eligibility after correction/revocation.
EasyAuth browser POSTs need PUBLIC_ORIGIN as Origin and X-CSRF-Token from /csrf;
Bearer requests are CSRF-exempt, never role- or scope-exempt. Work roles are
Analyst/Operator; decisions require Reviewer; publication/revoke requires
KnowledgeReviewer. Export requires any declared role except Viewer alone.

Worker integration: repository.register_result(header) validates and commits
an immutable revision, source binding, current CAS and supersession invalidation.
evidence_refs are {evidence_id, sha256, retained_until_utc, verified}; the worker,
not the browser, attests verification and retention. Stage keys are the nine
AGENTS names; absent stages are not completed. Header source_hash, if supplied,
must equal contracts.digest(source); result_hash covers the entire header.
Postmortem draft: {draft_id,text,sha256=digest(text),
authorship:{kind:"ai"} OR {kind:"human",oid:...},applicability}.

Dependency hooks: invalidate_evidence(id, reason), expire_publications(),
pending_outbox(), publication_for_publish(id). Evidence tombstones deny reuse
of an unavailable evidence identity, including future revisions. Preflight is
not a publisher lease/fence. Outbox publishing/removal/workflow-pause rows are durable
INTENT ONLY. Publisher leases/fencing/ack, actual Search writes/retrieval
verification, full evidence/finding/proposal/draft/workflow graph, sensitive
erasure tombstones and edited human-draft ingestion remain integration work.
No API marks knowledge searchable or implies physical recovery/execution.

Dependencies: Flask==3.1.1, PyJWT[crypto]==2.10.1; the parent's
mssql-python==1.15.0 driver and consumer.sql_connect.managed_identity_sql_factory
(server FQDN, database, client_id) supply SQL Server DB-API connections without
requiring pyodbc. Keep constructor injection; no SQL connection is made at import.
Run review/migrations/001_review.sql with a migration identity before SQL use.
SQLite tests prove local logic, not SQL Server, EasyAuth or live Entra readiness.
"""
from .app import create_app
from .auth import EntraAuthenticator, Principal
from .repository import ReviewRepository, SQLiteReviewRepository, SqlServerReviewRepository
from .work import ConsumerWorkAdapter, WorkRequest, WorkSubmitter

__all__ = [
    "create_app", "EntraAuthenticator", "Principal", "ReviewRepository",
    "SQLiteReviewRepository", "SqlServerReviewRepository", "ConsumerWorkAdapter",
    "WorkRequest", "WorkSubmitter",
]
