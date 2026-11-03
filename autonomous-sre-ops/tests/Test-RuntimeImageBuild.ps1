$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Repo = Split-Path $PSScriptRoot -Parent
$Directory = Join-Path ([IO.Path]::GetTempPath()) "asre-image-fixture-$([guid]::NewGuid().ToString('N'))"
$Plan = Join-Path $Directory 'plan'
$null = New-Item -ItemType Directory -Path (Join-Path $Plan 'operations') -Force
$global:FixtureImageExists = $false
$global:FixtureTagLocked = $false
$global:FixtureManifestLocked = $false
$global:FixtureIgnoreManifestLock = $false
$global:FixtureImageBuilds = 0
$global:FixtureAcrCalls = 0
$global:FixtureImageDigest = 'sha256:' + ('a' * 64)
$global:FixtureCommit = & git -C $Repo rev-parse HEAD
$global:FixtureRepository = 'asre-operations'
$OriginalSource = $env:BUILD_SOURCEVERSION
$OriginalTemplate = $env:TEMPLATE_SOURCE_COMMIT
$OriginalImageKind = $env:ASRE_RUNTIME_IMAGE_KIND

function global:az {
    $global:LASTEXITCODE = 0
    $global:FixtureAcrCalls++
    if ($args[0] -ne 'acr') { throw 'The fixture permits ACR commands only.' }
    if ($args[1] -eq 'build') {
        $global:FixtureImageBuilds++
        $global:FixtureImageExists = $true
        return 'Fixture only: no actual container was built.'
    }
    if ($args[1] -ne 'repository') { throw 'Unexpected fixture Azure operation.' }
    switch ($args[2]) {
        'list' { if ($global:FixtureImageExists) { return (ConvertTo-Json -InputObject @($global:FixtureRepository) -Compress) } else { return '[]' } }
        'show-tags' { return (@{name = $global:FixtureCommit} | ConvertTo-Json) }
        'update' {
            if (($args -join ' ') -notmatch '--write-enabled false --delete-enabled false') {
                throw 'The image fixture was not locked.'
            }
            $reference = $args[[Array]::IndexOf($args, '--image') + 1]
            if ($reference -eq "$($global:FixtureRepository):$global:FixtureCommit") {
                $global:FixtureTagLocked = $true
            }
            elseif ($reference -eq "$($global:FixtureRepository)@$global:FixtureImageDigest") {
                if (-not $global:FixtureIgnoreManifestLock) { $global:FixtureManifestLocked = $true }
            }
            else { throw 'Unexpected fixture lock reference.' }
            return '{}'
        }
        'show' {
            $reference = $args[[Array]::IndexOf($args, '--image') + 1]
            if ($reference -eq "$($global:FixtureRepository):$global:FixtureCommit") {
                $locked = $global:FixtureTagLocked
            }
            elseif ($reference -eq "$($global:FixtureRepository)@$global:FixtureImageDigest") {
                $locked = $global:FixtureManifestLocked
            }
            else { throw 'Unexpected fixture image reference.' }
            return (@{
                digest = $global:FixtureImageDigest
                changeableAttributes = @{
                    writeEnabled = -not $locked
                    deleteEnabled = -not $locked
                }
            } | ConvertTo-Json)
        }
        default { throw 'Unexpected fixture repository operation.' }
    }
}

