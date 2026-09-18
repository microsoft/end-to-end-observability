# Story 93: real Search, embedding, retrieval, and publisher Function

This implements the adapter/host code previously left gated in `publisher.md`.
It does **not** assert live Azure, model, native Foundry Search, identity, or SQL
permission proof. The implementation task performs no Azure writes, model
calls, deployments, grants, ADO comments, or commits. The parent owns approval
and execution of all live commands below.

## Concrete implementation

`agentic_itops.review.search_delivery` uses the actual
`azure-search-documents==12.0.0` and `openai==3.8.0` SDKs. The isolated
`requirements-search.txt` includes the existing consumer/Foundry pins and pins
Search, azure-core 1.41.0, azure-functions 2.3.0, and httpx2 2.12.0. The missing
Search SDK was first demonstrated by a failing test before installation.
Shared consumer/Foundry dependency files are unchanged.
`requirements-publisher.txt` is the publisher-specific assembly entry point and
includes that pinned Search/host profile. The canonical `deployment.package`
tool recursively flattens these includes; the worker can continue to include
`requirements-search.txt`.

| Surface | Implemented behavior |
| --- | --- |
| `ManagedIdentityEmbedding(settings, finance_gate)` | Explicit user-assigned `ManagedIdentityCredential`; account Azure OpenAI API, deployment/model/version/dimension pins; actual usage settlement. |
| `AzureSearchDelivery(settings, embedding, review_repository)` | One full SDK upload/delete; actual per-item result validation; SDK document readback before receipt return. |
| `ApprovedKnowledgeRetriever(settings, embedding, review_repository)` | Real top-three vector + semantic + lexical query, exact server-owned source/snapshot filters, independent SQL gate for each hit. |
| `BudgetLedgerEmbeddingGate(ledger, workflow_id=None)` | Existing durable BudgetLedger reservation and settlement; explicit logical workflow IDs are deterministically namespaced by embedding scope, avoiding the chat workflow's global SQL primary key. No finance default or free retry after unknown completion. |
| `services\publisher\function_app.py` | HTTP-only `POST /api/publisher/run-once`, signed app-only caller allowlist, one bounded maintenance batch and at most one publisher dispatch. No timers/polling. |
| `scripts\setup_search_index.py` | Offline schema by default. Explicitly approved live bootstrap uses SearchIndexClient create/readback, never silently replaces a differing index. |
| `deployment.package --service publisher` | Canonical deterministic source-only Function ZIP/staging with flattened dependency profiles and explicit assets; no Windows native binaries, tests, `.env`, or live captures. |

Production constructors select the configured UAMI directly, never
DefaultAzureCredential or AzureCliCredential. Ambient OpenAI keys/endpoints/API
versions cannot replace the explicit token provider and target. Provider retries
and redirects are disabled; normal SDK calls have a 20-second timeout and MI
transport timeouts are five seconds. A lost/invalid outcome propagates and the
existing publisher ledger retains uncertainty.

Embedding input is the **exact approved draft text**, without model-generated
summarization, truncation, or invented chunks. It must fit 8,000 UTF-8 bytes.
Queries must fit 4,096 bytes. Byte count plus one is a conservative token bound
for admission; actual prompt/total usage is validated before settlement.
Oversized drafts fail explicitly and require a separately reviewed shorter
draft/revision or a separately designed chunking policy. The adapter checks
1536 finite, nonzero-vector numeric values and the returned model name.
OpenAI usage/model/vector values are checked on the raw JSON before SDK type
coercion. No fake success/usage/embedding is produced.

## Environment-bound services and fixed model/schema pins

