# Implementation guide

This guide describes the source of `autonomous-sre-ops/`: its layout, the deployment context, the local checks, the
runtime image, the agent-asset flow, the fault controls and the deferred work. It describes what the source does and
guarantees. It does not report the state of any deployment; source checks and offline tests do not prove an Azure
fault, a private connection or an agent investigation. The operator sequence is in [deployment.md](deployment.md) and
the subproject overview is in [../README.md](../README.md).

## Scope of Phase 1

| Deployed in Phase 1 | Deferred (source and tests exist, nothing deployed) |
|---|---|
| Foundation and compute (three VMs, network, workspace, vault, registry, Key Vault) | ServiceNow case management and feedback |
| Runtime images and the Container Apps jobs | The operator dashboard |
| The optional managed agent (`deploy_managed_agent`) | A CI/CD pipeline |
| Seven Azure Monitor alert rules, created disabled | Autonomous repair and lifecycle workflows |
| The observe-only handler `asre-azure-monitor-observe` | |

## Layout

| Path | Content |
|---|---|
| `infra/` | One typed Terraform root, separated by concern (see below), with `environments/` examples and `tests/context.tftest.hcl` |
| `workloads/linux-demo-service/` | Real disk-growth and cgroup-bounded memory workload, fixed guest operations over SSH stdin (`guest_control.py`), one owned and reversible APT installation failure, the systemd unit and `bootstrap.sh` |
| `workloads/package-mirror/` | Unmodified Ubuntu vendor proxy with one exact, independently expiring artifact fault |
| `operations/` | Python runtime package: `worker.py`, `protocol.py`, `cloud.py`, `ssh.py`, `patch_provider.py`, `patch_sensor.py`, `lifecycle.py`, `trends.py` and `context.py`; the deferred modules are `servicenow.py`, `agent_case.py`, `feedback.py`, `feedback_runtime.py` and `facade_demo.py` |
| `agent/` | Handler declarations, instructions, knowledge, skills, tools, hooks and checklists; see [native-review-workflows.md](native-review-workflows.md) |
| `contracts/`, `operations/contracts/`, `config/` | Shared evidence and case schemas and the native incident runtime configuration (deferred) |
| `schemas/asre-context.v1.schema.json` | The deployment context schema |
| `dashboard/` | Local read-only operations dashboard (deferred) |
| `scripts/` | Operator scripts and shared PowerShell modules (see below) |
| `tests/` | Python tests, plain PowerShell tests (`Test-*.ps1`) and the context fixtures |

`operations/worker.py` is the private scheduled evidence worker; Review mode cannot execute remediation. The
Phase 1 worker takes only constants and id helpers from `operations/lifecycle.py`. The transaction, target-lock and
episode-budget logic there, and `operations/trends.py`, are tested libraries that no Phase 1 job calls. Trend
calculations need a real, ordered and sufficiently dense baseline and reject gaps and counter resets.

## Execution model

Terraform runs from the operator's workstation, not from a pipeline. `scripts/Invoke-Terraform.ps1` wraps it; read the
script header or run `Get-Help` for its parameters. It guarantees:

- Terraform is exactly 1.11.2, and the provider lock file must be present with its Linux and Windows hashes.
- A plan is saved with a manifest that records the subscription, tenant, commit and a SHA-256 for every input.
  The manifest expires after four hours.
- Apply runs only from a reviewed saved plan, re-verifies the manifest first and never replans.
- Any plan that deletes or replaces a resource is refused. The protected backup item, the vault and the Key Vault also
  have `prevent_destroy`.
- A dirty source tree is refused unless `-AllowDirty` is given, and the manifest then records it.
- Ambient service-principal, OIDC and MSI environment variables are removed before Terraform starts.

