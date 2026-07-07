# ---------------------------------------------------------------------------
# main.tf — Auto-Generated Workbooks (Story #33).
#
# Discovers every generated <domain>.workbook.json under var.workbooks_dir and
# deploys each as an Azure Monitor workbook scoped to the target Log Analytics
# workspace. Matches the CLI/Bicep path so both produce identical workbooks.
#
# The SRE Workbook Orchestrator agent runs this during the Deploy stage:
#   terraform init && terraform apply -var-file=env.tfvars
# ---------------------------------------------------------------------------

locals {
  # Map of domain name -> rendered workbook JSON, one entry per generated file.
  workbook_files = fileset(var.workbooks_dir, "*.workbook.json")

  workbooks = {
    for f in local.workbook_files :
    trimsuffix(basename(f), ".workbook.json") => file("${var.workbooks_dir}/${f}")
  }
}

resource "azurerm_application_insights_workbook" "this" {
  for_each = local.workbooks

  # Deterministic UUID v5 so re-applying updates in place per domain.
  name                = uuidv5("url", "${var.workspace_resource_id}/${each.key}")
  resource_group_name = var.resource_group_name
  location            = var.location
  display_name        = "Auto-Generated ${title(each.key)} Workbook"
  source_id           = lower(var.workspace_resource_id)
  category            = "workbook"
  data_json           = each.value

  tags = merge(var.tags, { domain = each.key })
}
