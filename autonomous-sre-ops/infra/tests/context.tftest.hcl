# Offline proof that the Terraform root module emits the deployment context document (asre-context/v1)
# and hands the same static core to every runtime job and to the dashboard. Nothing here contacts Azure:
# both providers are mocked, every run only plans, and the override values are the synthetic fixture
# values from tests/fixtures/context.synthetic.json. The SSH public keys are assembled at run time from
# the OpenSSH wire format, so no key material is stored in this file.

mock_provider "azurerm" {}
mock_provider "azapi" {}

variables {
  subscription_id       = "00000001-000a-4000-a000-000000000001"
  tenant_id             = "00000002-000a-4000-9000-000000000002"
  admin_ssh_public_key  = "ssh-ed25519 ${base64encode("\u0000\u0000\u0000\u000bssh-ed25519\u0000\u0000\u0000 ${format("%032d", 1)}")} offline-admin"
  runner_ssh_public_key = "ssh-ed25519 ${base64encode("\u0000\u0000\u0000\u000bssh-ed25519\u0000\u0000\u0000 ${format("%032d", 2)}")} offline-runner"
  operator_object_id    = "00000003-000a-4000-9000-000000000003"
  servicenow = {
    origin           = "https://example-instance.service-now.com"
    api_base         = "/api/x_0000000_asre/case_adapter/v1"
    client_id        = "0000000000000000000000000000003a"
    facade_client_id = "0000000000000000000000000000005a"
    assignment_group = "0000000000000000000000000000007a"
    configuration_items = {
      linux  = "0000000000000000000000000000014a"
      patch  = "0000000000000000000000000000001a"
      backup = "0000000000000000000000000000012a"
    }
  }
  # The expected document: the synthetic context fixture, field by field.
  synthetic = {
    schema             = "asre-context/v1"
    subscription_id    = "00000001-000a-4000-a000-000000000001"
    tenant_id          = "00000002-000a-4000-9000-000000000002"
    location           = "centralus"
    name_suffix        = "8a327010f9"
    operator_object_id = "00000003-000a-4000-9000-000000000003"
    resource_groups = {
      control = "rg-asre-control-cus"
      linux   = "rg-asre-linux-cus"
      patch   = "rg-asre-patch-cus"
      backup  = "rg-asre-backup-cus"
    }
    log_analytics = {
      name        = "law-asre-cus"
      customer_id = "00000062-000a-4000-8000-000000000062"
    }
    registry = {
      name         = "acrasre8a327010f9"
      login_server = "acrasre8a327010f9.azurecr.io"
    }
    key_vault = {
      name = "kv-asre-8a327010f9"
      uri  = "https://kv-asre-8a327010f9.vault.azure.net/"
    }
    storage = {
      account_name   = "stasre8a327010f9"
      blob_endpoint  = "https://stasre8a327010f9.blob.core.windows.net"
      table_endpoint = "https://stasre8a327010f9.table.core.windows.net"
    }
    recovery_vault = {
      name           = "rsv-asre-cus"
      resource_group = "rg-asre-backup-cus"
    }
    identities = {
      agent = {
        client_id    = "00000028-000a-4000-9000-000000000028"
        principal_id = "00000072-000a-4000-8000-000000000072"
      }
      runner = {
        client_id    = "00000071-000a-4000-8000-000000000071"
        principal_id = "00000073-000a-4000-8000-000000000073"
      }
      collector = {
        client_id    = "00000074-000a-4000-8000-000000000074"
        principal_id = "00000075-000a-4000-8000-000000000075"
      }
      pull = {
        client_id    = "00000076-000a-4000-8000-000000000076"
        principal_id = "00000077-000a-4000-8000-000000000077"
      }
      bootstrap = {
        client_id    = "00000078-000a-4000-8000-000000000078"
        principal_id = "00000079-000a-4000-8000-000000000079"
      }
    }
    targets = {
      linux = {
        name           = "vm-asre-linux-cus"
        resource_group = "rg-asre-linux-cus"
        address        = "10.42.0.10"
      }
      patch = {
        name           = "vm-asre-patch-cus"
        resource_group = "rg-asre-patch-cus"
        address        = "10.42.0.11"
      }
      backup = {
        name           = "vm-asre-backup-cus"
        resource_group = "rg-asre-backup-cus"
        address        = "10.42.0.12"
      }
    }
    agent = {
      name     = "asre-demo-cus"
      endpoint = "https://asre-demo-cus--0a1b2c3d.4e5f6a7b.centralus.azuresre.ai"
    }
    servicenow = {
      origin           = "https://example-instance.service-now.com"
      api_base         = "/api/x_0000000_asre/case_adapter/v1"
      client_id        = "0000000000000000000000000000003a"
      facade_client_id = "0000000000000000000000000000005a"
      assignment_group = "0000000000000000000000000000007a"
      configuration_items = {
        linux  = "0000000000000000000000000000014a"
        patch  = "0000000000000000000000000000001a"
        backup = "0000000000000000000000000000012a"
      }
    }
  }
}

