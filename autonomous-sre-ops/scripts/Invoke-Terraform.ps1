#Requires -Version 7.5
<#
.SYNOPSIS
Plans and applies the autonomous-sre-ops Terraform root from an operator workstation.

.DESCRIPTION
Plan  : validates the toolchain and the signed-in Azure CLI account, plans against the remote Entra-only state,
        refuses every deletion or replacement, and publishes a self-contained, hash-pinned artifact directory
        (saved plan, source copy, manifest.json). -WhatIf is ignored by Plan: it never changes Azure.
Apply : re-verifies the artifact (subscription/tenant, terraform version, 4-hour expiry, provider lock file,
        every SHA-256, no extra files, clean source) and applies exactly the saved plan. The apply itself sits
        behind ShouldProcess, so -WhatIf runs every verification and stops before the apply.

Authentication is the Azure CLI session of the operator. Ambient service-principal, OIDC, managed-identity and
state-credential variables are removed from the process so a stray credential cannot change the identity.
#>
[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High')]
param(
    [Parameter(Mandatory)][ValidateSet('Plan', 'Apply')][string]$Action,
    [Parameter(Mandatory)][string]$ArtifactDirectory,
    [Parameter(Mandatory)]
    [ValidateScript({ [regex]::IsMatch($_, '\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\z') },
        ErrorMessage = 'SubscriptionId must be a lowercase GUID.')][string]$SubscriptionId,
    [Parameter(Mandatory)]
    [ValidateScript({ [regex]::IsMatch($_, '\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\z') },
        ErrorMessage = 'TenantId must be a lowercase GUID.')][string]$TenantId,
    [string]$VarFile = 'environments/demo.tfvars',
    [switch]$AllowDirty
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'Context.Common.ps1')

$TerraformVersion = '1.11.2'
$Repo = Split-Path $PSScriptRoot -Parent
$Root = Join-Path $Repo 'infra'
$BackendConfig = 'environments/backend.hcl'

function Invoke-TerraformChecked {
    param([string[]]$Arguments)
    & terraform @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Terraform $($Arguments[1]) failed with exit code $LASTEXITCODE." }
}

function Get-OptionalProperty {
    param($Object, [string]$Name)
    if ($null -eq $Object) { return $null }
    $Property = $Object.PSObject.Properties[$Name]
    if ($null -eq $Property) { return $null }
    return $Property.Value
}

function Assert-BackendFile {
    # The state backend must be the Entra-only backend of the requested subscription; no credential key may be present.
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw 'infra/environments/backend.hcl is missing. Run scripts/Initialize-TerraformState.ps1 first.'
    }
    $Text = [IO.File]::ReadAllText($Path, [Text.UTF8Encoding]::new($false, $true))
    foreach ($Name in @('subscription_id', 'tenant_id')) {
        $Expected = if ($Name -ceq 'subscription_id') { $SubscriptionId } else { $TenantId }
        $Found = [regex]::Matches($Text, '(?m)^[ \t]*' + $Name + '[ \t]*=[ \t]*"([^"\r\n]*)"[ \t]*\r?$')
        if ($Found.Count -ne 1 -or $Found[0].Groups[1].Value -cne $Expected) {
            throw "The state backend file does not belong to the requested $Name."
        }
    }
    if (-not [regex]::IsMatch($Text, '(?m)^[ \t]*use_azuread_auth[ \t]*=[ \t]*true[ \t]*\r?$')) {
        throw 'The state backend must use Entra authentication (use_azuread_auth = true).'
    }
    if ([regex]::IsMatch($Text, '(?mi)^[ \t]*(access_key|sas_token|client_secret|client_certificate\w*|client_id|oidc\w*|use_oidc|use_msi|msi_endpoint)[ \t]*=')) {
        throw 'The state backend file must not carry credentials or an alternate identity.'
    }
}

# --- Operator identity and process hygiene ------------------------------------------------------------------------
Assert-AsreSubscription -Context @{ subscription_id = $SubscriptionId; tenant_id = $TenantId }

$AmbientIdentity = '\A(?:ARM_(?:CLIENT_\w+|OIDC_\w+|USE_OIDC|USE_MSI|MSI_ENDPOINT|MSI_API_VERSION|USE_AKS_WORKLOAD_IDENTITY|ADO_PIPELINE_SERVICE_CONNECTION_ID|ACCESS_KEY|SAS_TOKEN|USE_AZUREAD)|' +
    'AZURE_(?:CLIENT_\w+|FEDERATED_TOKEN_FILE|TENANT_ID|AUTHORITY_HOST|ADDITIONALLY_ALLOWED_TENANTS)|IDENTITY_ENDPOINT|IDENTITY_HEADER|MSI_ENDPOINT|MSI_SECRET|' +
    'TF_CLI_ARGS(?:_\w+)?|TF_WORKSPACE)\z'
