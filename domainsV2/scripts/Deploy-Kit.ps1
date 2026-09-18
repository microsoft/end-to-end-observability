#Requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet(
        'BootstrapResourceGroup',
        'Plan',
        'ProducerData',
        'ProducerApp',
        'ProducerPackage',
        'ProducerPublish',
        'Readiness'
    )]
    [string]$Stage,
    [ValidateSet('WhatIf', 'Validate', 'Provision', 'Package', 'Publish')]
    [string]$Action = 'WhatIf',
    [string]$ConfigPath = (Join-Path (Join-Path $PSScriptRoot '..') 'environment.local.json'),
    [switch]$ApproveResourceCostsAndRoleAssignments,
    [switch]$ApproveDeployment,
    [switch]$ApprovePublicAnonymousAccess
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Deployment-Guards.ps1')

$kitRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$configFile = if ([IO.Path]::IsPathRooted($ConfigPath)) {
    [IO.Path]::GetFullPath($ConfigPath)
} else {
    [IO.Path]::GetFullPath((Join-Path $kitRoot $ConfigPath))
}

function Get-ConfigValue {
    param(
        [Parameter(Mandatory)]$Config,
        [Parameter(Mandatory)][string[]]$Path,
        $Default = $null,
        [switch]$Required
    )

    $current = $Config
    foreach ($segment in $Path) {
        if ($current -is [System.Collections.IDictionary] -and $current.ContainsKey($segment)) {
            $current = $current[$segment]
            continue
        }
        if ($Required) {
            throw "Missing configuration value: $($Path -join '.')"
        }
        return $Default
    }
    if ($Required -and ($null -eq $current -or ($current -is [string] -and [string]::IsNullOrWhiteSpace($current)))) {
        throw "Missing configuration value: $($Path -join '.')"
    }
    return $current
}

function Resolve-KitPath {
    param(
        [Parameter(Mandatory)][string]$PathValue,
        [switch]$MustExist
    )

    $candidate = if ([IO.Path]::IsPathRooted($PathValue)) {
        [IO.Path]::GetFullPath($PathValue)
    } else {
        [IO.Path]::GetFullPath((Join-Path $kitRoot $PathValue))
    }
    if ($MustExist -and -not (Test-Path -LiteralPath $candidate)) {
        throw "Configured path does not exist: $candidate"
    }
    $candidate
}

function Get-ConfigBoolean {
    param([Parameter(Mandatory)][string[]]$Path)
    $value = Get-ConfigValue -Config $config -Path $Path -Required
    if ($value -isnot [bool]) {
        throw "$($Path -join '.') must be a JSON boolean, not a string."
    }
    $value
}

function Assert-AllowedAction {
    $allowed = switch ($Stage) {
        'BootstrapResourceGroup' { @('WhatIf', 'Validate', 'Provision') }
        'Plan' { @('WhatIf', 'Validate', 'Provision') }
        'ProducerData' { @('WhatIf', 'Validate', 'Provision') }
        'ProducerApp' { @('WhatIf', 'Validate', 'Provision') }
        'Readiness' { @('WhatIf', 'Validate', 'Provision') }
        'ProducerPackage' { @('Package') }
        'ProducerPublish' { @('Publish') }
    }
    if ($Action -notin $allowed) {
        throw "Stage $Stage only supports actions: $($allowed -join ', ')"
    }
}

function Assert-GuidValue {
    param(
        [Parameter(Mandatory)][string]$Value,
        [Parameter(Mandatory)][string]$Name
    )
    $guid = [guid]::Empty
    if (-not [guid]::TryParse($Value, [ref]$guid) -or $guid -eq [guid]::Empty) {
        throw "$Name must be a nonempty GUID."
    }
    $guid
}

function Assert-ProvidersRegistered {
    param(
        [Parameter(Mandatory)][guid]$SubscriptionId,
        [Parameter(Mandatory)][string[]]$Namespaces
    )

    $missing = [System.Collections.Generic.List[string]]::new()
    foreach ($namespace in ($Namespaces | Select-Object -Unique)) {
        $state = (Invoke-DeploymentAz -Arguments @(
            'provider', 'show', '--subscription', "$SubscriptionId", '--namespace', $namespace,
            '--query', 'registrationState', '--output', 'tsv', '--only-show-errors'
        ) | Out-String).Trim()
        if ($state -ne 'Registered') {
            $missing.Add($namespace)
        }
    }
    if ($missing.Count -ne 0) {
        throw ('Provider registration must be handled explicitly before this stage. ' +
               'Ask an approved admin to review governance, then inspect/register only the missing namespaces: ' +
               ($missing -join ', '))
    }
}

