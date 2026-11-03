resource "azurerm_role_definition" "feedback_thread_sender" {
  count       = var.enable_servicenow_feedback ? 1 : 0
  name        = "ASRE correlated feedback sender"
  scope       = azurerm_resource_group.demo["control"].id
  description = "Private feedback worker: read the fixed agent and read/write thread messages; no configuration or infrastructure mutation."
  permissions {
    actions = ["Microsoft.App/agents/read"]
    data_actions = [
      "Microsoft.App/agents/threads/read",
      "Microsoft.App/agents/threads/write",
    ]
  }
  assignable_scopes = [azurerm_resource_group.demo["control"].id]
}

resource "azurerm_role_assignment" "feedback_thread_sender" {
  count              = var.enable_servicenow_feedback ? 1 : 0
  scope              = azapi_resource.agent[0].id
  role_definition_id = azurerm_role_definition.feedback_thread_sender[0].role_definition_resource_id
  principal_id       = azurerm_user_assigned_identity.demo["runner"].principal_id
  principal_type     = "ServicePrincipal"
}

# Reuse the existing single-action refresh role for this new secret-free job.
resource "azurerm_role_assignment" "feedback_job_refresh" {
  count              = var.enable_servicenow_feedback ? 1 : 0
  scope              = azurerm_container_app_job.operations["feedback"].id
  role_definition_id = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/00000041-000a-4000-8000-000000000041"
  principal_id       = "00000043-000a-4000-a000-000000000043"
  principal_type     = "ServicePrincipal"
}
