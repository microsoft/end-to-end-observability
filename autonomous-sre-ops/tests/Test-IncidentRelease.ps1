#requires -Version 7.5
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Repo = Split-Path $PSScriptRoot -Parent
$Script = Join-Path $Repo 'scripts/Invoke-IncidentRelease.ps1'
$Temp = Join-Path ([IO.Path]::GetTempPath()) "asre-configuration-fixture-$([guid]::NewGuid().ToString('N'))"
$Source = Join-Path $Temp 'source'
$Templates = Join-Path $Temp 'templates'
$Artifact = Join-Path $Temp 'configuration'
$global:ReleaseFixtureSourceCommit = 'a' * 40
$global:ReleaseFixtureTemplateCommit = 'b' * 40
$global:ReleaseFixtureDirty = $false
$global:ReleaseFixtureUntracked = $false
$global:ReleaseFixtureWrongHead = $false
$global:ReleaseFixturePublications = 0
$global:ReleaseFixtureBuilds = 0
$global:ReleaseFixtureReadback = $true
$script:Passed = 0
$Names = @(
    'BUILD_SOURCEBRANCH', 'BUILD_REASON', 'BUILD_REPOSITORY_PROVIDER', 'BUILD_SOURCEVERSION',
    'TEMPLATE_SOURCE_COMMIT', 'SYSTEM_COLLECTIONURI', 'SYSTEM_TEAMPROJECTID', 'SYSTEM_DEFINITIONID',
    'BUILD_REPOSITORY_ID', 'BUILD_BUILDID', 'CONFIGURATION_VALIDATION_RUN_ID',
    'CONFIGURATION_VALIDATION_STARTED_AT', 'CONFIGURATION_VALIDATION_FINISHED_AT',
    'idToken', 'servicePrincipalId', 'tenantId'
)
$Original = @{}
foreach ($name in $Names) { $Original[$name] = [Environment]::GetEnvironmentVariable($name) }

function global:git {
    $global:LASTEXITCODE = 0
    if ($args[0] -cne '-C') { throw 'Unexpected fixture Git invocation.' }
    switch ($args[2]) {
        'rev-parse' {
            if ($global:ReleaseFixtureWrongHead) { return ('c' * 40) }
            if ($args[1].EndsWith('templates')) { return $global:ReleaseFixtureTemplateCommit }
            return $global:ReleaseFixtureSourceCommit
        }
        'ls-files' { if ($global:ReleaseFixtureUntracked) { $global:LASTEXITCODE = 1 } }
        'diff' { if ($global:ReleaseFixtureDirty) { $global:LASTEXITCODE = 1 } }
        default { throw 'Unexpected fixture Git operation.' }
    }
}
function global:az { throw 'Offline release fixtures must never invoke Azure.' }
function global:terraform { throw 'Configuration release must never invoke Terraform.' }
function Assert-Fixture {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
    $script:Passed++
}
function Invoke-Release {
    param([string]$Action = 'Verify')
    & $Script -Action $Action -SourceDirectory $Source -TemplateDirectory $Templates -ArtifactDirectory $Artifact
}
function Expect-Rejected {
    param([scriptblock]$Probe, [string]$Message)
    $before = $global:ReleaseFixturePublications
    $rejected = $false
    try { $null = & $Probe } catch {
        if ($_.Exception.Message -notmatch $Message) { throw "Unexpected rejection: $($_.Exception.Message); expected $Message" }
        $rejected = $true
    }
    Assert-Fixture ($rejected -and $global:ReleaseFixturePublications -eq $before) "Expected bounded pre-publication rejection: $Message"
}
function Write-Manifest {
    param($Manifest)
    $Manifest | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $Artifact 'configuration-manifest.json') -Encoding utf8
}
function Set-FixtureDiscoveryManifest([hashtable]$Manifest) {
    # Pin historical/capture fixtures, not product configuration. H is now
    # Production; the existing native-capture gate remains Discovery-only.
    $Manifest.phase = 'SchemaDiscovery'
    $Manifest.case_profiles = @('disk')
    $Manifest.response_plan.properties.maxAutomatedInvestigationAttempts = 1
    $Manifest.response_plan.properties.mergeEnabled = $true
    $Manifest.response_plan.properties.priorities = @('Sev1')
    $Manifest.response_plan.properties.titleContains = 'asre-demo-disk'
    $Manifest.response_plan.properties.alertId = '/subscriptions/00000001-000a-4000-a000-000000000001/resourcegroups/rg-asre-control-cus/providers/microsoft.operationalinsights/workspaces/law-asre-cus/providers/Microsoft.AlertsManagement/alerts/00000047-000a-d000-0000-000000000047'
}
function Invoke-FixtureChild([string]$Code) {
    # The complete native handler now exceeds Windows' native argv bound.
    # Execute identical fixture code via -File; retain every isolation/mutation
    # assertion and the real child process/exit boundary.
    $path = Join-Path $Temp ('child-' + [guid]::NewGuid().ToString('N') + '.ps1')
    [IO.File]::WriteAllText($path, $Code)
    try { & pwsh -NoLogo -NoProfile -NonInteractive -File $path 2>&1 }
    finally { Remove-Item -LiteralPath $path -Force }
}

