"""Fixed read-only queries; provider templates match the native Context guide."""

SUBSCRIPTION = "00000001-000a-4000-a000-000000000001"
TENANT = "00000002-000a-4000-9000-000000000002"
CONTROL = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-asre-control-cus"
AGENT_ID = CONTROL + "/providers/Microsoft.App/agents/asre-demo-cus"
WORKSPACE_ID = CONTROL + "/providers/Microsoft.OperationalInsights/workspaces/law-asre-cus"
LINUX_ID = (
    f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-asre-linux-cus"
    "/providers/Microsoft.Compute/virtualMachines/vm-asre-linux-cus"
)
PATCH_ID = LINUX_ID.replace("linux", "patch")
BACKUP_VAULT = (
    f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-asre-backup-cus"
    "/providers/Microsoft.RecoveryServices/vaults/rsv-asre-cus"
)
RULE_NAMES = ("disk", "patch-installation", "noise-overlap", "capacity-growth", "backup-job")
RULE_IDS = tuple(
    CONTROL + "/providers/Microsoft.Insights/scheduledQueryRules/asre-demo-" + name
    for name in RULE_NAMES
)
WINDOWS = {"1h": "PT1H", "6h": "PT6H", "24h": "PT24H", "7d": "P7D"}
BUCKETS = {"1h": "5m", "6h": "5m", "24h": "5m", "7d": "15m"}

QUERIES = {
    "filesystem": f'''Perf
| where TimeGenerated >= ago(24h)
| where _ResourceId =~ "{LINUX_ID}"
| where ObjectName == "Logical Disk" and CounterName == "% Used Space"
| where InstanceName == "/srv/asre-demo"
| summarize sample_count=count(), used_percent=avg(CounterValue),
    min_used=min(CounterValue), max_used=max(CounterValue),
    latest_sample=max(TimeGenerated)
    by bucket=bin(TimeGenerated, 5m), resource_id=_ResourceId, mount=InstanceName
| order by bucket asc
| limit 1000''',
    "alerts": '''arg("").AlertsManagementResources
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
| limit 1000''',
    "patch": '''arg("").PatchInstallationResources
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
| limit 1000''',
    "backup": '''arg("").RecoveryServicesResources
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
| limit 1000''',
}


def query_for(name: str, window: str) -> str:
    if name not in QUERIES or window not in WINDOWS:
        raise ValueError("Only the four fixed queries and listed time windows are supported.")
    query = QUERIES[name].replace("ago(24h)", f"ago({window})")
    if name == "filesystem":
        query = query.replace("bin(TimeGenerated, 5m)", f"bin(TimeGenerated, {BUCKETS[window]})")
    return query