override_resource {
  target          = azurerm_user_assigned_identity.demo["agent"]
  override_during = plan
  values = {
    id           = "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-asre-agent-cus"
    client_id    = "00000028-000a-4000-9000-000000000028"
    principal_id = "00000072-000a-4000-8000-000000000072"
  }
}

override_resource {
  target          = azurerm_user_assigned_identity.demo["runner"]
  override_during = plan
  values = {
    id           = "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-asre-runner-cus"
    client_id    = "00000071-000a-4000-8000-000000000071"
    principal_id = "00000073-000a-4000-8000-000000000073"
  }
}

override_resource {
  target          = azurerm_user_assigned_identity.demo["collector"]
  override_during = plan
  values = {
    id           = "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-asre-collector-cus"
    client_id    = "00000074-000a-4000-8000-000000000074"
    principal_id = "00000075-000a-4000-8000-000000000075"
  }
}

override_resource {
  target          = azurerm_user_assigned_identity.demo["pull"]
  override_during = plan
  values = {
    id           = "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-asre-pull-cus"
    client_id    = "00000076-000a-4000-8000-000000000076"
    principal_id = "00000077-000a-4000-8000-000000000077"
  }
}

override_resource {
  target          = azurerm_user_assigned_identity.bootstrap[0]
  override_during = plan
  values = {
    id           = "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-asre-bootstrap-cus"
    client_id    = "00000078-000a-4000-8000-000000000078"
    principal_id = "00000079-000a-4000-8000-000000000079"
  }
}

override_resource {
  target          = azurerm_log_analytics_workspace.demo
  override_during = plan
  values = {
    id           = "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.OperationalInsights/workspaces/law-asre-cus"
    workspace_id = "00000062-000a-4000-8000-000000000062"
  }
}

override_resource {
  target          = azurerm_container_registry.demo
  override_during = plan
  values = {
    id           = "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.ContainerRegistry/registries/acrasre8a327010f9"
    login_server = "acrasre8a327010f9.azurecr.io"
  }
}

override_resource {
  target          = azurerm_key_vault.operations
  override_during = plan
  values = {
    id        = "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.KeyVault/vaults/kv-asre-8a327010f9"
    vault_uri = "https://kv-asre-8a327010f9.vault.azure.net/"
  }
}

override_resource {
  target          = azurerm_storage_account.operations
  override_during = plan
  values = {
    id                     = "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.Storage/storageAccounts/stasre8a327010f9"
    primary_blob_endpoint  = "https://stasre8a327010f9.blob.core.windows.net/"
    primary_table_endpoint = "https://stasre8a327010f9.table.core.windows.net/"
  }
}

override_resource {
  target          = azurerm_recovery_services_vault.demo
  override_during = plan
  values = {
    id = "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-backup-cus/providers/Microsoft.RecoveryServices/vaults/rsv-asre-cus"
  }
}

override_resource {
  target          = azurerm_linux_virtual_machine.target["linux"]
  override_during = plan
  values = {
    id = "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-linux-cus/providers/Microsoft.Compute/virtualMachines/vm-asre-linux-cus"
  }
}

override_resource {
  target          = azurerm_linux_virtual_machine.target["patch"]
  override_during = plan
  values = {
    id = "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-patch-cus/providers/Microsoft.Compute/virtualMachines/vm-asre-patch-cus"
  }
}

