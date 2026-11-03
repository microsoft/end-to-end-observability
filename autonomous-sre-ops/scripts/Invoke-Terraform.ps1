[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Plan', 'Apply')][string]$Action,
    [Parameter(Mandatory)][string]$ArtifactDirectory
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Subscription = '00000001-000a-4000-a000-000000000001'
$Tenant = '00000002-000a-4000-9000-000000000002'

function Invoke-TerraformChecked {
    param([string[]]$Arguments)
    & terraform @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Terraform $($Arguments[1]) failed with exit code $LASTEXITCODE." }
}

if ([string]::IsNullOrWhiteSpace($env:idToken) -or
    [string]::IsNullOrWhiteSpace($env:servicePrincipalId) -or
    $env:tenantId -ne $Tenant) {
    throw 'Run through the approved AzureCLI@2 OIDC service connection in the confirmed tenant.'
}
$env:ARM_USE_OIDC = 'true'
$env:ARM_USE_AZUREAD = 'true'
$env:ARM_OIDC_TOKEN = $env:idToken
$env:ARM_CLIENT_ID = $env:servicePrincipalId
$env:ARM_TENANT_ID = $Tenant
$env:ARM_SUBSCRIPTION_ID = $Subscription
$env:TF_IN_AUTOMATION = 'true'
if ($env:TEMPLATE_SOURCE_COMMIT -and $env:TEMPLATE_SOURCE_COMMIT -notmatch '^[0-9a-fA-F]{40}$') {
    throw 'The shared template must resolve to a concrete Git commit.'
}

$Version = & terraform version -json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $Version.terraform_version -ne '1.11.2') {
    throw 'Terraform 1.11.2 is required.'
}
$ArtifactDirectory = [IO.Path]::GetFullPath($ArtifactDirectory)
$PlanPath = Join-Path $ArtifactDirectory 'demo.tfplan'
$ManifestPath = Join-Path $ArtifactDirectory 'manifest.json'

