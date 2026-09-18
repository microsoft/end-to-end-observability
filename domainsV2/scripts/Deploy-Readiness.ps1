[CmdletBinding()]
param(
    [Parameter(Mandatory)][guid]$SubscriptionId,
    [Parameter(Mandatory)][guid]$TenantId,
    [Parameter(Mandatory)][ValidatePattern('^[a-zA-Z0-9][-a-zA-Z0-9_.]{0,89}$')][string]$ResourceGroupName,
    [Parameter(Mandatory)][ValidatePattern('^[a-zA-Z0-9][-a-zA-Z0-9]{2,62}$')][string]$WorkspaceName,
    [Parameter(Mandatory)][ValidatePattern('^[a-zA-Z0-9][-a-zA-Z0-9]{1,58}[a-zA-Z0-9]$')][string]$AppName,
    [Parameter(Mandatory)][ValidatePattern('^[a-zA-Z0-9][-a-zA-Z0-9_.]{0,63}$')][string]$DeploymentName,
    [ValidateSet('WhatIf', 'Validate', 'Provision')][string]$Action = 'WhatIf',
    [switch]$ApproveResourceCostsAndRoleAssignments
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Deployment-Guards.ps1')
if ($Action -eq 'Provision' -and -not $ApproveResourceCostsAndRoleAssignments) {
    throw 'Provisioning requires explicit approval of the scoped readiness role definition and assignment.'
}
Assert-DeploymentContext -SubscriptionId $SubscriptionId -TenantId $TenantId
$operation = @{ WhatIf = 'what-if'; Validate = 'validate'; Provision = 'create' }[$Action]
Invoke-DeploymentAz -Arguments @(
    'deployment', 'group', $operation, '--subscription', "$SubscriptionId",
    '--resource-group', $ResourceGroupName, '--name', $DeploymentName, '--mode', 'Incremental',
    '--template-file', (Join-Path $PSScriptRoot '..\infra\readiness.bicep'),
    '--parameters', "workspaceName=$WorkspaceName", "appName=$AppName", '--only-show-errors'
)
