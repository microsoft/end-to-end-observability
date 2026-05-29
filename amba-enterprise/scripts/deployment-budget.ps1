<#
.SYNOPSIS
    Checks management group deployment count against the 800-limit budget.
.DESCRIPTION
    Azure has a limit of 800 deployments per management group. This script
    checks current usage and warns when approaching the limit.
    Stale AMBA deployments can be identified by prefix patterns:
    amba-, pid-, alzArm, ambaPreparingToLaunch
.EXAMPLE
    ./deployment-budget.ps1
#>

[CmdletBinding()]
param(
    [Parameter()]
    [string]$ManagementGroupId,

    [Parameter()]
    [int]$WarningThreshold = 700,

    [Parameter()]
    [int]$CriticalThreshold = 750
)

$ErrorActionPreference = "Stop"

Write-Host "=== Deployment Budget Check ===" -ForegroundColor Cyan
Write-Host "Limit: 800 deployments per management group"
Write-Host "Warning threshold: $WarningThreshold | Critical threshold: $CriticalThreshold`n"

# Check if Azure CLI is available
try {
    $account = az account show 2>$null | ConvertFrom-Json
    if (-not $account) {
        Write-Host "WARNING: Not authenticated to Azure CLI. Skipping live budget check." -ForegroundColor Yellow
        exit 0
    }
} catch {
    Write-Host "WARNING: Azure CLI not available. Skipping live budget check." -ForegroundColor Yellow
    exit 0
}

if (-not $ManagementGroupId) {
    Write-Host "No management group specified. Use -ManagementGroupId to check a specific group." -ForegroundColor Yellow
    Write-Host "SKIPPED: Provide ManagementGroupId for live budget validation." -ForegroundColor Yellow
    exit 0
}

# Get deployment count
$deployments = az deployment mg list --management-group-id $ManagementGroupId --query "length(@)" 2>$null | ConvertFrom-Json
$remaining = 800 - $deployments

# Identify AMBA-specific deployments
$ambaDeployments = az deployment mg list --management-group-id $ManagementGroupId --query "[?starts_with(name, 'amba-') || starts_with(name, 'pid-') || starts_with(name, 'alzArm') || starts_with(name, 'ambaPreparingToLaunch')].name" 2>$null | ConvertFrom-Json

Write-Host "Management Group: $ManagementGroupId"
Write-Host "Total deployments: $deployments / 800"
Write-Host "Remaining budget: $remaining"
Write-Host "AMBA deployments: $($ambaDeployments.Count)"
Write-Host ""

if ($deployments -ge $CriticalThreshold) {
    Write-Host "CRITICAL: Deployment count ($deployments) exceeds critical threshold ($CriticalThreshold)" -ForegroundColor Red
    Write-Host "ACTION: Immediately clean stale deployments before next AMBA deployment" -ForegroundColor Red
    Write-Host "`nStale AMBA deployments that can be cleaned:" -ForegroundColor Yellow
    $ambaDeployments | ForEach-Object { Write-Host "  - $_" }
    exit 1
} elseif ($deployments -ge $WarningThreshold) {
    Write-Host "WARNING: Deployment count ($deployments) exceeds warning threshold ($WarningThreshold)" -ForegroundColor Yellow
    Write-Host "Plan cleanup of stale deployments before next major rollout." -ForegroundColor Yellow
    exit 0
} else {
    Write-Host "PASSED: Deployment budget healthy ($remaining slots available)" -ForegroundColor Green
    exit 0
}
