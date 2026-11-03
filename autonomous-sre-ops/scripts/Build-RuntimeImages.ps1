#Requires -Version 7.5
<#
.SYNOPSIS
Builds one runtime image (operations or dashboard) in the deployment's Azure Container Registry and locks it by digest.

.DESCRIPTION
The registry comes from the deployment context. The build refuses to run unless the validated plan manifest proves the
exact source: same subscription/tenant, same Git commit as HEAD, clean source, and every build input matching its
recorded SHA-256. The resulting tag and digest are locked (write and delete disabled) and image-provenance.json is
written to a new output directory. Select the image with ASRE_RUNTIME_IMAGE_KIND (operations|dashboard).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateScript({ [regex]::IsMatch($_, '\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\z') },
        ErrorMessage = 'SubscriptionId must be a lowercase GUID.')][string]$SubscriptionId,
    [Parameter(Mandatory)]
    [ValidateScript({ [regex]::IsMatch($_, '\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\z') },
        ErrorMessage = 'TenantId must be a lowercase GUID.')][string]$TenantId,
    [Parameter(Mandatory)][string]$ValidatedPlanDirectory,
    [Parameter(Mandatory)][string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'Context.Common.ps1')
$ImageKind = if ($null -eq $env:ASRE_RUNTIME_IMAGE_KIND) { 'operations' } else { $env:ASRE_RUNTIME_IMAGE_KIND }
if ($ImageKind -cnotin @('operations', 'dashboard')) {
    throw 'ASRE_RUNTIME_IMAGE_KIND must be operations or dashboard.'
}
$Repository = "asre-$ImageKind"
$Repo = Split-Path $PSScriptRoot -Parent

# The registry is never a literal: it is the deployment context of the requested subscription.
$AsreContext = Get-AsreContext
if ($AsreContext.subscription_id -cne $SubscriptionId -or $AsreContext.tenant_id -cne $TenantId) {
    throw 'The deployment context does not belong to the requested subscription and tenant.'
}
Assert-AsreSubscription -Context $AsreContext
$Registry = $AsreContext.registry.name
$LoginServer = $AsreContext.registry.login_server

function Invoke-AzJson {
    param([string[]]$Arguments)
    $result = & az @Arguments --subscription $SubscriptionId --only-show-errors --output json
    if ($LASTEXITCODE -ne 0) { throw "An authenticated ACR operation failed: $($Arguments[0..2] -join ' ')." }
    return $result | ConvertFrom-Json
}

