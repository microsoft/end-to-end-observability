<#
.SYNOPSIS
    Validates Azure subscription names for AMBA deployment compatibility.
.DESCRIPTION
    AMBA uses subscription names in action group and alert processing rule names.
    Invalid characters in subscription names cause deployment failures.
    Valid characters: alphanumeric (a-z, A-Z, 0-9), underscores (_), hyphens (-).
.EXAMPLE
    ./validate-naming.ps1
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$exitCode = 0

Write-Host "=== Subscription Naming Compliance Check ===" -ForegroundColor Cyan
Write-Host "Valid characters: a-z, A-Z, 0-9, underscore (_), hyphen (-)`n"

# Check if Azure CLI is available and authenticated
try {
    $account = az account show 2>$null | ConvertFrom-Json
    if (-not $account) {
        Write-Host "WARNING: Not authenticated to Azure CLI. Performing pattern-only validation." -ForegroundColor Yellow
        Write-Host "Run 'az login' for live subscription name validation." -ForegroundColor Yellow
        exit 0
    }
} catch {
    Write-Host "WARNING: Azure CLI not available. Skipping live validation." -ForegroundColor Yellow
    exit 0
}

# Get all accessible subscriptions
$subscriptions = az account list --all --query "[].{name:name, id:id, state:state}" | ConvertFrom-Json

$invalidSubs = @()
$validPattern = '^[a-zA-Z0-9_-]+$'

foreach ($sub in $subscriptions) {
    if ($sub.state -ne "Enabled") { continue }

    if ($sub.name -notmatch $validPattern) {
        $invalidChars = ($sub.name.ToCharArray() | Where-Object { $_ -notmatch '[a-zA-Z0-9_-]' }) -join ', '
        $invalidSubs += [PSCustomObject]@{
            Name            = $sub.name
            Id              = $sub.id
            InvalidChars    = $invalidChars
        }
    }
}

if ($invalidSubs.Count -gt 0) {
    Write-Host "FAILED: $($invalidSubs.Count) subscription(s) have invalid names for AMBA:" -ForegroundColor Red
    Write-Host ""
    $invalidSubs | Format-Table -AutoSize
    Write-Host "`nAction Required:" -ForegroundColor Yellow
    Write-Host "  Rename these subscriptions to use only alphanumeric characters, underscores, and hyphens."
    Write-Host "  Or add them to the policy_assignment_exclusion_list to skip AMBA deployment."
    Write-Host ""
    Write-Host "Reference: https://azure.github.io/azure-monitor-baseline-alerts/patterns/alz/Resources/Known-Issues/"
    $exitCode = 1
} else {
    Write-Host "PASSED: All $($subscriptions.Count) enabled subscriptions have AMBA-compatible names" -ForegroundColor Green
}

exit $exitCode
