# New subscription deployment

This guide is for a **copied Domains V2 folder**. You can place this folder in
another repository and use it as that repository root. The workflow is stage
based on purpose: do not validate or provision later stages before earlier
dependencies exist.

No script in this kit registers providers, changes governance, expands IAM
beyond the shipped templates, writes exemption tags, opens broad Internet
storage access, or bypasses approval switches.

## 1. Prerequisites

### Recommended handoff

If you are moving this kit to another repository or machine, export a clean
copy first:

```powershell
.\scripts\Export-Kit.ps1 -OutputDirectory ..\domainsV2-portable
```

That export re-runs `.\scripts\Verify-Support.ps1` and excludes `.local`,
`.venv`, compiled infra JSON, ZIPs, and local operator config.

### Local prerequisites

- PowerShell 7
- Python 3.11+
- Azure CLI with Bicep support already installed
- A copied `environment.local.json` based on `environment.example.json`

Install the local Python environment from the copied folder root:

```powershell
python -m venv .venv
. .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Optional local validation:

```powershell
python -m pip install -e ".[validation]"
python -m unittest discover -s tests -v
```

### Azure permissions

The deployer needs ordinary resource deployment rights for the selected
subscription/resource groups plus explicit ability to create the readiness
custom role definition and assignment:

- `Microsoft.Authorization/roleDefinitions/write`
- `Microsoft.Authorization/roleAssignments/write`

The readiness assignment is conditioned to `EnergyObservations_CL` only. It is
not approval to widen the app to `EnergyInjectionAudit_CL` or arbitrary Log
Analytics tables.

Conditioned Log Analytics permissions can take about 15 minutes to propagate.
`AuditNoVisibleRows` is **not** verified denial by itself.

### Manual provider registration only

The kit checks provider state and stops if a required namespace is not already
registered. Review governance first, then ask an approved admin to inspect or
register only the missing providers.

Required for producer stages:

- `Microsoft.Web`
- `Microsoft.Storage`
- `Microsoft.OperationalInsights`
- `Microsoft.Insights`
- `Microsoft.Network`
- `Microsoft.Authorization`

Inspect a provider:

Complete the configuration and sign-in in section 2 before running these commands.

```powershell
az provider show --subscription $environment.azure.subscriptionId --namespace Microsoft.Web --query registrationState --output tsv
```

If an approved admin decides registration is allowed:

```powershell
az provider register --subscription $environment.azure.subscriptionId --namespace Microsoft.Web --wait
```

## 2. Environment config

Copy and edit the example:

```powershell
Copy-Item .\environment.example.json .\environment.local.json
```

Important fields:

- `azure.subscriptionId`, `azure.tenantId`, `azure.location`
- `producer.prefix`, `producer.storageName`
- `producer.approvedIpCidr` when `producer.unrestrictedPortal=false`
- `producer.unrestrictedPortal` only after explicit risk acceptance
- `producer.securityPerimeter` must be the JSON boolean `true`; the runner rejects
  private-network mode and string booleans
- `producer.approvedPackageSha256` stays blank until an independent review
  approves a newly built ZIP

Current validated/allowed location is `centralus`. Do not promise or assume
other regions until separately validated.

Replace the zero GUIDs, example resource names and documentation-only IP address
before any Azure stage. Use globally unique storage/app names for a new environment.
Do not select names belonging to an existing deployment you intend to preserve.
Keep booleans unquoted. If using unrestricted access, explicitly accept that anyone
with the URL can operate the demo and generate billable logs.

Sign in to the chosen tenant and select the subscription:

```powershell
$environment = Get-Content .\environment.local.json -Raw | ConvertFrom-Json
az login --tenant $environment.azure.tenantId
az account set --subscription $environment.azure.subscriptionId
az account show --query "{subscription:id,tenant:tenantId,state:state}" --output table
```

Confirm permissions, governance and Central US B2 availability before provisioning.
A policy or quota failure is
a blocker to resolve with the subscription administrator, not a reason to weaken
networking or identity.

## 3. Stage runner

Use `.\scripts\Deploy-Kit.ps1`. It supports only one stage per invocation:

| Stage | Allowed actions | Notes |
| --- | --- | --- |
| `BootstrapResourceGroup` | `WhatIf`, `Validate`, `Provision` | Creates only `<producer.prefix>-rg` via subscription-scope Bicep |
| `Plan` | `WhatIf`, `Validate`, `Provision` | Deploys `infra\plan.bicep` into the new RG; exact B2 plan only |
| `ProducerData` | `WhatIf`, `Validate`, `Provision` | Uses `UseExistingPlan=true`, `DeferApplication=true`, `SecurityPerimeter=true`, `PublicStorage=false` |
| `ProducerApp` | `WhatIf`, `Validate`, `Provision` | Uses `UseExistingPlan=true`, `DeferApplication=false`, `ResolveExistingDataEndpoints=true` |
| `Readiness` | `WhatIf`, `Validate`, `Provision` | Deploys the observation-only custom role and assignment |
| `ProducerPackage` | `Package` | Builds a new ZIP candidate locally |
| `ProducerPublish` | `Publish` | Publishes only an approved ZIP/hash pair |

`WhatIf` is the default action. `Provision` always requires
`-ApproveResourceCostsAndRoleAssignments`. `Publish` always requires
`-ApproveDeployment`. If `producer.unrestrictedPortal=true`, the `ProducerApp`
`Provision` step also requires `-ApprovePublicAnonymousAccess`.
Provision repeats provider validation for that exact stage and configuration
before creating resources. What-if review and approvals are still operator responsibilities.
Local packaging does not require sign-in or populated tenant/subscription GUIDs.

## 4. Required deployment sequence

The successful deployment sequence that this runner codifies is:

1. create the new resource group
2. deploy the exact B2 plan with `infra\plan.bicep`
3. deploy producer data/perimeter with `UseExistingPlan=true`,
   `SecurityPerimeter=true`, `PublicStorage=false`, `DeferApplication=true`
4. deploy the producer application with `UseExistingPlan=true`,
   `DeferApplication=false`, `ResolveExistingDataEndpoints=true`
5. deploy readiness separately
6. publish the approved producer ZIP

The app stage must resolve the **existing** storage account, DCR, and workspace
through management-plane ARM reads. Plain composed values are not sufficient for
the validated path.

## 5. Exact commands from the copied-folder root

### Bootstrap the resource group

```powershell
.\scripts\Deploy-Kit.ps1 -Stage BootstrapResourceGroup -Action WhatIf
.\scripts\Deploy-Kit.ps1 -Stage BootstrapResourceGroup -Action Validate
.\scripts\Deploy-Kit.ps1 -Stage BootstrapResourceGroup -Action Provision -ApproveResourceCostsAndRoleAssignments
```

### Deploy the exact B2 plan

```powershell
.\scripts\Deploy-Kit.ps1 -Stage Plan -Action WhatIf
.\scripts\Deploy-Kit.ps1 -Stage Plan -Action Validate
.\scripts\Deploy-Kit.ps1 -Stage Plan -Action Provision -ApproveResourceCostsAndRoleAssignments
```

### Deploy producer data/perimeter first

```powershell
.\scripts\Deploy-Kit.ps1 -Stage ProducerData -Action WhatIf
.\scripts\Deploy-Kit.ps1 -Stage ProducerData -Action Validate
.\scripts\Deploy-Kit.ps1 -Stage ProducerData -Action Provision -ApproveResourceCostsAndRoleAssignments
```

This stage keeps the application deferred. It does **not** claim that the later
application stage is already valid.

### Deploy the producer application second

```powershell
.\scripts\Deploy-Kit.ps1 -Stage ProducerApp -Action WhatIf
.\scripts\Deploy-Kit.ps1 -Stage ProducerApp -Action Validate
.\scripts\Deploy-Kit.ps1 -Stage ProducerApp -Action Provision -ApproveResourceCostsAndRoleAssignments
```

If `producer.unrestrictedPortal=true`, append `-ApprovePublicAnonymousAccess`
to the `Provision` command after the risk is explicitly accepted. Anonymous
portal access increases billable log exposure and must never become a silent
default.

### Deploy readiness separately

```powershell
.\scripts\Deploy-Kit.ps1 -Stage Readiness -Action WhatIf
.\scripts\Deploy-Kit.ps1 -Stage Readiness -Action Validate
.\scripts\Deploy-Kit.ps1 -Stage Readiness -Action Provision -ApproveResourceCostsAndRoleAssignments
```

### Build and approve the producer ZIP

```powershell
.\scripts\Deploy-Kit.ps1 -Stage ProducerPackage -Action Package
```

That stage wraps `.\scripts\package.ps1 -OutputDirectory <configured-folder>`.
The package contract is unchanged:

- stage directory: `<output>\stage`
- ZIP path: `<output>\domains-v2.zip`
- stdout JSON fields: `entries`, `bytes`, `sha256`

The reported SHA-256 is an integrity value only. Do not reuse an older
deployment hash. After independent review approves that exact file, copy the
approved SHA-256 into `producer.approvedPackageSha256`.

### Publish the approved producer ZIP

```powershell
.\scripts\Deploy-Kit.ps1 -Stage ProducerPublish -Action Publish -ApproveDeployment
```

The producer runner publishes to `<producer.prefix>-app` in `<producer.prefix>-rg`
and refuses to continue if the ZIP hash does not match
`producer.approvedPackageSha256`.

## 6. Safe local compilation

Do not overwrite checked-in `infra\*.json`. Compile into the ignored `build\`
tree instead:

```powershell
New-Item -ItemType Directory -Force .\build\compiled | Out-Null
az bicep build --file .\infra\bootstrap-resource-group.bicep --outfile .\build\compiled\bootstrap-resource-group.json --no-restore
az bicep build --file .\infra\plan.bicep --outfile .\build\compiled\plan.json --no-restore
az bicep build --file .\infra\deploy.bicep --outfile .\build\compiled\deploy.json --no-restore
az bicep build --file .\infra\readiness.bicep --outfile .\build\compiled\readiness.json --no-restore
```

This is syntax compilation only. It is not a substitute for provider-aware
validation, what-if review, or approval gates.
The producer templates use local modules only. Python installation requires its declared
dependencies from a configured package index.

## 7. Open and operate the demo

After publication, open `https://<producer.prefix>-app.azurewebsites.net`.
The launcher links all five monitoring and four inventory surfaces.
For an IP-restricted portal, connect from the configured address; do not broaden
access simply to work around a VPN or client-network mismatch.

