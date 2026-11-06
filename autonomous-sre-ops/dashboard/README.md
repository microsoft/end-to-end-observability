# ASRE incident journey console

Run from the subproject root (`autonomous-sre-ops\`) with the existing Python environment and a
deployment context (see **Deployment context**):

```powershell
$env:ASRE_CONTEXT_FILE = "$PWD\infra\environments\context.generated.json"
.\.venv\Scripts\python.exe -m dashboard.server
```

Open **http://127.0.0.1:8765**. The homepage is an interactive incident journey:
select an actual native incident, inspect its matched use-case branch, and
click stages for evidence, expected procedure and safe navigation actions.
The original charts, queries and sortable tables remain at **`/evidence`**.
If the port is already occupied, use
`-m dashboard.server --port 8766` and open the matching URL. Stop with Ctrl+C.
The single-file `dashboard\index.html` also opens directly, but deliberately
shows a disconnected layout rather than fake operational data.

For a new checkout, create a Python 3.11+ environment and install
`dashboard\requirements.txt`. Azure CLI must be installed and signed in:

```powershell
az login --tenant <tenant-id>
```

Use the `tenant_id` of the deployment context. The adapter pins its credential's tenant and every
resource/query's subscription, and both come from the context; it does not change the shared Azure CLI
default subscription. Tokens stay in server memory,
never in HTML, responses, browser storage or source files.

## Deployment context

Every deployment-specific value (subscription, tenant, resource ids, the ServiceNow origin) comes from the
deployment context document, never from the source:

- **Hosted:** Terraform sets the static `ASRE_CONTEXT` environment variable on the container app.
- **Local viewer:** point `ASRE_CONTEXT_FILE` at the context written by `scripts/Export-AsreContext.ps1`
  after an apply:

  ```powershell
  $env:ASRE_CONTEXT_FILE = '<repo>\autonomous-sre-ops\infra\environments\context.generated.json'
  ```

There is **no default**: without a context the modules raise `ContextError` at import. The dashboard needs
only the core context. It works without the optional `servicenow` section (no case reference is then
recognised), without `agent.endpoint` (the endpoint is read from the agent's ARM resource) and without
`operator_object_id`.

`dashboard\context.py` is a byte-identical vendored copy of `operations\context.py`, because the image build
context is `dashboard\` and cannot see `operations\`. `tests/test_dashboard_context.py` pins the equality;
refresh the copy with:

```powershell
Copy-Item operations\context.py dashboard\context.py
```

## Journey evidence and actions

The canvas uses the single existing `asre-azure-monitor-review` plan and its
five fixed profiles. Skill names/procedures are expected configuration, not
proof that a skill loaded. With no incident in a branch, it becomes an explicit
plan preview with no fabricated execution.

The bounded `/api/incidents/{uuid}` reader validates incident membership and
native Incident/Trigger provenance before reading that incident's details,
messages and pending approvals. It uses native
`incidentDetails.investigationStatus`, **not** Monitor's resolved condition,
for the investigation stage. Record modification time is not presented as an
execution completion timestamp.

The timeline projects public SREAgent updates and metadata-only hook events.
Reasoning, session insights, unknown message types, raw tool arguments/results
and approval prompts are excluded. A completed PostToolUse hook does not prove
that a named tool succeeded. Eligible events are capped at the last 250.

Case links are extracted only from native public agent messages and restricted
to the deployment context's ServiceNow origin and the incident/sys_id URL shape;
with no `servicenow` section no reference is ever recognised. They are labeled
**agent-reported references**, not independently verified case delivery or live
case state. Delivery verification remains Not observed until an independent
case/findings receipt source is bound. No case closure or recovery is inferred.

Click stages to inspect expected actions, open source evidence or the native
agent, filter timeline events, copy an incident ID, or export the normalized
trace. A reported case can be opened with its evidence limitation visible.
There are no tool-execution, repair, retry, approval-decision or ticket-write
buttons. Existing automatic case delivery gets no new mandatory human gate.
The six-hour scheduled review appears in its own read-only lane.

Native journey reads refresh every 15 seconds while visible, with a 10-second
detail cache. Overview source reads refresh every 60 seconds. Failed reads
mark retained data stale; selecting another incident discards the old journey.

## What is live

- ARM agent power, provisioning and action mode; five enabled-rule states.
- Native response-plan state, proactive schedule, connector health and
  Trigger-origin incident threads for the five ASRE rules.
- Actual `/srv/asre-demo` Perf samples, Update Manager installation results,
  Azure Monitor alert instances and Recovery Services backup jobs.
- Per-source request errors, empty results, row caps, retrieval timestamps and
  old filesystem samples. An empty ARG response does not independently qualify
  provider access: Azure Monitor can suppress underlying ARG errors.

The three default-24h provider queries exactly match
`agent\knowledge\asre-live-report-sources-v1.md` as rendered for the deployment
(`scripts/Render-AgentAssets.ps1`); the checked-in guide carries the synthetic test
context, and `tests/test_dashboard.py` pins the equality under it. Closed period choices change
the provider's own timestamp predicate as well as the API timespan. Filesystem
charts use interval means, not instantaneous readings; the seven-day choice
uses 15-minute bins to stay below the 1,000-row cap.

The selected period filters provider events and incident creation times.
Runtime/configuration cards show the latest retrieved state, not historical
configuration. Thread collection results are records returned by the native API,
not proof of investigation completion. A resolved source incident does not mean
a ServiceNow case is closed. The operational evidence page has no independently
verified case-link source; the journey's reported references are separately
labeled and never become a live ServiceNow counter.

Refresh is automatic every 60 seconds while the page is visible. Ordinary
requests share an in-memory 30-second cache; the Refresh button requests new
reads. Source event times and adapter retrieval times are separate.

## Access and boundaries

By default this is a **local viewer**, not a public hosted authentication service. It
binds only `127.0.0.1`, rejects unexpected Host/Origin and cross-site requests,
blocks authenticated redirects, bounds upstream responses and exposes only
fixed GET status routes. Its only upstream POST is a fixed read-only Logs
query. It cannot invoke tools, send chat, approve actions, trigger faults,
create tickets, modify schedules or grant permissions.

The signed-in account needs read access to the fixed ASRE resources, Logs
queries and native SRE data-plane APIs. Resource Reader alone is not a promise
that every runtime endpoint is authorized. In particular, a native Reader may
not read schedule/configuration endpoints; a 403 remains an explicit unavailable
panel. Do not elevate to Administrator just to fill a dashboard card.

The agent endpoint and workspace customer ID are resolved from their fixed ARM
resources each refresh, so redeployment does not require editing a dynamic
hostname or customer ID. The native plan, schedule and connectors are selected
by their owned names. Subscription, tenant and resource ids come from the
deployment context; resource names follow the fixed `asre` convention, which the
context loader enforces, so a different convention is a deliberate code change
(the queries, the rule ids and the loader).

Shared deployment requires Entra viewer authorization and a dedicated
least-privilege read identity. Do not expose the local mode publicly, add a
generic query/proxy endpoint, or reuse the agent's case-writing action identity.
Native incident processing, schedules, tools and all Azure configuration are
unchanged by running this dashboard.

## Opt-in hosted configuration

Cloud hosting is opt-in; the normal local command and credentials are unchanged.
For a container deployment, configure all three settings:

| Setting | Required hosted value |
|---|---|
| `ASRE_DASHBOARD_HOST` | `0.0.0.0` |
| `ASRE_DASHBOARD_PUBLIC_ORIGIN` | The exact HTTPS dashboard origin, without a port or path |
| `ASRE_DASHBOARD_MANAGED_IDENTITY_CLIENT_ID` | The dedicated read-only managed identity's client UUID |

The equivalent flags are `--host`, `--public-origin`, and
`--managed-identity-client-id`. `--port` still defaults to 8765.
Incomplete hosted configuration fails before the server listens. Hosted mode
uses only the specified managed identity; it never falls back to local Azure
CLI credentials or the SRE action identity. The resource/query allowlists,
response filtering, write restrictions, and UI remain unchanged.

These settings **do not implement viewer authentication**. Deploy only behind
HTTPS ingress with Entra authentication and an explicit approved-viewer policy.
Do not expose the container port directly or rely on Host/Origin checks as
authorization. Hosted mode permits top-level page navigation after sign-in,
but still rejects cross-site API reads and all operational writes.

Configure health probes for `/healthz` with `Host` set to the approved public
hostname. Its response describes process health, not successful Azure access.
The platform's authenticated ingress and actual source-read permissions require
separate deployment verification.

The offline tests need a context too. pytest sets one by itself (the committed synthetic fixture); plain
`unittest discover` needs `ASRE_CONTEXT_FILE` to point at a context file. The synthetic fixture
`tests\fixtures\context.synthetic.json` is fine for tests, never for a real run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_dashboard.py tests\test_dashboard_journey.py tests\test_dashboard_context.py -q
$env:ASRE_CONTEXT_FILE = "$PWD\tests\fixtures\context.synthetic.json"
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_dashboard.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_dashboard_journey.py
```

## Azure hosting (deferred phase)

The dashboard is a deferred component and is not deployed with the foundation. The image is
built from a workstation with `scripts/Build-RuntimeImages.ps1` and `ASRE_RUNTIME_IMAGE_KIND=dashboard`, which
selects only the `asre-dashboard` repository (the default kind builds `asre-operations`); the image digest
comes from the `image-provenance.json` that the build writes. The infrastructure is planned and applied with
`scripts/Invoke-Terraform.ps1 -Action Plan|Apply` from a reviewed saved plan. See `docs/deployment.md` for the sequence.

`infra/dashboard.tf` is off by default. The `dashboard` input has four phases; every phase other than
`disabled` also requires the field `viewer_group_id` (a lowercase GUID) and `operator_object_id` in the
deployment inputs:

| Phase | Effect |
|---|---|
| `disabled` | No dashboard resources |
| `bootstrap` | New dashboard group/environment/identity/vault and scoped read/pull grants; no app. Names derive from the deployment's `name_suffix` |
| `private` | Digest-pinned app, internal ingress and native Entra group authentication |
| `public` | Publish HTTPS only after private authentication/source verification |

Use the actual image provenance digest, not `latest` or a guessed digest.
The viewer group is the Entra security group whose object id the operator supplies as
`dashboard.viewer_group_id` (Terraform) and `-ViewerGroupId` (bootstrap helper); the helper cross-checks both
and verifies it is an enabled security group in the tenant. Native authentication uses
`allowedPrincipals.groups`, single-tenant issuance and `SecurityGroup` token
claims. Missing or group-overage claims must not become all-tenant access.
Do not change membership as part of deployment.

Only after the reviewed foundation apply (`phase = "bootstrap"`), the operator named in
`operator_object_id` (the signed-in user must be that principal) can run:

```powershell
.\scripts\Initialize-DashboardAuthentication.ps1 `
  -OutputsFile <actual-foundation-deployment-outputs.json> `
  -MetadataPath <ignored-session-directory>\dashboard-authentication.json `
  -ViewerGroupId <viewer-group-id> `
  -Execute
```

`-OutputsFile` is `terraform -chdir=infra output -json` saved to an ignored session directory.

The helper verifies the tenant-pinned operator, group and exact foundation.
It creates only the owned dashboard registration/enterprise application and a
90-day sign-in credential, then writes the credential directly to the new
dashboard vault in memory. Its output metadata contains only IDs, expiry and
the versioned secret reference. It never resets another app's credentials.
If issuance succeeds but storage fails, the partial issuance is recorded and
a retry stops rather than silently creating/replacing more credentials.
Resolve that recorded key before retrying. Rotation is a separate operation.
Before issuing a real credential, it checks newly propagated vault set access
using a disabled, noncredential version at the owned secret name.

Besides the viewer group, Terraform receives only `image_digest`, `entra_client_id` and
`secret_version_uri`. The app uses AzAPI metadata reads and a Key Vault
reference: no Plan `listSecrets` grant or actual credential value is needed.
No workspace shared keys, log-export jobs, SRE writes, tool invocation or
approval permissions are granted to the dashboard identity.

Verify actual source access separately from process health. The three provider
queries can return empty results without independently proving ARG access.
Review role assignments/provider access and known source evidence; never
interpret empty data or HTTP 200 as full acceptance. Check native Entra
configuration, anonymous rejection and group-member/nonmember behavior before
claiming viewer-access acceptance.
Private-phase startup performs one bounded seven-day read probe and logs only
source statuses/error codes under `ASRE_DASHBOARD_SOURCE_PROBE`. This permits
readback after a scale-to-zero replica exits without keeping minimum replicas
at one or logging ticket/incident contents. A partial probe remains a warning,
not an application health success claim. Public startup does not run this
qualification probe.

The private operations environment and the feedback worker
are not changed. The new app scales from zero to one replica; open tabs can
keep it active. The modeled compute/request subtotal is USD 0.85/month before
shared grants for one active hour/day and 100,000 requests/month. Logs,
builds/storage, egress and vault operations are additional. The new resource
group is isolated for eventual approved cleanup; do not destroy the SRE root.
