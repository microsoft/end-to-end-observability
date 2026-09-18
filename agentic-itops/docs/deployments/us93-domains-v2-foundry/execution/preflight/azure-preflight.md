# Story 93 — read-only Azure/resource/pricing preflight

**Observed:** 2026-09-14 evening America/New_York / 2026-09-15 UTC. **Decision: BLOCKED for deployment; conditionally viable Central US pilot.**

Subscription `643febd3-3170-4110-80f9-fc8980b70a7a`, tenant `d3f0c895-c524-4dde-9e90-422349767d24`, account name `ME-MngEnvMCAP977190-shumwilkin-1`, state Enabled were verified against the supplied values before quota checks. Proposed new resource group `agentic-itops-us93-rg` **does not exist** (`az group exists` returned false). Nothing was created, registered, granted, deployed or invoked. Existing Domains V2 and legacy Foundry remain unchanged. No source, deployment plan, Logs tables, producer portal, datasets or existing Foundry data-plane were accessed or changed.

Read design inputs: `docs\deployments\us93-domains-v2-foundry\us93-outcome-design\outputs\agentic-itops-outcomes\docs\cost-estimation.md` and `solution-design.md`. All nine agent YAML manifests retain `model: gpt-4o`; model version/SKU is not pinned there. No embedding dimensions were found in the inspected JSON/YAML/Bicep/Terraform and Python searches; an actual Search-index/model dimension contract remains to be implemented and verified.

## 1. Deployment gates, not remediation actions

1. **Approval remains absent** for spending, resource creation, identity/app registration, grants and provider registration. The USD1,000/month figure is a planning ceiling, not authorization.
2. **Microsoft.Search, Microsoft.Sql and Microsoft.App are NotRegistered.** Search and SQL cannot be treated as deployable. Current Functions documentation states that portal/CLI creation enforces Microsoft.App registration even when VNet integration is not initially used. Request explicit approval before registration; do not use automatic registration.
3. **Flex subscription memory quota is unresolved.** The installed CLI has no `functionapp list-flexconsumption-quotas` command. Microsoft.Web quota and management-plane usage responses do not expose a Flex memory entry. Official default is not proof of this subscription's effective quota.
4. **Exact model version, deployment type and capacity need approval and compatibility proof.** `gpt-4o` 2024-11-20 is a defensible same-model candidate, not a substitution or selected deployment. Lifecycle, residency, Responses/agent support, transport and SDK bindings remain gates.
5. **SQL/Search subscription quota and allocation are not established.** Regional catalog support and documented limits do not override unregistered-provider errors or guarantee capacity.
6. **Entra authentication is mandatory, not function keys alone.** Review UI registration, application roles/scopes and allowed principals require approval. MCP has its own registration/audience requirements; do not reuse the review UI registration by assumption. Prove both directions of managed A2A/MCP authentication before deployment.
7. **Observation-only access is a verified role pattern, not a verified new identity.** The future worker must have no additive broad table access. Positive and negative identity tests occur only after approved provisioning.
8. **The USD452.96 scenario below is a conditional, scope-priced pilot, not a final approved all-in deployment quote.** A different gateway/runtime, hosted-agent compute, paid evaluation, paid enrichment or additional data transfer must be priced before enabling it. Budget alerts are not hard caps.

## 2. Regional resource availability, counts and quotas

Mandatory `azure-quotas` skill was loaded. `az extension show --name quota` verified installed version **1.0.0**; no reinstall occurred. `az quota list` was used first with explicit subscription and regional provider scope. No quota requests or registration actions were attempted.