| Setting | Required value |
| --- | --- |
| `ITOPS_SEARCH_ENDPOINT` | Required parent-approved `https://<search-service>.search.windows.net` environment binding. |
| `ITOPS_SEARCH_INDEX` | `agentic-itops-knowledge` |
| `ITOPS_OPENAI_ENDPOINT` | Required parent-approved `https://<openai-account>.openai.azure.com/` environment binding. |
| `ITOPS_EMBEDDING_DEPLOYMENT` | `text-embedding-3-small` |
| `ITOPS_EMBEDDING_MODEL` | `text-embedding-3-small` |
| `ITOPS_EMBEDDING_MODEL_VERSION` | `1` |
| `ITOPS_EMBEDDING_DIMENSIONS` | `1536` |
| `AZURE_CLIENT_ID` | The **current service's** explicitly selected UAMI client ID, not its principal/object ID. |
| `ITOPS_SEARCH_SNAPSHOT_ID` | Parent-approved snapshot ID, identical to the compiled registered-handler binding. |
| `ITOPS_SEARCH_SCOPES_JSON` | Nonempty unique array of exact `{"producer_key":"...","run_id":"..."}` entries from trusted source configuration. |

There are no tenant-named endpoint constants or defaults in production code.
Both endpoints are required settings. Validation accepts only commercial Azure
HTTPS service/account root hosts: no credentials, explicit ports, queries,
fragments, project routes, nested paths, or foreign suffixes. Root trailing
slashes are canonicalized. Parent-owned configuration/RBAC selects the actual
resource; SQL's immutable projection binding prevents later retargeting of a
previously bound publication. Model, index, API-version and schema pins remain.

The embedding URL is the configured account route:
`/openai/deployments/text-embedding-3-small/embeddings?api-version=2024-10-21`.
The `services.ai.../api/projects/...` project endpoint is explicitly rejected.
Search uses API `2025-09-01`. Parent must verify the deployed account model
version through ARM before enabling the service: the embedding response exposes
the model name/dimensions, **not proof of the ARM deployment version**.

The real source-of-truth schema is `INDEX_SCHEMA` in `search_delivery.py`;
`services\publisher\knowledge-index.schema.json` is its generated REST JSON.
Current canonical schema SHA-256:
`85cd16aa7ae1d457a8d706bb6ca8677cb0ee6b4e5d3970497779834bc625d866`.

The schema includes publication/incident/revision identity, immutable content
and projection hashes, producer/run/snapshot fields, eligibility expiry,
synthetic markers, approved/revoked projection flags, draft text, canonical
whitelisted content, embedding model/version/request provenance, and a
1536-dimensional HNSW cosine vector. Semantic configuration is
`knowledge-semantic`. No raw audit, control, evidence payload, or producer
telemetry rows are indexed; evidence references contain IDs/hashes/retention.

## Runtime application binding contract

Use the registered managed-identity handler, **not a native Foundry Search
tool**. Import `SEARCH_REQUEST_SCHEMA` and `SEARCH_OUTPUT_SCHEMA` into the
existing compiler binding; they are tested with `compile_portfolio`.

```python
from agentic_itops.review.search_delivery import (
    SearchServiceSettings, ManagedIdentityEmbedding, BudgetLedgerEmbeddingGate,
    ApprovedKnowledgeRetriever, SEARCH_REQUEST_SCHEMA, SEARCH_OUTPUT_SCHEMA,
)

settings = SearchServiceSettings.from_environment(environment)
embedding = ManagedIdentityEmbedding(
    settings, BudgetLedgerEmbeddingGate(
        approved_embedding_ledger, workflow_id=workflow_id,
        parent_remaining_seconds=lambda: parent_chat_ledger.remaining_seconds(workflow_id),
    ),
)
retriever = ApprovedKnowledgeRetriever(settings, embedding, review_repository)
# Bind the registered handler to retriever.handle. The model-facing tool still
# accepts {} only; the trusted worker assembles this backend request.
artifact = retriever.handle({
    "query": trusted_query,
    "producer_key": trusted_producer_key,
    "run_id": trusted_run_id,
    "snapshot_id": settings.snapshot_id,
    # Optional "analysis_revision": positive integer, for an exact revision query.
})
```

