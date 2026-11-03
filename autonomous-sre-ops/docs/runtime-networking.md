# Runtime networking

This document describes how the managed Azure SRE Agent reaches the private runtime, which network paths are
private, and what must be proven after a deployment. The Terraform in `infra/` (`network.tf`, `data.tf`,
`runtime.tf`, `agent.tf`) is authoritative; this page explains the design behind it. The source alone does not prove
service admission or a private path: the checklist at the end is how that is established on a real deployment. The
operator sequence is in [deployment.md](deployment.md).

## Network layout

One isolated VNet, `vnet-asre-cus` (`10.42.0.0/16`), in `centralus`. It has no peering and no hub membership; shared
network changes would need a separate decision (see [decisions.md](decisions.md)). Every subnet has its own NSG.

| Subnet | CIDR | Delegation | Used for |
|---|---|---|---|
| `workloads` | `10.42.0.0/24` | none | The three Linux VMs |
| `runner` | `10.42.1.0/26` | `Microsoft.App/environments` | Container Apps environment `cae-asre-operations-cus` |
| `endpoints` | `10.42.2.0/27` | none | Private endpoints for blob, table, queue and Key Vault |
| `agent` | `10.42.3.0/27` | `Microsoft.App/environments` | Egress of the managed agent |

- The workloads NSG allows SSH (22) from the `runner` subnet and the workload port 8080 from the `runner` and `agent`
  subnets, and denies other inbound traffic.
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

The Container Apps environment runs on the `runner` subnet (a `/26`, which workload-profile environments support),
with an internal load balancer and the Consumption profile. Use the `runner` subnet for it; never the `agent` or
`endpoints` subnet. The environment network mode is fixed at creation, so it is not changed as an incidental update
to a live environment.

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

## Limits

- Private egress for the agent has no effect on connectors; prove any connector separately before it is described as
  private.
- The `agent`, `runner` and `endpoints` subnets are not interchangeable: their sizes and delegations differ.
- The module defines custom role names that are unique per tenant; the resulting limit on a second deployment is
  described in [implementation.md](implementation.md).