| Central US item | Observed current | Proposed increment / resulting count | Evidence and qualification |
|---|---:|---:|---|
| AIServices accounts | 1 | +1 = 2 | Cognitive usage `AIServices.S0.AccountCount`: 1/100. Overall Cognitive `AccountCount`: 1/200. Separate OpenAI-kind counter 0/30 is **not** the AIServices limit. |
| Foundry account projects | 1 | +1 = 2 | ARM resource count; independent project quota not returned/established. |
| Search services | 0 | +1 = 1 | No Search resources subscription-wide. Quota API reports provider must be registered. Published Basic default: 16 services per region; effective subscription quota **unverified**. |
| Linux B1 instances | 0 | +1 = 1 | `az quota`: B1 limit10, usage0; 9 remaining after planned instance. B1 Linux Central US appears in `az appservice list-locations`. |
| App Service plans, all SKUs | 2 | +4 = 6 | Existing B2 capacity1 and FC1 capacity0. Add one B1 plan and **three FC1 plans**, not one shared Flex plan. |
| Web sites / Function apps combined | 2 | +4 = 6 | Resource count includes existing producer and existing function app. No ownership/reuse is assumed. |
| Flex apps | 1 existing FC1 plan | +3 = 4 FC1 plans | Existing plan capacity0 is not authoritative live memory utilization. CLI confirms Central US Flex availability. Subscription-effective Flex quota unknown. |
| SQL logical servers / databases | 0 / 0 | +1 / +1 | No SQL resources subscription-wide. `az quota list`: BadRequest. Service-specific usages: SubscriptionNotFound while provider is NotRegistered; do not infer no limit. |
| GPv2 storage accounts | 2 | +4 = 6 | One consumer evidence account + three isolated Function host/deployment accounts. Storage quota250, usage2; 244 remaining after plan. Sharing storage could reduce count but is not assumed for isolation. |
| Consumer LAW | 2 existing LAW resources | +1 = 3 | ARM count; workspace creation/allocation quota not established. |
| Application Insights | 1 | +1 = 2 | One new shared consumer component with cloud-role separation is the estimate assumption; a component per app would change count, not automatically multiply ingestion. |

ARM provider metadata lists **Central US** for Cognitive accounts and child projects, Search services, SQL servers, storage accounts, Log Analytics workspaces and Application Insights components. This is resource-type availability, **not** a test of selected API/SDK, SKU provisioning capacity or Foundry Agent Service runtime features.

Provider states verified Registered: Microsoft.CognitiveServices, Microsoft.Quota, Microsoft.Web, Microsoft.ManagedIdentity, Microsoft.Storage, Microsoft.OperationalInsights, microsoft.insights and Microsoft.Authorization. States verified NotRegistered: Microsoft.Search, Microsoft.Sql, Microsoft.App.

### Quota details and unsupported results

