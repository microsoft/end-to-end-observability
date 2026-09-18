# Story 93 private service infrastructure and bootstrap

Status: **service infrastructure, private worker preflight and live worker
source-isolation proof complete**; application/private initialization and live
acceptance remain incomplete.
No Azure writes, Entra changes, role assignments, deployment/model calls, commits or ADO comments were executed by this implementation agent.
The parent owns `.azure/deployment-plan.md`, Azure validation, approval and deployment.
Do not run foundation provisioning as a substitute for the additive service entrypoint.

## Parent execution checkpoint - September 16

September18 update: replacement-r1 delivery completed on worker, tools, publisher
and review, independently verified at 13:14:05Z. Bootstrap is disabled/disarmed
and temporary settings are removed. No SQL-admin window, new role, network
change or agent registration was used. Deployment IDs and the subsequent
failed single replacement are recorded in
[application integration](application-integration.md). Successful deployment
does not establish all-nine acceptance or runtime negative-DML isolation.

Worker preflight OneDeploy receipt
`c31255c4-b6fd-45eb-b107-1dd2f297de1b` completed after the explicitly approved
worker-only activation. A disabled Function resource had rejected publication
with HTTP 403; enabling only the private, bootstrap-caller-restricted target
allowed publication without extra roles or public ingress.

At 13:10:53 UTC, the separate worker-MI proof verified 10,530 observation rows
and audit filtering to zero against four independently witnessed audit rows.
This is **filtered-to-zero evidence**, not an explicit HTTP 403. The exact
worker identity, bootstrap caller and contract digest are in the preserved
`.build\bootstrap-proof-r4-journal.json` receipt. No audit labels or rows became
inference inputs. Bootstrap was stopped/disarmed; native SQL remains a separate
initialization gate.

For repeatable rollout, verify actual Always On before bootstrap execution.
The Bicep declaration alone is insufficient: replacement-r1 encountered a live
false value and stopped before uploading. Restore the approved true setting,
verify the readback and unchanged deny-all ingress/SCM/VNet/identity guards,
then continue only from durable evidence. A journal with no upload intent can
continue as unsubmitted; an intent or provider receipt requires reconciliation,
not a new upload. Always stop/disarm/disable the bootstrap on failure.
Kudu may assign its own receipt ID and expose a pending `temp-<8 hex>` entry:
ignore only that exact pending shape, retain unique-deployer correlation, and
independently read the exact final ID; never blindly reupload. Prepared resumes
must start from stopped/disarmed state and skip redundant intermediate
start/stop cycling. Azure platform logs showed such cycling cancelling startup
while ARM still reported Running. Re-read the approved upload receipt, apply
the fresh witness, and start only once for execution.

`--source-witness` accepts the count collector's complete contract+witness
artifact as well as the strict flat witness. The parent validates the envelope's
fixed queries/digest before unwrapping it; private code still accepts only the
six count-only witness fields and enforces five-minute freshness. Collect after
remote preparation, not before a potentially lengthy build.

The user approved the exact SQL/Search and separate credential initialization
bundles recorded in `.azure\deployment-plan.md`, conditional on this now-passed
proof. Approval is not completion. The new approved `release` phase keeps review
disabled and enables the private Functions with bootstrap-only callers for
code delivery. Provider validation and exact artifact approval precede its
deployment; the subsequent `runtime` phase restores the service caller matrix.

## Historical parent execution checkpoint - September 15

`agentic-itops-us93-services-20260915-r2` succeeded after the approved serial
Function integration correction. Subsequently the approved
`agentic-itops-us93-bootstrap-20260915` deployment succeeded using that same
frozen template and the separately validated bootstrap parameters.
Live ARM read-back confirms only bootstrap is enabled; review and all three
Functions remain disabled. All five temporary bootstrap Azure assignments match
the exact approved principal, roles and resource scopes.

Bootstrap main ingress remains deny-all (external HTTP 403 confirmed), while
SCM uses its separate Entra-authenticated deployment path. Platform state
`Running` does not mean bootstrap application code has been published or run.
No code package, SQL migration, sign-in credential or Search schema has yet
been initialized. SQL and evidence Storage remain private, with the original
SQL administrator preserved. The actual worker identity source-access proof
remains pending.

The Kudu and canonical ZIP-path corrections passed their independent source
review. The first actual remote build subsequently exposed a provider-version
difference: its receipt ID was server-generated despite the requested ID header.
The exact unique deployer matched the persisted request. Read-only reconciliation
confirmed that successful build without another upload; bootstrap was stopped
and remains disarmed. The approved worker preflight deployment operation is now
in progress, not yet a source-isolation result. All nine prompt-agent definitions
are registered at version 1; this is not model-workflow execution.

## Exact additional authorization package

`infra/services.bicep` targets the existing resource group; it only reads the
foundation resources. It does not PUT the SQL server, evidence account, Search,
Foundry account/project/models, producer, LAW or Application Insights.
The deterministic suffix is the same `uniqueString(subscription().id,
resourceGroup().id, environmentName)` used by the deployed foundation:
`gcjgvzpp44b4i` for the approved environment.

| Addition | Names / settings |
|---|---|
| Linux B1 | `plan-web-gcjgvzpp44b4i`, one instance |
| Review and isolated temporary bootstrap apps | `app-review-gcjgvzpp44b4i`, `app-bootstrap-gcjgvzpp44b4i`; Python 3.13 on that same B1 |
| Flex apps and plans | `func-{worker,tools,publisher}-gcjgvzpp44b4i`; separate `plan-func-*` FC1; 2048 MB, zero always-ready, selected maximum 40 per app, HTTP concurrency 1 |
| Host/deployment storage | `sthost{0,1,2}gcjgvzpp44b4i`, LRS, Blob + Table private endpoints, no shared keys/public access/bypass |
| Vault | `kv-itops-gcjgvzpp44b4i`, Standard, RBAC, private-only, purge-protected, 90-day soft delete |
| VNet | `vnet-itops-gcjgvzpp44b4i`, `10.93.0.0/16`; web `10.93.0.0/26`, Flex `10.93.1.0/24`, PE `10.93.2.0/26`; no peering/VPN/NAT/VM |
| DNS + endpoints | Five zones; 12 PEs: SQL, evidence Blob, vault, six host Blob/Table, three private Function ingress (including SCM records) |
| UAMIs | `id-{review,worker,tools,publisher,bootstrap}-gcjgvzpp44b4i`; no shared runtime identity |

