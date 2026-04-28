<#
.SYNOPSIS
    Removes the discovery test fixture resource group.

.PARAMETER SubscriptionId
    Target subscription.

.PARAMETER ResourceGroupName
    Resource group to delete (default: rg-obs-discovery-test).

.EXAMPLE
    .\Remove-TestFixture.ps1 -SubscriptionId <id>
#>

[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)] [string] $SubscriptionId,
    [Parameter()]          [string] $ResourceGroupName = 'rg-obs-discovery-test'
)

$ErrorActionPreference = 'Stop'

az account set --subscription $SubscriptionId | Out-Null

if ($PSCmdlet.ShouldProcess($ResourceGroupName, 'Delete resource group')) {
    Write-Host "==> Deleting $ResourceGroupName (this will run async)..." -ForegroundColor Yellow
    az group delete --name $ResourceGroupName --yes --no-wait
    Write-Host "Delete submitted. Track with: az group show -n $ResourceGroupName" -ForegroundColor Green
}