function Get-RequiredProviders {
    switch ($Stage) {
        'BootstrapResourceGroup' { @('Microsoft.Resources') }
        'Plan' { @('Microsoft.Web') }
        'ProducerData' { @('Microsoft.Web', 'Microsoft.Storage', 'Microsoft.OperationalInsights', 'Microsoft.Insights', 'Microsoft.Network', 'Microsoft.Authorization') }
        'ProducerApp' { @('Microsoft.Web', 'Microsoft.Storage', 'Microsoft.OperationalInsights', 'Microsoft.Insights', 'Microsoft.Network', 'Microsoft.Authorization') }
        'Readiness' { @('Microsoft.Web', 'Microsoft.OperationalInsights', 'Microsoft.Authorization') }
        'ProducerPublish' { @('Microsoft.Web') }
        default { @() }
    }
}

function Get-ProducerNames {
    param([Parameter(Mandatory)][string]$Prefix)
    @{
        ResourceGroupName = "$Prefix-rg"
        AppName = "$Prefix-app"
        WorkspaceName = "$Prefix-law"
    }
}

function Invoke-KitScript {
    param(
        [Parameter(Mandatory)][string]$RelativeScript,
        [Parameter(Mandatory)][hashtable]$Parameters
    )
    $scriptPath = Join-Path $kitRoot $RelativeScript
    & $scriptPath @Parameters
}

if (-not (Test-Path -LiteralPath $configFile -PathType Leaf)) {
    throw "Configuration file not found: $configFile. Copy environment.example.json to environment.local.json and edit it first."
}

$config = Get-Content -LiteralPath $configFile -Raw | ConvertFrom-Json -AsHashtable
Assert-AllowedAction

if ($Action -ne 'Package') {
    $subscriptionId = Assert-GuidValue -Value (Get-ConfigValue -Config $config -Path @('azure', 'subscriptionId') -Required) -Name 'azure.subscriptionId'
    $tenantId = Assert-GuidValue -Value (Get-ConfigValue -Config $config -Path @('azure', 'tenantId') -Required) -Name 'azure.tenantId'
}
$location = [string](Get-ConfigValue -Config $config -Path @('azure', 'location') -Required)
$producerPrefix = [string](Get-ConfigValue -Config $config -Path @('producer', 'prefix') -Required)
$storageName = [string](Get-ConfigValue -Config $config -Path @('producer', 'storageName') -Required)
$approvedIpCidr = [string](Get-ConfigValue -Config $config -Path @('producer', 'approvedIpCidr') -Default '')
$securityPerimeter = Get-ConfigBoolean -Path @('producer', 'securityPerimeter')
$unrestrictedPortal = Get-ConfigBoolean -Path @('producer', 'unrestrictedPortal')
$producerNames = Get-ProducerNames -Prefix $producerPrefix

if (!$securityPerimeter) {
    throw 'producer.securityPerimeter must be true: this kit requires public HTTPS with managed identity, without private endpoints.'
}
if ($location -ne 'centralus') {
    throw 'Only centralus is supported by this deployment workflow.'
}
if ($producerPrefix -cnotmatch '^[a-z][a-z0-9-]{1,24}[a-z0-9]$' -or $storageName -cnotmatch '^[a-z0-9]{3,24}$') {
    throw 'Use a 3-26 character lowercase producer prefix and a 3-24 character lowercase alphanumeric storage name.'
}
if ($unrestrictedPortal -and $Action -eq 'Provision' -and ($Stage -in @('ProducerApp')) -and -not $ApprovePublicAnonymousAccess) {
    throw 'ProducerApp provisioning with unrestrictedPortal=true requires -ApprovePublicAnonymousAccess.'
}
if ($Stage -in @('BootstrapResourceGroup', 'Plan', 'ProducerData', 'ProducerApp', 'Readiness') -and
    $Action -eq 'Provision' -and -not $ApproveResourceCostsAndRoleAssignments) {
    throw 'Provision requires -ApproveResourceCostsAndRoleAssignments.'
}
if ($Stage -eq 'ProducerPublish' -and -not $ApproveDeployment) {
    throw 'Publish requires -ApproveDeployment.'
}

$providers = Get-RequiredProviders
if (@($providers).Count -ne 0) {
    Assert-ProvidersRegistered -SubscriptionId $subscriptionId -Namespaces $providers
}

if ($Action -eq 'Provision') {
    # Revalidate this exact stage/config before any resource mutation.
    & $PSCommandPath -Stage $Stage -Action Validate -ConfigPath $configFile
}

$deploymentOperation = @{ WhatIf = 'what-if'; Validate = 'validate'; Provision = 'create' }