Remote state is an Entra-only storage account created by `scripts/Initialize-TerraformState.ps1`: shared-key access
off, versioning and soft delete on, and a default-deny firewall that admits only the operator address. The backend is
partial: copy `infra/environments/backend.hcl.example` to the git-ignored `infra/environments/backend.hcl`. Its state
key is `autonomous-sre-ops/demo.tfstate` and it sets `use_azuread_auth = true`. `-backend=false` is for local schema
validation only; local state or a fresh workspace is never a way around inaccessible remote state. The script reads the
account back after its writes and fails closed if public network access is not `Enabled`; a tenant whose Modify policy
forces it off needs the opt-in `-ApplyPolicyExclusionTag` (state account only), see D13 and `docs/deployment.md`, Wave 0.

After a provider change, regenerate the lock file with hashes for both platforms and commit the hidden
`infra/.terraform.lock.hcl`. Do not remove `-lockfile=readonly` to hide a mismatch:

```powershell
terraform -chdir=infra providers lock -platform=linux_amd64 -platform=windows_amd64
```

## Context contract

Terraform emits one document, `output "context"` in `infra/outputs.tf`, in the format `asre-context/v1`. It is the only
source of deployment values. `scripts/Export-AsreContext.ps1` validates it against the schema, refuses a synthetic
context and saves it atomically as the git-ignored `infra/environments/context.generated.json`.

| Consumer | Loader | Precedence |
|---|---|---|
| Python (`operations/context.py`, `load()`) | `ASRE_CONTEXT` (a JSON document), then the file named by `ASRE_CONTEXT_FILE` | No default; a missing context is an error |
| PowerShell (`scripts/Context.Common.ps1`, `Get-AsreContext`) | `-Path`, then `$env:ASRE_CONTEXT_FILE`, then `infra\environments\context.generated.json` | No default beyond the generated file |

Rules of the contract:

- Both loaders fail closed. The schema forbids unknown properties, limits the document to 65,536 bytes and rejects
  duplicate keys. The PowerShell loader rejects a synthetic context unless `-AllowSynthetic` is given, and
  `Assert-AsreSubscription` confirms with `az account show` that the signed-in subscription matches the context.
- Resource ids are derived from `subscription_id` plus naming conventions and are never stored.
- `name_suffix` is the first 10 hex characters of the SHA-256 of `<subscription-id>:asre-demo`. It makes globally
  unique names (`stasre<suffix>`, `kv-asre-<suffix>`, the registry) deterministic.
- Optional parts are `operator_object_id`, `servicenow`, `identities.bootstrap` and `agent.endpoint`. Python code that
  needs one uses a `require_*` helper, which fails with a fixed message when the part is absent. Phase 1 modules must
  import under a context that lacks `servicenow` and `agent.endpoint`.
- Each runtime job receives a trimmed static `ASRE_CONTEXT` (`local.context_core`). It carries no `agent.endpoint` and no
  `operator_object_id`, never references the agent resource and stays under 4,096 bytes, so a job does not depend on
  the agent being created.
- Tests use `tests/fixtures/context.synthetic.json` and `context.alt.json`. Both are synthetic: the PowerShell loader
  refuses either unless `-AllowSynthetic` is given, and tests that need a context it accepts build a non-synthetic
  document from the fixture. `tests/alt_context.py` provides a leak
  detector (`leaks_in_module`) that proves no deployment value is hard-coded in a module. `tests/conftest.py` points
  `ASRE_CONTEXT_FILE` at the synthetic fixture.

## Terraform root

| File | Content |
|---|---|
| `versions.tf`, `providers.tf`, `backend.tf` | Terraform 1.11.2, AzureRM 4.51.0, AzAPI 2.13.0; `resource_provider_registrations = "none"`; partial backend |
| `variables.tf`, `outputs.tf` | Typed inputs and the outputs listed below |
| `main.tf`, `network.tf`, `data.tf` | Resource groups, identities, the workspace and the registry; the VNet, NSGs, NAT and private DNS; the operations storage account and Key Vault |
| `compute.tf`, `backup.tf` | The three VMs and their disks; the Recovery Services vault, policy and protected item |
| `monitoring.tf` | The data collection rule and the alert rules |
| `runtime.tf`, `agent.tf` | The Container Apps environment, jobs and role grants; the managed agent and its roles |
| `dashboard.tf`, `feedback.tf` | Deferred: dashboard hosting and the ServiceNow feedback job |

