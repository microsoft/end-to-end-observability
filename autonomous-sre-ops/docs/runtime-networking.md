# Runtime networking

This document describes how the managed Azure SRE Agent reaches the private runtime, which network paths are
private, and what must be proven after a deployment. The Terraform in `infra/` (`network.tf`, `data.tf`,
`runtime.tf`, `agent.tf`) is authoritative; this page explains the design behind it. The source alone does not prove
service admission or a private path: the checklist at the end is how that is established on a real deployment. The
operator sequence is in [deployment.md](deployment.md).

## Network layout

One isolated VNet, `vnet-asre-cus` (`10.42.0.0/16`), in `centralus`. It has no peering by default and no hub membership;
shared network changes would need a separate decision (see [decisions.md](decisions.md)). The one exception is the
optional runner-only satellite VNet described under [Alternate runner region](#alternate-runner-region). Every subnet
has its own NSG.

| Subnet | CIDR | Delegation | Used for |
|---|---|---|---|
| `workloads` | `10.42.0.0/24` | none | The three Linux VMs |
| `runner` | `10.42.1.0/26` | `Microsoft.App/environments` | Container Apps environment `cae-asre-operations-cus` (this is the primary runner subnet; with `runner_location` the environment uses the satellite runner subnet) |
| `endpoints` | `10.42.2.0/27` | none | Private endpoints for blob, table, queue and Key Vault |
| `agent` | `10.42.3.0/27` | `Microsoft.App/environments` | Egress of the managed agent |

- The workloads NSG allows SSH (22) from the `runner` subnet and the workload port 8080 from the `runner` and `agent`
  subnets, and denies other inbound traffic. With the alternate runner region the runner source is the satellite
  runner subnet (`local.runner_cidr`) instead of the primary `runner` subnet.
- Outbound internet access from the VNet goes through NAT gateway `nat-asre-cus` and one public IP. Terraform ignores
  only the `FirstPartyUsage` entry of the IP tags, which the platform adds itself.
- Private DNS zones (`privatelink.*`) and private endpoints cover blob, table, queue and the Key Vault.
- The operations storage account has shared-key access off, public access off, default action Deny and no bypass.
  A single `private_link_access` rule preserves the Defender for Storage scanner resource instance.

## Managed agent

`azapi_resource.agent` (`infra/agent.tf`) creates `Microsoft.App/agents` named `asre-demo-cus` only when
`deploy_managed_agent` is true (default false). `operator_object_id` and `agent_model` are then required.

| Setting | Value |
|---|---|
| Action mode and access level | Review, Low; no Contributor role anywhere |
| Egress | `sandboxConfiguration.egress.mode = AzureVNet`, `usePrivateDnsResolution = true` |
| Allow lists | `allowedHosts`, `allowedRegistries` and `allowedCodeRepositories` are empty; `allowHttpMcpServerNetworkAccess = false` |
| Subnet | `vnetConfiguration.subnetResourceId` is the `agent` subnet |
| Identities | System-assigned, plus the user-assigned `agent` identity |
| Roles of the `agent` identity | Reader and Monitoring Reader on each resource group, Log Analytics Reader on the workspace, plus the storage roles below |
| Operator | `SRE Agent Administrator` on the agent, for `operator_object_id` |

Platform constraints that shape the design:

- Network integration needs a dedicated, same-region subnet of `/27` or larger, delegated to
  `Microsoft.App/environments`. The `agent` subnet is used for nothing else.
- Inbound private endpoints are not supported. The feature controls the agent's egress, not access to its portal or
  data-plane endpoint.
- Ordinary connectors use the public internet, not the delegated subnet. A connector that targets a private URL is
  not a private-path integration and must not be described as one.
- Python tools can call internal services. Their managed-identity scope presets are ARM, Key Vault and Storage; do
  not assume another audience works without a live identity and tool test.
- The agent data plane uses a separate audience. Resolve the per-agent endpoint from ARM
  (`properties.agentEndpoint` of the agent resource); never construct it from a name. `scripts/AgentPlane.Common.ps1`
  accepts only `https://*.azuresre.ai` hosts on port 443 (see [native-review-workflows.md](native-review-workflows.md)).

## Provider pinning and schema validation

Providers are pinned: Terraform 1.11.2, AzureRM 4.51.0, AzAPI 2.13.0, with a lock file that carries Linux and Windows
hashes. The pinned AzAPI embedded schema rejects `vnetConfiguration` and `sandboxConfiguration` for the agent resource
type, so the agent resource sets `schema_validation_enabled = false`. Validation stays on for every other resource.
The ARM request and a post-create GET are what validate service support. No provider-lock or integrity check is
disabled for this exception.

## Private runtime

The Container Apps environment runs on the primary `runner` subnet (a `/26`, which workload-profile environments support),
with an internal load balancer and the Consumption profile. With `runner_location` it runs on the satellite runner
subnet instead (next section). Use a runner subnet for it; never the `agent` or `endpoints` subnet. The
environment network mode is fixed at creation, so it is not changed as an incidental update to a live environment.

The transport is a private Storage Queue. There is no HTTP application ingress.

1. The evidence tool `asre-review-evidence` (`agent/tools/review_evidence.py`) runs in the agent sandbox, acquires a
   Storage token for the agent identity and posts one of four fixed commands (`inspect`, `telemetry`, `patch_jobs`,
   `backup_jobs`) to the `agent-requests` queue. The tool accepts only the three exact lab VM resource ids.
2. The scheduled worker job (`mode=queue`, every minute) reads the queue, validates the request
   (`operations/protocol.py`), runs the read-only operation against Azure read APIs or a pinned-host SSH inspection,
   and writes an immutable receipt to the `evidence` container.
3. The tool reads that receipt with the `result` action and the original request UUID. Page 0 waits up to 90 seconds
   for a pending receipt; receipts above the size bound come back as numbered fragments that must all be read.
4. The collector job (`mode=collect`, every five minutes) gathers read-only evidence with its own identity. A manual
   bootstrap job handles the runner SSH credential in Key Vault. The container image is pulled by managed identity from the
   registry by digest (`<login_server>/asre-operations@sha256:<digest>`); the registry admin account is disabled.

Private networking is not caller authorization. Separate role grants bound each identity:

| Identity | Storage and Key Vault grants | Other |
|---|---|---|
| `agent` | Storage Queue Data Message Sender, Storage Blob Data Reader | Reader roles listed above |
| runner | Storage Table Data Contributor, Storage Blob Data Contributor, Storage Queue Data Message Processor, Key Vault Secrets User | none |
| collector | Storage Table Data Contributor, Storage Blob Data Contributor | Reader on the VMs and vault, Log Analytics Reader |
| bootstrap | the custom role `ASRE credential bootstrap` (get and set secret only) | none |

## Alternate runner region

Use it only when the primary region cannot create the Container Apps environment (for example repeated
`ManagedEnvironmentCapacityHeavyUsageError`; see [deployment.md](deployment.md), Troubleshooting). Set
`runner_location` to `westcentralus`, `southcentralus` or `westus2`. It is `null` by default, and `null` or
`centralus` keeps the runtime on the primary `runner` subnet with no change to any existing resource. It takes effect
only when `deploy_runtime` is true. The decision is [D24](decisions.md), which builds on D04.

With a satellite region `<c>` (`wcus`, `scus` or `wus2`) Terraform adds these resources in `rg-asre-control-cus`:

| Resource | Name | Detail |
|---|---|---|
| Virtual network | `vnet-asre-runner-<c>` | `10.43.0.0/24`, in the runner region; outside `10.42.0.0/16` so the two can peer |
| Subnet | `snet-runner` | `10.43.0.0/26`, delegated to `Microsoft.App/environments`, default outbound access off |
| NSG | `nsg-asre-runner-<c>` | No custom rules, like the primary runner NSG |
| Egress | `nat-asre-<c>`, `pip-asre-egress-<c>` | Standard NAT gateway (idle timeout 10 minutes) and one static Standard public IP |
| Peerings | `peer-asre-cus-to-<c>`, `peer-asre-<c>-to-cus` | Global peering, both directions, between these two VNets only |
| DNS links | `asre-runner` on each of the four `privatelink.*` zones | Registration off |

- The peering allows virtual network access only: no forwarded traffic, no gateway transit, no remote gateways, no hub.
- The jobs resolve the four private zones through the links to the private endpoints in the `endpoints` subnet of the
  primary VNet and reach storage and Key Vault over the peering. Both stay private-only; no firewall changes.
- Image pulls from the registry go through the NAT gateway, because the registry (Basic) is public.
- The workloads NSG rules `AllowRunnerSSH` and `AllowInternalWorkloadProbe` name the satellite runner CIDR in place of
  the primary one, so the unused primary runner subnet is no longer an allowed source. They are the only existing
  resources that change (two in-place updates).

What stays in Central US: the primary VNet and subnets, the VMs, storage, Key Vault, the registry, the identities,
the Log Analytics workspace (the environment sends logs across regions; confirm one run's rows on a real deployment),
the managed agent and the context `location`. Only `cae-asre-operations-cus` and the jobs `job-asre-<key>-cus` run in
the runner region. Their names keep the `cus` suffix, which names the deployment, not where the runtime runs; the
platform resource group is `ME_cae-asre-operations-cus_rg-asre-control-cus_<runner_location>`.

The SSH bootstrap (`scripts/Initialize-RuntimeSsh.ps1`) reads the host-key and verification markers the jobs print from
`ContainerAppConsoleLogs_CL` in that Central US workspace. A missing marker after `Succeeded` executions is therefore
the symptom of missing cross-region log ingestion; check ingestion first, then stop and report (see
[deployment.md](deployment.md), Wave 4).

The primary `snet-runner`, its NSG and its NAT association stay in place and unused. They are in state and the
wrapper refuses deletions; they cost nothing.

Limits:

- Choose the region before the first runtime apply. The environment region and subnet are fixed at creation, so
  switching later is a replacement of the environment and its jobs, which `scripts/Invoke-Terraform.ps1` refuses.
- Extra cost is about 36 USD per month for the NAT gateway and public IP. Peering data transfer is negligible for
  queue messages, receipts and SSH inspections.
- Capacity in the runner region is not guaranteed either; run the preflight in [deployment.md](deployment.md)
  (Prerequisites) before choosing it.
- A failed environment create leaves the satellite network in state. Retry in the same region, after the failed
  environment and its `ME_` group are gone. Another region would replace the satellite network, which the wrapper
  refuses: stop and ask the owner. Removing the satellite network is an intentional manual deletion (see
  [deployment.md](deployment.md), Teardown).

## Prove after deployment

None of the following is shown by Terraform plan or by the offline tests. Record each one with sanitized evidence
(see [README.md](README.md)) before relying on the private path.

- The agent resource is `Succeeded` and running, in Review mode with access level Low, and reports `AzureVNet`
  egress with the `agent` subnet.
- A name in a private zone resolves to an address in the `endpoints` subnet from a real agent tool, not only from a VM.
- The agent identity obtains a Storage token and the queue accepts a command from a sandbox tool call.
- The worker completes the request and the receipt can be read back, including a multi-fragment receipt.
- No tool of the agent uses a public path to reach the operations storage, Key Vault or VMs. Public connector traffic
  is expected for ordinary connectors and is reported as such.
- The runner and collector jobs run from the pinned digest and reach the VMs over the private network only.
- With `runner_location` set: the environment reports the runner region, both peerings are `Connected`, and a job
  resolves a `privatelink.*` name to an address in the `endpoints` subnet.

## Limits

- Private egress for the agent has no effect on connectors; prove any connector separately before it is described as
  private.
- The `agent`, `runner` and `endpoints` subnets are not interchangeable: their sizes and delegations differ.
- The module defines custom role names that are unique per tenant; the resulting limit on a second deployment is
  described in [implementation.md](implementation.md).