Equivalent method:
`retrieve(query, *, producer_key, run_id, snapshot_id, analysis_revision=None)`.
An explicit `workflow_id` passed to `BudgetLedgerEmbeddingGate` is the host's
logical workflow ID; the gate derives a separate `embedding-<hash>` ledger ID
from that ID and the embedding budget scope. This prevents a different embedding
pricing policy from colliding with the chat workflow row. Repeated calls in that
logical embedding workflow share its bounds; policy changes cannot reset it.
With `workflow_id=None`, each provider attempt gets its own bounded workflow
while still sharing the approved monthly scope cap.
For worker queries, pass `parent_remaining_seconds` as a trusted callback to the
parent chat ledger's remaining time, not merely the same duration value. The
gate checks it before and after reservation and caps the returned timeout. The
embedding adapter also deducts identity-token acquisition time and refuses to
start the model request after its reservation deadline. Publisher-only calls
without a parent workflow retain their independent bounded service policy.

The query always requests `top=3`, a vector query with `k=50`, semantic ranking,
`preFilter`, coverage 100, and semantic error handling `fail`. It adds exact
snapshot, producer, run, schema, approval, revocation, safety and expiry filters;
an optional revision adds equality. The returned revision of **every** hit is
also independently compared to SQL. Opaque run IDs retain case, spaces, and
escaped apostrophes. The retriever will not follow result continuation pages.

Artifact fields: `status="complete"`, `snapshot_id`,
`query_type="vector_semantic_hybrid"`, `index_name`, actual Search and embedding
request IDs, `documents`, `rejected_documents`, and the synthetic overlay.
Each document contains `doc_id`, string `revision`, `publication_id`,
`analysis_revision`, `content_hash`, `sha256=content_hash`, `projection_hash`,
`snapshot_id`, `title`, `text`, full `source`, `evidence_refs`, `eligible_until`,
and `retained_until_utc=eligible_until`. These support existing runtime knowledge
citations, revision comparison, evidence hashing, and dependency invalidation.

Bind the runtime knowledge guard at every use:

```python
retriever.guard_documents(
    list(context.knowledge.values()),
    producer_key=trusted_producer_key,
    run_id=trusted_run_id,
    snapshot_id=settings.snapshot_id,
)
```

This guard performs SQL revalidation only, not another Search/model call. It
rejects changed runtime content, expired/revoked/corrected publications, scope
changes and snapshot mismatch. The caller must retain normal source-catalog
and workflow entitlement checks.

Known SQL-ineligible hits are explicitly omitted with
`rejected_documents=[{"doc_id":"...","reason":"sql-ineligible"}]`; they are never
citable documents. A genuinely empty, successful query is allowed. Schema
errors, wrong-scope hits, bad provenance, semantic partial responses, missing
reranker evidence, pagination, provider errors, SQL failures and unconfirmed
delivery raise errors rather than becoming empty successful searches.

## Durable projection and physical delivery guarantees

Apply additive migration `003_search_projection.sql` **after 001 and 002**.
It adds `review.publisher_projections` with an immutable snapshot/destination/
schema/model/source binding per publication. The dispatcher writes this binding
before any adapter embedding/upload. Retry cannot move an existing publication
to another snapshot, index, model pin, or producer/run. This is per-publication
immutability, not a claim that a mutable approved snapshot membership is a
sealed native Search dataset.

`publication_for_retrieval` preserves its old three-argument API and adds:
`snapshot_id`, `producer_key`, `run_id`, `analysis_revision`, `projection_hash`
keyword arguments. The real retriever supplies **all** of them. SQL independently
checks the immutable binding, current eligible revision, approval/review state,
exact content/key, expiry and lineage, then returns its own content instead of
Search-supplied text.

An upload must return exactly one successful matching actual SDK item and then
read back the full matching projection/vector. A delete must return a successful
item **and** receive document GET 404; 403/500 or a still-present document is not
confirmed deletion. Eventual visibility failures stay uncertain/failed and are
retried only by the existing durable publisher protocol, not SDK retries.
Revocation during embedding stops the upload preflight. Revocation during Search
upload is rejected by the SQL completion fence and queues real deletion.