Main inputs (see `variables.tf` and `infra/environments/demo.tfvars.example` for types and examples):

| Variable | Meaning |
|---|---|
| `subscription_id`, `tenant_id`, `location` | Deployment target; `location` accepts only `centralus` |
| `admin_ssh_public_key`, `runner_ssh_public_key` | Two different public keys; the runner key is bound to a forced command |
| `operator_object_id` | Required when the managed agent or the dashboard is deployed |
| `deploy_managed_agent`, `agent_model` | Optional agent (default false); the model is required when it is deployed |
| `deploy_runtime`, `runtime_image_digest` | The Container Apps jobs and their image, as `sha256:<64 hex>` from `image-provenance.json` |
| `backup_protection_enabled` | Default false; set it to true for the second apply, never with `-target` |
| `enabled_native_alert_rules` | Enables `disk`, `patch-installation`, `noise-overlap`, `capacity-growth` or `backup-job` |
| `servicenow`, `enable_servicenow_feedback`, `dashboard` | Deferred components; identifiers only, no secrets |

Outputs: `context`, `resource_groups`, `targets`, `log_analytics_workspace_id`, `registry_name`, `registry_server`,
`key_vault_uri`, `storage_account_name`, `managed_identities`, `agent_resource_id`, `agent_endpoint`,
`agent_network_subnet_id`, `faults_enabled`, `runtime_jobs`, `runtime_bootstrap_client_id` and `dashboard`.

Facts worth knowing before changing the root:

- **VMs.** `Standard_D2s_v3`, Ubuntu 22.04 (Canonical, generation 2), a 32 GB `Standard_LRS` OS disk, a 4 GB disposable
  data disk and the Azure Monitor agent extension. Faults are disabled in the guest policy by default.
- **Alert rules.** `asre-demo-<key>` for `disk`, `workload-oom`, `patch-installation`, `heartbeat`, `noise-overlap`,
  `capacity-growth` and `backup-job`. Disk, workload-oom, patch-installation, heartbeat and backup-job are Sev1;
  noise-overlap and capacity-growth are Sev2. Heartbeat, patch-installation and backup-job evaluate every five minutes
  and the others every minute, over a ten-minute window. The backup rule reads `arg("").RecoveryServicesResources`
  and the patch rule parses `ASRE_PATCH_FAILURE_V1` events in `ContainerAppConsoleLogs_CL`. Queries are created with
  `skip_query_validation = true`, so Terraform does not validate them at apply time. The data collection rule
  `dcr-asre-linux-cus` collects performance counters (30 second sampling) and Syslog.
- **Storage and Key Vault.** The storage account has the `evidence` container, the tables `Episodes`, `Operations`,
  `TargetLocks` and `PendingWrites`, and the queues `pending-writes` and `agent-requests`; shared-key access is off and
  blob versioning and a seven-day soft delete are on. The Key Vault uses RBAC, purge protection and no public access.
- **Jobs.** `job-asre-<key>-cus` for the worker (`mode=queue`, every minute), the collector (`mode=collect`, every five
  minutes) and a manual bootstrap job. The feedback job exists only with `enable_servicenow_feedback`. Each job has a
  300 second replica timeout, no retries, 0.25 CPU and 0.5 Gi, and runs `python -m operations.worker`. Environment
  variables carry the identity client ids, `ASRE_EXECUTION_MODE=Review` and `ASRE_CONTEXT`.
