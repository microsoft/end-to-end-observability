#Requires -Version 7.5
<#
.SYNOPSIS
Creates (or corrects) the Entra-only Terraform state storage account for one subscription and writes backend.hcl.

.DESCRIPTION
Idempotent. In centralus it ensures the resource group rg-asre-state-cus, the storage account stasrestate<suffix>
(shared-key access off, no public blob access, TLS 1.2, blob versioning, blob and container soft delete, default
network action Deny with exactly one rule for the operator's public IPv4 address), the private container tfstate, and the
operator's Storage Blob Data Contributor assignment. It then waits, bounded, for data-plane access with the operator's
Entra identity and only then writes the backend file. Drift on an existing account is corrected in place; nothing is
recreated or deleted. Keys, SAS tokens and connection strings are never requested. -WhatIf makes no mutating call.

After the last account-level write, every real run reads the account back and fails closed, before the data-plane wait
and before any backend file is written, unless public network access is Enabled (with -ApplyPolicyExclusionTag the
exclusion tag must also be present). A tenant policy that forces public network access off therefore stops the run with
a message instead of ending in a misleading propagation timeout. -WhatIf cannot show this; only the real run's read-back can.

.PARAMETER ApplyPolicyExclusionTag
Opt-in, for a tenant whose Azure Policy rewrites a storage account's public network access to Disabled unless the account
carries the tag SecurityControl=Ignore. Tags ONLY the state storage account, in the same create call for a new account, or
with a Merge tag update before the first account write for an existing one (other tags are kept). The resource group is
never tagged. The tag is a self-service bypass of that policy: see docs\deployment.md, Wave 0, for what it does, what it
does not do and the compensating controls. Never on by default; there is no environment-variable or configuration trigger
and no automatic retry with the tag. The run emits exactly one warning when the switch is given.
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)]
    [ValidateScript({ [regex]::IsMatch($_, '\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\z') },
        ErrorMessage = 'SubscriptionId must be a lowercase GUID.')][string]$SubscriptionId,
    [Parameter(Mandatory)]
    [ValidateScript({ [regex]::IsMatch($_, '\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\z') },
        ErrorMessage = 'TenantId must be a lowercase GUID.')][string]$TenantId,
    [Parameter(Mandatory)][string]$OperatorIp,
    [ValidateScript({ [regex]::IsMatch($_, '\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\z') },
        ErrorMessage = 'OperatorObjectId must be a lowercase GUID.')][string]$OperatorObjectId,
    [string]$BackendFile,
    [switch]$ApplyPolicyExclusionTag
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'Context.Common.ps1')

$Repo = Split-Path $PSScriptRoot -Parent
$Location = 'centralus'
$ResourceGroup = 'rg-asre-state-cus'
$Container = 'tfstate'
$StateKey = 'autonomous-sre-ops/demo.tfstate'
$RoleName = 'Storage Blob Data Contributor'
$RetentionDays = 14
$PollAttempts = 24
$PollSeconds = 10
$PolicyExclusionTagName = 'SecurityControl'
$PolicyExclusionTagValue = 'Ignore'
$PolicyExclusionTag = "$PolicyExclusionTagName=$PolicyExclusionTagValue"

function Test-PublicIpv4 {
    # A single, plain, globally routable-looking IPv4 address: no CIDR, no private/loopback/link-local/CGNAT/multicast.
    param([string]$Address)
    $octet = '(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])'
    if (-not [regex]::IsMatch($Address, "\A(?:$octet\.){3}$octet\z")) { return $false }
    $o = @($Address.Split('.') | ForEach-Object { [int]$_ })
    if ($o[0] -in @(0, 10, 127) -or $o[0] -ge 224) { return $false }
    if ($o[0] -eq 100 -and $o[1] -ge 64 -and $o[1] -le 127) { return $false }
    if ($o[0] -eq 169 -and $o[1] -eq 254) { return $false }
    if ($o[0] -eq 172 -and $o[1] -ge 16 -and $o[1] -le 31) { return $false }
    if ($o[0] -eq 192 -and ($o[1] -eq 168 -or ($o[1] -eq 0 -and $o[2] -eq 0))) { return $false }
    if ($o[0] -eq 198 -and $o[1] -in @(18, 19)) { return $false }
    return $true
}
if (-not (Test-PublicIpv4 -Address $OperatorIp)) {
    throw 'OperatorIp must be one plain public IPv4 address (no CIDR range, private, loopback, link-local, shared or multicast address).'
}
if ([string]::IsNullOrWhiteSpace($BackendFile)) { $BackendFile = Join-Path $Repo 'infra\environments\backend.hcl' }
$BackendFile = [IO.Path]::GetFullPath($BackendFile)
if (-not $BackendFile.EndsWith('.hcl', [StringComparison]::OrdinalIgnoreCase)) { throw 'BackendFile must be an .hcl file.' }
if (-not (Test-Path -LiteralPath (Split-Path $BackendFile -Parent) -PathType Container)) { throw 'The BackendFile directory does not exist.' }