try {
    $tokens = $null; $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile($Script, [ref]$tokens, [ref]$errors)
    Assert-Fixture ($errors.Count -eq 0) 'Release runner has syntax errors.'
    foreach ($variable in @('SourceFiles', 'TemplateFiles')) {
        $assignment = $ast.Find({ param($node)
            $node -is [Management.Automation.Language.AssignmentStatementAst] -and $node.Left.ToString() -ceq ('$' + $variable)
        }.GetNewClosure(), $true)
        $files = @($assignment.Right.FindAll({
            param($node) $node -is [Management.Automation.Language.StringConstantExpressionAst]
        }, $true) | ForEach-Object { $_.Value })
        $root = if ($variable -eq 'SourceFiles') { $Source } else { $Templates }
        foreach ($file in $files) {
            $path = Join-Path $root $file
            $null = New-Item -ItemType Directory -Path (Split-Path $path -Parent) -Force
            [IO.File]::WriteAllText($path, "Fixture input $file")
        }
    }
    # Fixture-only deterministic builder/publisher, never a runtime adapter.
    [IO.File]::WriteAllText((Join-Path $Source 'scripts/Build-CaseTool.ps1'), @'
param([switch]$PassThru)
$global:ReleaseFixtureBuilds++
if (-not $PassThru) { throw 'Expected frozen B PassThru contract.' }
'# offline deterministic generated package'
'@)
    [IO.File]::WriteAllText((Join-Path $Source 'scripts/Publish-IncidentWorkflow.ps1'), @'
param([ValidateSet('PublishDisabled')][string]$Action)
if ($Action -cne 'PublishDisabled') { throw 'Activation is prohibited.' }
if (-not $global:ReleaseFixtureReadback) { return @{ status = 'unverified'; runtime_accepted = $false } }
$global:ReleaseFixturePublications++
@{ status = 'published-disabled'; runtime_accepted = $false }
'@)
    $env:BUILD_SOURCEBRANCH = 'refs/heads/master'
    $env:BUILD_REASON = 'Manual'
    $env:BUILD_REPOSITORY_PROVIDER = 'TfsGit'
    $env:BUILD_SOURCEVERSION = $global:ReleaseFixtureSourceCommit
    $env:TEMPLATE_SOURCE_COMMIT = $global:ReleaseFixtureTemplateCommit
    $env:SYSTEM_COLLECTIONURI = 'https://dev.azure.com/offline-fixture/'
    $env:SYSTEM_TEAMPROJECTID = '11111111-1111-1111-1111-111111111111'
    $env:SYSTEM_DEFINITIONID = '42'
    $env:BUILD_REPOSITORY_ID = '22222222-2222-2222-2222-222222222222'
    $env:BUILD_BUILDID = '101'
    $env:CONFIGURATION_VALIDATION_RUN_ID = '101'
    $env:CONFIGURATION_VALIDATION_STARTED_AT = [DateTimeOffset]::UtcNow.AddMinutes(-1).ToString('o')
    $null = Invoke-Release Validate
    $env:CONFIGURATION_VALIDATION_FINISHED_AT = [DateTimeOffset]::UtcNow.ToString('o')
    $env:BUILD_BUILDID = '102'
    $null = Invoke-Release
    Assert-Fixture ($global:ReleaseFixturePublications -eq 0) 'Validation or verification attempted publication.'
    Assert-Fixture (-not (Test-Path (Join-Path $Artifact 'manifest.json')) -and -not (Test-Path (Join-Path $Artifact 'demo.tfplan'))) 'Configuration produced a Terraform artifact.'
    Expect-Rejected { Invoke-Release Validate } 'new configuration artifact'
    $manifestText = Get-Content -LiteralPath (Join-Path $Artifact 'configuration-manifest.json') -Raw
    $originalManifest = $manifestText | ConvertFrom-Json -AsHashtable -DateKind String
    Assert-Fixture ($originalManifest.files.ContainsKey('generated/case-tool.py')) 'Generated B package lacks an explicit hash.'
    foreach ($field in @('artifact_kind', 'profile', 'collection_uri', 'project_id', 'definition_id',
            'repository_id', 'source_branch', 'source_commit', 'template_repository', 'template_commit',
            'subscription_id', 'tenant_id', 'agent_resource_id', 'agent_endpoint', 'publication_action')) {
        $manifest = $manifestText | ConvertFrom-Json -AsHashtable -DateKind String
        $manifest[$field] = 'wrong-context'
        Write-Manifest $manifest
        Expect-Rejected { Invoke-Release } 'context mismatch'
    }
    $manifest = $manifestText | ConvertFrom-Json -AsHashtable -DateKind String
    $manifest.runtime_accepted = $true
    Write-Manifest $manifest
    Expect-Rejected { Invoke-Release } 'context mismatch'
    $manifest.runtime_accepted = 'False'
    Write-Manifest $manifest
    Expect-Rejected { Invoke-Release } 'context mismatch'
    Write-Manifest $originalManifest
    foreach ($field in @('BUILD_SOURCEBRANCH', 'BUILD_REASON', 'BUILD_REPOSITORY_PROVIDER', 'BUILD_SOURCEVERSION', 'TEMPLATE_SOURCE_COMMIT')) {
        $value = [Environment]::GetEnvironmentVariable($field)
        [Environment]::SetEnvironmentVariable($field, 'untrusted')
        $before = $global:ReleaseFixtureBuilds
        Expect-Rejected { Invoke-Release } 'manual trusted master'
        Assert-Fixture ($global:ReleaseFixtureBuilds -eq $before) 'Ineligible source executed a builder.'
        [Environment]::SetEnvironmentVariable($field, $value)
    }
    foreach ($scenario in @('Dirty', 'Untracked', 'WrongHead')) {
        Set-Variable -Name "ReleaseFixture$scenario" -Scope Global -Value $true
        Expect-Rejected { Invoke-Release } 'checkout|versioned'
        Set-Variable -Name "ReleaseFixture$scenario" -Scope Global -Value $false
    }
    foreach ($field in @('validation_run_id', 'created_at')) {
        $manifest = $manifestText | ConvertFrom-Json -AsHashtable -DateKind String
        $manifest[$field] = if ($field -eq 'validation_run_id') { '999' } else { [DateTimeOffset]::UtcNow.AddHours(-5).ToString('o') }
        Write-Manifest $manifest
        Expect-Rejected { Invoke-Release } 'run mismatch|expired'
    }
    $manifest.created_at = [DateTimeOffset]::UtcNow.AddHours(1).ToString('o')
    Write-Manifest $manifest
    Expect-Rejected { Invoke-Release } 'invalid provenance'
    # Prove the age limit independently of provenance-window validation.
    $start = $env:CONFIGURATION_VALIDATION_STARTED_AT
    $finish = $env:CONFIGURATION_VALIDATION_FINISHED_AT
    $env:CONFIGURATION_VALIDATION_STARTED_AT = [DateTimeOffset]::UtcNow.AddHours(-6).ToString('o')
    $env:CONFIGURATION_VALIDATION_FINISHED_AT = [DateTimeOffset]::UtcNow.AddHours(-4.5).ToString('o')
    $manifest = $manifestText | ConvertFrom-Json -AsHashtable -DateKind String
    $manifest.created_at = [DateTimeOffset]::UtcNow.AddHours(-5).ToString('o')
    Write-Manifest $manifest
    Expect-Rejected { Invoke-Release } 'expired'
    $env:CONFIGURATION_VALIDATION_STARTED_AT = $start
    $env:CONFIGURATION_VALIDATION_FINISHED_AT = $finish
    $manifest = $manifestText | ConvertFrom-Json -AsHashtable -DateKind String
    $manifest.files.Remove('source/scripts/ReviewAgent.Common.ps1')
    Write-Manifest $manifest
    Expect-Rejected { Invoke-Release } 'incomplete'
    $manifest = $manifestText | ConvertFrom-Json -AsHashtable -DateKind String
    $manifest.files['source/scripts/ReviewAgent.Common.ps1'] = '0' * 64
    Write-Manifest $manifest
    Expect-Rejected { Invoke-Release } 'source hash mismatch'
    Write-Manifest $originalManifest
    foreach ($relative in @('source/scripts/ReviewAgent.Common.ps1', 'templates/.pipelines/cd-template.yaml', 'generated/case-tool.py')) {
        $path = Join-Path $Artifact $relative
        $bytes = [IO.File]::ReadAllBytes($path)
        [IO.File]::WriteAllText($path, 'mutation')
        Expect-Rejected { Invoke-Release } 'hash mismatch'
        # Rehashing the modified artifact cannot substitute trusted source.
        $manifest = $manifestText | ConvertFrom-Json -AsHashtable -DateKind String
        $manifest.files[$relative] = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        Write-Manifest $manifest
        Expect-Rejected { Invoke-Release } 'hash mismatch'
        [IO.File]::WriteAllBytes($path, $bytes)
        Write-Manifest $originalManifest
    }
    $extra = Join-Path $Artifact 'manifest.json'
    [IO.File]::WriteAllText($extra, '{}')
    Expect-Rejected { Invoke-Release } 'Unexpected configuration artifact'
    Remove-Item -LiteralPath $extra
    $manifest = $manifestText | ConvertFrom-Json -AsHashtable -DateKind String
    $manifest.files['../../escape.ps1'] = '0' * 64
    Write-Manifest $manifest
    Expect-Rejected { Invoke-Release } 'incomplete'
    Write-Manifest $originalManifest
    $env:idToken = 'fixture-not-a-token'
    $env:servicePrincipalId = '00000018-000a-4000-b000-000000000018'
    $env:tenantId = '00000002-000a-4000-9000-000000000002'
    foreach ($field in @('idToken', 'servicePrincipalId', 'tenantId')) {
        $value = [Environment]::GetEnvironmentVariable($field)
        [Environment]::SetEnvironmentVariable($field, '')
        Expect-Rejected { Invoke-Release PublishDisabled } 'retained protected Apply'
        [Environment]::SetEnvironmentVariable($field, $value)
    }
    $global:ReleaseFixtureReadback = $false
    Expect-Rejected { Invoke-Release PublishDisabled } 'strict verified readback'
    $global:ReleaseFixtureReadback = $true
    $result = Invoke-Release PublishDisabled
    Assert-Fixture ($result.status -ceq 'published-disabled' -and -not $result.runtime_accepted -and
        $global:ReleaseFixturePublications -eq 1) 'The frozen publisher was not delegated exactly disabled publication.'
    foreach ($action in @('Apply', 'BootstrapPlatform', 'Cutover', 'Enable', 'Pause', 'Rollback')) {
        Expect-Rejected { Invoke-Release $action } 'ValidateSet|validation set|does not belong'
    }
    $controllerAst = [Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $Repo 'scripts/Publish-IncidentWorkflow.ps1'), [ref]$tokens, [ref]$errors)
    Assert-Fixture ($errors.Count -eq 0) 'Public controller failed parsing.'
    $parameters = @($controllerAst.ParamBlock.Parameters | ForEach-Object { $_.Name.VariablePath.UserPath })
    foreach ($name in @('BootstrapProfile','BootstrapReceipt','PublicationReceipt')) {
        Assert-Fixture ($parameters -ccontains $name) "Public controller omitted bounded $name input."
    }
    Assert-Fixture ($parameters -cnotcontains 'Transport' -and $parameters -cnotcontains 'Force' -and
        $parameters -cnotcontains 'IgnoreEtag') 'Public controller exposed an authority bypass.'
    # Run the actual non-interactive OUTER release entrypoint, actual controller
    # and actual Common/native adapter. Only CLI/HTTP and the package builder
    # are mocked. No reconstructed receipt or table parsing is used.
    $builderFixture = [IO.File]::ReadAllText((Join-Path $Source 'scripts/Build-CaseTool.ps1'))
    $sourceAssignment = $ast.Find({ param($node)
        $node -is [Management.Automation.Language.AssignmentStatementAst] -and $node.Left.ToString() -ceq '$SourceFiles'
    }, $true)
    foreach ($relative in $sourceAssignment.Right.Expression.SafeGetValue()) {
        Copy-Item -LiteralPath (Join-Path $Repo $relative) -Destination (Join-Path $Source $relative) -Force
    }
    [IO.File]::WriteAllText((Join-Path $Source 'scripts/Build-CaseTool.ps1'), $builderFixture)
    # Existing receipt/chain probes remain genuinely legacy. The new
    # source-declared capture path gets a separate real outer-wrapper probe.
    $legacyManifestPath=Join-Path $Source 'agent/workflows/azure-monitor-review.json'
    $legacyManifest=Get-Content -LiteralPath $legacyManifestPath -Raw|ConvertFrom-Json -AsHashtable
    $legacyManifest.Remove('native_capture')
    # This branch models the pre-capture manifest, including absence of its
    # future workspace selector. The separate current-source branch below
    # restores and strictly verifies the real fixed selector without defaults.
    $legacyManifest.response_plan.properties.Remove('azMonitorFilterSettings')
    Set-FixtureDiscoveryManifest $legacyManifest
    $legacyManifest|ConvertTo-Json -Depth 40|Set-Content -LiteralPath $legacyManifestPath -Encoding utf8
    Remove-Item -LiteralPath $Artifact -Recurse -Force
    $env:BUILD_BUILDID = '101'
    $env:CONFIGURATION_VALIDATION_STARTED_AT = [DateTimeOffset]::UtcNow.AddSeconds(-1).ToString('o')
    $null = Invoke-Release Validate
    $env:CONFIGURATION_VALIDATION_FINISHED_AT = [DateTimeOffset]::UtcNow.ToString('o')
    $env:BUILD_BUILDID = '102'
    $child = @'
$ErrorActionPreference='Stop'
$global:ReleaseFixtureSourceCommit='a'*40
$global:ReleaseFixtureTemplateCommit='b'*40
$global:ReleaseFixtureDirty=$false
$global:ReleaseFixtureUntracked=$false
$global:ReleaseFixtureWrongHead=$false
$global:ReleaseFixtureBuilds=0
# Shield only the exact host receipt; LOCALAPPDATA alone cannot redirect
# Environment.GetFolderPath on Windows. Simulated existence never touches J.
$global:FixtureLiveReceipt=[IO.Path]::GetFullPath((Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'ASRE/native-bootstrap/asre-demo-cus/bootstrap-reconciliation.json'))
$global:FixturePathComparison=$(if($IsWindows){[StringComparison]::OrdinalIgnoreCase}else{[StringComparison]::Ordinal})
$global:FixtureHideLiveReceipt=$true
$global:FixtureReceiptMasks=0
$global:FixtureReceiptReads=0
function global:Test-Path {
    [CmdletBinding()]
    param([string]$Path,[string]$LiteralPath,[string]$PathType)
    $candidate=$(if($PSBoundParameters.ContainsKey('LiteralPath')){$LiteralPath}else{$Path})
    if($candidate -and [string]::Equals([IO.Path]::GetFullPath($candidate),$global:FixtureLiveReceipt,$global:FixturePathComparison)){
        if($global:FixtureHideLiveReceipt){$global:FixtureReceiptMasks++;return $false}
        return $true # In-memory simulation of an existing live receipt.
    }
    Microsoft.PowerShell.Management\Test-Path @PSBoundParameters
}
function global:Get-Content {
    [CmdletBinding()]
    param([string]$Path,[string]$LiteralPath,[switch]$Raw)
    $candidate=$(if($PSBoundParameters.ContainsKey('LiteralPath')){$LiteralPath}else{$Path})
    if($candidate -and [string]::Equals([IO.Path]::GetFullPath($candidate),$global:FixtureLiveReceipt,$global:FixturePathComparison)){
        $global:FixtureReceiptReads++
        throw 'LIVE_RECEIPT_READ_BLOCKED'
    }
    Microsoft.PowerShell.Management\Get-Content @PSBoundParameters
}
$global:FixtureHideLiveReceipt=$false
if(-not(Test-Path -LiteralPath $global:FixtureLiveReceipt)){throw 'Receipt existence simulation failed'}
$global:FixtureHideLiveReceipt=$true
if(Test-Path -LiteralPath $global:FixtureLiveReceipt){throw 'Exact receipt isolation failed'}
if(-not(Test-Path -LiteralPath '__SOURCE__' -PathType Container)){throw 'Unrelated filesystem delegation failed'}
$global:FixtureReceiptMasks=0
function global:git {
    $global:LASTEXITCODE=0
    if($args[0] -cne '-C'){throw 'Unexpected child Git operation'}
    switch($args[2]){
        'rev-parse' {if($args[1].EndsWith('templates')){return $global:ReleaseFixtureTemplateCommit};return $global:ReleaseFixtureSourceCommit}
        'ls-files' {}
        'diff' {}
        default {throw 'Unexpected child Git operation'}
    }
}
function global:az {
    $global:LASTEXITCODE=0
    if(($args -join ' ') -like 'account show *'){
        return (@{id='00000001-000a-4000-a000-000000000001';tenantId='00000002-000a-4000-9000-000000000002'
            user=@{type='servicePrincipal';name='00000018-000a-4000-b000-000000000018'}}|ConvertTo-Json -Compress)
    }
    if(($args -join ' ') -like 'account get-access-token *'){return 'offline-token'}
    throw 'Unexpected child CLI operation'
}
$global:OuterResources=@{
 '/api/v2/extendedAgent/agents/asre-azure-monitor-review'=([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__HANDLER__'))|ConvertFrom-Json -AsHashtable)
}
$global:OuterPolicy=@{allow=@()}
$global:OuterCaptureFixture=$false
$global:OuterCaptureRace=$false
$global:OuterFilterPatches=0
function global:Invoke-WebRequest {
    param($Uri,$Method,$Headers,$MaximumRedirection,$TimeoutSec,$ErrorAction,$SkipHttpErrorCheck,$Body,$ContentType)
    $path=([uri]$Uri).AbsolutePath;$code=200;$data=$null;$etag='"00000056-000a-0000-0000-000000000056"'
    if($Uri.StartsWith('https://management.azure.com/')){
        if($Method -cne 'Get'){throw 'Outer publication attempted ARM mutation'}
        $identity='/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-asre-agent-cus'
        $etag=''
        $data=@{id=$path;identity=@{userAssignedIdentities=@{$identity=@{principalId='00000050-000a-4000-8000-000000000050'}}}
          properties=@{agentEndpoint='https://asre-demo-cus--0a1b2c3d.4e5f6a7b.centralus.azuresre.ai'
            actionConfiguration=@{mode='Review';accessLevel='Low';identity=$identity}
            incidentManagementConfiguration=@{type='AzMonitor';connectionName='azmonitor';oboUser=''
                connectionUrl='https://example-instance.service-now.com/';connectionKey='';apiConnectionName='asre-demo-cus-servicenow-buq5o4th4p'}}}
    }elseif($path -ceq '/api/v2/agent/settings/global'){
        if($Method -cne 'Get'){throw 'Global write'}
        $data=@{permissions=@{allow=@();ask=@();deny=@()}}
    }elseif($path -ceq '/api/v2/incidentManagement/checkConnectivityDetailed'){
        $data=@{success=$true;errorMessage=$null;incidentType='AzMonitor';observedApiConnectionName=$null
            observedConfigurationRevision=$null;observedConnectionStatus='Connected'}
    }elseif($path -ceq '/api/v2/incidentManagement/incidentFilters'){
        $data=@{value=@();nextLink=$null}
        $key='/api/v2/incidentManagement/incidentFilters/asre-azure-monitor-review'
        if($global:OuterResources.ContainsKey($key)){$data.value=@($global:OuterResources[$key])}
    }elseif($path -ceq '/api/v2/extendedAgent/asre-azure-monitor-review/permissions'){
        $etag='"00000059-000a-0000-0000-000000000059"'
        if($Method -ceq 'Put'){$global:OuterPolicy=$Body|ConvertFrom-Json -AsHashtable}
        $data=$global:OuterPolicy
    }else{
        if($path -ceq '/api/v2/incidentManagement/incidentFilters/asre-azure-monitor-review'){
            if($Method -ceq 'Patch'){$global:OuterFilterPatches++}
            if($Method -ceq 'Get' -and $global:OuterCaptureRace){
                $global:OuterResources[$path].properties.isEnabled=$true
            }
        }
        if($Method -cin @('Put','Patch')){
            $global:OuterResources[$path]=$Body|ConvertFrom-Json -AsHashtable
            if($path -ceq '/api/v2/incidentManagement/incidentFilters/asre-azure-monitor-review' -and -not $global:OuterCaptureFixture){
                $global:OuterResources[$path].properties.azMonitorFilterSettings=@{targetResourceType='';targetResource=''}
            }
        }
        if($global:OuterResources.ContainsKey($path)){$data=$global:OuterResources[$path]}else{$code=404}
    }
    return @{StatusCode=$code;Content=$(if($null -ne $data){ConvertTo-Json -InputObject $data -Depth 40}else{''});Headers=@{ETag=$etag}}
}
__SUPPRESS__
__ISOLATION_BYPASS__
$result=& '__RUNNER__' -Action PublishDisabled -SourceDirectory '__SOURCE__' -TemplateDirectory '__TEMPLATES__' -ArtifactDirectory '__ARTIFACT__' -InformationVariable receiptInformation
if($global:FixtureReceiptMasks -lt 1 -or $global:FixtureReceiptReads -ne 0){throw 'Publisher did not isolate the simulated existing receipt'}
Write-Output 'ASRE_RELEASE_FIXTURE_RECEIPT_ISOLATED_V1'
$records=@($receiptInformation|Where-Object {$_.MessageData -is [string] -and $_.MessageData.StartsWith('ASRE_MONITOR_PUBLICATION_RECEIPT_V1=',[StringComparison]::Ordinal)})
if($records.Count -ne 1){throw 'Missing/duplicate actual publication marker'}
$wire=$records[0].MessageData.Substring('ASRE_MONITOR_PUBLICATION_RECEIPT_V1='.Length)|ConvertFrom-Json -AsHashtable
if($result.publication_receipt.Count -ne 7 -or $wire.Count -ne 7){throw 'Receipt field count changed'}
$contexts=@($receiptInformation|Where-Object {$_.MessageData -is [string] -and $_.MessageData.StartsWith('ASRE_MONITOR_PUBLICATION_CONTEXT_V1=',[StringComparison]::Ordinal)})
if($contexts.Count -ne 1){throw 'Missing actual cross-host publication context'}
$portable=$contexts[0].MessageData.Substring('ASRE_MONITOR_PUBLICATION_CONTEXT_V1='.Length)|ConvertFrom-Json -AsHashtable
if($portable.version -cne 'asre-monitor-publication-context/v1' -or
   $portable.protected_run.publication_run_id -cne '102' -or
   $portable.adopted_pre_snapshot.filters.Count -ne 0){throw 'Cross-host publication context changed'}
foreach($key in $result.publication_receipt.Keys){
    if((ConvertTo-Json -InputObject $result.publication_receipt[$key] -Compress) -cne (ConvertTo-Json -InputObject $wire[$key] -Compress)){throw 'Marker differs from actual returned receipt'}
}
$result
'@
    foreach ($entry in @(@('__RUNNER__',$Script),@('__SOURCE__',$Source),@('__TEMPLATES__',$Templates),@('__ARTIFACT__',$Artifact))) {
        $child = $child.Replace($entry[0], $entry[1].Replace("'", "''"))
    }
    $handler = & { . (Join-Path $Repo 'scripts/ReviewAgent.Common.ps1'); @(Get-MonitorReviewConfiguration -Root $Source)[0] }
    $legacyHandler = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject $handler -Depth 40 -Compress)))
    $child=$child.Replace('__HANDLER__',$legacyHandler)
    $wireOutput = @(Invoke-FixtureChild ($child.Replace('__SUPPRESS__','').Replace('__ISOLATION_BYPASS__','')))
    Assert-Fixture ($LASTEXITCODE -eq 0) "Outer receipt path failed: $($wireOutput -join ' ')"
    $markers = @($wireOutput | ForEach-Object { "$_" } | Where-Object { $_.StartsWith('ASRE_MONITOR_PUBLICATION_RECEIPT_V1=',[StringComparison]::Ordinal) })
    Assert-Fixture ($markers.Count -eq 1) 'Outer stdout lost/duplicated the exact marker.'
    Assert-Fixture (@($wireOutput|Where-Object {"$_" -like 'ASRE_MONITOR_PUBLICATION_CONTEXT_V1=*'}).Count -eq 1) 'Clean-host publisher omitted its adopted context.'
    $wire = $markers[0].Substring('ASRE_MONITOR_PUBLICATION_RECEIPT_V1='.Length) | ConvertFrom-Json -AsHashtable
    Assert-Fixture ($wire.Count -eq 7 -and $wire.runtime_accepted -is [bool] -and -not $wire.runtime_accepted) 'Outer receipt shape/type changed.'
    foreach ($key in @('pre_snapshot_sha256','paused_snapshot_sha256','agent_definition_sha256')) {
        Assert-Fixture ($wire[$key] -cmatch '^[0-9a-f]{64}\z') "Outer marker omitted actual $key."
    }
    Assert-Fixture (@($wireOutput|Where-Object {"$_" -ceq 'ASRE_RELEASE_FIXTURE_RECEIPT_ISOLATED_V1'}).Count -eq 1) 'Simulated live receipt influenced/read by outer fixture.'
    $unshielded=$child.Replace('__SUPPRESS__','').Replace('__ISOLATION_BYPASS__','$global:FixtureHideLiveReceipt=$false')
    $unshieldedOutput=@(Invoke-FixtureChild $unshielded)
    Assert-Fixture ($LASTEXITCODE -ne 0 -and ($unshieldedOutput -join ' ') -match 'LIVE_RECEIPT_READ_BLOCKED') 'Unshielded simulated receipt did not reach the read guard.'
    $suppressed = $child.Replace('__SUPPRESS__', 'function global:Write-Information { param($MessageData,$InformationAction) }').Replace('__ISOLATION_BYPASS__','')
    $suppressedOutput = @(Invoke-FixtureChild $suppressed)
    Assert-Fixture ($LASTEXITCODE -ne 0 -and ($suppressedOutput -join ' ') -match 'Missing/duplicate actual publication marker') 'Receipt-channel suppression mutation survived.'
    # AC7: real entrypoint/import regression. The old dot-source import replaced
    # a wrapper's hashtable $Profile with the initializer's Native default.
    $importProbe = @'
$ErrorActionPreference='Stop'
$Profile=@{sentinel='wrapper-profile'}
$OutputsFile='wrapper-output'
. '__COMMON__'
if($Profile -isnot [hashtable] -or $Profile.sentinel -cne 'wrapper-profile' -or $OutputsFile -cne 'wrapper-output'){throw 'Import variable collision'}
$global:ReconcileProbeAuth=0
function global:az {$global:ReconcileProbeAuth++;throw 'Unexpected authentication'}
$rejected=$false
try {
 & '__CONTROLLER__' -Action ReconcileBootstrap -ActivationWindow @{not_before='2020-01-01T00:00:00Z';expires_at='2020-01-01T00:05:00Z'} -PublicationArtifactDirectory 'never-read'
} catch {
 if($_.Exception.Message -notmatch 'window|expired'){throw}
 $rejected=$true
}
if(-not $rejected -or $global:ReconcileProbeAuth -ne 0){throw 'Public reconciliation did not reject before authentication'}
'@
    $importProbe=$importProbe.Replace('__COMMON__',(Join-Path $Repo 'scripts/ReviewAgent.Common.ps1').Replace("'","''")).
        Replace('__CONTROLLER__',(Join-Path $Repo 'scripts/Publish-IncidentWorkflow.ps1').Replace("'","''"))
    $importOutput=@(Invoke-FixtureChild $importProbe)
    Assert-Fixture ($LASTEXITCODE -eq 0) "Public reconciliation/import probe failed: $($importOutput -join ' ')"
    $actions=@((Get-Command (Join-Path $Repo 'scripts/Publish-IncidentWorkflow.ps1')).Parameters.Action.Attributes |
        Where-Object {$_ -is [Management.Automation.ValidateSetAttribute]})[0]
    Assert-Fixture ($actions.ValidValues -ccontains 'ReconcileBootstrap' -and -not $actions.IgnoreCase) 'Public reconciliation action admission changed.'
    Assert-Fixture ($actions.ValidValues -ccontains 'NativeCaptureEnable' -and
        $actions.ValidValues -ccontains 'NativeCapturePreflight') 'Explicit native intake actions missing.'
    # Second real outer -> real public wrapper -> real native adapter path:
    # source opts into static candidate intake, fixed protected context, no J.
    $captureManifest=Get-Content -LiteralPath (Join-Path $Repo 'agent/workflows/azure-monitor-review.json') -Raw|ConvertFrom-Json -AsHashtable
    Set-FixtureDiscoveryManifest $captureManifest
    $captureManifest|ConvertTo-Json -Depth 40|Set-Content -LiteralPath $legacyManifestPath -Encoding utf8
    $global:ReleaseFixtureTemplateCommit='000000000000000000000000000000000000006a'
    $env:TEMPLATE_SOURCE_COMMIT=$global:ReleaseFixtureTemplateCommit
    $env:SYSTEM_COLLECTIONURI='https://dev.azure.com/example-org/'
    $env:SYSTEM_TEAMPROJECTID='00000016-000a-4000-8000-000000000016'
    $env:SYSTEM_DEFINITIONID='8'
    $env:BUILD_REPOSITORY_ID='00000039-000a-4000-a000-000000000039'
    Remove-Item -LiteralPath $Artifact -Recurse -Force
    $env:BUILD_BUILDID='101'
    $env:CONFIGURATION_VALIDATION_STARTED_AT=[DateTimeOffset]::UtcNow.AddSeconds(-1).ToString('o')
    $null=Invoke-Release Validate
    $env:CONFIGURATION_VALIDATION_FINISHED_AT=[DateTimeOffset]::UtcNow.ToString('o')
    $env:BUILD_BUILDID='102'
    $prefix=$child.Substring(0,$child.IndexOf('$result=&'))
    $prefix=$prefix.Replace("`$global:ReleaseFixtureTemplateCommit='b'*40","`$global:ReleaseFixtureTemplateCommit='000000000000000000000000000000000000006a'")
    $handler = & { . (Join-Path $Repo 'scripts/ReviewAgent.Common.ps1'); @(Get-MonitorReviewConfiguration -Root $Source)[0] }
    $currentHandler = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject $handler -Depth 40 -Compress)))
    $prefix = $prefix.Replace("'$legacyHandler'", "'$currentHandler'")
    $candidateChild=$prefix+@'
