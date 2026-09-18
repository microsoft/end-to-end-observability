# Service-stage continuation and separate quote

Status: implementation approved; exact service-stage Azure changes remain gated.
Quote date: 2026-09-15. Story 93 remains Active; its acceptance-criteria field is
absent. The retained E01-E15 design matrix remains the proposed release contract,
not acceptance evidence.

## Confirmed scope

The user requested continuation of all nine agents, the separate authenticated
review UI and Domains V2 ingestion. They confirmed reuse of
`agentic-itops-us93-rg`, Central US, subscription
`643febd3-3170-4110-80f9-fc8980b70a7a`, and explicitly chose:
**Keep USD 500/month foundation-only; quote services separately.**

The foundation is already deployed. Do not redeploy models, increase their
capacity, replace the producer, enable public SQL/Storage access or change
Defender plans to make this quote work. US data-zone inference remains approved.
All results remain synthetic and non-operational; schedules stay disabled.

## Proposed service topology

| Addition | Quantity and purpose |
|---|---|
| App Service | One Linux B1 plan, one separate Flask review/API app, one instance |
| Functions | Three distinct Flex Consumption apps: worker, tools, publisher; 2,048 MB, zero always-ready instances |
| Function host storage | Three dedicated Hot LRS GPv2 accounts with key authentication and public access disabled |
| VNet | One isolated VNet; separate App Service integration, Flex integration and nondelegated private-endpoint subnets |
| Private endpoints | Twelve: existing SQL and evidence Blob; new vault; Blob and Table for each host account; private ingress for each Function app |
| Private DNS | Five shared zones: SQL, Blob, Table, Key Vault, App Service/Functions; linked to the new VNet |
| Secrets | One Standard Key Vault, Entra/RBAC, private-only, soft delete and purge protection |
| Identities | Separate review, worker, tools and publisher UAMIs; separately scoped deployment/migration identity |
| Entra applications | Review sign-in/app roles and service-to-service API audience/roles; no automatic tenant-wide consent |

B1 supports regional VNet integration. App Service and Flex require different
subnet delegations: `Microsoft.Web/serverFarms` and `Microsoft.App/environments`.
Neither integration subnet can hold private endpoints. Private ingress does not
follow automatically from outbound VNet integration.

Flex host/deployment authentication must both use managed identity. Blob-only
private networking does not cover host Table diagnostics, so this quote includes
Table endpoints. The initial hosts use HTTP-triggered bounded work and the
existing SQL queue, not Storage Queue/Blob triggers or Durable Functions. If
those triggers become necessary, add their Queue endpoints, DNS, roles and costs
to the package before approval. Azure Files mounts are not proposed.

The review UI is HTTPS/Entra-authenticated; the three Function APIs are private
and independently authenticate callers. A real deployment path from within the
VNet is required: neither a private endpoint nor an operator Owner role makes a
local machine able to reach private SQL, Storage, Key Vault or Function ingress.
Do not temporarily expose these resources to bootstrap them.

## Incremental monthly quote

USD public PAYG rates, 730 hours/month, before tax, negotiated discounts and
usage. No shared subscription free grants or Defender trial discounts deducted.
Existing Foundry, Search, SQL, evidence Storage and monitoring standing charges
belong to the foundation allowance and are not added again here.

| Component | Rate / calculation | Monthly USD |
|---|---|---:|
| Linux B1 | 0.018/hour x 730 | 13.14 |
| Twelve private endpoints | 12 x 0.01/hour x 730 | 87.60 |
| Five private DNS zones | 5 x 0.50/zone-month | 2.50 |
| Defender for three host accounts | 3 x 0.0134/account-hour x 730 | 29.35 |
| CSPM for three host accounts | 3 x 0.007/resource-hour x 730 | 15.33 |
| Defender for B1 | 0.02/compute-instance-hour x 730 | 14.60 |
| Defender for new Key Vault | PerKeyVault: 0.00034/hour x 730 | 0.25 |
| Sample host Blob capacity | 30 billed GB-month x 0.0184 | 0.55 |
| **Identified subtotal** | Sum using unrounded rates | **163.32** |

The storage sample uses billed GB-month, not an unverified conversion from GiB.
Storage capacity is consumed usage, not reserved capacity. Private DNS's API
unit is `1`; the calculation uses the published zone-month pricing convention.
Plain VNet/subnets have no separate charge in this scenario; no NAT gateway,
firewall, VPN gateway, DNS Private Resolver, VM or private build runner is
included.

Live read-only subscription inspection confirms Standard AppServices,
StorageAccounts/DefenderForStorageV2, KeyVaults/PerKeyVault and CloudPosture plans.
CSPM charges eligible Storage accounts, not every ARM child resource. The
foundation's existing SQL/storage CSPM charges are not duplicated.

Defender App Service billing is documented per compute instance. The exact
Flex-specific billable instance behavior has not been established. A conservative
planning reserve of three additional full-month instance charges is USD 43.80,
making the subtotal with that reserve **USD 207.12/month**. This is a scenario,
not an asserted Flex charge or upper bound on autoscaling.

Approved **separate services allowance: USD 250/month**. The user selected
"Approve $250/month additional services allowance and continue implementation".
This is additional to the USD 500 foundation allowance, not a new estimate that
the foundation itself costs USD 500. Neither allowance is an Azure billing cap.
This allowance does not authorize an unspecified role grant or bypass
the deployment validation gates below.

