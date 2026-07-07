---
name: workbook-generator
description: >-
  Discovery-linked Azure Monitor workbook generation. Use when you have an SRE
  Agent resource discovery result and need to turn it into deployable Azure
  Monitor (Log Analytics) workbooks for the infrastructure, application, network,
  and storage domains. Triggers: "auto-generate workbooks", "discovery-linked
  dashboards", "build workbooks from inventory", "operational dashboards for a
  workspace". This skill consumes a discovered inventory (it does not perform
  discovery — that is the SRE Agent's built-in capability) and emits workbook
  JSON ready for the Bicep or Terraform deployment paths. Invoked by the
  sre-workbook-orchestrator agent, but usable standalone.
---

# Workbook Generator

Turns an SRE Agent **discovery result** into Azure Monitor workbooks aligned to
what is actually deployed. Discovery itself is the SRE Agent's built-in
capability; this skill *consumes* its output. Implements the generation half of
**Story #33 — Auto-Generated Workbooks** (Task #18, *Adapt Workbook Generation
Logic*).

## When to use

- A customer needs operational dashboards stood up quickly from their real estate.
- The discovered inventory has changed and dashboards must be re-aligned to it.
- You need workbook JSON to feed the `bicep/` or `terraform/` deployment.

## Inputs

| Input | Description |
| ----- | ----------- |
| SRE Agent discovery result (JSON) | Output of the agent's built-in discovery. Either a flat `resources[]` list or a pre-bucketed `domains{}` inventory. See `generator/sample-discovery-input.json`. |
| Target Log Analytics workspace resource ID | Where the workbooks will resolve queries. |
| Domains (optional) | Subset of `infrastructure,application,network,storage`. Default: all with discovered resources. |

## Procedure

### Stage 1 — Consume the discovery result
Provide the SRE Agent's discovery output as JSON. The generator buckets a flat
`resources[]` list into the four domains by resource type (case-insensitive), or
uses an already domain-bucketed inventory as-is:

- **infrastructure** — VMs, VMSS, Arc machines, availability/scale sets
- **application** — App Service, Functions, Container Apps, AKS
- **network** — VNets, NSGs, Load Balancers, App/Front Door, Public IPs
- **storage** — Storage accounts, Managed Disks, SQL, Cosmos

### Stage 2 — Generate
Bind the inventory to the domain templates and emit one workbook per populated
domain:

```bash
python ./generator/generate_workbooks.py \
  --inventory ./discovery-result.json \
  --workspace-resource-id <workspace-id> \
  --templates-dir ./templates \
  --output-dir ./out
```

Generation rules:
- **Skip empty domains.** No storage accounts discovered -> no storage workbook.
- **Bind the workspace.** Every template's `{{workspaceResourceId}}` and
  `{{crossComponentResources}}` placeholders are replaced from the inventory.
- **Tag provenance.** Each workbook header records the inventory timestamp and
  source subscription so dashboards are auditable.
- **Deterministic output.** Re-running on the same inventory yields byte-identical
  workbooks (stable ordering), so deployments are idempotent.

### Stage 3 — Hand off
Emit `out/<domain>.workbook.json`. These are the deployment inputs for:
- **Bicep:** `bicep/main.bicep` (parameter `workbooks[]`, loaded in `main.bicepparam`)
- **Terraform:** `terraform` variable `workbooks_dir` (reads `*.workbook.json`)

## Output contract

- Files conform to Azure Monitor Workbook schema `Notebook/1.0`.
- No unbound `{{...}}` placeholders remain.
- A `manifest.json` lists generated domains, source inventory hash, and target
  workspace for the orchestrator's Validate stage.

## Definition of done

- [ ] Generated workbooks for every domain with discovered resources.
- [ ] All queries scoped to the discovered workspace (no manual edits — AC #3).
- [ ] Output consumable unchanged by both Bicep and Terraform paths.