$global:OuterCaptureFixture=$true
$m=Microsoft.PowerShell.Management\Get-Content -LiteralPath '__SOURCE__/agent/workflows/azure-monitor-review.json' -Raw|ConvertFrom-Json -AsHashtable
$global:OuterResources['/api/v2/incidentManagement/incidentFilters/asre-azure-monitor-review']=$m.response_plan
$result=& '__RUNNER__' -Action PublishDisabled -SourceDirectory '__SOURCE__' -TemplateDirectory '__TEMPLATES__' -ArtifactDirectory '__ARTIFACT__' -InformationVariable captureInformation
if($result.status -cne 'published-disabled' -or $result.runtime_accepted -isnot [bool] -or $result.runtime_accepted){throw 'Capture outer contract failed'}
if($global:FixtureReceiptMasks -ne 0 -or $global:FixtureReceiptReads -ne 0){throw 'Capture consulted old bootstrap authority'}
$records=@($captureInformation|Where-Object {$_.MessageData -is [string] -and $_.MessageData.StartsWith('ASRE_NATIVE_CAPTURE_PUBLICATION_V1=',[StringComparison]::Ordinal)})
$contexts=@($captureInformation|Where-Object {$_.MessageData -is [string] -and $_.MessageData.StartsWith('ASRE_NATIVE_CAPTURE_CONTEXT_V1=',[StringComparison]::Ordinal)})
if($records.Count -ne 1 -or $contexts.Count -ne 1){throw 'Capture receipt/context marker missing'}
$r=$records[0].MessageData.Substring('ASRE_NATIVE_CAPTURE_PUBLICATION_V1='.Length)|ConvertFrom-Json -AsHashtable
if($r.version -cne 'asre-native-capture-publication/v1' -or $r.publication_run_id -cne '102' -or $r.runtime_accepted){throw 'Wrong capture receipt'}
$plan=$global:OuterResources['/api/v2/incidentManagement/incidentFilters/asre-azure-monitor-review']
if($plan.properties.isEnabled -or $plan.properties.alertId -cne '' -or
    $plan.properties.azMonitorFilterSettings.targetResourceType -cne 'Microsoft.OperationalInsights/workspaces'){throw 'Capture did not publish disabled future selector'}
