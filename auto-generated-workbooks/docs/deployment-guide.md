# Deployment Guide — Auto-Generated Workbooks

Covers running the full Discover → Generate → Deploy → Verify flow, validating
both IaC paths, and the cross-workspace deployment test for Story #33, Task #19.

## Prerequisites

| Tool | Used for | Notes |
|------|----------|-------|
| SRE Agent discovery | producing the inventory | built-in to the SRE Agent; not part of this repo |
| Python 3.8+ | generator | standard library only, no pip installs |
| Azure CLI (`az`) | Bicep deploy | `az login`; Contributor on target RG |
| Terraform ≥ 1.9 | Terraform deploy | optional if using Bicep path |
| Bicep CLI | Bicep deploy | `az bicep install` |

## 1. Consume the discovery result

Discovery is performed by the SRE Agent's built-in discovery functions — this
workflow does not re-run it. Save the agent's discovery output as
`discovery-result.json`. The generator accepts either shape:

- **Flat** (typical SRE Agent output): `{ "subscriptions": [...], "resources": [ { "type", "id", "name", "location", ... } ] }` — see `generator/sample-discovery-input.json`.
- **Pre-bucketed**: `{ "domains": { "<domain>": { "resources": [...] } } }`.

The generator buckets a flat list into the four domains by resource type
(case-insensitive): infrastructure, application, network, storage.

## 2. Generate

```powershell
python ./generator/generate_workbooks.py `
  --inventory ./discovery-result.json `
  --workspace-resource-id "<workspace-resource-id>" `
  --templates-dir ./templates `
  --output-dir ./generator/out
```

Outputs `generator/out/<domain>.workbook.json` for each **non-empty** domain plus
`manifest.json`. Output is deterministic — re-running over the same inventory
produces identical files. Empty domains are listed under `skippedDomains` and
produce no workbook.

**Validate the generated JSON before deploying:**

```powershell
Get-ChildItem ./generator/out/*.workbook.json | ForEach-Object {
  Get-Content $_ -Raw | python -c "import sys,json; json.load(sys.stdin)"
  if ($LASTEXITCODE -eq 0) { "OK  $($_.Name)" } else { "BAD $($_.Name)" }
}
```

## 3a. Deploy with Bicep (CLI path)

`main.bicepparam` embeds each workbook via `loadTextContent('../generator/out/...')`,
so generate before you build. Update the workspace ID and the loaded paths to
match the domains that were actually generated.

```powershell
az bicep build --file ./bicep/main.bicep        # compile-time validation
az deployment group create `
  --resource-group <target-rg> `
  --template-file ./bicep/main.bicep `
  --parameters ./bicep/main.bicepparam
```

The deployment is idempotent: each workbook name is a deterministic `guid()` of
the resource group, display name, and workspace, so re-deploys update in place.

## 3b. Deploy with Terraform

Terraform reads `*.workbook.json` directly from `workbooks_dir` with `fileset()`,
so no per-domain edits are needed — whatever the generator produced gets deployed.

```powershell
cd terraform
Copy-Item terraform.tfvars.example env.tfvars   # then edit values
terraform init
terraform validate
terraform plan  -var-file=env.tfvars
terraform apply -var-file=env.tfvars
```

Each workbook uses a deterministic `uuidv5()` name keyed on the workspace and
domain, matching the Bicep idempotency behavior.

## 4. Verify

1. **Workbooks render:** Azure Portal → Monitor → Workbooks → gallery; open each
   `Auto-Generated <Domain> Workbook` and confirm tiles load.
2. **Data flows without manual steps:** confirm the inventory (Resource Graph)
   tiles populate immediately, and the Log Analytics tiles populate once the
   workspace receives telemetry. No dashboard editing is required.
3. **Manifest matches portal:** `generator/out/manifest.json` `workbooks[]` should
   equal the deployed set; `deployedWorkbookIds` (Bicep) / `workbook_ids`
   (Terraform) outputs should list one ID per generated domain.

### Understanding what each tile shows (why some tiles say "no data")

Every workbook mixes **two kinds of tiles**, and they get their data from
different places:

| Tile kind | `resourceType` | Data source | Populates when… |
|-----------|----------------|-------------|-----------------|
| **Inventory** | `microsoft.resourcegraph/resources` | Azure Resource Graph (the discovered scopes) | always — independent of any workspace |
| **Performance / logs** | `microsoft.operationalinsights/workspaces` | the bound Log Analytics workspace (`--workspace-resource-id`) | the discovered resources actually send that telemetry **to that workspace** |

Key point: a resource being *discovered* does not mean it *logs to the bound
workspace*. The Log Analytics tiles only show data for resources whose
diagnostic settings / VM Insights target the workspace you generated against.
So it is normal to see populated **inventory** tiles but empty **performance**
tiles if you point the workbook at a workspace those resources don't log to.

To make the performance tiles light up, generate against the workspace the
resources actually report to (set `--workspace-resource-id` to that workspace
and redeploy). The inventory tiles work regardless of workspace choice.

## Cross-workspace deployment test (Task #19)

Confirms the same generated artifacts deploy to multiple workspaces unchanged —
only the `workspace-resource-id` differs.

1. Use the SRE Agent's discovery output captured per environment (dev/prod).
2. Run **Generate** per environment, passing that environment's workspace ID. The
   templates are identical; only the bound `sourceId`/`crossComponentResources`
   change, proving the generation is discovery-linked, not hard-coded.
3. Deploy to **workspace A** with Bicep and to **workspace B** with Terraform.
4. Verify both galleries show the same domain workbooks and that each set queries
   its own workspace. Record workbook IDs from both IaC outputs as evidence.

| Environment | Workspace | IaC path | Evidence |
|-------------|-----------|----------|----------|
| Dev | `law-dev` | Bicep | `deployedWorkbookIds` |
| Prod | `law-prod` | Terraform | `workbook_ids` |

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Generator skips a domain you expected | discovery result had 0 resources of those types, or types weren't mapped | confirm the SRE Agent discovered them; check `DOMAIN_TYPE_MAP` in `generate_workbooks.py` |
| Bicep `loadTextContent` not found | generator not run, or path/domain mismatch | run Generate first; align `main.bicepparam` paths to generated domains |
| Inventory tiles empty / show one weird row | `crossComponentResources` not a proper JSON array (subscriptions comma-joined into one string) | fixed in generator (`build_cross_component_resources` emits each scope as its own array element) — regenerate and redeploy |
| Performance/log tiles empty | the bound workspace doesn't receive that telemetry (resource discovered ≠ resource logs here) | generate against the workspace the resources actually report to, or enable diagnostic settings / VM Insights to the bound workspace |
| Duplicate workbooks after re-deploy | name not deterministic | both IaC paths use deterministic names — ensure the same display name/workspace inputs |