The bootstrap app adds **no additional plan/PE/DNS baseline** to the previously
quoted $163.32/month ($207.12 with the conditional Flex Defender reserve).
It shares B1 capacity and may increase usage; this is not a no-cost operational
guarantee. B1 contention during build/migration is why runtime hosts stay stopped
until bootstrap finishes. The $250 additional allowance is not a billing cap.

| Principal | Scope | Azure role/action |
|---|---|---|
| Review | New vault | Key Vault Secrets User `4633458b-17de-408a-b874-0445c86b69e6` |
| Each Function identity | Its own host storage only | Storage Blob Data Owner `b7e6dc6d-f1e8-4753-8033-0f276bb0955b`; Storage Table Data Contributor `0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3` |
| Four runtime identities | Existing App Insights | Monitoring Metrics Publisher `3913510d-42f4-4e42-8a64-420c390055eb` |
| Worker | Existing owned Foundry project | Foundry User `53ca6127-db72-4b80-b1b0-d745d6d5456d` |
| Worker, publisher | Existing owned AI account | Cognitive Services OpenAI User `5e0bd9bd-7b93-4f28-af87-19fc36ad61bd` |
| Worker | Existing owned Search | Search Index Data Reader `1407120a-92aa-4202-b7e9-c0e197c71c8f` |
| Publisher | Existing owned Search | Search Index Data Contributor `8ebe5a00-799e-43f5-93ac-243d3dce84a7` |
| Worker | Existing `evidence` container only | Custom role with blobs/read and blobs/write; **no delete** |
| Review, tools | Existing `evidence` container only | Storage Blob Data Reader `2a2b9908-6ea1-4ae2-8e65-a410df84e7d1`; review validates independent result projection |
| Bootstrap, temporary | New vault | Key Vault Secrets Officer `b86a8fe4-44ce-4948-aee5-eccb2c155cd7` |
| Bootstrap, temporary | Existing owned Search | Search Service Contributor `7ca78c08-252a-4471-8644-bb5ff32d4ba0` (index definitions, not document data) |
| Bootstrap, temporary | Each of the three new Function apps | Custom `Microsoft.Web/sites/read` and `Microsoft.Web/sites/publish/Action` only |
| Bootstrap, temporary, separate explicit approval | Existing SQL logical server administrator child | Parent temporarily sets bootstrap **principal/object ID** as Entra admin, then restores existing operator `caef45e0-0b85-4991-a3df-eaba0359e71f` in a `finally` path; no SQL password, server identity or Graph grants |

Neither generic SQL `db_datawriter` nor runtime `db_owner` is acceptable.
Exact SQL object grants are source-controlled in `deployment\sql-grants.json`
and hash-bound through the approved bootstrap ZIP. No producer LAW grant is
created in this entrypoint: parent must approve the observation-table-only
authority and independently prove audit/control denial. Foundry and Search
roles grant service-level capabilities, not per-model/per-index restrictions;
application policy is not Azure RBAC enforcement of those finer boundaries.
Likewise Blob write permission can overwrite: this package removes delete but
does not claim Azure-enforced WORM protection on the existing account.
Code-publish permission can execute code as the target runtime identity;
its narrow ARM action is not a claim that bootstrap cannot exercise those
runtime capabilities. Remove the temporary publish assignments after acceptance.

The parent/operator uses already-approved deployment authority, not a new
subscription Owner assignment: scoped resource/deployment writes and role
assignment/definition administration for provisioning; site read/config
read-write/start/stop/publish for the bootstrap window; and SQL administrator
child read/write only when migrations/grants are selected. The bootstrap UAMI
does not receive those operator control-plane privileges.

### One-time confidential credential transport approval

The parent may implement its journaled Entra helper independently. Only its
isolated, authorized administrator creates the review application's 30-day
`addPassword`, configures the callback and assigns app roles. Bootstrap never
receives a Graph token, Graph permission or directory role.

| Stage | Actor / exact transport | Persistence and cleanup |
|---|---|---|
| Create credential | Parent's authorized Graph client; verify expected review application before `addPassword` | Journal only application/key IDs, expiry and operation state; never `secretText` |
| Local handoff | Set `ITOPS_REVIEW_ENTRA_CLIENT_SECRET` in the executing parent's process environment; `deployment.control_plane` reads it there | No command argument, stdout, ZIP, manifest, plaintext journal or source file; clear local environment in the parent's `finally` |
| Azure handoff | Original ARM operator sends HTTPS PUT to the isolated bootstrap site's `/config/appsettings`; secret is only the `ITOPS_REVIEW_ENTRA_CLIENT_SECRET` setting | App Service encrypts settings at rest; authorized settings readers can still retrieve them. This is not a claim of invisibility from administrators |
| Private vault write | Bootstrap's own UAMI uses `SecretClient` over the private vault endpoint; writes `review-oidc-secret` | No Graph access; session key is generated/preserved separately and reviewed scope JSON is stored as `review-scopes` |
| Receipt and cleanup | Private SCM journal reports completed secret operation with names only; operator stops bootstrap and removes the transient secret/hash/attempt settings, disables execution gate | Cleanup also runs on failure. A failed cleanup/restoration is not a completed rollout |