foreach ($Name in @([Environment]::GetEnvironmentVariables().Keys | ForEach-Object { [string]$_ })) {
    if ([regex]::IsMatch($Name, $AmbientIdentity, [Text.RegularExpressions.RegexOptions]::IgnoreCase)) {
        # Remove-Item, not SetEnvironmentVariable($null): the latter leaves an empty-string variable in the process.
        Remove-Item -LiteralPath "Env:\$Name" -Force -ErrorAction SilentlyContinue
    }
}
$env:ARM_USE_CLI = 'true'
$env:ARM_SUBSCRIPTION_ID = $SubscriptionId
$env:ARM_TENANT_ID = $TenantId
$env:TF_VAR_subscription_id = $SubscriptionId
$env:TF_VAR_tenant_id = $TenantId
$env:TF_IN_AUTOMATION = 'true'
if ([string]::IsNullOrWhiteSpace($env:TF_PLUGIN_CACHE_DIR)) {
    $LocalData = [Environment]::GetFolderPath('LocalApplicationData')
    if ([string]::IsNullOrWhiteSpace($LocalData)) { throw 'The local application data folder is unavailable for the provider cache.' }
    $env:TF_PLUGIN_CACHE_DIR = [IO.Path]::Combine($LocalData, 'asre-terraform', 'plugin-cache')
}
[void][IO.Directory]::CreateDirectory($env:TF_PLUGIN_CACHE_DIR)

$VersionOutput = & terraform version -json
if ($LASTEXITCODE -ne 0) { throw "Terraform $TerraformVersion is required." }
try { $Version = (@($VersionOutput) -join "`n") | ConvertFrom-Json -DateKind String }
catch { throw "Terraform $TerraformVersion is required." }
if ((Get-OptionalProperty $Version 'terraform_version') -cne $TerraformVersion) { throw "Terraform $TerraformVersion is required." }

$ArtifactDirectory = [IO.Path]::GetFullPath($ArtifactDirectory)
$PlanPath = Join-Path $ArtifactDirectory 'demo.tfplan'
$ManifestPath = Join-Path $ArtifactDirectory 'manifest.json'

