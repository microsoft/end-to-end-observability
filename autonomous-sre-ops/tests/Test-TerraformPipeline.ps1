$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Repo = Split-Path $PSScriptRoot -Parent
$TestDirectory = Join-Path ([IO.Path]::GetTempPath()) "asre-pipeline-test-$([guid]::NewGuid().ToString('N'))"
$Artifact = Join-Path $TestDirectory 'plan'
$null = New-Item -ItemType Directory -Path $TestDirectory
$global:TerraformCalls = [Collections.Generic.List[object]]::new()
$global:PlanDeletes = $false
$OriginalTemplateSourceCommit = $env:TEMPLATE_SOURCE_COMMIT

function global:terraform {
    $Arguments = @($args)
    $global:TerraformCalls.Add($Arguments)
    $global:LASTEXITCODE = 0
    if ($Arguments[0] -eq 'version') {
        return '{"terraform_version":"1.11.2"}'
    }
    if ($Arguments -contains 'plan') {
        $Out = @($Arguments | Where-Object { $_.StartsWith('-out=') })[0].Substring(5)
        [IO.File]::WriteAllText($Out, 'UNIT TEST FIXTURE ONLY; NOT A TERRAFORM PLAN')
    }
    if ($Arguments -contains 'show') {
        if ($global:PlanDeletes) {
            return '{"resource_changes":[{"change":{"actions":["delete","create"]}}]}'
        }
        return '{"resource_changes":[{"address":"azurerm_resource_group.fixture","type":"azurerm_resource_group","change":{"actions":["create"],"after":{"name":"fixture-only"}}}]}'
    }
    if ($Arguments -contains 'output') {
        return '{"fixture_only":true}'
    }
}

