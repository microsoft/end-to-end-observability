# Approved foundation: GPT-5.6-luna

This is the first infrastructure stage of Story 93, not the complete nine-agent
release. The user approved the foundation package with GPT-5.6-luna after
reviewing costs, identities, permissions and network restrictions.
**Status: deployed and verified on 2026-09-15; full application still incomplete.**

## Approved scope

All resources are new and in the approved resource group; regional services use
Central US. The Azure-generated smart-alert rule is a global control-plane
resource, not a change to model data-zone processing. Names are
deterministic from the environment, subscription and resource group; deployment
identifiers and operator identity are supplied through ignored azd environment
state, not hard-coded in IaC. The existing Domains V2 deployment and older
Foundry resources must not be changed.

| Component | Configuration |
| --- | --- |
| Foundry | AIServices S0 account, project, system identities; Basic Agent Setup |
| Reasoning deployment | `gpt-5-6-luna`: GPT-5.6-luna `2026-07-09`, DataZoneStandard, 50 units |
| Embedding deployment | `text-embedding-3-small`: version `1`, DataZoneStandard, 10 units |
| Search | Basic, one replica/partition; free semantic tier during foundation |
| SQL | Basic, 5 DTU, 2 GiB, local backup storage, Entra-only administrator |
| Evidence | StorageV2 Hot LRS, private container, versioning, seven-day soft delete |
| Monitoring | LAW Analytics, 30 days, 0.05 GB daily cap; workspace-based App Insights |

Both models use `NoAutoUpgrade`. US data-zone inference is approved; this is not
a guarantee that inference executes only in Central US. No native tools or
prompt agents are registered by this stage. The existing agent manifests and
runtime defaults are not silently rewritten; binding all nine to the approved
deployment belongs to the subsequent service integration stage.

The operator gets Foundry User **at the project only**, separately approved
Cognitive Services OpenAI User **at the new account only**, and is the new SQL
server's Entra administrator. No runtime identities, producer read permissions,
SQL application users, Search data roles or Blob data roles are granted.

Foundry and Search are public but require Entra authentication; key/local auth
is disabled. SQL and evidence Storage have public access disabled, no bypass
and no firewall allow rules. There are deliberately no private endpoints yet.
**Those data services cannot serve application clients until the service
networking stage is approved and implemented.** An Online database or a created
container does not prove connectivity or managed-identity access.

Blob soft delete/versioning is recovery protection, not WORM retention, an
evidence-retention receipt, publication eligibility or completed erasure.
App Insights is Entra-only with IP masking; application telemetry identity and
connection wiring remain part of the service stage.

## Cost authorization

**Current approval: USD 500/month** foundation operating allowance, including **at most USD 5 total**
for bounded synthetic smoke tests. This is not a subscription billing cap.
The LAW daily ingestion cap is an additional operational guardrail, not a
guaranteed financial limit. No automated model calls are enabled.

At 730 hours/month, Search is USD 73.73 and SQL is approximately USD 4.90:
**USD 78.63/month standing**, before tokens, Storage and monitoring. Retail
estimates exclude negotiated discounts/taxes and are not billing guarantees.

**Pre-deployment correction:** that initial figure omitted existing subscription
Defender plans. SQL Defender adds USD 15/month and Defender for Storage V2 adds
USD 0.0134/hour (USD 9.782 at 730 hours), bringing the standing subtotal to
approximately **USD 103.41/month**. CSPM is additionally USD 0.007 per eligible
resource-hour after its remaining trial; Defender for AI is USD 0.0008 per
1,000 analyzed tokens. Malware scanning, when enabled, is USD 0.15/GB.
The original USD 100 allowance could not cover this footprint. A revised USD 150
allowance was requested before provisioning; existing protections must not
be disabled to fit the original estimate.

The user subsequently authorized **USD 500/month**, superseding the initial
USD 100 and proposed USD 150. Resource scope and the USD 5 total smoke-test
bound are unchanged. This is not authorization to deploy the unfinished services.

Central US retail token meters retrieved on 2026-09-15:

| GPT-5.6 model | Uncached short-context input / 1M | Output / 1M |
| --- | ---: | ---: |
| Luna (approved) | USD 0.22 | USD 1.32 |
| Terra | USD 2.20 | USD 13.20 |
| Sol | USD 4.40 | USD 22.00 |

Long-context and cache meters differ. The previous GPT-4o usage scenario must
not be reused for the later full-service budget.

## IaC and provenance

`azure.yaml` deliberately has no application services or deployment hooks.
`infra/main.bicep` creates the group and composes the foundation. Parameter
substitution uses `infra/main.parameters.json` and the ignored azd environment.
No SQL grant/migration hooks run against these network-locked resources.

The AVM pattern index was checked first. The AI platform baseline is an
ML-workspace/network/compute footprint, not this approved package. The
AI Foundry pattern targets Standard Agent Services and hard-codes an
AzureServices account bypass. Resource-level composition gives explicit control
over this Basic Setup, only explicitly approved operator permissions and no
network bypass.

Pinned public AVM resource modules:

| Module | Version |
| --- | --- |
| `avm/res/cognitive-services/account` | 0.19.0 |
| `avm/res/search/search-service` | 0.13.0 |
| `avm/res/storage/storage-account` | 0.33.0 |
| `avm/res/sql/server` | 0.22.1 |
| `avm/res/operational-insights/workspace` | 0.16.1 |
| `avm/res/insights/component` | 0.8.0 |

