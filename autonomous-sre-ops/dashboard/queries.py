"""Fixed read-only queries; provider templates match the native Context guide."""

from dashboard import context as deployment

CTX = deployment.load()

SUBSCRIPTION = CTX.subscription_id
TENANT = CTX.tenant_id
CONTROL = CTX.resource_group_id("control")
AGENT_ID = CTX.agent.resource_id
WORKSPACE_ID = CTX.log_analytics.resource_id
LINUX_ID = CTX.targets["linux"].resource_id
PATCH_ID = CTX.targets["patch"].resource_id
BACKUP_VAULT = CTX.recovery_vault.resource_id
RULE_NAMES = ("disk", "patch-installation", "noise-overlap", "capacity-growth", "backup-job")
RULE_IDS = tuple(
    CONTROL + "/providers/Microsoft.Insights/scheduledQueryRules/asre-demo-" + name
    for name in RULE_NAMES
)
WINDOWS = {"1h": "PT1H", "6h": "PT6H", "24h": "PT24H", "7d": "P7D"}
BUCKETS = {"1h": "5m", "6h": "5m", "24h": "5m", "7d": "15m"}
_RULE_LIST = ",\n    ".join('"' + rule + '"' for rule in RULE_IDS)
_PATCH_GROUP = CTX.targets["patch"].resource_group
_BACKUP_GROUP = CTX.recovery_vault.resource_group
_BACKUP_VM = CTX.targets["backup"].name

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
    "alerts": f'''arg("").AlertsManagementResources
| where subscriptionId =~ "{SUBSCRIPTION}"
| where type =~ "microsoft.alertsmanagement/alerts"
| extend p=parse_json(properties)
| where tostring(p.essentials.alertRule) in~ (
    {_RULE_LIST})
| extend fired_at=todatetime(p.essentials.startDateTime)
| where fired_at >= ago(24h)
| project alert_id=id, fired_at, rule_id=tostring(p.essentials.alertRule),
    monitor_condition=tostring(p.essentials.monitorCondition),
    alert_state=tostring(p.essentials.alertState),
    severity=tostring(p.essentials.severity),
    alert_target_id=tostring(p.essentials.targetResource)
| order by fired_at desc
| limit 1000''',
    "patch": f'''arg("").PatchInstallationResources
| where subscriptionId =~ "{SUBSCRIPTION}"
| where type =~ "microsoft.compute/virtualmachines/patchinstallationresults"
| where resourceGroup =~ "{_PATCH_GROUP}"
| where id startswith "{PATCH_ID}/patchInstallationResults/"
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
    "backup": f'''arg("").RecoveryServicesResources
| where subscriptionId =~ "{SUBSCRIPTION}"
| where type =~ "microsoft.recoveryservices/vaults/backupjobs"
| where resourceGroup =~ "{_BACKUP_GROUP}"
| where id startswith "{BACKUP_VAULT}/backupJobs/"
| extend p=parse_json(properties)
| where tostring(p.backupManagementType) == "AzureIaasVM"
| where tostring(p.operation) == "Backup"
| where tostring(p.entityFriendlyName) == "{_BACKUP_VM}"
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