function Assert-Test {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

try {
    $env:ASRE_RUNTIME_IMAGE_KIND = $null
    $env:idToken = 'unit-test-fixture-only'
    $env:servicePrincipalId = [guid]::NewGuid().ToString()
    $env:tenantId = '00000002-000a-4000-9000-000000000002'
    $env:BUILD_SOURCEVERSION = $global:FixtureCommit
    $env:TEMPLATE_SOURCE_COMMIT = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
    $files = @{}
    $context = Join-Path $Repo 'operations'
    Get-ChildItem -LiteralPath $context -Recurse -File -Force | Where-Object {
        $_.Extension -in @('.py', '.json') -or $_.Name -in @('requirements.txt', 'Dockerfile', '.dockerignore')
    } | ForEach-Object {
        $relative = [IO.Path]::GetRelativePath($context, $_.FullName)
        $destination = Join-Path (Join-Path $Plan 'operations') $relative
        $null = New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($destination)) -Force
        Copy-Item -LiteralPath $_.FullName -Destination $destination
        $files['operations/' + $relative.Replace('\', '/')] = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
    }
    $manifest = @{
        source_commit = $global:FixtureCommit
        template_commit = $env:TEMPLATE_SOURCE_COMMIT
        subscription_id = '00000001-000a-4000-a000-000000000001'
        files = $files
    }
    $path = Join-Path $Plan 'manifest.json'
    $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $path -Encoding utf8
    $script = Join-Path (Join-Path $Repo 'scripts') 'Build-RuntimeImages.ps1'
    & $script -ValidatedPlanDirectory $Plan -OutputDirectory (Join-Path $Directory 'build')
    Assert-Test ($global:FixtureImageBuilds -eq 1 -and $global:FixtureTagLocked -and $global:FixtureManifestLocked) 'The actual build and both independent locks were not selected.'
    $proof = Get-Content -LiteralPath (Join-Path (Join-Path $Directory 'build') 'image-provenance.json') -Raw | ConvertFrom-Json
    Assert-Test ($proof.digest -eq $global:FixtureImageDigest -and $proof.source_commit -eq $global:FixtureCommit) 'Provenance did not bind the actual digest to source.'
    Assert-Test ($proof.write_enabled -eq $false -and $proof.delete_enabled -eq $false -and
        $proof.tag_write_enabled -eq $false -and $proof.tag_delete_enabled -eq $false) 'Provenance did not record both verified lock surfaces.'

    & $script -ValidatedPlanDirectory $Plan -OutputDirectory (Join-Path $Directory 'reuse')
    Assert-Test ($global:FixtureImageBuilds -eq 1) 'An existing locked image was rebuilt.'
    $global:FixtureTagLocked = $false
    $rejected = $false
    try { & $script -ValidatedPlanDirectory $Plan -OutputDirectory (Join-Path $Directory 'unlocked') }
    catch {
        Assert-Test ($_.Exception.Message -match 'unlocked source-revision image') 'Unexpected unlocked-image failure.'
        $rejected = $true
    }
    Assert-Test $rejected 'An existing mutable image was accepted.'

    $global:FixtureTagLocked = $true
    $global:FixtureManifestLocked = $false
    $rejected = $false
    try { & $script -ValidatedPlanDirectory $Plan -OutputDirectory (Join-Path $Directory 'mutable-digest') }
    catch {
        Assert-Test ($_.Exception.Message -match 'image digest remains mutable') 'Unexpected mutable-digest failure.'
        $rejected = $true
    }
    Assert-Test $rejected 'A locked tag with a mutable digest was accepted.'

    $global:FixtureImageExists = $false
    $global:FixtureTagLocked = $false
    $global:FixtureIgnoreManifestLock = $true
    $rejected = $false
    $failedOutput = Join-Path $Directory 'failed-manifest-lock'
    try { & $script -ValidatedPlanDirectory $Plan -OutputDirectory $failedOutput }
    catch {
        Assert-Test ($_.Exception.Message -match 'image digest remains mutable') 'Unexpected unsuccessful manifest-lock failure.'
        $rejected = $true
    }
    Assert-Test ($rejected -and -not (Test-Path -LiteralPath $failedOutput)) 'An unverified manifest lock published success-shaped provenance.'

    $nested = 'operations/contracts/servicenow-action-audit.schema.json'
    Assert-Test ($manifest.files.ContainsKey($nested)) 'The fixture did not preserve the nested contract input.'
    $nestedHash = $manifest.files[$nested]
    $before = $global:FixtureAcrCalls
    $manifest.files.Remove($nested)
    $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $path -Encoding utf8
    $rejected = $false
    try { & $script -ValidatedPlanDirectory $Plan -OutputDirectory (Join-Path $Directory 'missing-contract') }
    catch {
        Assert-Test ($_.Exception.Message -match 'Build input is missing from') 'Unexpected missing-contract failure.'
        $rejected = $true
    }
    Assert-Test ($rejected -and $global:FixtureAcrCalls -eq $before) 'A missing nested contract reached ACR.'
    $manifest.files[$nested] = $nestedHash

    $nestedSnapshot = Join-Path (Join-Path (Join-Path $Plan 'operations') 'contracts') 'servicenow-action-audit.schema.json'
    '{}' | Set-Content -LiteralPath $nestedSnapshot -Encoding utf8
    $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $path -Encoding utf8
    $rejected = $false
    try { & $script -ValidatedPlanDirectory $Plan -OutputDirectory (Join-Path $Directory 'tampered-contract') }
    catch {
        Assert-Test ($_.Exception.Message -match 'validated plan source was modified') 'Unexpected nested-integrity failure.'
        $rejected = $true
    }
    Assert-Test ($rejected -and $global:FixtureAcrCalls -eq $before) 'A tampered nested contract reached ACR.'
    Copy-Item -LiteralPath (Join-Path (Join-Path $context 'contracts') 'servicenow-action-audit.schema.json') -Destination $nestedSnapshot

    $before = $global:FixtureAcrCalls
    $manifest.files['operations/cloud.py'] = '0' * 64
    $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $path -Encoding utf8
    $rejected = $false
    try { & $script -ValidatedPlanDirectory $Plan -OutputDirectory (Join-Path $Directory 'tampered') }
    catch {
        Assert-Test ($_.Exception.Message -match 'validated plan source was modified') 'Unexpected source-integrity failure.'
        $rejected = $true
    }
    Assert-Test ($rejected -and $global:FixtureAcrCalls -eq $before) 'Tampered source reached ACR.'

    $env:ASRE_RUNTIME_IMAGE_KIND = 'dashboard'
    $global:FixtureRepository = 'asre-dashboard'
    $global:FixtureImageExists = $false
    $global:FixtureTagLocked = $false
    $global:FixtureManifestLocked = $false
    $global:FixtureIgnoreManifestLock = $false
    $manifest.files = @{}
    $dashboard = Join-Path $Repo 'dashboard'
    Get-ChildItem -LiteralPath $dashboard -File -Force | Where-Object {
        $_.Extension -in @('.py', '.html') -or $_.Name -in @('requirements.txt', 'Dockerfile', '.dockerignore')
    } | ForEach-Object {
        $destination = Join-Path (Join-Path $Plan 'dashboard') $_.Name
        $null = New-Item -ItemType Directory -Path (Split-Path $destination -Parent) -Force
        Copy-Item -LiteralPath $_.FullName -Destination $destination
        $manifest.files['dashboard/' + $_.Name] = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
    }
    $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $path -Encoding utf8
    $buildsBefore = $global:FixtureImageBuilds
    & $script -ValidatedPlanDirectory $Plan -OutputDirectory (Join-Path $Directory 'dashboard-build')
    $dashboardProof = Get-Content -LiteralPath (Join-Path $Directory 'dashboard-build\\image-provenance.json') -Raw | ConvertFrom-Json
    Assert-Test ($global:FixtureImageBuilds -eq $buildsBefore + 1 -and
        $dashboardProof.repository -eq 'asre-dashboard' -and
        $dashboardProof.image -eq "acrasre8a327010f9.azurecr.io/asre-dashboard@$global:FixtureImageDigest") 'Dashboard publication selected the wrong repository or unpinned image.'
    $before = $global:FixtureAcrCalls
    $manifest.files.Remove('dashboard/evidence.html')
    $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $path -Encoding utf8
    $rejected = $false
    try { & $script -ValidatedPlanDirectory $Plan -OutputDirectory (Join-Path $Directory 'missing-dashboard-html') }
    catch {
        Assert-Test ($_.Exception.Message -match 'Build input is missing from') 'Unexpected missing-dashboard-input failure.'
        $rejected = $true
    }
    Assert-Test ($rejected -and $global:FixtureAcrCalls -eq $before) 'Unvalidated dashboard HTML reached ACR.'
    $env:ASRE_RUNTIME_IMAGE_KIND = '../operations'
    $rejected = $false
    try { & $script -ValidatedPlanDirectory $Plan -OutputDirectory (Join-Path $Directory 'invalid-image-kind') }
    catch {
        Assert-Test ($_.Exception.Message -match 'must be operations or dashboard') 'Unexpected invalid-image-kind failure.'
        $rejected = $true
    }
    Assert-Test ($rejected -and $global:FixtureAcrCalls -eq $before) 'An arbitrary image context reached ACR.'
    Write-Output 'Image delivery fixtures passed; no actual image or Azure resource was created.'
}
finally {
    $env:BUILD_SOURCEVERSION = $OriginalSource
    $env:TEMPLATE_SOURCE_COMMIT = $OriginalTemplate
    $env:ASRE_RUNTIME_IMAGE_KIND = $OriginalImageKind
    Remove-Item Function:\az
    Remove-Item -LiteralPath $Directory -Recurse -Force
}