$Hash = [Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes("${SubscriptionId}:asre-demo"))
$Suffix = [Convert]::ToHexString($Hash).ToLowerInvariant().Substring(0, 10)
$Account = "stasrestate$Suffix"
$AccountId = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup/providers/Microsoft.Storage/storageAccounts/$Account"

function Invoke-AzRead {
    # Read-only Azure CLI call; scoped with --subscription except for directory (ad) verbs, which reject that flag;
    # failures never echo output.
    param([string[]]$Arguments)
    $Scope = @(if ($Arguments[0] -cne 'ad') { '--subscription', $SubscriptionId })
    $json = & az @Arguments @Scope --only-show-errors --output json
    if ($LASTEXITCODE -ne 0) { throw "Azure CLI failed: $($Arguments[0..2] -join ' ')." }
    $text = (@($json) -join "`n")
    if ([string]::IsNullOrWhiteSpace($text)) { return $null }
    return $text | ConvertFrom-Json
}

function Get-Field {
    # Null-safe walk of a parsed Azure CLI document (a missing property is drift, not a StrictMode error).
    param($Object, [string[]]$Path)
    foreach ($Segment in $Path) {
        if ($null -eq $Object -or $null -eq $Object.PSObject.Properties[$Segment]) { return $null }
        $Object = $Object.PSObject.Properties[$Segment].Value
    }
    return $Object
}

function Invoke-AzWrite {
    # The only mutating path: behind ShouldProcess so -WhatIf performs no mutating call.
    param([string[]]$Arguments, [string]$Target)
    if (-not $PSCmdlet.ShouldProcess($Target, "az $($Arguments[0..2] -join ' ')")) { return }
    & az @Arguments --subscription $SubscriptionId --only-show-errors --output none
    if ($LASTEXITCODE -ne 0) { throw "Azure CLI failed: $($Arguments[0..2] -join ' ')." }
}

Assert-AsreSubscription -Context @{ subscription_id = $SubscriptionId; tenant_id = $TenantId }
if ([string]::IsNullOrWhiteSpace($OperatorObjectId)) {
    # A separate variable: assigning a bad value to the validated parameter would fail with a generic validation error.
    $Resolved = ([string](Invoke-AzRead -Arguments @('ad', 'signed-in-user', 'show', '--query', 'id'))).Trim().ToLowerInvariant()
    if (-not [regex]::IsMatch($Resolved, '\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\z')) {
        throw 'Azure CLI did not return the signed-in operator object id; pass -OperatorObjectId.'
    }
    $OperatorObjectId = $Resolved
}
if ($ApplyPolicyExclusionTag) {
    Write-Warning ("-ApplyPolicyExclusionTag: the state storage account $Account, and only that account, is tagged " +
        "$PolicyExclusionTag so that a tenant policy cannot force its public network access off. The resource group is " +
        'never tagged. See docs\deployment.md, Wave 0, for the compensating controls.')
}

# --- Resource group --------------------------------------------------------------------------------------------------
$GroupExists = [bool](Invoke-AzRead -Arguments @('group', 'exists', '--name', $ResourceGroup))
if (-not $GroupExists) {
    Invoke-AzWrite -Target $ResourceGroup -Arguments @('group', 'create', '--name', $ResourceGroup, '--location', $Location)
}