- **Custom roles.** The module defines six `azurerm_role_definition` resources in total: `ASRE credential bootstrap`
  (with `deploy_runtime`), `ASRE correlated feedback sender` (with `enable_servicenow_feedback`) and four dashboard
  roles (with the dashboard). This release creates only one, `ASRE credential bootstrap`, which grants only
  get and set secret. Custom role names are unique per tenant: a second deployment of this module into another
  subscription of the same tenant collides on the custom role names (one is created in this release; the module defines
  six in total).
- **Network and agent.** See [runtime-networking.md](runtime-networking.md).

## Local checks

Run from `autonomous-sre-ops/` in PowerShell 7.5 or later. None of these checks contacts Azure.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r tests\requirements.txt -r operations\requirements.txt
$env:ASRE_CONTEXT_FILE = (Resolve-Path tests\fixtures\context.synthetic.json).Path
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
terraform -chdir=infra fmt -check -recursive
terraform -chdir=infra init -backend=false -input=false
terraform -chdir=infra validate
terraform -chdir=infra test
```

- `unittest` needs `ASRE_CONTEXT_FILE` because it does not load `tests/conftest.py`. With pytest installed,
  `python -m pytest tests` sets it through `conftest.py`.
- `terraform test` (`infra/tests/context.tftest.hcl`) mocks both providers and only plans. It proves that the root
  emits the context document and hands the same static core to every runtime job.
- Each `tests/Test-*.ps1` is a plain PowerShell script (no Pester) that stops on its first failed assertion. Run the
  ones that cover the area you changed, for example `.\tests\Test-AgentAssets.ps1` or
  `.\tests\Test-RuntimeImageBuild.ps1`.
- The Linux guest-safety cases (descriptor, symlink and hardlink handling in `tests/test_guest_safety.py`) need Linux.
  On Windows run them in WSL: `wsl -- python3 -m unittest discover -s tests -p test_guest_safety.py -v` and
  `wsl -- bash -n workloads/linux-demo-service/bootstrap.sh`. They check filesystem and policy behaviour; they do not
  prove that a VM-local systemd timer restored a live fault.
- `scripts/Test-DemoReadiness.ps1` checks the source, a preflight and the delivery state. It is read-only and prints
  JSON that always carries `demo_ready = false`, `requires_saved_plan = true` and `faults_enabled = false`. The
  preflight checks resource providers, the VM image version, quota and (informationally) Defender plans.

## Runtime images

`scripts/Build-RuntimeImages.ps1` builds one image in the deployment's Azure Container Registry; the registry comes
from the context. `ASRE_RUNTIME_IMAGE_KIND` selects `operations` (default) or `dashboard`.

- The build refuses to run unless a validated plan manifest proves the exact source: the same subscription and tenant,
  the same commit as `HEAD`, a clean tree and every build input matching its recorded SHA-256.
- The tag and the manifest digest are both locked (write and delete disabled), and `image-provenance.json` is written
  to a new output directory. Locking the tag alone does not lock the manifest digest, so both are locked.
- Terraform takes the digest through `runtime_image_digest` (`sha256:<64 hex>`). Jobs pull
  `<login_server>/asre-operations@<digest>` by managed identity; the registry admin account is disabled. A new digest
  needs a new saved plan.
- `operations/Dockerfile` uses a digest-pinned Python 3.13 slim base, installs pinned requirements, runs as the
  non-root user `65532:65532` and records the source commit in an image label. The entry point is
  `python -m operations.worker`.

`scripts/Initialize-RuntimeSsh.ps1` bootstraps and verifies the private, pinned-host SSH path of the runtime jobs: it
retrieves the host keys, runs the bootstrap and verification jobs and installs the restricted runner public key on the
three targets. It must preserve `ASRE_CONTEXT` when it patches jobs. See its header for parameters.

## Agent assets

Committed assets under `agent/`, `contracts/`, `operations/contracts/` and `config/native-incident-runtime.json` carry
the synthetic fixture values. They are never hand-edited per deployment and are never published as committed.

1. **Render.** `scripts/Render-AgentAssets.ps1` (`Render` parameter set, `-ContextPath` and `-OutputRoot`) applies
   `scripts/AgentAssets.map.json` (`asre-agent-assets-map/v1`: 46 context-leaf rules, 4 sentinel patterns and a list
   of inert files) to the committed assets. Output goes to a new git-ignored directory (default
   `out/agent-assets/<UTC stamp>-<8 hex>`) with `render-receipt.json`.
2. **Verify.** The `Verify` parameter set (`-VerifyRoot`) re-renders in memory and requires byte equality with the
   directory. It never trusts the receipt alone.
3. **Publish.** `scripts/Initialize-ReviewAgent.ps1` and `scripts/Publish-ObserveWorkflow.ps1` take `-AssetRoot
   <rendered>`. The observe handler's two files carry no deployment value, so `Publish-ObserveWorkflow.ps1` requires
   them in a non-default root to be byte-identical (after CRLF to LF) to the subproject's own copies. The `Common`
   profile never publishes inert assets.

Render semantics, implemented in `scripts/AgentAssets.Common.ps1` (`Export-AsreAgentAssets`,
`Assert-AsreRenderedAssets`): one pass per file with the longest match first and case-insensitive matching; an
exact-case match becomes the target value exactly, an all-lowercase match becomes the lower-cased target value and any
other casing fails; a rule whose target value is null fails when it matches in a non-inert file; for any context other
than the synthetic one, a non-inert file may not keep a sentinel shape that the target context lacks. Rendering the
synthetic context returns the identity. Messages never contain an asset or context value.

## Guest baseline recheck

`scripts/Test-GuestBaseline.sh` is a read-only administrative check of a deployed guest. Run it through Run Command for
each role, with the operations storage account name and the Key Vault name as its two positional arguments:

```powershell
az vm run-command invoke --subscription <subscription-id> `
  --resource-group rg-asre-<role>-cus --name vm-asre-<role>-cus `
  --command-id RunShellScript --scripts '@scripts\Test-GuestBaseline.sh' `
  --parameters stasre<suffix> kv-asre-<suffix> --output json
```