$Commit = (@(& git -C $Repo rev-parse HEAD) -join '').Trim()
if ($LASTEXITCODE -ne 0 -or $Commit -notmatch '^[0-9a-f]{40}$') {
    throw 'Image build requires the exact Git source commit of the working tree.'
}
$ManifestPath = Join-Path $ValidatedPlanDirectory 'manifest.json'
if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) { throw 'The validated plan directory has no manifest.json.' }
$Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json -DateKind String
foreach ($Name in @('source_commit', 'subscription_id', 'tenant_id', 'source_dirty', 'files')) {
    if (-not $Manifest.PSObject.Properties[$Name]) { throw 'The validated source proof is incomplete.' }
}
if ($Manifest.source_commit -ne $Commit -or
    $Manifest.subscription_id -cne $SubscriptionId -or $Manifest.tenant_id -cne $TenantId) {
    throw 'The validated source proof does not match this build, subscription or tenant.'
}
if ($Manifest.source_dirty -isnot [bool] -or $Manifest.source_dirty) {
    throw 'The validated plan was made from an uncommitted source tree; an exact-source image cannot be built from it.'
}
foreach ($Entry in $Manifest.files.PSObject.Properties) {
    $path = [IO.Path]::GetFullPath((Join-Path $ValidatedPlanDirectory $Entry.Name))
    if (-not $path.StartsWith([IO.Path]::GetFullPath($ValidatedPlanDirectory) + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
        -not (Test-Path -LiteralPath $path -PathType Leaf) -or
        (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $Entry.Value) {
        throw "The validated plan source was modified: $($Entry.Name)"
    }
}
$BuildContext = Join-Path $Repo $ImageKind
$InputExtensions = if ($ImageKind -eq 'dashboard') { @('.py', '.html') } else { @('.py', '.json') }
$Inputs = @(Get-ChildItem -LiteralPath $BuildContext -Recurse -File -Force | Where-Object {
    $_.FullName -notmatch '[\\/]__pycache__[\\/]' -and
    ($_.Extension -in $InputExtensions -or $_.Name -in @('requirements.txt', 'Dockerfile', '.dockerignore'))
})
foreach ($InputFile in $Inputs) {
    $name = $ImageKind + '/' + [IO.Path]::GetRelativePath($BuildContext, $InputFile.FullName).Replace('\', '/')
    if (-not $Manifest.files.PSObject.Properties[$name] -or
        (Get-FileHash -LiteralPath $InputFile.FullName -Algorithm SHA256).Hash -ne $Manifest.files.$name) {
        throw "Build input is missing from, or differs from, its validated source proof: $name"
    }
}
$repositories = @(Invoke-AzJson -Arguments @('acr', 'repository', 'list', '--name', $Registry))
$existing = $null
if ($repositories -contains $Repository) {
    $tags = @(Invoke-AzJson -Arguments @('acr', 'repository', 'show-tags', '--name', $Registry, '--repository', $Repository, '--detail'))
    $existing = $tags | Where-Object name -EQ $Commit | Select-Object -First 1
}
if ($existing) {
    $image = Invoke-AzJson -Arguments @('acr', 'repository', 'show', '--name', $Registry, '--image', "${Repository}:$Commit")
    if ($image.changeableAttributes.writeEnabled -or $image.changeableAttributes.deleteEnabled) {
        throw 'An unlocked source-revision image already exists; refuse to overwrite or silently trust it.'
    }
    Write-Output 'Reusing the existing locked exact-source image.'
}
else {
    & az acr build --registry $Registry --subscription $SubscriptionId --image "${Repository}:$Commit" `
        --platform linux/amd64 --build-arg "SOURCE_COMMIT=$Commit" --only-show-errors $BuildContext
    if ($LASTEXITCODE -ne 0) { throw 'The actual ACR source build failed; no image provenance will be published.' }
    $null = Invoke-AzJson -Arguments @(
        'acr', 'repository', 'update', '--name', $Registry, '--image', "${Repository}:$Commit",
        '--write-enabled', 'false', '--delete-enabled', 'false'
    )
    $image = Invoke-AzJson -Arguments @('acr', 'repository', 'show', '--name', $Registry, '--image', "${Repository}:$Commit")
    if ($image.digest -notmatch '^sha256:[0-9a-f]{64}$') {
        throw 'The source tag did not resolve to an actual image digest.'
    }
    $null = Invoke-AzJson -Arguments @(
        'acr', 'repository', 'update', '--name', $Registry, '--image', "${Repository}@$($image.digest)",
        '--write-enabled', 'false', '--delete-enabled', 'false'
    )
}
if ($image.digest -notmatch '^sha256:[0-9a-f]{64}$' -or
    $image.changeableAttributes.writeEnabled -ne $false -or $image.changeableAttributes.deleteEnabled -ne $false) {
    throw 'The actual source image is missing a digest or remains mutable.'
}
$manifestImage = Invoke-AzJson -Arguments @(
    'acr', 'repository', 'show', '--name', $Registry, '--image', "${Repository}@$($image.digest)"
)
if ($manifestImage.digest -ne $image.digest -or
    $manifestImage.changeableAttributes.writeEnabled -ne $false -or
    $manifestImage.changeableAttributes.deleteEnabled -ne $false) {
    throw 'The actual source image digest remains mutable or differs from its locked tag.'
}
$null = New-Item -ItemType Directory -Path $OutputDirectory
@{
    source_commit = $Commit
    built_at = [DateTimeOffset]::UtcNow.ToString('o')
    registry = $LoginServer
    repository = $Repository
    digest = $image.digest
    image = "$LoginServer/$Repository@$($image.digest)"
    write_enabled = $false
    delete_enabled = $false
    tag_write_enabled = $false
    tag_delete_enabled = $false
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $OutputDirectory 'image-provenance.json') -Encoding utf8