# --- Storage account (create once, then correct drift in place) -------------------------------------------------------
$Present = @()
if ($GroupExists) {
    $Present = @(Invoke-AzRead -Arguments @('storage', 'account', 'list', '--resource-group', $ResourceGroup,
            '--query', "[?name=='$Account'].name") | Where-Object { $null -ne $_ })
}
$AccountKnown = $Present -ccontains $Account
if (-not $AccountKnown) {
    $Check = Invoke-AzRead -Arguments @('storage', 'account', 'check-name', '--name', $Account)
    if ((Get-Field $Check @('nameAvailable')) -ne $true) { throw 'The derived state storage account name is not available to this subscription.' }
    $CreateArguments = @(
        'storage', 'account', 'create', '--name', $Account, '--resource-group', $ResourceGroup, '--location', $Location,
        '--kind', 'StorageV2', '--sku', 'Standard_LRS', '--allow-blob-public-access', 'false',
        '--allow-shared-key-access', 'false', '--min-tls-version', 'TLS1_2', '--https-only', 'true',
        '--default-action', 'Deny', '--bypass', 'None')
    # The tag travels in the same create call, so no write is ever made to an untagged new account.
    if ($ApplyPolicyExclusionTag) { $CreateArguments += @('--tags', $PolicyExclusionTag) }
    Invoke-AzWrite -Target $Account -Arguments $CreateArguments
    $AccountKnown = -not $WhatIfPreference
}
if ($AccountKnown) {
    $Properties = Invoke-AzRead -Arguments @('storage', 'account', 'show', '--name', $Account, '--resource-group', $ResourceGroup)
    if ((Get-Field $Properties @('id')) -cne $AccountId) { throw 'The state storage account is not the expected resource.' }
    # Merge keeps the other tags (an account update --tags would replace the whole set). Before the first account-level
    # write, so that no write can be rewritten by the tenant policy. A wrong value counts as untagged.
    if ($ApplyPolicyExclusionTag -and (Get-Field $Properties @('tags', $PolicyExclusionTagName)) -cne $PolicyExclusionTagValue) {
        Invoke-AzWrite -Target $Account -Arguments @('tag', 'update', '--resource-id', $AccountId, '--operation', 'Merge',
            '--tags', $PolicyExclusionTag)
    }
    $Rules = Get-Field $Properties @('networkRuleSet')
    $Drift = (Get-Field $Properties @('allowBlobPublicAccess')) -ne $false -or
        (Get-Field $Properties @('allowSharedKeyAccess')) -ne $false -or
        (Get-Field $Properties @('minimumTlsVersion')) -cne 'TLS1_2' -or
        (Get-Field $Properties @('enableHttpsTrafficOnly')) -ne $true -or
        (Get-Field $Rules @('defaultAction')) -cne 'Deny' -or (Get-Field $Rules @('bypass')) -cne 'None' -or
        (Get-Field $Properties @('publicNetworkAccess')) -cne 'Enabled'
    if ($Drift) {
        Invoke-AzWrite -Target $Account -Arguments @(
            'storage', 'account', 'update', '--name', $Account, '--resource-group', $ResourceGroup,
            '--allow-blob-public-access', 'false', '--allow-shared-key-access', 'false', '--min-tls-version', 'TLS1_2',
            '--https-only', 'true', '--default-action', 'Deny', '--bypass', 'None', '--public-network-access', 'Enabled')
    }
    $Existing = @(@(Get-Field $Rules @('ipRules')) | ForEach-Object { Get-Field $_ @('ipAddressOrRange') } | Where-Object { $null -ne $_ })
    foreach ($Stale in @($Existing | Where-Object { $_ -cne $OperatorIp })) {
        Invoke-AzWrite -Target $Account -Arguments @('storage', 'account', 'network-rule', 'remove',
            '--account-name', $Account, '--resource-group', $ResourceGroup, '--ip-address', $Stale)
    }
    if ($Existing -cnotcontains $OperatorIp) {
        Invoke-AzWrite -Target $Account -Arguments @('storage', 'account', 'network-rule', 'add',
            '--account-name', $Account, '--resource-group', $ResourceGroup, '--ip-address', $OperatorIp)
    }
    # Fail closed on a tenant policy that rewrites public network access: read the account back fresh after the last
    # account-level write and before the data-plane wait, the blob service, the container, the role and the backend file.
    # -WhatIf wrote nothing, so there is nothing to read back.
    if (-not $WhatIfPreference) {
        $Final = Invoke-AzRead -Arguments @('storage', 'account', 'show', '--name', $Account, '--resource-group', $ResourceGroup)
        $Access = Get-Field $Final @('publicNetworkAccess')
        $AccessText = if ($null -eq $Access) { '<not reported>' } else { "$Access" }
        $Tag = Get-Field $Final @('tags', $PolicyExclusionTagName)
        $TagText = if ($null -eq $Tag) { '<absent>' } else { "$Tag" }
        if ($ApplyPolicyExclusionTag -and $Tag -cne $PolicyExclusionTagValue) {
            throw ("-ApplyPolicyExclusionTag was given, but the tag $PolicyExclusionTagName on the state storage account is " +
                "'$TagText' after the writes, not '$PolicyExclusionTagValue'. The tag was not applied or was removed. Check " +
                'your permissions to write tags and the Activity Log. No backend file was written.')
        }
        if ($Access -cne 'Enabled' -and $ApplyPolicyExclusionTag) {
            throw ("The state storage account public network access is '$AccessText', not 'Enabled', although the account " +
                "carries the tag $PolicyExclusionTag. The exclusion tag did not help: another policy parameter, a network " +
                'perimeter, or propagation delay may apply (if it is propagation, wait and re-run). Local Terraform state ' +
                'is not a workaround. See docs\deployment.md, Wave 0. No backend file was written.')
        }
        if ($Access -cne 'Enabled') {
            throw ("The state storage account public network access is '$AccessText', not 'Enabled'. Most likely a tenant " +
                'policy forces it to Disabled (for example the SFI network-security Modify policy ' +
                'StorageAccount_PublicNetwork_Modify). Re-run with -ApplyPolicyExclusionTag; see docs\deployment.md, ' +
                'Wave 0. No backend file was written.')
        }
    }
    $Service = Invoke-AzRead -Arguments @('storage', 'account', 'blob-service-properties', 'show',
        '--account-name', $Account, '--resource-group', $ResourceGroup)
}
else { $Service = $null }
$ServiceDrift = $null -eq $Service -or (Get-Field $Service @('isVersioningEnabled')) -ne $true -or
    (Get-Field $Service @('deleteRetentionPolicy', 'enabled')) -ne $true -or
    [int](Get-Field $Service @('deleteRetentionPolicy', 'days')) -lt 7 -or
    (Get-Field $Service @('containerDeleteRetentionPolicy', 'enabled')) -ne $true -or
    [int](Get-Field $Service @('containerDeleteRetentionPolicy', 'days')) -lt 7
