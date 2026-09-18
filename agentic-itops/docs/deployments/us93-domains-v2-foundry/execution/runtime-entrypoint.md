# Consumer runtime entry points

Status: executable local source and production configuration factory, not a
deployed service. The later [foundation stage](foundation.md) provisions the
underlying resources and models; it does not deploy this application or grant
its managed identities access.

## Review application

`agentic_itops.hosting.create_review_app()` constructs the production Flask app
with explicit managed-identity SQL connections, the SQL review repository,
consumer queue adapter and Entra signature verification. It never substitutes
SQLite, a test authenticator, developer credentials or anonymous access.
Construction does not create database schemas or grant permissions.

On the Linux web host, install `requirements-web.txt` from `agentic-itops`.
The intended production startup command is:

```text
gunicorn --bind 0.0.0.0:8000 --workers 1 --threads 4 'agentic_itops.hosting:create_review_app()'
```

Gunicorn is Linux-host tooling, not the Windows test server. Build packaging,
App Service configuration and deployment still require completion.

| Required environment value | Meaning |
|---|---|
| `ITOPS_SQL_SERVER` | Exact commercial Azure SQL hostname ending in `.database.windows.net` |
| `ITOPS_SQL_DATABASE` | Explicit application database |
| `AZURE_CLIENT_ID` | This host's user-assigned managed-identity client UUID |
| `ENTRA_TENANT_ID` | Exact accepted tenant |
| `ENTRA_AUDIENCE` | Exact application audience; token issuer must be v2 |
| `ITOPS_PUBLIC_ORIGIN` | HTTPS public origin, without a path |
| `ITOPS_REVIEW_SECRET_KEY` | Protected Flask signing secret, at least 32 characters |
| `ITOPS_ALLOW_EASYAUTH` | Explicit `true` or `false`; EasyAuth tokens are still cryptographically verified |
| `ITOPS_REVIEW_SCOPES_JSON` | Server-owned source catalog and actor entitlements |

Secrets must come from the approved deployment secret mechanism. Missing values,
unexpanded `${...}` variables and unresolved Key Vault references fail startup.
Do not commit tenant-bound settings. `.azure` state other than the deployment
plan, `.env` variants and `.consumer-runtime.json` are ignored.

The scope JSON has exactly two arrays:

- `sources`: `{producer_key, run_id, domain, surfaces}` records.
- `entitlements`: `{oid, producer_key, run_id}` records referencing catalog entries.

Actor UUIDs are canonicalized; opaque producer/run identities are not trimmed
or case-folded. Missing entitlements grant nothing. Configuration is copied at
startup, so changes require a controlled restart/redeployment.

The factory loads the installed native SQL library before serving. The Windows
probe loaded `msodbcsql18` version `18.6.2.1`; that does not prove Linux library
availability, SQL connectivity or database authorization. The local Docker Linux
daemon was not running. The health endpoint is liveness, not database readiness.

An independently authorized migration identity must apply the consumer, review
and runtime-budget schemas. Existing consumer queues require migration
`002_job_scopes.sql` and explicit `store.backfill_job_scopes()` before their old
jobs can participate in scoped claims. Do not reset or silently reclassify them.

## Portfolio and workflow

`scripts/compile_portfolio.py` validates all nine source agents and all twelve
logical tool requirements. `--require-bound` rejects incomplete declarations;
`--sdk` constructs SDK objects locally only. It does not register agents or
prove any endpoint, managed identity, model allocation or permission.

The approved trusted Search alternative has these binding fields:

```text
backend=registered_handler
handler=<reviewed backend registry key>
auth=managed_identity
snapshot_field=<explicit index field>
snapshot_id=<approved immutable snapshot>
filter=<eligibility filter>
request_schema=<actual worker-side request contract>
output_schema=<actual backend response contract>
```

It exposes only `search_approved_knowledge({})` to models, preserves
`vector_semantic_hybrid`, top 3 and snapshot filtering, and applies to every
declared Search user: 10, 04, 05 and 08. The actual backend must independently
bind producer/run scope and revalidate SQL eligibility. Native Search remains
a separate, proof-gated option; never invent its isolation/budget approvals.

`runtime.bind_runtime_agents(portfolio, versions)` requires a complete compiler
artifact, all nine exact numeric versions, and a trusted function representation
for every declared tool and delegation. `PortfolioRuntime` runs 10/02 first,
then the actual 00 agent delegates 04 -> 03 -> 05 -> deterministic gate -> 07.
08 and 09 have separate lifecycle triggers. No-action is not an annotation
fallback; a real proposal receives a policy/evidence/proposal-bound denial for
synthetic input. Source retention and retrieved-knowledge eligibility guards
remain mandatory runtime inputs.

The consumer worker now fences ingestion commits inside SQL, partitions claims
by source scope and persists escaped diagnostic failures. Model/tool calls use
durable conservative reservations. None of these mechanisms is an Azure-wide
spending cap or an exactly-once billing guarantee.

## Remaining release gates

The [registration handoff](registration.md) now checks matching project/source/
plan receipts before binding exact versions. The [publisher ledger](publisher.md)
now implements durable claim/renew/fence/ack/failure and reconciliation through
an injected delivery interface. Both are local implementation, not proof of a
live endpoint or deployed identity.

Real authenticated OpenAPI/MCP/Search implementations; authoritative
result/revision/lifecycle integration; actual SQL Server publisher semantics,
Search delivery and end-to-end revocation/erasure; production detector-policy approval;
service IaC, networking and secret/identity provisioning; service-stage
cost/permission approval; application deployment and live end-to-end acceptance
are still required. Foundation resource provisioning does not satisfy these gates.

The retained producer probe verifies the actual observation contract under the
operator identity. It is not consumer-MI isolation, detector accuracy, Foundry
reasoning quality or deployment acceptance.