function Assert-Test {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

try {
    $env:TEMPLATE_SOURCE_COMMIT = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
    $env:idToken = 'unit-test-fixture-only'
    $env:servicePrincipalId = [guid]::NewGuid().ToString()
    $env:tenantId = '00000002-000a-4000-9000-000000000002'
    $env:TF_VAR_admin_ssh_public_key = 'ssh-ed25519 Zm9yLXVuaXQtdGVzdHMtb25seQ=='
    $env:TF_VAR_runner_ssh_public_key = 'ssh-ed25519 ZGlzdGluY3QtdW5pdC10ZXN0LWZpeHR1cmU='
    $Script = Join-Path (Join-Path $Repo 'scripts') 'Invoke-Terraform.ps1'

    & $Script -Action Plan -ArtifactDirectory $Artifact
    Assert-Test (Test-Path (Join-Path $Artifact 'manifest.json')) 'Plan did not produce its source-integrity manifest.'
    $LockFile = Join-Path (Join-Path $Artifact 'infra') '.terraform.lock.hcl'
    Assert-Test (Test-Path -LiteralPath $LockFile) 'The hidden provider lock file was omitted from the saved source.'
    $Manifest = Get-Content -LiteralPath (Join-Path $Artifact 'manifest.json') -Raw | ConvertFrom-Json
    Assert-Test ($Manifest.files.'infra/.terraform.lock.hcl' -eq (Get-FileHash -LiteralPath $LockFile -Algorithm SHA256).Hash) 'The hidden provider lock file was not included in artifact integrity checks.'
    foreach ($Name in @('Dockerfile', '.dockerignore', 'requirements.txt')) {
        $File = Join-Path (Join-Path $Artifact 'operations') $Name
        Assert-Test (Test-Path -LiteralPath $File) "Runtime build input was omitted: $Name"
        Assert-Test ($Manifest.files.("operations/$Name") -eq (Get-FileHash -LiteralPath $File -Algorithm SHA256).Hash) "Runtime build input was not integrity checked: $Name"
    }
    foreach ($Name in @('server.py', 'index.html', 'evidence.html', 'Dockerfile', '.dockerignore', 'requirements.txt')) {
        $File = Join-Path (Join-Path $Artifact 'dashboard') $Name
        Assert-Test (Test-Path -LiteralPath $File) "Dashboard build input was omitted: $Name"
        Assert-Test ($Manifest.files.("dashboard/$Name") -eq (Get-FileHash -LiteralPath $File -Algorithm SHA256).Hash) "Dashboard build input was not integrity checked: $Name"
    }
    $Summary = Get-Content -LiteralPath (Join-Path $Artifact 'plan-summary.json') -Raw | ConvertFrom-Json
    Assert-Test ($Summary.resources.Count -eq 1 -and -not $Summary.destructive) 'Plan did not publish its restricted change summary.'
    Assert-Test (@($global:TerraformCalls | Where-Object { $_ -contains 'apply' }).Count -eq 0) 'Plan path called apply.'
    $PlanCalls = @($global:TerraformCalls | Where-Object { $_ -contains 'plan' })
    Assert-Test ($PlanCalls.Count -eq 1 -and $PlanCalls[0] -contains '-input=false') 'Terraform arguments were not preserved.'
    Assert-Test ($PlanCalls[0] -contains '-lock=false') 'Read-only plan identity attempted a state lease.'

    $global:TerraformCalls.Clear()
    & $Script -Action Apply -ArtifactDirectory $Artifact
    Assert-Test (@($global:TerraformCalls | Where-Object { $_ -contains 'plan' }).Count -eq 0) 'Apply replanned instead of using its saved artifact.'
    Assert-Test (@($global:TerraformCalls | Where-Object { $_ -contains 'apply' }).Count -eq 1) 'Explicit apply did not use the saved artifact.'
    $ApplyCalls = @($global:TerraformCalls | Where-Object { $_ -contains 'apply' })
    Assert-Test ($ApplyCalls[0] -contains '-lock-timeout=60s' -and $ApplyCalls[0] -notcontains '-lock=false') 'Apply must acquire the backend lease.'

    $ManifestPath = Join-Path $Artifact 'manifest.json'
    $OriginalManifest = Get-Content -LiteralPath $ManifestPath -Raw
    $global:TerraformCalls.Clear()
    $Rejected = $false
    try {
        $Manifest.files.PSObject.Properties.Remove('infra/.terraform.lock.hcl')
        $Manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $ManifestPath -Encoding utf8
        & $Script -Action Apply -ArtifactDirectory $Artifact
    }
    catch {
        Assert-Test ($_.Exception.Message -match 'missing its pinned provider lock file') 'Unexpected failure instead of missing-lock rejection.'
        $Rejected = $true
    }
    finally {
        Set-Content -LiteralPath $ManifestPath -Value $OriginalManifest -Encoding utf8
    }
    Assert-Test $Rejected 'An artifact without a hashed provider lock file was accepted.'
    Assert-Test (@($global:TerraformCalls | Where-Object { $_ -contains 'init' -or $_ -contains 'apply' }).Count -eq 0) 'An incomplete artifact reached Terraform initialization or apply.'

    $Rejected = $false
    try {
        $env:TEMPLATE_SOURCE_COMMIT = '$(resources.repositories.templates.version)'
        & $Script -Action Apply -ArtifactDirectory $Artifact
    }
    catch {
        Assert-Test ($_.Exception.Message -match 'must resolve to a concrete Git commit') 'Unexpected failure instead of unresolved-metadata rejection.'
        $Rejected = $true
    }
    finally {
        $env:TEMPLATE_SOURCE_COMMIT = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
    }
    Assert-Test $Rejected 'Unresolved shared-template metadata was accepted.'

    $OriginalSourceVersion = $env:BUILD_SOURCEVERSION
    $global:TerraformCalls.Clear()
    $Rejected = $false
    try {
        $env:BUILD_SOURCEVERSION = '0000000000000000000000000000000000000000'
        & $Script -Action Apply -ArtifactDirectory $Artifact
    }
    catch {
        Assert-Test ($_.Exception.Message -match 'does not match this pipeline source commit') 'Unexpected failure instead of source-commit rejection.'
        $Rejected = $true
    }
    finally {
        $env:BUILD_SOURCEVERSION = $OriginalSourceVersion
    }
    Assert-Test $Rejected 'A saved plan from another commit was accepted.'
    Assert-Test (@($global:TerraformCalls | Where-Object { $_ -contains 'apply' }).Count -eq 0) 'Commit mismatch reached apply.'

    $global:TerraformCalls.Clear()
    $Rejected = $false
    try {
        $env:TEMPLATE_SOURCE_COMMIT = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
        & $Script -Action Apply -ArtifactDirectory $Artifact
    }
    catch {
        Assert-Test ($_.Exception.Message -match 'shared template changed after planning') 'Unexpected failure instead of shared-template rejection.'
        $Rejected = $true
    }
    finally {
        $env:TEMPLATE_SOURCE_COMMIT = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
    }
    Assert-Test $Rejected 'A saved plan was accepted after the shared template changed.'
    Assert-Test (@($global:TerraformCalls | Where-Object { $_ -contains 'apply' }).Count -eq 0) 'Template mismatch reached apply.'

    Add-Content -LiteralPath (Join-Path (Join-Path $Artifact 'infra') 'main.tf') -Value '# modified fixture'
    $Rejected = $false
    try { & $Script -Action Apply -ArtifactDirectory $Artifact }
    catch {
        Assert-Test ($_.Exception.Message -match 'integrity check failed') 'Unexpected failure instead of integrity rejection.'
        $Rejected = $true
    }
    Assert-Test $Rejected 'Tampered source bundle was accepted.'

    $global:PlanDeletes = $true
    $Rejected = $false
    try { & $Script -Action Plan -ArtifactDirectory (Join-Path $TestDirectory 'destructive') }
    catch {
        Assert-Test ($_.Exception.Message -match 'rejects every deletion') 'Unexpected failure instead of deletion rejection.'
        $Rejected = $true
    }
    Assert-Test $Rejected 'A destructive/replacement plan was accepted.'
    Write-Output 'PowerShell pipeline behavior checks passed using a fixture-only Terraform command.'
}
finally {
    $env:TEMPLATE_SOURCE_COMMIT = $OriginalTemplateSourceCommit
    Remove-Item Function:\terraform
    Remove-Item -LiteralPath $TestDirectory -Recurse -Force
}