- CognitiveServices `az quota list`: **BadRequest**; fallback `az cognitiveservices usage list --location centralus` succeeded.
- Search quota discovery: **NotFound / SubscriptionNotFound**, explicitly stating Azure Search must be registered.
- SQL quota discovery: **BadRequest**; service-specific GET usages `api-version=2023-08-01` returned SubscriptionNotFound. This is a deployment blocker, not evidence of exhausted quota or an absent subscription.
- Web quota discovery and B1 usage succeeded. The older Web usage API returned `limit:-1` for some family counters; those are **not interpreted as unlimited**. The newer API returned instance limits but still no Flex memory quota.
- Storage quota discovery and usage succeeded.
- Functions [current hosting documentation](https://learn.microsoft.com/en-us/azure/azure-functions/flex-consumption-plan) lists a **default 250 cores / 512,000 MB** shared region/subscription quota. A proposed active instance of 2,048 MB for each of three apps is 6,144 MB / 3 typical cores, but multiple independently scaling function groups can consume more. This is a sizing scenario, **not** measured available headroom. Maximum scale controls are applied per group; always-ready instances are additional.
- No deployment was attempted to establish allocation. **Quota headroom never guarantees allocation.**

## 3. Model, embedding and residency evidence

Saved regional management-plane model catalog: `model-catalog-evidence.json`. It includes OpenAI and AIServices kinds; use the AIServices record for the proposed Foundry account.

| Model/version | Current catalog lifecycle | Catalog deployment types relevant to PAYG | Current official retirement |
|---|---|---|---|
| gpt-4o / 2024-05-13 | Deprecating | GlobalStandard, DataZoneStandard; no regional Standard in this record | 2026-10-01 |
| gpt-4o / 2024-08-06 | Deprecating | Standard, GlobalStandard, DataZoneStandard | 2027-04-14 |
| gpt-4o / 2024-11-20 | Legacy | Standard, GlobalStandard, DataZoneStandard | 2027-04-14 |
| text-embedding-3-small / 1 | GenerallyAvailable | GlobalStandard, DataZoneStandard; **no Standard** in this Central US record | 2028-02-09 |

Other catalog SKUs (batch, provisioned and fine-tuned) are not pilot choices or free alternatives. No model change was performed. Public [retirement schedule](https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-retirement-schedule), updated 2026-09-14, was retrieved directly. An initial search synthesis contained stale retirement dates; those are deliberately excluded. Direct documentation and management-plane evidence govern.

[Lifecycle policy](https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-retirements) says API `Deprecating` means deprecated but still serving, blocked to **new customers**; existing-customer eligibility is subscription-and-exact-version history, not tenant membership. That history was not inspected and is not assumed. API `Legacy` permits new deployments until deprecation. Thus 2024-11-20 merits approval consideration without changing the manifest's gpt-4o intent. Pin version and explicitly assess upgrade behavior; Microsoft's recommended retirement replacement is **not** permission to silently migrate this design.

Current Cognitive quota usage:

| Quota name | Used / limit, API unit Count | Meaning |
|---|---:|---|
| OpenAI.Standard.gpt-4o | 0 / 150 | Regional standard pool |
| OpenAI.DataZoneStandard.gpt-4o | 0 / 300 | Data-zone standard pool |
| OpenAI.GlobalStandard.gpt-4o | 0 / 450 | Global standard pool |
| OpenAI.GlobalStandard.text-embedding-3-small | 0 / 1,000 | Global embedding pool |
| OpenAI.DataZoneStandard.text-embedding-3-small | 0 / 1,000 | Data-zone embedding pool |

For gpt-4o 2024-11-20, each of those three catalog SKUs exposes rate-limit metadata **1,000 tokens/60s and 1 request/10s** per capacity unit; catalog default10, maximum1,000,000, minimum/step null. These are capacity-unit metadata, **not a configured deployment** or a recommended TPM reservation. Regional standard150 units corresponds to a nominal150,000-TPM quota pool under this mapping, data-zone300 to300,000 and global450 to450,000. **Requested capacity remains TBD**, bounded by subscription quota, actual allocation and measured workflow demand. Do not confuse the catalog maximum with subscription headroom.

Embedding candidate for approval: `text-embedding-3-small` v1, explicit **1,536 dimensions**. [Embedding skill documentation](https://learn.microsoft.com/en-us/azure/search/cognitive-search-skill-azure-openai-embedding) permits dimensions1–1,536 and requires the Search vector field to match. This is a proposed contract, not evidence the current index already matches.

[Deployment-type residency documentation](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/deployment-types): at-rest storage remains in the designated Azure geography; global inferencing is not Central-US-only, and DataZoneStandard constrains processing to the relevant US data zone, not Central US specifically. The Central US embedding catalog lacks regional Standard, so a **Central-US-only processing requirement would block this embedding choice**. Public authenticated endpoints are not approval of global processing. The priced conservative scenario uses data-zone chat and embedding rates pending explicit residency approval.

## 4. Service compatibility

### Search Basic

Central US [region matrix](https://learn.microsoft.com/en-us/azure/search/search-region-support) explicitly supports semantic ranker and does not carry the current capacity-blocked footnote. Basic supports vector fields, hybrid queries and semantic configurations. [Current limits](https://learn.microsoft.com/en-us/azure/search/search-limits-quotas-capacity) give new Central US Basic partitions15GB storage, vector-field dimension maximum4,096, Basic100 fields/index and15 indexes for newly created services. Validate actual vector memory usage before indexing.

One replica × one partition = **one billable search unit**. One replica has **no query/indexing SLA**; two replicas are required for query SLA, three for query-and-indexing SLA. The requested one-replica sandbox remains a candidate; no concrete incompatibility justifies upgrading its tier here. Semantic queries are explicitly priced separately. No agentic-retrieval, enrichment or Search-hosted model use is silently included.

### Functions, MCP and A2A

Flex is Linux-only and requires **one app per plan**, hence three independent FC1 plans and host/deployment storage. Assume 2GB instances, on demand, **zero always-ready**, no Durable Task Scheduler, no Azure Files mount, no broker and a SQL-backed work outbox. Host-storage Blob and Queue allowance is explicitly charged below; different triggers/storage bindings require re-quotation.

The [MCP binding documentation](https://learn.microsoft.com/en-us/azure/azure-functions/functions-bindings-mcp) documents system-key authorization by default and the possibility of built-in identity authorization. A key is not Entra authentication. Setting webhook authorization to Anonymous is only acceptable behind **proven fail-closed Entra enforcement**, never as a public unauthenticated workaround.

[App Service MCP authentication documentation](https://learn.microsoft.com/en-us/azure/app-service/configure-authentication-mcp) requires a registration unique to the MCP server. Entra does not provide Dynamic Client Registration; clients must be preconfigured, audiences/scopes/allowed applications configured, and consent assessed. None was configured.

No concrete evidence currently forces a separate MCP/A2A host. First prove streaming/transport, supported Functions binding, both authentication directions, caller correlation, replay handling, bounded intermediate validation and SDK/Responses agent-version behavior. If that fails, the conditional same-region **one additional Linux B1 instance** would add **USD13.14/month** standing compute using the verified meter, but it is **not a validated replacement architecture**. Runtime load, additional app registration, storage/telemetry and billing must still be reviewed. A gateway or managed hosted-agent runtime is never assumed free.

### SQL Basic / consumer telemetry

[SQL single-database DTU limits](https://learn.microsoft.com/en-us/azure/azure-sql/database/resource-limits-dtu-single-databases?view=azuresql) establish Basic5DTU and2GB maximum storage; this is a small metadata-only pilot, not unrestricted event storage. Keep large evidence/response artifacts in Blob. Measure contention, outbox growth and retention before accepting Basic. SQL server Entra administrator, Entra-only authentication, contained identities/database permissions and restricted public firewall access need approved provisioning. No password fallback or blanket Allow Azure Services firewall setting is proposed.

The cost scenario assumes one consumer LAW and workspace-based Application Insights charged once for common ingestion, no commitment tier and no separately billable cloud evaluation. Source LAW configuration/retention/table plan and any source-query charges were **not inspected**, so source-observation read billing is not claimed free.

## 5. Exact retail rate quote — USD / Central US

Source: [Azure Retail Prices API](https://prices.azure.com/api/retail/prices), public unauthenticated GET with `currencyCode=USD`; original service/product filters, item fields, meter IDs, SKU IDs, tiers and effective dates are saved in `retail-evidence.json`. Retrieval timestamps are in that JSON; rates fetched 2026-09-15 UTC. HTTP429s were retried without tenant data in requests. These are retail list prices, not the subscription's negotiated invoice, tax or FX quote. No reservation, savings plan, free grant or cache discount is assumed.

**Standing costs at730 hours/month:**

| Exact product / meter | Retail rate and unit | Effective start | Quantity | USD/month |
|---|---:|---|---:|---:|
| Azure AI Search / Basic Unit | 0.101 / 1 Hour | 2016-08-01 | 730 SU-hours | 73.73 |
| Azure App Service Basic Plan - Linux / B1 | 0.018 / 1 Hour | 2019-06-01 | 730 instance-hours | 13.14 |
| SQL Database Single Basic, SKU B / B DTU | 0.161 / 1/Day | 2014-11-01 | 730/24 days | 4.897083 |
| **Standing subtotal** | | | | **91.767083** |

SQL's exact retail meter is **daily**, not hourly and not per individual DTU. It is `cae64797-9ecf-4906-b517-6238c80c045f`, product `SQL Database Single Basic`, SKU `B`. The search filter `skuName eq 'Basic'` does not find it. Do not accidentally quote PostgreSQL/MySQL Basic or elastic-pool/secondary rates.

**Variable usage allowance across all nine agents and all three Functions apps, including retries, nested calls and host traffic:**

| Exact meter / product | Rate, unit | Effective start | Monthly allowance | USD |
|---|---:|---|---:|---:|
| gpt 4o 1120 Inp Data Zone Tokens / Azure OpenAI | 0.00275 / 1K tokens | 2025-11-01 | 20M input tokens | 55.00 |
| gpt 4o 1120 Outp Data Zone Tokens / Azure OpenAI | 0.011 / 1K tokens | 2025-11-01 | 5M output tokens | 55.00 |
| text embedding 3 small DZ Tokens / Azure OpenAI Embedding | 0.000022 / 1K tokens | 2026-02-01 | 10M embedding tokens | 0.22 |
| Semantic Ranker queries / Azure AI Search | 1.00 / 1K queries | 2023-11-15 | 10,000 charged queries | 10.00 |
| On Demand Execution Time / Flex Consumption | 0.000026 / GB-second | 2025-02-01 | 3M aggregate GB-seconds | 78.00 |
| On Demand Total Executions / Flex Consumption | 0.000004 / 10 executions | 2025-02-01 | 1M aggregate executions | 0.40 |
| Hot LRS Data Stored / General Block Blob v2 | 0.0184 / GB-month, first tier | 2017-02-03 | 100GB across four accounts | 1.84 |
| Hot LRS Write Operations / General Block Blob v2 | 0.05 / 10K operations | 2016-05-09 | 1M | 5.00 |
| Hot Read Operations / General Block Blob v2 | 0.004 / 10K operations | 2016-05-09 | 5M | 2.00 |
| LRS List and Create Container Operations / General Block Blob v2 | 0.05 / 10K operations | 2016-05-09 | 100K | 0.50 |
| LRS Data Stored / Queues v2 | 0.045 / GB-month | 2017-11-01 | 3GB host queues total | 0.135 |
| LRS Class 1 Operations / Queues v2 | 0.004 / 10K operations | 2017-11-01 | 1M host operations total | 0.40 |
| Analytics Logs Data Ingestion / Log Analytics | 2.76 / GB, paid tier | 2018-02-01 | 50GB consumer ingestion | 138.00 |
| Analytics Logs Data Retention / Log Analytics | 0.12 / GB-month | 2018-02-01 | 50GB-month **chargeable** extended retention | 6.00 |
| Standard Data Transfer Out / Rtn Preference: MGN | 0.087 / GB, first paid tier | 2022-05-01 | 100GB charged internet egress | 8.70 |
| **Variable subtotal** | | | | **361.195** |

**Conditional pilot total: USD452.962083 ≈ USD452.96/month**; annual planning extrapolation **USD5,435.55** at the same usage every month. Arithmetic persisted in `scenario-evidence.json`. The remaining **USD547.04** against USD1,000 is **unallocated planning headroom, not a purchased contingency or priced service**. A conditional extra B1 host would make the known subtotal **USD466.10**, before any changed variable use or newly required service.

Useful exact comparison, not a recommendation to change residency: gpt-4o1120 GlobalStandard input/output retail is0.0025/0.01 per1K (2025-11-01), versus regional Standard0.00275/0.011 and data-zone0.00275/0.011. Embedding global is0.000020/1K (2025-11-01), versus data-zone0.000022. Selection requires residency approval, not merely lower price.

Always-ready is **disabled in the scenario**, not free: Flex `Always Ready Baseline` is0.000004/GB-second, `Always Ready Execution Time`0.000016/GB-second, and executions0.000004/10 (effective2024-12-01). One continuously allocated2GB baseline alone would cost **USD21.024/month**, plus its execution charges; do not add always-ready unnoticed.

### Quote scope and unresolved costs

- Foundry account/project/model deployments are represented as resource containers for PAYG prompt inference; no separate flat account/project fee was established by the consulted retail records. **Absence of a meter is not proof all Foundry services are free.** The estimate includes chat/embedding tokens, not additional hosted-agent compute, evaluation judges, fine-tuning, code interpreter, Bing, web search, file-search charges, premium enrichment or provisioned capacity.
- Three Flex plans are on demand, not three flat hourly hosting charges. Host account count itself is not four compute fees; host storage/data/transactions are budgeted explicitly.
- No dedicated MCP/A2A host is silently included. Any compatibility-driven additional runtime needs final architecture and price approval. No model-backed evaluation is enabled or presumed free.
- No private endpoints, DNS/private networking, NAT Gateway, APIM, Service Bus, custom domain, Azure Files mount, SQL replicas/LTR, paid table querying, cross-region traffic, support plan or optional Defender plan is purchased/assumed. Their absence must match actual deployment. Source LAW query plan and tenant policy-imposed add-ons remain to be checked without querying data.
- Existing producer/legacy Foundry standing costs are excluded because this is **incremental new-consumer** planning, not a subscription-wide spend forecast. New producer runs and their ingestion remain separately authorized and billable.
- Billing-unit allowances above deliberately apply paid marginal rates without claiming free allocations still exist elsewhere in this subscription. Included SQL2GB is not additional Blob capacity. Consumer AppInsights and LAW are not double-counted.
- Workload admission/token budgets, retries, concurrency, schedules-disabled-by-default, logs sampling and retention are needed in code. A monthly budget alert does not stop usage; scaling limits are not comprehensive financial caps. Reprice after measured workload serialization/SDK execution and after any required gateway decision.

## 6. Principal and role classes — no grants performed

| Principal | Required class/scope to design and approve |
|---|---|
| Deployer | Resource creation rights for the new RG and required resources (e.g. narrowly scoped Contributor/custom deployment role); subscription-level RG creation/provider registration separately approved. If authoring approved assignments, RBAC Administrator or User Access Administrator/custom equivalent at the narrow applicable scopes. Ability to read resources does not prove these write privileges. Actual deployer effective permissions/PIM/deny assignments were not established. |
| Foundry deployment administrator | Appropriate Foundry account/project/agent management and model-deployment rights, separate from runtime inference. Azure AI Project Manager / developer roles or scoped custom equivalent must be matched to actual APIs; do not use Owner for workload execution. |
| Worker | New dedicated managed identity. Observation-only custom LAW role+condition; minimal SQL stored-procedure/table/outbox permissions; evidence-container Blob write/read; approved Foundry project invocation role such as Azure AI User and appropriate Cognitive inference role. No producer write, arbitrary Azure Reader or unrestricted Logs role. |
| Tool host | Separate managed identity. Only tool-specific evidence/metadata reads and deterministic gate operations. Observation querying, if implemented in this identity, requires its own approved table-constrained assignment. No generic tenant/subscription Reader as a substitute for table isolation; no remediation grants. |
| UI/API | Its own managed identity for narrow SQL read/review-transition operations and necessary evidence Blob read. Reviewer users authenticate through a new Entra application and explicit application roles/group assignments. UI identity does not inherit worker/publisher authority. |
| Publisher | Separate managed identity: approved SQL publication/outbox operations, required evidence reads, `Search Index Data Contributor` scoped to new Search service/index constraints where supported, approved embedding-inference role. Index/schema administration (`Search Service Contributor`) belongs to deployment, not routine publication unless separately justified. |
| Retrieval identity / Foundry project identity | `Search Index Data Reader` scoped to new Search service; exact connection authentication and downstream token audience must be proven. No publisher write permissions for prompt agents. |
| Function host/deployment identities | Host storage roles selected from the actual bindings: Blob data ownership/contributor and queue data rights as required, deployment-package read/write separated. Management-plane Contributor alone does not grant Blob/Queue data access. Avoid account keys. |
| SQL Entra administrator / database provisioning identity | Approved administrator sets up contained users and precise database permissions. Runtime identities do not need SQL admin. Graph lookup/directory-permission prerequisites, if required by identity resolution, are a separate approval gate. |

Review UI app registration is **new and requires approval**. Specify single-tenant issuer, HTTPS redirect URI, audience, user/application roles, reviewer assignment, allowed callers and consent. Do not invent a secret, auto-create a service principal or grant directory roles. MCP documentation requires a **distinct MCP-server registration**; noninteractive client/service authentication, consent and connection support must be validated rather than relying on delegated user tokens.

### Verified source observation-only role

Source workspace: `iot-dv2-us92-0914-law` in `iot-dv2-us92-0914-rg`.

- Custom role **`iot-dv2-us92-0914-law-observation-readiness`**
- Role definition ID **`0ae05324-8854-5c35-84ca-a60a5d44a3a7`**
- Assignable scope is the **source RG**, not the new consumer RG.
- Actions: `Microsoft.OperationalInsights/workspaces/query/read`
- DataActions: `Microsoft.OperationalInsights/workspaces/tables/data/read`
- No NotActions/NotDataActions; **definition alone is not table-specific**.
- A current ServicePrincipal assignment at the exact source workspace has conditionVersion **2.0** and:

```text
(
  !(ActionMatches{'Microsoft.OperationalInsights/workspaces/tables/data/read'})
  OR
  @Resource[Microsoft.OperationalInsights/workspaces/tables:name] StringEquals 'EnergyObservations_CL'
)
```

Other older similarly named roles are scoped to the legacy RG and must not be substituted. Existing source-workspace inherited assignments include broad management roles for other principals; their presence proves **nothing** about the future worker. The preflight deliberately did not expose principal names/contact details or treat broad inherited Reader as table isolation.

After approved identity creation, inspect **that exact principal's** direct, group, inherited and relevant data permissions and perform both (a) bounded positive `EnergyObservations_CL` read and (b) negative unrelated-table/producer-write tests. Existing successful role assignment is not evidence that a new identity has been provisioned, isolated or negatively tested.

## 7. Evidence and reproducibility

Read-only command classes used, always from the `agentic-itops` directory:

- `az account show`; `az extension show --name quota`; `az group exists`.
- `az quota list` then `az quota usage show/list`, explicit subscription and provider/centralus scopes.
- `az provider list/show`; `az resource list` reduced to types/locations/counts.
- `az cognitiveservices usage list` and regional `model list` — management-plane only.
- `az appservice list-locations`, `az appservice plan list`, `az functionapp list-flexconsumption-locations`.
- Management-plane GET usage fallbacks for Microsoft.Web and Microsoft.Sql.
- `az role definition list --custom-role-only true` and source-workspace `az role assignment list --include-inherited`, omitting principal identities/contact information.
- Public Microsoft Learn/retail GETs with generic public filters only.

Persistent companions:

1. `retail-evidence.json` — Central US retail items, exact units/meter IDs/effective dates and original public filters. Includes nonselected comparison meters; **only tabled meters above enter the scenario**.
2. `model-catalog-evidence.json` — selected model/version capability and SKU metadata, no data-plane response.
3. `scenario-evidence.json` — verified scenario arithmetic; does not confer approval.

**Next permitted step is parent review of these findings and an explicit approval request**, not modifying Azure to make preflight pass. Preserve the deployment-plan and implementation ownership boundaries. No ADO comment/commit was made by this read-only preflight.
