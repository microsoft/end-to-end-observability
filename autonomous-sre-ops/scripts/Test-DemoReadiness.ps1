#Requires -Version 7.5
<#
.SYNOPSIS
Read-only readiness checks for the autonomous SRE operations demo.

.DESCRIPTION
Source    : required files, terraform fmt/validate (no backend, no Azure access), product configuration still disabled.
Preflight : Source + Azure subscription context, resource-provider registration, the pinned VM image, VM quota and
            informational Defender plan tiers. Azure Resource Manager is only read.
Delivery  : Preflight + Terraform state access using infra/environments/backend.hcl.
Prints one JSON object and exits 1 when any check failed. Nothing is created, changed or deleted.
#>
[CmdletBinding()]
param(
    [ValidateSet('Source', 'Preflight', 'Delivery')][string]$Phase = 'Source',
    [string]$SubscriptionId,
    [string]$TenantId
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'Context.Common.ps1')

$Repo = Split-Path $PSScriptRoot -Parent
$Root = Join-Path $Repo 'infra'
$Location = 'centralus'
$Checks = [Collections.Generic.List[object]]::new()
$Warnings = [Collections.Generic.List[string]]::new()

# Every resource-provider namespace the Terraform module touches. The provider block sets
# resource_provider_registrations = "none", so an unregistered namespace would fail the apply late.
# tests/Test-DemoReadiness.ps1 asserts that every Microsoft.<Namespace> token in infra/*.tf is listed here.
$ProviderNamespaces = @(
    'Microsoft.AlertsManagement',
    'Microsoft.App',
    'Microsoft.Authorization',
    'Microsoft.Compute',
    'Microsoft.ContainerRegistry',
    'Microsoft.Insights',
    'Microsoft.KeyVault',
    'Microsoft.ManagedIdentity',
    'Microsoft.Network',
    'Microsoft.OperationalInsights',
    'Microsoft.RecoveryServices',
    'Microsoft.ResourceGraph',
    'Microsoft.Resources',
    'Microsoft.Security',
    'Microsoft.Storage'
)
$VmCount = 3
$QuotaTable = @{ 'Standard_D2s_v3' = @{ family = 'standardDSv3Family'; vcpus = 2 } }
$DefenderPlans = @('VirtualMachines', 'StorageAccounts')

function Add-Check {
    param([string]$Name, [bool]$Passed, [string]$Detail)
    $Checks.Add([pscustomobject]@{ name = $Name; passed = $Passed; detail = $Detail })
}

function Limit-Text {
    param($Text, [int]$Length = 300)
    $Value = (@($Text) -join ' ').Trim()
    if ($Value.Length -gt $Length) { return $Value.Substring(0, $Length) }
    return $Value
}

function Invoke-AzRead {
    # Read-only Azure CLI call; the subscription is always explicit.
    param([Parameter(Mandatory)][string[]]$Arguments)
    $Output = & az @Arguments --subscription $SubscriptionId --only-show-errors 2>&1
    return [pscustomobject]@{ ExitCode = $LASTEXITCODE; Output = @($Output) }
}

function Test-GuidText {
    param($Value)
    return $Value -is [string] -and [regex]::IsMatch($Value, '\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\z')
}

if ($Phase -ne 'Source' -or -not [string]::IsNullOrEmpty($SubscriptionId) -or -not [string]::IsNullOrEmpty($TenantId)) {
    if (-not (Test-GuidText $SubscriptionId)) { throw 'SubscriptionId must be a lowercase GUID.' }
    if (-not (Test-GuidText $TenantId)) { throw 'TenantId must be a lowercase GUID.' }
}

# --- Source -------------------------------------------------------------------------------------------------------
foreach ($Path in @('infra/versions.tf', 'infra/backend.tf', 'infra/.terraform.lock.hcl',
        'infra/environments/backend.hcl.example', 'infra/environments/demo.tfvars.example',
        'schemas/asre-context.v1.schema.json',
        'workloads/linux-demo-service/guest_control.py', 'contracts/servicenow-case.schema.json')) {
    Add-Check $Path (Test-Path -LiteralPath (Join-Path $Repo $Path) -PathType Leaf) 'Required preparation artifact'
}

$Init = & terraform "-chdir=$Root" init -backend=false -input=false '-lockfile=readonly' 2>&1
Add-Check 'terraform-init' ($LASTEXITCODE -eq 0) (Limit-Text $Init 600)
$Format = & terraform "-chdir=$Root" fmt -check -recursive 2>&1
Add-Check 'terraform-format' ($LASTEXITCODE -eq 0) (Limit-Text $Format 600)
$Validation = & terraform "-chdir=$Root" validate -json
if ($LASTEXITCODE -eq 0) {
    $Result = (@($Validation) -join "`n") | ConvertFrom-Json
    Add-Check 'terraform-schema' ($Result.valid -eq $true) 'Pinned-provider local schema validation; not a live Azure plan'
}
else {
    Add-Check 'terraform-schema' $false (Limit-Text $Validation 600)
}

