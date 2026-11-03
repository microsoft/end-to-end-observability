import {
  for_each = var.adopt_existing_native_alerts ? toset(["patch-installation", "capacity-growth", "backup-job"]) : toset([])
  to       = azurerm_monitor_scheduled_query_rules_alert_v2.guest[each.key]
  id       = "/subscriptions/${var.subscription_id}/resourceGroups/rg-asre-control-cus/providers/Microsoft.Insights/scheduledQueryRules/asre-demo-${each.key}"
}