if ($Action -eq 'Plan') {
    $WhatIfPreference = $false
    $EnvironmentsRoot = [IO.Path]::GetFullPath((Join-Path $Root 'environments')) + [IO.Path]::DirectorySeparatorChar
    $VarPath = [IO.Path]::GetFullPath($VarFile, $Root)
    if (-not $VarPath.StartsWith($EnvironmentsRoot, [StringComparison]::OrdinalIgnoreCase) -or
        -not (Test-Path -LiteralPath $VarPath -PathType Leaf)) {
        throw 'VarFile must be an existing file under infra/environments/.'
    }
    $VarRelative = [IO.Path]::GetRelativePath($Root, $VarPath).Replace('\', '/')

    if (-not [regex]::IsMatch([string]$env:TF_VAR_admin_ssh_public_key, '\Assh-(ed25519|rsa) [A-Za-z0-9+/=]+( .*)?\z')) {
        throw 'Set the nonsecret TF_VAR_admin_ssh_public_key environment variable to the actual deployment public key.'
    }
    if (-not [regex]::IsMatch([string]$env:TF_VAR_runner_ssh_public_key, '\Assh-(ed25519|rsa) [A-Za-z0-9+/=]+( .*)?\z') -or
        $env:TF_VAR_runner_ssh_public_key -ceq $env:TF_VAR_admin_ssh_public_key) {
        throw 'Set TF_VAR_runner_ssh_public_key to a separate, nonadministrator public key.'
    }
    if (Test-Path -LiteralPath $ArtifactDirectory) {
        throw 'Use a new, empty artifact directory; existing saved plans are never overwritten.'
    }
    Assert-BackendFile -Path (Join-Path $Root $BackendConfig)

    $Commit = (@(& git -C $Repo rev-parse HEAD) -join '').Trim()
    if ($LASTEXITCODE -ne 0 -or -not [regex]::IsMatch($Commit, '\A[0-9a-f]{40}\z')) { throw 'Unable to identify the source commit.' }
    $Status = @(& git -C $Repo status --porcelain -- .)
    if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect the source tree state.' }
    $SourceDirty = -not [string]::IsNullOrWhiteSpace(($Status -join ''))
    if ($SourceDirty -and -not $AllowDirty) {
        throw 'The source tree has uncommitted changes. Commit them, or pass -AllowDirty to record source_dirty in the manifest.'
    }

    $null = New-Item -ItemType Directory -Path $ArtifactDirectory
    Invoke-TerraformChecked -Arguments @("-chdir=$Root", 'fmt', '-check', '-recursive')
    Invoke-TerraformChecked -Arguments @("-chdir=$Root", 'init', '-input=false', '-lockfile=readonly', "-backend-config=$BackendConfig")
    Invoke-TerraformChecked -Arguments @("-chdir=$Root", 'validate', '-no-color')
    Invoke-TerraformChecked -Arguments @("-chdir=$Root", 'state', 'list')
    Invoke-TerraformChecked -Arguments @("-chdir=$Root", 'plan', '-input=false', '-lock=false', "-var-file=$VarRelative", "-out=$PlanPath")
    $RawPlan = & terraform "-chdir=$Root" show -json $PlanPath
    if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect the saved plan.' }
    $Plan = (@($RawPlan) -join "`n") | ConvertFrom-Json -DateKind String
    $PlanVariables = Get-OptionalProperty $Plan 'variables'
    foreach ($Pair in @(@('subscription_id', $SubscriptionId), @('tenant_id', $TenantId))) {
        if ((Get-OptionalProperty (Get-OptionalProperty $PlanVariables $Pair[0]) 'value') -cne $Pair[1]) {
            throw "The saved plan was not planned for the requested $($Pair[0]); check the var file."
        }
    }
    $Changes = @(Get-OptionalProperty $Plan 'resource_changes' | Where-Object { $null -ne $_ })
    $Deleting = @($Changes | Where-Object { $_.change.actions -contains 'delete' })
    if ($Deleting.Count -gt 0) {
        $Names = (@($Deleting | Select-Object -First 10 | ForEach-Object { $_.address }) -join ', ')
        throw "The baseline rejects every deletion or replacement. No apply artifact will be published. Affected: $Names. The partial directory has no manifest and cannot be applied."
    }
    [ordered]@{
        subscription_id = $SubscriptionId
        tenant_id       = $TenantId
        source_commit   = $Commit
        destructive     = $false
        resources       = @($Changes | ForEach-Object {
            [ordered]@{
                address = $_.address
                type    = $_.type
                actions = $_.change.actions
                after   = $_.change.after | Select-Object name, resource_group_name, location, size,
                    address_space, public_network_access_enabled, disable_password_authentication,
                    evaluation_frequency, enabled
            }
        })
    } | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $ArtifactDirectory 'plan-summary.json') -Encoding utf8

    $Excluded = '\A(?:infra/environments/keys/|infra/environments/context\.generated\.json\z)|\.tfstate(?:\.[^/]*)?\z|\.tfplan\z'
    foreach ($Folder in @('infra', 'workloads', 'scripts', 'operations', 'contracts', 'agent', 'dashboard')) {
        $Source = Join-Path $Repo $Folder
        if (-not (Test-Path -LiteralPath $Source -PathType Container)) { throw "The source folder '$Folder' is missing." }
        $Destination = Join-Path $ArtifactDirectory $Folder
        $null = New-Item -ItemType Directory -Path $Destination
        Get-ChildItem -LiteralPath $Source -Recurse -File -Force | Where-Object {
            $RepoRelative = $Folder + '/' + [IO.Path]::GetRelativePath($Source, $_.FullName).Replace('\', '/')
            $_.FullName -notmatch '[\\/](\.terraform|__pycache__)[\\/]' -and
            -not [regex]::IsMatch($RepoRelative, $Excluded, [Text.RegularExpressions.RegexOptions]::IgnoreCase) -and
            ($_.Extension -in @('.tf', '.hcl', '.tfvars', '.py', '.sh', '.service', '.ps1', '.json', '.md', '.html') -or
                $_.Name -in @('Dockerfile', '.dockerignore', 'requirements.txt'))
        } | ForEach-Object {
            $Relative = [IO.Path]::GetRelativePath($Source, $_.FullName)
            $Target = Join-Path $Destination $Relative
            $null = New-Item -ItemType Directory -Force -Path (Split-Path $Target -Parent)
            Copy-Item -LiteralPath $_.FullName -Destination $Target
        }
    }
    Copy-Item -LiteralPath (Join-Path $Root '.terraform.lock.hcl') `
        -Destination (Join-Path (Join-Path $ArtifactDirectory 'infra') '.terraform.lock.hcl') -Force
    $Files = [ordered]@{}
    Get-ChildItem -LiteralPath $ArtifactDirectory -Recurse -File -Force | ForEach-Object {
        [pscustomobject]@{
            Relative = [IO.Path]::GetRelativePath($ArtifactDirectory, $_.FullName).Replace('\', '/')
            Path     = $_.FullName
        }
    } | Sort-Object -Property Relative -CaseSensitive | ForEach-Object {
        $Files[$_.Relative] = (Get-FileHash -LiteralPath $_.Path -Algorithm SHA256).Hash
    }
    [ordered]@{
        subscription_id = $SubscriptionId
        tenant_id       = $TenantId
        terraform       = $TerraformVersion
        source_commit   = $Commit
        source_dirty    = $SourceDirty
        created_at      = [DateTimeOffset]::UtcNow.ToString('o')
        files           = $Files
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $ManifestPath -Encoding utf8
}
else {
    if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) { throw 'The artifact directory has no manifest.json.' }
    try { $Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json -DateKind String }
    catch { throw 'The artifact manifest is unreadable.' }
    if ((Get-OptionalProperty $Manifest 'subscription_id') -cne $SubscriptionId -or
        (Get-OptionalProperty $Manifest 'tenant_id') -cne $TenantId -or
        (Get-OptionalProperty $Manifest 'terraform') -cne $TerraformVersion) {
        throw 'The saved plan does not match the requested subscription, tenant and Terraform version.'
    }
    $CreatedAt = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParseExact([string](Get-OptionalProperty $Manifest 'created_at'), 'o',
            [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::None, [ref]$CreatedAt)) {
        throw 'The artifact manifest has no valid creation time.'
    }
    $Age = [DateTimeOffset]::UtcNow - $CreatedAt
    if ($Age -gt [TimeSpan]::FromHours(4) -or $Age -lt [TimeSpan]::FromMinutes(-5)) {
        throw 'The saved plan has expired. Create a fresh plan; do not replan inside apply.'
    }
    if (-not [regex]::IsMatch([string](Get-OptionalProperty $Manifest 'source_commit'), '\A[0-9a-f]{40}\z')) {
        throw 'The artifact manifest has no valid source commit.'
    }
    $Files = Get-OptionalProperty $Manifest 'files'
    if ($null -eq $Files -or $null -eq $Files.PSObject.Properties['infra/.terraform.lock.hcl']) {
        throw 'The saved plan is missing its pinned provider lock file. Create a fresh plan.'
    }
    $Known = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($Entry in $Files.PSObject.Properties) {
        $Path = [IO.Path]::GetFullPath((Join-Path $ArtifactDirectory $Entry.Name))
        if (-not $Path.StartsWith($ArtifactDirectory + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Artifact manifest contains an out-of-bundle path.'
        }
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf) -or
            (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -cne $Entry.Value) {
            throw "Saved plan/source integrity check failed: $($Entry.Name)"
        }
        [void]$Known.Add($Entry.Name)
    }
    foreach ($Present in (Get-ChildItem -LiteralPath $ArtifactDirectory -Recurse -File -Force)) {
        $Relative = [IO.Path]::GetRelativePath($ArtifactDirectory, $Present.FullName).Replace('\', '/')
        if ($Relative -in @('manifest.json', 'deployment-outputs.json') -or $Relative.StartsWith('infra/.terraform/', [StringComparison]::Ordinal)) { continue }
        if (-not $Known.Contains($Relative)) { throw "Saved plan/source integrity check failed: unlisted file $Relative" }
    }
    $Dirty = Get-OptionalProperty $Manifest 'source_dirty'
    if ($Dirty -isnot [bool]) { throw 'The artifact manifest does not record the source state.' }
    if ($Dirty -and -not $AllowDirty) {
        throw 'The saved plan was made from an uncommitted source tree. Create a clean plan, or pass -AllowDirty.'
    }
    if ($null -eq $Files.PSObject.Properties['infra/environments/backend.hcl']) {
        throw 'The saved plan carries no state backend configuration. Create a fresh plan.'
    }
    $Root = Join-Path $ArtifactDirectory 'infra'
    Assert-BackendFile -Path (Join-Path $Root $BackendConfig)
    Invoke-TerraformChecked -Arguments @("-chdir=$Root", 'init', '-input=false', '-lockfile=readonly', "-backend-config=$BackendConfig")
    if ($PSCmdlet.ShouldProcess($ArtifactDirectory, 'terraform apply of the verified saved plan')) {
        Invoke-TerraformChecked -Arguments @("-chdir=$Root", 'apply', '-input=false', '-lock-timeout=60s', $PlanPath)
        $RawOutputs = & terraform "-chdir=$Root" output -json
        if ($LASTEXITCODE -ne 0) { throw 'Apply completed but deployment output collection failed.' }
        [IO.File]::WriteAllText((Join-Path $ArtifactDirectory 'deployment-outputs.json'),
            ((@($RawOutputs) -join "`n") + "`n"), [Text.UTF8Encoding]::new($false))
    }
}
