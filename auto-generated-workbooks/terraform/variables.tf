# ---------------------------------------------------------------------------
# variables.tf — Auto-Generated Workbooks (Story #33) Terraform inputs.
# ---------------------------------------------------------------------------

variable "subscription_id" {
  description = "Subscription ID where the workbooks are deployed."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group that will contain the workbook resources."
  type        = string
}

variable "location" {
  description = "Azure region for the workbook resources."
  type        = string
  default     = "eastus"
}

variable "workspace_resource_id" {
  description = "Resource ID of the Log Analytics workspace the workbooks query."
  type        = string
}

variable "workbooks_dir" {
  description = <<-EOT
    Directory containing the generated <domain>.workbook.json files
    (generator/out by default). Every *.workbook.json in this directory is
    deployed as a separate Azure Monitor workbook.
  EOT
  type        = string
  default     = "../generator/out"
}

variable "tags" {
  description = "Tags applied to every deployed workbook."
  type        = map(string)
  default = {
    workload    = "auto-generated-workbooks"
    generatedBy = "sre-workbook-orchestrator"
  }
}