if ($ServiceDrift) {
    Invoke-AzWrite -Target $Account -Arguments @('storage', 'account', 'blob-service-properties', 'update',
        '--account-name', $Account, '--resource-group', $ResourceGroup, '--enable-versioning', 'true',
        '--enable-delete-retention', 'true', '--delete-retention-days', "$RetentionDays",
        '--enable-container-delete-retention', 'true', '--container-delete-retention-days', "$RetentionDays")
}

# --- Container (control plane, so it does not depend on role propagation) and operator role -------------------------
$Containers = @()
if ($AccountKnown) {
    $Containers = @(Invoke-AzRead -Arguments @('storage', 'container-rm', 'list', '--storage-account', $Account,
            '--resource-group', $ResourceGroup, '--query', "[?name=='$Container'].name") | Where-Object { $null -ne $_ })
}
if ($Containers -cnotcontains $Container) {
    Invoke-AzWrite -Target $Container -Arguments @('storage', 'container-rm', 'create', '--storage-account', $Account,
        '--resource-group', $ResourceGroup, '--name', $Container, '--public-access', 'none')
}
$Assigned = @()
if ($AccountKnown) {
    $Assigned = @(Invoke-AzRead -Arguments @('role', 'assignment', 'list', '--scope', $AccountId, '--include-inherited',
            '--query', "[?principalId=='$OperatorObjectId'&&roleDefinitionName=='$RoleName'].id") | Where-Object { $null -ne $_ })
}
if ($Assigned.Count -eq 0) {
    Invoke-AzWrite -Target $OperatorObjectId -Arguments @('role', 'assignment', 'create', '--assignee-object-id', $OperatorObjectId,
        '--assignee-principal-type', 'User', '--role', $RoleName, '--scope', $AccountId)
}

# --- Bounded wait for data-plane access with the operator's Entra identity, then the backend file -------------------
if ($WhatIfPreference) {
    Write-Output "What if: the backend file $BackendFile would be written after data-plane access is confirmed."
    return
}
$Ready = $false
for ($Attempt = 1; $Attempt -le $PollAttempts -and -not $Ready; $Attempt++) {
    $null = & az storage blob list --account-name $Account --container-name $Container --auth-mode login --num-results 1 `
        --subscription $SubscriptionId --only-show-errors --output none
    if ($LASTEXITCODE -eq 0) { $Ready = $true }
    elseif ($Attempt -lt $PollAttempts) { Start-Sleep -Seconds $PollSeconds }
}
if (-not $Ready) {
    throw 'The state container is not yet reachable with the operator identity (role or network propagation). Re-run this script in a few minutes.'
}
$Lines = @(
    "resource_group_name  = `"$ResourceGroup`"",
    "storage_account_name = `"$Account`"",
    "container_name       = `"$Container`"",
    "key                  = `"$StateKey`"",
    'use_azuread_auth     = true',
    "subscription_id      = `"$SubscriptionId`"",
    "tenant_id            = `"$TenantId`""
)
$Temporary = "$BackendFile.tmp-$([guid]::NewGuid().ToString('N'))"
try {
    [IO.File]::WriteAllText($Temporary, (($Lines -join "`n") + "`n"), [Text.UTF8Encoding]::new($false))
    [IO.File]::Move($Temporary, $BackendFile, $true)
}
finally { if (Test-Path -LiteralPath $Temporary) { Remove-Item -LiteralPath $Temporary -Force } }
Write-Output "State backend ready: account $Account, container $Container (Entra-only, operator address rule)."
Write-Output "Backend file written: $BackendFile"
Write-Output "Operator object id for operator_object_id: $OperatorObjectId"
