# ---------------------------------------------------------------------------
# outputs.tf — SRE Agent Observability Workbooks.
# ---------------------------------------------------------------------------

output "workbook_ids" {
  description = "Map of workbook slug to deployed workbook resource ID."
  value       = { for k, w in azurerm_application_insights_workbook.this : k => w.id }
}

output "workbook_count" {
  description = "Number of workbooks deployed."
  value       = length(azurerm_application_insights_workbook.this)
}
