resource "azurerm_container_app_environment" "operations" {
  count                              = var.deploy_runtime ? 1 : 0
  name                               = "cae-asre-operations-cus"
  location                           = var.location
  resource_group_name                = azurerm_resource_group.demo["control"].name
  infrastructure_resource_group_name = "ME_cae-asre-operations-cus_${azurerm_resource_group.demo["control"].name}_${var.location}"
  infrastructure_subnet_id           = azurerm_subnet.demo["runner"].id
  internal_load_balancer_enabled     = true
  log_analytics_workspace_id         = azurerm_log_analytics_workspace.demo.id
  tags                               = local.tags

  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
    minimum_count         = 0
    maximum_count         = 0
  }

  depends_on = [
    azurerm_subnet_nat_gateway_association.demo,
    azurerm_subnet_network_security_group_association.demo,
  ]
}

resource "azapi_resource" "agent_requests" {
  count     = var.deploy_runtime ? 1 : 0
  type      = "Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01"
  name      = "agent-requests"
  parent_id = "${azurerm_storage_account.operations.id}/queueServices/default"
  body      = { properties = {} }
}

resource "azurerm_role_assignment" "agent_requests_sender" {
  count                = var.deploy_runtime ? 1 : 0
  scope                = azapi_resource.agent_requests[0].id
  role_definition_name = "Storage Queue Data Message Sender"
  principal_id         = azurerm_user_assigned_identity.demo["agent"].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "runner_requests_processor" {
  count                = var.deploy_runtime ? 1 : 0
  scope                = azapi_resource.agent_requests[0].id
  role_definition_name = "Storage Queue Data Message Processor"
  principal_id         = azurerm_user_assigned_identity.demo["runner"].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "agent_evidence_reader" {
  count                = var.deploy_runtime ? 1 : 0
  scope                = azapi_resource.evidence_container.id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = azurerm_user_assigned_identity.demo["agent"].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "collector_reader" {
  for_each = var.deploy_runtime ? merge(
    { for name, vm in azurerm_linux_virtual_machine.target : name => vm.id },
    { vault = azurerm_recovery_services_vault.demo.id },
  ) : {}
  scope                = each.value
  role_definition_name = "Reader"
  principal_id         = azurerm_user_assigned_identity.demo["collector"].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "collector_logs" {
  count                = var.deploy_runtime ? 1 : 0
  scope                = azurerm_log_analytics_workspace.demo.id
  role_definition_name = "Log Analytics Reader"
  principal_id         = azurerm_user_assigned_identity.demo["collector"].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_user_assigned_identity" "bootstrap" {
  count               = var.deploy_runtime ? 1 : 0
  name                = "id-asre-bootstrap-cus"
  location            = var.location
  resource_group_name = azurerm_resource_group.demo["control"].name
  tags                = local.tags
}

resource "azurerm_role_definition" "credential_bootstrap" {
  count       = var.deploy_runtime ? 1 : 0
  name        = "ASRE credential bootstrap"
  scope       = azurerm_resource_group.demo["control"].id
  description = "Generate and persist the isolated runner key and authenticated host trust; no delete or purge."
  permissions {
    actions = []
    data_actions = [
      "Microsoft.KeyVault/vaults/secrets/getSecret/action",
      "Microsoft.KeyVault/vaults/secrets/setSecret/action",
    ]
  }
  assignable_scopes = [azurerm_resource_group.demo["control"].id]
}

resource "azurerm_role_assignment" "credential_bootstrap" {
  count              = var.deploy_runtime ? 1 : 0
  scope              = azurerm_key_vault.operations.id
  role_definition_id = azurerm_role_definition.credential_bootstrap[0].role_definition_resource_id
  principal_id       = azurerm_user_assigned_identity.bootstrap[0].principal_id
  principal_type     = "ServicePrincipal"
}

locals {
  runtime_jobs = var.deploy_runtime ? merge({
    worker    = { mode = "queue", cron = "* * * * *" }
    collector = { mode = "collect", cron = "*/5 * * * *" }
    bootstrap = { mode = "bootstrap", cron = null }
    }, var.enable_servicenow_feedback ? {
    feedback = { mode = "feedback", cron = "* * * * *" }
  } : {}) : {}
}

resource "azurerm_container_app_job" "operations" {
  for_each                     = local.runtime_jobs
  name                         = "job-asre-${each.key}-cus"
  location                     = var.location
  resource_group_name          = azurerm_resource_group.demo["control"].name
  container_app_environment_id = azurerm_container_app_environment.operations[0].id
  workload_profile_name        = "Consumption"
  replica_timeout_in_seconds   = 300
  replica_retry_limit          = 0
  tags                         = local.tags

  dynamic "manual_trigger_config" {
    for_each = each.key == "bootstrap" ? [true] : []
    content {
      parallelism              = 1
      replica_completion_count = 1
    }
  }
  dynamic "schedule_trigger_config" {
    for_each = each.key == "bootstrap" ? [] : [true]
    content {
      cron_expression          = each.value.cron
      parallelism              = 1
      replica_completion_count = 1
    }
  }
  identity {
    type = "UserAssigned"
    identity_ids = concat(
      [azurerm_user_assigned_identity.demo["pull"].id],
      each.key == "bootstrap" ? [azurerm_user_assigned_identity.bootstrap[0].id] :
      each.key == "collector" ? [azurerm_user_assigned_identity.demo["collector"].id] :
      each.key == "feedback" ? [azurerm_user_assigned_identity.demo["runner"].id] :
      [azurerm_user_assigned_identity.demo["runner"].id, azurerm_user_assigned_identity.demo["collector"].id],
    )
  }
  registry {
    server   = azurerm_container_registry.demo.login_server
    identity = azurerm_user_assigned_identity.demo["pull"].id
  }
  template {
    container {
      name    = "operations"
      image   = "${azurerm_container_registry.demo.login_server}/asre-operations@${var.runtime_image_digest}"
      cpu     = 0.25
      memory  = "0.5Gi"
      command = ["python", "-m", "operations.worker"]
      args    = [each.value.mode]
      dynamic "env" {
        for_each = each.key == "bootstrap" ? {
          ASRE_BOOTSTRAP_CLIENT_ID = azurerm_user_assigned_identity.bootstrap[0].client_id
          ASRE_CONTEXT             = jsonencode(local.context_core)
          } : merge({
            ASRE_RUNNER_CLIENT_ID    = azurerm_user_assigned_identity.demo["runner"].client_id
            ASRE_COLLECTOR_CLIENT_ID = azurerm_user_assigned_identity.demo["collector"].client_id
            ASRE_EXECUTION_MODE      = "Review"
            ASRE_CONTEXT             = jsonencode(local.context_core)
            }, each.key == "feedback" ? {
            ASRE_SERVICENOW_FEEDBACK_ENABLED = "true"
        } : {})
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }
  depends_on = [
    azurerm_role_assignment.pull,
    azurerm_role_assignment.credential_bootstrap,
    azurerm_role_assignment.collector_reader,
    azurerm_role_assignment.feedback_thread_sender,
    azurerm_role_assignment.collector_logs,
    azurerm_role_assignment.runner_requests_processor,
    azurerm_role_assignment.runner_secret,
    azurerm_role_assignment.table_writer,
    azurerm_role_assignment.blob_writer,
  ]
}
