resource "azurerm_monitor_data_collection_rule" "guest" {
  name                = "dcr-asre-linux-cus"
  location            = var.location
  resource_group_name = azurerm_resource_group.demo["control"].name
  kind                = "Linux"
  tags                = local.tags

  destinations {
    log_analytics {
      workspace_resource_id = azurerm_log_analytics_workspace.demo.id
      name                  = "workspace"
    }
  }

  data_flow {
    streams      = ["Microsoft-Perf", "Microsoft-Syslog"]
    destinations = ["workspace"]
  }

  data_sources {
    performance_counter {
      name                          = "guest-performance"
      streams                       = ["Microsoft-Perf"]
      sampling_frequency_in_seconds = 30
      counter_specifiers = [
        "\\Memory\\Available MBytes Memory",
        "\\Memory\\% Used Memory",
        "\\Logical Disk(*)\\% Used Space",
        "\\Logical Disk(*)\\Free Megabytes",
        "\\Process(asre-demo)\\Used Memory"
      ]
    }
    syslog {
      name           = "guest-syslog"
      streams        = ["Microsoft-Syslog"]
      facility_names = ["daemon", "kern", "user"]
      log_levels     = ["Debug", "Info", "Notice", "Warning", "Error", "Critical", "Alert", "Emergency"]
    }
  }
}

resource "azurerm_monitor_data_collection_rule_association" "guest" {
  for_each                = local.targets
  name                    = "asre-guest"
  target_resource_id      = azurerm_linux_virtual_machine.target[each.key].id
  data_collection_rule_id = azurerm_monitor_data_collection_rule.guest.id
}

locals {
  native_created_rules = toset(["noise-overlap", "patch-installation", "capacity-growth", "backup-job"])
  linux_id             = azurerm_linux_virtual_machine.target["linux"].id
  alert_queries = {
    disk = {
      severity = 1
      query    = <<-KQL
        Perf
        | where _ResourceId =~ "${local.linux_id}"
        | where ObjectName == "Logical Disk" and CounterName == "% Used Space"
        | where InstanceName == "/srv/asre-demo"
        | summarize Value = max(CounterValue) by _ResourceId
        | where Value >= 80
      KQL
    }
    workload-oom = {
      severity = 1
      query    = <<-KQL
        Syslog
        | where _ResourceId =~ "${local.linux_id}"
        | where SyslogMessage contains "asre-demo" and (SyslogMessage contains "oom-kill" or SyslogMessage contains "Memory cgroup out of memory")
      KQL
    }
    patch-installation = {
      severity = 1
      query    = <<-KQL
        ContainerAppConsoleLogs_CL
        | where Log_s startswith_cs 'ASRE_PATCH_FAILURE_V1 '
        | extend event_json=substring(Log_s, strlen('ASRE_PATCH_FAILURE_V1 '))
        | extend patch_event=parse_json(event_json)
        | where tostring(patch_event.version) == '1.0'
        | where tostring(patch_event.resource_id) == '${azurerm_linux_virtual_machine.target["patch"].id}'
        | extend installation_id=tostring(patch_event.installation_id), status=tostring(patch_event.status),
          failed_patch_count=toint(patch_event.failed_patch_count), provider_time=todatetime(patch_event.provider_time),
          digest=tostring(patch_event.digest)
        | where installation_id matches regex '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        | where digest matches regex '^[0-9a-f]{64}$'
        | where status in ('Failed', 'CompletedWithWarnings') and failed_patch_count > 0
        | where provider_time > ago(10m) and provider_time <= now()
        | project TimeGenerated=provider_time, resource_id='${azurerm_linux_virtual_machine.target["patch"].id}', observed_at=provider_time,
          installation_id, status, failed_patch_count, digest, event_json
      KQL
    }
    heartbeat = {
      severity = 1
      query    = <<-KQL
        let Targets = datatable(_ResourceId:string) [
          "${azurerm_linux_virtual_machine.target["linux"].id}",
          "${azurerm_linux_virtual_machine.target["patch"].id}",
          "${azurerm_linux_virtual_machine.target["backup"].id}"
        ];
        Targets
        | extend _ResourceId=tolower(_ResourceId)
        | join kind=leftouter (Heartbeat | where TimeGenerated > ago(10m) | summarize LastSeen=max(TimeGenerated) by _ResourceId=tolower(_ResourceId)) on _ResourceId
        | where isnull(LastSeen) or LastSeen < ago(5m)
      KQL
    }
    noise-overlap = {
      severity = 2
      query    = <<-KQL
        Perf
        | where _ResourceId =~ "${local.linux_id}"
        | where ObjectName == "Logical Disk" and CounterName == "% Used Space"
        | where InstanceName == "/srv/asre-demo" and CounterValue >= 80
      KQL
    }
    capacity-growth = {
      severity = 2
      query    = <<-KQL
        Perf
        | where _ResourceId =~ "${local.linux_id}"
        | where ObjectName == "Logical Disk" and CounterName == "% Used Space"
        | where InstanceName == "/srv/asre-demo"
        | sort by TimeGenerated asc
        | serialize previous_value=prev(CounterValue), previous_time=prev(TimeGenerated)
        | summarize sample_count=count(), first_observed=min(TimeGenerated), last_observed=max(TimeGenerated),
          first_value=min(CounterValue), last_value=max(CounterValue),
          reversals=countif(isnotnull(previous_value) and CounterValue < previous_value),
          gaps=countif(isnotnull(previous_time) and TimeGenerated - previous_time > 120s) by _ResourceId
        | extend baseline_seconds=(last_observed-first_observed)/1s
        | where sample_count >= 6 and baseline_seconds >= 300 and reversals == 0 and gaps == 0
          and last_value < 80 and last_value > first_value
        | extend forecast_seconds=(80.0-last_value)/(last_value-first_value)*baseline_seconds
        | where forecast_seconds > 0 and forecast_seconds <= 900
      KQL
    }
    backup-job = {
      severity = 1
      query    = <<-KQL
        arg("").RecoveryServicesResources
        | where type =~ 'microsoft.recoveryservices/vaults/backupjobs'
        | where id startswith '${azurerm_recovery_services_vault.demo.id}/backupJobs/'
        | where tostring(properties.backupInstanceId) =~ '${azurerm_recovery_services_vault.demo.id}/backupFabrics/Azure/protectionContainers/IaasVMContainer;iaasvmcontainerv2;rg-asre-backup-cus;vm-asre-backup-cus/protectedItems/VM;iaasvmcontainerv2;rg-asre-backup-cus;vm-asre-backup-cus'
        | where tostring(properties.backupManagementType) == 'AzureIaasVM'
          and tostring(properties.operation) == 'Backup' and tostring(properties.status) == 'Failed'
        | extend observed_at=todatetime(properties.endTime) | where observed_at > ago(10m)
        | project resource_id='${azurerm_linux_virtual_machine.target["backup"].id}', observed_at, job_id=id, status=tostring(properties.status),
          protected_item_id=tostring(properties.backupInstanceId), error_details=substring(tostring(properties.errorDetails), 0, 1600)
      KQL
    }
  }
}