switch ($Stage) {
    'BootstrapResourceGroup' {
        Assert-DeploymentContext -SubscriptionId $subscriptionId -TenantId $tenantId
        $deploymentName = [string](Get-ConfigValue -Config $config -Path @('producer', 'bootstrapDeploymentName') -Required)
        Invoke-DeploymentAz -Arguments @(
            'deployment', 'sub', $deploymentOperation[$Action], '--subscription', "$subscriptionId",
            '--location', $location, '--name', $deploymentName,
            '--template-file', (Join-Path $kitRoot 'infra\bootstrap-resource-group.bicep'),
            '--parameters', "location=$location", "prefix=$producerPrefix", '--only-show-errors'
        )
        break
    }
    'Plan' {
        Assert-DeploymentContext -SubscriptionId $subscriptionId -TenantId $tenantId
        $deploymentName = [string](Get-ConfigValue -Config $config -Path @('producer', 'planDeploymentName') -Required)
        Invoke-DeploymentAz -Arguments @(
            'deployment', 'group', $deploymentOperation[$Action], '--subscription', "$subscriptionId",
            '--resource-group', $producerNames.ResourceGroupName, '--name', $deploymentName, '--mode', 'Incremental',
            '--template-file', (Join-Path $kitRoot 'infra\plan.bicep'),
            '--parameters', "location=$location", "prefix=$producerPrefix", '--only-show-errors'
        )
        break
    }
    'ProducerData' {
        Invoke-KitScript -RelativeScript 'scripts\Deploy-Infrastructure.ps1' -Parameters @{
            SubscriptionId = $subscriptionId
            TenantId = $tenantId
            Prefix = $producerPrefix
            StorageName = $storageName
            DeploymentName = [string](Get-ConfigValue -Config $config -Path @('producer', 'dataDeploymentName') -Required)
            Location = $location
            Action = $Action
            PublicStorage = $false
            UseExistingPlan = $true
            DeferApplication = $true
            SecurityPerimeter = $securityPerimeter
            UnrestrictedPortal = $unrestrictedPortal
            ApprovedIpCidr = $approvedIpCidr
            ApproveResourceCostsAndRoleAssignments = $ApproveResourceCostsAndRoleAssignments
        }
        break
    }
    'ProducerApp' {
        Invoke-KitScript -RelativeScript 'scripts\Deploy-Infrastructure.ps1' -Parameters @{
            SubscriptionId = $subscriptionId
            TenantId = $tenantId
            Prefix = $producerPrefix
            StorageName = $storageName
            DeploymentName = [string](Get-ConfigValue -Config $config -Path @('producer', 'appDeploymentName') -Required)
            Location = $location
            Action = $Action
            PublicStorage = $false
            UseExistingPlan = $true
            DeferApplication = $false
            ResolveExistingDataEndpoints = $true
            SecurityPerimeter = $securityPerimeter
            UnrestrictedPortal = $unrestrictedPortal
            ApprovedIpCidr = $approvedIpCidr
            ApproveResourceCostsAndRoleAssignments = $ApproveResourceCostsAndRoleAssignments
        }
        break
    }
    'Readiness' {
        Invoke-KitScript -RelativeScript 'scripts\Deploy-Readiness.ps1' -Parameters @{
            SubscriptionId = $subscriptionId
            TenantId = $tenantId
            ResourceGroupName = $producerNames.ResourceGroupName
            WorkspaceName = $producerNames.WorkspaceName
            AppName = $producerNames.AppName
            DeploymentName = [string](Get-ConfigValue -Config $config -Path @('producer', 'readinessDeploymentName') -Required)
            Action = $Action
            ApproveResourceCostsAndRoleAssignments = $ApproveResourceCostsAndRoleAssignments
        }
        break
    }
    'ProducerPackage' {
        $outputDirectory = Resolve-KitPath -PathValue (Get-ConfigValue -Config $config -Path @('producer', 'packageOutputDirectory') -Required)
        $result = Invoke-KitScript -RelativeScript 'scripts\package.ps1' -Parameters @{ OutputDirectory = $outputDirectory } | ConvertFrom-Json
        [pscustomobject]@{
            Stage = $Stage
            Action = $Action
            PackagePath = (Join-Path $outputDirectory 'domains-v2.zip')
            ActualSHA256 = $result.sha256
            Entries = $result.entries
            Bytes = $result.bytes
            ApprovedSHA256 = [string](Get-ConfigValue -Config $config -Path @('producer', 'approvedPackageSha256') -Default '')
            ReviewRequired = 'Do not copy ActualSHA256 into approvedPackageSha256 until an independent review approves this candidate.'
        }
        break
    }
    'ProducerPublish' {
        $approvedSha = [string](Get-ConfigValue -Config $config -Path @('producer', 'approvedPackageSha256') -Required)
        Invoke-KitScript -RelativeScript 'scripts\Publish-Code.ps1' -Parameters @{
            SubscriptionId = $subscriptionId
            TenantId = $tenantId
            ResourceGroupName = $producerNames.ResourceGroupName
            AppName = $producerNames.AppName
            PackagePath = (Join-Path (Resolve-KitPath -PathValue (Get-ConfigValue -Config $config -Path @('producer', 'packageOutputDirectory') -Required)) 'domains-v2.zip')
            ExpectedSHA256 = $approvedSha
            ApproveDeployment = $ApproveDeployment
        }
        break
    }
}