override_resource {
  target          = azurerm_linux_virtual_machine.target["backup"]
  override_during = plan
  values = {
    id = "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-backup-cus/providers/Microsoft.Compute/virtualMachines/vm-asre-backup-cus"
  }
}

run "phase1_defaults_emit_the_synthetic_context" {
  command = plan

  assert {
    condition     = jsonencode(output.context) == jsonencode(merge(var.synthetic, { identities = { for key, value in var.synthetic.identities : key => value if key != "bootstrap" }, agent = { name = var.synthetic.agent.name, endpoint = null } }))
    error_message = "The Phase 1 context must equal the synthetic document with identities.bootstrap omitted and agent.endpoint null."
  }

  assert {
    condition     = output.context.name_suffix == substr(sha256("${var.subscription_id}:asre-demo"), 0, 10) && output.context.name_suffix == var.synthetic.name_suffix
    error_message = "name_suffix must be the first 10 hex characters of sha256(<subscription_id>:asre-demo)."
  }

  assert {
    condition     = !contains(keys(output.context.identities), "bootstrap") && output.context.agent.endpoint == null
    error_message = "Phase 1 must omit identities.bootstrap and carry a null agent.endpoint."
  }

  assert {
    condition     = output.context.operator_object_id == var.synthetic.operator_object_id && output.context.servicenow == var.synthetic.servicenow
    error_message = "operator_object_id and servicenow must flow from their variables into the document."
  }

  assert {
    condition     = output.context.storage.blob_endpoint == var.synthetic.storage.blob_endpoint && output.context.storage.table_endpoint == var.synthetic.storage.table_endpoint
    error_message = "Storage endpoints must carry no trailing slash."
  }

  assert {
    condition     = output.context.key_vault.uri == var.synthetic.key_vault.uri && endswith(output.context.key_vault.uri, "/")
    error_message = "The Key Vault URI must keep its trailing slash."
  }

  assert {
    condition     = length(jsonencode(local.context_core)) < 4096
    error_message = "The static ASRE_CONTEXT value must stay under 4096 bytes."
  }

  assert {
    condition     = !contains(keys(local.context_core), "operator_object_id") && !contains(keys(local.context_core.agent), "endpoint")
    error_message = "The static core must exclude operator_object_id and agent.endpoint."
  }

  assert {
    condition     = length(azurerm_container_app_job.operations) == 0 && length(azurerm_key_vault.dashboard) == 0
    error_message = "Phase 1 defaults must create no runtime job and no dashboard."
  }
}

run "runtime_adds_bootstrap_identity_and_the_static_context_env" {
  command = plan

  variables {
    deploy_runtime       = true
    runtime_image_digest = "sha256:${format("%064d", 7)}"
  }

  assert {
    condition     = jsonencode(output.context) == jsonencode(merge(var.synthetic, { agent = { name = var.synthetic.agent.name, endpoint = null } }))
    error_message = "deploy_runtime must add identities.bootstrap and nothing else to the Phase 1 document."
  }

  assert {
    condition     = output.context.identities.bootstrap == var.synthetic.identities.bootstrap
    error_message = "identities.bootstrap must carry the bootstrap identity client and principal ids."
  }

  assert {
    condition     = join(",", sort(keys(azurerm_container_app_job.operations))) == "bootstrap,collector,worker"
    error_message = "Without feedback the runtime must create exactly the bootstrap, collector and worker jobs."
  }

  assert {
    condition = alltrue([
      for job in azurerm_container_app_job.operations :
      [for variable in job.template[0].container[0].env : variable.value if variable.name == "ASRE_CONTEXT"] == [jsonencode(local.context_core)]
    ])
    error_message = "Every runtime job must carry exactly one ASRE_CONTEXT equal to jsonencode(local.context_core)."
  }

  assert {
    condition = alltrue([
      for job in azurerm_container_app_job.operations : alltrue([
        for variable in job.template[0].container[0].env :
        length(variable.value) < 4096 && (variable.name != "ASRE_CONTEXT" ? true : (
          !contains(keys(jsondecode(variable.value)), "operator_object_id") &&
          !contains(keys(jsondecode(variable.value).agent), "endpoint") &&
          jsondecode(variable.value).identities.bootstrap.client_id == var.synthetic.identities.bootstrap.client_id
        ))
      ])
    ])
    error_message = "ASRE_CONTEXT must stay under 4096 bytes, exclude operator_object_id and agent.endpoint, and include the bootstrap identity."
  }

  assert {
    condition = (
      join(",", sort([for variable in azurerm_container_app_job.operations["bootstrap"].template[0].container[0].env : variable.name])) == "ASRE_BOOTSTRAP_CLIENT_ID,ASRE_CONTEXT" &&
      join(",", sort([for variable in azurerm_container_app_job.operations["worker"].template[0].container[0].env : variable.name])) == "ASRE_COLLECTOR_CLIENT_ID,ASRE_CONTEXT,ASRE_EXECUTION_MODE,ASRE_RUNNER_CLIENT_ID" &&
      join(",", [for variable in azurerm_container_app_job.operations["worker"].template[0].container[0].env : variable.value if variable.name == "ASRE_EXECUTION_MODE"]) == "Review"
    )
    error_message = "The existing ASRE_* variable names and the Review execution mode must be unchanged."
  }
}