resource "azurerm_monitor_scheduled_query_rules_alert_v2" "guest" {
  for_each                = local.alert_queries
  name                    = "asre-demo-${each.key}"
  resource_group_name     = azurerm_resource_group.demo["control"].name
  location                = var.location
  scopes                  = [contains(local.native_created_rules, each.key) ? lower(azurerm_log_analytics_workspace.demo.id) : azurerm_log_analytics_workspace.demo.id]
  display_name            = contains(local.native_created_rules, each.key) ? "asre-demo-${each.key}" : null
  severity                = each.value.severity
  evaluation_frequency    = contains(["heartbeat", "patch-installation", "backup-job"], each.key) ? "PT5M" : "PT1M"
  window_duration         = "PT10M"
  enabled                 = contains(var.enabled_native_alert_rules, each.key)
  auto_mitigation_enabled = true
  description             = each.key == "patch-installation" ? "ASRE fixed-target terminal Update Manager installation failure; native SRE Review intake." : contains(local.native_created_rules, each.key) ? "ASRE fixed-target native incident signal; native SRE Review intake." : "Disabled until the actual table schema, baseline and SRE intake have passed readiness."
  skip_query_validation   = false
  tags                    = local.tags

  dynamic "identity" {
    for_each = contains(["patch-installation", "backup-job"], each.key) ? [true] : []
    content {
      type         = "UserAssigned"
      identity_ids = [azurerm_user_assigned_identity.demo["agent"].id]
    }
  }

  criteria {
    query                   = contains(local.native_created_rules, each.key) ? join(" ", [for line in split("\n", trimspace(each.value.query)) : trimspace(line)]) : each.value.query
    time_aggregation_method = "Count"
    threshold               = 0
    operator                = "GreaterThan"
    dynamic "dimension" {
      for_each = each.key == "patch-installation" ? [true] : []
      content {
        name     = "installation_id"
        operator = "Include"
        values   = ["*"]
      }
    }
    failing_periods {
      minimum_failing_periods_to_trigger_alert = 1
      number_of_evaluation_periods             = 1
    }
  }
}