Require the `ASRE_BASELINE_VERIFIED` JSON marker in the output, not only a successful CLI return code: Run Command
acceptance alone does not establish guest-script success. The script checks cloud-init, the workload service and its
cgroup limits, the root-only guest policy with faults disabled, the runner key restriction, the dedicated incident disk
on the Linux VM, and DNS resolution and TLS to the private endpoints of storage and the Key Vault. It does not prove
private data-plane authorization, SSH host trust, watchdog recovery or a running agent.

## Fault controls

Faults let the five use cases produce real evidence. The guest policy and the package mirror default to faults
disabled, and the guest helper has no operation that arms a scenario. Only the deployment principal arms one, after a
readiness check and separate authorization.

| Driver | Fault | Bounds |
|---|---|---|
| `scripts/Invoke-LinuxIncident.ps1` | `-Fault Disk`, `WorkloadOom` or `CapacityGrowth` on the Linux VM | `-LifetimeSeconds` 120 to 900 |
| `scripts/Invoke-PatchIncident.ps1` | An owned, reversible APT installation failure on the patch VM | 120 to 900 seconds |
| `scripts/Invoke-BackupIncident.ps1` | Pauses the Azure guest agent on the backup VM while one native backup runs | 180 to 900 seconds |
| `scripts/Invoke-BackupPolicyProbe.ps1` | A temporary Azure Policy deny that makes one native backup fail | `MaxFaultMinutes` 5 to 120; `WatchdogMinutes` 35 to 150 and at least 30 more than the fault window |

