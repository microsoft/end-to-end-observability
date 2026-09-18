<#
.SYNOPSIS
Deploy only the producer resources with an explicitly selected subscription and tenant.
.DESCRIPTION
This does not grant the producer observation-query access. After the producer exists,
separately validate and provision Deploy-Readiness.ps1 with explicit ResourceGroupName,
WorkspaceName, AppName and DeploymentName before observation-readiness acceptance.
UseExistingPlan requires the separate exact B2 plan stage to have completed.
DeferApplication requires UseExistingPlan and excludes the app and its identity
assignments. The later application stage must independently pass full validation.
SecurityPerimeter creates an enforced subscription-only boundary and takes
precedence over PublicStorage; it never opens the underlying storage firewall.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][guid]$SubscriptionId,
    [Parameter(Mandatory)][guid]$TenantId,
    [Parameter(Mandatory)][ValidatePattern('^[a-z][a-z0-9-]{1,24}[a-z0-9]$')][string]$Prefix,
    [Parameter(Mandatory)][ValidatePattern('^[a-z0-9]{3,24}$')][string]$StorageName,
    [Parameter(Mandatory)][ValidatePattern('^[a-zA-Z0-9][-a-zA-Z0-9_.]{0,63}$')][string]$DeploymentName,
    [Parameter(Mandatory)][ValidateSet('centralus')][string]$Location,
    [ValidateSet('WhatIf', 'Validate', 'Provision')][string]$Action = 'WhatIf',
    [bool]$PublicStorage = $false,
    [bool]$UseExistingPlan = $false,
    [bool]$DeferApplication = $false,
    [switch]$ResolveExistingDataEndpoints,
    [bool]$SecurityPerimeter = $false,
    [bool]$UnrestrictedPortal = $false,
    [string]$ApprovedIpCidr = '',
    [switch]$ApproveResourceCostsAndRoleAssignments
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Deployment-Guards.ps1')
if ($DeferApplication -and -not $UseExistingPlan) {
    throw 'DeferApplication requires UseExistingPlan and the independently deployed exact B2 plan.'
}
if ($ResolveExistingDataEndpoints -and (-not $UseExistingPlan -or $DeferApplication)) {
    throw 'ResolveExistingDataEndpoints requires UseExistingPlan and a non-deferred application stage.'
}
if ($Action -eq 'Provision' -and -not $ApproveResourceCostsAndRoleAssignments) {
    throw 'Provisioning requires explicit approval of resource costs and scoped role assignments.'
}
if (-not $UnrestrictedPortal -and [string]::IsNullOrWhiteSpace($ApprovedIpCidr)) {
    throw 'ApprovedIpCidr is required unless UnrestrictedPortal is explicitly true.'
}
if ($ApprovedIpCidr) {
    $parts = $ApprovedIpCidr.Split('/')
    $address = $null
    $mask = 0
    if ($parts.Count -ne 2 -or -not [System.Net.IPAddress]::TryParse($parts[0], [ref]$address) -or
        $address.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork -or
        -not [int]::TryParse($parts[1], [ref]$mask) -or $mask -lt 0 -or $mask -gt 32) {
        throw 'ApprovedIpCidr must be a valid IPv4 CIDR.'
    }
}
Assert-DeploymentContext -SubscriptionId $SubscriptionId -TenantId $TenantId
$runtimeParameters = @()
$runtimeParameterFile = $null
if ($ResolveExistingDataEndpoints) {
    $baseId = "/subscriptions/$SubscriptionId/resourceGroups/$Prefix-rg/providers"
    $resourceSpecs = @(
        @{ Id = "$baseId/Microsoft.Storage/storageAccounts/$StorageName"; Api = '2023-05-01' },
        @{ Id = "$baseId/Microsoft.Insights/dataCollectionRules/$Prefix-dcr"; Api = '2024-03-11' },
        @{ Id = "$baseId/Microsoft.OperationalInsights/workspaces/$Prefix-law"; Api = '2023-09-01' }
    )
    $readback = @(
        foreach ($spec in $resourceSpecs) {
            $resource = Invoke-DeploymentAz -Arguments @(
                'resource', 'show', '--subscription', "$SubscriptionId", '--ids', $spec.Id,
                '--api-version', $spec.Api, '--output', 'json', '--only-show-errors'
            ) | ConvertFrom-Json
            if ($resource.id -ne $spec.Id) {
                throw 'Existing data resource readback does not match the exact selected resource ID.'
            }
            $resource
        }
    )
    $endpoints = @{
        storageAccountUrl = $readback[0].properties.primaryEndpoints.blob
        logsEndpoint = $readback[1].properties.endpoints.logsIngestion
        dcrImmutableId = $readback[1].properties.immutableId
        workspaceId = $readback[2].properties.customerId
    }
    foreach ($name in $endpoints.Keys) {
        if ($endpoints[$name] -isnot [string] -or [string]::IsNullOrWhiteSpace($endpoints[$name])) {
            throw "Existing data readback is missing required endpoint field: $name"
        }
    }
    foreach ($name in @('storageAccountUrl', 'logsEndpoint')) {
        $uri = $null
        if (-not [uri]::TryCreate($endpoints[$name], [UriKind]::Absolute, [ref]$uri) -or
            $uri.Scheme -ne 'https' -or $uri.UserInfo -or $uri.Query -or $uri.Fragment) {
            throw "Existing data readback is not a credential-free HTTPS endpoint: $name"
        }
    }
    $workspaceGuid = [guid]::Empty
    if ($endpoints.dcrImmutableId -notmatch '^dcr-[a-fA-F0-9]{32}$' -or
        -not [guid]::TryParse($endpoints.workspaceId, [ref]$workspaceGuid) -or $workspaceGuid -eq [guid]::Empty) {
        throw 'Existing data readback has an invalid DCR immutable ID or workspace customer ID.'
    }
    $runtimeParameterFile = New-TemporaryFile
    $runtimeParameters = @('@' + $runtimeParameterFile.FullName)
}
$operation = @{ WhatIf = 'what-if'; Validate = 'validate'; Provision = 'create' }[$Action]
try {
    if ($runtimeParameterFile) {
        @{ resolvedRuntimeEndpoints = @{ value = $endpoints } } |
            ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $runtimeParameterFile.FullName -Encoding utf8
    }
Invoke-DeploymentAz -Arguments (@(
    'deployment', 'sub', $operation, '--subscription', "$SubscriptionId",
    '--location', $Location, '--name', $DeploymentName,
    '--template-file', (Join-Path $PSScriptRoot '..\infra\deploy.bicep'),
    '--parameters', "location=$Location", "prefix=$Prefix", "storageName=$StorageName",
    "publicStorage=$($PublicStorage.ToString().ToLowerInvariant())",
    "useExistingPlan=$($UseExistingPlan.ToString().ToLowerInvariant())",
    "deferApplication=$($DeferApplication.ToString().ToLowerInvariant())",
    "securityPerimeter=$($SecurityPerimeter.ToString().ToLowerInvariant())",
    "unrestrictedPortal=$($UnrestrictedPortal.ToString().ToLowerInvariant())",
    "approvedIpCidr=$ApprovedIpCidr"
) + $runtimeParameters + @('--only-show-errors'))
}
finally {
    if ($runtimeParameterFile) {
        Remove-Item -LiteralPath $runtimeParameterFile.FullName -ErrorAction Stop
    }
}