run "agent_and_feedback_complete_the_synthetic_context" {
  command = plan

  variables {
    deploy_managed_agent       = true
    agent_model                = { name = "Automatic", provider = "MicrosoftFoundry" }
    deploy_runtime             = true
    runtime_image_digest       = "sha256:${format("%064d", 7)}"
    enable_servicenow_feedback = true
  }

  override_resource {
    target          = azapi_resource.agent[0]
    override_during = plan
    values = {
      id = "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.App/agents/asre-demo-cus"
      output = {
        properties = {
          agentEndpoint = "https://asre-demo-cus--0a1b2c3d.4e5f6a7b.centralus.azuresre.ai"
        }
      }
    }
  }

  assert {
    condition     = jsonencode(output.context) == jsonencode(var.synthetic)
    error_message = "With the agent, runtime and ServiceNow values present the context must equal the synthetic document exactly."
  }

  assert {
    condition     = join(",", sort(keys(azurerm_container_app_job.operations))) == "bootstrap,collector,feedback,worker"
    error_message = "Enabling feedback must add exactly the feedback job."
  }

  assert {
    condition = alltrue([
      for job in azurerm_container_app_job.operations :
      [for variable in job.template[0].container[0].env : variable.value if variable.name == "ASRE_CONTEXT"] == [jsonencode(local.context_core)]
    ])
    error_message = "Every runtime job, feedback included, must carry ASRE_CONTEXT equal to jsonencode(local.context_core)."
  }

  assert {
    condition = (
      [for variable in azurerm_container_app_job.operations["feedback"].template[0].container[0].env : variable.value if variable.name == "ASRE_SERVICENOW_FEEDBACK_ENABLED"] == ["true"] &&
      jsondecode(jsonencode(local.context_core)).servicenow.origin == var.synthetic.servicenow.origin
    )
    error_message = "The feedback job must keep ASRE_SERVICENOW_FEEDBACK_ENABLED and receive the ServiceNow identifiers in its context."
  }

  assert {
    condition     = azurerm_role_assignment.agent_administrator[0].principal_id == var.operator_object_id && azurerm_role_assignment.agent_administrator[0].principal_type == "User" && azurerm_role_assignment.agent_administrator[0].role_definition_name == "SRE Agent Administrator"
    error_message = "The operator must hold SRE Agent Administrator on the agent."
  }
}