Common bounds of the three incident drivers: the actions are `Trigger`, `Status` and `Reset`; the scenario id is a
canonical lowercase UUID; `Reset` needs the original scenario id; evidence files are created with `CreateNew`; `-WhatIf`
is supported; an independent guest watchdog restores the target; an unknown outcome is never retried and a fault is
never renewed. The guest-agent pause has no Run Command reset transport: the original guest watchdog is its recovery
path, and pinned-host SSH inspection is how the guest is observed while the agent is paused.

The backup policy probe has the actions `Preflight` (the default, read-only), `Trigger` and `Cleanup`; cleanup needs the
original scenario id and the evidence directory. The temporary assignment is the built-in *Not allowed resource types*
policy scoped to the Azure Backup restore-point resource group. It denies restore point collections, restore points,
disk restore points and snapshots, plus a canary type. `Trigger` registers a Windows Task Scheduler watchdog first,
waits for the policy to be enforced (the canary), submits exactly one native backup, never retries it, then removes the
assignment and verifies the removal. It refuses to start if the assignment already exists. The denial makes the
native snapshot step fail; the result is a real failed backup job, not a repair and not a replacement backup.

Guest controls:

- The fixed helper accepts bounded JSON on SSH stdin, never an arbitrary shell command, path or service name.
- It validates target ownership and role, installs expiry before it mutates anything and rejects active ownership
  conflicts. Replaying a notification does not extend the timer.
- Repairs restore actual workload configuration and files, or the existing guest-agent service.
- Cleanup requires a filesystem different from `/`, a root-owned marker, an exact bounded manifest, regular single-link
  files and safe directory descriptors. Unknown entries or links cause an explicit refusal before cleanup.
- Watchdog restoration is recorded separately from operator or agent restoration.
- Forecasts are linear estimates, not guarantees. Compare the service and filesystem limits, the growth cap and the
  current usage before claiming a future breach. A memory calibration mode below the threshold is not proof of a
  future OOM.

## Troubleshooting notes

Technical findings that remain true and are easy to rediscover:

- Do not submit a Container Apps job GET template as the body of a start request. It carries fields such as `volumes`
  that the start API rejects; build only the supported properties.
- Run Command splits a public key that contains a space into two arguments. Pass only the key body as one named
  parameter and rebuild the algorithm prefix inside the guest.
- AzureRM reads Container Apps jobs through `listSecrets`, even when a job has no secrets. An identity that plans needs
  a one-action secret-list grant on the jobs; Reader alone is not enough.
- The managed environment's infrastructure resource group name is a ForceNew field. Keep it explicit in source, or a
  refresh proposes replacing the environment and its jobs.
- Azure injects the `FirstPartyUsage` IP tag on the egress public IP. Ignore only that entry.
- A saved plan must be regenerated, never forced, when state changes; Apply keeps Terraform's lineage and serial checks.

## Deferred work

| Item | State in source | What enabling needs |
|---|---|---|
| ServiceNow case management | The production handler `azure-monitor-review`, the case tool, hooks, the `servicenow` variable and context part, the feedback job, `scripts/Build-CaseTool.ps1`, `scripts/Invoke-FacadeDemo.ps1` | A ServiceNow integration, the context parts `servicenow` and `agent.endpoint`, a scoped application whose name replaces the placeholder `x_0000000_asre` that the adapter response schemas and the knowledge note pin, and a decision to publish |
| Operator dashboard | `dashboard/`, `dashboard.tf`, `scripts/Initialize-DashboardAuthentication.ps1` | `dashboard` variable set above disabled, a viewer group and `operator_object_id` |
| CI/CD pipeline | None | A pipeline that keeps the saved-plan rules above and a protected path to the state account |
| Autonomous repair and lifecycle | `operations/lifecycle.py` and `trends.py` as libraries only | A durable case-delivery path, pre-repair audit, repair dispatch and scenario-specific recovery verification |
| Response plans and scheduled tasks | Checklists with `deployed = false` | A decision to publish; a scheduled task uses the `Paused` status |
