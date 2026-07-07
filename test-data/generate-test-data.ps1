<#
.SYNOPSIS
    Generates a large synthetic, multi-subscription test dataset for the AMBA and
    Monitoring Baselines observability components.
.DESCRIPTION
    Produces Azure Resource Graph-shaped sample data that the workbooks and Grafana
    dashboards consume, so every visualization flow can be tested without a live
    tenant. The generated data is shaped to match the exact KQL queries in:
      - amba-enterprise/visualizations/workbook-alert-health.json
      - monitoring-baselines/visualizations/workbook-monitoring-posture.json

    Flows covered:
      1. Policy compliance states (microsoft.policyinsights/policystates) for:
         - AMBA alerting initiatives          (Alerting-*)
         - Monitoring Baselines initiatives    (Monitoring-Baselines-<env>) for each ring
      2. AMBA alerts (microsoft.alertsmanagement/alerts) incl. alertState for stale-alert flow.
      3. Alert processing rules (microsoft.alertsmanagement/actionrules).
      4. Action groups (microsoft.insights/actiongroups) tagged _deployed_by_amba.
      5. AMBA alert rule definitions (metricalerts / scheduledqueryrules) for inventory.
      6. Policy remediation tasks (microsoft.policyinsights/remediations) for AMBA + Baselines.
      7. A consolidated AMBA-deployed resource inventory (tagged _deployed_by_amba).
      8. A summary report (compliance %, counts per initiative / category / subscription).

    NOTE: This is SYNTHETIC data for local testing only. It is not collected from Azure.
.PARAMETER ResourceCount
    Approximate number of distinct policy-state resources to simulate. Default 2000.
.PARAMETER Environment
    Environment ring used for the Monitoring Baselines initiative when -AllEnvironments
    is not set. Default 'prod'.
.PARAMETER AllEnvironments
    Generate Monitoring Baselines compliance data for every ring (sandbox, pre-prod, prod).
.PARAMETER OutputDir
    Directory to write the dataset files. Default: ./test-data/output
.EXAMPLE
    ./generate-test-data.ps1 -ResourceCount 6000 -AllEnvironments
#>

[CmdletBinding()]
param(
    [Parameter()]
    [int]$ResourceCount = 2000,

    [Parameter()]
    [ValidateSet("sandbox", "pre-prod", "prod")]
    [string]$Environment = "prod",

    [Parameter()]
    [switch]$AllEnvironments,

    [Parameter()]
    [string]$OutputDir
)

$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $OutputDir) { $OutputDir = Join-Path $scriptRoot "output" }
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

# --- Reference data -----------------------------------------------------------

