#requires -Version 7.5
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Validate', 'Verify', 'PublishDisabled')][string]$Action,
    [Parameter(Mandatory)][string]$ArtifactDirectory,
    [Parameter(Mandatory)][string]$TemplateDirectory,
    [string]$SourceDirectory = (Split-Path $PSScriptRoot -Parent)
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
# This is a configuration receipt, NEVER a Terraform plan or activation authority.
$Profile = 'asre-configuration'
$Subscription = '00000001-000a-4000-a000-000000000001'
$Tenant = '00000002-000a-4000-9000-000000000002'
$Agent = "/subscriptions/$Subscription/resourceGroups/rg-asre-control-cus/providers/Microsoft.App/agents/asre-demo-cus"
$Endpoint = 'https://asre-demo-cus--0a1b2c3d.4e5f6a7b.centralus.azuresre.ai'
$SourceFiles = @(
    'azure-pipelines.yml', 'scripts/Invoke-IncidentRelease.ps1',
    'config/native-incident-runtime.json',
    'scripts/Publish-IncidentWorkflow.ps1', 'scripts/ReviewAgent.Common.ps1',
    'scripts/Initialize-ReviewAgent.ps1', 'scripts/Build-CaseTool.ps1',
    'scripts/Initialize-OperationsConnector.ps1',
    'agent/workflows/azure-monitor-review.json', 'agent/instructions/azure-monitor-review.md',
    'agent/hooks/monitor-case-policy.py', 'agent/tools/servicenow_case.json',
    'agent/tools/servicenow_case.py', 'agent/tools/review_evidence.py',
    'agent/knowledge/operating-boundaries.md',
    'agent/knowledge/index.json', 'agent/knowledge/asre-environment-v1.md',
    'agent/knowledge/asre-investigation-v1.md', 'agent/knowledge/asre-servicenow-review-v1.md',
    'agent/knowledge/asre-live-report-sources-v1.md',
    'agent/skills/linux-rca/SKILL.md', 'agent/skills/proactive-analysis/SKILL.md',
    'agent/skills/patch-backup-recovery/SKILL.md', 'agent/skills/case-and-verification/SKILL.md',
    'contracts/servicenow-case.schema.json',
    'operations/agent_case.py', 'operations/servicenow.py',
    'operations/patch_provider.py', 'operations/patch_sensor.py',
    'operations/worker.py', 'operations/feedback.py', 'infra/monitoring.tf',
    'operations/contracts/servicenow-case.schema.json',
    'operations/contracts/servicenow-case-write.schema.json',
    'operations/contracts/servicenow-note-write.schema.json',
    'operations/contracts/servicenow-feedback-ack.schema.json',
    'operations/contracts/servicenow-action-audit.schema.json',
    'operations/contracts/servicenow-adapter-response.schema.json'
)
$TemplateFiles = @(
    '.pipelines/cd-template.yaml', '.pipelines/asre-configuration-stages.yaml',
    '.pipelines/helpers/asre-runtime.yaml'
)

function Assert-RegularPath {
    param([string]$Root, [string]$Relative)
    if ($Relative -cnotmatch '^[a-zA-Z0-9_.-]+(/[a-zA-Z0-9_.-]+)*$' -or
        @($Relative.Split('/') | Where-Object { $_ -in @('.', '..') }).Count) {
        throw 'Invalid configuration artifact path.'
    }
    $path = $Root
    foreach ($part in $Relative.Split('/')) {
        $path = Join-Path $path $part
        $item = Get-Item -LiteralPath $path -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Configuration links are not allowed.' }
    }
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'Configuration file is missing.' }
    return $path
}

function Assert-Checkout {
    param([string]$Root, [string]$Commit, [string[]]$Files)
    $head = & git -C $Root rev-parse HEAD
    if ($LASTEXITCODE -ne 0 -or $head -cne $Commit) { throw 'Configuration checkout commit mismatch.' }
    $null = & git -C $Root ls-files --error-unmatch -- @Files
    if ($LASTEXITCODE -ne 0) { throw 'Configuration inputs must be versioned.' }
    & git -C $Root diff --quiet HEAD -- @Files
    if ($LASTEXITCODE -ne 0) { throw 'Configuration checkout has modified inputs.' }
    foreach ($name in $Files) { $null = Assert-RegularPath $Root $name }
}

function Get-TextHash {
    param([string]$Text)
    return [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.UTF8Encoding]::new($false).GetBytes($Text))).ToLowerInvariant()
}