Choose the participating portals and a total duration of 60 or 120 minutes,
then click **Prepare demo** once. Wait through the ten-minute normal baseline
and for the queryable-baseline indication. In any participating portal, choose
an anomaly and click **Inject anomaly**; **Recover normal** clears it early.
The configured anomaly duration also recovers automatically. **Stop session**
ends generation while preserving queued delivery. Wait for pending and parked
counts to reach zero; investigate errors rather than starting another overlapping run.

In the producer workspace's Logs view, inspect the selected run:

```kusto
EnergyObservations_CL
| where TimeGenerated > ago(2h)
| summarize Observations=count(), LastSeen=max(TimeGenerated) by RunId, Domain, Surface
| order by LastSeen desc
```

Compare an observation's `EventId`, `Payload` and `Context` with the portal's
log-details view. The control audit is separate in `EnergyInjectionAudit_CL`;
it is not observation input. This producer-only deployment includes no
agent/model authorization, inference, or detector resources.

## 8. Runtime and cost boundaries

- Public HTTPS and managed identity are required.
- The validated path uses enforced NSP on producer storage.
  No private endpoints are required for that path.
- Never publish or restart an active demo environment. The publish scripts rely
  on the existing explicit approval switches and do not auto-stop or auto-start
  a session for you.
- Demo duration is at most 120 minutes, including the 10-minute warm-up.
- Start is manual. Stop drains delivery; it does not remove resources or log
  charges.
- Resource costs continue after Stop.
- No enabled anomaly-alert or SRE dispatch automation is included in this phase.

## 9. Historical evidence

On 2026-09-14, a separate Central US deployment succeeded using the same staged
sequence documented here: new RG, exact B2 plan, producer data/perimeter,
producer app with resolved existing endpoints, separate readiness, approved ZIP
publish. That historical run proves the producer order,
not your new environment's approval, names, hashes, or permissions.