Variable costs and example units:

| Meter | USD rate / illustration |
|---|---|
| Flex on-demand compute | 0.000026/GB-second; 100 aggregate active 2-GB instance-hours = 18.72 |
| Flex executions | 0.40/million, excluding any free grant |
| Private Link traffic | 0.01/GB ingress plus 0.01/GB egress in first tier, summed across endpoints |
| Private DNS queries | 0.40/million |
| Standard vault operations | 0.03/10,000 |
| Hot LRS writes / list-create | 0.05/10,000 in each category |
| Hot Blob reads / other | 0.004/10,000 in each category |
| Standard LRS Table capacity / operations | 0.045/GB-month; 0.00036/10,000 standard operations |
| Defender malware scanning, when enabled | 0.15/GB scanned |

New log ingestion, egress, table storage, retries and applicable security
overages remain additional. Existing LAW's daily cap is a soft operational
guardrail, not a billing cap or assurance of complete diagnostics. Model and
embedding consumption stay accounted against the foundation usage allowance;
new live acceptance calls need an explicit bounded batch approval. The old
foundation-only smoke authorization is not extended automatically.

## Permission and bootstrap gates

Before Azure changes, write and validate an exact principal/resource/role
manifest and deployment preview. The intended boundaries are:

- Review: its own SQL review/work-submission authority and vault secrets;
  no model, Search-write, evidence-write or producer permissions.
- Worker: only the producer observation table, immutable evidence operations,
  scoped consumer/result SQL operations, Foundry invocation and approved Search
  reads. Never workspace-wide Log Analytics Reader; independently prove denial
  against a known-nonempty `EnergyInjectionAudit_CL`.
- Tools: bounded approved evidence/context operations; no source control,
  infrastructure modification, arbitrary KQL or publication authority.
- Publisher: eligible approved revisions only, embedding invocation and Search
  index data writes/removals. No review approval authority or producer access.
- Deployment/migration: isolated from runtime identities, limited to owned new
  resources and explicit schema/user setup. No subscription-wide Contributor,
  Directory Readers or permanent runtime `db_owner` shortcut.

The current SQL repositories issue direct table writes. Their exact runtime
grants and approval/write separation must be proven or hardened before assigning
roles; a Python-internal method is not a SQL authorization boundary.
Host/deployment storage roles are distinct from evidence roles.
Entra role assignments require identified reviewers; self-publication approval
remains prohibited. Missing tenant permissions are a blocker, not a reason to
request broad Graph consent.

## Execution order and evidence

1. Complete local runtime/result/publisher integration, Linux packaging, scoped
   SQL grants/migration bootstrap and exact Luna tool/version settings.
2. Prepare parameterized service IaC from verified official templates/modules;
   prove the private deployment/bootstrap path and inventory all new resources.
3. Obtain exact resource, permission, reviewer and bounded-inference approval;
   validate policy/quota, compile, package, provider validation and what-if.
4. Deploy networking and service hosts; apply migrations through the isolated
   bootstrap identity. Prove private DNS/TLS and actual MI authorization.
5. Connect the consumer to bounded retained observations; prove positive reads
   and independent audit-table denial before any inference.
6. Register and invoke all nine exact Luna-backed versions. Persist validated
   outcomes and expose them in the authenticated review UI.
7. Prove review CAS/idempotency, separate publication approval, Search delivery,
   revocation/erasure and lifecycle/hygiene behavior; keep schedules disabled.

Do not label placeholder hosts, liveness-only HTTP 200 responses, empty-data
denials or nine registrations as full acceptance. The existing 687-test source
checkpoint and 18 foundation regressions do not prove these service gates.

## Sources and unresolved preflight

- [Retail Prices API](https://prices.azure.com/api/retail/prices):
  Central US Consumption meters for Linux B1, Flex On Demand, Hot LRS Blob,
  Tables and Standard Key Vault; global Private Link/DNS and Defender meters.
  Defender prices were re-read directly after a search summary gave incorrect
  CSPM and Key Vault pricing; that summary is not used as quote evidence.
- [B1 VNet support](https://learn.microsoft.com/en-us/azure/app-service/overview-vnet-integration).
- [Functions networking](https://learn.microsoft.com/en-us/azure/azure-functions/functions-networking-options).
- [Flex hosting](https://learn.microsoft.com/en-us/azure/azure-functions/flex-consumption-plan).
- [Function storage requirements](https://learn.microsoft.com/en-us/azure/azure-functions/storage-considerations).
- [Identity-based host connections](https://learn.microsoft.com/en-us/azure/azure-functions/manage-connections?tabs=host&pivots=functions-auth-identity).
- [Flex deployment authentication](https://learn.microsoft.com/en-us/azure/azure-functions/flex-consumption-how-to#configure-deployment-settings).
- [Defender compute-instance billing](https://learn.microsoft.com/en-us/azure/defender-for-cloud/defender-for-app-service-introduction).
- [CSPM billable resource types](https://learn.microsoft.com/en-us/azure/defender-for-cloud/concept-cloud-security-posture-management#defender-cspm-billable-resources).

Unresolved before a deployable quote: actual Flex regional quota/allocation,
private build/migration transport, exact trigger/API routes and endpoint count,
effective table-level Log Analytics permissions, Linux native SQL runtime,
Entra registration/role authority, workload consumption and Flex Defender
instance billing. If resolving one adds resources or changes security boundaries,
revise this package rather than silently spending the reserve.