if ($Action -eq 'Plan') {
    $Repo = Split-Path $PSScriptRoot -Parent
    $Root = Join-Path $Repo 'infra'
    if ($env:TF_VAR_admin_ssh_public_key -notmatch '^ssh-(ed25519|rsa) [A-Za-z0-9+/=]+( .*)?$') {
        throw 'Set the nonsecret TF_VAR_admin_ssh_public_key pipeline variable to the actual deployment public key.'
    }
    if ($env:TF_VAR_runner_ssh_public_key -notmatch '^ssh-(ed25519|rsa) [A-Za-z0-9+/=]+( .*)?$' -or
        $env:TF_VAR_runner_ssh_public_key -eq $env:TF_VAR_admin_ssh_public_key) {
        throw 'Set TF_VAR_runner_ssh_public_key to a separate, nonadministrator public key.'
    }
    if (Test-Path $ArtifactDirectory) {
        throw 'Use a new, empty artifact directory; existing saved plans are never overwritten.'
    }
    $null = New-Item -ItemType Directory -Path $ArtifactDirectory
    Invoke-TerraformChecked -Arguments @("-chdir=$Root", 'fmt', '-check', '-recursive')
    Invoke-TerraformChecked -Arguments @("-chdir=$Root", 'init', '-input=false', '-lockfile=readonly')
    Invoke-TerraformChecked -Arguments @("-chdir=$Root", 'validate', '-no-color')
    Invoke-TerraformChecked -Arguments @("-chdir=$Root", 'state', 'list')
    $Variables = Join-Path 'environments' 'demo.tfvars'
    Invoke-TerraformChecked -Arguments @("-chdir=$Root", 'plan', '-input=false', '-lock=false', "-var-file=$Variables", "-out=$PlanPath")
    $RawPlan = & terraform "-chdir=$Root" show -json $PlanPath
    if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect the saved plan.' }
    $Plan = $RawPlan | ConvertFrom-Json
    $Changes = if ($Plan.PSObject.Properties['resource_changes']) { $Plan.resource_changes } else { @() }
    if ($Changes | Where-Object { $_.change.actions -contains 'delete' }) {
        throw 'The baseline pipeline rejects every deletion or replacement. No apply artifact will be published.'
    }
    $Commit = & git -C $Repo rev-parse HEAD
    if ($LASTEXITCODE -ne 0) { throw 'Unable to identify the source commit.' }
    @{
        subscription_id = $Subscription
        tenant_id       = $Tenant
        source_commit   = $Commit
        destructive     = $false
        resources       = @($Changes | ForEach-Object {
            @{
                address = $_.address
                type    = $_.type
                actions = $_.change.actions
                after   = $_.change.after | Select-Object name, resource_group_name, location, size,
                    address_space, public_network_access_enabled, disable_password_authentication,
                    evaluation_frequency, enabled
            }
        })
    } | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $ArtifactDirectory 'plan-summary.json') -Encoding utf8
    foreach ($Folder in @('infra', 'workloads', 'scripts', 'operations', 'contracts', 'agent', 'dashboard')) {
        $Source = Join-Path $Repo $Folder
        $Destination = Join-Path $ArtifactDirectory $Folder
        $null = New-Item -ItemType Directory -Path $Destination
        Get-ChildItem $Source -Recurse -File -Force | Where-Object {
            $_.FullName -notmatch '[\\/](\.terraform|__pycache__)[\\/]' -and
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
    $Files = @{}
    Get-ChildItem $ArtifactDirectory -Recurse -File -Force | ForEach-Object {
        $Relative = [IO.Path]::GetRelativePath($ArtifactDirectory, $_.FullName).Replace('\', '/')
        $Files[$Relative] = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
    }
    @{
        subscription_id = $Subscription
        tenant_id       = $Tenant
        terraform       = '1.11.2'
        source_commit   = $Commit
        template_commit = $env:TEMPLATE_SOURCE_COMMIT
        created_at      = [DateTimeOffset]::UtcNow.ToString('o')
        files           = $Files
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $ManifestPath -Encoding utf8
}
else {
    $Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    if ($Manifest.subscription_id -ne $Subscription -or $Manifest.tenant_id -ne $Tenant -or
        $Manifest.terraform -ne '1.11.2') {
        throw 'The saved plan does not match the approved deployment context.'
    }
    if ($env:BUILD_SOURCEVERSION -and $Manifest.source_commit -ne $env:BUILD_SOURCEVERSION) {
        throw 'The selected plan does not match this pipeline source commit.'
    }
    if ($env:TEMPLATE_SOURCE_COMMIT -and $Manifest.template_commit -ne $env:TEMPLATE_SOURCE_COMMIT) {
        throw 'The shared template changed after planning. Queue a fresh plan.'
    }
    if ([DateTimeOffset]::UtcNow - [DateTimeOffset]::Parse($Manifest.created_at) -gt [TimeSpan]::FromHours(4)) {
        throw 'The saved plan has expired. Queue a fresh plan; do not replan inside apply.'
    }
    if (-not $Manifest.files.PSObject.Properties['infra/.terraform.lock.hcl']) {
        throw 'The saved plan is missing its pinned provider lock file. Queue a fresh plan.'
    }
    foreach ($Entry in $Manifest.files.PSObject.Properties) {
        $Path = [IO.Path]::GetFullPath((Join-Path $ArtifactDirectory $Entry.Name))
        if (-not $Path.StartsWith($ArtifactDirectory + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Artifact manifest contains an out-of-bundle path.'
        }
        if ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ne $Entry.Value) {
            throw "Saved plan/source integrity check failed: $($Entry.Name)"
        }
    }
    $Root = Join-Path $ArtifactDirectory 'infra'
    Invoke-TerraformChecked -Arguments @("-chdir=$Root", 'init', '-input=false', '-lockfile=readonly')
    Invoke-TerraformChecked -Arguments @("-chdir=$Root", 'apply', '-input=false', '-lock-timeout=60s', $PlanPath)
    & terraform "-chdir=$Root" output -json | Set-Content -LiteralPath (Join-Path $ArtifactDirectory 'deployment-outputs.json') -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw 'Apply completed but deployment output collection failed.' }
}
