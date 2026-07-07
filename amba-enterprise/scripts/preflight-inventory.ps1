<#
.SYNOPSIS
    Brownfield subscription preflight inventory for AMBA deployment.
.DESCRIPTION
    Scans existing subscriptions before AMBA onboarding to identify:
    - Existing alert rules and their action groups
    - Existing action groups
    - Existing alert processing rules
    - Resource tags relevant to AMBA
    - VM Insights deployment status
    - Subscription naming compliance
    Outputs a report to guide the onboarding decision (standard vs BYON).
.PARAMETER SubscriptionId
    The subscription ID to inventory.
.PARAMETER OutputPath
    Path to write the inventory report (JSON).
.EXAMPLE
    ./preflight-inventory.ps1 -SubscriptionId "00000000-0000-0000-0000-000000000000"
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$SubscriptionId,

    [Parameter()]
    [string]$OutputPath = "./preflight-report-$SubscriptionId.json"
)

$ErrorActionPreference = "Stop"

Write-Host "=== AMBA Brownfield Preflight Inventory ===" -ForegroundColor Cyan
Write-Host "Subscription: $SubscriptionId`n"

# Set subscription context
az account set --subscription $SubscriptionId

$report = @{
    subscriptionId    = $SubscriptionId
    timestamp         = (Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ")
    subscriptionName  = ""
    namingCompliant   = $false
    existingAlerts    = @()
    existingActionGroups = @()
    existingAlertProcessingRules = @()
    ambaTaggedResources = @()
    vmInsightsStatus  = @()
    recommendations   = @()
}

# 1. Subscription naming check
Write-Host "1. Checking subscription naming compliance..." -ForegroundColor White
$subInfo = az account show --subscription $SubscriptionId | ConvertFrom-Json
$report.subscriptionName = $subInfo.name
$validPattern = '^[a-zA-Z0-9_-]+$'
$report.namingCompliant = $subInfo.name -match $validPattern

if (-not $report.namingCompliant) {
    $report.recommendations += "BLOCKER: Subscription name '$($subInfo.name)' contains invalid characters. Rename before AMBA deployment."
    Write-Host "  FAIL: Invalid subscription name" -ForegroundColor Red
} else {
    Write-Host "  PASS: Subscription name is AMBA-compatible" -ForegroundColor Green
}

# 2. Existing alert rules
Write-Host "2. Scanning existing alert rules..." -ForegroundColor White
$alerts = az monitor metrics alert list --subscription $SubscriptionId 2>$null | ConvertFrom-Json
$logAlerts = az monitor scheduled-query list --subscription $SubscriptionId 2>$null | ConvertFrom-Json
$activityAlerts = az monitor activity-log alert list --subscription $SubscriptionId 2>$null | ConvertFrom-Json

$report.existingAlerts = @{
    metricAlerts   = $alerts.Count
    logAlerts      = $logAlerts.Count
    activityAlerts = $activityAlerts.Count
    total          = $alerts.Count + $logAlerts.Count + $activityAlerts.Count
}
Write-Host "  Found: $($report.existingAlerts.total) existing alerts (metric: $($alerts.Count), log: $($logAlerts.Count), activity: $($activityAlerts.Count))"

# 3. Existing action groups
Write-Host "3. Scanning existing action groups..." -ForegroundColor White
$actionGroups = az monitor action-group list --subscription $SubscriptionId 2>$null | ConvertFrom-Json
$report.existingActionGroups = $actionGroups | ForEach-Object {
    @{
        name       = $_.name
        resourceId = $_.id
        receivers  = @{
            email   = $_.emailReceivers.Count
            sms     = $_.smsReceivers.Count
            webhook = $_.webhookReceivers.Count
        }
    }
}
Write-Host "  Found: $($actionGroups.Count) action groups"

if ($actionGroups.Count -gt 0) {
    $report.recommendations += "INFO: $($actionGroups.Count) existing action groups found. Consider BYON if these should route AMBA alerts."
}

# 4. Existing alert processing rules
Write-Host "4. Scanning existing alert processing rules..." -ForegroundColor White
$aprs = az monitor alert-processing-rule list --subscription $SubscriptionId 2>$null | ConvertFrom-Json
$report.existingAlertProcessingRules = $aprs | ForEach-Object {
    @{
        name       = $_.name
        resourceId = $_.id
        actionType = $_.properties.actions.actionType
    }
}
Write-Host "  Found: $($aprs.Count) alert processing rules"

if ($aprs.Count -gt 0) {
    $report.recommendations += "WARNING: $($aprs.Count) existing APRs found. Evaluate for conflicts with AMBA APR. Consider BYON APR path."
}

# 5. AMBA-tagged resources
Write-Host "5. Scanning for existing AMBA-tagged resources..." -ForegroundColor White
$ambaResources = az resource list --subscription $SubscriptionId --query "[?tags._deployed_by_amba != null]" 2>$null | ConvertFrom-Json
$report.ambaTaggedResources = $ambaResources.Count
Write-Host "  Found: $($ambaResources.Count) resources with _deployed_by_amba tag"

if ($ambaResources.Count -gt 0) {
    $report.recommendations += "INFO: Previous AMBA deployment detected ($($ambaResources.Count) tagged resources). Run cleanup before fresh deployment."
}

# 6. VM Insights status
Write-Host "6. Checking VM Insights status..." -ForegroundColor White
$vms = az vm list --subscription $SubscriptionId --query "[].{name:name, rg:resourceGroup, id:id}" 2>$null | ConvertFrom-Json
$vmInsightsReady = 0
$vmInsightsMissing = @()

foreach ($vm in $vms) {
    $extensions = az vm extension list --resource-group $vm.rg --vm-name $vm.name --query "[?contains(publisher, 'Microsoft.Azure.Monitor')]" 2>$null | ConvertFrom-Json
    if ($extensions.Count -gt 0) {
        $vmInsightsReady++
    } else {
        $vmInsightsMissing += $vm.name
    }
}

$report.vmInsightsStatus = @{
    totalVMs        = $vms.Count
    insightsEnabled = $vmInsightsReady
    insightsMissing = $vmInsightsMissing.Count
    missingVMs      = $vmInsightsMissing
}
Write-Host "  VMs: $($vms.Count) total, $vmInsightsReady with VM Insights, $($vmInsightsMissing.Count) missing"

if ($vmInsightsMissing.Count -gt 0) {
    $report.recommendations += "PREREQUISITE: $($vmInsightsMissing.Count) VMs missing VM Insights. Deploy DCR before VM log alert remediation."
}

# 7. Generate onboarding recommendation
Write-Host "`n7. Generating onboarding recommendation..." -ForegroundColor White

if ($actionGroups.Count -gt 0 -and $aprs.Count -gt 0) {
    $report.recommendations += "RECOMMENDATION: Use BYON notification path (existing AG + existing APR)."
} elseif ($actionGroups.Count -gt 0) {
    $report.recommendations += "RECOMMENDATION: Use BYON action group with AMBA-created APR."
} else {
    $report.recommendations += "RECOMMENDATION: Use standard AMBA Notification Assets path."
}

# Write report
$report | ConvertTo-Json -Depth 5 | Set-Content $OutputPath -Encoding UTF8
Write-Host "`n=== Report saved to: $OutputPath ===" -ForegroundColor Cyan
Write-Host "`nRecommendations:" -ForegroundColor Yellow
$report.recommendations | ForEach-Object { Write-Host "  - $_" }
