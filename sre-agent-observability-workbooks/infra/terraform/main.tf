# ---------------------------------------------------------------------------
# main.tf — SRE Agent Observability Workbooks.
#
# Deploys every NN-<slug>.workbook.json built by builder/build_workbooks.py as an
# Azure Monitor workbook scoped to the target Log Analytics workspace. Display
# names are read from the builder's manifest.json so the portal gallery shows the
# friendly suite names (e.g. "SRE Agent Command Center"); unknown files fall back
# to a title-cased slug.
#
# Matches the Bicep and PowerShell paths so all three produce identical workbooks
# with the same deterministic resource names (uuidv5).
#
#   terraform init && terraform apply -var-file=terraform.tfvars
# ---------------------------------------------------------------------------

locals {
  workbook_files = fileset(var.workbooks_dir, "*.workbook.json")

  # slug (filename without .workbook.json) -> serialized JSON.
  workbooks = {
    for f in local.workbook_files :
    trimsuffix(basename(f), ".workbook.json") => file("${var.workbooks_dir}/${f}")
  }

  # file -> display name, from the builder manifest when present.
  manifest_path = "${var.workbooks_dir}/manifest.json"
  manifest = fileexists(local.manifest_path) ? jsondecode(file(local.manifest_path)) : { workbooks = [] }
  display_names = {
    for w in local.manifest.workbooks :
    trimsuffix(w.file, ".workbook.json") => w.displayName
  }
}

resource "azurerm_application_insights_workbook" "this" {
  for_each = local.workbooks

  # Deterministic UUID v5 so re-applying updates in place per workbook.
  name                = uuidv5("url", "${var.workspace_resource_id}/${each.key}")
  resource_group_name = var.resource_group_name
  location            = var.location
  display_name        = lookup(local.display_names, each.key, title(replace(each.key, "-", " ")))
  source_id           = lower(var.workspace_resource_id)
  category            = "workbook"
  data_json           = each.value

  tags = merge(var.tags, { slug = each.key })
}
