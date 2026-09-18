[CmdletBinding()]
param(
    [Parameter(Mandatory)][guid]$SubscriptionId,
    [Parameter(Mandatory)][guid]$TenantId,
    [Parameter(Mandatory)][ValidatePattern('^[a-zA-Z0-9][-a-zA-Z0-9_.]{0,89}$')][string]$ResourceGroupName,
    [Parameter(Mandatory)][ValidatePattern('^[a-zA-Z0-9][-a-zA-Z0-9]{1,58}[a-zA-Z0-9]$')][string]$AppName,
    [Parameter(Mandatory)][string]$PackagePath,
    [ValidatePattern('^[a-fA-F0-9]{64}$')][string]$ExpectedSHA256,
    [switch]$ApproveDeployment
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Deployment-Guards.ps1')
if (-not $ApproveDeployment) {
    throw 'Publishing requires explicit approval of this package and App Service target.'
}
$package = Get-DeploymentPackage -PackagePath $PackagePath -ExpectedSHA256 $ExpectedSHA256
Assert-DeploymentContext -SubscriptionId $SubscriptionId -TenantId $TenantId
Invoke-DeploymentAz -Arguments @(
    'webapp', 'deploy', '--subscription', "$SubscriptionId", '--resource-group', $ResourceGroupName,
    '--name', $AppName, '--src-path', $package, '--type', 'zip', '--only-show-errors'
)
