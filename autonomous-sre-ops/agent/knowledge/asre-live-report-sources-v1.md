# ASRE Operations Overview: live data sources

This is trusted query guidance, not report data or permission to perform actions.
Use the existing native connectors and their existing managed identity. Never
create tickets, run faults, start investigations, change infrastructure or add
tools to make a report appear complete.

## Connector and query scope

- Workspace: `law-asre-cus`, resource group `rg-asre-control-cus`.
- Workspace customer ID: `00000062-000a-4000-8000-000000000062`.
- Subscription: `00000001-000a-4000-a000-000000000001`.
- Workspace connector: `asre-operations-logs`; native backend: `monitor-client`.
- Native MCP workspace query tool:
  `system-mcp-monitor_monitor_workspace_log_query`.

Filesystem utilization and capacity trends use real `Perf` samples for
`/srv/asre-demo`. Alert instances, patch installation results and backup jobs
are **Azure Resource Graph provider tables**, not ordinary local workspace
tables. Their absence from a workspace table listing or an empty
`AzureDiagnostics` query does not establish that these records are inaccessible.

Azure Monitor Logs supports read-only cross-service KQL:
`arg("").AlertsManagementResources`, `arg("").PatchInstallationResources` and
`arg("").RecoveryServicesResources`. Use the full custom queries below through
the same configured MCP workspace query tool that successfully queries Perf.
Keep the successful filesystem call's subscription, workspace and resource-group
arguments. If the tool requires a nonempty table argument, retain `Perf`; pass
the **complete ARG query** in its query argument. Do not prepend `Perf |`, use
the `recent`/`errors` shortcuts, or query these as unqualified local tables.
Microsoft's Monitor MCP implementation passes a full custom query through;
the table argument supplies the table only for predefined query shortcuts.
Set the tool's hours to 24 and result limit to 1000.

The agent's existing `QueryLogAnalyticsByResourceId` native tool independently
returned genuine records from all three providers with these cross-service
expressions. That establishes native data access, not successful execution
through the report's saved MCP tool policy. Probe the MCP calls before saving.
Do not substitute built-in tool output or an embedded snapshot for live report
connector results.

## Azure Monitor alert instances

`alertRule` contains an ARM rule ID, not just a name. Use exact case-insensitive
IDs; a test such as `alertRule startswith "asre-demo-"` incorrectly excludes them.

```kusto
arg("").AlertsManagementResources
| where subscriptionId =~ "00000001-000a-4000-a000-000000000001"
| where type =~ "microsoft.alertsmanagement/alerts"
| extend p=parse_json(properties)
| where tostring(p.essentials.alertRule) in~ (
    "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.Insights/scheduledQueryRules/asre-demo-disk",
    "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.Insights/scheduledQueryRules/asre-demo-patch-installation",
    "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.Insights/scheduledQueryRules/asre-demo-noise-overlap",
    "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.Insights/scheduledQueryRules/asre-demo-capacity-growth",
    "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.Insights/scheduledQueryRules/asre-demo-backup-job")
| extend fired_at=todatetime(p.essentials.startDateTime)
| where fired_at >= ago(24h)
| project alert_id=id, fired_at, rule_id=tostring(p.essentials.alertRule),
    monitor_condition=tostring(p.essentials.monitorCondition),
    alert_state=tostring(p.essentials.alertState),
    severity=tostring(p.essentials.severity),
    alert_target_id=tostring(p.essentials.targetResource)
| order by fired_at desc
| limit 1000
```

Show actual provider alert instances, including the noise-overlap rule. Preserve
the reported target: workspace-scoped alerts must not be relabeled as VM-scoped.
Alert instances and Perf sample counts are different things. Do not infer
deduplication, remediation, or temporal overlap solely from sample counts.

## Azure Update Manager installation records

```kusto
arg("").PatchInstallationResources
| where subscriptionId =~ "00000001-000a-4000-a000-000000000001"
| where type =~ "microsoft.compute/virtualmachines/patchinstallationresults"
| where resourceGroup =~ "rg-asre-patch-cus"
| where id startswith "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-patch-cus/providers/Microsoft.Compute/virtualMachines/vm-asre-patch-cus/patchInstallationResults/"
| extend p=parse_json(properties)
| extend observed_at=todatetime(p.lastModifiedDateTime)
| where observed_at >= ago(24h)
| project installation_id=id, observed_at,
    started_at=todatetime(p.startDateTime), status=tostring(p.status),
    installed_patch_count=toint(p.installedPatchCount),
    failed_patch_count=toint(p.failedPatchCount),
    reboot_status=tostring(p.rebootStatus)
| order by observed_at desc
| limit 1000
```

Show successful as well as failed installations. An installation record with a
provider status and installation ID is required; empty candidate log tables,
assessment-only results and a VM extension's existence are not substitutes.

## Recovery Services backup jobs

```kusto
arg("").RecoveryServicesResources
| where subscriptionId =~ "00000001-000a-4000-a000-000000000001"
| where type =~ "microsoft.recoveryservices/vaults/backupjobs"
| where resourceGroup =~ "rg-asre-backup-cus"
| where id startswith "/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-backup-cus/providers/Microsoft.RecoveryServices/vaults/rsv-asre-cus/backupJobs/"
| extend p=parse_json(properties)
| where tostring(p.backupManagementType) == "AzureIaasVM"
| where tostring(p.operation) == "Backup"
| where tostring(p.entityFriendlyName) == "vm-asre-backup-cus"
| extend started_at=todatetime(p.startTime)
| where started_at >= ago(24h)
| project job_id=id, started_at, ended_at=todatetime(p.endTime),
    status=tostring(p.status), operation=tostring(p.operation),
    vm=tostring(p.entityFriendlyName),
    protected_item_id=tostring(p.backupInstanceId)
| order by started_at desc
| limit 1000
```

Show all returned job statuses and a separate failed count. Completed jobs are
valid panel data, not evidence of a failed backup or an automatic failure case.
Never start or cancel a backup to populate a report.

## Authoring and refresh acceptance

Probe these queries through the configured MCP tool before creating or saving
the five-panel report. Return exact query errors or missing permissions rather
than inventing another connector requirement. Do not claim success from a
non-error response alone: Azure Monitor can suppress underlying ARG errors.
Require actual provider IDs and statuses for initial source qualification.

Apply time-range changes to the timestamps inside each ARG query; the Log
Analytics API's timespan alone does not filter provider timestamps. ARG is
eventually consistent and cross-service results are capped at 1000 records.
Disclose this cap rather than presenting a capped row count as a complete total.
Distinguish a legitimately empty requested interval from source/query failure.

Save only the expected read-only connector tool and arguments in report policy.
Keep discovery and context-upload tools out of refresh actions. Use live calls,
not embedded sample rows, and verify the saved gallery report with Reload.
Do not add ServiceNow or investigation links unless a verified source provides
those links. If a required MCP source still cannot be queried, stop without
saving an incomplete report and identify that specific failed call.

## Authoritative references

- https://learn.microsoft.com/en-us/azure/azure-monitor/logs/azure-monitor-data-explorer-proxy
- https://learn.microsoft.com/en-us/azure/developer/azure-mcp-server/tools/azure-monitor
- https://github.com/microsoft/mcp/blob/main/tools/Azure.Mcp.Tools.Monitor/src/Services/MonitorService.cs
- https://learn.microsoft.com/en-us/azure/sre-agent/live-reports