run "dashboard_names_follow_the_suffix_contract" {
  command = plan

  variables {
    deploy_managed_agent = true
    agent_model          = { name = "Automatic", provider = "MicrosoftFoundry" }
    deploy_runtime       = true
    runtime_image_digest = "sha256:${format("%064d", 7)}"
    dashboard = {
      phase              = "private"
      image_digest       = "sha256:${format("%064d", 8)}"
      entra_client_id    = "00000081-000a-4000-8000-000000000081"
      secret_version_uri = "https://kv-asre-dash-8a327010f9.vault.azure.net/secrets/entra-client-secret/${format("%032d", 9)}"
      viewer_group_id    = "00000042-000a-4000-8000-000000000042"
    }
  }

  override_resource {
    target          = azapi_resource.agent[0]
    override_during = plan
    values = {
      id = "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.App/agents/asre-demo-cus"
    }
  }

  override_resource {
    target          = azurerm_container_app_environment.dashboard[0]
    override_during = plan
    values = {
      id             = "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-dash-8a327010f9/providers/Microsoft.App/managedEnvironments/cae-asre-dash-8a327010f9"
      default_domain = "example.centralus.azurecontainerapps.io"
    }
  }

  override_resource {
    target          = azurerm_key_vault.dashboard[0]
    override_during = plan
    values = {
      id        = "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-dash-8a327010f9/providers/Microsoft.KeyVault/vaults/kv-asre-dash-8a327010f9"
      vault_uri = "https://kv-asre-dash-8a327010f9.vault.azure.net/"
    }
  }

  override_resource {
    target          = azurerm_user_assigned_identity.dashboard[0]
    override_during = plan
    values = {
      id           = "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-dash-8a327010f9/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-asre-dash-8a327010f9"
      client_id    = "00000082-000a-4000-8000-000000000082"
      principal_id = "00000083-000a-4000-8000-000000000083"
    }
  }

  override_resource {
    target          = azurerm_resource_group.dashboard[0]
    override_during = plan
    values = {
      id = "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-dash-8a327010f9"
    }
  }

  assert {
    condition = (
      azurerm_resource_group.dashboard[0].name == "rg-asre-dash-${var.synthetic.name_suffix}" &&
      azurerm_container_app_environment.dashboard[0].name == "cae-asre-dash-${var.synthetic.name_suffix}" &&
      azurerm_user_assigned_identity.dashboard[0].name == "id-asre-dash-${var.synthetic.name_suffix}" &&
      azurerm_key_vault.dashboard[0].name == "kv-asre-dash-${var.synthetic.name_suffix}" &&
      local.dashboard_name == "ca-asre-dash-${var.synthetic.name_suffix}"
    )
    error_message = "Dashboard resources must be named <prefix>-asre-dash-<name_suffix>."
  }

  assert {
    condition     = length(azurerm_key_vault.dashboard[0].name) <= 24 && length(local.dashboard_name) <= 32
    error_message = "The dashboard vault name must not exceed 24 characters and the container app name 32."
  }

  assert {
    condition = (
      output.dashboard.viewer_group_id == var.dashboard.viewer_group_id &&
      output.dashboard.app_name == local.dashboard_name &&
      output.dashboard.vault_uri == "https://kv-asre-dash-${var.synthetic.name_suffix}.vault.azure.net/" &&
      join(",", sort(keys(output.dashboard))) == "app_name,environment_name,identity_client_id,identity_id,phase,public_origin,resource_group,resource_id,vault_uri,viewer_group_id"
    )
    error_message = "The dashboard output must keep its keys and take the group, vault and app name from the same locals."
  }

  assert {
    condition     = azurerm_role_assignment.dashboard_credential_setter[0].principal_id == var.operator_object_id && azurerm_role_assignment.dashboard_credential_setter[0].principal_type == "User"
    error_message = "The operator must be the dashboard credential setter."
  }

  assert {
    condition = (
      [for container in azapi_resource.dashboard[0].body.properties.template.containers : [for variable in container.env : variable.value if variable.name == "ASRE_CONTEXT"]] == [[jsonencode(local.context_core)]] &&
      azapi_resource.dashboard_auth[0].body.properties.identityProviders.azureActiveDirectory.validation.defaultAuthorizationPolicy.allowedPrincipals.groups == [var.dashboard.viewer_group_id]
    )
    error_message = "The dashboard app must carry ASRE_CONTEXT and restrict sign-in to the configured viewer group."
  }
}

run "uppercase_subscription_id_is_rejected" {
  command = plan

  variables {
    subscription_id = "0000000A-000A-4000-A000-00000000000A"
  }

  expect_failures = [var.subscription_id]
}

run "malformed_subscription_id_is_rejected" {
  command = plan

  variables {
    subscription_id = ""
  }

  expect_failures = [var.subscription_id]
}

run "missing_subscription_id_is_rejected" {
  command = plan

  variables {
    subscription_id = null
  }

  expect_failures = [var.subscription_id]
}