The prior physical-vs-eligibility limitation remains: an unknown old remote
upload can finish after a delete. SQL rejects ineligible retrieval; unresolved
attempts keep removal reconciliation pending. A successful immediate readback
is not a global replica propagation, cancellation, or writer-quiescence proof.

## Publisher Function configuration and authentication

In addition to Search settings, the Function requires:

| Setting | Meaning |
| --- | --- |
| `ITOPS_PUBLISHER_ENABLED` | Must be exactly `true` after parent approval; otherwise no delivery. |
| `ITOPS_SQL_SERVER`, `ITOPS_SQL_DATABASE` | Existing private Azure SQL target; no connection string/password fallback. |
| `ENTRA_TENANT_ID` | Exact canonical tenant UUID for signature/issuer validation. |
| `ITOPS_PUBLISHER_AUDIENCE` | Exact **services API application/client-ID GUID** for v2 access tokens. The `api://<appId>` Application ID URI is the scope prefix, not the v2 `aud` claim. |
| `ITOPS_PUBLISHER_CALLER_OIDS_JSON` | Unique allowlist of service principal/UAMI **object IDs**, not client IDs; maximum 20. |
| `ITOPS_EMBEDDING_BUDGET_SCOPE` | Approved durable embedding budget scope; not the services hosting allowance. |
| `ITOPS_EMBEDDING_BUDGET_JSON` | Explicit existing `BudgetLimits` fields with current approved pricing/reference/cap; context at least 8001 and `max_output_tokens=1`. Actual embedding output usage is zero. |

The budget profile must use approved embedding pricing and cap; no prices or
funding approval are invented here. The separate USD250/month service allowance
does not implicitly fund unbounded embedding/semantic calls. Unknown embedding
completion retains its full financial reservation.

The HTTP trigger is platform `ANONYMOUS` **because application code verifies the
signed bearer token itself**. Function keys and `X-MS-CLIENT-PRINCIPAL` headers
grant nothing. Accepted tokens must be RS256, exact tenant/v2 issuer/audience,
unexpired, have `idtyp=app`, no delegated `scp`, an allowlisted `oid`, and the
signed application role **`Publisher.Invoke`**. `Worker.Invoke`, `Tools.Invoke`,
human reviewer roles, or unsigned platform role headers do not authorize this
endpoint. Role and caller allowlist checks both precede maintenance/dispatch.
Configure the API registration for v2 access tokens and the `idtyp` access-token
optional claim where needed. Human Azure CLI user tokens are intentionally
rejected by this app-only endpoint.

The proposed shared services registration is `agentic-itops-us93-services`,
with application roles `Worker.Invoke`, `Tools.Invoke`, and `Publisher.Invoke`.
The publisher caller allowlist contains the review UAMI and the isolated
bootstrap UAMI, each separately assigned `Publisher.Invoke`; the worker UAMI's
`Tools.Invoke` assignment does not authorize publishing. The browser review
registration remains distinct (`agentic-itops-us93-review`). App IDs stay
deployment parameters. No Microsoft Graph permission scopes, role assignments,
registrations, secrets, or monthly-budget changes are made by this code update.
Entra write 403 is an administrator-assistance blocker, never a bypass trigger.

