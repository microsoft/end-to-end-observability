[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ValidatedPlanDirectory,
    [Parameter(Mandatory)][string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Subscription = '00000001-000a-4000-a000-000000000001'
$Registry = 'acrasre8a327010f9'
$ImageKind = if ($null -eq $env:ASRE_RUNTIME_IMAGE_KIND) { 'operations' } else { $env:ASRE_RUNTIME_IMAGE_KIND }
if ($ImageKind -notin @('operations', 'dashboard')) {
    throw 'ASRE_RUNTIME_IMAGE_KIND must be operations or dashboard.'
}
$Repository = "asre-$ImageKind"
$Repo = Split-Path $PSScriptRoot -Parent

function Invoke-AzJson {
    param([string[]]$Arguments)
    $result = & az @Arguments --subscription $Subscription --only-show-errors --output json
    if ($LASTEXITCODE -ne 0) { throw "An authenticated ACR operation failed: $($Arguments[0..2] -join ' ')." }
    return $result | ConvertFrom-Json
}

if ([string]::IsNullOrWhiteSpace($env:idToken) -or
    [string]::IsNullOrWhiteSpace($env:servicePrincipalId) -or
    $env:tenantId -ne '00000002-000a-4000-9000-000000000002') {
    throw 'Build only through the protected OIDC Apply service connection.'
}
$Commit = & git -C $Repo rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $Commit -notmatch '^[0-9a-f]{40}$' -or $Commit -ne $env:BUILD_SOURCEVERSION) {
    throw 'Image build requires the exact pipeline Git source commit.'
}
$Manifest = Get-Content -LiteralPath (Join-Path $ValidatedPlanDirectory 'manifest.json') -Raw | ConvertFrom-Json
if ($Manifest.source_commit -ne $Commit -or $Manifest.template_commit -ne $env:TEMPLATE_SOURCE_COMMIT -or
    $Manifest.subscription_id -ne $Subscription) {
    throw 'The validated source proof does not match this build, template or subscription.'
}
foreach ($Entry in $Manifest.files.PSObject.Properties) {
    $path = [IO.Path]::GetFullPath((Join-Path $ValidatedPlanDirectory $Entry.Name))
    if (-not $path.StartsWith([IO.Path]::GetFullPath($ValidatedPlanDirectory) + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
        (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $Entry.Value) {
        throw "The validated plan source was modified: $($Entry.Name)"
    }
}
$Context = Join-Path $Repo $ImageKind
$InputExtensions = if ($ImageKind -eq 'dashboard') { @('.py', '.html') } else { @('.py', '.json') }
$Inputs = @(Get-ChildItem -LiteralPath $Context -Recurse -File -Force | Where-Object {
    $_.FullName -notmatch '[\\/]__pycache__[\\/]' -and
    ($_.Extension -in $InputExtensions -or $_.Name -in @('requirements.txt', 'Dockerfile', '.dockerignore'))
})
foreach ($InputFile in $Inputs) {
    $name = $ImageKind + '/' + [IO.Path]::GetRelativePath($Context, $InputFile.FullName).Replace('\', '/')
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
    & az acr build --registry $Registry --subscription $Subscription --image "${Repository}:$Commit" `
        --platform linux/amd64 --build-arg "SOURCE_COMMIT=$Commit" --only-show-errors $Context
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
    template_commit = $env:TEMPLATE_SOURCE_COMMIT
    built_at = [DateTimeOffset]::UtcNow.ToString('o')
    registry = "$Registry.azurecr.io"
    repository = $Repository
    digest = $image.digest
    image = "$Registry.azurecr.io/$Repository@$($image.digest)"
    write_enabled = $false
    delete_enabled = $false
    tag_write_enabled = $false
    tag_delete_enabled = $false
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $OutputDirectory 'image-provenance.json') -Encoding utf8
