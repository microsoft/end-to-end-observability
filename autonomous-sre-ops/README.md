# Autonomous SRE Ops

A deployable demonstration of a managed **Azure SRE Agent** (Review mode, access level Low)
investigating real Azure Monitor alerts in a small, self-contained Azure environment: three
private Linux VMs, Azure Update Manager, Azure Backup, a Log Analytics workspace and a private
runtime that collects immutable evidence for the agent. Resources use the prefix `asre` and live
in **one subscription in `centralus`**.

> This is a standalone demonstration. It is **not** a phase of the client-onboarding pipeline
> described in [`.github/copilot-instructions.md`](../.github/copilot-instructions.md), and it
> does not depend on `amba-enterprise/` or `monitoring-baselines/`.

## What it demonstrates

Five use cases, each driven by a real Azure failure rather than a replay fixture: Linux disk
root-cause analysis, alert noise and overlap, a proactive capacity trend, a patch (Azure Update
Manager) investigation and a backup (Azure Backup) triage. Bounded, self-resetting fault drivers in
`scripts/` create the faults; the agent only reads evidence.

## Scope of this release

| Area | Status |
| --- | --- |
| Foundation and compute: network, identities, storage, Key Vault, container registry, Log Analytics, Recovery Services vault, three VMs, monitoring agent, workloads | Deploys |
| Private runtime: runtime images and Azure Container Apps jobs (`worker`, `collector`, `bootstrap`) | Deploys (`deploy_runtime`) |
| Azure Monitor alert rules | Deploys disabled; enabled one at a time |
| Managed SRE Agent (Review mode, access level Low, no Contributor) | Deploys when `deploy_managed_agent = true` (billed while it exists) |
| Observe-only incident handler `asre-azure-monitor-observe` (Azure Monitor alert to an automatic Review-mode investigation; one read-only evidence tool; no case tool, hook or skill) | Published disabled, enabled last |
| ServiceNow case management (case tool, production Review handler, feedback job) | Source and tests present; **not deployed** (deferred) |
| Operator dashboard | Source and tests present; **not deployed** (deferred) |
| CI/CD pipeline | Deferred; deployment runs from the operator's workstation |

## Architecture

- **Compute:** three `Standard_D2s_v3` Ubuntu VMs (`linux`, `patch`, `backup`) on static addresses in an
  isolated VNet (`10.42.0.0/16`, no hub membership), each in its own resource group so one fault cannot
  disable another's recovery or collection.
- **Telemetry:** Log Analytics workspace (30-day retention, 1 GB/day cap), Application Insights, a data
  collection rule for the three VMs, and seven scheduled-query alert rules that are created disabled (five of
  them can be enabled through `enabled_native_alert_rules`).
- **Backup and patching:** one Recovery Services vault protecting the `backup` VM; Azure Update Manager
  on the `patch` VM.
- **Runtime:** an internal Azure Container Apps environment running a queue `worker`, a five-minute
  `collector` and a manual `bootstrap` job, images pinned by digest in a private registry; evidence goes to
  a storage account and secrets to Key Vault, both behind private endpoints.
- **Identity:** separate user-assigned identities for the agent, runner, collector, image pull and
  bootstrap, plus custom roles; no shared keys.
- **Agent:** `Microsoft.App/agents` with a dedicated private egress subnet; the observe handler and its
  incident filter are published from the workstation.

See [`docs/implementation.md`](docs/implementation.md) and
[`docs/runtime-networking.md`](docs/runtime-networking.md) for detail.

## Prerequisites

- PowerShell 7.5 or later (`pwsh`). Run the deployment scripts in-session with the call operator
  (`& .\scripts\<Script>.ps1`), not through a nested `pwsh -File`, which can hang on the first `az` call.
- Terraform **exactly 1.11.2** (enforced by `scripts/Invoke-Terraform.ps1`).
- Azure CLI signed in (`az login`) to the target tenant, with rights to create resource groups, custom
  role definitions and role assignments in the subscription (for example Owner, or Contributor plus User
  Access Administrator). The scripts refuse to run against a subscription other than the one in the context.
- Python 3.13 to run the tests (the runtime image uses the same version); no Python is needed to deploy.
- Required resource providers, the pinned VM image version and VM quota are checked by
  `scripts/Test-DemoReadiness.ps1 -Phase Preflight` before anything is created.

## Deploy

Deployment is a sequence of reviewed steps, each with a read-only or `-WhatIf` path first and nothing that
applies automatically. [`docs/deployment.md`](docs/deployment.md) is the runbook; in outline:

1. Pre-flight checks, then the remote-state bootstrap (`Initialize-TerraformState.ps1`).
2. Foundation and compute (one saved plan, reviewed, then applied), then export the context.
3. Backup protection (a second apply; one-way).
4. Runtime images (`Build-RuntimeImages.ps1`), then the runtime and its jobs.
5. The agent last, because its billing starts at creation: bind Azure Monitor, render the agent assets,
   publish the common profile and publish the observe handler disabled.
6. Enable alert rules one at a time, then enable the handler last. A controlled fault is injected only on
   explicit instruction.

## Configuration: the context document

No deployment-specific value is stored in source. Terraform's `context` output is the single source of
truth (`asre-context/v1`, schema in [`schemas/`](schemas/)). `scripts/Export-AsreContext.ps1` writes it to the
git-ignored `infra/environments/context.generated.json`. The PowerShell loader (`scripts/Context.Common.ps1`)
and the Python loader (`operations/context.py`) read it and fail closed; resource ids are derived from the
subscription id and naming conventions rather than stored, and the runtime jobs receive a trimmed copy as the
`ASRE_CONTEXT` environment variable. Agent instructions, knowledge, workflow manifests and schemas are
rendered from the context into a git-ignored `out/` folder before publication
(`scripts/Render-AgentAssets.ps1`); nothing published is edited by hand.

## Tests

All tests are offline and use synthetic fixture contexts (`tests/fixtures/`); none calls Azure.

```powershell
cd autonomous-sre-ops
pip install -r operations/requirements.txt -r tests/requirements.txt pytest
python -m pytest tests -q                      # conftest.py selects the synthetic context
Get-ChildItem tests -Filter 'Test-*.ps1' | ForEach-Object { pwsh -NoProfile -ExecutionPolicy Bypass -File $_.FullName }
terraform -chdir=infra init -backend=false
terraform -chdir=infra validate
terraform -chdir=infra test
```

`python -m unittest` does not load `conftest.py`: set `ASRE_CONTEXT_FILE` to a fixture first, for example
`$env:ASRE_CONTEXT_FILE = "$PWD\tests\fixtures\context.synthetic.json"`.

## Cost

Indicative figures for Central US, pay-as-you-go, while everything runs: roughly **USD 600 per month** for the
deployed resources, of which about 290 is the always-on managed agent (billed from creation until deletion;
stopping it does not stop billing) and about 210 the three VMs. Subscription-level Defender plans, where enabled,
add to that. The agent is created last and only when `deploy_managed_agent = true`; setting it back to `false`
is the only lever that stops its charge. Investigations add token-based Azure Agent Unit (AAU) usage on top; set the
agent's monthly AAU limit as the runbook describes (Wave 5) so a busy month cannot run past your budget. Confirm
actuals in Cost Management filtered by the tag `project=autonomous-sre-ops`.

## Limitations

- Custom role definition names are **tenant-unique**. A second deployment of this module into another
  subscription of the same tenant collides on them (this release creates one, `ASRE credential bootstrap`;
  the module defines six in total, the rest belonging to the deferred feedback and dashboard parts);
  parameterizing the role names is future work.
- The platform may deallocate the VMs overnight; the runbook has a start-and-settle step before any trial.
- Region is fixed to `centralus`; the agent runs in Review mode only.
- The dashboard and ServiceNow paths are ported for later use and are guarded so they cannot be reached from
  this release's Terraform defaults.

## Layout

| Path | Purpose |
| --- | --- |
| `infra/` | Terraform for the environment (partial backend; `environments/*.example` templates) |
| `operations/` | Python runtime: worker, evidence collectors, context loader |
| `agent/` | Agent instructions, skills, evidence tool, hooks, workflow manifests, knowledge, response plans |
| `scripts/` | PowerShell deployment, publishing, rendering and fault-driver scripts |
| `contracts/` | JSON schemas for evidence and cases |
| `schemas/` | The `asre-context/v1` schema |
| `dashboard/` | Optional evidence dashboard (deferred) |
| `workloads/` | Workloads that run on the demo VMs |
| `tests/` | Offline Python and PowerShell tests and fixture contexts |
| `config/` | Sanitized sample runtime configuration |
| `docs/` | Runbook and design documents |

## Documentation

- [`docs/deployment.md`](docs/deployment.md): the runbook (waves, validation, rollback, teardown)
- [`docs/implementation.md`](docs/implementation.md): source layout, context contract, local checks, fault controls
- [`docs/native-review-workflows.md`](docs/native-review-workflows.md): the agent plane and incident filters
- [`docs/runtime-networking.md`](docs/runtime-networking.md): managed-agent and private-runtime networking
- [`docs/decisions.md`](docs/decisions.md): decision log