Prefer a **dedicated secrets-only approved manifest**
(`deployment.manifest --operations secrets`) after bootstrap infrastructure,
roles and source build are ready. This requires **no SQL administrator
replacement**. The operator transfers the credential only after successful
remote build, runs private secret initialization, then stops/disarms and clears
the setting as soon as that run completes. It does not keep the credential
exposed through a subsequent three-Function deployment. ARM settings changes
can restart Linux apps, so cleanup deliberately occurs after stopping the
bounded startup process, not mid-operation.

That operation explicitly covers three vault entries: `review-oidc-secret`,
`review-session-key` (generated once unless already valid) and `review-scopes`
(the manifest's approved catalog). The 30-day credential lifetime is enforced
by the Entra password's expiry set by the parent; bootstrap does not extend it.
No secret values appear in bootstrap receipts.

The parent's helper must not blindly repeat `addPassword`: if its one-time
secret is lost before the vault receipt, reconcile the recorded credential key
ID and obtain approval for cleanup/replacement. The secret cannot be read back
from Graph. New credential creation and this ARM-to-private-vault transport
remain distinct approval steps; implementing the helper authorizes neither.

### Bootstrap network and deployment boundary

The bootstrap app has **deny-all main-site ingress**, no administration route,
and a separate **Entra-only SCM endpoint**. SCM and FTP publishing basic
authentication are disabled. The operator can upload a reviewed ZIP with
`az webapp deploy --type zip --track-status false` (CLI >= 2.48.1) without
publishing profiles. The bootstrap startup runs in the site's integrated
VNet, not the local shell or an assumed VPN. It connects to normal private-link
hostnames with TLS verification and rejects non-private DNS resolution.

From that VNet it can POST each exact reviewed Function ZIP to
`https://<function>.scm.azurewebsites.net/api/publish?RemoteBuild=true`
using its own ARM-audience managed-identity token, then poll that deployment's
receipt. If a unique receipt route returns 404, the official Flex `/latest`
transport is used **only with exact returned deployment-ID matching**; another
deployment's success is never accepted. An ambiguous POST is never blindly
retried. This is the official Azure CLI Flex OneDeploy transport. No SAS,
account key, external package URL, `WEBSITE_RUN_FROM_PACKAGE`, or legacy
zipdeploy is used for Flex. Flex's own UAMI authenticates to its private
deployment Blob container. Bootstrap needs no host/evidence Storage write role.
Remote builds resolve Python Linux dependencies; local Windows wheels are never
included. Native `mssql-python` load and actual MI SQL connectivity must be
verified in the Linux startup, not inferred from Windows tests.

`phase=infrastructure` creates stopped hosts. `phase=bootstrap` additionally
requires `bootstrapAuthorized=true`; runtime hosts remain stopped.
`phase=preflight` enables only the worker for its separately deployed count-only
package; review/tools/publisher remain stopped. Bootstrap remains separately
gated. Do not activate this phase before verifying the preflight ZIP receipt.
`phase=runtime` enables runtime hosts and disables bootstrap. Incremental ARM
deployment **does not delete omitted roles/resources**: switching the gate off
is not permission revocation. Parent must separately approve and execute
temporary role removal, restore SQL admin, stop/delete the bootstrap app and
optionally its identity/custom role. Keep the B1 plan and all runtime identities.

## Verified provenance

- Functions manifest 1.10.0 selected Python HTTP + Bicep + VNet:
  <https://cdn.functions.azure.com/public/templates-manifest/manifest.json>.
- Official base: `Azure-Samples/functions-quickstart-python-http-azd`,
  tag `v1.0.0`, ref object `58801d875f2eef08cf986822822646689b4b0cd7`;
  `infra/app/api.bicep` and `rbac.bicep`. Copied architecture/configuration
  patterns, not demo endpoints. Reviewed AVM pattern index first; no matching
  combined private Flex + SQL/bootstrap pattern, so use resource composition.
- Pinned AVM: web/site 0.15.1, web/serverfarm 0.1.1,
  managed-identity/user-assigned-identity 0.4.1,
  network/virtual-network 0.7.2, network/private-dns-zone 0.7.1,
  network/private-endpoint 0.11.1, key-vault/vault 0.13.1,
  storage/storage-account 0.33.0 (same storage pin as foundation).
  The six foundation AVM pins remain unchanged.
- [Entra deployment authentication](https://learn.microsoft.com/azure/app-service/deploy-authentication-types).
- [SCM minimum publish permission](https://learn.microsoft.com/azure/app-service/resources-kudu#rbac-permissions-required-to-access-kudu).
- [Private Flex deployment and remote-build requirements](https://learn.microsoft.com/azure/azure-functions/functions-deployment-technologies).
- [Official CLI OneDeploy implementation](https://github.com/Azure/azure-cli/blob/dev/src/azure-cli/azure/cli/command_modules/appservice/custom.py)
  `enable_zip_deploy_flex`, `get_scm_site_headers_flex`.
- [Azure SQL CREATE USER WITH SID, TYPE=E](https://learn.microsoft.com/sql/t-sql/statements/create-user-transact-sql?view=azuresqldb-current).
  No `FROM EXTERNAL PROVIDER` Graph lookup. Verify SID/object-ID mapping with
  actual runtime identity before acceptance; do not copy Fabric's distinct
  service-principal client-ID wording into Azure SQL configuration.
- [Azure SQL managed identity authentication and object-ID users without validation](https://learn.microsoft.com/azure/azure-sql/database/authentication-azure-ad-user-assigned-managed-identity?view=azuresql).
  Before any schema/user operations, the Linux bootstrap asserts that
  `SUSER_SID()` equals its approved principal/object UUID in SQL byte order.
  Unexpected SID behavior fails closed; no Directory Readers workaround.

Read-only Azure checks confirmed the expected subscription/tenant and
`centralus` Flex availability. Regional listing is not allocation/quota proof.
Compile/static/package/migration tests do not prove VNet DNS, MI token support,
native SQL runtime, private OneDeploy or actual application acceptance.

## Application and identity contracts

The parent supplies two approved existing/new **application client IDs**:
review's confidential OIDC application and a service API application. These are
not UAMI client IDs. Creating either application, credential, service principal
or app-role assignment is a separate Entra approval; this IaC does none of them.

After the UAMIs exist, the parent applies these approved service-application
role assignments using the identities' **principal/object IDs** and the
administrator's ignored Entra journal. No application IDs, service-principal
IDs or role UUIDs are hardcoded into tracked deployment sources:

| UAMI | Service API application-role values | Activation condition |
|---|---|---|
| `id-review-<suffix>` | `Worker.Invoke`, `Publisher.Invoke` | Exact endpoint caller OID/client allowlists configured |
| `id-worker-<suffix>` | `Tools.Invoke`, `Results.Project` | Tools and independent review projection only |
| `id-tools-<suffix>` | None | Evidence/context reads only |
| `id-publisher-<suffix>` | None | Search/embedding/SQL data-plane work only |
| `id-bootstrap-<suffix>` | `Worker.Invoke`, `Tools.Invoke`, `Publisher.Invoke` | Parent-approved initial probes only; not implicitly exercised by initialization/deployment |

These are Entra application roles, not Azure RBAC assignments. The SQL admin
window and bootstrap's five Azure assignments require their own explicit
cloud approval. Bootstrap's three temporary Entra app-role assignments must
also be removed at retirement if the parent installs them for probes.

- Review redirect URI: `https://app-review-gcjgvzpp44b4i.azurewebsites.net/.auth/login/aad/callback`.
  Review's tenant, public origin, UAMI and Key Vault reference settings are
  generated by IaC. Three private vault secrets are `review-session-key`
  (generated once, at least 32 characters), `review-scopes` (server-held JSON),
  and `review-oidc-secret` (operator-provided confidential OIDC credential).
- Service token issuer is the approved tenant's v2 issuer. EasyAuth and
  application JWT validation accept **the service application GUID**, not
  an additional `api://<GUID>` audience, because Microsoft's
  [access-token claims reference](https://learn.microsoft.com/entra/identity-platform/access-token-claims-reference)
  specifies that v2 `aud` is always the API client ID, not its `api://` URI.
  Parent must configure `api.requestedAccessTokenVersion=2` and the `idtyp`
  access-token optional claim required by the application verifier, then
  prove the actual app-only token claims. EasyAuth does not prove app roles.
  Outbound `ITOPS_TOOLS_TOKEN_SCOPE` and `ITOPS_PROJECTION_TOKEN_SCOPE` are
  separately wired to `api://<GUID>/.default`; a requested scope is not the
  expected token audience. Tool binding `base_url` is the bare tools origin
  plus `/api`, while `ITOPS_TOOLS_ORIGIN` itself remains the bare host.
- `workerSettings.ITOPS_SERVICE_CALLERS_JSON` and the tools equivalent are
  JSON strings containing explicit `{oid, client_id, roles}` entries.
  Worker entry-point role is `Worker.Invoke`; worker's tools access requires
  `Tools.Invoke`. `serviceCallerObjectIds` separately configures platform
  allowed-principal checks. Tools automatically includes the worker OID in
  that platform list; parent still supplies its exact application allowlist
  and Entra app-role assignment.
- Review's independent `/internal/results/project` endpoint accepts only the
  worker's OID/client ID with `Results.Project`. This allowlist, service
  audience and worker review-origin are wired automatically. The worker has
  **no review table write grants**. Review EasyAuth also accepts the service
  API audience for this mount. B1 starts
  `agentic_itops.service_projection:create_review_service()` through gunicorn;
  one worker/four threads allow independent callbacks alongside UI requests.
  Its WSGI dispatcher wraps the unchanged browser-review factory and the
  separately authenticated projection endpoint. Starting only
  `hosting:create_review_app()` would omit that endpoint.
- Worker and tools read `.runtime\runtime.json` and `.runtime\bindings.json`.
  Worker also reads `.runtime\registration-plan.json` and
  `.runtime\registration-receipt.json`. These must be real parent-validated,
  non-secret, mutually consistent artifacts, not `{}` examples.
- Publisher uses `requirements-publisher.txt` (the pinned Search-profile alias),
  separate OID/caller settings,
  explicit `Publisher.Invoke` app role and execution enablement, approved
  budget limits, snapshot and scopes.
  Shared SQL, Search index/endpoint, embedding deployment/model/version/dimensions
  and publisher audience are wired; parent supplies the remaining
  `publisherSettings` from the publisher's integration contract.
  Exact additional strings are `ITOPS_SEARCH_SNAPSHOT_ID`,
  `ITOPS_SEARCH_SCOPES_JSON` (approved `{producer_key, run_id}` entries),
  `ITOPS_PUBLISHER_ENABLED`, `ITOPS_PUBLISHER_CALLER_OIDS_JSON`,
  `ITOPS_EMBEDDING_BUDGET_JSON` (all approved `BudgetLimits` fields) and
  `ITOPS_EMBEDDING_BUDGET_SCOPE`. Worker also needs the Search snapshot/scopes
  and explicit `ITOPS_EMBEDDING_BUDGET_JSON` through `workerSettings`.
  Set the single top-level **`embeddingBudgetScope`** deployment parameter:
  IaC supplies it as `ITOPS_EMBEDDING_BUDGET_SCOPE` to worker and publisher,
  overriding per-service scope settings so they cannot diverge. Its empty
  infrastructure/preflight default deliberately fails model startup; supply
  the approved value before runtime activation. Worker and publisher
  must use the **same approved embedding scope and policy** when sharing one
  monthly embedding allowance. Different scopes require separately allocated
  caps; never duplicate the full allowance across two ledgers. The worker
  runtime must consume that setting, not derive a separate hardcoded scope.
  These values are never inferred from index contents or copied from the
  reasoning budget.
- `workerSettings`, `toolsSettings`, `publisherSettings` contain **non-secret
  string app settings only**. Do not pass secrets through ARM parameters.
  Hard identity/network/authentication values override those objects.
  No scheduler, queue/file trigger or always-ready activation is added.

See `application-integration.md` and the publisher's service contract for
application-specific readiness requirements. Bootstrap performs explicit
migrations, grants, secrets, index definition, code deployment, or the separately
approved count-only source proof below. It never silently invokes a business
worker/publisher endpoint or spends a model budget.

## SQL migration and runtime boundary

The original SQL migration files are unchanged. Eight ordered ledger entries:
consumer 001/002, review 001/002/003, runtime budget 001, application run 001,
and deployment authority 001. `deployment\migrate.py` takes a session-owned SQL
application lock, uses an explicit outer transaction, and commits each
migration's DDL and SHA256 ledger row together. Original inner transaction
batches are checked for a healthy outer transaction. GO/string/comment/bracket
lexing supports the existing files and rejects SQLCMD, repeated GO and malformed
input. Changed ledger hashes or untracked pre-existing sentinel objects stop
instead of adopting potentially incompatible state.

Four contained SQL users are created by known **application/client-ID SID**
with `TYPE=E`:
`itops_review`, `itops_worker`, `itops_tools`, `itops_publisher`. Existing users
must match; unexpected role memberships, explicit permissions, schema ownership
or individual object ownership fail. Only exact SELECT/INSERT/UPDATE/EXECUTE object allowlists are
accepted; no DELETE, broad database roles, DDL, db_owner or Graph lookup.

Do not confuse the two identity identifiers. ARM role assignments and the
temporary SQL **administrator resource** use the UAMI principal/object ID.
Azure SQL application principals use the binary application/client ID for
their SQL SID. The grant manifest carries both maps; the parent reads each
actual UAMI and verifies its object/client pair before opening an administrator
window. Native bootstrap checks `SUSER_SID()` against its client-ID bytes, not
its object-ID bytes. See Microsoft's
[principal SID reference](https://learn.microsoft.com/sql/relational-databases/system-catalog-views/sys-server-principals-transact-sql).

The first private initialization connected to SQL but failed the old,
incorrect object-SID assertion before any migration/user creation. The original
administrator was independently read back after restoration, with Entra-only
authentication preserved. Its failed journal and ZIP remain intact. A corrected
client-SID bundle requires separate exact-hash approval before retrying.

The additive `deployment\sql\001_runtime_boundaries.sql` installs four database
triggers: publisher incident/revision/publication updates can only revoke
eligibility, cannot extend approval expiry, and cannot create publication-upload
commands. Outbox identities and payloads cannot change. The added rules also
require publisher-created exclusion/invalidation commands to start
pending and permit status updates only on publisher-owned command kinds to
leased/failed/processed/cancelled. Repeated revocations remain permitted.
Byte-length checks reject trailing-space variants that SQL equality otherwise
pads even under BIN2; all allowed kind/state strings are canonical ASCII.
Original immutability triggers, the application attempt/final-receipt
immutability trigger and these
authority triggers must exist, be enabled and belong to the expected tables
(nine required triggers total) before grants apply. No trigger is added to
`review.publisher_jobs`, whose fenced UPDATE uses OUTPUT without INTO.
SQL Server execution, each runtime login and negative privilege tests still
require the approved live private bootstrap/acceptance window.

## Operator execution runbook

The first live provisioning attempt exposed a shared-subnet
`ServiceAssociationLinkLease` conflict when the three Flex apps integrated in
parallel. The Function-module loop now uses `@batchSize(1)` so integrations
complete sequentially. Retry only after inspecting failed operations; do not
delete the shared subnet, recreate identities or weaken networking. This
ordering change adds no resources or privileges. The canonical preflight
artifact path is `.runtime/access-probe-contract.json`; the standalone
application packager's `.preflight` path must not replace it in this IaC.

All commands start in `agentic-itops`. `.build` examples are ignored local
artifacts; **never commit deployment captures or credentials**. PowerShell
variables below must be supplied by the parent from the approved deployment
plan. Commands containing Azure writes are instructions only, not authorization.

### 1. Offline validation and source packaging

```powershell
az bicep build --file infra\services.bicep --outfile .build\services.arm.json
.\.venv\Scripts\python.exe -m pytest tests\test_service_deployment.py tests\test_service_infrastructure.py -q

.\.venv\Scripts\python.exe -m deployment.package --service review `
  --output .build\packages\review.zip --stage .build\azd\review
.\.venv\Scripts\python.exe -m deployment.package --service publisher `
  --output .build\packages\publisher.zip --stage .build\azd\publisher
.\.venv\Scripts\python.exe -m deployment.package --service tools `
  --artifact "runtime.json=$RuntimeConfiguration" --artifact "bindings.json=$Bindings" `
  --output .build\packages\tools.zip --stage .build\azd\tools
.\.venv\Scripts\python.exe -m deployment.package --service worker `
  --artifact "runtime.json=$RuntimeConfiguration" --artifact "bindings.json=$Bindings" `
  --artifact "registration-plan.json=$RegistrationPlan" `
  --artifact "registration-receipt.json=$RegistrationReceipt" `
  --output .build\packages\worker.zip --stage .build\azd\worker
```

Worker profile is `requirements-worker.txt` (Functions plus Search); tools is
`requirements-functions.txt`; review is `requirements-web.txt`. Bootstrap has
its own minimal `requirements-bootstrap.txt`; no consumer/foundry manifest was
changed for deployment dependencies. Only install a profile when it changed
or validation reports its missing dependency.
Publisher's canonical profile is `requirements-publisher.txt`, recursively
flattened through `requirements-search.txt` like its owner-provided package.

The packager uses standard Python ZipFile, deterministic Unix entries, sorted
source-only allowlists and flattened pinned requirements. No Windows wheels,
venv, `.azure`, captures or workspace-wide archive. Explicit credential fields
inside runtime JSON are rejected. Source-only worker/tools ZIPs can be built
without artifacts for inspection, but **the release bootstrap bundle rejects
them**. Staging rejects stale extra files rather than silently shipping them.
Runtime archives include the explicit original consumer/review/runtime SQL
source assets, but never execute migrations at startup. The deployment-owned
authority migration and grant executor remain only in the bootstrap package.

### 2. Approve/provision stopped services, then approve bootstrap RBAC

Parent first completes azure-validate/provider validation/what-if using
`infra\services.bicep` and a **fully resolved** non-secret parameters file.
`infra\services.parameters.json` contains azd substitutions, not a ready
`az deployment` parameters file. `phase=infrastructure`, bootstrap gate false
is the first approved deployment. Do not run `infra\main.bicep` or provision the
foundation again.

After initial deployment, read output contracts without credentials:

```powershell
az deployment sub show --name $ServiceDeploymentName --query properties.outputs `
  --output json | Set-Content -Encoding utf8 .build\service-outputs.json
```

Outputs include four service names, bootstrap name, vault name, review URL,
all five UAMI resource/client/principal IDs and the private hostname set.
`ITOPS_SERVICE_HOSTNAMES` comes from each actual site's `defaultHostName`;
`ITOPS_REVIEW_URL` uses that returned hostname for the Entra callback. The
resource-name-derived URLs listed above are planned names until that readback.
Manifest generation fails if Azure returns hostnames differing from the
reviewed network contract (for example a platform-generated regional suffix);
reconcile all origin, private-DNS and authenticated SCM targets before
bootstrap rather than silently directing credentials to a guessed hostname.
The next separately approved service deployment sets `phase=bootstrap`,
`bootstrapAuthorized=true` to install only the listed temporary Azure roles
and enable the isolated host. No SQL admin changes occur in Bicep. The startup
cannot execute privileged work without a separately supplied manifest hash.

### 3. Assemble the exact hash-bound execution package

`$ReviewScopes` is the validated sources/entitlements JSON file.
`$SearchIndex` is `services\publisher\knowledge-index.schema.json`, the
publisher's exact current Search index schema (`agentic-itops-knowledge`,
API `2025-09-01`), not an invented sample. Worker/publisher packages include
that explicit asset; a Search-initialization bundle rejects a manifest
definition that differs from it.
`$ApprovalExpiry` is an explicit UTC ISO timestamp covering the bounded run.

```powershell
.\.venv\Scripts\python.exe -m deployment.manifest `
  --service-outputs .build\service-outputs.json --environment-name agentic-itops-us93 `
  --review-scopes $ReviewScopes --search-index $SearchIndex `
  --package-directory .build\packages --expires-at $ApprovalExpiry `
  --output .build\bootstrap-manifest.json
.\.venv\Scripts\python.exe -m deployment.bundle `
  --manifest .build\bootstrap-manifest.json --package-directory .build\packages `
  --output .build\packages\bootstrap.zip
```

The default operations are migrate, grants, secrets, search, deploy, in that
order. A deployment-only repeat can explicitly select `--operations deploy`;
the operator executor then **does not open any SQL administrator window**.
Use a new deployment ID/manifest/journal for changed approved content.

### 4. Review dry-run, explicitly approve, execute with automatic restoration

```powershell
$BootstrapArgs = @(
  '--manifest', '.build\bootstrap-manifest.json',
  '--package', '.build\packages\bootstrap.zip',
  '--subscription', '643febd3-3170-4110-80f9-fc8980b70a7a',
  '--resource-group', 'agentic-itops-us93-rg',
  '--bootstrap-object-id', $BootstrapObjectId,
  '--restore-operator-object-id', 'caef45e0-0b85-4991-a3df-eaba0359e71f',
  '--recovery-journal', '.build\bootstrap-operator-recovery.json'
)
.\.venv\Scripts\python.exe -m deployment.control_plane @BootstrapArgs
# STOP: parent obtains approval of the printed exact package SHA256 and operations.
# Parent securely sets ITOPS_REVIEW_ENTRA_CLIENT_SECRET in its process environment.
# Never place the credential in this command, a manifest, transcript or repository file.
try {
  .\.venv\Scripts\python.exe -m deployment.control_plane @BootstrapArgs `
    --execute --approved-package-sha256 $ApprovedBootstrapZipSha256
  if ($LASTEXITCODE -ne 0) { throw 'Bootstrap failed; inspect the sanitized recovery journal.' }
}
finally {
  Remove-Item Env:\ITOPS_REVIEW_ENTRA_CLIENT_SECRET -ErrorAction SilentlyContinue
}
```

The executor checks isolated identity, deny-all ingress, VNet integration and
disabled basic publishing before mutations. It stops and disarms bootstrap,
starts it **unprivileged** for public Entra SCM build, persists upload intent,
and polls its request-correlated receipt. Only after successful Linux build does it stop
again, persist original admin recovery data, temporarily set the bootstrap
object ID as SQL Entra admin (SQL operations only), supply the approved gate
and run. Main-site HTTP remains denied throughout.

SQL admin restoration and stop/disarm/secret-setting removal run in cleanup,
including failure paths. The receipt must match both manifest SHA and the new
attempt ID; stale success/failure cannot satisfy the current run. Bootstrap
uses a Linux process lock and persistent journal under
`/home/data/itops-bootstrap/<deployment-id>.json`; the operator reads it only
over authenticated SCM VFS. No secret values are emitted.

Remote build/native driver, private DNS/TLS, Azure RBAC propagation and Flex
OneDeploy may fail live. Such failures **never** permit public endpoint,
publishing password, shared-key or assumed-VPN fallback. Reconcile durable
receipts; a POST whose outcome is unknown cannot be blindly retried.

#### Supported Linux Kudu asynchronous correlation

Linux Kudu's actual asynchronous ZIP response can point to
`/api/deployments/latest?deployer=...&time=...`; that URL is not a unique receipt.
The operator therefore generates a fresh deployment GUID and persists it,
the approved ZIP hash, unique deployer label and exact status URL **before
POST**. It sends the supported `SCM-DEPLOYMENT-ID` header and `deployer` query
parameter. It ignores the response's Location hint and first polls
`/api/deployments/<persisted-guid>`. On 404, it reads a bounded deployment
inventory and requires exactly one receipt with the persisted unique deployer.
It validates/persists that server-generated GUID, then independently reads its
exact endpoint, requiring both ID and deployer to match before accepting success.
An inventory success alone cannot pass. Duplicate matches, unrelated receipts,
invalid IDs or uncorrelated prior journals fail closed. It never polls an unbound
`/latest` or republishes an ambiguous upload.

This follows public KuduLite's `Common/Constants.cs`
(`ScmDeploymentIdHeader`), `PushDeploymentController.GetExternalDeploymentId`,
`ArtifactDeploymentInfo.GetRepository` and `NullRepository.Commit`: the
external header can become the artifact changeset/deployment ID in that source.
The actual September 16 Azure host instead allocated its own GUID while retaining
the exact deployer label. Live behavior therefore requires the reconciliation
path above; the source reference is not a guarantee across deployed versions.
Sources: [push controller](https://github.com/Azure-App-Service/KuduLite/blob/master/Kudu.Services/Deployment/PushDeploymentController.cs),
[header constant](https://github.com/Azure-App-Service/KuduLite/blob/master/Common/Constants.cs),
[artifact repository](https://github.com/Azure-App-Service/KuduLite/blob/master/Kudu.Core/Deployment/ArtifactDeploymentInfo.cs),
[receipt identity](https://github.com/Azure-App-Service/KuduLite/blob/master/Kudu.Core/SourceControl/NullRepository.cs).

After a timeout or interrupted POST, recovery with the same journal performs
only correlated inventory/exact-ID reads; it never sends another ZIP POST, including when
the receipt remains absent. `--prepare-only` followed by normal execution
reuses this same correlation. Legacy records without pre-POST correlation
require explicit operator reconciliation; no automatic adoption or resubmit.
Rebuild/reapprove the bootstrap ZIP for this source change. Approved Bicep
and the outbox authority migration are unchanged.

### 5. Interrupted SQL administrator window

Keep the local recovery journal durable before starting. If the operator
process is interrupted while SQL is elevated, inspect and explicitly approve:

```powershell
.\.venv\Scripts\python.exe -m deployment.restore_admin `
  --journal .build\bootstrap-operator-recovery.json `
  --approved-restore-object-id caef45e0-0b85-4991-a3df-eaba0359e71f
# Parent approval required before adding --execute.
```

Recovery refuses a different original operator, mismatched resource or an
unrelated current administrator. It stops bootstrap, restores the saved
operator, verifies Entra-only authentication and disarms secret settings.
Do not edit a migration ledger or upload receipt to force a retry. A failed
restoration is an incident requiring this recovery, not a successful rollout.

### 6. Review deployment, runtime activation, acceptance and retirement

Deploy the review source ZIP with approved Entra-only `az webapp deploy` or the
review-only azd path; never call ordinary `azd deploy` for the three private
Functions. Their predeploy hooks deliberately reject it. Review's remote
Linux build uses `SCM_DO_BUILD_DURING_DEPLOYMENT=true`; no publishing profile.

Parent performs a final, explicit `phase=runtime`, gate-false deployment with
complete app settings and Entra role bindings. Then prove real private
Function execution, review sign-in/projection, native UAMI SQL access and
negative privilege boundaries, bounded model/embedding use, Search
publish/search/revocation and telemetry. **This implementation does not create
a hidden private business-invocation runner.** Parent must separately approve
the VNet business-acceptance caller/mechanism; the explicit count-only preflight
below is not business-workflow acceptance. Do not attach privileged bootstrap UAMI
to review or expose Functions for testing.

After acceptance and explicit retirement approval: remove only bootstrap's
five Azure assignments (vault, Search, three Function publish scopes), its
publish-only custom role definition, app and optionally UAMI. Preserve B1,
runtime identities, DNS, PEs, storage and all foundation resources. Confirm
original SQL operator, no runtime db_owner/DDL, bootstrap stopped/deleted,
temporary secret setting removed, and no unintended schedule/model activation.

## Restricted worker-MI source preflight

This is a separate, model-free approval package using the **same** worker Flex
host and isolated bootstrap app. It adds no service, subnet, endpoint, plan or
budget default. It does not generate the producer-workspace role assignment.
The parent must separately approve/apply the supported exact-table ABAC grant,
wait for effective propagation and retain an independent known-nonempty witness.
No legacy `query/<custom-table>/read`, `Tables.Custom/read`, producer Reader or
Contributor fallback is permitted; no producer workspace properties change.

### Package/deploy only the count endpoint

The trusted `$ProbeContract` comes from the parent's
`agentic_itops.access_probe.query_contract`. It fixes workspace/run/time/count
queries and hash; the worker never accepts replacement authority in its request.
The canonical package maps `services\worker\preflight_function_app.py` to ZIP-root
`function_app.py` and includes only `.runtime\access-probe-contract.json` as its
runtime artifact. It exposes only `POST /api/preflight/source-access`, not the
inference routes. Full worker package gates still require the four model/runtime
artifacts; they are not weakened for preflight.
Its minimal `requirements-preflight.txt` profile contains no SQL/model/Search/
Blob/MCP SDK. Normal worker/publisher dependency profiles are unchanged.

```powershell
.\.venv\Scripts\python.exe -m deployment.package --service worker-preflight `
  --artifact "access-probe-contract.json=$ProbeContract" `
  --output .build\packages\worker-preflight.zip
.\.venv\Scripts\python.exe -m deployment.manifest `
  --service-outputs .build\service-outputs.json --environment-name agentic-itops-us93 `
  --review-scopes $ReviewScopes --search-index $SearchIndex `
  --package-directory .build\packages --expires-at $ApprovalExpiry `
  --operations deploy-preflight --output .build\deploy-preflight-manifest.json
.\.venv\Scripts\python.exe -m deployment.bundle `
  --manifest .build\deploy-preflight-manifest.json --package-directory .build\packages `
  --output .build\packages\deploy-preflight-bootstrap.zip
```

Use the existing `deployment.control_plane` dry-run/exact-hash approval flow
with **these** manifest/package paths and a dedicated recovery journal. Its
`deploy-preflight` operation can deploy only `worker-preflight.zip` to the
owned worker SCM, and cannot be combined with SQL, secrets, Search or full
runtime deployment. SQL administrator is never changed.

After that exact deployment succeeds, parent approves/applies `phase=preflight`.
It enables only worker and generates both platform and in-app caller allowlists
for the bootstrap OID/client ID with `Worker.Invoke`. It also supplies
`ITOPS_PREFLIGHT_BOOTSTRAP_OID` and
`ITOPS_ACCESS_PROBE_CONTRACT_PATH=.runtime/access-probe-contract.json`.
This is the canonical deployment path matching the approved ARM template;
the standalone application helper's `.preflight` layout is not interchangeable.
The parent must have actually assigned bootstrap `Worker.Invoke` in Entra.
The preflight factory initializes no SQL, Blob, Search, registration or model
path; it creates its Logs client with **worker's** explicit UAMI.

### Execute the private proof with a fresh witness

```powershell
.\.venv\Scripts\python.exe -m deployment.manifest `
  --service-outputs .build\service-outputs.json --environment-name agentic-itops-us93 `
  --review-scopes $ReviewScopes --search-index $SearchIndex `
  --package-directory .build\packages --expires-at $ApprovalExpiry `
  --operations probe-source --probe-contract $ProbeContract `
  --service-application-client-id $ServiceApplicationClientId `
  --output .build\source-probe-manifest.json
.\.venv\Scripts\python.exe -m deployment.bundle `
  --manifest .build\source-probe-manifest.json --package-directory .build\packages `
  --output .build\packages\source-probe-bootstrap.zip
```

Use `deployment.control_plane` with this new manifest/ZIP and a dedicated
recovery journal. Parent first reviews its dry-run and approves its exact ZIP
hash, then runs **`--execute --approved-package-sha256 <approved-hash>
--prepare-only`**. This builds the bootstrap ZIP, stops/disarms it and executes
no data operation. It is also available for secrets-only preparation before
creating a one-time credential.

**After** build completes and permissions have propagated, parent generates a
new count-only operator witness using its own authorized operator context.
No Graph token or operator credential is transferred. Run the same approved
command/journal **without `--prepare-only`**, adding
`--source-witness <fresh-witness-json>`. The existing unique SCM receipt avoids
rebuilding; the witness must remain under five minutes old at the actual probe.
If cold start exhausts that window, fail and refresh; never increase the
freshness limit or treat missing/empty data as proof.

The operator puts only this bounded witness into transient
`ITOPS_SOURCE_PROBE_WITNESS_JSON`, then starts the isolated bootstrap. Bootstrap
calls only the private worker count endpoint with its service token and
`{witness: ...}`. Worker obtains its **own** Logs token and must demonstrate
matching positive observation count plus denied access to independently
nonempty audit data. Bootstrap verifies returned caller/worker IDs, contract,
fresh timestamp, counts, denial evidence and `inference_input=false`.
No audit rows or labels are accepted or forwarded to inference.

The receipt is persisted in the private bootstrap/operator journal; the
operator stops/disarms and clears the transient witness setting afterward.
No SQL administrator window, vault write, Search call or model call occurs.
A completed source-proof deployment ID cannot be replayed as a fresh proof:
generate a new manifest/approval for a later verification.

Promote to full worker code only with the separate three-runtime-package
approval and complete runtime artifacts. Parent then explicitly approves
`phase=runtime` with normal caller settings. Count-only proof is a gate for,
not evidence of, the later full application/business acceptance.

## Validation evidence and remaining live gates

- Service Bicep builds with Bicep 0.38.33; commercial-cloud hostname linter
  warnings are expected, not validation of sovereign clouds.
- **84 targeted offline tests pass**: deterministic packages and exclusions,
  runtime artifact gates, exact role/resource composition, migration batching
  and transactional ledger, SQL grant boundaries, private DNS rejection,
  manifest hash/expiry, application client-SID assertion, idempotent vault/Search behavior,
  receipt-bound OneDeploy, late SQL elevation/restoration failures, separate
  preflight packages, prepare-only execution and identity-bound count receipts.
- `azure.yaml` validated against the official azd JSON schema.
- All five source package profiles build; missing Key Vault SDK was resolved
  by installing the dedicated new bootstrap profile. SDK import/native-provider
  metadata checks were read-only; Windows load is not Linux proof.
- Read-only provider checks confirm Central US Python 3.13/Flex availability.
  Web usage returns Basic current 1/limit -1, not a finite reservation; the
  API did **not expose Flex regional memory quota**. The
  [Flex plan documentation](https://learn.microsoft.com/azure/azure-functions/flex-consumption-plan),
  updated September 14, 2026 and verified by the parent, documents a regional
  default of 250 cores / 512,000 MB, excluding scale-to-zero instances. Quota
  overage can throttle executions/deployments but does not prevent creation of
  new apps. Missing live quota evidence therefore does not block approved
  stopped-infrastructure creation; runtime allocation remains unproven.
  Current documentation permits a maximum-scale setting as low as 1.
  The approved template's 40 is a selected configuration, not a platform
  minimum, and is not silently reduced. No quota increase was requested.
- Still pending: provider what-if/validation with resolved approved values,
  actual resource provisioning/RBAC, OIDC/app-role changes, Linux private
  bootstrap/OneDeploy, runtime data-plane/negative authorization tests,
  reviewed budgeted acceptance and explicit bootstrap retirement.
  A retained baseline cannot justify invented incidents. Any consumer-only
  all-nine fixture/provenance path requires separate implementation and
  approval if needed; E14 accuracy is not established by these tests or the
  count-only authorization proof.