foreach ($Config in @(@('response-plans', 'plans.json', 'plans'), @('scheduled-tasks', 'tasks.json', 'tasks'))) {
    $Path = Join-Path (Join-Path (Join-Path $Repo 'agent') $Config[0]) $Config[1]
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        Add-Check $Config[0] $false 'The product configuration manifest is missing'
        continue
    }
    try { $Manifest = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json }
    catch {
        Add-Check $Config[0] $false 'The product configuration manifest is unreadable'
        continue
    }
    if ($Manifest -isnot [pscustomobject] -or -not $Manifest.PSObject.Properties[$Config[2]]) {
        Add-Check $Config[0] $false "The product configuration manifest does not list its $($Config[2])"
        continue
    }
    $Items = @($Manifest.($Config[2]))
    # Fail closed: only an explicit boolean false counts as disabled / not deployed.
    $Enabled = @($Items | Where-Object {
        -not ($_ -is [pscustomobject] -and $null -ne $_.PSObject.Properties['enabled'] -and $_.enabled -is [bool] -and $_.enabled -eq $false)
    })
    $Deployed = -not ($null -ne $Manifest.PSObject.Properties['deployed'] -and $Manifest.deployed -is [bool] -and $Manifest.deployed -eq $false)
    Add-Check $Config[0] ($Enabled.Count -eq 0 -and -not $Deployed) 'Prepared only; all product configuration remains disabled'
}