# All of this runs before any builder, publication helper or cloud operation.
if ($env:BUILD_SOURCEBRANCH -cne 'refs/heads/master' -or $env:BUILD_REASON -cne 'Manual' -or
    $env:BUILD_REPOSITORY_PROVIDER -cne 'TfsGit' -or
    $env:BUILD_SOURCEVERSION -cnotmatch '^[0-9a-f]{40}$' -or
    $env:TEMPLATE_SOURCE_COMMIT -cnotmatch '^[0-9a-f]{40}$') {
    throw 'Configuration delivery requires a manual trusted master source and concrete source/template commits.'
}
foreach ($name in @('SYSTEM_COLLECTIONURI', 'SYSTEM_TEAMPROJECTID', 'SYSTEM_DEFINITIONID',
        'BUILD_REPOSITORY_ID', 'BUILD_BUILDID')) {
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))) { throw "Missing pipeline context: $name" }
}
if ($env:SYSTEM_COLLECTIONURI -cnotmatch '^https://dev\.azure\.com/[^/]+/$' -or
    $env:SYSTEM_TEAMPROJECTID -notmatch '^[0-9a-fA-F-]{36}$' -or
    $env:BUILD_REPOSITORY_ID -notmatch '^[0-9a-fA-F-]{36}$' -or
    $env:SYSTEM_DEFINITIONID -notmatch '^[1-9][0-9]*$' -or $env:BUILD_BUILDID -notmatch '^[1-9][0-9]*$') {
    throw 'Invalid pipeline context.'
}
$SourceDirectory = [IO.Path]::GetFullPath($SourceDirectory)
$TemplateDirectory = [IO.Path]::GetFullPath($TemplateDirectory)
$ArtifactDirectory = [IO.Path]::GetFullPath($ArtifactDirectory)
foreach ($root in @($SourceDirectory, $TemplateDirectory)) {
    if ((Get-Item -LiteralPath $root -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Checkout links are not allowed.' }
}
Assert-Checkout $SourceDirectory $env:BUILD_SOURCEVERSION $SourceFiles
Assert-Checkout $TemplateDirectory $env:TEMPLATE_SOURCE_COMMIT $TemplateFiles
$context = [ordered]@{
    artifact_kind = 'asre-configuration/v1'; profile = $Profile
    collection_uri = $env:SYSTEM_COLLECTIONURI; project_id = $env:SYSTEM_TEAMPROJECTID
    definition_id = $env:SYSTEM_DEFINITIONID; repository_id = $env:BUILD_REPOSITORY_ID
    source_branch = 'refs/heads/master'; source_commit = $env:BUILD_SOURCEVERSION
    template_repository = 'alz-landingzone/alz-mgmt-templates'; template_commit = $env:TEMPLATE_SOURCE_COMMIT
    subscription_id = $Subscription; tenant_id = $Tenant; agent_resource_id = $Agent
    agent_endpoint = $Endpoint; publication_action = 'PublishDisabled'; runtime_accepted = $false
}
$expected = @{}
foreach ($name in $SourceFiles) {
    $expected["source/$name"] = (Get-FileHash -LiteralPath (Assert-RegularPath $SourceDirectory $name) -Algorithm SHA256).Hash.ToLowerInvariant()
}
foreach ($name in $TemplateFiles) {
    $expected["templates/$name"] = (Get-FileHash -LiteralPath (Assert-RegularPath $TemplateDirectory $name) -Algorithm SHA256).Hash.ToLowerInvariant()
}
$manifestPath = Join-Path $ArtifactDirectory 'configuration-manifest.json'
if ($Action -eq 'Validate') {
    if (Test-Path -LiteralPath $ArtifactDirectory) { throw 'Use a new configuration artifact directory.' }
    $null = New-Item -ItemType Directory -Path $ArtifactDirectory
    foreach ($name in $SourceFiles) {
        $target = Join-Path $ArtifactDirectory "source/$name"
        $null = New-Item -ItemType Directory -Path (Split-Path $target -Parent) -Force
        Copy-Item -LiteralPath (Join-Path $SourceDirectory $name) -Destination $target
    }
    foreach ($name in $TemplateFiles) {
        $target = Join-Path $ArtifactDirectory "templates/$name"
        $null = New-Item -ItemType Directory -Path (Split-Path $target -Parent) -Force
        Copy-Item -LiteralPath (Join-Path $TemplateDirectory $name) -Destination $target
    }
}
else {
    if ((Get-Item -LiteralPath $ArtifactDirectory -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Artifact links are not allowed.' }
    $manifest = Get-Content -LiteralPath (Assert-RegularPath $ArtifactDirectory 'configuration-manifest.json') -Raw | ConvertFrom-Json -AsHashtable -DateKind String
    $keys = @($context.Keys) + @('validation_run_id', 'created_at', 'files')
    if ($manifest.Count -ne $keys.Count -or @($manifest.Keys | Where-Object { $_ -cnotin $keys }).Count) {
        throw 'Configuration manifest schema mismatch; Terraform plans are not configuration proof.'
    }
    foreach ($key in $context.Keys) {
        if ($null -eq $manifest[$key] -or $manifest[$key].GetType() -ne $context[$key].GetType() -or
            $manifest[$key] -cne $context[$key]) { throw "Configuration context mismatch: $key" }
    }
    if ($env:CONFIGURATION_VALIDATION_RUN_ID -cnotmatch '^[1-9][0-9]*$' -or
        $manifest.validation_run_id -isnot [string] -or
        $manifest.validation_run_id -cne $env:CONFIGURATION_VALIDATION_RUN_ID) { throw 'Configuration validation run mismatch.' }
    $created = [DateTimeOffset]::Parse($manifest.created_at)
    $start = [DateTimeOffset]::Parse($env:CONFIGURATION_VALIDATION_STARTED_AT)
    $finish = [DateTimeOffset]::Parse($env:CONFIGURATION_VALIDATION_FINISHED_AT)
    $now = [DateTimeOffset]::UtcNow
    if ($created.Offset -ne [TimeSpan]::Zero -or $finish -lt $start -or
        $created -lt $start -or $created -gt $finish -or $finish -gt $now -or
        $now - $created -gt [TimeSpan]::FromHours(4)) {
        throw 'Configuration validation expired or has invalid provenance timestamps.'
    }
    # Closed set: omitted inputs, self-consistent substituted hashes and extra files all fail.
    if ($manifest.files -isnot [Collections.IDictionary] -or $manifest.files.Count -ne $expected.Count + 1) { throw 'Configuration file manifest is incomplete.' }
    foreach ($name in $expected.Keys) {
        if ($manifest.files[$name] -isnot [string] -or $manifest.files[$name] -cne $expected[$name]) { throw "Configuration source hash mismatch: $name" }
        $actual = (Get-FileHash -LiteralPath (Assert-RegularPath $ArtifactDirectory $name) -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -cne $expected[$name]) { throw "Configuration artifact hash mismatch: $name" }
    }
    $files = @(Get-ChildItem -LiteralPath $ArtifactDirectory -Recurse -Force -File)
    if ($files.Count -ne $expected.Count + 2) { throw 'Unexpected configuration artifact files.' }
    foreach ($file in $files) {
        $relative = [IO.Path]::GetRelativePath($ArtifactDirectory, $file.FullName).Replace('\', '/')
        if ($relative -cnotin (@($expected.Keys) + @('configuration-manifest.json', 'generated/case-tool.py'))) { throw 'Unexpected configuration artifact path.' }
        $null = Assert-RegularPath $ArtifactDirectory $relative
    }
}
# Execute only the already commit-checked builder, never a supplied artifact script.
$generated = & (Join-Path $SourceDirectory 'scripts/Build-CaseTool.ps1') -PassThru
if ($generated -isnot [string] -or [string]::IsNullOrWhiteSpace($generated)) { throw 'The frozen case-tool builder omitted its package.' }
$generatedHash = Get-TextHash $generated
if ($Action -eq 'Validate') {
    $null = New-Item -ItemType Directory -Path (Join-Path $ArtifactDirectory 'generated')
    [IO.File]::WriteAllText((Join-Path $ArtifactDirectory 'generated/case-tool.py'), $generated, [Text.UTF8Encoding]::new($false))
    $expected['generated/case-tool.py'] = $generatedHash
    $context.validation_run_id = $env:BUILD_BUILDID
    $context.created_at = [DateTimeOffset]::UtcNow.ToString('o')
    $context.files = $expected
    $context | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding utf8
    Write-Output 'Configuration validation artifact created; no Terraform plan or runtime acceptance is claimed.'
    return
}
if ($manifest.files['generated/case-tool.py'] -cne $generatedHash -or
    (Get-FileHash -LiteralPath (Assert-RegularPath $ArtifactDirectory 'generated/case-tool.py') -Algorithm SHA256).Hash.ToLowerInvariant() -cne $generatedHash) {
    throw 'Generated case-tool package hash mismatch.'
}
if ($Action -eq 'Verify') {
    Write-Output 'Configuration source, template, context, age and every explicit artifact hash verified; no cloud operation attempted.'
    return
}
if ([string]::IsNullOrWhiteSpace($env:idToken) -or
    $env:servicePrincipalId -cne '00000018-000a-4000-b000-000000000018' -or $env:tenantId -cne $Tenant) {
    throw 'Disabled publication requires the retained protected Apply OIDC service connection.'
}
# No arbitrary transport/file parameter or activation escape hatch. A owns the
# verified adapter. Until it is installed in A's frozen entry point this throws,
# deliberately, rather than guessing native REST paths or claiming publication.
$result = & (Join-Path $ArtifactDirectory 'source/scripts/Publish-IncidentWorkflow.ps1') -Action PublishDisabled
if ($null -eq $result -or $result.status -cne 'published-disabled' -or $result.runtime_accepted -isnot [bool] -or $result.runtime_accepted) {
    throw 'Disabled publication did not return strict verified readback.'
}
$result