The project and project-scoped operator assignment follow the official
[Foundry starter](https://github.com/Azure-Samples/azd-ai-starter-basic/tree/5bf59a056c41ff37801dea334ba6a141e2dc5f67).
No hosted agents, ACR, capability hosts or additional permissions are copied.
AVM usage telemetry is disabled; AVM internal secure outputs are not surfaced by
the root deployment. Never print/export child-module credential outputs.

Sources:

- [AVM resource index](https://github.com/Azure/Azure-Verified-Modules/blob/main/docs/static/module-indexes/BicepResourceModules.csv)
- [AVM pattern index](https://github.com/Azure/Azure-Verified-Modules/blob/main/docs/static/module-indexes/BicepPatternModules.csv)
- [AVM module source](https://github.com/Azure/bicep-registry-modules/tree/main/avm/res)
- [Responses regions and models](https://learn.microsoft.com/azure/foundry/openai/how-to/responses)
- [Agent Service regional availability](https://learn.microsoft.com/azure/foundry/agents/concepts/limits-quotas-regions)
- [Azure retail prices API](https://prices.azure.com/api/retail/prices)

The current regional documentation includes Central US, despite the older
starter allow-list omitting it. The pre-deployment subscription catalog reported GA,
Agents V2/Responses support and 333 unused DataZoneStandard units for Luna.
These checks do not reserve regional capacity.

## Execution and model probe

The initial infrastructure deployment succeeded as
`agentic-itops-us93-1789490133` in 3 minutes 45 seconds. Live reads confirmed
the model versions/capacities, SQL Online/5 DTU, Search 1x1, evidence container,
Entra-only settings and network restrictions.

The initial Luna call completed with 15 input and 7 output tokens. The embedding
probe returned 404 at the project endpoint. The documented account OpenAI
endpoint then returned 401 PermissionDenied for the embeddings data action.
The operator's existing account access was checked before requesting the
additional OpenAI User role; inherited Owner does not grant that data action.
The approved assignment uses the same deterministic name as the AVM definition.
Only that permission was applied, avoiding full-resource PUTs merely to add a
role. The remaining full-template what-if contains default/metadata differences;
it is not a zero-drift proof.

The final combined probe passed at 13:00:46 EDT: Luna completed with the expected
readiness marker and embeddings produced one finite, nonzero vector with exactly
1,536 dimensions. Successful calls across the probe history report 50 tokens;
the rejected 404/401 attempts are retained separately, not assigned fabricated
billing values. This is operator model-access proof, not all-nine acceptance.

Azure also created a default failure-anomaly rule and role-notification action
group for Application Insights, plus SQL's system `master` database. At the
user's explicit request, only the new failure-anomaly rule was changed to
Disabled and independently read back at 13:15 EDT. Its disabled state is now
declared in IaC; the action group and inherited Defender protections remain
unchanged. No Foundry schedules or application services were activated.

Use project-scoped Responses for the agent path and the account OpenAI endpoint
for embeddings. A future embedding worker will need its own approved data-plane
identity/role; the operator grant is not a production credential fallback.

The reproducible probe uses explicit Azure CLI credentials, fixed synthetic
inputs, two model calls maximum, zero automatic retries, a 2,048-token response
limit and exclusive per-attempt receipts. It checks completed Responses and
finite, nonzero 1,536-dimensional vectors. It does not register or invoke agents.
Eighteen local regressions cover routing, mismatched endpoints, receipt replay
and invalid vectors.

From `agentic-itops`, after separate smoke-cost authorization:

```powershell
$env:AZURE_DEV_USER_AGENT = 'microsoft_foundry_skill'
$env:AZD_CONFIG_DIR = Join-Path (Get-Location).Path '.azure\agentic-itops-us93\azd-cli'
$values = azd env get-values -o json | ConvertFrom-Json
& .\.venv\Scripts\python.exe .\scripts\smoke_foundation.py `
  --project-endpoint $values.AZURE_AI_PROJECT_ENDPOINT `
  --openai-endpoint $values.AZURE_OPENAI_ENDPOINT `
  --tenant-id $values.AZURE_TENANT_ID `
  --output .\.build\foundation\smoke-new-attempt.json
```

The isolated azd configuration delegates to the already authenticated Azure CLI
without changing global user settings. Root deployment outputs are kept in
ignored azd state. The account endpoint was also verified against the deployed
account's endpoint metadata; the updated root template now exports it.

## Foundation acceptance and remaining gates

Require Bicep compilation, approved-scope checks, ARM validation/what-if, actual
resource/model provisioning state, exact SKUs/versions/capacities, auth/network
settings and scoped role verification. Save deployment outputs only in ignored
state and run bounded synthetic model smoke tests with explicit operator
credentials, no producer data and no automatic retries.

SQL/Blob data-plane validation, source-table MI isolation, application hosting,
real tool backends, all-nine version registration/execution, review publication,
revocation/erasure and approved detector schedules remain release gates.
Before activating the runtime, bind all nine agents to the Luna deployment and
recalculate model budgets/settings rather than reusing the legacy GPT-4o defaults.
Foundation success must not close Story 93.