# --- Preflight / Delivery -----------------------------------------------------------------------------------------
if ($Phase -ne 'Source') {
    $Authenticated = $false
    try {
        Assert-AsreSubscription -Context @{ subscription_id = $SubscriptionId; tenant_id = $TenantId }
        $Authenticated = $true
        Add-Check 'azure-context' $true 'Explicit target subscription and tenant are enabled for the signed-in Azure CLI session'
    }
    catch { Add-Check 'azure-context' $false (Limit-Text $_.Exception.Message) }

    if ($Authenticated) {
        foreach ($Namespace in $ProviderNamespaces) {
            $Provider = Invoke-AzRead -Arguments @('provider', 'show', '--namespace', $Namespace,
                '--query', 'registrationState', '--output', 'tsv')
            $State = (@($Provider.Output) -join '').Trim()
            Add-Check "provider:$Namespace" ($Provider.ExitCode -eq 0 -and $State -ceq 'Registered') "Observed registration state: $(Limit-Text $State 80)"
        }

        # The NAT gateway uses a Standard-SKU public IP; some subscriptions reject it until this feature is registered
        # (SubscriptionNotRegisteredForFeature, late in the apply). Read-only: the script never registers anything.
        $FeatureNamespace = 'Microsoft.Network'
        $FeatureName = 'AllowBringYourOwnPublicIpAddress'
        $Feature = Invoke-AzRead -Arguments @('feature', 'show', '--namespace', $FeatureNamespace, '--name', $FeatureName,
            '--query', 'properties.state', '--output', 'tsv')
        $FeatureState = (@($Feature.Output) -join '').Trim()
        if ($Feature.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($FeatureState)) {
            $Warnings.Add("Informational: the $FeatureNamespace feature $FeatureName could not be read; a late SubscriptionNotRegisteredForFeature failure is possible.")
        }
        elseif ($FeatureState -ceq 'Registered') {
            Add-Check 'network-feature' $true "$FeatureNamespace/$FeatureName is Registered"
        }
        else {
            Add-Check 'network-feature' $false ("$FeatureNamespace/$FeatureName is $(Limit-Text $FeatureState 80), not Registered. The operator must run " +
                "'az feature register --namespace $FeatureNamespace --name $FeatureName' and then " +
                "'az provider register --namespace $FeatureNamespace'; this script never registers anything.")
        }

        $Compute = Get-Content -LiteralPath (Join-Path $Root 'compute.tf') -Raw
        $Block = [regex]::Match($Compute, 'source_image_reference\s*\{(?<body>[^}]*)\}')
        $Image = @{}
        if ($Block.Success) {
            foreach ($Key in @('publisher', 'offer', 'sku', 'version')) {
                $Match = [regex]::Match($Block.Groups['body'].Value, "(?m)^\s*$Key\s*=\s*`"(?<value>[^`"]+)`"")
                if ($Match.Success) { $Image[$Key] = $Match.Groups['value'].Value }
            }
        }
        if ($Image.Count -eq 4 -and [regex]::IsMatch($Image['version'], '\A[0-9]+(\.[0-9]+)+\z')) {
            $Urn = "$($Image['publisher']):$($Image['offer']):$($Image['sku']):$($Image['version'])"
            $Show = Invoke-AzRead -Arguments @('vm', 'image', 'show', '--location', $Location, '--urn', $Urn, '--output', 'json')
            Add-Check 'vm-image' ($Show.ExitCode -eq 0) $(if ($Show.ExitCode -eq 0) { "Pinned image $Urn exists in $Location" } else { "Pinned image $Urn was not found in $Location" })
        }
        else {
            Add-Check 'vm-image' $false 'infra/compute.tf does not pin an exact marketplace image version'
        }

        $Size = [regex]::Match($Compute, '(?m)^\s*size\s*=\s*"(?<size>Standard_[A-Za-z0-9_]+)"')
        if ($Size.Success -and $QuotaTable.ContainsKey($Size.Groups['size'].Value)) {
            $Plan = $QuotaTable[$Size.Groups['size'].Value]
            $Needed = $VmCount * $Plan.vcpus
            $Usage = Invoke-AzRead -Arguments @('vm', 'list-usage', '--location', $Location, '--output', 'json')
            if ($Usage.ExitCode -ne 0) {
                Add-Check 'vm-quota' $false (Limit-Text $Usage.Output)
            }
            else {
                $Entries = @((@($Usage.Output) -join "`n") | ConvertFrom-Json)
                $Detail = [Collections.Generic.List[string]]::new()
                $Enough = $true
                foreach ($Counter in @('cores', $Plan.family)) {
                    $Entry = @($Entries | Where-Object { $_.name.value -ceq $Counter }) | Select-Object -First 1
                    if ($null -eq $Entry) { $Enough = $false; $Detail.Add("$Counter not reported"); continue }
                    $Free = [int]$Entry.limit - [int]$Entry.currentValue
                    if ($Free -lt $Needed) { $Enough = $false }
                    $Detail.Add("${Counter}: $Free free of $($Entry.limit), need $Needed")
                }
                Add-Check 'vm-quota' $Enough ($Detail -join '; ')
            }
        }
        else {
            Add-Check 'vm-quota' $false 'infra/compute.tf uses a VM size the readiness quota table does not cover'
        }

        foreach ($DefenderPlan in $DefenderPlans) {
            $Tier = Invoke-AzRead -Arguments @('security', 'pricing', 'show', '--name', $DefenderPlan,
                '--query', 'pricingTier', '--output', 'tsv')
            if ($Tier.ExitCode -eq 0) {
                $Warnings.Add("Informational: Defender plan $DefenderPlan tier is $(Limit-Text (@($Tier.Output) -join '') 40).")
            }
            else {
                $Warnings.Add("Informational: Defender plan $DefenderPlan tier could not be read.")
            }
        }

        if ($Phase -eq 'Delivery') {
            $BackendFile = Join-Path $Root 'environments\backend.hcl'
            if (-not (Test-Path -LiteralPath $BackendFile -PathType Leaf)) {
                Add-Check 'state-backend-file' $false 'infra/environments/backend.hcl is missing; run scripts/Initialize-TerraformState.ps1'
            }
            else {
                $Settings = @{}
                foreach ($Line in [IO.File]::ReadAllLines($BackendFile)) {
                    $Match = [regex]::Match($Line, '\A\s*(?<key>[a-z_]+)\s*=\s*(?:"(?<text>[^"]*)"|(?<bare>true|false))\s*\z')
                    if ($Match.Success) {
                        $Settings[$Match.Groups['key'].Value] = if ($Match.Groups['text'].Success) { $Match.Groups['text'].Value } else { $Match.Groups['bare'].Value }
                    }
                }
                $Valid = $Settings.ContainsKey('storage_account_name') -and
                    [regex]::IsMatch($Settings['storage_account_name'], '\A[a-z0-9]{3,24}\z') -and
                    $Settings.ContainsKey('container_name') -and
                    [regex]::IsMatch($Settings['container_name'], '\A[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?\z') -and
                    $Settings['use_azuread_auth'] -ceq 'true' -and
                    $Settings['subscription_id'] -ceq $SubscriptionId -and $Settings['tenant_id'] -ceq $TenantId
                if (-not $Valid) {
                    Add-Check 'state-backend-file' $false 'backend.hcl is incomplete, does not use Entra authentication, or belongs to another subscription or tenant'
                }
                else {
                    Add-Check 'state-backend-file' $true 'backend.hcl names the state account and uses Entra authentication'
                    $Blobs = Invoke-AzRead -Arguments @('storage', 'blob', 'list', '--account-name', $Settings['storage_account_name'],
                        '--container-name', $Settings['container_name'], '--auth-mode', 'login', '--num-results', '1',
                        '--query', '[].name', '--output', 'json')
                    Add-Check 'private-state-access' ($Blobs.ExitCode -eq 0) $(if ($Blobs.ExitCode -eq 0) { 'The signed-in identity can read the state container' } else { Limit-Text $Blobs.Output })
                }
            }
        }
    }
}

$Failed = @($Checks | Where-Object { -not $_.passed })
[pscustomobject]@{
    phase               = $Phase
    checked_at          = [DateTimeOffset]::UtcNow.ToString('o')
    passed              = ($Failed.Count -eq 0)
    demo_ready          = $false
    requires_saved_plan = $true
    faults_enabled      = $false
    checks              = $Checks
    warnings            = $Warnings
} | ConvertTo-Json -Depth 6
if ($Failed.Count) { exit 1 }
