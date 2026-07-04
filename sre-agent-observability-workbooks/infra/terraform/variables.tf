# ---------------------------------------------------------------------------
# variables.tf — SRE Agent Observability Workbooks Terraform inputs.
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
  description = <<-EOT
    Resource ID of the Log Analytics workspace the workbooks query and are scoped
    to. This is the value the LogAnalyticsWorkspaceId workbook parameter should be
    set to at view time; it also scopes the deployed workbook resources.
  EOT
  type        = string
}

variable "workbooks_dir" {
  description = <<-EOT
    Directory containing the built NN-<slug>.workbook.json files. Defaults to the
    builder output (../../workbooks). Every *.workbook.json in this directory is
    deployed as a separate Azure Monitor workbook.
  EOT
  type        = string
  default     = "../../workbooks"
}

variable "tags" {
  description = "Tags applied to every deployed workbook."
  type        = map(string)
  default = {
    workload  = "sre-agent-observability-workbooks"
    managedBy = "sre-agent"
    suite     = "SRE Agent Observability Workbooks"
  }
}
