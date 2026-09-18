# Azure Deployment Plan - Story 92

Status: Deployed

All producer stages were individually validated and what-if reviewed after
their predecessors existed, then provisioned successfully. The reviewed ZIP
was published and its hosted nine-surface acceptance run passed.

## Goal and approval

Deploy the four-domain/nine-surface producer into a new resource group, without
Foundry. User approved on 2026-09-14: new target, resource costs and scoped
managed-identity roles; public anonymous portal access; removal of local Foundry
source; one bounded 20-minute synthetic acceptance run including the 10-minute
baseline, followed by stop and drain. Existing Azure environments are out of scope.

## Target and recipe

- Subscription: `643febd3-3170-4110-80f9-fc8980b70a7a`
- Tenant: `d3f0c895-c524-4dde-9e90-422349767d24`
- Region: `centralus`
- Prefix: `iot-dv2-us92-0914`
- Resource group: `iot-dv2-us92-0914-rg`
- Storage: `iotdv2us920914sa`
- Recipe: existing staged Bicep and PowerShell, not AZD
- Hosting: Linux B2 capacity 1, Python 3.11, one Gunicorn worker
- Access: explicitly approved public anonymous portal; keyless, anonymous-disabled
  Storage with enforced NSP; observation-only query role
- Cost: additional B2 approximately USD 25.55/730 hours, plus usage

## Components and resource inventory

One producer app and system-assigned identity, one B2 plan, one Storage account/
blob state container, one workspace/two custom tables, one Direct DCR, one NSP/
profile/subscription inbound rule/Storage association, scoped Blob contributor
and ingestion assignments, one observation-readiness custom role and assignment.
One new resource group. No Foundry, model, Functions, add-on workspace/storage,
or add-on role assignments.

New RG absence and Storage name availability verified before preparing this file.
Current subscription/tenant confirmed; subscription permissions include all
management actions without exclusions. Central US B2 Linux is listed by
App Service. Microsoft.Quota registration was missing; user explicitly approved
registering only that provider before repeating quota checks.

Preparation completed: local Foundry directory and operational integration removed;
73 focused deployment/portability/packaging/readiness tests passed on Python
3.13.15 (target remains 3.11); all producer PowerShell scripts parsed; 223 support
manifest hashes matched. Producer behavior and isolated Bicep path preserved.
Environment configuration contains only Azure and producer sections.
Remaining quota/provider-aware/staged checks must pass at their applicable gates.

## Execution and verification gates

1. Remove approved Foundry source and integration surfaces; preserve producer behavior.
2. Validate changed sources, support integrity, tests, Bicep and producer-only ZIP.
3. Build new candidate and independently review exact SHA-256.
4. Validate/what-if BootstrapResourceGroup, then provision after approval.
5. In dependency order, validate/what-if/provision Plan, ProducerData, ProducerApp,
   Readiness. Dependent stages cannot be validated before predecessors exist.
6. Publish only the reviewed producer ZIP/hash.
7. Confirm live/ready, correct identity/storage/perimeter/roles, nine surfaces and
   absence of Foundry resources in the new group.
8. Run the approved bounded demonstration, verify run-scoped observation baseline
   (nine timestamps and 480-second span per surface within 600-second warmup),
   then stop and drain. Report negative audit-access uncertainty explicitly.
9. Post change/deployment evidence on Story 92; no commit requested.

## Validation proof

Validation was staged, not a claim that one early ARM validation covered all
dependencies. Bootstrap, Plan, ProducerData, ProducerApp and Readiness each passed
their own Validate/WhatIf gates before their Provision call.

- All validation checks pass:
  - Core validation: Azure CLI/auth, Bicep compilation, provider-level validation,
    and what-if for the current stage.
  - Optional lint diagnostics recorded without suppressing them.
  - Azure Policy validation through provider-level validation plus assignment review.
  - Static app-identity roles checked against Blob writes, DCR ingestion and
    observation-only queries.

| Check | Evidence | Result |
|---|---|---|
| Target identity | account show: approved subscription/tenant, interactive user | Pass |
| Management rights | subscription permissions actions `*`, no exclusions | Pass |
| Region/capacity | B2 Central US listed; B2 usage 1 + 1 <= 11 | Pass |
| Storage quota | Central US usage 2 + 1 <= 250 | Pass |
| Source tests | 73 focused deployment/portability/packaging/readiness tests | Pass |
| Support | Verify-Support.ps1: 223 hashes match | Pass |
| Templates | bootstrap, plan, deploy, readiness compiled | Pass with existing Bicep type/linter warnings |
| Package integrity | Independent review: 422 entries, 5,923,001 bytes; 223 support hashes and 171 replay fixtures verified | Approved |
| Producer-only ZIP | No Foundry, Functions, model/dependency artifacts, V1 gateway or prohibited secret/state filenames | Pass |
| Bootstrap validate | Deploy-Kit BootstrapResourceGroup Validate, 2026-09-14T22:14:50Z | Succeeded |
| Bootstrap what-if | Exactly one new RG; no existing resource modification | Pass |

Reviewed package SHA-256:
`3F7DA1EADCC2DF2DBACF06C581AA17A22F38978EDBC30C1148B6CBB9F024BCD5`.
Independent reviewer verdict APPROVE, high confidence for release integrity only.
Public controls are explicitly accepted risk; runtime and provider-stage checks
were subsequently completed as recorded below. Bootstrap workflow checks completed
under azure-validate before provisioning; dependent-stage validation continued
through the producer stage runner.

Additional validation proof, 2026-09-14:

- Core helper `validate-deployment.ps1 -Scope sub -Location centralus -Template
  build\us92-validation\bootstrap-resource-group.bicep -Parameters
  build\us92-validation\bootstrap.parameters.json -Subscription
  643febd3-3170-4110-80f9-fc8980b70a7a`: OVERALL PASS.
  Its textual create counter includes a legend line; actual reviewed what-if is
  one new resource group and no changes to existing resources.
- Applicable policy assignment listing reviewed; current bootstrap provider
  validation passed. Each later stage must independently validate its policy effects.
- `python -m pip wheel --no-deps --no-build-isolation --wheel-dir
  build\us92-validation\wheels . .\support`: both wheels built successfully,
  without dependency installation or isolated build dependency restore.

### Static role assignment verification

Verified from existing producer Bicep and runtime operations:
app system identity -> Storage Blob Data Contributor at the new Storage account;
app identity -> Monitoring Metrics Publisher at the new DCR for ingestion;
app identity -> custom query role at the new workspace with data-read condition
limited to EnergyObservations_CL. No local-development data role is required for
the approved hosted acceptance path. No Foundry identities or grants remain in
the deployment. Effective live roles and table-access behavior are post-deploy gates.

## Safety and rollback

Do not touch existing resource groups. Do not weaken perimeter, Storage or RBAC
to resolve failures. Do not create Foundry resources. No force-push, commit,
resource deletion or destructive state reset is approved. Leave failed resources
and stop for guidance if cleanup would be destructive.

T3 budget: 90 minutes, up to 140 parent tool calls, two agents, three correction
cycles. No factory. Track exposed telemetry only. Working ledgers stay in session
files; this deployment plan and directly related documentation belong to the kit.

## Deployment and acceptance result - 2026-09-14

Endpoint: https://iot-dv2-us92-0914-app.azurewebsites.net

| Stage | Actual evidence | Result |
|---|---|---|
| BootstrapResourceGroup | New RG create; 2026-09-14T22:21:45Z | Succeeded |
| Plan | Exact B2 capacity 1; 22:24:08Z | Succeeded |
| ProducerData | 11 creates reviewed, Storage Deny/Disabled and enforced NSP; no resource deletions | Succeeded |
| ProducerApp | 5 creates, 5 default-property modifications within new RG reviewed; public-access switch explicitly approved; final nested resources deployment 22:33:31Z | Succeeded |
| Readiness | 2 creates: new custom role plus conditioned assignment; 22:35:34Z | Succeeded |
| ProducerPublish | Deployment ID `96499e0c-ff3e-45dc-a33d-95f79d812f66`; RuntimeSuccessful, one successful instance, no failures | Succeeded |
| Hosted health | Live and ready both HTTP 200; Python 3.11, one worker, eight threads, Always On | Pass |
| Hosted portal/data routes | All nine pages and their corresponding data routes HTTP 200 | Pass |
| Synthetic baseline | Run `11779618-138d-4200-aebe-1354f16fcc35`; 600-second warmup, all nine surfaces queryable | Pass |
| Baseline thresholds | 100 samples, 10 distinct timestamps and 545.499637-second span per surface | Pass |
| Independent operator Logs query | 100 distinct observations per surface, 900 total; all marked synthetic and operational use prohibited | Pass |
| Final runtime | Stopped, running=false, pending=0, parked=0, worker_error=null | Pass |

### Live isolation and roles

The new group contains only six top-level producer resources: B2 plan, App
Service, Storage, Log Analytics, Direct DCR and NSP. Child resources include
the two tables, blob container, perimeter profile/rule/association and producer
roles. No Cognitive Services account, model, project, Functions app, add-on
workspace/storage or Foundry identities/roles were deployed.

The app identity has exactly the expected new-resource assignments:
Monitoring Metrics Publisher at the DCR; Storage Blob Data Contributor at Storage;
and the observation-readiness custom role at the workspace, retaining the
EnergyObservations_CL condition. Storage has shared keys and anonymous blobs
disabled, public network Disabled and default Deny. Its sole NSP association
reports Enforced/Succeeded.

The public anonymous portal is intentionally enabled under the user's explicit
approval. The demo is stopped, but the app and retained resources remain billable.
Existing producer and Foundry resource groups were not modified by this rollout.
The only approved subscription-level governance change was Microsoft.Quota
registration. No quota increase was requested.

### Residual diagnostic limitation

The initial observation query probe returned 403 during fresh RBAC propagation;
after propagation it allowed observation queries and all baseline checks passed.
The final audit probe reports `AuditNoVisibleRows`, `verified=false` and
`audit_denied=false`. Do not interpret this as verified audit denial or a proven
leak. Effective negative-access evidence remains inconclusive; no role was
broadened to change that result.

### Source, evidence and verdict

Local Foundry source and its operational deployment/config/export/doc integration
were removed with explicit approval. Producer-only tests and documentation were
updated; existing producer behavior was retained. The source folder remains
untracked and no Git commit was created.

Session evidence files:

- `us92-producer-acceptance.json`: nine page/data checks, RunId, measured baseline,
  final stopped/drained state and APPROVE verdict.
- `us92-producer-logs.json`: independent operator run-scoped query results.
- `domainsV2-deploy-run.json`: goal/outcome, approval boundaries and retrospective.

Stage output logs are in `build\us92-validation`; the approved ZIP remains
`build\us92-producer-20260914\domains-v2.zip`. Environment-specific configuration
and build output are ignored by Git.

Verdict: **APPROVE for producer deployment and synthetic-log acceptance**, high
confidence from independent package review plus hosted objective checks.
This is not a comprehensive security audit or verified negative audit-access
verdict. Story 92 receives an evidence summary, not automatic state closure.