# Multi-subscription estate across all rings plus shared platform subscriptions.
$subscriptions = @(
    # prod landing zones
    @{ id = "11111111-1111-1111-1111-111111111111"; name = "contoso-lz-app-prod-01"; ring = "prod" },
    @{ id = "22222222-2222-2222-2222-222222222222"; name = "contoso-lz-app-prod-02"; ring = "prod" },
    @{ id = "33333333-3333-3333-3333-333333333333"; name = "contoso-lz-data-prod-01"; ring = "prod" },
    @{ id = "44444444-4444-4444-4444-444444444444"; name = "contoso-lz-shared-prod-01"; ring = "prod" },
    # pre-prod landing zones
    @{ id = "55555555-5555-5555-5555-555555555555"; name = "contoso-lz-app-preprod-01"; ring = "pre-prod" },
    @{ id = "66666666-6666-6666-6666-666666666666"; name = "contoso-lz-data-preprod-01"; ring = "pre-prod" },
    # sandbox landing zones
    @{ id = "77777777-7777-7777-7777-777777777777"; name = "contoso-lz-sandbox-01"; ring = "sandbox" },
    @{ id = "88888888-8888-8888-8888-888888888888"; name = "contoso-lz-sandbox-02"; ring = "sandbox" },
    # shared platform subscriptions (apply to every ring)
    @{ id = "99999999-9999-9999-9999-999999999999"; name = "contoso-platform-connectivity"; ring = "platform" },
    @{ id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"; name = "contoso-platform-management"; ring = "platform" },
    @{ id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"; name = "contoso-platform-identity"; ring = "platform" }
)

function Get-SubscriptionsForRing([string]$ring) {
    return @($subscriptions | Where-Object { $_.ring -eq $ring -or $_.ring -eq "platform" })
}

$resourceGroups = @("rg-web-01", "rg-api-01", "rg-data-01", "rg-network-01", "rg-shared-01", "rg-compute-01")

# AMBA alerting initiatives -> representative resource types
$ambaInitiatives = @(
    @{ initiative = "Alerting-VM"; type = "microsoft.compute/virtualmachines"; prefix = "vm" },
    @{ initiative = "Alerting-VMSS"; type = "microsoft.compute/virtualmachinescalesets"; prefix = "vmss" },
    @{ initiative = "Alerting-Storage"; type = "microsoft.storage/storageaccounts"; prefix = "st" },
    @{ initiative = "Alerting-KeyManagement"; type = "microsoft.keyvault/vaults"; prefix = "kv" },
    @{ initiative = "Alerting-Web"; type = "microsoft.web/sites"; prefix = "app" },
    @{ initiative = "Alerting-LoadBalancing"; type = "microsoft.network/loadbalancers"; prefix = "lb" },
    @{ initiative = "Alerting-Connectivity"; type = "microsoft.network/virtualnetworkgateways"; prefix = "vng" },
    @{ initiative = "Alerting-RecoveryServices"; type = "microsoft.recoveryservices/vaults"; prefix = "rsv" }
)

# Monitoring Baselines categories -> built-in policy reference IDs + resource types
$mbPolicies = @(
    @{ group = "azure-monitor-agent"; refId = "ama_linux_vm"; type = "microsoft.compute/virtualmachines"; prefix = "vm-lnx" },
    @{ group = "azure-monitor-agent"; refId = "ama_windows_vm"; type = "microsoft.compute/virtualmachines"; prefix = "vm-win" },
    @{ group = "azure-monitor-agent"; refId = "ama_linux_vmss"; type = "microsoft.compute/virtualmachinescalesets"; prefix = "vmss-lnx" },
    @{ group = "azure-monitor-agent"; refId = "ama_arc_linux"; type = "microsoft.hybridcompute/machines"; prefix = "arc-lnx" },
    @{ group = "diagnostic-settings"; refId = "diag_keyvault"; type = "microsoft.keyvault/vaults"; prefix = "kv" },
    @{ group = "diagnostic-settings"; refId = "diag_storage"; type = "microsoft.storage/storageaccounts"; prefix = "st" },
    @{ group = "diagnostic-settings"; refId = "diag_sql_db"; type = "microsoft.sql/servers/databases"; prefix = "sqldb" },
    @{ group = "diagnostic-settings"; refId = "diag_event_hub"; type = "microsoft.eventhub/namespaces"; prefix = "eh" },
    @{ group = "diagnostic-settings"; refId = "diag_recovery_vault"; type = "microsoft.recoveryservices/vaults"; prefix = "rsv" },
    @{ group = "private-endpoints"; refId = "pe_storage"; type = "microsoft.storage/storageaccounts"; prefix = "st" },
    @{ group = "private-endpoints"; refId = "pe_keyvault"; type = "microsoft.keyvault/vaults"; prefix = "kv" }
)

# Environments (rings) to generate Monitoring Baselines data for.
$mbEnvironments = if ($AllEnvironments) { @("sandbox", "pre-prod", "prod") } else { @($Environment) }

# Per-ring compliance posture multipliers (sandbox is noisier, prod is cleaner).
$ringNonComplianceMultiplier = @{ "sandbox" = 1.6; "pre-prod" = 1.1; "prod" = 0.8 }

$rng = [System.Random]::new(42) # fixed seed for reproducible datasets

function Get-RandomItem($array) { return $array[$rng.Next(0, $array.Count)] }
function Get-ShortId { return [guid]::NewGuid().ToString('N').Substring(0, 6) }

function New-ResourceId($sub, $rg, $type, $name) {
    return "/subscriptions/$sub/resourceGroups/$rg/providers/$type/$name"
}

$now = Get-Date

# --- 1. AMBA alerting policy compliance states -------------------------------

Write-Host "Generating AMBA alerting policy states..." -ForegroundColor Cyan

$ambaStates = New-Object System.Collections.Generic.List[object]
$ambaCount = [math]::Floor($ResourceCount / 2)

for ($i = 0; $i -lt $ambaCount; $i++) {
    $sub = Get-RandomItem $subscriptions
    $rg = Get-RandomItem $resourceGroups
    $def = Get-RandomItem $ambaInitiatives
    $name = "$($def.prefix)-$(Get-ShortId)"
    $isNonCompliant = $rng.NextDouble() -lt 0.12
    $ageDays = $rng.Next(0, 30)

    $ambaStates.Add([ordered]@{
            type       = "microsoft.policyinsights/policystates"
            properties = [ordered]@{
                resourceId                  = New-ResourceId $sub.id $rg $def.type $name
                resourceType                = $def.type
                subscriptionId              = $sub.id
                resourceGroup               = $rg
                policySetDefinitionName     = $def.initiative
                policyDefinitionGroupNames  = @($def.initiative)
                policyDefinitionReferenceId = "$($def.prefix)-alert"
                complianceState             = if ($isNonCompliant) { "NonCompliant" } else { "Compliant" }
                timestamp                   = $now.AddDays(-$ageDays).ToString("o")
            }
        })
}

# --- 2. Monitoring Baselines policy compliance states (per ring) -------------

Write-Host "Generating Monitoring Baselines policy states for: $($mbEnvironments -join ', ')..." -ForegroundColor Cyan

$mbStates = New-Object System.Collections.Generic.List[object]
$mbTotal = $ResourceCount - $ambaCount
$mbPerEnv = [math]::Floor($mbTotal / $mbEnvironments.Count)
$baseRates = @{ "azure-monitor-agent" = 0.18; "diagnostic-settings" = 0.22; "private-endpoints" = 0.35 }

foreach ($env in $mbEnvironments) {
    $mbInitiative = "Monitoring-Baselines-$env"
    $envSubs = Get-SubscriptionsForRing $env
    $mult = $ringNonComplianceMultiplier[$env]

    for ($i = 0; $i -lt $mbPerEnv; $i++) {
        $sub = Get-RandomItem $envSubs
        $rg = Get-RandomItem $resourceGroups
        $pol = Get-RandomItem $mbPolicies
        $name = "$($pol.prefix)-$(Get-ShortId)"
        $rate = [math]::Min(0.95, $baseRates[$pol.group] * $mult)
        $isNonCompliant = $rng.NextDouble() -lt $rate
        $ageDays = $rng.Next(0, 30)

        $mbStates.Add([ordered]@{
                type       = "microsoft.policyinsights/policystates"
                properties = [ordered]@{
                    resourceId                  = New-ResourceId $sub.id $rg $pol.type $name
                    resourceType                = $pol.type
                    subscriptionId              = $sub.id
                    resourceGroup               = $rg
                    policySetDefinitionName     = $mbInitiative
                    policyDefinitionGroupNames  = @($pol.group)
                    policyDefinitionReferenceId = $pol.refId
                    complianceState             = if ($isNonCompliant) { "NonCompliant" } else { "Compliant" }
                    timestamp                   = $now.AddDays(-$ageDays).ToString("o")
                }
            })
    }
}

# --- 3. AMBA alerts (alert-health workbook) ----------------------------------

Write-Host "Generating AMBA alert records..." -ForegroundColor Cyan

$alerts = New-Object System.Collections.Generic.List[object]
$alertCount = [math]::Floor($ResourceCount / 4)
$severities = @("Sev0", "Sev1", "Sev2", "Sev3", "Sev4")
$alertRules = @("CPU Percentage", "Available Memory", "OS Disk Latency", "Heartbeat", "Data Disk IOPS", "Network In", "VM Availability")
$alertStates = @("New", "Acknowledged", "Closed")

for ($i = 0; $i -lt $alertCount; $i++) {
    $sub = Get-RandomItem $subscriptions
    $rg = Get-RandomItem $resourceGroups
    $sev = Get-RandomItem $severities
    $rule = Get-RandomItem $alertRules
    $startHoursAgo = $rng.Next(0, 168) # within 7 days
    $isFired = $rng.NextDouble() -lt 0.65
    # Bias toward "New" so the stale-alert (>24h unacknowledged) flow has data.
    $state = if ($rng.NextDouble() -lt 0.5) { "New" } else { Get-RandomItem $alertStates }
    $name = "vm-$(Get-ShortId)"

    $alerts.Add([ordered]@{
            type       = "microsoft.alertsmanagement/alerts"
            properties = [ordered]@{
                essentials = [ordered]@{
                    severity         = $sev
                    alertRule        = "$rule - AMBA"
                    monitorCondition = if ($isFired) { "Fired" } else { "Resolved" }
                    alertState       = $state
                    targetResource   = New-ResourceId $sub.id $rg "microsoft.compute/virtualmachines" $name
                    startDateTime    = $now.AddHours(-$startHoursAgo).ToString("o")
                    monitorService   = "Platform"
                    signalType       = "Metric"
                }
            }
        })
}

# --- 4. Alert processing rules (microsoft.alertsmanagement/actionrules) ------

Write-Host "Generating alert processing rules..." -ForegroundColor Cyan

$actionRules = New-Object System.Collections.Generic.List[object]
$ruleTypes = @("AddActionGroups", "RemoveAllActionGroups", "Suppression")
foreach ($sub in $subscriptions) {
    $ruleCountPerSub = $rng.Next(1, 4)
    for ($r = 0; $r -lt $ruleCountPerSub; $r++) {
        $rg = "rg-amba-monitoring-001"
        $ruleType = Get-RandomItem $ruleTypes
        $enabled = $rng.NextDouble() -lt 0.85
        $name = "apr-amba-$($ruleType.ToLower())-$(Get-ShortId)"
        $actionRules.Add([ordered]@{
                type           = "microsoft.alertsmanagement/actionrules"
                name           = $name
                subscriptionId = $sub.id
                resourceGroup  = $rg
                location       = "global"
                tags           = [ordered]@{ _deployed_by_amba = "True" }
                properties     = [ordered]@{
                    enabled = $enabled
                    type    = $ruleType
                    scopes  = @("/subscriptions/$($sub.id)")
                }
            })
    }
}

# --- 5. Action groups (microsoft.insights/actiongroups) ----------------------

Write-Host "Generating AMBA action groups..." -ForegroundColor Cyan

$actionGroups = New-Object System.Collections.Generic.List[object]
foreach ($sub in $subscriptions) {
    $agCountPerSub = $rng.Next(1, 3)
    for ($a = 0; $a -lt $agCountPerSub; $a++) {
        $enabled = $rng.NextDouble() -lt 0.9
        $name = "ag-amba-$(Get-ShortId)"
        $emailCount = $rng.Next(1, 4)
        $webhookCount = $rng.Next(0, 2)
        $armRoleCount = $rng.Next(0, 2)
        $emailReceivers = @()
        for ($e = 1; $e -le $emailCount; $e++) { $emailReceivers += [ordered]@{ name = "oncall$e"; emailAddress = "oncall$e@contoso.com" } }
        $webhookReceivers = @()
        for ($w = 1; $w -le $webhookCount; $w++) { $webhookReceivers += [ordered]@{ name = "hook$w"; serviceUri = "https://hooks.contoso.com/$w" } }
        $armRoleReceivers = @()
        for ($m = 1; $m -le $armRoleCount; $m++) { $armRoleReceivers += [ordered]@{ name = "owner$m"; roleId = "owner" } }
        $actionGroups.Add([ordered]@{
                type           = "microsoft.insights/actiongroups"
                name           = $name
                subscriptionId = $sub.id
                resourceGroup  = "rg-amba-monitoring-001"
                location       = "global"
                tags           = [ordered]@{ _deployed_by_amba = "True" }
                properties     = [ordered]@{
                    enabled          = $enabled
                    groupShortName   = "amba$a"
                    emailReceivers   = $emailReceivers
                    webhookReceivers = $webhookReceivers
                    armRoleReceivers = $armRoleReceivers
                }
            })
    }
}

# --- 6. AMBA alert rule definitions (for the deployed-resource inventory) -----

Write-Host "Generating AMBA alert rule definitions..." -ForegroundColor Cyan

$alertRuleDefs = New-Object System.Collections.Generic.List[object]
$alertRuleResourceTypes = @("microsoft.insights/metricalerts", "microsoft.insights/scheduledqueryrules", "microsoft.insights/activitylogalerts")
foreach ($sub in $subscriptions) {
    $defCountPerSub = $rng.Next(3, 9)
    for ($d = 0; $d -lt $defCountPerSub; $d++) {
        $t = Get-RandomItem $alertRuleResourceTypes
        $name = "alert-amba-$(Get-ShortId)"
        $alertRuleDefs.Add([ordered]@{
                type           = $t
                name           = $name
                subscriptionId = $sub.id
                resourceGroup  = "rg-amba-monitoring-001"
                location       = "global"
                tags           = [ordered]@{ _deployed_by_amba = "True" }
                properties     = [ordered]@{ enabled = $true }
            })
    }
}

# --- 7. Policy remediation tasks (AMBA + Monitoring Baselines) ----------------

Write-Host "Generating remediation tasks..." -ForegroundColor Cyan

$remediations = New-Object System.Collections.Generic.List[object]
$provisioningStates = @("Succeeded", "Running", "Failed", "Canceled")

function Add-Remediation($assignmentName, $sub, $refId) {
    $state = if ($rng.NextDouble() -lt 0.7) { "Succeeded" } else { Get-RandomItem $provisioningStates }
    $createdDaysAgo = $rng.Next(0, 14)
    $total = $rng.Next(1, 250)
    $script:remediations.Add([ordered]@{
            type       = "microsoft.policyinsights/remediations"
            name       = "remediate-$refId-$(Get-ShortId)"
            properties = [ordered]@{
                policyAssignmentId = "/subscriptions/$($sub.id)/providers/microsoft.authorization/policyassignments/$assignmentName"
                provisioningState  = $state
                createdOn          = $now.AddDays(-$createdDaysAgo).ToString("o")
                deploymentStatus   = [ordered]@{ totalDeployments = $total }
            }
        })
}

# Monitoring Baselines remediations -> assignment name contains 'mon-baselines'
foreach ($env in $mbEnvironments) {
    $envSubs = Get-SubscriptionsForRing $env
    foreach ($grp in @("ama_linux_vm", "ama_windows_vm", "diag_storage", "diag_keyvault", "pe_storage")) {
        $sub = Get-RandomItem $envSubs
        Add-Remediation "mon-baselines-$env" $sub $grp
    }
}

# AMBA remediations -> assignment name contains 'amba'
foreach ($def in $ambaInitiatives) {
    $sub = Get-RandomItem $subscriptions
    Add-Remediation "amba-$($def.initiative.ToLower())" $sub $def.prefix
}

# --- 8. Consolidated AMBA-deployed resource inventory ------------------------

$deployedResources = New-Object System.Collections.Generic.List[object]
foreach ($r in $actionGroups) { $deployedResources.Add($r) }
foreach ($r in $actionRules) { $deployedResources.Add($r) }
foreach ($r in $alertRuleDefs) { $deployedResources.Add($r) }

# --- Write outputs ------------------------------------------------------------

function Write-Jsonl($items, $path) {
    $items | ForEach-Object { $_ | ConvertTo-Json -Depth 8 -Compress } | Set-Content -Path $path -Encoding utf8
}

$paths = [ordered]@{
    ambaPolicyStates                = Join-Path $OutputDir "amba-policy-states.jsonl"
    monitoringBaselinesPolicyStates = Join-Path $OutputDir "monitoring-baselines-policy-states.jsonl"
    ambaAlerts                      = Join-Path $OutputDir "amba-alerts.jsonl"
    alertProcessingRules            = Join-Path $OutputDir "amba-action-rules.jsonl"
    actionGroups                    = Join-Path $OutputDir "amba-action-groups.jsonl"
    alertRuleDefinitions            = Join-Path $OutputDir "amba-alert-rules.jsonl"
    remediationTasks                = Join-Path $OutputDir "remediation-tasks.jsonl"
    deployedResources               = Join-Path $OutputDir "amba-deployed-resources.jsonl"
}

Write-Jsonl $ambaStates $paths.ambaPolicyStates
Write-Jsonl $mbStates $paths.monitoringBaselinesPolicyStates
Write-Jsonl $alerts $paths.ambaAlerts
Write-Jsonl $actionRules $paths.alertProcessingRules
Write-Jsonl $actionGroups $paths.actionGroups
Write-Jsonl $alertRuleDefs $paths.alertRuleDefinitions
Write-Jsonl $remediations $paths.remediationTasks
Write-Jsonl $deployedResources $paths.deployedResources

# --- Build summary ------------------------------------------------------------

function Get-ComplianceSummary($states, $groupByField) {
    $states | Group-Object { $_.properties.$groupByField } | ForEach-Object {
        $total = $_.Count
        $nonCompliant = ($_.Group | Where-Object { $_.properties.complianceState -eq "NonCompliant" }).Count
        [pscustomobject]@{
            Name              = $_.Name
            Total             = $total
            NonCompliant      = $nonCompliant
            CompliancePercent = [math]::Round(100.0 * ($total - $nonCompliant) / $total, 1)
        }
    } | Sort-Object CompliancePercent
}

$ambaSummary = Get-ComplianceSummary $ambaStates "policySetDefinitionName"
$mbByInitiative = Get-ComplianceSummary $mbStates "policySetDefinitionName"
$mbByCategory = Get-ComplianceSummary $mbStates "policyDefinitionGroupNames"

$staleAlerts = ($alerts | Where-Object {
        $_.properties.essentials.monitorCondition -eq "Fired" -and
        $_.properties.essentials.alertState -eq "New" -and
        ([datetime]$_.properties.essentials.startDateTime) -lt $now.AddHours(-24)
    }).Count

$subscriptionBreakdown = $subscriptions | ForEach-Object {
    $sid = $_.id
    [pscustomobject]@{
        Subscription = $_.name
        Ring         = $_.ring
        AmbaStates   = ($ambaStates | Where-Object { $_.properties.subscriptionId -eq $sid }).Count
        MbStates     = ($mbStates | Where-Object { $_.properties.subscriptionId -eq $sid }).Count
        Alerts       = ($alerts | Where-Object { $_.properties.essentials.targetResource -like "/subscriptions/$sid/*" }).Count
    }
}

$summary = [ordered]@{
    generatedAt                               = $now.ToString("o")
    seed                                      = 42
    environmentsGenerated                     = $mbEnvironments
    totals                                    = [ordered]@{
        ambaPolicyStates                = $ambaStates.Count
        monitoringBaselinesPolicyStates = $mbStates.Count
        ambaAlerts                      = $alerts.Count
        staleAlertsOver24h              = $staleAlerts
        alertProcessingRules            = $actionRules.Count
        actionGroups                    = $actionGroups.Count
        alertRuleDefinitions            = $alertRuleDefs.Count
        remediationTasks                = $remediations.Count
        deployedResources               = $deployedResources.Count
        subscriptions                   = $subscriptions.Count
    }
    ambaComplianceByInitiative                = $ambaSummary
    monitoringBaselinesComplianceByInitiative = $mbByInitiative
    monitoringBaselinesComplianceByCategory   = $mbByCategory
    subscriptionBreakdown                     = $subscriptionBreakdown
    files                                     = $paths
}

$summaryPath = Join-Path $OutputDir "summary.json"
$summary | ConvertTo-Json -Depth 8 | Set-Content -Path $summaryPath -Encoding utf8

# --- Console report -----------------------------------------------------------

Write-Host "`n=== Test Data Generated ===" -ForegroundColor Green
Write-Host "Output directory            : $OutputDir"
Write-Host "Subscriptions               : $($subscriptions.Count)"
Write-Host "AMBA policy states          : $($ambaStates.Count)"
Write-Host "Monitoring Baselines states : $($mbStates.Count) across $($mbEnvironments.Count) ring(s)"
Write-Host "AMBA alerts                 : $($alerts.Count) (stale >24h: $staleAlerts)"
Write-Host "Alert processing rules      : $($actionRules.Count)"
Write-Host "Action groups               : $($actionGroups.Count)"
Write-Host "Alert rule definitions      : $($alertRuleDefs.Count)"
Write-Host "Remediation tasks           : $($remediations.Count)"
Write-Host "Deployed-resource inventory : $($deployedResources.Count)"

Write-Host "`n--- AMBA compliance by initiative ---" -ForegroundColor Cyan
$ambaSummary | Format-Table -AutoSize

Write-Host "--- Monitoring Baselines compliance by initiative (per ring) ---" -ForegroundColor Cyan
$mbByInitiative | Format-Table -AutoSize

Write-Host "--- Monitoring Baselines compliance by category ---" -ForegroundColor Cyan
$mbByCategory | Format-Table -AutoSize

Write-Host "--- Per-subscription record counts ---" -ForegroundColor Cyan
$subscriptionBreakdown | Format-Table -AutoSize

Write-Host "Summary written to: $summaryPath" -ForegroundColor Green
