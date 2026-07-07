# ---------------------------------------------------------------------------
# outputs.tf — Auto-Generated Workbooks (Story #33).
# ---------------------------------------------------------------------------

output "workbook_ids" {
  description = "Map of domain name to deployed workbook resource ID."
  value       = { for k, w in azurerm_application_insights_workbook.this : k => w.id }
}

output "workbook_count" {
  description = "Number of workbooks deployed."
  value       = length(azurerm_application_insights_workbook.this)
}
