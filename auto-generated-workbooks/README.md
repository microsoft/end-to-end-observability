# Auto-Generated Workbooks

Discovery-linked Azure Monitor workbook generation, orchestrated by the **SRE
Agent** as the central, AI-centric controller. Implements Azure DevOps Story
**#33 — Auto-Generated Workbooks** (Epic #34, *Operational Visibility and
Workbooks*).

> *As a CSA, I want discovery-linked workbook generation, so that customers have
> meaningful dashboards within days.*

## Why this exists

Standing up observability for a new customer normally means hand-building
dashboards per resource type. This capability inverts that: it **consumes** the
SRE Agent's discovery of what actually exists in the subscriptions, **generates**
workbooks aligned to that inventory, and **deploys** them to a Log Analytics
workspace — with no manual authoring step. Empty domains are skipped, so
customers only get dashboards that have something to show.

## SRE-Agent-centric architecture

The **SRE Agent is the center**. Discovery is its own built-in function; this
workflow consumes that result and turns it into deployed workbooks. Everything
else is a capability it invokes or infrastructure it deploys:

```
                         ┌─────────────────────────────┐
                         │   SRE Workbook Orchestrator  │   (subagent)
                         │   agents/…orchestrator.agent │
                         └─────────────┬───────────────┘
                                       │ owns lifecycle
   Discovery (built-in) ──► Generate ──► Validate ──► Deploy ──► Verify
       │                       │                          │
       ▼                       ▼                          ▼
 SRE Agent native        skills/workbook-generator/   bicep/  +  terraform/
 discovery output        (generator/generate_workbooks.py) (CLI)   (Terraform)
 (resources[] JSON)          │
                             ▼
                       templates/*.workbook.json
```

| Stage | Owner | Artifact |
|-------|-------|----------|
| Discovery | **SRE Agent (built-in)** | discovery result JSON (`resources[]`) — see `generator/sample-discovery-input.json` |
| Generate | **skill** (capability) | `skills/workbook-generator/SKILL.md`, `generator/generate_workbooks.py` |
| Validate | orchestrator | JSON parse + `terraform validate` / `az bicep build` |
| Deploy | IaC (agent runs) | `bicep/` (CLI) **and** `terraform/` |
| Verify | orchestrator | confirms workbooks render and data flows |

- **Discovery is not re-implemented here.** The SRE Agent already discovers
  resources; this workflow *consumes* that output and buckets it into the four
  domains.
- **Skill vs subagent (as requested):** the *generation capability* is packaged
  as a **skill** (`skills/workbook-generator/`); the *central orchestrator* is a
  **subagent** (`agents/sre-workbook-orchestrator.agent.md`).
- **Two IaC versions (as requested):** identical workbooks ship as both
  **Bicep** (`bicep/`) and **Terraform** (`terraform/`).

## Layout

```
auto-generated-workbooks/
├── agents/
│   └── sre-workbook-orchestrator.agent.md   # central SRE orchestrator (subagent)
├── skills/
│   └── workbook-generator/SKILL.md          # generation capability (skill)
├── generator/
│   ├── generate_workbooks.py                # consumes discovery result → templates
│   └── sample-discovery-input.json          # example SRE Agent discovery output
├── templates/
│   ├── infrastructure.workbook.json         # VMs, VMSS, Arc
│   ├── application.workbook.json            # App Service, Functions, ACA, AKS
│   ├── network.workbook.json                # VNet, NSG, LB, App Gateway
│   └── storage.workbook.json                # Storage, Disks, SQL, Cosmos
├── bicep/
│   ├── main.bicep
│   ├── main.bicepparam
│   └── modules/workbook.bicep
├── terraform/
│   ├── main.tf  variables.tf  outputs.tf  providers.tf  versions.tf
│   └── terraform.tfvars.example
├── deploy/
│   └── Deploy-Workbooks.ps1                 # quick standalone deploy for testing
├── docs/
│   └── deployment-guide.md
└── README.md
```

## The four domains

| Domain | Resource types discovered | Sample dashboards |
|--------|---------------------------|-------------------|
| `infrastructure` | VMs, VM Scale Sets, Arc machines | CPU, heartbeat health, inventory |
| `application` | App Service, Functions, Container Apps, AKS | request/failure rate, top exceptions |
| `network` | VNets, NSGs, Load Balancers, App Gateway, Public IPs | allowed/denied flows, gateway status |
| `storage` | Storage accounts, Disks, SQL, Cosmos | transactions, error rate, used capacity |

## Quick start

```powershell
# 1. Obtain the discovery result from the SRE Agent's built-in discovery.
#    (A sample of the expected shape is in generator/sample-discovery-input.json.)
#    Save it as ./discovery-result.json

# 2. Generate (writes generator/out/<domain>.workbook.json + manifest.json)
python ./generator/generate_workbooks.py `
  --inventory ./discovery-result.json `
  --workspace-resource-id "/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.OperationalInsights/workspaces/<law>" `
  --templates-dir ./templates `
  --output-dir ./generator/out

# 3a. Deploy with Bicep (CLI)
az deployment group create -g <rg> `
  --template-file ./bicep/main.bicep `
  --parameters ./bicep/main.bicepparam

# 3b. …or deploy with Terraform
cd terraform
terraform init
terraform apply -var-file=env.tfvars
```

### Quick test (standalone, no IaC)

For a fast smoke test of the workflow before wiring up Terraform, the
`deploy/Deploy-Workbooks.ps1` helper deploys every generated workbook via
`az rest` (ARM PUT, idempotent). It uses the **same deterministic resource
names and `sourceId` as Terraform**, so a later `terraform import`/apply lines
up one-to-one instead of duplicating.

```powershell
$ws = "/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.OperationalInsights/workspaces/<law>"

# Preview what would deploy
./deploy/Deploy-Workbooks.ps1 -WorkspaceResourceId $ws -WhatIf

# Deploy (resource group + subscription default to the workspace's)
./deploy/Deploy-Workbooks.ps1 -WorkspaceResourceId $ws

# Remove the test workbooks afterwards
./deploy/Deploy-Workbooks.ps1 -WorkspaceResourceId $ws -Cleanup
```

See **[docs/deployment-guide.md](docs/deployment-guide.md)** for the
cross-workspace deployment test (Story #33, Task #19) and validation steps.

## Acceptance criteria mapping

| Story #33 criterion | Where satisfied |
|---------------------|-----------------|
| Consume discovery to produce infra/app/network/storage workbooks aligned to inventory | `generator/` (consumes SRE Agent discovery) + `templates/` |
| Workbooks deployable to Log Analytics via CLI **and** Terraform | `bicep/` (CLI) + `terraform/` |
| Alerts and logs flow to dashboards without manual steps | consume → generate → deploy is fully scripted; workbooks bind to the workspace `sourceId` |