Write-Output 'ASRE_RELEASE_STATIC_CAPTURE_INTEGRATION_OK'
'@
    foreach($entry in @(@('__RUNNER__',$Script),@('__SOURCE__',$Source),@('__TEMPLATES__',$Templates),@('__ARTIFACT__',$Artifact))){
        $candidateChild=$candidateChild.Replace($entry[0],$entry[1].Replace("'","''"))
    }
    $candidateOutput=@(Invoke-FixtureChild ($candidateChild.Replace('__SUPPRESS__','').Replace('__ISOLATION_BYPASS__','')))
    Assert-Fixture ($LASTEXITCODE -eq 0) "Real outer/static intake integration failed: $($candidateOutput -join ' ')"
    Assert-Fixture (@($candidateOutput|Where-Object {"$_" -ceq 'ASRE_RELEASE_STATIC_CAPTURE_INTEGRATION_OK'}).Count -eq 1) 'Static intake outer integration proof missing.'
    Assert-Fixture (@($candidateOutput|Where-Object {"$_" -like 'ASRE_MONITOR_PUBLICATION_RECEIPT_V1=*'}).Count -eq 0) 'Capture fabricated a legacy receipt.'
    $raceChild=$prefix+@'
$global:OuterCaptureFixture=$true
$global:OuterCaptureRace=$true
$m=Microsoft.PowerShell.Management\Get-Content -LiteralPath '__SOURCE__/agent/workflows/azure-monitor-review.json' -Raw|ConvertFrom-Json -AsHashtable
$global:OuterResources['/api/v2/incidentManagement/incidentFilters/asre-azure-monitor-review']=$m.response_plan
$rejected=$false
try {$null=& '__RUNNER__' -Action PublishDisabled -SourceDirectory '__SOURCE__' -TemplateDirectory '__TEMPLATES__' -ArtifactDirectory '__ARTIFACT__'}
catch {if($_.Exception.Message -notlike '*newly active intake*'){throw};$rejected=$true}
if(-not $rejected -or $global:OuterFilterPatches -ne 0 -or
    -not $global:OuterResources['/api/v2/incidentManagement/incidentFilters/asre-azure-monitor-review'].properties.isEnabled){
    throw 'Concurrent activation was not preserved without a disabling PATCH'
}
Write-Output 'ASRE_RELEASE_CAPTURE_RACE_SAFE'
'@
    foreach($entry in @(@('__RUNNER__',$Script),@('__SOURCE__',$Source),@('__TEMPLATES__',$Templates),@('__ARTIFACT__',$Artifact))){
        $raceChild=$raceChild.Replace($entry[0],$entry[1].Replace("'","''"))
    }
    $raceOutput=@(Invoke-FixtureChild ($raceChild.Replace('__SUPPRESS__','').Replace('__ISOLATION_BYPASS__','')))
    Assert-Fixture ($LASTEXITCODE -eq 0) "Concurrent intake preservation failed: $($raceOutput -join ' ')"
    Assert-Fixture (@($raceOutput|Where-Object {"$_" -ceq 'ASRE_RELEASE_CAPTURE_RACE_SAFE'}).Count -eq 1) 'Concurrent intake preservation proof missing.'
    $unsupported=Get-Content -LiteralPath $legacyManifestPath -Raw|ConvertFrom-Json -AsHashtable
    $unsupported.native_capture.version='unsupported'
    $unsupported|ConvertTo-Json -Depth 40|Set-Content -LiteralPath $legacyManifestPath -Encoding utf8
    Expect-Rejected {& (Join-Path $Source 'scripts/Publish-IncidentWorkflow.ps1') -Action PublishDisabled -PublicationArtifactDirectory $Artifact} 'capture-declaration'
    Expect-Rejected {& (Join-Path $Repo 'scripts/Publish-IncidentWorkflow.ps1') -Action NativeCaptureEnable -PublicationRunId 0} 'ValidateRange|range|less than'
    Expect-Rejected {& (Join-Path $Repo 'scripts/Publish-IncidentWorkflow.ps1') -Action NativeCapturePreflight -PublicationRunId 102 -PublicationArtifactDirectory $Artifact} 'SchemaDiscovery-only'
    Write-Output "Incident release fixtures passed: $script:Passed assertions; local fixture only, no cloud/publication acceptance."
}
finally {
    foreach ($name in $Names) { [Environment]::SetEnvironmentVariable($name, $Original[$name]) }
    Remove-Item Function:\git, Function:\az, Function:\terraform
    if (Test-Path -LiteralPath $Temp) { Remove-Item -LiteralPath $Temp -Recurse -Force }
}