run "uppercase_tenant_id_is_rejected" {
  command = plan

  variables {
    tenant_id = "0000000B-000A-4000-9000-00000000000B"
  }

  expect_failures = [var.tenant_id]
}

run "malformed_tenant_id_is_rejected" {
  command = plan

  variables {
    tenant_id = "not-a-guid"
  }

  expect_failures = [var.tenant_id]
}

run "missing_tenant_id_is_rejected" {
  command = plan

  variables {
    tenant_id = null
  }

  expect_failures = [var.tenant_id]
}

run "managed_agent_requires_the_operator_object_id" {
  command = plan

  variables {
    deploy_managed_agent = true
    agent_model          = { name = "Automatic", provider = "MicrosoftFoundry" }
    operator_object_id   = null
  }

  expect_failures = [var.operator_object_id]
}

run "uppercase_operator_object_id_is_rejected" {
  command = plan

  variables {
    operator_object_id = "0000000C-000A-4000-9000-00000000000C"
  }

  expect_failures = [var.operator_object_id]
}

run "malformed_servicenow_origin_is_rejected" {
  command = plan

  variables {
    servicenow = {
      origin           = "http://example-instance.service-now.com"
      api_base         = "/api/x_0000000_asre/case_adapter/v1"
      client_id        = "0000000000000000000000000000003a"
      facade_client_id = "0000000000000000000000000000005a"
      assignment_group = "0000000000000000000000000000007a"
      configuration_items = {
        linux  = "0000000000000000000000000000014a"
        patch  = "0000000000000000000000000000001a"
        backup = "0000000000000000000000000000012a"
      }
    }
  }

  expect_failures = [var.servicenow]
}

run "malformed_servicenow_identifier_is_rejected" {
  command = plan

  variables {
    servicenow = {
      origin           = "https://example-instance.service-now.com"
      api_base         = "/api/x_0000000_asre/case_adapter/v1"
      client_id        = "0000000000000000000000000000003A"
      facade_client_id = "0000000000000000000000000000005a"
      assignment_group = "0000000000000000000000000000007a"
      configuration_items = {
        linux  = "0000000000000000000000000000014a"
        patch  = "0000000000000000000000000000001a"
        backup = "0000000000000000000000000000012a"
      }
    }
  }

  expect_failures = [var.servicenow]
}

run "dashboard_phase_requires_the_viewer_group" {
  command = plan

  variables {
    deploy_managed_agent = true
    agent_model          = { name = "Automatic", provider = "MicrosoftFoundry" }
    deploy_runtime       = true
    runtime_image_digest = "sha256:${format("%064d", 7)}"
    dashboard            = { phase = "bootstrap" }
  }

  expect_failures = [var.dashboard]
}

run "dashboard_phase_requires_the_operator_object_id" {
  command = plan

  variables {
    deploy_managed_agent = true
    agent_model          = { name = "Automatic", provider = "MicrosoftFoundry" }
    deploy_runtime       = true
    runtime_image_digest = "sha256:${format("%064d", 7)}"
    operator_object_id   = null
    dashboard = {
      phase           = "bootstrap"
      viewer_group_id = "00000042-000a-4000-8000-000000000042"
    }
  }

  # The managed agent (which the dashboard requires) already demands the operator, so the missing
  # value is reported on the operator variable; the dashboard rule is defence in depth behind it.
  expect_failures = [var.operator_object_id]
}

run "dashboard_secret_must_name_the_suffix_derived_vault" {
  command = plan

  variables {
    deploy_managed_agent = true
    agent_model          = { name = "Automatic", provider = "MicrosoftFoundry" }
    deploy_runtime       = true
    runtime_image_digest = "sha256:${format("%064d", 7)}"
    dashboard = {
      phase              = "private"
      image_digest       = "sha256:${format("%064d", 8)}"
      entra_client_id    = "00000081-000a-4000-8000-000000000081"
      secret_version_uri = "https://kv-asre-dash-0123456789.vault.azure.net/secrets/entra-client-secret/${format("%032d", 9)}"
      viewer_group_id    = "00000042-000a-4000-8000-000000000042"
    }
  }

  expect_failures = [var.dashboard]
}