[Microsoft's access-token claims reference](https://learn.microsoft.com/en-us/entra/identity-platform/access-token-claims-reference)
specifies that v2 `aud` is the API's client-ID GUID. Request the token using
`api://<services-appId>/.default`, and configure validators for that GUID.
Do not broaden audience acceptance to compensate for mismatched configuration.

Only POST with `{}` is accepted. The body cannot choose a publication, scope,
identity, model, index, job count, grant, or approval. Each invocation performs
`reconcile_publisher(limit=10)` then one `run_once()` with a 180-second lease.
The host has a three-minute timeout. No timer, queue auto-poll, or autonomous loop
is registered. Provider/configuration errors produce non-success responses and
redacted logs; durable receipts remain the diagnostic source.

## Required identities and SQL authorization hardening

| Identity | Azure permissions, subject to parent approval |
| --- | --- |
| Publisher UAMI | Cognitive Services OpenAI User on the account; Search Index Data Contributor at the owned index if supported, otherwise explicitly approved dedicated service scope; publisher SQL execution boundary and budget operations. |
| Application/reader UAMI | Search Index Data Reader; this retriever performs query embeddings, so OpenAI User on the account and approved embedding budget operations are also needed; SQL read/eligibility execution boundary. **No Search writes.** |
| Index bootstrap identity | Search Service Contributor for schema create/read; separate from publisher and inference identities. Bootstrap-only AzureCliCredential is allowed with explicit `--auth azure-cli`. |
| Caller service identity | Shared services API token with `Publisher.Invoke` **and** exact OID allowlist; no reviewer or publication approval entitlement is implied. |

**Do not treat the following direct-table requirements as safe runtime grants.**
The source repository alone is not a SQL authorization boundary. Deployment-owned
`deployment\sql\001_runtime_boundaries.sql` now implements publisher revocation-only
update restrictions and outbox authority checks; `deployment\sql-grants.json`
defines the runtime access inventory. Migration 003 supplies the immutable
projection trigger. No SQL grants are made by this task. **Live SQL verification
is required**, including effective identity/column grants, enabled triggers,
negative approval/reactivation tests, and positive publisher lifecycle tests.
The deployment trigger now requires publisher-created exclusion/invalidation
rows to start as `pending`, and limits publisher updates to publish/exclusion
kinds with `leased`, `failed`, `processed`, or `cancelled` status. The previously
reported initial-status source gap is closed; live positive/negative proof
remains required. Unrestricted direct-table updates are not an acceptable
substitute for these enforced restrictions.

Exact current publisher data access required by its code paths:

| Table | Access needed by current implementation |
| --- | --- |
| `review.incidents` | SELECT identity/current revision/source/review state/CAS; UPDATE `review_state,cas_version` only for dependent invalidation. |
| `review.revisions` | SELECT `incident_id,revision,result_hash,payload_json,eligible`; UPDATE `eligible` only for dependent invalidation. |
| `review.publications` | SELECT identity/revision/payload/status/eligibility/deadline; UPDATE `status,eligible` only to revoke. **No INSERT or payload/deadline updates.** |
| `review.dependencies` | SELECT `publication_id,incident_id,revision`. |
| `review.outbox` | SELECT all four columns; INSERT `outbox_id,kind,payload_json,status` for exclusion/consumer invalidation; UPDATE `status`. Deployment triggers restrict kind/direction, require initial `pending`, and constrain update statuses; live verification remains required. |
| `review.publisher_jobs` | SELECT all; INSERT `outbox_id,publication_id,fencing_token`; UPDATE `owner,fencing_token,expires_at,not_before`. |
| `review.publisher_state` | SELECT all; INSERT `publication_id,generation`; UPDATE `accepted_outbox_id,generation,removed_generation,reconciled_at`. |
| `review.publisher_attempts` | SELECT all; INSERT `outbox_id,fencing_token,owner,status,receipt_json`; UPDATE `status,receipt_json,started_generation`. |
| `review.publisher_projections` | SELECT and INSERT `publication_id,projection_json`; never UPDATE/DELETE. |

The review repository also executes transaction-owned
`sys.sp_getapplock` for resource `agentic_itops_review_v1`. It needs **no**
human review receipt inserts, publication approval inserts, evidence tombstone
writes, consumer work completion, schema creation, or role administration.

The retriever/guard only require SELECT on the incident/revision/publication,
publisher-state and publisher-projection fields used by the eligibility check,
plus the existing application lock. It must not receive the publisher's
invalidation/update permissions.

The embedding gate separately uses the existing `itops_runtime_*` budget tables:
workflow SELECT/INSERT and UPDATE `api_calls,tokens_accounted,stop_reason`;
month SELECT/INSERT and UPDATE `accounted_nanos`; call SELECT/INSERT and UPDATE
`state,input_tokens,output_tokens,charged_nanos`. It does not need budget schema
creation or model/provider role assignment. Direct budget-table mutation is
also not a substitute for a hardened finance execution boundary.

## Parent-only live sequence and commands

First verify the fixed OpenAI deployment model/version/capacity through ARM,
Search Basic 1x1/Entra-only settings, semantic-ranker availability, explicit
identities, private Function ingress/VNet egress to private SQL/storage, Linux
native SQL dependencies, approved financial settings, and SQL execution-boundary
grants. Apply review migrations 001, 002, 003, the existing budget migration, and
the deployment-owned authority migration through the canonical hash-ledgered
bootstrap path with the migration identity while services are stopped. Require
the immutable projection trigger and verified outbox authority restrictions
before runtime grants. The tightened authority migration changes its source
hash: rebuild and reapprove the bootstrap bundle before first application.
If that migration version was already applied, do not overwrite its ledger
entry; use a new approved migration version. No migration runs at Function startup.

From `agentic-itops`, offline schema review (safe; no Azure/model calls):

```powershell
.\.venv\Scripts\python.exe scripts\setup_search_index.py `
  --write-schema services\publisher\knowledge-index.schema.json
```

After **separate parent schema/bootstrap approval**, with the documented Search
environment configured and a bootstrap identity authorized for index creation:

```powershell
.\.venv\Scripts\python.exe scripts\setup_search_index.py --apply --auth azure-cli `
  --approval-reference US93-parent-approved-index-bootstrap `
  --expected-schema-hash 85cd16aa7ae1d457a8d706bb6ca8677cb0ee6b4e5d3970497779834bc625d866
```

This is a real Azure write command, **not executed by this implementation**.
An existing differing schema raises an error rather than deleting/replacing it.

Build the deployable source archive, then let the parent deploy using Linux
remote build (required for native dependencies; never package this Windows venv):

```powershell
.\.venv\Scripts\python.exe -m deployment.package --service publisher `
  --output .build\packages\publisher.zip --stage .build\azd\publisher
az functionapp deployment source config-zip `
  --resource-group $env:ITOPS_SERVICES_RESOURCE_GROUP `
  --name $env:ITOPS_PUBLISHER_FUNCTION_APP_NAME `
  --src .build\packages\publisher.zip --build-remote true
```

The archive places `function_app.py`/`host.json` at the root, includes the
allowlisted application source/assets, and emits flattened root requirements.
The former publisher-specific packaging tool has been removed to keep one
deployment implementation. Do **not** deploy the raw
`services\publisher` directory without the shared source/profiles. Parent must
use the deployment command appropriate for the provisioned plan, preserving
remote build and private storage access.

For a live invocation from the **allowlisted service identity's environment**,
request `api://<publisher-api-client-id>/.default` with
`ManagedIdentityCredential(client_id=<caller-uami-client-id>)`, then POST `{}` to
`https://<publisher-function-app>.azurewebsites.net/api/publisher/run-once` with
the resulting bearer token and a timeout longer than the host's bounded attempt.
Do not log the token or use a human CLI token as service-identity proof.
The parent must verify real upload, exact document readback, guarded retrieval,
revocation, delete/readback and SQL receipts before claiming live completion.

## Local validation boundary

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_search_delivery.py tests\test_publisher.py `
  tests\test_review.py tests\test_review_identity_regressions.py -q -o addopts=
```

Tests exercise genuine SDK serialization/deserialization with fake HTTP
transports, including the production RequestsTransport REST response surface,
real SQLite review/budget repositories, JWT signatures, Function dispatch,
compile-compatible schemas, finance denial/unknown settlement, target pins,
malformed/partial outcomes, source/snapshot/revision rejection, no retries,
concurrent revocation, readback and deletion, and deterministic packaging.
Live sockets are prohibited in the Search test module. None of these results
are live Azure SQL, Search, model, Entra service-principal, or deployment proof.
