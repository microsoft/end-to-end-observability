# Deployment

Three deployment paths produce **identical** workbooks with the **same
deterministic resource names** (uuidv5 of `"<workspaceResourceId>/<slug>"`), so
you can move between them without creating duplicates. PowerShell is the fast
test path; Terraform is the production path; Bicep is the ARM/CLI path.

## Prerequisites

- Azure CLI (`az`) logged in: `az login`.
- Contributor on the target resource group.
- A **Log Analytics workspace** (the workbooks are scoped to it).
- Python 3.9+ to (re)build the workbook JSON.
- For production data: the `SREAgentRecommendations_CL` custom table and its
  Logs Ingestion pipeline (see [data-sources.md](./data-sources.md)).

## 1. Build the workbooks

The deployable artifacts are generated from the builder:

```powershell
cd sre-agent-observability-workbooks
python builder/build_workbooks.py      # -> workbooks/*.workbook.json + manifest.json
python tests/validate_workbooks.py     # must print "All 10 workbook(s) valid."
```

Never hand-edit `workbooks/*.workbook.json`; edit `builder/specs/*.py` (or the
shared libraries) and rebuild.

## 2a. PowerShell (fast test path)

> On Windows, run **in-session** with `& ./script.ps1`. A nested
> `powershell -File ...` invocation hangs on the first `az` call.

```powershell
cd sre-agent-observability-workbooks/scripts
& ./Deploy-Workbooks.ps1 -WorkspaceResourceId "/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.OperationalInsights/workspaces/<ws>"

# Preview without calling Azure:
& ./Deploy-Workbooks.ps1 -WorkspaceResourceId "<id>" -WhatIf
# Remove everything this script deploys:
& ./Deploy-Workbooks.ps1 -WorkspaceResourceId "<id>" -Cleanup
```

Display names come from `workbooks/manifest.json` (e.g. "SRE Agent Command
Center"). Open **Azure Monitor → Workbooks** and filter by the `suite` tag.

## 2b. Terraform (production path)

```powershell
cd sre-agent-observability-workbooks/infra/terraform
Copy-Item terraform.tfvars.example terraform.tfvars   # then edit
terraform init
terraform apply -var-file=terraform.tfvars
```

`main.tf` enumerates `../../workbooks/*.workbook.json`, reads friendly display
names from `manifest.json`, and creates one `azurerm_application_insights_workbook`
per file with a deterministic `uuidv5` name.

## 2c. Bicep (ARM/CLI path)

Bicep cannot read a directory of files at deploy time, so assemble the parameter
file first:

```powershell
cd sre-agent-observability-workbooks/scripts
& ./New-BicepParam.ps1 -WorkspaceResourceId "<id>"   # writes infra/bicep/main.bicepparam

cd ../infra/bicep
az deployment group create -g <rg> --template-file main.bicep --parameters main.bicepparam
```

## 3. Verify

- Azure Monitor → Workbooks shows 10 workbooks tagged
  `suite = SRE Agent Observability Workbooks`.
- Open each; set **LogAnalyticsWorkspaceId** if it isn't already your workspace.
- The **Top 10 SRE Agent Recommendations** panel is empty until
  `SREAgentRecommendations_CL` has rows. Seed it for testing:

```powershell
cd sre-agent-observability-workbooks/scripts
& ./New-SampleRecommendations.ps1 -Count 30 `
    -DcrEndpoint "https://<dce>.<region>.ingest.monitor.azure.com" `
    -DcrImmutableId "<dcr-immutable-id>" -StreamName "Custom-SREAgentRecommendations_CL"
```

## Idempotency & rollback

- Re-running any path **updates in place** (same uuidv5 name).
- Terraform: `terraform destroy`. PowerShell: `-Cleanup`. Bicep: delete the
  workbook resources by name.
- All three share names, so a `terraform import` after a PowerShell test lines up
  one-to-one.
