$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repo = Split-Path $PSScriptRoot -Parent
. (Join-Path $repo 'scripts/ReviewAgent.Common.ps1')
# Default-adapter fixtures must not consume the real host's now-existing J.
function Get-MonitorBootstrapJournalDirectory { return Join-Path ([IO.Path]::GetTempPath()) 'asre-workflow-no-host-journal' }
$script:WorkflowHostReceipt = [IO.Path]::GetFullPath((Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'ASRE/native-bootstrap/asre-demo-cus/bootstrap-reconciliation.json'))
function Test-Path {
    [CmdletBinding()]
    param([string]$LiteralPath,[string]$Path,[string]$PathType)
    $candidate = $(if($PSBoundParameters.ContainsKey('LiteralPath')){$LiteralPath}else{$Path})
    if ($candidate -and [string]::Equals([IO.Path]::GetFullPath($candidate),$script:WorkflowHostReceipt,[StringComparison]::OrdinalIgnoreCase)) { return $false }
    Microsoft.PowerShell.Management\Test-Path @PSBoundParameters
}
# Retain explicit Discovery fixtures after the source advances to Production.
$script:CurrentMonitorManifest = ${function:Get-MonitorManifest}
$script:OriginalMonitorManifest = {
    param([string]$Root)
    $m = & $script:CurrentMonitorManifest -Root $Root
    $m.phase = 'SchemaDiscovery'
    $m.response_plan.properties.maxAutomatedInvestigationAttempts = 1
    $m.response_plan.properties.mergeEnabled = $true
    $m.response_plan.properties.priorities = @('Sev1')
    $m.response_plan.properties.Remove('azMonitorFilterSettings')
    $m.response_plan.properties.alertId = '/subscriptions/00000001-000a-4000-a000-000000000001/resourcegroups/rg-asre-control-cus/providers/microsoft.operationalinsights/workspaces/law-asre-cus/providers/Microsoft.AlertsManagement/alerts/00000047-000a-d000-0000-000000000047'
    return $m
}
function Get-MonitorManifest {
    param([string]$Root)
    $m = & $script:OriginalMonitorManifest -Root $Root
    $m.phase = 'Production'
    $m.response_plan.properties.maxAutomatedInvestigationAttempts = 3
    $m.response_plan.properties.mergeEnabled = $false
    $m.response_plan.properties.priorities = @('Sev1', 'Sev2')
    $m.response_plan.properties.azMonitorFilterSettings = @{
        targetResourceType = 'Microsoft.OperationalInsights/workspaces'
        targetResource = $m.scope.workspace_resource_id
    }
    $m.response_plan.properties.Remove('alertId')
    return $m
}
# All default-adapter tests are offline. Never fall through to the real CLI.
function az { throw 'Offline authentication fixture.' }
$script:Checks = 0
$script:Filter97 = $false
function Assert-Fixture {
    param([bool]$Condition, [string]$Message)
    $script:Checks++
    if (-not $Condition) { throw $Message }
}
function Assert-Rejected {
    param([scriptblock]$Run, [string]$Pattern)
    $rejected = $false
    $actualReason = 'No rejection.'
    try { & $Run | Out-Null } catch { $actualReason = $_.Exception.Message; $rejected = $actualReason -match $Pattern }
    Assert-Fixture $rejected "Expected fail-closed rejection: $Pattern; actual: $actualReason"
}
function Copy-Fixture {
    param($Value)
    return ConvertTo-Json -InputObject $Value -Depth 40 | ConvertFrom-Json -AsHashtable -DateKind String
}
function Reset-Fixture {
    $script:State = @{
        agentResourceId = '/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.App/agents/asre-demo-cus'
        mode = 'Review'; accessLevel = 'Low'
        platformConfiguration = @{ type = 'ServiceNow'; connectionName = 'retained-original'; oboUser = '' }
        permissions = @{ allow = @(); ask = @(); deny = @() }
        permissionsEtag = '"fixture-etag-1"'
        agentPermissions = @{ allow = @() }
        filters = @(@{ name = 'asre-servicenow-review-pilot'; type = 'IncidentFilter'; properties = @{ isEnabled = $false } })
    }
    $script:Resources = @{}
    $script:Operations = @()
    $script:CorruptResource = $false
    $script:CorruptNative = $false
    $script:FailEnableOnce = $false
    $script:RuntimeReady = $true
    $script:RuntimeFlagString = $false
    $script:WrongCommissioning = $false
}
$transport = {
    param($Operation, $Request)
    $script:Operations += $Operation
    switch ($Operation) {
        ReadState {
            $copy = Copy-Fixture $script:State
            if ($script:CorruptNative) {
                foreach ($f in $copy.filters) {
                    if ($f.name -ceq 'asre-azure-monitor-review') { $f.properties.handlingAgent = 'unrelated' }
                }
            }
            return $copy
        }
        PublishResource {
            $r = Copy-Fixture $Request.resource
            if ($r.name -ceq 'asre-azure-monitor-review' -and $r.kind -ceq 'incidentFilters' -and
                $r.properties.isEnabled -and $script:FailEnableOnce) {
                # Simulate a lost response AFTER the side effect.
                $script:FailEnableOnce = $false
                $script:State.filters = @($script:State.filters | Where-Object name -CNE $r.name) + @($r)
                $script:Resources["$($r.kind)/$($r.name)"] = $r
                throw 'fixture token=NEVER-ECHO-THIS'
            }
            $script:Resources["$($r.kind)/$($r.name)"] = $r
            if ($r.kind -ceq 'incidentFilters') {
                $script:State.filters = @($script:State.filters | Where-Object name -CNE $r.name) + @($r)
            }
            return $true
        }
        ReadResource {
            $r = Copy-Fixture $script:Resources["$($Request.kind)/$($Request.name)"]
            if ($script:CorruptResource) { $r.properties.unexpectedTool = 'shell' }
            return $r
        }
        SetPlatform { $script:State.platformConfiguration = Copy-Fixture $Request.configuration; return $true }
        SetAgentPermissions {
            $script:State.agentPermissions = Copy-Fixture $Request.permissions
            return $true
        }
        VerifyRuntime {
            $proofCommissioning = Copy-Fixture $Request.commissioning
            if ($script:WrongCommissioning) { $proofCommissioning.expires_at = $now.AddSeconds(800).ToString('o') }
            return @{
                status = $(if ($script:RuntimeReady) { 'verified' } else { 'blocked' })
                authoritative_reader_bound = $script:RuntimeReady; commissioning_bound = $script:RuntimeReady
                scanner_permissions_verified = $script:RuntimeReady
                selected_tools_no_approval = $(if ($script:RuntimeFlagString) { 'true' } else { $script:RuntimeReady })
                commissioning = $proofCommissioning
            }
        }
        default { throw "Unexpected external/mutation surface: $Operation" }
    }
}
$binding = @{ toolPatterns = @{} }
$d = Get-MonitorCaseDeclaration -Root $repo
foreach ($name in @('QueryLogAnalyticsByResourceId', 'ReadFile', 'SearchMemory', 'asre-review-evidence', $d.name)) {
    # Explicit MOCK pattern syntax. This is not claimed to be a native policy DTO.
    $binding.toolPatterns[$name] = "fixture:$name"
}
$now = [DateTimeOffset]::UtcNow
$commission = @{
    version = '1.0'
    not_before = $now.AddSeconds(-10).ToString('o'); expires_at = $now.AddSeconds(600).ToString('o')
    historical_alert_ids = @('fixture-historical-alert')
}
$protectedSource = & (Join-Path $repo $d.buildScript) -PassThru
$fixtureActivationWindow = @{ not_before = $now.AddMinutes(-1).ToString('o'); expires_at = $now.AddMinutes(8).ToString('o') }
$context = @{ Root = $repo; Transport = $transport; Binding = $binding
    ProtectedCaseSource = $protectedSource; ActivationWindow = $fixtureActivationWindow }
Reset-Fixture
$m = Get-MonitorManifest -Root $repo
Assert-Rejected { Invoke-IncidentWorkflow @context -Action Cutover -ProbeProfile @{ version = '2.0-connected' } } 'SchemaDiscovery-only'
$productionAgent = @(Get-MonitorReviewConfiguration -Root $repo -CaseToolSource $protectedSource |
    Where-Object kind -EQ agents)[0]
& {
    $readyConfiguration = Get-Content -LiteralPath (Join-Path $repo 'config/native-incident-runtime.json') -Raw |
        ConvertFrom-Json -AsHashtable -DateKind String
    $readyResources = @(Get-MonitorReviewConfiguration -Root $repo -CaseToolSource $protectedSource)
    $readyState = @{ permissions = @{ allow = @(); ask = @(); deny = @() }
        agentPermissions = Get-MonitorAgentPermissions; platformConfiguration = @{ type = 'AzMonitor' } }
    function Get-MonitorNativeState {
        param($Context)
        $Context.lastArm = @{ properties = @{ actionConfiguration = @{
            identity = (Get-MonitorScannerBinding).identity_resource_id } } }
        return $readyState
    }
    function Assert-MonitorConnected { param($Context, $State) }
    function Invoke-MonitorNativeRequest {
        param($Context, $Service, $Method, $Path)
        Assert-Fixture ($Service -ceq 'Sre' -and $Method -ceq 'Get') 'Production readiness attempted a write.'
        $resource = @($readyResources | Where-Object {
            $Path -ceq "/api/v2/extendedAgent/$($_.kind)/$($_.name)" })
        Assert-Fixture ($resource.Count -eq 1) 'Production readiness escaped its fixed resources.'
        return @{ data = $resource[0] }
    }
    $readyContext = @{ root = $repo }
    $ready = Test-MonitorProductionReadiness -Context $readyContext -Commissioning $readyConfiguration
    Assert-Fixture ($ready.status -ceq 'verified' -and -not $ready.runtime_accepted) 'Configuration readiness claimed business acceptance.'
    $readyState.agentPermissions.allow += 'RunShell'
    Assert-Rejected { Test-MonitorProductionReadiness $readyContext $readyConfiguration } 'production-agent-policy'
    $readyState.agentPermissions = Get-MonitorAgentPermissions
    $readyState.permissions.ask = @('asre-servicenow-case')
    Assert-Rejected { Test-MonitorProductionReadiness $readyContext $readyConfiguration } 'production-global-policy'
    $readyState.permissions.ask = @()
    $badConfiguration = Copy-Fixture $readyConfiguration
    $badConfiguration.not_before = '2026-10-19T00:00:00Z'
    Assert-Rejected { Test-MonitorProductionReadiness $readyContext $badConfiguration } 'frozen-production-commissioning'
}
& {
    $owned = @{}
    foreach ($r in @(Get-MonitorReviewConfiguration -Root $repo)) {
        $copy = Copy-Fixture $r
        if ($r.name -cne 'asre-review-evidence') { $copy.properties.description = 'Previous owned definition' }
        if ($r.name -ceq 'asre-review-evidence') { $copy.properties.functionCode = 'unit-retained-installed-definition' }
        if ($r.kind -ceq 'agents') { $copy.properties.permissions = @{ allow = @() } }
        $owned["/api/v2/extendedAgent/$($r.kind)/$($r.name)"] = $copy
    }
    $policyPath = '/api/v2/extendedAgent/asre-azure-monitor-review/permissions'
    $owned[$policyPath] = @{ allow = @() }
    $route = Copy-Fixture (Get-MonitorManifest -Root $repo).response_plan
    $route.properties.isEnabled = $true
    $routePath = '/api/v2/incidentManagement/incidentFilters/asre-azure-monitor-review'
    $owned[$routePath] = $route
    $writes = [Collections.Generic.List[string]]::new()
    $context = @{ root = $repo; action = 'PublishHandler'; authorityAction = 'PublishHandler'
        readOnly = $false; endpoint = 'https://unit.invalid'; sreHeaders = @{}; agent = '/unit-owned-agent' }
    function Get-MonitorNativeContext { param($Action, $Root); return $context }
    function Get-MonitorNativeState {
        param($Context)
        $Context.lastArm = @{ properties = @{ actionConfiguration = @{
            identity = (Get-MonitorScannerBinding).identity_resource_id } } }
        return @{ permissions = @{ allow = @(); ask = @(); deny = @() }; filters = @($owned[$routePath])
            agentPermissions = ConvertTo-MonitorAgentPermissions $owned[$policyPath]
            platformConfiguration = @{ type = 'AzMonitor' } }
    }
    function Assert-MonitorConnected { param($Context, $State) }
    function Invoke-WebRequest {
        [CmdletBinding()]
        param([uri]$Uri, $Method, $Headers, $MaximumRedirection, $TimeoutSec,
            [switch]$SkipHttpErrorCheck, $Body, $ContentType)
        $path = $Uri.AbsolutePath
        Assert-Fixture ($owned.ContainsKey($path)) 'Handler transport escaped its owned resources.'
        if ($Method -ceq 'Put') {
            $writes.Add($path)
            if ($path -ceq $policyPath) {
                Assert-Fixture ($Headers['If-Match'] -ceq '"unit-policy-revision"') 'Policy PUT lost its actual revision.'
            } else {
                Assert-Fixture (-not $Headers.ContainsKey('If-Match')) 'Revisionless content claimed atomic CAS.'
            }
            $owned[$path] = $Body | ConvertFrom-Json -AsHashtable -DateKind String
            $agentPath = '/api/v2/extendedAgent/agents/asre-azure-monitor-review'
            if ($path -ceq $policyPath -or $path -ceq $agentPath) {
                $owned[$agentPath].properties.permissions = $owned[$policyPath]
            }
        } elseif ($Method -ceq 'Patch') {
            Assert-Fixture ($path -ceq $routePath) 'Profile expansion escaped the existing owned route.'
            Assert-Fixture ($Headers['If-Match'] -ceq '"unit-route-revision"') 'Expansion lost the actual route revision.'
            $replacement = $Body | ConvertFrom-Json -AsHashtable -DateKind String
            Assert-Fixture ($replacement.properties.isEnabled -is [bool] -and $replacement.properties.isEnabled) 'Expansion disabled native intake.'
            Assert-Fixture ($replacement.properties.titleContains -ceq 'asre-demo-') 'Expansion did not retain its closed source title.'
            Assert-Fixture (($replacement.properties.priorities -join ',') -ceq 'Sev1,Sev2') 'Expansion did not retain the exact released severities.'
            $writes.Add($path)
            $owned[$path] = $replacement
        }
        return @{ StatusCode = 200; Content = ConvertTo-Json -InputObject $owned[$path] -Depth 40
            Headers = $(if ($path -ceq $policyPath) { @{ ETag = '"unit-policy-revision"' } }
                elseif ($path -ceq $routePath) { @{ ETag = '"unit-route-revision"' } } else { @{} }) }
    }
    $journal = Join-Path ([IO.Path]::GetTempPath()) "asre-handler-$([guid]::NewGuid()).jsonl"
    try {
        $result = Publish-MonitorHandler -JournalPath $journal -Root $repo
        Assert-Fixture ($result.status -ceq 'verified' -and -not $result.runtime_accepted) 'Handler publication claimed case acceptance.'
        Assert-Fixture ($writes.Count -eq 7 -and $writes[-1] -ceq '/api/v2/extendedAgent/agents/asre-azure-monitor-review') 'Handler must be published last.'
        Assert-Fixture ($route.properties.isEnabled) 'Handler update disabled intake.'
        Assert-Rejected { Invoke-MonitorNativeRequest $context Sre Put $writes[0] -Body @{} } 'one journaled update'
        Assert-Rejected { Invoke-MonitorNativeRequest $context Sre Patch '/api/v2/incidentManagement/incidentFilters/asre-azure-monitor-review' -Body @{} } 'one journaled update'
        Assert-Fixture ($writes.Count -eq 7) 'Rejected publication performed a write.'
        $records = @(Get-Content -LiteralPath $journal | ForEach-Object { $_ | ConvertFrom-Json -AsHashtable })
        Assert-Fixture (@($records | Where-Object event -CEQ 'SubmitOnce').Count -eq 7) 'Publication did not retain every submit intent.'
        foreach ($legacyTitle in @('asre-demo-disk', 'asre-demo-')) {
            $owned[$routePath].properties.titleContains = $legacyTitle
            $owned[$routePath].properties.priorities = @('Sev1')
            $priorWrites = $writes.Count
            $expandedJournal = Join-Path ([IO.Path]::GetTempPath()) "asre-profile-expansion-$([guid]::NewGuid()).jsonl"
            try {
                $expanded = Publish-MonitorHandler -JournalPath $expandedJournal -Root $repo -ExpandProfiles
                Assert-Fixture ($expanded.status -ceq 'verified' -and -not $expanded.runtime_accepted) 'Profile publication claimed terminal failure/case acceptance.'
                Assert-Fixture ($writes.Count -eq $priorWrites + 1 -and $writes[-1] -ceq $routePath) 'Only one existing enabled-route update was allowed.'
                Assert-Fixture ($owned[$routePath].properties.isEnabled) 'Expansion interrupted existing intake.'
                $expandedRecords = @(Get-Content -LiteralPath $expandedJournal | ForEach-Object { $_ | ConvertFrom-Json -AsHashtable })
                Assert-Fixture (@($expandedRecords | Where-Object event -CEQ 'SubmitOnce').Count -eq 1) 'Expansion did not journal its one route intent.'
            } finally { Remove-Item -LiteralPath $expandedJournal -Force -ErrorAction SilentlyContinue }
        }
    } finally { Remove-Item -LiteralPath $journal -Force -ErrorAction SilentlyContinue }
}
$hookCommand = ". '$($repo.Replace("'", "''"))\scripts\ReviewAgent.Common.ps1'; Get-MonitorObjectSha256 (Get-MonitorHook -Root '$($repo.Replace("'", "''"))')"
$hookHashes = @(1..2 | ForEach-Object {
    $hash = & pwsh -NoProfile -Command $hookCommand
    Assert-Fixture ($LASTEXITCODE -eq 0 -and $hash -cmatch '^[0-9a-f]{64}$') 'Fresh-process hook build failed.'
    $hash
})
Assert-Fixture ($hookHashes.Count -eq 2 -and $hookHashes[0] -ceq $hookHashes[1]) 'Hook source must be deterministic across publisher processes.'
Assert-Fixture (-not $m.response_plan.properties.isEnabled -and $m.response_plan.properties.incidentPlatform -ceq 'AzMonitor' -and
    $m.response_plan.properties.mergeWindowHours -eq 3) 'Monitor source defaults are not disabled/narrow.'
Assert-Rejected { Invoke-IncidentWorkflow -Root $repo } 'Fixed Monitor authentication failed'
$preflight = Invoke-IncidentWorkflow @context
Assert-Fixture ($preflight.status -ceq 'preflight' -and ($script:Operations -join ',') -ceq 'ReadState') 'Preflight changed settings.'
Assert-Rejected { Assert-MonitorExact $false 'False' } 'Strict readback'
Assert-Rejected { Assert-MonitorExact @() $null } 'Strict readback'
Assert-Rejected { Assert-MonitorExact @{ tools = @('ReadFile') } @{ tools = @('ReadFile'); shell = $true } } 'Strict readback'
Assert-Rejected { Assert-MonitorExact @{ tools = @('ReadFile') } @{ Tools = @('ReadFile') } } 'Strict readback'
$script:State.agentResourceId = '/unrelated'
Assert-Rejected { Invoke-IncidentWorkflow @context } 'Review/Low'
$script:State.agentResourceId = '/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.App/agents/asre-demo-cus'
$oldFilters = $script:State.filters
$script:State.filters = @(@{ name = 'flat'; type = 'IncidentFilter'; isEnabled = $false })
Assert-Rejected { Invoke-IncidentWorkflow @context } 'properties|resource envelopes'
$script:State.filters = @($oldFilters[0], $oldFilters[0])
Assert-Rejected { Invoke-IncidentWorkflow @context } 'Duplicate'
$script:State.filters = $oldFilters
$result = Invoke-IncidentWorkflow @context -Action PublishDisabled
Assert-Fixture ($result.status -ceq 'published-disabled' -and -not $result.runtime_accepted) 'Disabled publication claimed live acceptance.'
Assert-Fixture ($script:Resources.Count -eq 8) 'Expected two tools, four native skills, one agent and one response plan.'
Assert-Fixture (-not ($script:Operations | Where-Object { $_ -in @('SetPlatform','SetPermissions','VerifyRuntime') })) 'Disabled publication performed intake cutover.'
Assert-Fixture ($script:State.platformConfiguration.type -ceq 'ServiceNow') 'Disabled publication changed the retained incoming platform.'
$null = Invoke-IncidentWorkflow @context -Action PublishDisabled
Assert-Fixture ($script:Resources.Count -eq 8 -and -not $script:State.filters[-1].properties.isEnabled) 'Repeat publication duplicated or enabled resources.'
$script:CorruptResource = $true
Assert-Rejected { Invoke-IncidentWorkflow @context -Action PublishDisabled } 'Strict readback'
$script:CorruptResource = $false
$script:CorruptNative = $true
Assert-Rejected { Invoke-IncidentWorkflow @context -Action PublishDisabled } 'Strict readback'
$script:CorruptNative = $false
$snapshot = (Invoke-IncidentWorkflow @context).snapshot
Assert-Rejected { Invoke-IncidentWorkflow @context -Action Cutover } 'snapshot'
$script:State.filters[0].properties.isEnabled = $true
Assert-Rejected { Invoke-IncidentWorkflow @context -Action Cutover -Snapshot $snapshot } 'overlapping'
$script:State.filters[0].properties.isEnabled = $false
$badBinding = Copy-Fixture $binding
$badBinding.toolPatterns[$d.name] = '*'
Assert-Rejected { Invoke-IncidentWorkflow -Root $repo -Transport $transport -Binding $badBinding -Action Cutover -Snapshot $snapshot `
    -ProtectedCaseSource $protectedSource -ActivationWindow $fixtureActivationWindow } 'wildcards'
$script:State.accessLevel = 'High'
Assert-Rejected { Invoke-IncidentWorkflow @context } 'Review/Low'
$script:State.accessLevel = 'Low'
$badSnapshot = Copy-Fixture $snapshot
$badSnapshot.permissionsEtag = 'stale'
Assert-Rejected { Invoke-IncidentWorkflow @context -Action Cutover -Snapshot $badSnapshot } 'Strict readback'
$script:State.permissionsEtag = ''
$withoutEtag = Copy-Fixture $script:State
Assert-Rejected { Invoke-IncidentWorkflow @context -Action Cutover -Snapshot $withoutEtag } 'ETag'
# Cutover failure can leave the scanner selected, but the response plan MUST
# remain disabled. Re-read and reconcile; never blindly retry unknown outcomes.
Assert-Fixture (-not $script:State.filters[-1].properties.isEnabled) 'Failed cutover activated the plan.'
$script:State = Copy-Fixture $snapshot
$result = Invoke-IncidentWorkflow @context -Action Cutover -Snapshot $snapshot
Assert-Fixture ($result.status -ceq 'cutover-disabled' -and $script:State.platformConfiguration.type -ceq 'AzMonitor') 'Explicit cutover did not verify native selection.'
Assert-Fixture (-not $script:State.filters[-1].properties.isEnabled -and $script:State.permissions.ask.Count -eq 0) 'Cutover enabled intake or retained runtime asks.'
Assert-Rejected { Invoke-IncidentWorkflow @context -Action Enable } 'commissioning'
$expired = Copy-Fixture $commission
$expired.expires_at = $now.AddSeconds(-1).ToString('o')
Assert-Rejected { Invoke-IncidentWorkflow @context -Action Enable -Commissioning $expired } 'Commissioning'
$historical = Copy-Fixture $commission
$historical.not_before = ([DateTimeOffset]::Parse($m.commissioning.minimum_not_before)).AddMilliseconds(-1).ToString('o')
$historical.expires_at = ([DateTimeOffset]::Parse($historical.not_before)).AddSeconds(900).ToString('o')
Assert-Rejected { Invoke-IncidentWorkflow @context -Action Enable -Commissioning $historical } 'Commissioning'
Assert-Rejected {
    Assert-MonitorCommissioning -Commissioning $historical -Root $repo `
        -Now ([DateTimeOffset]::Parse($m.commissioning.minimum_not_before)).AddSeconds(100)
} 'Commissioning'
$long = Copy-Fixture $commission
$long.not_before = $m.commissioning.minimum_not_before
$long.Remove('expires_at')
Assert-MonitorCommissioning -Commissioning $long -Root $repo
Assert-Fixture $true 'Persistent commissioning without expiry was rejected.'
$long.expires_at = $now.AddDays(30).ToString('o')
Assert-MonitorCommissioning -Commissioning $long -Root $repo
Assert-Fixture $true 'Optional long reviewed expiry was confused with guest TTL.'
Assert-Rejected { Assert-MonitorActivationWindow @{ not_before = $now.AddMinutes(-5).ToString('o'); expires_at = $now.AddSeconds(-1).ToString('o') } } 'authority window'
$script:RuntimeReady = $false
Assert-Rejected { Invoke-IncidentWorkflow @context -Action Enable -Commissioning $commission } 'prerequisites'
$script:RuntimeReady = $true
$script:RuntimeFlagString = $true
Assert-Rejected { Invoke-IncidentWorkflow @context -Action Enable -Commissioning $commission } 'Boolean'
$script:RuntimeFlagString = $false
$script:WrongCommissioning = $true
Assert-Rejected { Invoke-IncidentWorkflow @context -Action Enable -Commissioning $commission } 'Strict readback'
$script:WrongCommissioning = $false
$script:CorruptResource = $true
Assert-Rejected { Invoke-IncidentWorkflow @context -Action Enable -Commissioning $commission } 'Strict readback'
$script:CorruptResource = $false
$script:State.permissions.ask = @("fixture:$($d.name)")
Assert-Rejected { Invoke-IncidentWorkflow @context -Action Enable -Commissioning $commission } 'Strict readback'
$script:State.permissions.ask = @()
$script:FailEnableOnce = $true
Assert-Rejected { Invoke-IncidentWorkflow @context -Action Enable -Commissioning $commission } 'independently paused'
Assert-Fixture (-not $script:State.filters[-1].properties.isEnabled) 'Unknown activation outcome was not reconciled to paused.'
$result = Invoke-IncidentWorkflow @context -Action Enable -Commissioning $commission
Assert-Fixture ($result.status -ceq 'enabled' -and $script:State.filters[-1].properties.isEnabled -and -not $result.runtime_accepted) 'Explicit activation fixture did not enable the exact plan.'
Assert-Rejected { Invoke-IncidentWorkflow @context -Action PublishDisabled } 'Pause'
$result = Invoke-IncidentWorkflow @context -Action Pause
Assert-Fixture ($result.status -ceq 'paused' -and -not $script:State.filters[-1].properties.isEnabled) 'Pause did not read back disabled.'
Assert-Rejected { Invoke-IncidentWorkflow @context -Action Rollback } 'snapshot'
$script:State.filters[0].properties.isEnabled = $true
Assert-Rejected { Invoke-IncidentWorkflow @context -Action Rollback -Snapshot $snapshot } 'overlapping'
$script:State.filters[0].properties.isEnabled = $false
$result = Invoke-IncidentWorkflow @context -Action Rollback -Snapshot $snapshot
Assert-Fixture ($result.status -ceq 'rolled-back-paused' -and -not $script:State.filters[-1].properties.isEnabled) 'Rollback reenabled a historical route.'
Assert-MonitorExact $snapshot.platformConfiguration $script:State.platformConfiguration 'restored-platform'
Assert-MonitorExact $snapshot.permissions $script:State.permissions 'restored-policies'
Assert-Rejected { Invoke-MonitorTransport { throw 'password=do-not-log' } ReadState } '^Monitor adapter failed during ReadState; outcome may be unknown'
Assert-Fixture (@($script:Operations | Where-Object { $_ -notin @('ReadState','PublishResource','ReadResource','SetPlatform','SetAgentPermissions','VerifyRuntime') }).Count -eq 0) 'Lifecycle exposed case write, fault, repair or generic execution.'
Write-Output "$($script:Checks) incident workflow checks passed; mocked transport only, no external actions or runtime acceptance."
Set-Item -Path function:Get-MonitorManifest -Value $script:OriginalMonitorManifest

# Exercise the real default adapter and public entry point. Only CLI/HTTP are
# replaced; identity/path/default-projection/publication logic is production.
$script:CliUserType = 'user'
$script:CliUserName = 'fixture-user'
$script:NativeCalls = @()
$script:NativeResources = @{}
$script:NativePlatform = @{ type = 'ServiceNow'; connectionName = 'retained-native'; oboUser = '' }
$script:NativeGlobal = @{ permissions = @{ allow = @(); ask = @(); deny = @() } }
$script:DiscoveryGlobalRevision = $null
$script:DiscoveryPublicationDrift = ''
$script:NativeAgentPolicy = @{ allow = @(); ask = @(); deny = @() }
$script:NativeFilter = $null
$script:WrongEndpoint = $false
$script:HttpFailure = 0
$script:DiagnosticFault = $null
$script:DiagnosticSecret = 'MONITOR-DIAGNOSTIC-SECRET-SENTINEL'
$script:CliClaims = $null
$script:CapabilityAvailable = $true
$script:ScannerPrincipal = (Get-MonitorScannerBinding).principal_id
$script:ScannerIdentity = (Get-MonitorScannerBinding).identity_resource_id
$script:ScannerRole = (Get-MonitorScannerBinding).role_definition_id
$script:ScannerRoleScope = (Get-MonitorScannerBinding).scope
$script:ScannerRoleName = 'Monitoring Contributor'
$script:ScannerAssignmentsPaged = $false
$script:ScannerGrantPresent = $true
$script:PrimaryCondition = 'Fired'
$script:SecondaryCondition = 'Resolved'
$script:ExpireDuringEnable = $false
$script:NativeNow = $null
$script:ExpireBeforeEnable = $false
$script:ExtendCallerWindow = $false
$script:NearExpiry = $false
$script:NearExpirySeconds = 7.5
$script:ExpireInPlatformReads = $false
$script:PlatformGlobalReads = 0
function Get-MonitorUtcNow {
    if ($null -ne $script:NativeNow) { return $script:NativeNow }
    return [DateTimeOffset]::UtcNow
}
function az {
    $global:LASTEXITCODE = 0
    if (($args -join ' ') -like 'account show *') {
        return @{
            id = '00000001-000a-4000-a000-000000000001'
            tenantId = '00000002-000a-4000-9000-000000000002'
            user = @{ type = $script:CliUserType; name = $script:CliUserName }
        } | ConvertTo-Json -Compress
    }
    if (($args -join ' ') -like 'account get-access-token *') {
        if ($script:CliClaims) {
            $claims = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($script:CliClaims | ConvertTo-Json -Compress))).TrimEnd('=').Replace('+','-').Replace('/','_')
            return "eyJhbGciOiJub25lIn0.$claims.offline"
        }
        return 'offline-fixture-token'
    }
    throw 'The default adapter attempted an unexpected CLI operation.'
}
function Add-NativeHookDefaults {
    param([hashtable]$HookEvents)
    # Independently observed nested command DTO hydration, not expected output.
    foreach ($hooks in $HookEvents.Values) {
        foreach ($hook in $hooks) {
            foreach ($key in @('prompt','command','model','maxRejections','sources')) {
                if (-not $hook.ContainsKey($key)) { $hook[$key] = $null }
            }
        }
    }
}
function Invoke-WebRequest {
    param($Uri, $Method, $Headers, $MaximumRedirection, $TimeoutSec, $ErrorAction,
        $SkipHttpErrorCheck, $Body, $ContentType)
    $script:NativeCalls += @{ uri = $Uri; method = $Method; ifMatch = $Headers['If-Match']; timeout = $TimeoutSec; body = $Body }
    if ($MaximumRedirection -ne 0 -or $TimeoutSec -lt 1 -or $TimeoutSec -gt 60 -or -not $SkipHttpErrorCheck) {
        throw 'Native request dropped bounded/no-redirect safeguards.'
    }
    $code = 200
    $data = $null
    $path = ([uri]$Uri).AbsolutePath
    if ($script:DiagnosticFault -and $path -ceq $script:DiagnosticFault.path -and
        $Method -ceq $script:DiagnosticFault.method) {
        switch ($script:DiagnosticFault.kind) {
            Dns { throw [Net.Http.HttpRequestException]::new($script:DiagnosticSecret, [Net.Sockets.SocketException]::new(11001)) }
            Timeout { throw [TimeoutException]::new($script:DiagnosticSecret) }
            Other { throw [InvalidOperationException]::new($script:DiagnosticSecret, [Exception]::new($script:DiagnosticSecret)) }
        }
        return @{
            StatusCode = $script:DiagnosticFault.status
            Content = $(if ($script:DiagnosticFault.ContainsKey('body')) { $script:DiagnosticFault.body }
            elseif ($script:DiagnosticFault.kind -ceq 'Json') {
                "{ invalid $($script:DiagnosticSecret)"
            } else { '{"secret":"' + $script:DiagnosticSecret + '"}' })
            Headers = @{ ETag = $script:DiagnosticSecret; Authorization = $script:DiagnosticSecret }
        }
    }
    if ($Method -ceq 'Get' -and $path -ceq '/api/v2/incidentManagement/incidentFilters/asre-azure-monitor-review') {
        if ($script:ExpireBeforeEnable) {
            $script:ExpireBeforeEnable = $false
            $script:NativeNow = [DateTimeOffset]::Parse($fixtureActivationWindow.expires_at).AddSeconds(1)
            if ($script:ExtendCallerWindow) {
                $fixtureActivationWindow.expires_at = $script:NativeNow.AddHours(1).ToString('o')
            }
        } elseif ($script:NearExpiry) {
            $script:NearExpiry = $false
            $script:NativeNow = [DateTimeOffset]::Parse($fixtureActivationWindow.expires_at).AddSeconds(-$script:NearExpirySeconds)
        }
    }
    if ($script:HttpFailure) { $code = $script:HttpFailure; $data = @{ error = 'DO-NOT-ECHO-FIXTURE-DETAILS' } }
    elseif ($path.EndsWith('/providers/Microsoft.Authorization/permissions')) {
        if ($Method -cne 'Get') { throw 'Coordinator capability verification must be GET-only.' }
        $data = @{ value = @() }
        if ($script:CapabilityAvailable) {
            $data.value = @(@{ actions = @('Microsoft.App/agents/write'); notActions = @()
                dataActions = @('Microsoft.App/agents/*/read','Microsoft.App/agents/*/write','Microsoft.App/agents/threads/approve/action')
                notDataActions = @() })
        }
    }
    elseif ($path.EndsWith('/providers/Microsoft.Authorization/roleAssignments')) {
        if ($Method -cne 'Get') { throw 'Readiness must never grant scanner permissions.' }
        $data = @{ value = @(@{
            id = "$((Get-MonitorScannerBinding).scope)/providers/Microsoft.Authorization/roleAssignments/11111111-2222-3333-4444-555555555555"
            properties = @{
                principalId = $script:ScannerPrincipal; principalType = 'ServicePrincipal'
                roleDefinitionId = $script:ScannerRole; scope = $script:ScannerRoleScope
                condition = $null; conditionVersion = $null
            }
        }); nextLink = $(if ($script:ScannerAssignmentsPaged) { 'https://unrelated.invalid/page2' } else { $null }) }
        if (-not $script:ScannerGrantPresent) { $data.value = @() }
    }
    elseif ($path -like '*/providers/Microsoft.Authorization/roleDefinitions/*') {
        if ($Method -cne 'Get') { throw 'Readiness must never change a role definition.' }
        $data = @{
            id = (Get-MonitorScannerBinding).role_definition_id
            properties = @{ roleName = $script:ScannerRoleName; type = 'BuiltInRole'
                permissions = @(@{ actions = @('*/read','Microsoft.AlertsManagement/alerts/*')
                    notActions = @(); dataActions = @(); notDataActions = @() }) }
        }
    }
    elseif ($path -like '*/providers/Microsoft.AlertsManagement/alerts/*') {
        if ($Method -cne 'Get') { throw 'Readiness must never acknowledge/close/create an alert.' }
        $m = Get-MonitorManifest -Root $repo
        $data = @{ id = $path; type = 'microsoft.alertsmanagement/alerts'
            properties = @{ essentials = @{
                monitorCondition = $(if ($path.EndsWith('00000047-000a-d000-0000-000000000047')) {
                    $script:PrimaryCondition } else { $script:SecondaryCondition })
                alertState = 'New'; actionStatus = @{ isSuppressed = $false }
                alertRule = $m.scope.rule_id; targetResource = $m.scope.workspace_resource_id
                severity = 'Sev1'; signalType = 'Log'; monitorService = 'Log Alerts V2'
            } }
        }
    }
    elseif ($Uri.StartsWith('https://management.azure.com/')) {
        if ($Method -cne 'Get') {
            if ($Headers['If-Match'] -cne '"arm-fixture"') { throw 'ARM PATCH missed ETag.' }
            $request = $Body | ConvertFrom-Json -AsHashtable -DateKind String
            $script:NativePlatform = $request.properties.incidentManagementConfiguration
        }
        $data = @{
            id = '/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.App/agents/asre-demo-cus'
            identity = @{ userAssignedIdentities = @{
                $script:ScannerIdentity = @{ principalId = $script:ScannerPrincipal }
            } }
            properties = @{
                agentEndpoint = $(if ($script:WrongEndpoint) { 'https://unrelated.invalid' } else {
                    'https://asre-demo-cus--0a1b2c3d.4e5f6a7b.centralus.azuresre.ai' })
                actionConfiguration = @{ mode = 'Review'; accessLevel = 'Low'; identity = $script:ScannerIdentity }
                incidentManagementConfiguration = $script:NativePlatform
            }
        }
    }
    elseif ($path -ceq '/api/v2/agent/settings/global') {
        if ($script:ExpireInPlatformReads) {
            $script:PlatformGlobalReads++
            if ($script:PlatformGlobalReads -eq 3) {
                $script:ExpireInPlatformReads = $false
                $script:NativeNow = [DateTimeOffset]::Parse($fixtureActivationWindow.expires_at).AddSeconds(1)
            }
        }
        if ($Method -cne 'Get') { throw 'Global policies must never be mutated.' }
        $data = $script:NativeGlobal
    }
    elseif ($path -ceq '/api/v2/incidentManagement/checkConnectivityDetailed') {
        if ($Method -cne 'Get') { throw 'Connectivity is GET-only.' }
        $data = @{ success = $true; incidentType = $script:NativePlatform.type }
    }
    elseif ($path -ceq '/api/v2/incidentManagement/incidentFilters') {
        $data = @{ value = @(); nextLink = $null }
        if ($script:NativeFilter) { $data.value = @($script:NativeFilter) }
    }
    elseif ($path -ceq '/api/v2/extendedAgent/asre-azure-monitor-review/permissions') {
        if ($Method -ceq 'Put') {
            $expectedEtag = $(if($script:Filter97){'"00000059-000a-0000-0000-000000000059"'}else{'"global-fixture"'})
            if ($Headers['If-Match'] -cne $expectedEtag) { throw 'Agent policy PUT omitted its actual read ETag.' }
            $p = $Body | ConvertFrom-Json -AsHashtable -DateKind String
            Assert-MonitorExact (Get-MonitorAgentPermissions) $p 'real-agent-policy-DTO'
            $script:NativeAgentPolicy = @{ allow = $p.allow; ask = @(); deny = @() }
            if ($script:DiscoveryPublicationDrift -ceq 'policy') {
                $script:NativeGlobal.permissions.ask = @('fixture-policy-change')
            } elseif ($script:DiscoveryPublicationDrift -ceq 'revision') {
                $script:DiscoveryGlobalRevision = '"fixture-transaction-change"'
            }
        }
        $data = $script:NativeAgentPolicy
    }
    else {
        if ($Method -in @('Put', 'Patch')) {
            $r = $Body | ConvertFrom-Json -AsHashtable -DateKind String
            if ($path -like '/api/v2/incidentManagement/incidentFilters/*') {
                if (($null -eq $script:NativeFilter -and $Method -cne 'Put') -or
                    ($null -ne $script:NativeFilter -and $Method -cne 'Patch')) { throw 'Incorrect native filter create/update verb.' }
                # Full observed native-filter defaults.
                foreach ($k in @('name','impactedService','state','category','subcategory','configurationItem',
                    'callerId','location','impact','urgency','severity','incidentType','alertId','owningTeamId')) {
                    if (-not $r.properties.ContainsKey($k)) { $r.properties[$k] = '' }
                }
                foreach ($k in @('assignmentGroups','customFields','incidentTypes','titleContainsAll',
                    'titleContainsAny','titleNotContains','owningTeamIds')) { $r.properties[$k] = @() }
                foreach ($k in @('handlingAgents','icmFilterSettings','configurationWarning')) { $r.properties[$k] = $null }
                if (-not $r.properties.ContainsKey('azMonitorFilterSettings') -and $r.properties.incidentPlatform -ceq 'AzMonitor') {
                    $r.properties.azMonitorFilterSettings = @{ targetResourceType = ''; targetResource = '' }
                } elseif (-not $r.properties.ContainsKey('azMonitorFilterSettings')) {
                    $r.properties.azMonitorFilterSettings = $null
                }
                $r.properties.requiresServiceNowPlatformFeatures = $false
                $r.properties.etag = '"filter-fixture"'
                $script:NativeFilter = $r
                if ($r.properties.isEnabled -and $script:ExpireDuringEnable) {
                    $script:ExpireDuringEnable = $false
                    $fixtureActivationWindow.expires_at = [DateTimeOffset]::UtcNow.AddSeconds(-1).ToString('o')
                }
            } elseif ($r.type -ceq 'ExtendedAgentTool') {
                foreach ($k in @('dependencies','authScopes','connector','attributes')) {
                    if (-not $r.properties.ContainsKey($k)) { $r.properties[$k] = $null }
                }
                $r.properties.authEnabled = $false; $r.properties.toolMode = 'Auto'
            } elseif ($r.type -ceq 'ExtendedAgent') {
                foreach ($k in @('connectors','allowParallelToolCalls','agentsAsTools','maxReflectionCount',
                    'criticPromptPath','criticOnHandOff','customReflectionNote','commonPrompts',
                    'disableDocumentRetrieval','enableHandoffPromptOverride','userPromptOverride',
                    'handoffPromptOverride','instructionsOverride','temperature','llmModelName',
                    'addSystemSkills','outputType','permissions')) {
                    if (-not $r.properties.ContainsKey($k)) { $r.properties[$k] = $null }
                }
                $r.properties.enableVanillaMode = $false
                Add-NativeHookDefaults $r.properties.hooks
            }
            $script:NativeResources[$path] = $r
        }
        if ($script:NativeResources.ContainsKey($path)) { $data = $script:NativeResources[$path] }
        else { $code = 404 }
    }
    return @{
        StatusCode = $code; Content = $(if ($null -ne $data) { ConvertTo-Json -InputObject $data -Depth 40 } else { '' })
        Headers = @{ ETag = $(if($path -ceq '/api/v2/incidentManagement/incidentFilters/asre-azure-monitor-review' -and
            $null -ne $data -and $data.properties.ContainsKey('etag')){$data.properties.etag}
        elseif($path -ceq '/api/v2/agent/settings/global' -and $null -ne $script:DiscoveryGlobalRevision){
            $script:DiscoveryGlobalRevision
        } elseif($script:Filter97){
            if($path -ceq '/api/v2/extendedAgent/asre-azure-monitor-review/permissions'){'"00000059-000a-0000-0000-000000000059"'}
            elseif($Uri.StartsWith('https://management.azure.com/')){''}
            else{'"00000056-000a-0000-0000-000000000056"'}
        } elseif ($Uri.StartsWith('https://management.azure.com/')) { '"arm-fixture"' } else { '"global-fixture"' }) }
    }
}
function Test-BootstrapFixtures {
    param([string]$ArtifactDirectory, [hashtable]$ArtifactReceipt)
    $startChecks = $script:Checks
    $filter97SavedEnvironment = @{}
    $sandbox = Join-Path ([IO.Path]::GetTempPath()) ("asre-bootstrap-fixture-" + [guid]::NewGuid().ToString('N'))
    $null = [IO.Directory]::CreateDirectory($sandbox)
    $script:BootstrapSandbox = $sandbox
    # Actual captured ServiceNow fields; alias and API resource name differ.
    # Azure Monitor responses below remain explicitly synthetic.
    $script:BootstrapOriginalPlatform = @{ type = 'ServiceNow'; connectionName = 'servicenow-oauth'
        connectionUrl = 'https://example-instance.service-now.com/'; connectionKey = ''
        oboUser = 'sreagent-config-revision:00000030-000a-4000-9000-000000000030'
        apiConnectionName = 'asre-demo-cus-servicenow-buq5o4th4p' }
    $script:BootstrapOriginalConnectivity = @{ success = $true; errorMessage = $null; incidentType = 'ServiceNow'
        observedApiConnectionName = 'asre-demo-cus-servicenow-buq5o4th4p'
        observedConfigurationRevision = '00000030-000a-4000-9000-000000000030'; observedConnectionStatus = 'Connected' }
    function Get-MonitorBootstrapJournalDirectory { return Join-Path $script:BootstrapSandbox 'journal' }
    function Get-MonitorUtcNow { return $script:NativeNow }
    function Start-Sleep { param($Milliseconds) $script:BootstrapSleeps++; $script:NativeNow = $script:NativeNow.AddMilliseconds($Milliseconds) }
    # Exact nonsecret retained archive bytes, not a synthesized hydrated DTO.
    $archive64 = 'REDACTED-ENCODED-LAB-PAYLOAD'
    $writable64 = 'REDACTED-ENCODED-LAB-PAYLOAD'
    $qualification64 = 'REDACTED-ENCODED-LAB-PAYLOAD'
    foreach ($entry in @(@('archive',$archive64),@('writable',$writable64),@('qualification',$qualification64))) {
        [IO.File]::WriteAllBytes((Join-Path $sandbox "$($entry[0]).json"), [Convert]::FromBase64String($entry[1]))
    }
    $script:BootstrapPilot = (Get-Content -LiteralPath (Join-Path $sandbox 'archive.json') -Raw | ConvertFrom-Json -AsHashtable).body |
        ConvertFrom-Json -AsHashtable -DateKind String
    $originalHttp = ${function:Invoke-WebRequest}
    function Invoke-WebRequest {
        param($Uri, $Method, $Headers, $MaximumRedirection, $TimeoutSec, $ErrorAction, $SkipHttpErrorCheck, $Body, $ContentType)
        $path = ([uri]$Uri).AbsolutePath
        $ownedArm = $path -ceq $script:BootstrapAgent
        $legacy = $path -ceq '/api/v2/incidentManagement/incidentFilters/asre-servicenow-review-pilot'
        $list = $path -ceq '/api/v2/incidentManagement/incidentFilters'
        $conn = $path -ceq '/api/v2/incidentManagement/checkConnectivityDetailed'
        if (-not ($ownedArm -or $legacy -or $list -or $conn)) {
            $response = & $originalHttp @PSBoundParameters
            if ($path -ceq '/api/v2/extendedAgent/asre-azure-monitor-review/permissions' -and
                (($script:BootstrapExpire -ceq 'Delete' -and $script:BootstrapLists -eq 2) -or
                 ($script:BootstrapExpire -ceq 'Patch' -and $script:BootstrapLists -eq 4))) {
                $script:NativeNow = $(if ($script:BootstrapSubsecond) {
                    [DateTimeOffset]::Parse($script:BootstrapWindow.expires_at).AddMilliseconds(-500)
                } else { $script:NativeNow.AddMinutes(20) })
                if ($script:BootstrapCallerProfile) {
                    $script:BootstrapCallerProfile.activation_window.expires_at = $script:NativeNow.AddHours(1).ToString('o')
                }
            }
            return $response
        }
        $script:NativeCalls += @{ uri = $Uri; method = $Method; ifMatch = $Headers['If-Match']; timeout = $TimeoutSec; body = $Body }
        $data = $null; $code = 200
        if ($Method -cne 'Get') {
            Assert-Fixture ($TimeoutSec -ge 1 -and $TimeoutSec -le 30) 'Bootstrap timeout is not finite/bounded.'
            $walStream = [IO.File]::Open((Join-Path (Get-MonitorBootstrapJournalDirectory) 'attempt.jsonl'),
                [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
            $walReader = [IO.StreamReader]::new($walStream)
            try { $wal = $walReader.ReadToEnd() } finally { $walReader.Dispose() }
            Assert-Fixture ($wal.Contains($(if ($Method -ceq 'Delete') { 'BeforeDelete' } else { 'BeforePatch' }))) 'Mutation submitted before durable intent.'
        }
        if ($legacy) {
            if ($Method -ceq 'Delete') {
                Assert-Fixture ($null -eq $Body -and -not $Headers['If-Match']) 'Retirement invented a body/CAS.'
                $script:BootstrapLegacy = $null
                if ($script:BootstrapFault -ceq 'DeleteLost') { throw 'BOOTSTRAP-SECRET' }
                if ($script:BootstrapFault -ceq 'Unexpected') {
                    $script:BootstrapForeign = @{ name = 'foreign'; type = 'IncidentFilter'; properties = @{ isEnabled = $false } }
                }
            } elseif ($script:BootstrapLegacy) { $data = $script:BootstrapLegacy } else {
                $code = $(if ($script:BootstrapFault -ceq 'ReadAfterDelete') { 503 } else { 404 })
            }
        } elseif ($list) {
            $script:BootstrapLists++
            $data = @{ value = @(); nextLink = $null }
            foreach ($f in @($script:BootstrapLegacy,$script:NativeFilter,$script:BootstrapForeign)) {
                if ($null -ne $f) { $data.value += @($f) }
            }
        } elseif ($conn) {
            $data = @{ success = $true; incidentType = $script:NativePlatform.type; errorMessage = $null
                observedApiConnectionName = $null; observedConfigurationRevision = $null; observedConnectionStatus = 'Connected' }
            if ($script:BootstrapActualConnectivity) { $data = @{ success = $true; errorMessage = $null; incidentType = 'AzMonitor' } }
            if ($script:NativePlatform.type -ceq 'ServiceNow') {
                $data = Copy-Fixture $script:BootstrapOriginalConnectivity
            } else {
                $script:BootstrapPolls++
                switch ($script:BootstrapFault) {
                    ReadinessType { $data.success = 'true' }
                    ReadinessFalse { $data.success = $false }
                    ReadinessCase { $data.incidentType = 'azmonitor' }
                    ReadinessNull { $data.incidentType = "AzMonitor`0" }
                    ReadinessExtra { $data["unknown`0"] = 'BOOTSTRAP-SECRET' }
                    ReadinessStatusType { $data.observedConnectionStatus = $true }
                    ConnectivityHttp { $code = 503; $data = @{ error = 'BOOTSTRAP-SECRET' } }
                    ConnectivityAuth { $code = 401; $data = @{ error = 'BOOTSTRAP-SECRET' } }
                    ConnectivityTransport { throw 'BOOTSTRAP-SECRET' }
                    Pending {
                        if ($script:BootstrapPolls -eq 1) { $data.success = $false }
                    }
                    OldPending {
                        if ($script:BootstrapPolls -eq 1) { $data = Copy-Fixture $script:BootstrapOriginalConnectivity }
                    }
                }
                if ($script:BootstrapFault -ceq 'ConnectivityParse') {
                    return @{ StatusCode = 200; Content = 'INVALID BOOTSTRAP-SECRET'; Headers = @{} }
                }
            }
        } else {
            if ($Method -ceq 'Patch') {
                Assert-Fixture (([uri]$Uri).Query -ceq '?api-version=2025-05-01-preview' -and -not $Headers['If-Match']) 'Bootstrap used wrong ARM contract.'
                $request = $Body | ConvertFrom-Json -AsHashtable
                Assert-MonitorBootstrapBinding @{ properties = @{ incidentManagementConfiguration = @{
                    type = 'AzMonitor'; connectionName = 'azmonitor'; oboUser = '' } } } $request
                $script:NativePlatform = $request.properties.incidentManagementConfiguration
                if ($script:BootstrapFault -ceq 'PatchLost') { throw 'BOOTSTRAP-SECRET' }
            }
            $data = @{ id = $script:BootstrapAgent
                identity = @{ userAssignedIdentities = @{ $script:ScannerIdentity = @{ principalId = $script:ScannerPrincipal } } }
                properties = @{ agentEndpoint = 'https://asre-demo-cus--0a1b2c3d.4e5f6a7b.centralus.azuresre.ai'
                    actionConfiguration = @{ mode = 'Review'; accessLevel = 'Low'; identity = $script:ScannerIdentity }
                    incidentManagementConfiguration = $script:NativePlatform; provisioningState = 'Succeeded' } }
        }
        if ($Method -cne 'Get') { $code = $script:BootstrapResponseStatus }
        return @{ StatusCode = $code; Content = $(if ($Method -ceq 'Patch' -and $script:BootstrapFault -ceq 'PatchParse') {
            'INVALID BOOTSTRAP-SECRET'
        } elseif ($code -eq 204) { '' } elseif ($null -ne $data) { ConvertTo-Json -InputObject $data -Depth 40 } else { '' })
            Headers = $script:BootstrapResponseHeaders }
    }
    function Reset-Bootstrap {
        $j = Get-MonitorBootstrapJournalDirectory
        if (Test-Path -LiteralPath $j) { Remove-Item -LiteralPath $j -Recurse -Force }
        $script:NativeNow = [DateTimeOffset]::UtcNow
        $script:NativePlatform = Copy-Fixture $script:BootstrapOriginalPlatform
        $script:NativeFilter = $null; $script:BootstrapForeign = $null
        $script:NativeResources.Remove('/api/v2/incidentManagement/incidentFilters/asre-azure-monitor-review')
        $script:BootstrapLegacy = Copy-Fixture $script:BootstrapPilot
        $script:BootstrapFault = ''; $script:BootstrapExpire = ''; $script:BootstrapLists = 0
        $script:BootstrapCallerProfile = $null
        $script:BootstrapSubsecond = $false; $script:BootstrapSleeps = 0; $script:BootstrapPolls = 0
        $script:BootstrapActualConnectivity = $false
        $script:BootstrapResponseStatus = 200
        $script:BootstrapResponseHeaders = @{ 'Azure-AsyncOperation' = 'https://example.invalid/operation?sig=PRIVATE-REF'
            Location = @('opaque-location?sig=PRIVATE-REF'); 'Retry-After' = '7'
            'x-ms-request-id' = 'private-request-id'; Authorization = 'FORBIDDEN-AUTH'; 'Set-Cookie' = 'FORBIDDEN-COOKIE' }
        $script:CliUserType = 'user'
        $script:BootstrapAgent = $script:State.agentResourceId
        $window = @{ not_before = $script:NativeNow.AddSeconds(-1).ToString('o'); expires_at = $script:NativeNow.AddSeconds(8).ToString('o') }
        $script:BootstrapWindow = $window.Clone()
        $ctx = Get-MonitorNativeContext -Action BootstrapPlatform -Root $repo -AuthorityWindow $window
        $p = @{ version = 'asre-monitor-bootstrap/v1'; phase = 'SchemaDiscovery'
            attempt_id = '11111111-2222-3333-4444-555555555555'
            tenant_id = $script:CliClaims.tid; coordinator_object_id = $script:CliClaims.oid
            coordinator_client_id = $script:CliClaims.appid; agent_resource_id = $ctx.agent
            source_commit = $ArtifactReceipt.source_commit; template_commit = $ArtifactReceipt.template_commit
            publication_manifest_sha256 = (Get-FileHash -LiteralPath (Join-Path $ArtifactDirectory 'configuration-manifest.json') -Algorithm SHA256).Hash.ToLowerInvariant()
            original_snapshot = Get-MonitorNativeState $ctx
            original_connectivity = Copy-Fixture $script:BootstrapOriginalConnectivity
            journal_directory = $j; activation_window = $window; acknowledge_non_atomic_risk = $true
            max_delete = 1; max_patch = 1 }
        foreach ($kind in @('archive','writable','qualification')) {
            $p["${kind}_path"] = Join-Path $sandbox "$kind.json"
            $p["${kind}_sha256"] = (Get-FileHash -LiteralPath $p["${kind}_path"] -Algorithm SHA256).Hash.ToLowerInvariant()
        }
        $script:BootstrapLists = 0; $script:NativeCalls = @()
        return $p
    }
    try {
        foreach ($field in @('version','phase','tenant_id','coordinator_object_id','coordinator_client_id','agent_resource_id',
            'source_commit','template_commit','publication_manifest_sha256','archive_sha256','writable_sha256','qualification_sha256','journal_directory')) {
            $p = Reset-Bootstrap; $p[$field] += "`0"
            Assert-Rejected { Invoke-IncidentWorkflow -Root $repo -Action BootstrapPlatform -BootstrapProfile $p -PublicationArtifactDirectory $ArtifactDirectory } 'Bootstrap'
            Assert-Fixture (@($script:NativeCalls | Where-Object method -CNE Get).Count -eq 0) "Bad $field mutated."
        }
        $p = Reset-Bootstrap; $p.extra = $true
        Assert-Rejected { Invoke-IncidentWorkflow -Root $repo -Action BootstrapPlatform -BootstrapProfile $p -PublicationArtifactDirectory $ArtifactDirectory } 'Bootstrap'
        foreach ($field in @('max_delete','max_patch','acknowledge_non_atomic_risk','attempt_id')) {
            $p = Reset-Bootstrap
            $p[$field] = $(if ($field -ceq 'attempt_id') { $p.attempt_id + "`n" } else { '1' })
            Assert-Rejected { Invoke-IncidentWorkflow -Root $repo -Action BootstrapPlatform -BootstrapProfile $p -PublicationArtifactDirectory $ArtifactDirectory } 'Bootstrap'
            Assert-Fixture (@($script:NativeCalls | Where-Object method -CNE Get).Count -eq 0) 'Bad typed profile reached mutation.'
        }
        $p = Reset-Bootstrap
        $oid = $script:CliClaims.oid; $script:CliClaims.oid = '00000000-0000-0000-0000-000000000000'
        Assert-Rejected { Invoke-IncidentWorkflow -Root $repo -Action BootstrapPlatform -BootstrapProfile $p -PublicationArtifactDirectory $ArtifactDirectory } 'pinned existing coordinator'
        $script:CliClaims.oid = $oid
        $p = Reset-Bootstrap
        $ctx = Get-MonitorNativeContext -Action BootstrapPlatform -Root $repo -AuthorityWindow $p.activation_window
        Assert-Rejected { Invoke-MonitorNativeRequest $ctx Arm Get "$($ctx.agent)?api-version=2026-01-01" } 'escaped'
        Assert-Rejected { Invoke-MonitorNativeRequest $ctx Sre Delete '/api/v2/incidentManagement/incidentFilters/foreign' } 'only the exact'
        $ctx.bootstrapWrite = 'Retire'
        Assert-Rejected { Invoke-MonitorNativeRequest $ctx Sre Delete '/api/v2/incidentManagement/incidentFilters/asre-servicenow-review-pilot' -Body @{} } 'boundary'
        Assert-Fixture (@($script:NativeCalls | Where-Object method -CNE Get).Count -eq 0) 'Bootstrap exposed a nonfixed request.'
        $p = Reset-Bootstrap
        $bytes = [IO.File]::ReadAllBytes($p.archive_path)
        try {
            [IO.File]::AppendAllText($p.archive_path, ' ')
            Assert-Rejected { Invoke-IncidentWorkflow -Root $repo -Action BootstrapPlatform -BootstrapProfile $p -PublicationArtifactDirectory $ArtifactDirectory } 'Bootstrap'
            Assert-Fixture (@($script:NativeCalls | Where-Object method -CNE Get).Count -eq 0) 'Changed archive bytes reached mutation.'
        } finally { [IO.File]::WriteAllBytes($p.archive_path, $bytes) }
        $p = Reset-Bootstrap
        $handlerPath = '/api/v2/extendedAgent/agents/asre-azure-monitor-review'
        $savedHandler = Copy-Fixture $script:NativeResources[$handlerPath]
        try {
            $script:NativeResources[$handlerPath].properties.hooks.PreToolUse[0].script += "`0"
            $unready = Invoke-IncidentWorkflow -Root $repo -Action BootstrapPlatform -BootstrapProfile $p -PublicationArtifactDirectory $ArtifactDirectory
            Assert-Fixture ($unready.status -ceq 'unknown' -and
                @($script:NativeCalls | Where-Object method -CNE Get).Count -eq 0) 'Non-source-exact handler crossed bootstrap boundary.'
        } finally { $script:NativeResources[$handlerPath] = $savedHandler }
        $p = Reset-Bootstrap
        $savedHandler = Copy-Fixture $script:NativeResources[$handlerPath]
        try {
            $script:NativeResources[$handlerPath].properties.unapprovedTool = 'shell'
            $unready = Invoke-IncidentWorkflow -Root $repo -Action BootstrapPlatform -BootstrapProfile $p -PublicationArtifactDirectory $ArtifactDirectory
            Assert-Fixture ($unready.status -ceq 'unknown' -and
                @($script:NativeCalls | Where-Object method -CNE Get).Count -eq 0) 'Unknown handler property crossed bootstrap boundary.'
        } finally { $script:NativeResources[$handlerPath] = $savedHandler }
        foreach ($field in @('oboUser','apiConnectionName')) {
            $p = Reset-Bootstrap
            $script:NativePlatform[$field] = 'wrong-preimage'
            $p.original_snapshot.platformConfiguration[$field] = 'wrong-preimage'
            $unready = Invoke-IncidentWorkflow -Root $repo -Action BootstrapPlatform -BootstrapProfile $p -PublicationArtifactDirectory $ArtifactDirectory
            Assert-Fixture ($unready.status -ceq 'unknown' -and
                @($script:NativeCalls | Where-Object method -CNE Get).Count -eq 0) "Wrong original $field accepted."
        }
        foreach ($change in @('enabled','criteria','metadata','duplicate','foreign','Delete','Patch','DeleteLost','PatchLost','Unexpected',
            'ReadinessType','ReadinessCase','ReadinessNull','ReadinessExtra','ReadinessStatusType',
            'ConnectivityHttp','ConnectivityAuth','ConnectivityParse','ConnectivityTransport','ReadAfterDelete','PatchParse')) {
            $p = Reset-Bootstrap
            switch ($change) {
                enabled { $script:BootstrapLegacy.properties.isEnabled = $true }
                criteria { $script:BootstrapLegacy.properties.titleContains += 'changed' }
                metadata { $script:BootstrapLegacy.properties.etag += 'changed' }
                duplicate { $script:BootstrapForeign = Copy-Fixture $script:BootstrapLegacy }
                foreign { $script:BootstrapForeign = @{ name = 'foreign'; type = 'IncidentFilter'; properties = @{ isEnabled = $false } } }
                Delete { $script:BootstrapExpire = 'Delete' }
                Patch { $script:BootstrapExpire = 'Patch' }
                default { $script:BootstrapFault = $change }
            }
            $result = Invoke-IncidentWorkflow -Root $repo -Action BootstrapPlatform -BootstrapProfile $p -PublicationArtifactDirectory $ArtifactDirectory
            Assert-Fixture ($result.status -cne 'bootstrap-connected' -and -not $result.runtime_accepted) "Unsafe $change accepted."
            $writes = @($script:NativeCalls | Where-Object method -CNE Get)
            Assert-Fixture (@($writes | Where-Object method -CEQ Delete).Count -le 1 -and @($writes | Where-Object method -CEQ Patch).Count -le 1) 'Bootstrap retried mutation.'
            if ($change -cin @('enabled','criteria','metadata','duplicate','foreign','Delete')) {
                Assert-Fixture ($writes.Count -eq 0) "Pre-retirement $change reached a mutation."
            }
            if ($change -cin @('Patch','DeleteLost','Unexpected')) {
                Assert-Fixture (@($writes | Where-Object method -CEQ Patch).Count -eq 0) "Unsafe $change reached platform mutation."
            }
            if ($change -ceq 'DeleteLost') { Assert-Fixture ($result.status -ceq 'retired-only') "Lost DELETE was not truthfully reconciled: $($result.status); writes=$($writes.Count)." }
            if ($change -ceq 'PatchLost') { Assert-Fixture ($result.status -ceq 'configured-but-unready') 'Lost PATCH claimed readiness.' }
            if ($change -cin @('ReadinessType','ReadinessCase','ReadinessNull','ReadinessExtra','ReadinessStatusType',
                'ConnectivityHttp','ConnectivityAuth','ConnectivityParse','ConnectivityTransport')) {
                Assert-Fixture ($script:BootstrapPolls -eq 1 -and $script:BootstrapSleeps -eq 0) "Unexpected $change was retried as pending."
                $records = @(Get-Content -LiteralPath (Join-Path (Get-MonitorBootstrapJournalDirectory) 'attempt.jsonl') |
                    ForEach-Object { ConvertFrom-Json -InputObject $_ -AsHashtable })
                $failure = @($records | Where-Object event -CEQ Stopped)[-1].failure
                if ($change -cin @('ConnectivityHttp','ConnectivityAuth')) {
                    Assert-Fixture ($failure.category -ceq 'HttpFailure' -and
                        $failure.http_status -eq $(if ($change -ceq 'ConnectivityAuth') { 401 } else { 503 })) 'HTTP classification was lost.'
                } elseif ($change -ceq 'ConnectivityParse') {
                    Assert-Fixture ($failure.category -ceq 'ParseFailure') 'Parse failure classification was lost.'
                } elseif ($change -ceq 'ConnectivityTransport') {
                    Assert-Fixture ($failure.category -ceq 'TransportFailure') 'Transport failure classification was lost.'
                } else { Assert-Fixture ($failure.category -ceq 'SchemaFailure') 'Schema failure classification was lost.' }
            }
            if ($change -cin @('ReadAfterDelete','PatchParse')) {
                $records = @(Get-Content -LiteralPath (Join-Path (Get-MonitorBootstrapJournalDirectory) 'attempt.jsonl') |
                    ForEach-Object { ConvertFrom-Json -InputObject $_ -AsHashtable })
                Assert-Fixture (@($records | Where-Object event -CEQ MutationResponse).Count -eq $(if ($change -ceq 'ReadAfterDelete') { 1 } else { 2 })) 'Later read/parse failure lost prior mutation response evidence.'
            }
            $p.attempt_id = '22222222-2222-3333-4444-555555555555'
            $script:NativeNow = [DateTimeOffset]::Parse($p.activation_window.not_before).AddSeconds(1)
            $before = $writes.Count
            Assert-Rejected { Invoke-IncidentWorkflow -Root $repo -Action BootstrapPlatform -BootstrapProfile $p -PublicationArtifactDirectory $ArtifactDirectory } 'recorded|unavailable'
            Assert-Fixture (@($script:NativeCalls | Where-Object method -CNE Get).Count -eq $before) 'Different attempt bypassed durable resource attempt.'
        }
        $p = Reset-Bootstrap; $script:BootstrapCallerProfile = $p; $script:BootstrapExpire = 'Delete'
        $expiry = $p.activation_window.expires_at
        $renewed = Invoke-IncidentWorkflow -Root $repo -Action BootstrapPlatform -BootstrapProfile $p -PublicationArtifactDirectory $ArtifactDirectory
        Assert-Fixture ($p.activation_window.expires_at -cne $expiry -and $renewed.status -ceq 'unknown' -and
            @($script:NativeCalls | Where-Object method -CNE Get).Count -eq 0) 'Caller renewal extended the captured bootstrap window.'
        $p = Reset-Bootstrap; $script:BootstrapExpire = 'Delete'; $script:BootstrapSubsecond = $true
        $subsecond = Invoke-IncidentWorkflow -Root $repo -Action BootstrapPlatform -BootstrapProfile $p -PublicationArtifactDirectory $ArtifactDirectory
        Assert-Fixture ($subsecond.status -ceq 'unknown' -and
            @($script:NativeCalls | Where-Object method -CNE Get).Count -eq 0) 'Subsecond bootstrap submitted HTTP.'
        foreach ($pending in @('Pending','OldPending')) {
            $p = Reset-Bootstrap; $script:BootstrapFault = $pending
            $ready = Invoke-IncidentWorkflow -Root $repo -Action BootstrapPlatform -BootstrapProfile $p -PublicationArtifactDirectory $ArtifactDirectory
            Assert-Fixture ($ready.status -ceq 'bootstrap-connected' -and $script:BootstrapSleeps -eq 1 -and $script:BootstrapPolls -eq 2) 'Recognized typed pending could not converge.'
        }
        foreach ($statusCode in @(200,202,204,503)) {
            $p = Reset-Bootstrap; $script:BootstrapResponseStatus = $statusCode
            $evidenceResult = Invoke-IncidentWorkflow -Root $repo -Action BootstrapPlatform -BootstrapProfile $p -PublicationArtifactDirectory $ArtifactDirectory
            $logPath = Join-Path (Get-MonitorBootstrapJournalDirectory) 'attempt.jsonl'
            $log = [IO.File]::ReadAllText($logPath)
            $responses = @(Get-Content -LiteralPath $logPath | ForEach-Object { ConvertFrom-Json -InputObject $_ -AsHashtable } |
                Where-Object event -CEQ MutationResponse)
            Assert-Fixture ($responses.Count -eq $(if ($statusCode -eq 503) { 1 } else { 2 })) 'Mutation response record was lost.'
            foreach ($response in $responses) {
                Assert-Fixture ($response.http_status -eq $statusCode -and
                    $response.headers['Azure-AsyncOperation'] -ceq $script:BootstrapResponseHeaders['Azure-AsyncOperation']) 'Selected operation status/reference not retained.'
                Assert-MonitorBootstrapBinding $script:BootstrapResponseHeaders.Location $response.headers.Location
            }
            Assert-Fixture ($log -notmatch 'FORBIDDEN-AUTH|FORBIDDEN-COOKIE|BOOTSTRAP-SECRET') 'Journal retained auth/raw response content.'
            Assert-Fixture (($evidenceResult | ConvertTo-Json -Depth 40 -Compress) -notmatch 'PRIVATE-REF|private-request-id') 'Private operation references leaked publicly.'
            if ($statusCode -ne 503) {
                Assert-Fixture ($evidenceResult.journal_sha256 -ceq (Get-FileHash -LiteralPath $logPath -Algorithm SHA256).Hash.ToLowerInvariant()) 'Receipt did not bind response records.'
            }
        }
        $baseBootstrap = ${function:Invoke-MonitorBootstrap}
        $baseRequest = ${function:Invoke-MonitorNativeRequest}
        $baseConnected = ${function:Assert-MonitorConnected}
        $baseSafe = ${function:Assert-MonitorBootstrapSafe}
        $mutationKills = 0
        foreach ($mutation in @('actor','archive','journal','deadline','routing','readiness','M7','M9','response-evidence',
            'F1-alias','F1-revision','error-poll')) {
            $p = Reset-Bootstrap
            $body = $baseBootstrap.ToString()
            $requestBody = $baseRequest.ToString()
            $connectedBody = $baseConnected.ToString()
            $safeBody = $baseSafe.ToString()
            $mutationHandler = Copy-Fixture $script:NativeResources[$handlerPath]
            $archiveBytes = [IO.File]::ReadAllBytes($p.archive_path)
            switch ($mutation) {
                actor {
                    $p.coordinator_object_id += "`0"
                    $body = $body.Replace("@('coordinator_object_id',`$authority.object_id),", '')
                }
                archive {
                    [IO.File]::AppendAllText($p.archive_path, ' ')
                    $body = $body.Replace('Assert-MonitorBootstrapBinding $Profile["${kind}_sha256"] $digest', '')
                }
                journal {
                    $first = Invoke-IncidentWorkflow -Root $repo -Action BootstrapPlatform -BootstrapProfile $p -PublicationArtifactDirectory $ArtifactDirectory
                    Assert-Fixture ($first.status -ceq 'bootstrap-connected') 'Journal mutation baseline failed.'
                    $script:NativePlatform = Copy-Fixture $script:BootstrapOriginalPlatform
                    $script:BootstrapLegacy = Copy-Fixture $script:BootstrapPilot
                    $p.attempt_id = '22222222-2222-3333-4444-555555555555'
                    $script:NativeCalls = @()
                    $body = $body.Replace('[IO.FileMode]::CreateNew', '[IO.FileMode]::OpenOrCreate')
                }
                deadline {
                    $script:BootstrapExpire = 'Delete'
                    $requestBody = $requestBody.Replace('Assert-MonitorActivationWindow $Context.authorityWindow -Discovery', '')
                    $requestBody = $requestBody.Replace("if (`$remaining -lt 1) { throw 'Bootstrap original window has less than one second remaining.' }", '')
                }
                routing {
                    $script:BootstrapFault = 'Unexpected'
                    $body = $body.Replace('Assert-MonitorBootstrapBinding $retired $state', '')
                    $body = $body.Replace('Assert-MonitorBootstrapBinding $retired (Get-MonitorNativeState $Context)', '')
                }
                readiness {
                    $script:BootstrapFault = 'ReadinessType'
                    $connectedBody = $connectedBody.Replace('$c.success -isnot [bool]', '$false')
                }
                M7 {
                    $script:BootstrapExpire = 'Delete'; $script:BootstrapSubsecond = $true
                    $requestBody = $requestBody.Replace("if (`$remaining -lt 1) { throw 'Bootstrap original window has less than one second remaining.' }", '')
                }
                M9 {
                    $script:NativeResources[$handlerPath].properties.unapprovedTool = 'shell'
                    $safeBody = $safeBody.Replace('Assert-MonitorResource $expected $handler', '')
                }
                response-evidence {
                    $requestBody = $requestBody.Replace('Write-MonitorBootstrapRecord $Context.bootstrapJournal $record', '')
                }
                F1-alias {
                    $body = $body.Replace('$state.platformConfiguration.apiConnectionName', '$state.platformConfiguration.connectionName')
                }
                F1-revision {
                    $script:NativePlatform.oboUser = 'sreagent-config-revision:wrong'
                    $p.original_snapshot.platformConfiguration.oboUser = $script:NativePlatform.oboUser
                    $body = [regex]::Replace($body, '\s+-or\s+-not \[string\]::Equals\(\$state\.platformConfiguration\.oboUser,\s*\(''sreagent-config-revision:'' \+ \$connectivity\.observedConfigurationRevision\), \[StringComparison\]::Ordinal\)', '')
                }
                error-poll {
                    $script:BootstrapFault = 'ConnectivityHttp'
                    $body = $body.Replace('$readiness = Assert-MonitorConnected $Context $state -AllowPending',
                        "try { `$readiness = Assert-MonitorConnected `$Context `$state -AllowPending } catch { `$readiness = 'pending' }")
                }
            }
            try {
                Set-Item function:Invoke-MonitorBootstrap ([scriptblock]::Create($body))
                Set-Item function:Invoke-MonitorNativeRequest ([scriptblock]::Create($requestBody))
                Set-Item function:Assert-MonitorConnected ([scriptblock]::Create($connectedBody))
                Set-Item function:Assert-MonitorBootstrapSafe ([scriptblock]::Create($safeBody))
                $mutantResult = $null
                try { $mutantResult = Invoke-IncidentWorkflow -Root $repo -Action BootstrapPlatform -BootstrapProfile $p -PublicationArtifactDirectory $ArtifactDirectory } catch { }
                $writes = @($script:NativeCalls | Where-Object method -CNE Get)
                $escaped = switch ($mutation) {
                    routing { @($writes | Where-Object method -CEQ Patch).Count -gt 0 }
                    deadline { $writes.Count -gt 0 }
                    M7 { $writes.Count -gt 0 }
                    F1-alias { $null -ne $mutantResult -and $mutantResult.status -cne 'bootstrap-connected' }
                    error-poll { $script:BootstrapSleeps -gt 0 -and $script:BootstrapPolls -gt 1 }
                    response-evidence {
                        $records = @(Get-Content -LiteralPath (Join-Path (Get-MonitorBootstrapJournalDirectory) 'attempt.jsonl') |
                            ForEach-Object { ConvertFrom-Json -InputObject $_ -AsHashtable } | Where-Object event -CEQ MutationResponse)
                        $records.Count -ne 2
                    }
                    default { $null -ne $mutantResult -and $mutantResult.status -ceq 'bootstrap-connected' }
                }
                Assert-Rejected { Assert-Fixture (-not $escaped) "Mutation escaped $mutation boundary." } 'Mutation escaped'
                $mutationKills++
            } finally {
                Set-Item function:Invoke-MonitorBootstrap $baseBootstrap
                Set-Item function:Invoke-MonitorNativeRequest $baseRequest
                Set-Item function:Assert-MonitorConnected $baseConnected
                Set-Item function:Assert-MonitorBootstrapSafe $baseSafe
                $script:NativeResources[$handlerPath] = $mutationHandler
                [IO.File]::WriteAllBytes($p.archive_path, $archiveBytes)
            }
        }
        Assert-Fixture ($mutationKills -eq 12) 'A bootstrap mutation survived.'
        $p = Reset-Bootstrap
        $bootstrap = Invoke-IncidentWorkflow -Root $repo -Action BootstrapPlatform -BootstrapProfile $p -PublicationArtifactDirectory $ArtifactDirectory
        Assert-Fixture ($bootstrap.status -ceq 'bootstrap-connected' -and -not $bootstrap.runtime_accepted) 'One-shot bootstrap did not return source-bound nonacceptance receipt.'
        Assert-Fixture ($null -eq $script:BootstrapLegacy -and $null -eq $script:NativeFilter) 'Bootstrap recreated or published a plan.'
        Assert-Fixture (@($script:NativeCalls | Where-Object method -CNE Get).Count -eq 2) 'Bootstrap did not submit exactly DELETE1/PATCH1.'
        $script:CliUserType = 'servicePrincipal'; $script:CliUserName = $env:servicePrincipalId
        $script:BootstrapForeign = @{ name = 'quickstart_response_plan'; type = 'IncidentFilter'; properties = @{ isEnabled = $false } }
        $before = @($script:NativeCalls | Where-Object method -CNE Get).Count
        Assert-Rejected { Invoke-IncidentWorkflow -Root $repo -Action PublishDisabled } 'zero plans'
        Assert-Fixture (@($script:NativeCalls | Where-Object method -CNE Get).Count -eq $before) 'Publication silently accepted a disabled quickstart.'
        $script:BootstrapForeign = $null
        $published = Invoke-IncidentWorkflow -Root $repo -Action PublishDisabled
        $script:CliUserType = 'user'
        $paused = (Invoke-IncidentWorkflow -Root $repo).snapshot
        $w = @{ not_before = $script:NativeNow.AddSeconds(-1).ToString('o'); expires_at = $script:NativeNow.AddMinutes(3).ToString('o') }
        $probe = @{ version = '2.0-connected'; phase = 'SchemaDiscovery'; probe_id = $p.attempt_id
            tenant_id = $p.tenant_id; coordinator_object_id = $p.coordinator_object_id; coordinator_client_id = $p.coordinator_client_id
            agent_resource_id = $p.agent_resource_id; publication_manifest_sha256 = $p.publication_manifest_sha256
            agent_definition_sha256 = $published.publication_receipt.agent_definition_sha256
            snapshot_sha256 = Get-MonitorObjectSha256 $paused; activation_window = $w
            bootstrap_receipt_sha256 = Get-MonitorObjectSha256 $bootstrap
            publication_receipt_sha256 = Get-MonitorObjectSha256 $published.publication_receipt }
        $a = @{ Root = $repo; Snapshot = $paused; PublicationArtifactDirectory = $ArtifactDirectory
            ActivationWindow = $w; ProbeProfile = $probe; BootstrapReceipt = $bootstrap; PublicationReceipt = $published.publication_receipt }
        $patches = @($script:NativeCalls | Where-Object { $_.method -ceq 'Patch' -and $_.uri.StartsWith('https://management.azure.com/') }).Count
        $bad = $a.Clone(); $bad.PublicationReceipt = Copy-Fixture $published.publication_receipt; $bad.PublicationReceipt.pre_snapshot_sha256 = '0' * 64
        $bad.ProbeProfile = Copy-Fixture $probe
        $bad.ProbeProfile.publication_receipt_sha256 = Get-MonitorObjectSha256 $bad.PublicationReceipt
        Assert-Rejected { Invoke-IncidentWorkflow @bad -Action Cutover } 'VerifyRuntime'
        $chainDefinition = ${function:Test-MonitorConnectedDiscoveryChain}
        try {
            Set-Item function:Test-MonitorConnectedDiscoveryChain ([scriptblock]::Create(
                $chainDefinition.ToString().Replace('Assert-MonitorBootstrapBinding $b.connected_snapshot_sha256 $p.pre_snapshot_sha256', '')))
            $escapedChain = Invoke-IncidentWorkflow @bad -Action Cutover
            Assert-Rejected { Assert-Fixture ($escapedChain.status -cne 'cutover-disabled') 'Mutation escaped M6 inner link.' } 'Mutation escaped M6'
        } finally { Set-Item function:Test-MonitorConnectedDiscoveryChain $chainDefinition }
        foreach ($variant in @('bootstrapplatform','BOOTSTRAPPLATFORM','Bootstrapplatform')) {
            $before = @($script:NativeCalls | Where-Object method -CNE Get).Count
            Assert-Rejected { Invoke-IncidentWorkflow @a -Action $variant } 'ValidateSet|validation set|does not belong'
            Assert-Rejected { Get-MonitorNativeContext -Root $repo -Action $variant } 'ValidateSet|validation set|does not belong'
            Assert-Rejected { & (Join-Path $repo 'scripts/Publish-IncidentWorkflow.ps1') -Action $variant } 'ValidateSet|validation set|does not belong'
            Assert-Fixture (@($script:NativeCalls | Where-Object method -CNE Get).Count -eq $before) 'Noncanonical action reached Enable.'
        }
        foreach ($command in @((Get-Command Invoke-IncidentWorkflow),(Get-Command Get-MonitorNativeContext),
            (Get-Command (Join-Path $repo 'scripts/Publish-IncidentWorkflow.ps1')))) {
            $validation = @($command.Parameters.Action.Attributes | Where-Object { $_ -is [Management.Automation.ValidateSetAttribute] })[0]
            Assert-Fixture (-not $validation.IgnoreCase) 'Action admission became case-insensitive.'
        }
        $workflowDefinition = ${function:Invoke-IncidentWorkflow}
        $contextDefinition = ${function:Get-MonitorNativeContext}
        try {
            Set-Item function:Invoke-IncidentWorkflow ([scriptblock]::Create($workflowDefinition.ToString().
                Replace('IgnoreCase = $false','IgnoreCase = $true').
                Replace("if (`$Action -cne 'Enable') { throw 'Unsupported incident action; no activation.' }", '')))
            Set-Item function:Get-MonitorNativeContext ([scriptblock]::Create(
                $contextDefinition.ToString().Replace('IgnoreCase = $false','IgnoreCase = $true')))
            $escapedAction = Invoke-IncidentWorkflow @a -Action 'bootstrapplatform'
            Assert-Rejected { Assert-Fixture ($escapedAction.status -cne 'enabled') 'Mutation escaped F4 action dispatch.' } 'Mutation escaped F4'
        } finally {
            Set-Item function:Invoke-IncidentWorkflow $workflowDefinition
            Set-Item function:Get-MonitorNativeContext $contextDefinition
        }
        $null = Invoke-IncidentWorkflow -Root $repo -Action Pause
        $cut = Invoke-IncidentWorkflow @a -Action Cutover
        Assert-Fixture ($cut.status -ceq 'cutover-disabled') 'Connected-target Cutover required an ARM ETag or failed.'
        Assert-Fixture ($cut.original_migration_snapshot_sha256 -ceq $bootstrap.original_snapshot_sha256 -and
            $cut.paused_snapshot_sha256 -ceq (Get-MonitorObjectSha256 $paused) -and
            $cut.original_migration_snapshot_sha256 -cne $cut.paused_snapshot_sha256) 'Migration and post-publication snapshots were conflated.'
        $enabled = Invoke-IncidentWorkflow @a -Action Enable
        Assert-Fixture ($enabled.status -ceq 'enabled' -and -not $enabled.runtime_accepted) 'Connected-target Enable did not retain independent gates.'
        Assert-Fixture (@($script:NativeCalls | Where-Object { $_.method -ceq 'Patch' -and $_.uri.StartsWith('https://management.azure.com/') }).Count -eq $patches) 'Connected target redundantly PATCHed ARM.'
        # AC1: sanitized actual82 six-field wire platform and three-field
        # connectivity. These are not fabricated successful-bootstrap receipts.
        $observed = Copy-Fixture $script:BootstrapOriginalPlatform
        $observed.type = 'AzMonitor'; $observed.connectionName = 'azmonitor'; $observed.oboUser = ''
        foreach ($platform in @(@{type='AzMonitor';connectionName='azmonitor';oboUser=''},$observed)) {
            Assert-MonitorPlatform $platform
            Assert-Fixture ($true) 'Exact platform representation was rejected.'
            foreach ($key in @($platform.Keys)) {
                foreach ($wrong in @($null,$true,1,@(),@{},'ALTERED',($platform[$key]+' '),($platform[$key]+"`0"))) {
                    $badPlatform = Copy-Fixture $platform; $badPlatform[$key] = $wrong
                    Assert-Rejected { Assert-MonitorPlatform $badPlatform } 'platform|Bootstrap'
                }
                $badPlatform = Copy-Fixture $platform; $badPlatform.Remove($key)
                Assert-Rejected { Assert-MonitorPlatform $badPlatform } 'platform|Bootstrap'
            }
            $badPlatform = Copy-Fixture $platform; $badPlatform.extra = ''
            Assert-Rejected { Assert-MonitorPlatform $badPlatform } 'platform|Bootstrap'
        }
        $p = Reset-Bootstrap
        $p.attempt_id = '00000040-000a-4000-a000-000000000040'
        $p.source_commit = '3'*40; $p.template_commit = '4'*40
        $p.activation_window = @{ not_before=$script:NativeNow.AddMinutes(-20).ToString('o'); expires_at=$script:NativeNow.AddMinutes(-10).ToString('o') }
        $originDirectory = Join-Path $sandbox 'origin'
        $null = [IO.Directory]::CreateDirectory($originDirectory)
        $oldArtifact = Join-Path $originDirectory 'auto-native-validation-197-artifact'
        Copy-Item -LiteralPath $ArtifactDirectory -Destination $oldArtifact -Recurse
        $oldManifestPath = Join-Path $oldArtifact 'configuration-manifest.json'
        $oldManifest = Get-Content -LiteralPath $oldManifestPath -Raw | ConvertFrom-Json -AsHashtable
        $oldManifest.source_commit=$p.source_commit; $oldManifest.template_commit=$p.template_commit; $oldManifest.validation_run_id='197'
        [IO.File]::WriteAllText($oldManifestPath,(ConvertTo-Json -InputObject $oldManifest -Depth 40))
        $p.publication_manifest_sha256=(Get-FileHash -LiteralPath $oldManifestPath).Hash.ToLowerInvariant()
        $launchAt=[DateTimeOffset]::Parse($p.activation_window.not_before).AddSeconds(10)
        $originFiles=@{
            'auto-native-monitor-bootstrap78-profile.json'=$p
            'auto-native-monitor-bootstrap81-launch.json'=@{at=$launchAt.ToString('o');child_start_attempt_consumed=$true
                prior79_consumed_not_reused=$true;prior_bootstrap_absent=$true;trusted_source_files_verified=27}
            'auto-native-monitor-bootstrap81-process-result.json'=@{completed_at=$launchAt.AddSeconds(20).ToString('o')
                exit_code=1;deadline_terminated=$false;authority_consumed=$true;replay_allowed=$false
                attempt_exists=$true;receipt_exists=$false;runtime_accepted=$false}
            'auto-native-validation-197.build.json'=@{id=197;status='completed';result='succeeded'
                sourceVersion=$p.source_commit;finishTime=$launchAt.AddMinutes(-5).ToString('o')}
            'auto-native-validation-197-artifact-qualification.json'=@{runtime_accepted=$false;files=32}
        }
        $pins=Get-MonitorReconciliationPins
        $pins.directory=$originDirectory; $pins.files=@{}
        $pins.profile_sha256=Get-MonitorObjectSha256 $p; $pins.source_commit=$p.source_commit
        $pins.template_commit=$p.template_commit; $pins.manifest_sha256=$p.publication_manifest_sha256
        foreach($name in $originFiles.Keys) {
            $path=Join-Path $originDirectory $name
            [IO.File]::WriteAllText($path,(ConvertTo-Json -InputObject $originFiles[$name] -Depth 40))
            $pins.files[$name]=(Get-FileHash -LiteralPath $path).Hash.ToLowerInvariant()
        }
        $journalDirectory=Get-MonitorBootstrapJournalDirectory
        $null=[IO.Directory]::CreateDirectory($journalDirectory)
        [IO.File]::WriteAllText((Join-Path $journalDirectory 'resource.lock'),'')
        $journalPath=Join-Path $journalDirectory 'attempt.jsonl'
        # Sanitized synthetic origin sequence: NO operation headers copied.
        $records=@(@{event='Attempt';profile=$p;profile_sha256=$pins.profile_sha256},
            @{event='BeforeDelete';attempt_id=$p.attempt_id},@{event='MutationResponse';method='Delete';service='Sre';http_status=202},
            @{event='BeforePatch';attempt_id=$p.attempt_id},@{event='MutationResponse';method='Patch';service='Arm';http_status=202},
            @{event='Stopped';status='configured-but-unready';failure=@{category='SuppressedLocalFailure'}})
        [IO.File]::WriteAllText($journalPath,(($records|ForEach-Object {ConvertTo-Json -InputObject $_ -Depth 40 -Compress}) -join "`n")+"`n")
        $pins.journal_sha256=(Get-FileHash -LiteralPath $journalPath).Hash.ToLowerInvariant()
        $script:ReconciliationPins=$pins
        function Get-MonitorReconciliationPins { return $script:ReconciliationPins }
        $script:NativePlatform=Copy-Fixture $observed
        $script:BootstrapLegacy=$null; $script:BootstrapActualConnectivity=$true
        $w=@{not_before=$script:NativeNow.AddSeconds(-1).ToString('o');expires_at=$script:NativeNow.AddMinutes(5).ToString('o')}
        $rq=@{Root=$repo;Action='ReconcileBootstrap';ActivationWindow=$w;PublicationArtifactDirectory=$ArtifactDirectory}
        $journalBefore=[IO.File]::ReadAllBytes($journalPath)
        $ctx=Get-MonitorNativeContext -Root $repo -Action ReconcileBootstrap -AuthorityWindow $w
        Assert-Fixture $ctx.readOnly 'Reconciliation did not set readOnly before requests.'
        $before=$script:NativeCalls.Count
        foreach($method in @('Put','Patch','Delete','Post')) {
            Assert-Rejected { Invoke-MonitorNativeRequest $ctx Sre $method '/api/v2/incidentManagement/incidentFilters/asre-azure-monitor-review' } 'read-only|GET only|validation|does not belong'
        }
        $ctx.readOnly=$false
        Assert-Rejected { Invoke-MonitorNativeRequest $ctx Arm Get "$($ctx.agent)?api-version=2025-05-01-preview" } 'GET only'
        Assert-Fixture ($script:NativeCalls.Count -eq $before) 'Rejected reconciliation methods reached HTTP.'
        foreach($name in $pins.files.Keys) {
            $path=Join-Path $originDirectory $name; $bytes=[IO.File]::ReadAllBytes($path)
            [IO.File]::AppendAllText($path,' ')
            Assert-Rejected {Invoke-IncidentWorkflow @rq} 'origin'
            [IO.File]::WriteAllBytes($path,$bytes)
        }
        [IO.File]::AppendAllText($journalPath,"`n")
        Assert-Rejected {Invoke-IncidentWorkflow @rq} 'origin'
        [IO.File]::WriteAllBytes($journalPath,$journalBefore)
        $missing=Join-Path $originDirectory 'auto-native-monitor-bootstrap81-launch.json'
        $missingBytes=[IO.File]::ReadAllBytes($missing)
        Remove-Item -LiteralPath $missing
        Assert-Rejected {Invoke-IncidentWorkflow @rq} 'origin'
        [IO.File]::WriteAllBytes($missing,$missingBytes)
        $badRq=$rq.Clone();$badRq.PublicationArtifactDirectory=$oldArtifact
        Assert-Rejected {Invoke-IncidentWorkflow @badRq} 'Historical artifact'
        $badRq=$rq.Clone();$badRq.ActivationWindow=$p.activation_window
        Assert-Rejected {Invoke-IncidentWorkflow @badRq} 'window|expired'
        Assert-Rejected {Invoke-IncidentWorkflow @rq -Transport {throw 'unreachable'}} 'fixed native'
        foreach($fault in @('ReadinessType','ReadinessCase','ReadinessFalse')) {
            $script:BootstrapFault=$fault
            Assert-Rejected {Invoke-IncidentWorkflow @rq} 'connectivity|readiness|platform|DTO'
        }
        $script:BootstrapFault=''
        $script:BootstrapForeign=@{name='foreign-disabled';type='IncidentFilter';properties=@{isEnabled=$false}}
        Assert-Rejected {Invoke-IncidentWorkflow @rq} 'Bootstrap'
        $script:BootstrapForeign=$null
        $receiptPath=Join-Path $journalDirectory 'receipt.json'
        [IO.File]::WriteAllText($receiptPath,'{}')
        Assert-Rejected {Invoke-IncidentWorkflow @rq} 'overwrite|fabricate'
        Remove-Item -LiteralPath $receiptPath
        $script:NativeCalls=@()
        $reconciled=Invoke-IncidentWorkflow @rq
        Assert-Fixture ($reconciled.status -ceq 'connected-reconciled' -and -not $reconciled.runtime_accepted) 'Expired origin/fresh qualification did not reconcile.'
        Assert-Fixture ($reconciled.connected_snapshot.platformConfiguration.Count -eq 6) 'Reconciliation normalized/lost retained references.'
        Assert-Fixture ($reconciled.origin.publication_manifest_sha256 -cne $reconciled.publication_manifest_sha256) 'Old/new qualification manifests were conflated.'
        Assert-Fixture (@($script:NativeCalls|Where-Object method -CNE Get).Count -eq 0) 'Reconciliation issued a cloud write.'
        Assert-Fixture ([Convert]::ToBase64String($journalBefore) -ceq [Convert]::ToBase64String([IO.File]::ReadAllBytes($journalPath))) 'Original attempt journal was modified.'
        Assert-Fixture (-not(Test-Path -LiteralPath $receiptPath)) 'Reconciliation fabricated legacy receipt.json.'
        Assert-Rejected {Invoke-IncidentWorkflow @rq} 'overwrite'
        $script:CliUserType='servicePrincipal';$script:CliUserName=$env:servicePrincipalId
        Assert-Rejected {Invoke-IncidentWorkflow -Root $repo -Action PublishDisabled -PublicationArtifactDirectory $oldArtifact} 'Bootstrap'
        $published=Invoke-IncidentWorkflow -Root $repo -Action PublishDisabled -PublicationArtifactDirectory $ArtifactDirectory
        $script:CliUserType='user'
        $paused=(Invoke-IncidentWorkflow -Root $repo).snapshot
        $probe=Copy-Fixture $probe
        $probe.activation_window=$w; $probe.publication_manifest_sha256=$reconciled.publication_manifest_sha256
        $probe.snapshot_sha256=Get-MonitorObjectSha256 $paused
        $probe.bootstrap_receipt_sha256=Get-MonitorObjectSha256 $reconciled
        $probe.publication_receipt_sha256=Get-MonitorObjectSha256 $published.publication_receipt
        $a=@{Root=$repo;Snapshot=$paused;PublicationArtifactDirectory=$ArtifactDirectory;ActivationWindow=$w
            ProbeProfile=$probe;BootstrapReceipt=$reconciled;PublicationReceipt=$published.publication_receipt}
        $before=@($script:NativeCalls|Where-Object method -CNE Get).Count
        $cut=Invoke-IncidentWorkflow @a -Action Cutover
        Assert-Fixture ($cut.status -ceq 'cutover-disabled') 'Reconciliation/publication chain did not verify.'
        Assert-Fixture (@($script:NativeCalls|Where-Object method -CNE Get).Count -eq $before) 'Connected reconciliation Cutover repeated a mutation.'
        $reconciliationPath=Join-Path $journalDirectory 'bootstrap-reconciliation.json'
        $reconciliationBytes=[IO.File]::ReadAllBytes($reconciliationPath)
        foreach($field in @('origin','source_commit','template_commit','publication_manifest_sha256','agent_definition_sha256','activation_window')) {
            $badA=$a.Clone();$badA.BootstrapReceipt=Copy-Fixture $reconciled
            if($field -ceq 'origin'){$badA.BootstrapReceipt.origin.journal_sha256='0'*64}
            elseif($field -ceq 'activation_window'){$badA.BootstrapReceipt.activation_window.extra=$true}
            else{$badA.BootstrapReceipt[$field]='0'*64}
            $badA.ProbeProfile=Copy-Fixture $probe;$badA.ProbeProfile.bootstrap_receipt_sha256=Get-MonitorObjectSha256 $badA.BootstrapReceipt
            # Rehash AND substitute the temporary local record so a field/link
            # guard, rather than merely a stale outer hash, must catch this.
            [IO.File]::WriteAllText($reconciliationPath,(ConvertTo-Json -InputObject $badA.BootstrapReceipt -Depth 40))
            try {Assert-Rejected {Invoke-IncidentWorkflow @badA -Action Cutover} 'VerifyRuntime'}
            finally {[IO.File]::WriteAllBytes($reconciliationPath,$reconciliationBytes)}
        }
        [IO.File]::AppendAllText($journalPath,"`n")
        Assert-Rejected {Invoke-IncidentWorkflow @a -Action Cutover} 'VerifyRuntime'
        [IO.File]::WriteAllBytes($journalPath,$journalBefore)
        Assert-Fixture ([Convert]::ToBase64String($journalBefore) -ceq [Convert]::ToBase64String([IO.File]::ReadAllBytes($journalPath))) 'Chain verification modified the original journal.'
        $badA=$a.Clone();$badA.BootstrapReceipt=Copy-Fixture $bootstrap
        $badA.BootstrapReceipt.attempt_id=$pins.attempt_id
        $badA.ProbeProfile=Copy-Fixture $probe;$badA.ProbeProfile.bootstrap_receipt_sha256=Get-MonitorObjectSha256 $badA.BootstrapReceipt
        Assert-Rejected {Invoke-IncidentWorkflow @badA -Action Cutover} 'VerifyRuntime'
        # Complete captured readonly95 route, not a projection with the
        # troublesome nested field removed. All fixtures below remain offline.
        $actual97 = @'
{"name":"asre-azure-monitor-review","type":"IncidentFilter","tags":null,"properties":{"etag":"\"00000060-000a-0000-0000-000000000060\"","name":"","incidentPlatform":"AzMonitor","impactedService":"","priorities":["Sev1"],"incidentType":"","incidentTypes":[],"alertId":"/subscriptions/00000001-000a-4000-a000-000000000001/resourcegroups/rg-asre-control-cus/providers/microsoft.operationalinsights/workspaces/law-asre-cus/providers/Microsoft.AlertsManagement/alerts/00000047-000a-d000-0000-000000000047","titleContains":"asre-demo-disk","titleContainsAll":[],"titleContainsAny":[],"titleNotContains":[],"agentMode":"Review","handlingAgent":"asre-azure-monitor-review","handlingAgents":null,"owningTeamId":"","owningTeamIds":[],"maxAutomatedInvestigationAttempts":1,"mergeEnabled":true,"mergeWindowHours":3,"createdAt":"2026-10-19T18:38:10.9931853Z","updatedAt":"2026-10-19T18:38:10.9932223Z","isEnabled":false,"configurationWarning":null,"icmFilterSettings":null,"azMonitorFilterSettings":{"targetResourceType":"","targetResource":""}}}
'@ | ConvertFrom-Json -AsHashtable -DateKind String
        $expected97=(Get-MonitorManifest -Root $repo).response_plan
        $expected97.properties.titleContains = 'asre-demo-disk'
        Assert-MonitorResource $expected97 $actual97
        Assert-Fixture ($actual97.properties.azMonitorFilterSettings.Count -eq 2) 'Full captured route was mutated/projected.'
        foreach($nested in @($null,'',@(),@{},@{targetResource=''},@{targetResource='';targetResourceType='';extra=''},
            @{TargetResource='';targetResourceType=''},@{targetResource="`0";targetResourceType=''},
            @{targetResource=' ';targetResourceType=''},@{targetResource=$null;targetResourceType=''},
            @{targetResource=$false;targetResourceType=''},@{targetResource='';targetResourceType=0},
            @{targetResource='';targetResourceType='vm'},@{"targetResource`0"='';targetResourceType=''})) {
            $badRoute=Copy-Fixture $actual97;$badRoute.properties.azMonitorFilterSettings=$nested
            Assert-Rejected {Assert-MonitorResource $expected97 $badRoute} 'Monitor filter|readback|default'
        }
        $absent=Copy-Fixture $actual97;$absent.properties.Remove('azMonitorFilterSettings')
        Assert-MonitorResource $expected97 $absent
        $explicit=Copy-Fixture $expected97;$explicit.properties.azMonitorFilterSettings=@{targetResource='explicit';targetResourceType='explicit'}
        $matching=Copy-Fixture $actual97;$matching.properties.azMonitorFilterSettings=Copy-Fixture $explicit.properties.azMonitorFilterSettings
        Assert-MonitorResource $explicit $matching
        Assert-Rejected {Assert-MonitorResource $explicit $actual97} 'targetResource'
        $nonMonitor=Copy-Fixture $expected97;$nonMonitor.properties.incidentPlatform='ServiceNow'
        $nonMonitorActual=Copy-Fixture $actual97;$nonMonitorActual.properties.incidentPlatform='ServiceNow'
        Assert-Rejected {Assert-MonitorResource $nonMonitor $nonMonitorActual} 'default'
        $nonMonitorActual.properties.azMonitorFilterSettings=$null
        Assert-MonitorResource $nonMonitor $nonMonitorActual
        foreach($key in @('targetResource','targetResourceType')) {
            foreach($value in @($null,$true,1,@{},@(),' ',"`0",'nonempty')) {
                $badRoute=Copy-Fixture $actual97;$badRoute.properties.azMonitorFilterSettings[$key]=$value
                Assert-Rejected {Assert-MonitorResource $expected97 $badRoute} 'Monitor filter'
            }
        }
        $wrongCase=Copy-Fixture $expected97;$wrongCase.properties.incidentPlatform='AZMONITOR'
        $wrongCaseActual=Copy-Fixture $actual97;$wrongCaseActual.properties.incidentPlatform='AZMONITOR'
        Assert-Rejected {Assert-MonitorResource $wrongCase $wrongCaseActual} 'default'
        $null=Reset-Bootstrap
        $history97=Get-MonitorPublicationOrigin
        $script:Filter97=$true
        $script:NativePlatform=Copy-Fixture $history97.connected_snapshot.platformConfiguration
        $script:BootstrapLegacy=$null;$script:BootstrapActualConnectivity=$true
        $script:NativeFilter=Copy-Fixture $actual97
        $script:NativeFilter.properties.titleContains=(Get-MonitorManifest -Root $repo).response_plan.properties.titleContains
        $filterPath='/api/v2/incidentManagement/incidentFilters/asre-azure-monitor-review'
        $script:NativeResources[$filterPath]=Copy-Fixture $script:NativeFilter
        # A Linux publisher has no local Windows receipt. Source-pinned origin
        # custody and fresh protected artifact/run metadata must suffice.
        $runEnv=@{SYSTEM_COLLECTIONURI=$ArtifactReceipt.collection_uri;SYSTEM_TEAMPROJECTID=$ArtifactReceipt.project_id
            SYSTEM_DEFINITIONID=$ArtifactReceipt.definition_id;BUILD_REPOSITORY_ID=$ArtifactReceipt.repository_id
            BUILD_BUILDID='124';BUILD_SOURCEVERSION=$ArtifactReceipt.source_commit;TEMPLATE_SOURCE_COMMIT=$ArtifactReceipt.template_commit
            BUILD_SOURCEBRANCH='refs/heads/master';BUILD_REASON='Manual';BUILD_REPOSITORY_PROVIDER='TfsGit'}
        foreach($name in $runEnv.Keys){$filter97SavedEnvironment[$name]=[Environment]::GetEnvironmentVariable($name);[Environment]::SetEnvironmentVariable($name,$runEnv[$name])}
        $script:CliUserType='servicePrincipal';$script:CliUserName=$env:servicePrincipalId
        $pre97=(Invoke-IncidentWorkflow -Root $repo).snapshot
        foreach($field in @('permissionsEtag','mode','accessLevel','armEtag')) {
            $badState=Copy-Fixture $pre97;$badState[$field]+='changed'
            Assert-Rejected {Assert-MonitorAdoptedState $history97.connected_snapshot $badState $repo} 'Bootstrap'
        }
        $badState=Copy-Fixture $pre97;$badState.agentPermissionsEtag=''
        Assert-Rejected {Assert-MonitorAdoptedState $history97.connected_snapshot $badState $repo} 'ETag'
        $badState=Copy-Fixture $pre97;$badState.agentPermissions.allow=@('shell')
        Assert-Rejected {Assert-MonitorAdoptedState $history97.connected_snapshot $badState $repo} 'Bootstrap'
        $identityContext=Get-MonitorNativeContext -Root $repo -Action Preflight
        $null=Get-MonitorNativeState $identityContext
        $identityContext.lastArm.identity.userAssignedIdentities[$script:ScannerIdentity].principalId='foreign'
        Assert-Rejected {Assert-MonitorRetainedIdentity $identityContext} 'Bootstrap'
        # Raw ARM envelopes preserve key casing, duplicate aliases and invalid
        # types. Do not JSON-roundtrip these identity-only regression fixtures.
        function New-RetainedIdentityFixture {
            param($Key=$script:ScannerIdentity, $Action=$script:ScannerIdentity, $Principal=$script:ScannerPrincipal)
            $map=[Collections.Generic.Dictionary[object,object]]::new()
            $map.Add($Key,@{principalId=$Principal})
            return @{lastArm=@{identity=@{userAssignedIdentities=$map}
                properties=@{actionConfiguration=@{identity=$Action}}}}
        }
        $identityCases=@($script:ScannerIdentity,$script:ScannerIdentity.ToUpperInvariant(),$script:ScannerIdentity.ToLowerInvariant())
        foreach($key in $identityCases) {
            foreach($action in $identityCases) {
                $rawIdentity=New-RetainedIdentityFixture -Key $key -Action $action
                Assert-MonitorRetainedIdentity $rawIdentity
                Assert-Fixture ($rawIdentity.lastArm.identity.userAssignedIdentities.Comparer.Equals($key,$key)) 'Raw identity fixture was coerced.'
            }
        }
        $mixedIdentity=$script:ScannerIdentity.Replace('/subscriptions/','/SuBsCrIpTiOnS/').Replace('/resourceGroups/','/ReSoUrCeGrOuPs/')
        $caseSensitiveMap=[Collections.Generic.Dictionary[string,object]]::new([StringComparer]::Ordinal)
        $caseSensitiveMap.Add($mixedIdentity,@{principalId=$script:ScannerPrincipal})
        $rawIdentity=New-RetainedIdentityFixture -Action $mixedIdentity
        $rawIdentity.lastArm.identity.userAssignedIdentities=$caseSensitiveMap
        Assert-Fixture (-not $caseSensitiveMap.ContainsKey($script:ScannerIdentity)) 'Mixed-case fixture accidentally permits expected-casing lookup.'
        Assert-MonitorRetainedIdentity $rawIdentity
        Assert-Fixture ($caseSensitiveMap.Count -eq 1) 'Actual mixed-case matched key was not accepted.'
        $longSIdentity=$script:ScannerIdentity.Remove(1,1).Insert(1,[string][char]0x017f)
        $badIds=@(($script:ScannerIdentity+[char]0x00ad),($script:ScannerIdentity+"`0"),
            " $($script:ScannerIdentity)","$($script:ScannerIdentity) ",($script:ScannerIdentity+"`t"),
            $longSIdentity,$script:ScannerIdentity.Replace('o',[string][char]0x043e),
            "$($script:ScannerIdentity)/child",$script:ScannerIdentity.Substring(1),'/other/identity','')
        Assert-Fixture ($badIds.Count -eq 11) 'Identity alias fixtures were combined or coerced.'
        foreach($badId in $badIds) {
            $rawIdentity=New-RetainedIdentityFixture -Key $badId
            Assert-Rejected {Assert-MonitorRetainedIdentity $rawIdentity} '^Retained'
            $rawIdentity=New-RetainedIdentityFixture -Action $badId
            Assert-Rejected {Assert-MonitorRetainedIdentity $rawIdentity} '^Retained'
        }
        foreach($alias in @($script:ScannerIdentity.ToUpperInvariant(),$longSIdentity,($script:ScannerIdentity+[char]0x00ad))) {
            $rawIdentity=New-RetainedIdentityFixture
            $rawIdentity.lastArm.identity.userAssignedIdentities.Add($alias,@{principalId=$script:ScannerPrincipal})
            Assert-Rejected {Assert-MonitorRetainedIdentity $rawIdentity} '^Retained'
        }
        $rawIdentity=New-RetainedIdentityFixture
        $shadowMap=[Collections.Hashtable]::new([StringComparer]::Ordinal)
        $shadowMap.Add($script:ScannerIdentity,@{principalId=$script:ScannerPrincipal})
        $shadowMap.Add($script:ScannerIdentity.ToUpperInvariant(),@{principalId=$script:ScannerPrincipal})
        $shadowMap.Add('Keys',$script:ScannerIdentity)
        $rawIdentity.lastArm.identity.userAssignedIdentities=$shadowMap
        Assert-Rejected {Assert-MonitorRetainedIdentity $rawIdentity} '^Retained'
        foreach($badValue in @($null,$false,1,'string-container',@(),@{},[pscustomobject]@{identity=$script:ScannerIdentity},
            [Text.StringBuilder]::new($script:ScannerIdentity),@($script:ScannerIdentity))) {
            foreach($field in @('lastArm','identity','properties','actionConfiguration','map','action','matched','principal')) {
                $rawIdentity=New-RetainedIdentityFixture
                switch($field) {
                    lastArm {$rawIdentity.lastArm=$badValue}
                    identity {$rawIdentity.lastArm.identity=$badValue}
                    properties {$rawIdentity.lastArm.properties=$badValue}
                    actionConfiguration {$rawIdentity.lastArm.properties.actionConfiguration=$badValue}
                    map {$rawIdentity.lastArm.identity.userAssignedIdentities=$badValue}
                    action {$rawIdentity.lastArm.properties.actionConfiguration.identity=$badValue}
                    matched {$rawIdentity.lastArm.identity.userAssignedIdentities[$script:ScannerIdentity]=$badValue}
                    principal {$rawIdentity.lastArm.identity.userAssignedIdentities[$script:ScannerIdentity].principalId=$badValue}
                }
                $identityError=$(if($field -ceq 'principal' -and $badValue -is [string]){'^Bootstrap'}else{'^Retained'})
                Assert-Rejected {Assert-MonitorRetainedIdentity $rawIdentity} $identityError
            }
        }
        foreach($badKey in @($false,1,@('identity'),[pscustomobject]@{id=$script:ScannerIdentity})) {
            $rawIdentity=New-RetainedIdentityFixture -Key $badKey
            Assert-Rejected {Assert-MonitorRetainedIdentity $rawIdentity} '^Retained'
        }
        foreach($principal in @($script:ScannerPrincipal.ToUpperInvariant(),($script:ScannerPrincipal+[char]0x00ad),
            "$($script:ScannerPrincipal) ",($script:ScannerPrincipal+"`0"),'other-principal')) {
            $rawIdentity=New-RetainedIdentityFixture -Principal $principal
            Assert-Rejected {Assert-MonitorRetainedIdentity $rawIdentity} '^Bootstrap'
        }
        foreach($variant in @('enabled','foreign','duplicate')) {
            $before=@($script:NativeCalls|Where-Object method -CNE Get).Count
            if($variant -ceq 'enabled'){$script:NativeFilter.properties.isEnabled=$true}
            else{$script:BootstrapForeign=Copy-Fixture $actual97;if($variant -ceq 'foreign'){$script:BootstrapForeign.name='foreign'}}
            Assert-Rejected {Invoke-IncidentWorkflow -Root $repo -Action PublishDisabled -PublicationArtifactDirectory $ArtifactDirectory} 'Bootstrap|Adoption|resource|owned|Strict readback|Duplicate native'
            Assert-Fixture (@($script:NativeCalls|Where-Object method -CNE Get).Count -eq $before) 'Invalid partial route reached a publication mutation.'
            $script:NativeFilter.properties.isEnabled=$false;$script:BootstrapForeign=$null
        }
        $published97=Invoke-IncidentWorkflow -Root $repo -Action PublishDisabled -PublicationArtifactDirectory $ArtifactDirectory
        Assert-Fixture ($published97.publication_receipt.Count -eq 7 -and $published97.publication_context.Count -eq 14) 'Publication changed the seven-field receipt or closed context.'
        Assert-Fixture ($published97.publication_receipt.pre_snapshot_sha256 -ceq (Get-MonitorObjectSha256 $pre97) -and
            $published97.publication_receipt.pre_snapshot_sha256 -cne $history97.connected_snapshot_sha256) 'Publication used the historical zero-plan hash instead of adopted pre-state.'
        $script:CliUserType='user'
        $paused97=(Invoke-IncidentWorkflow -Root $repo).snapshot
        $w97=@{not_before=$script:NativeNow.AddSeconds(-1).ToString('o');expires_at=$script:NativeNow.AddMinutes(5).ToString('o')}
        $probe97=Copy-Fixture $probe;$probe97.version='3.0-adopted';$probe97.activation_window=$w97
        $probe97.snapshot_sha256=Get-MonitorObjectSha256 $paused97
        $probe97.publication_manifest_sha256=$published97.publication_context.publication_manifest_sha256
        $probe97.bootstrap_receipt_sha256=Get-MonitorObjectSha256 $history97
        $probe97.publication_receipt_sha256=Get-MonitorObjectSha256 $published97.publication_receipt
        $probe97.publication_context_sha256=Get-MonitorObjectSha256 $published97.publication_context
        $build97=@{id=124;status='completed';result='succeeded';reason='manual';sourceBranch='refs/heads/master'
            sourceVersion=$ArtifactReceipt.source_commit;definition=@{id=8;project=@{id=$ArtifactReceipt.project_id}}
            repository=@{id=$ArtifactReceipt.repository_id;type='TfsGit'}}
        $buildPath97=Join-Path (Get-MonitorReconciliationPins).directory 'auto-native-publication124-build-completed.json'
        [IO.File]::WriteAllText($buildPath97,(ConvertTo-Json -InputObject $build97 -Depth 20))
        $probe97.publication_build_sha256=(Get-FileHash -LiteralPath $buildPath97).Hash.ToLowerInvariant()
        $args97=@{Root=$repo;Snapshot=$paused97;PublicationArtifactDirectory=$ArtifactDirectory;ActivationWindow=$w97
            ProbeProfile=$probe97;BootstrapReceipt=$history97;PublicationReceipt=$published97.publication_receipt
            PublicationContext=$published97.publication_context}
        $writes97=@($script:NativeCalls|Where-Object method -CNE Get).Count
        $cut97=Invoke-IncidentWorkflow @args97 -Action Cutover
        Assert-Fixture ($cut97.status -ceq 'cutover-disabled') 'New adopted branch did not pass complete controller readiness.'
        Assert-Fixture (@($script:NativeCalls|Where-Object method -CNE Get).Count -eq $writes97) 'New connected branch fell through to legacy SetPlatform.'
        Assert-Fixture ((Get-Command (Join-Path $repo 'scripts/Publish-IncidentWorkflow.ps1')).Parameters.ContainsKey('PublicationContext')) 'Public entry cannot consume the closed publication context.'
        # Isolated semantic-policy fixtures use the real protected publisher,
        # authenticated chain and full fresh readiness, never a ready stub.
        $semanticStart = $script:Checks
        $semanticSaved = @{ global = Copy-Fixture $script:NativeGlobal; revision = $script:DiscoveryGlobalRevision
            filter = Copy-Fixture $script:NativeFilter; resources = Copy-Fixture $script:NativeResources
            agentPolicy = Copy-Fixture $script:NativeAgentPolicy; userType = $script:CliUserType
            userName = $script:CliUserName; manifest = ${function:Get-MonitorManifest}
            calls = @($script:NativeCalls) }
        try {
            $script:DiscoveryGlobalRevision = '"fixture-semantic-pre"'
            $semanticPre = (Invoke-IncidentWorkflow -Root $repo).snapshot
            $originBefore = ConvertTo-Json -InputObject $history97 -Depth 40
            $preBefore = ConvertTo-Json -InputObject $semanticPre -Depth 40
            Assert-MonitorSchemaDiscoveryAdoptedState $history97.connected_snapshot $semanticPre $repo
            Assert-Fixture ($semanticPre.permissionsEtag -cne $history97.connected_snapshot.permissionsEtag) 'Semantic fixture did not vary the global revision.'
            Assert-Rejected { Assert-MonitorAdoptedState $history97.connected_snapshot $semanticPre $repo } 'Bootstrap'
            foreach ($side in @('retained','fresh')) {
                foreach ($variant in @('allow','ask','deny','unknown','alias','duplicate-alias','missing','null','scalar','collection')) {
                    $left = Copy-Fixture $history97.connected_snapshot
                    $right = Copy-Fixture $semanticPre
                    $badPolicy = @{ allow = @(); ask = @(); deny = @() }
                    switch ($variant) {
                        allow { $badPolicy.allow = @('fixture-tool') }
                        ask { $badPolicy.ask = @('fixture-tool') }
                        deny { $badPolicy.deny = @('fixture-tool') }
                        unknown { $badPolicy.extra = @() }
                        alias { $badPolicy.Remove('allow'); $badPolicy['Allow'] = @() }
                        duplicate-alias {
                            $badPolicy = [Collections.Hashtable]::new([StringComparer]::Ordinal)
                            $badPolicy.Add('allow',@()); $badPolicy.Add('Allow',@())
                            $badPolicy.Add('ask',@()); $badPolicy.Add('deny',@())
                        }
                        missing { $badPolicy.Remove('deny') }
                        null { $badPolicy.allow = $null }
                        scalar { $badPolicy.ask = '' }
                        collection { $badPolicy.deny = [Collections.Generic.List[string]]::new() }
                    }
                    if ($side -ceq 'retained') { $left.permissions = $badPolicy } else { $right.permissions = $badPolicy }
                    Assert-Rejected { Assert-MonitorSchemaDiscoveryAdoptedState $left $right $repo } '^SchemaDiscovery adoption'
                }
                foreach ($revision in @($null,'',' ',1,$false,@('revision'),@{})) {
                    $left = Copy-Fixture $history97.connected_snapshot
                    $right = Copy-Fixture $semanticPre
                    if ($side -ceq 'retained') { $left.permissionsEtag = $revision } else { $right.permissionsEtag = $revision }
                    Assert-Rejected { Assert-MonitorSchemaDiscoveryAdoptedState $left $right $repo } '^SchemaDiscovery adoption'
                }
            }
            $bothNonempty = Copy-Fixture $semanticPre; $bothNonempty.permissions.ask = @('fixture-tool')
            Assert-Rejected { Assert-MonitorSchemaDiscoveryAdoptedState $bothNonempty $bothNonempty $repo } '^SchemaDiscovery adoption'
            foreach ($field in @('mode','accessLevel','armEtag','agentResourceId')) {
                $badState = Copy-Fixture $semanticPre; $badState[$field] += 'changed'
                Assert-Rejected { Assert-MonitorSchemaDiscoveryAdoptedState $history97.connected_snapshot $badState $repo } 'Bootstrap'
            }
            $badState = Copy-Fixture $semanticPre; $badState.agentPermissions.allow = @('fixture-tool')
            Assert-Rejected { Assert-MonitorSchemaDiscoveryAdoptedState $history97.connected_snapshot $badState $repo } 'Bootstrap'
            $script:CliUserType = 'servicePrincipal'; $script:CliUserName = $env:servicePrincipalId
            foreach ($field in @('allow','ask','deny')) {
                $script:NativeGlobal.permissions[$field] = @('fixture-tool')
                $before = @($script:NativeCalls | Where-Object method -CNE Get).Count
                Assert-Rejected { Invoke-IncidentWorkflow -Root $repo -Action PublishDisabled -PublicationArtifactDirectory $ArtifactDirectory } 'SchemaDiscovery adoption'
                Assert-Fixture (@($script:NativeCalls | Where-Object method -CNE Get).Count -eq $before) 'Changed policy reached publication writes.'
                $script:NativeGlobal.permissions[$field] = @()
            }
            foreach ($drift in @('policy','revision')) {
                $script:DiscoveryPublicationDrift = $drift
                Assert-Rejected { Invoke-IncidentWorkflow -Root $repo -Action PublishDisabled -PublicationArtifactDirectory $ArtifactDirectory } 'unchanged-global-policy'
                $script:DiscoveryPublicationDrift = ''
                $script:NativeGlobal = Copy-Fixture $semanticSaved.global
                $script:DiscoveryGlobalRevision = '"fixture-semantic-pre"'
                Assert-Fixture (-not $script:NativeFilter.properties.isEnabled) 'Rejected transaction activated the route.'
            }
            $semanticPublished = Invoke-IncidentWorkflow -Root $repo -Action PublishDisabled -PublicationArtifactDirectory $ArtifactDirectory
            Assert-Fixture ($semanticPublished.status -ceq 'published-disabled' -and
                $semanticPublished.publication_context.adopted_pre_snapshot.permissionsEtag -ceq $semanticPre.permissionsEtag) 'Revision-only adopted publication did not retain its actual pre-state.'
            $script:CliUserType = 'user'; $script:CliUserName = $semanticSaved.userName
            $semanticPaused = (Invoke-IncidentWorkflow -Root $repo).snapshot
            $semanticProbe = Copy-Fixture $probe97
            $semanticProbe.snapshot_sha256 = Get-MonitorObjectSha256 $semanticPaused
            $semanticProbe.publication_receipt_sha256 = Get-MonitorObjectSha256 $semanticPublished.publication_receipt
            $semanticProbe.publication_context_sha256 = Get-MonitorObjectSha256 $semanticPublished.publication_context
            $semanticContext = Get-MonitorNativeContext -Root $repo -Action Enable -AuthorityWindow $w97
            $semanticContext.action = 'Enable'; $semanticContext.activationWindow = $w97
            $semanticContext.snapshot = $semanticPaused; $semanticContext.probeProfile = $semanticProbe
            $semanticContext.qualifyingArtifact = Read-MonitorPublicationArtifact $ArtifactDirectory $repo
            $semanticContext.publicationManifestSha256 = $semanticContext.qualifyingArtifact.manifest_sha256
            $semanticContext.bootstrapReceipt = $history97
            $semanticContext.publicationReceipt = $semanticPublished.publication_receipt
            $semanticContext.publicationContext = $semanticPublished.publication_context
            $retainedBefore = ConvertTo-Json -InputObject @($semanticPaused,$semanticPublished,$semanticProbe) -Depth 40
            Test-MonitorAdoptedPublicationChain $semanticContext
            Assert-Fixture $true 'Revision-only historical adoption chain failed.'
            $script:DiscoveryGlobalRevision = '"fixture-semantic-fresh"'
            $before = @($script:NativeCalls | Where-Object method -CNE Get).Count
            $semanticReady = Test-MonitorDiscoveryReadiness $semanticContext
            Assert-Fixture ($semanticReady.status -ceq 'verified') 'Revision-only fresh adopted readiness failed.'
            Assert-Fixture (@($script:NativeCalls | Where-Object method -CNE Get).Count -eq $before) 'Semantic readiness performed a write.'
            foreach ($variant in @('profile','phase','lineage','paused-revision','policy')) {
                $badContext = $semanticContext.Clone()
                $badContext.probeProfile = Copy-Fixture $semanticProbe
                switch ($variant) {
                    profile { $badContext.probeProfile.version = '2.0-connected' }
                    phase { $badContext.probeProfile.phase = 'Production' }
                    lineage { $badContext.probeProfile.publication_context_sha256 = '0' * 64 }
                    paused-revision {
                        $badContext.snapshot = Copy-Fixture $semanticPaused
                        $badContext.snapshot.permissionsEtag = '"fixture-invalid-paused"'
                        $badContext.publicationContext = Copy-Fixture $semanticPublished.publication_context
                        $badContext.publicationReceipt = Copy-Fixture $semanticPublished.publication_receipt
                        $hash = Get-MonitorObjectSha256 $badContext.snapshot
                        $badContext.probeProfile.snapshot_sha256 = $hash
                        $badContext.publicationContext.paused_snapshot_sha256 = $hash
                        $badContext.publicationReceipt.paused_snapshot_sha256 = $hash
                        $badContext.publicationContext.publication_receipt_sha256 = Get-MonitorObjectSha256 $badContext.publicationReceipt
                        $badContext.probeProfile.publication_receipt_sha256 = Get-MonitorObjectSha256 $badContext.publicationReceipt
                        $badContext.probeProfile.publication_context_sha256 = Get-MonitorObjectSha256 $badContext.publicationContext
                    }
                    policy { $script:NativeGlobal.permissions.deny = @('fixture-tool') }
                }
                Assert-Rejected { Test-MonitorDiscoveryReadiness $badContext } 'Bootstrap|Discovery|Strict readback'
                $script:NativeGlobal = Copy-Fixture $semanticSaved.global
                Assert-Fixture (@($script:NativeCalls | Where-Object method -CNE Get).Count -eq $before) 'Rejected semantic readiness reached an activation write.'
            }
            $semanticArgs = $args97.Clone()
            $semanticArgs.Snapshot = $semanticPaused; $semanticArgs.ProbeProfile = $semanticProbe
            $semanticArgs.PublicationReceipt = $semanticPublished.publication_receipt
            $semanticArgs.PublicationContext = $semanticPublished.publication_context
            $badArgs = $semanticArgs.Clone(); $badArgs.ProbeProfile = Copy-Fixture $semanticProbe
            $badArgs.ProbeProfile.publication_context_sha256 = '0' * 64
            Assert-Rejected { Invoke-IncidentWorkflow @badArgs -Action Enable } 'VerifyRuntime'
            Assert-Fixture (@($script:NativeCalls | Where-Object method -CNE Get).Count -eq $before) 'Rejected semantic Enable reached an activation write.'
            # Cutover and ordinary Production retain their whole-snapshot gate.
            Assert-Rejected { Invoke-IncidentWorkflow @semanticArgs -Action Cutover } 'pre-cutover-snapshot'
            function Get-MonitorManifest {
                param([string]$Root)
                $m = & $script:OriginalMonitorManifest -Root $Root
                $m.phase = 'Production'; $m.response_plan.properties.Remove('alertId')
                return $m
            }
            Assert-Rejected { Invoke-IncidentWorkflow @semanticArgs -Action Enable } 'SchemaDiscovery-only'
            Set-Item function:Get-MonitorManifest $semanticSaved.manifest
            Assert-Fixture ((ConvertTo-Json -InputObject $history97 -Depth 40) -ceq $originBefore -and
                (ConvertTo-Json -InputObject $semanticPre -Depth 40) -ceq $preBefore -and
                (ConvertTo-Json -InputObject @($semanticPaused,$semanticPublished,$semanticProbe) -Depth 40) -ceq $retainedBefore) 'Semantic compatibility mutated retained evidence.'
        } finally {
            Set-Item function:Get-MonitorManifest $semanticSaved.manifest
            $script:NativeGlobal = $semanticSaved.global; $script:DiscoveryGlobalRevision = $semanticSaved.revision
            $script:DiscoveryPublicationDrift = ''
            $script:NativeFilter = $semanticSaved.filter; $script:NativeResources = $semanticSaved.resources
            $script:NativeAgentPolicy = $semanticSaved.agentPolicy
            $script:CliUserType = $semanticSaved.userType; $script:CliUserName = $semanticSaved.userName
            $script:NativeCalls = $semanticSaved.calls
        }
        Write-Output "$($script:Checks - $semanticStart) discovery semantic-policy checks passed; three real comparison boundaries, strict transaction and retained evidence."
        foreach($variant in @('version','extra','run','pre','post')) {
            $bad97=$args97.Clone();$bad97.PublicationContext=Copy-Fixture $published97.publication_context
            switch($variant){
                version {$bad97.PublicationContext.version='asre-monitor-publication-context/v2'}
                extra {$bad97.PublicationContext.ready=$true}
                run {$bad97.PublicationContext.protected_run.publication_run_id='123'}
                pre {$bad97.PublicationContext.adopted_pre_snapshot.filters[0].properties.isEnabled=$true
                    $bad97.PublicationContext.adopted_pre_snapshot_sha256=Get-MonitorObjectSha256 $bad97.PublicationContext.adopted_pre_snapshot}
                post {$bad97.PublicationReceipt=Copy-Fixture $published97.publication_receipt;$bad97.PublicationReceipt.paused_snapshot_sha256='0'*64
                    $bad97.PublicationContext.publication_receipt_sha256=Get-MonitorObjectSha256 $bad97.PublicationReceipt}
            }
            $bad97.ProbeProfile=Copy-Fixture $probe97
            $bad97.ProbeProfile.publication_receipt_sha256=Get-MonitorObjectSha256 $bad97.PublicationReceipt
            $bad97.ProbeProfile.publication_context_sha256=Get-MonitorObjectSha256 $bad97.PublicationContext
            Assert-Rejected {Invoke-IncidentWorkflow @bad97 -Action Cutover} 'VerifyRuntime'
        }
        $chain97=@{root=$repo;agent=$pre97.agentResourceId;qualifyingArtifact=(Read-MonitorPublicationArtifact $ArtifactDirectory $repo)
            bootstrapReceipt=$history97;publicationReceipt=$published97.publication_receipt
            publicationContext=$published97.publication_context;probeProfile=$probe97;snapshot=$paused97}
        $chain97.qualifyingArtifact.manifest_sha256=$history97.publication_manifest_sha256
        Assert-Rejected {Test-MonitorAdoptedPublicationChain $chain97} 'Historical qualification'
        foreach($field in @('publication_manifest_sha256','adopted_pre_snapshot_sha256','paused_snapshot_sha256','origin_receipt_sha256','source_commit')) {
            $bad97=$args97.Clone();$bad97.PublicationContext=Copy-Fixture $published97.publication_context
            $bad97.PublicationContext[$field]='0'*64
            $bad97.ProbeProfile=Copy-Fixture $probe97
            $bad97.ProbeProfile.publication_context_sha256=Get-MonitorObjectSha256 $bad97.PublicationContext
            Assert-Rejected {Invoke-IncidentWorkflow @bad97 -Action Cutover} 'VerifyRuntime'
            Assert-Fixture (@($script:NativeCalls|Where-Object method -CNE Get).Count -eq $writes97) 'Rejected controller context reached a mutation.'
        }
        $bad97=$args97.Clone();$bad97.BootstrapReceipt=Copy-Fixture $history97
        $bad97.BootstrapReceipt.origin.journal_sha256='0'*64;$bad97.ProbeProfile=Copy-Fixture $probe97
        $bad97.ProbeProfile.bootstrap_receipt_sha256=Get-MonitorObjectSha256 $bad97.BootstrapReceipt
        Assert-Rejected {Invoke-IncidentWorkflow @bad97 -Action Cutover} 'VerifyRuntime'
        $build97.result='failed';[IO.File]::WriteAllText($buildPath97,(ConvertTo-Json -InputObject $build97 -Depth 20))
        $bad97=$args97.Clone();$bad97.ProbeProfile=Copy-Fixture $probe97
        $bad97.ProbeProfile.publication_build_sha256=(Get-FileHash -LiteralPath $buildPath97).Hash.ToLowerInvariant()
        Assert-Rejected {Invoke-IncidentWorkflow @bad97 -Action Cutover} 'VerifyRuntime'
    } finally {
        foreach($name in $filter97SavedEnvironment.Keys){[Environment]::SetEnvironmentVariable($name,$filter97SavedEnvironment[$name])}
        $script:Filter97=$false
        $script:NativeNow = $null
        if (Test-Path -LiteralPath $sandbox) { Remove-Item -LiteralPath $sandbox -Recurse -Force }
    }
    Write-Output "$($script:Checks - $startChecks) bootstrap/connected-target checks passed; offline CLI/HTTP and temporary journals only."
}
$savedEnvironment = @{ client = $env:servicePrincipalId; tenant = $env:tenantId; token = $env:idToken }
try {
    $default = Invoke-IncidentWorkflow -Root $repo
    Assert-Fixture ($default.status -ceq 'preflight' -and
        @($script:NativeCalls | Where-Object method -CNE Get).Count -eq 0) 'User AAD preflight was not GET-only.'
    Assert-Rejected { Invoke-IncidentWorkflow -Root $repo -Action PublishDisabled } 'authentication failed'
    $env:servicePrincipalId = '00000018-000a-4000-b000-000000000018'
    $env:tenantId = '00000002-000a-4000-9000-000000000002'
    $env:idToken = 'offline-fixture-federation'
    Assert-Rejected { Invoke-IncidentWorkflow -Root $repo -Action PublishDisabled } 'authentication failed'
    $script:CliUserType = 'servicePrincipal'; $script:CliUserName = $env:servicePrincipalId
    $script:WrongEndpoint = $true
    Assert-Rejected { Invoke-IncidentWorkflow -Root $repo } 'ReadState'
    $script:WrongEndpoint = $false
    $beforePublication = @($script:NativeCalls | Where-Object method -CNE Get).Count
    # Explicit legacy lifecycle: real legacy public-wrapper coverage is retained
    # in Test-IncidentRelease's no-native_capture source fixture.
    Assert-Rejected { Invoke-IncidentWorkflow -Root $repo -Action PublishDisabled } 'binding|readiness'
    Assert-Fixture (@($script:NativeCalls | Where-Object method -CNE Get).Count -eq $beforePublication) 'Old ServiceNow-first sequence reached publication mutation.'
    $script:NativePlatform = @{ type = 'AzMonitor'; connectionName = 'azmonitor'; oboUser = '' }
    $default = Invoke-IncidentWorkflow -Root $repo -Action PublishDisabled
    Assert-Fixture ($default.status -ceq 'published-disabled' -and $default.phase -ceq 'SchemaDiscovery' -and
        -not $script:NativeFilter.properties.isEnabled) 'Real default adapter did not publish disabled Discovery.'
    Assert-Fixture (@($script:NativeCalls | Where-Object { $_.uri -like '*/extendedAgent/tools/*' -and $_.method -cne 'Get' }).Count -eq 0) 'First Discovery release published a case tool or production reader.'
    Assert-Fixture (@($script:NativeCalls | Where-Object { $_.uri -like '*/agent/settings/global' -and $_.method -cne 'Get' }).Count -eq 0) 'Default publication wrote global policy.'
    Assert-MonitorExact @{ allow = @(); ask = @(); deny = @() } $script:NativeGlobal.permissions 'unchanged-real-global-baseline'
    Assert-Fixture ($script:NativePlatform.type -ceq 'AzMonitor') 'Default publication changed the already-connected target.'
    $null = Invoke-IncidentWorkflow -Root $repo -Action PublishDisabled
    Assert-Fixture (@($script:NativeCalls | Where-Object {
        $_.uri -like '*/incidentFilters/asre-azure-monitor-review' -and $_.method -ceq 'Patch' }).Count -ge 1) 'Republish did not use documented native PATCH.'
    $snapshot = (Invoke-IncidentWorkflow -Root $repo).snapshot
    Assert-Rejected { Invoke-IncidentWorkflow -Root $repo -Action Cutover -Snapshot $snapshot } 'pinned existing coordinator'
    $script:CliUserType = 'user'; $script:CliUserName = 'coordinator-fixture'
    $script:CliClaims = @{
        tid = '00000002-000a-4000-9000-000000000002'
        oid = '00000057-000a-4000-a000-000000000057'
        appid = '04b07795-8ddb-461a-bbee-02f9e1bf7b46'
    }
    $coordinator = Get-MonitorNativeContext -Action Pause -Root $repo
    Assert-Fixture (-not $coordinator.readOnly) 'Pinned coordinator could not pause independently of any expired activation window.'
    $script:CliClaims.oid = '00000000-0000-0000-0000-000000000001'
    Assert-Rejected { Get-MonitorNativeContext -Action Enable -Root $repo } 'pinned existing coordinator'
    $script:CliClaims.oid = '00000057-000a-4000-a000-000000000057'
    $script:CapabilityAvailable = $false
    Assert-Rejected { Get-MonitorNativeContext -Action Enable -Root $repo } 'effective own-agent'
    $script:CapabilityAvailable = $true
    Assert-Rejected { Invoke-IncidentWorkflow -Root $repo -Action Enable -ActivationWindow $fixtureActivationWindow } 'complete protected publication artifact'
    $entryParams = (Get-Command (Join-Path $repo 'scripts/Publish-IncidentWorkflow.ps1')).Parameters
    Assert-Fixture (-not $entryParams.ContainsKey('Transport') -and -not $entryParams.ContainsKey('Binding')) 'Public entry point accepts caller executable/Boolean policy input.'
    Assert-Fixture (-not $entryParams.ContainsKey('Phase')) 'Activation exposes a runtime phase override.'
    $ctx = Get-MonitorNativeContext -Action Preflight -Root $repo
    Assert-Rejected { Invoke-MonitorNativeRequest $ctx Sre Put '/api/v2/agent/settings/global' -Body @{} } 'Read-only'
    $ctx.readOnly = $false
    Assert-Rejected { Invoke-MonitorNativeRequest $ctx Sre Put '/api/v2/agent/settings/global' -Body @{} } 'allowlist'
    Assert-Rejected { Invoke-MonitorNativeRequest $ctx Sre Get '/api/v1/extendedAgent/systemTool/execute' } 'allowlist'
    $script:HttpFailure = 302
    Assert-Rejected { Invoke-MonitorNativeRequest $ctx Sre Get '/api/v2/agent/settings/global' } 'HTTP 302'
    $script:HttpFailure = 403
    Assert-Rejected { Invoke-MonitorNativeRequest $ctx Sre Get '/api/v2/agent/settings/global' } 'HTTP 403'
    $script:HttpFailure = 0
    $expectedAgent = @(Get-MonitorReviewConfiguration -Root $repo | Where-Object kind -EQ agents)[0]
    # Literal hook captured from publication189 reconciliation. Only Discovery
    # is a live observation; Production below is an offline generated shape.
    $capturedHook = @'
{
  "type": "command",
  "prompt": null,
  "command": null,
  "script": "#!/usr/bin/env python3\nimport json, sys\nreason = \"ASRE SchemaDiscovery denies ALL tools.\"\nprint(json.dumps({\"decision\": \"block\", \"reason\": reason}))\nprint(reason, file=sys.stderr)\nsys.exit(2)",
  "matcher": "(?s:.*)",
  "timeout": 15,
  "model": null,
  "failMode": "block",
  "maxRejections": null,
  "sources": null
}
'@ | ConvertFrom-Json -AsHashtable -DateKind String
    $capturedAgent = Copy-Fixture $expectedAgent
    $capturedAgent.properties.hooks = @{ PreToolUse = @($capturedHook) }
    Assert-MonitorResource $expectedAgent $capturedAgent
    Assert-Fixture $true 'Actual captured ten-field Discovery hook failed strict resource readback.'
    $hydrated = Copy-Fixture $capturedAgent.properties.hooks
    foreach ($key in @('prompt','command','model','maxRejections','sources')) { $hydrated.PreToolUse[0].Remove($key) }
    Assert-Fixture ($hydrated.PreToolUse[0].Count -eq 5) 'Nested hydration fixture did not start with the original five fields.'
    Add-NativeHookDefaults $hydrated
    Assert-MonitorExact $capturedAgent.properties.hooks $hydrated 'independent-native-hook-hydration'
    Assert-Fixture $true 'Mock failed to hydrate the five observed nested null defaults.'
    $hookProbeStart = $script:Checks
    foreach ($phase in @(
        @{ name = 'SchemaDiscovery'; event = 'PreToolUse'; expected = $expectedAgent; actual = $capturedAgent },
        @{ name = 'Production (offline only)'; event = 'PostToolUse'; expected = $productionAgent; actual = (Copy-Fixture $productionAgent) }
    )) {
        $event = $phase.event
        $hook = $phase.expected.properties.hooks[$event][0]
        Assert-MonitorExact @('command','failMode','matcher','maxRejections','model','prompt','script','sources','timeout','type') `
            @($hook.Keys | Sort-Object) "$($phase.name)-exact-ten-hook-fields"
        Assert-Fixture ($hook.Count -eq 10) "$($phase.name) hook did not contain exactly ten fields."
        foreach ($key in @('prompt','command','model','maxRejections','sources')) {
            Assert-Fixture ($null -eq $hook[$key]) "$($phase.name) hook default $key was not literal null."
            foreach ($invalid in @(
                @{ value = '' }, @{ value = $false }, @{ value = 0 },
                @{ value = @() }, @{ value = 'unexpected' }
            )) {
                $bad = Copy-Fixture $phase.actual
                $bad.properties.hooks[$event][0][$key] = $invalid.value
                Assert-Rejected { Assert-MonitorResource $phase.expected $bad } 'Strict readback'
            }
            $bad = Copy-Fixture $phase.actual
            $bad.properties.hooks[$event][0].Remove($key)
            Assert-Rejected { Assert-MonitorResource $phase.expected $bad } 'Strict readback'
        }
        foreach ($unknown in @(@{ value = $null }, @{ value = 'unexpected' })) {
            $bad = Copy-Fixture $phase.actual
            $bad.properties.hooks[$event][0].unknownHookField = $unknown.value
            Assert-Rejected { Assert-MonitorResource $phase.expected $bad } 'Strict readback'
        }
        foreach ($key in @('type','script','matcher','timeout','failMode')) {
            $bad = Copy-Fixture $phase.actual
            $bad.properties.hooks[$event][0][$key] = $(if ($key -ceq 'timeout') { 16 } else { 'changed' })
            Assert-Rejected { Assert-MonitorResource $phase.expected $bad } 'Strict readback'
            $bad = Copy-Fixture $phase.actual
            $bad.properties.hooks[$event][0].Remove($key)
            Assert-Rejected { Assert-MonitorResource $phase.expected $bad } 'Strict readback'
        }
        $bad = Copy-Fixture $phase.actual
        $bad.properties.hooks[$event] += @(Copy-Fixture $hook)
        Assert-Rejected { Assert-MonitorResource $phase.expected $bad } 'Strict readback'
        $bad = Copy-Fixture $phase.actual
        $extraEvent = $(if ($event -ceq 'PostToolUse') { 'PreToolUse' } else { 'PostToolUse' })
        $bad.properties.hooks[$extraEvent] = @(Copy-Fixture $hook)
        Assert-Rejected { Assert-MonitorResource $phase.expected $bad } 'Strict readback'
        $bad = Copy-Fixture $phase.actual
        $bad.properties.tools += @('inherited.Shell')
        Assert-Rejected { Assert-MonitorResource $phase.expected $bad } 'Strict readback'
        $bad = Copy-Fixture $phase.actual
        $bad.properties.permissions = @{ allow = @('inherited.Shell') }
        Assert-Rejected { Assert-MonitorResource $phase.expected $bad } 'Strict readback'
    }
    Write-Output "$($script:Checks - $hookProbeStart) dual-phase hook shape/negative probes passed; Production is offline only."
    $bad = Copy-Fixture $script:NativeResources['/api/v2/extendedAgent/agents/asre-azure-monitor-review']
    $bad.properties.agentsAsTools = @('repair-agent')
    Assert-Rejected { Assert-MonitorResource $expectedAgent $bad } 'Strict readback'
    $bad.properties.agentsAsTools = $null; $bad.properties.newSecuritySwitch = $true
    Assert-Rejected { Assert-MonitorResource $expectedAgent $bad } 'unknown security'
    $bad = Copy-Fixture $script:NativeResources['/api/v2/extendedAgent/agents/asre-azure-monitor-review']
    $bad.properties.tools = @('inherited.Shell')
    Assert-Rejected { Assert-MonitorResource $expectedAgent $bad } 'Strict readback'
    $bad = Copy-Fixture $script:NativeResources['/api/v2/extendedAgent/agents/asre-azure-monitor-review']
    $bad.properties.hooks.PreToolUse[0].failMode = 'ignore'
    Assert-Rejected { Assert-MonitorResource $expectedAgent $bad } 'Strict readback'
    $bad = Copy-Fixture $script:NativeResources['/api/v2/extendedAgent/agents/asre-azure-monitor-review']
    $bad.properties.instructions = $bad.properties.instructions.Replace('SchemaDiscovery','Production')
    Assert-Rejected { Assert-MonitorResource $expectedAgent $bad } 'Strict readback'
    Assert-Rejected { Invoke-IncidentWorkflow -Root $repo -Action Enable -Phase Production } 'parameter.*Phase'
    Assert-Rejected {
        Assert-MonitorActivationWindow -Discovery @{ not_before = $now.AddMinutes(-1).ToString('o'); expires_at = $now.AddHours(1).ToString('o') }
    } '600 seconds'

    # Frozen artifact consumption: construct only local test data. The actual
    # source closure is read as literals; no D script or builder runs here.
    $artifactDirectory = Join-Path ([IO.Path]::GetTempPath()) ("asre-monitor-artifact-" + [guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Path $artifactDirectory
    try {
        $tokens = $null; $parseErrors = $null
        $releaseAst = [Management.Automation.Language.Parser]::ParseFile(
            (Join-Path $repo 'scripts/Invoke-IncidentRelease.ps1'), [ref]$tokens, [ref]$parseErrors)
        $files = @{}
        foreach ($name in @('SourceFiles','TemplateFiles')) {
            $assignment = @($releaseAst.FindAll({
                param($node)
                $node -is [Management.Automation.Language.AssignmentStatementAst] -and
                    $node.Left.Extent.Text -ceq ('$' + $name)
            }, $true))[0]
            foreach ($relative in $assignment.Right.Expression.SafeGetValue()) {
                $prefix = $(if ($name -ceq 'SourceFiles') { 'source/' } else { 'templates/' })
                $target = Join-Path $artifactDirectory ($prefix + $relative)
                $null = New-Item -ItemType Directory -Path (Split-Path $target -Parent) -Force
                if ($name -ceq 'SourceFiles') { Copy-Item -LiteralPath (Join-Path $repo $relative) -Destination $target }
                else { [IO.File]::WriteAllText($target, 'offline template receipt fixture') }
                $files[$prefix + $relative] = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
            }
        }
        $null = New-Item -ItemType Directory -Path (Join-Path $artifactDirectory 'generated')
        $generatedPath = Join-Path $artifactDirectory 'generated/case-tool.py'
        [IO.File]::WriteAllText($generatedPath, $protectedSource, [Text.UTF8Encoding]::new($false))
        $files['generated/case-tool.py'] = (Get-FileHash -LiteralPath $generatedPath -Algorithm SHA256).Hash.ToLowerInvariant()
        $receipt = @{
            artifact_kind = 'asre-configuration/v1'; profile = 'asre-configuration'
            publication_action = 'PublishDisabled'; runtime_accepted = $false
            agent_resource_id = $script:State.agentResourceId
            agent_endpoint = 'https://asre-demo-cus--0a1b2c3d.4e5f6a7b.centralus.azuresre.ai'
            tenant_id = '00000002-000a-4000-9000-000000000002'
            subscription_id = '00000001-000a-4000-a000-000000000001'
            source_branch = 'refs/heads/master'; source_commit = ('1' * 40); template_commit = ('2' * 40)
            validation_run_id = '123'; files = $files
            collection_uri = 'https://dev.azure.com/offline/'; project_id = '11111111-1111-1111-1111-111111111111'
            definition_id = '8'; repository_id = '22222222-2222-2222-2222-222222222222'
        }
        $receiptPath = Join-Path $artifactDirectory 'configuration-manifest.json'
        [IO.File]::WriteAllText($receiptPath, ($receipt | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))
        $protected = Read-MonitorPublicationArtifact -Directory $artifactDirectory -Root $repo
        Assert-Fixture ($protected.source -ceq $protectedSource) 'Protected activation changed generated source.'
        $receipt.profile = 'unrelated-publication'
        [IO.File]::WriteAllText($receiptPath, ($receipt | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))
        Assert-Rejected { Read-MonitorPublicationArtifact -Directory $artifactDirectory -Root $repo } 'context changed'
        $receipt.profile = 'asre-configuration'
        [IO.File]::WriteAllText($receiptPath, ($receipt | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))
        $snapshot = (Invoke-IncidentWorkflow -Root $repo).snapshot
        $beforeWrites = @($script:NativeCalls | Where-Object method -CNE Get).Count
        Assert-Rejected {
            Invoke-IncidentWorkflow -Root $repo -Action Cutover -Snapshot $snapshot `
                -PublicationArtifactDirectory $artifactDirectory -ActivationWindow $fixtureActivationWindow
        } 'VerifyRuntime'
        Assert-Fixture (@($script:NativeCalls | Where-Object method -CNE Get).Count -eq $beforeWrites) 'Unproven activation republished tools or changed runtime.'
        Assert-Fixture ((Get-FileHash -LiteralPath $generatedPath -Algorithm SHA256).Hash.ToLowerInvariant() -ceq $files['generated/case-tool.py']) 'Activation regenerated or replaced the protected bundle.'
        # Preserve the original ETag-dependent migration/TOCTOU regression
        # separately from the newly connected publication fixture.
        $script:NativePlatform = @{ type = 'ServiceNow'; connectionName = 'retained-native'; oboUser = '' }
        $discoverySnapshot = (Invoke-IncidentWorkflow -Root $repo).snapshot
        $profile = @{
            version = '1.0'; phase = 'SchemaDiscovery'; probe_id = '11111111-2222-3333-4444-555555555555'
            tenant_id = $script:CliClaims.tid; coordinator_object_id = $script:CliClaims.oid
            coordinator_client_id = $script:CliClaims.appid; agent_resource_id = $discoverySnapshot.agentResourceId
            publication_manifest_sha256 = (Get-FileHash -LiteralPath $receiptPath -Algorithm SHA256).Hash.ToLowerInvariant()
            agent_definition_sha256 = Get-MonitorObjectSha256 $expectedAgent
            snapshot_sha256 = Get-MonitorObjectSha256 $discoverySnapshot
            activation_window = Copy-Fixture $fixtureActivationWindow
        }
        $discoveryArgs = @{ Root = $repo; Snapshot = $discoverySnapshot; ProbeProfile = $profile
            PublicationArtifactDirectory = $artifactDirectory; ActivationWindow = $fixtureActivationWindow }
        Assert-Fixture ((Get-MonitorObjectSha256 @{ b = 1; a = @() }) -ceq
            (Get-MonitorObjectSha256 @{ a = @(); b = 1 })) 'Canonical profile hash depends on hashtable iteration order.'
        foreach ($field in @('phase','coordinator_object_id','publication_manifest_sha256','snapshot_sha256','agent_definition_sha256')) {
            $badProfile = Copy-Fixture $profile
            $badProfile[$field] = 'wrong'
            $badArgs = $discoveryArgs.Clone(); $badArgs.ProbeProfile = $badProfile
            Assert-Rejected { Invoke-IncidentWorkflow @badArgs -Action Cutover } 'VerifyRuntime'
        }
        $badProfile = Copy-Fixture $profile; $badProfile.ready = $true
        $badArgs = $discoveryArgs.Clone(); $badArgs.ProbeProfile = $badProfile
        Assert-Rejected { Invoke-IncidentWorkflow @badArgs -Action Cutover } 'VerifyRuntime'
        $agentPath = '/api/v2/extendedAgent/agents/asre-azure-monitor-review'
        $originalAgent = Copy-Fixture $script:NativeResources[$agentPath]
        foreach ($field in @('script','matcher','failMode')) {
            $script:NativeResources[$agentPath].properties.hooks.PreToolUse[0][$field] = 'wrong'
            Assert-Rejected { Invoke-IncidentWorkflow @discoveryArgs -Action Cutover } 'Strict readback'
            $script:NativeResources[$agentPath] = Copy-Fixture $originalAgent
        }
        $script:NativeResources[$agentPath].properties.instructions =
            $script:NativeResources[$agentPath].properties.instructions.Replace('SchemaDiscovery','Production')
        Assert-Rejected { Invoke-IncidentWorkflow @discoveryArgs -Action Cutover } 'Strict readback'
        $script:NativeResources[$agentPath] = Copy-Fixture $originalAgent
        $script:NativeFilter.properties.handlingAgent = 'unrelated-handler'
        Assert-Rejected { Invoke-IncidentWorkflow @discoveryArgs -Action Cutover } 'Strict readback'
        $script:NativeFilter.properties.handlingAgent = 'asre-azure-monitor-review'
        $script:ScannerRole = '/wrong-role'
        Assert-Rejected { Invoke-IncidentWorkflow @discoveryArgs -Action Cutover } 'VerifyRuntime'
        $script:ScannerRole = (Get-MonitorScannerBinding).role_definition_id
        $script:ScannerGrantPresent = $false
        Assert-Rejected { Invoke-IncidentWorkflow @discoveryArgs -Action Cutover } 'VerifyRuntime'
        $script:ScannerGrantPresent = $true
        $script:ScannerRoleScope += '/resourceGroups/rg-asre-control-cus'
        Assert-Rejected { Invoke-IncidentWorkflow @discoveryArgs -Action Cutover } 'VerifyRuntime'
        $script:ScannerRoleScope = (Get-MonitorScannerBinding).scope
        $script:ScannerRoleName = 'Contributor'
        Assert-Rejected { Invoke-IncidentWorkflow @discoveryArgs -Action Cutover } 'VerifyRuntime'
        $script:ScannerRoleName = 'Monitoring Contributor'
        $script:ScannerPrincipal = '00000000-0000-0000-0000-000000000001'
        Assert-Rejected { Invoke-IncidentWorkflow @discoveryArgs -Action Cutover } 'VerifyRuntime'
        $script:ScannerPrincipal = (Get-MonitorScannerBinding).principal_id
        $script:ScannerAssignmentsPaged = $true
        Assert-Rejected { Invoke-IncidentWorkflow @discoveryArgs -Action Cutover } 'VerifyRuntime'
        $script:ScannerAssignmentsPaged = $false
        $script:PrimaryCondition = 'Resolved'
        Assert-Rejected { Invoke-IncidentWorkflow @discoveryArgs -Action Cutover } 'VerifyRuntime'
        $script:PrimaryCondition = 'Fired'; $script:SecondaryCondition = 'Fired'
        Assert-Rejected { Invoke-IncidentWorkflow @discoveryArgs -Action Cutover } 'VerifyRuntime'
        $script:SecondaryCondition = 'Resolved'
        Assert-Fixture (@($script:NativeCalls | Where-Object method -CNE Get).Count -eq $beforeWrites) 'Rejected readiness wrote configuration or installed case code.'
        # Public entrypoint imports production definitions afresh; install only
        # the deterministic UTC clock seam after those import fixtures.
        function Get-MonitorUtcNow {
            if ($null -ne $script:NativeNow) { return $script:NativeNow }
            return [DateTimeOffset]::UtcNow
        }
        $originalExpiry = $fixtureActivationWindow.expires_at
        $script:ExpireInPlatformReads = $true; $script:PlatformGlobalReads = 0
        $writesBefore = @($script:NativeCalls | Where-Object method -CNE Get).Count
        Assert-Rejected { Invoke-IncidentWorkflow @discoveryArgs -Action Cutover } 'SetPlatform'
        Assert-Fixture ($script:PlatformGlobalReads -eq 3 -and
            @($script:NativeCalls | Where-Object method -CNE Get).Count -eq $writesBefore -and
            $script:NativePlatform.type -ceq 'ServiceNow') 'SetPlatform internal GET crossed expiry but an ARM mutation was submitted.'
        $script:NativeNow = $null
        $cutover = Invoke-IncidentWorkflow @discoveryArgs -Action Cutover
        Assert-Fixture ($cutover.status -ceq 'cutover-disabled' -and -not $script:NativeFilter.properties.isEnabled) 'Complete default Discovery proof did not permit disabled cutover.'
        $cutoverSubmit = @($script:NativeCalls | Where-Object { $_.method -ceq 'Patch' -and $_.uri.StartsWith('https://management.azure.com/') })[-1]
        Assert-Fixture ($cutoverSubmit.timeout -eq 30) 'Cutover submission exceeded reviewed maximum30seconds.'
        foreach ($extend in @($false, $true)) {
            $script:ExpireBeforeEnable = $true; $script:ExtendCallerWindow = $extend
            $writesBefore = @($script:NativeCalls | Where-Object method -CNE Get).Count
            Assert-Rejected { Invoke-IncidentWorkflow @discoveryArgs -Action Enable } 'independently paused'
            $writes = @($script:NativeCalls | Where-Object method -CNE Get | Select-Object -Skip $writesBefore)
            Assert-Fixture ($writes.Count -eq 1 -and
                ($writes[0].body | ConvertFrom-Json -AsHashtable).properties.isEnabled -eq $false -and
                -not $script:NativeFilter.properties.isEnabled) 'Expired pre-enable GET submitted an increase or caller extension renewed captured authority.'
            $fixtureActivationWindow.expires_at = $originalExpiry
            $script:NativeNow = $null; $script:ExtendCallerWindow = $false
        }
        $script:NearExpirySeconds = 0.5; $script:NearExpiry = $true
        $writesBefore = @($script:NativeCalls | Where-Object method -CNE Get).Count
        Assert-Rejected { Invoke-IncidentWorkflow @discoveryArgs -Action Enable } 'independently paused'
        $writes = @($script:NativeCalls | Where-Object method -CNE Get | Select-Object -Skip $writesBefore)
        Assert-Fixture ($writes.Count -eq 1 -and
            ($writes[0].body | ConvertFrom-Json -AsHashtable).properties.isEnabled -eq $false) 'Subsecond authority submitted an increase or rounded timeout up.'
        $script:NativeNow = $null; $script:NearExpirySeconds = 7.5
        $expiredContext = Get-MonitorNativeContext -Action Enable -Root $repo
        $expiredContext.authorityWindow = @{
            not_before = $now.AddMinutes(-2).ToString('o'); expires_at = $now.AddMinutes(-1).ToString('o') }
        $badPause = Copy-Fixture (Get-MonitorManifest -Root $repo).response_plan
        $badPause.properties.handlingAgent = 'unrelated-handler'
        $badPauseBody = @{ name = $badPause.name; type = $badPause.type; properties = $badPause.properties }
        $writesBefore = @($script:NativeCalls | Where-Object method -CNE Get).Count
        Assert-Rejected {
            Invoke-MonitorNativeRequest $expiredContext Sre Patch '/api/v2/incidentManagement/incidentFilters/asre-azure-monitor-review' -Body $badPauseBody
        } 'expired-owned-pause-only'
        Assert-Rejected {
            Invoke-MonitorNativeRequest $expiredContext Sre Put '/api/v2/extendedAgent/asre-azure-monitor-review/permissions' -Body @{ allow = @() }
        } 'does not permit'
        Assert-Fixture (@($script:NativeCalls | Where-Object method -CNE Get).Count -eq $writesBefore) 'Expired compensation exception authorized a non-source plan or other route.'
        $script:ExpireDuringEnable = $true
        Assert-Rejected { Invoke-IncidentWorkflow @discoveryArgs -Action Enable } 'independently paused'
        Assert-Fixture (-not $script:NativeFilter.properties.isEnabled) 'Deadline reached during enable was not compensated to paused.'
        $fixtureActivationWindow.expires_at = $originalExpiry
        $script:NearExpiry = $true
        $enabled = Invoke-IncidentWorkflow @discoveryArgs -Action Enable
        Assert-Fixture ($enabled.status -ceq 'enabled' -and $script:NativeFilter.properties.isEnabled -and
            -not $enabled.runtime_accepted) 'Complete default Discovery proof did not permit real-adapter Enable.'
        $enableSubmit = @($script:NativeCalls | Where-Object {
            $_.method -cne 'Get' -and $_.uri -like '*/incidentFilters/asre-azure-monitor-review' -and
            ($_.body | ConvertFrom-Json -AsHashtable).properties.isEnabled })[-1]
        Assert-Fixture ($enableSubmit.timeout -eq 7) 'Near-expiry submit did not floor remaining time or exceeded its original budget.'
        $script:NativeNow = $null
        Assert-Fixture (@($script:NativeCalls | Where-Object {
            $_.uri -like '*/extendedAgent/tools/*' -or $_.uri -match 'vault|service-now' }).Count -eq 0) 'Discovery required case source installation, outbound credential or facade execution.'
        $pauseResult = Invoke-IncidentWorkflow -Root $repo -Action Pause
        Assert-Fixture ($pauseResult.status -ceq 'paused') 'Discovery could not pause after complete proof activation.'
        $expiredWindow = @{ not_before = $now.AddMinutes(-10).ToString('o'); expires_at = $now.AddMinutes(-1).ToString('o') }
        Assert-Rejected {
            Invoke-IncidentWorkflow -Root $repo -Action Enable `
                -PublicationArtifactDirectory $artifactDirectory -ActivationWindow $expiredWindow
        } 'authority window'
        $paused = . (Join-Path $repo 'scripts/Publish-IncidentWorkflow.ps1') -Action Pause -ActivationWindow $expiredWindow
        Assert-Fixture ($paused.status -ceq 'paused' -and -not $script:NativeFilter.properties.isEnabled) 'Pause incorrectly depended on an unexpired activation/tool window.'
        Set-Item function:Get-MonitorManifest $script:OriginalMonitorManifest
        Test-BootstrapFixtures -ArtifactDirectory $artifactDirectory -ArtifactReceipt $receipt
        [IO.File]::AppendAllText($generatedPath, '# tampered')
        Assert-Rejected { Read-MonitorPublicationArtifact -Directory $artifactDirectory -Root $repo } 'hash mismatch'
    } finally { Remove-Item -LiteralPath $artifactDirectory -Recurse -Force }
} finally {
    $env:servicePrincipalId = $savedEnvironment.client
    $env:tenantId = $savedEnvironment.tenant
    $env:idToken = $savedEnvironment.token
}

# Full native request -> operation -> transport diagnostics; still no real CLI
# or HTTP. Inspect rendered errors, not just whether a catch was reached.
$diagnosticStart = $script:Checks
function Assert-SafeDiagnosticError {
    param([scriptblock]$Run, [string]$Expected)
    $caught = $null
    try { & $Run | Out-Null } catch { $caught = $_ }
    Assert-Fixture ($null -ne $caught) 'Diagnostic probe incorrectly succeeded.'
    Assert-Fixture ($caught.Exception.Message -ceq $Expected) 'Diagnostic probe did not retain exactly the validated safe fields.'
    $rendered = ($caught | Out-String) + ($caught | Format-List * -Force | Out-String) +
        ($caught.Exception | Format-List * -Force | Out-String)
    Assert-Fixture ($rendered -notmatch 'MONITOR-DIAGNOSTIC-SECRET-SENTINEL|https://|/api/|Bearer |Authorization|If-Match') `
        'Rendered diagnostic exposed suppressed request/response/exception data.'
    Assert-Fixture ($null -eq $caught.Exception.InnerException -and $caught.Exception.Data.Count -eq 0 -and
        $null -eq $caught.TargetObject) 'Outer error retained original exception, metadata or target data.'
}
$diagnosticContext = Get-MonitorNativeContext -Action Preflight -Root $repo
$diagnosticContext.readOnly = $false
$diagnosticContext.sreHeaders.Authorization = "Bearer $($script:DiagnosticSecret)"
$diagnosticTransport = {
    param($Operation, $Request)
    Invoke-MonitorNativeOperation -Context $diagnosticContext -Operation $Operation -Request $Request
}
$filterPath = '/api/v2/incidentManagement/incidentFilters/asre-azure-monitor-review'
$agentPath = '/api/v2/extendedAgent/agents/asre-azure-monitor-review'
$filterResource = (Get-MonitorManifest -Root $repo).response_plan
$savedResources = Copy-Fixture $script:NativeResources
$savedFilter = Copy-Fixture $script:NativeFilter
$cases = @(
    foreach ($method in @('Get','Put','Patch')) {
        foreach ($status in @(302,403,503)) {
            @{ method = $method; status = $status; kind = 'Http'; path = $filterPath
                target = 'OwnedIncidentFilter'; operation = 'PublishResource'; resource = $filterResource }
        }
        foreach ($kind in @('Dns','Timeout','Other','Json')) {
            @{ method = $method; status = 200; kind = $kind; path = $filterPath
                target = 'OwnedIncidentFilter'; operation = 'PublishResource'; resource = $filterResource }
        }
    }
    @{ method = 'Get'; status = 500; kind = 'Http'; path = $agentPath
        target = 'OwnedAgent'; operation = 'PublishResource'; resource = $expectedAgent }
    @{ method = 'Put'; status = 409; kind = 'Http'; path = $agentPath
        target = 'OwnedAgent'; operation = 'PublishResource'; resource = $expectedAgent }
    @{ method = 'Get'; status = 403; kind = 'Http'; path = '/api/v2/extendedAgent/asre-azure-monitor-review/permissions'
        target = 'AgentPermissions'; operation = 'SetAgentPermissions' }
    @{ method = 'Put'; status = 429; kind = 'Http'; path = '/api/v2/extendedAgent/asre-azure-monitor-review/permissions'
        target = 'AgentPermissions'; operation = 'SetAgentPermissions' }
    @{ method = 'Get'; status = 500; kind = 'Http'; path = '/api/v2/extendedAgent/tools/asre-review-evidence'
        target = 'OtherAllowedRequest'; operation = 'ReadResource'; name = 'asre-review-evidence'; resourceKind = 'tools' }
    @{ method = 'Get'; status = 404; kind = 'Http'; path = $filterPath
        target = 'OwnedIncidentFilter'; operation = 'ReadResource'; name = 'asre-azure-monitor-review'; resourceKind = 'incidentFilters' }
    foreach ($status in @('503', 99, 600, [long]503)) {
        @{ method = 'Get'; status = $status; kind = 'Http'; path = $filterPath
            target = 'OwnedIncidentFilter'; operation = 'PublishResource'; resource = $filterResource; generic = $true }
    }
)
try {
    foreach ($case in $cases) {
        $script:NativeResources = Copy-Fixture $savedResources
        $script:NativeFilter = Copy-Fixture $savedFilter
        if ($case.method -ceq 'Put' -and $case.operation -ceq 'PublishResource' -and $case.path -ceq $filterPath) {
            $script:NativeResources.Remove($filterPath); $script:NativeFilter = $null
        }
        $script:DiagnosticFault = $case
        $before = $script:NativeCalls.Count
        $request = switch ($case.operation) {
            PublishResource { @{ resource = $case.resource } }
            SetAgentPermissions { @{ permissions = @{ allow = @() } } }
            ReadResource { @{ kind = $case.resourceKind; name = $case.name } }
        }
        $prefix = "Monitor adapter failed during $($case.operation)"
        $detail = $(if ($case.kind -ceq 'Http') { "HTTP $($case.status)" } elseif ($case.kind -ceq 'Json') { 'parse-failure' } else { 'transport-failure' })
        if (-not $case.ContainsKey('generic')) { $prefix += " ($detail; $($case.method); $($case.target))" }
        Assert-SafeDiagnosticError {
            Invoke-MonitorTransport $diagnosticTransport $case.operation $request
        } "$prefix; outcome may be unknown. Re-read before retry; response details suppressed."
        $calls = @($script:NativeCalls | Select-Object -Skip $before)
        Assert-Fixture ($calls.Count -eq $(if ($case.method -ceq 'Get') { 1 } else { 2 }) -and
            $calls[-1].method -ceq $case.method) 'Failure retried, changed verb, wrote after failed GET or added requests.'
    }
    # Only explicit AllowNotFound on the preliminary GET permits 404 -> create.
    $script:NativeResources.Remove($filterPath); $script:NativeFilter = $null
    $script:DiagnosticFault = @{ path = $filterPath; method = 'Get'; kind = 'Http'; status = 404 }
    $before = $script:NativeCalls.Count
    $created = Invoke-MonitorTransport $diagnosticTransport PublishResource @{ resource = $filterResource }
    $calls = @($script:NativeCalls | Select-Object -Skip $before)
    Assert-Fixture ($created -eq $true -and $calls.Count -eq 2 -and $calls[0].method -ceq 'Get' -and
        $calls[1].method -ceq 'Put') 'Explicit AllowNotFound404 behavior changed or retried.'
} finally {
    $script:DiagnosticFault = $null
    $script:NativeResources = $savedResources
    $script:NativeFilter = $savedFilter
}

# Untrusted metadata must never be interpolated, even when its exception also
# carries malicious inner/target/ErrorDetails/message values.
$validMeta = @{ MonitorNativeCategory = 'HttpFailure'; MonitorNativeMethod = 'Get'
    MonitorNativeTarget = 'OwnedAgent'; MonitorNativeStatus = 403 }
$badMetadata = @(@{}, @{ unrelated = $script:DiagnosticSecret })
foreach ($key in $validMeta.Keys) {
    $missing = $validMeta.Clone(); $missing.Remove($key); $badMetadata += @($missing)
    foreach ($value in @(
        @{ value = $script:DiagnosticSecret }, @{ value = '' }, @{ value = $null },
        @{ value = @() }, @{ value = $false }
    )) {
        $bad = $validMeta.Clone(); $bad[$key] = $value.value; $badMetadata += @($bad)
    }
}
foreach ($status in @('403', -1, 0, 99, 200, 299, 600, [long]403)) {
    $bad = $validMeta.Clone(); $bad.MonitorNativeStatus = $status; $badMetadata += @($bad)
}
$bad = $validMeta.Clone(); $bad.extra = $script:DiagnosticSecret; $badMetadata += @($bad)
foreach ($kind in @('TransportFailure','ParseFailure')) {
    $bad = $validMeta.Clone(); $bad.MonitorNativeCategory = $kind; $badMetadata += @($bad)
}
$maliciousTransport = {
    $errorException = [InvalidOperationException]::new($script:DiagnosticSecret, [Exception]::new($script:DiagnosticSecret))
    foreach ($key in $script:DiagnosticMetadata.Keys) { $errorException.Data[$key] = $script:DiagnosticMetadata[$key] }
    $record = [Management.Automation.ErrorRecord]::new($errorException, $script:DiagnosticSecret,
        [Management.Automation.ErrorCategory]::NotSpecified, $script:DiagnosticSecret)
    $record.ErrorDetails = [Management.Automation.ErrorDetails]::new($script:DiagnosticSecret)
    throw $record
}
foreach ($script:DiagnosticMetadata in $badMetadata) {
    Assert-SafeDiagnosticError { Invoke-MonitorTransport $maliciousTransport PublishResource } `
        'Monitor adapter failed during PublishResource; outcome may be unknown. Re-read before retry; response details suppressed.'
}
$script:DiagnosticMetadata = $validMeta
Assert-SafeDiagnosticError { Invoke-MonitorTransport $maliciousTransport $script:DiagnosticSecret } `
    'Monitor adapter failed during UnknownOperation; outcome may be unknown. Re-read before retry; response details suppressed.'
Assert-Rejected { Invoke-MonitorTransport { $null } $script:DiagnosticSecret } '^Monitor adapter omitted UnknownOperation readback\.$'
Write-Output "$($script:Checks - $diagnosticStart) safe diagnostic probes passed; complete request/operation/transport path and rendered errors."

# Synthetic validation bodies only: no actual publication response body was
# observed. Recognize one reviewed nested code, never arbitrary values/keys.
$validationStart = $script:Checks
$pendingCode = 'IncidentPlatformConfigurationPending'
$pendingBody = '{"error":{"code":"IncidentPlatformConfigurationPending","message":"' + $script:DiagnosticSecret +
    '","details":{"' + $script:DiagnosticSecret + '":"https://sentinel.invalid/"}}}'
$bodyCases = @(@{ body = $pendingBody; classified = $true })
foreach ($code in @(
    'incidentPlatformConfigurationPending','IncidentPlatformConfigurationPending ',
    ' IncidentPlatformConfigurationPending','xIncidentPlatformConfigurationPending',
    'IncidentPlatformConfigurationPendingx', "`0IncidentPlatformConfigurationPending",
    "IncidentPlatform`0ConfigurationPending", "IncidentPlatformConfigurationPending`0",
    $script:DiagnosticSecret, '', $null, 0, $false,
    @('IncidentPlatformConfigurationPending'), @{ nested = 'IncidentPlatformConfigurationPending' }
)) {
    $bodyCases += @{ body = (ConvertTo-Json -InputObject @{ error = @{ code = $code; message = $script:DiagnosticSecret } } -Depth 8 -Compress); classified = $false }
}
foreach ($body in @(
    '{"errorCode":"IncidentPlatformConfigurationPending"}',
    '{"code":"IncidentPlatformConfigurationPending"}',
    '{"Error":{"code":"IncidentPlatformConfigurationPending"}}',
    '{"error":{"Code":"IncidentPlatformConfigurationPending"}}',
    '{"other":{"error":{"code":"IncidentPlatformConfigurationPending"}}}',
    '{"error":{"nested":{"code":"IncidentPlatformConfigurationPending"}}}',
    '[{"error":{"code":"IncidentPlatformConfigurationPending"}}]',
    '{"error":[{"code":"IncidentPlatformConfigurationPending"}]}',
    ('{"title":"IncidentPlatformConfigurationPending","detail":"' + $script:DiagnosticSecret + '"}'),
    ('{malformed ' + $script:DiagnosticSecret), '', 'null', '[]', 0, @{}, @()
)) { $bodyCases += @{ body = $body; classified = $false } }
$atLimit = $pendingBody + (' ' * (16384 - [Text.Encoding]::UTF8.GetByteCount($pendingBody)))
$bodyCases += @{ body = $atLimit; classified = $true }
$bodyCases += @{ body = ($atLimit + ' '); classified = $false }
$bodyCases += @{ body = ('{"error":{"code":"IncidentPlatformConfigurationPending"},"message":"' + ('é' * 9000) + '"}'); classified = $false }
$depth8 = '{"error":{"code":"IncidentPlatformConfigurationPending"},"extra":' + ('{"n":' * 7) + '0' + ('}' * 7) + '}'
$depth9 = '{"error":{"code":"IncidentPlatformConfigurationPending"},"extra":' + ('{"n":' * 8) + '0' + ('}' * 8) + '}'
$bodyCases += @{ body = $depth8; classified = $true }
$bodyCases += @{ body = $depth9; classified = $false }
$validationCases = @(
    foreach ($bodyCase in $bodyCases) {
        @{ body = $bodyCase.body; classified = $bodyCase.classified; status = 400; method = 'Put'
            path = $filterPath; target = 'OwnedIncidentFilter'; resource = $filterResource; operation = 'PublishResource' }
    }
    foreach ($method in @('Get','Patch')) {
        @{ body = $pendingBody; classified = $false; status = 400; method = $method
            path = $filterPath; target = 'OwnedIncidentFilter'; resource = $filterResource; operation = 'PublishResource' }
    }
    foreach ($status in @(302,403,500)) {
        @{ body = $pendingBody; classified = $false; status = $status; method = 'Put'
            path = $filterPath; target = 'OwnedIncidentFilter'; resource = $filterResource; operation = 'PublishResource' }
    }
    @{ body = $pendingBody; classified = $false; status = 400; method = 'Put'
        path = $agentPath; target = 'OwnedAgent'; resource = $expectedAgent; operation = 'PublishResource' }
    @{ body = $pendingBody; classified = $false; status = 400; method = 'Put'
        path = '/api/v2/extendedAgent/asre-azure-monitor-review/permissions'; target = 'AgentPermissions'; operation = 'SetAgentPermissions' }
)
try {
    foreach ($case in $validationCases) {
        $script:NativeResources = Copy-Fixture $savedResources; $script:NativeFilter = Copy-Fixture $savedFilter
        if ($case.method -ceq 'Put' -and $case.path -ceq $filterPath) {
            $script:NativeResources.Remove($filterPath); $script:NativeFilter = $null
        }
        $script:DiagnosticFault = $case.Clone(); $script:DiagnosticFault.kind = 'Http'
        $before = $script:NativeCalls.Count
        $request = $(if ($case.operation -ceq 'SetAgentPermissions') {
            @{ permissions = @{ allow = @() } }
        } else { @{ resource = $case.resource } })
        $detail = "HTTP $($case.status)"
        if ($case.classified) { $detail += '; IncidentPlatformConfigurationPending' }
        Assert-SafeDiagnosticError { Invoke-MonitorTransport $diagnosticTransport $case.operation $request } `
            "Monitor adapter failed during $($case.operation) ($detail; $($case.method); $($case.target)); outcome may be unknown. Re-read before retry; response details suppressed."
        $calls = @($script:NativeCalls | Select-Object -Skip $before)
        Assert-Fixture ($calls.Count -eq $(if ($case.method -ceq 'Get') { 1 } else { 2 }) -and
            $calls[-1].method -ceq $case.method) 'Validation diagnostics added requests, retry or changed a verb.'
    }
} finally {
    $script:DiagnosticFault = $null
    $script:NativeResources = $savedResources; $script:NativeFilter = $savedFilter
}
$extendedMeta = @{ MonitorNativeCategory = 'HttpFailure'; MonitorNativeMethod = 'Put'
    MonitorNativeTarget = 'OwnedIncidentFilter'; MonitorNativeStatus = 400
    MonitorNativeValidation = 'IncidentPlatformConfigurationPending' }
$script:DiagnosticMetadata = $extendedMeta
Assert-SafeDiagnosticError { Invoke-MonitorTransport $maliciousTransport PublishResource } `
    'Monitor adapter failed during PublishResource (HTTP 400; IncidentPlatformConfigurationPending; Put; OwnedIncidentFilter); outcome may be unknown. Re-read before retry; response details suppressed.'
$extensionMutations = @()
foreach ($key in $extendedMeta.Keys) {
    $bad = $extendedMeta.Clone(); $bad.Remove($key)
    if ($key -cne 'MonitorNativeValidation') { $extensionMutations += @{ meta = $bad; operation = 'PublishResource' } }
    foreach ($value in @(@{ value = $script:DiagnosticSecret }, @{ value = $null }, @{ value = @() }, @{ value = $false })) {
        $bad = $extendedMeta.Clone(); $bad[$key] = $value.value
        $extensionMutations += @{ meta = $bad; operation = 'PublishResource' }
    }
}
foreach ($code in @('incidentPlatformConfigurationPending','IncidentPlatformConfigurationPending ',
    'xIncidentPlatformConfigurationPending','IncidentPlatformConfigurationPendingx',
    "`0IncidentPlatformConfigurationPending", "IncidentPlatform`0ConfigurationPending",
    "IncidentPlatformConfigurationPending`0")) {
    $bad = $extendedMeta.Clone(); $bad.MonitorNativeValidation = $code
    $extensionMutations += @{ meta = $bad; operation = 'PublishResource' }
}
foreach ($method in @('Get','Patch')) {
    $bad = $extendedMeta.Clone(); $bad.MonitorNativeMethod = $method
    $extensionMutations += @{ meta = $bad; operation = 'PublishResource' }
}
foreach ($target in @('OwnedAgent','AgentPermissions','OtherAllowedRequest')) {
    $bad = $extendedMeta.Clone(); $bad.MonitorNativeTarget = $target
    $extensionMutations += @{ meta = $bad; operation = 'PublishResource' }
}
foreach ($status in @(403,500,'400',[long]400)) {
    $bad = $extendedMeta.Clone(); $bad.MonitorNativeStatus = $status
    $extensionMutations += @{ meta = $bad; operation = 'PublishResource' }
}
foreach ($operation in @('ReadState','ReadResource','SetAgentPermissions','SetPlatform','VerifyRuntime',$script:DiagnosticSecret)) {
    $extensionMutations += @{ meta = $extendedMeta; operation = $operation }
}
$bad = $extendedMeta.Clone(); $bad.extra = $script:DiagnosticSecret
$extensionMutations += @{ meta = $bad; operation = 'PublishResource' }
foreach ($mutation in $extensionMutations) {
    $script:DiagnosticMetadata = $mutation.meta
    $safeOperation = $(if ($mutation.operation -ceq $script:DiagnosticSecret) { 'UnknownOperation' } else { $mutation.operation })
    Assert-SafeDiagnosticError { Invoke-MonitorTransport $maliciousTransport $mutation.operation } `
        "Monitor adapter failed during $safeOperation; outcome may be unknown. Re-read before retry; response details suppressed."
}
# Removing the optional extension restores the unchanged four-field contract.
$script:DiagnosticMetadata = $extendedMeta.Clone(); $script:DiagnosticMetadata.Remove('MonitorNativeValidation')
Assert-SafeDiagnosticError { Invoke-MonitorTransport $maliciousTransport PublishResource } `
    'Monitor adapter failed during PublishResource (HTTP 400; Put; OwnedIncidentFilter); outcome may be unknown. Re-read before retry; response details suppressed.'
Write-Output "$($script:Checks - $validationStart) single-code validation probes passed; $($validationCases.Count) synthetic native cases and $($extensionMutations.Count) hostile metadata mutations."
$captureStart = $script:Checks
$captureSavedManifest = ${function:Get-MonitorManifest}
$captureSavedContext = ${function:Get-MonitorNativeContext}
$captureSavedRequest = ${function:Invoke-MonitorNativeRequest}
$captureSavedState = ${function:Get-MonitorNativeState}
$captureSavedArtifact = ${function:Read-MonitorPublicationArtifact}
$captureSavedRun = ${function:Get-MonitorProtectedPublicationRun}
$captureSavedProof = ${function:Get-MonitorCapturePublicationProof}
$captureSavedAssert = ${function:Assert-MonitorCaptureArtifact}
try {
    Set-Item Function:Get-MonitorManifest $script:OriginalMonitorManifest
    $captureWindow = @{not_before=[DateTimeOffset]::UtcNow.AddSeconds(-10).ToString('o');expires_at=[DateTimeOffset]::UtcNow.AddMinutes(8).ToString('o')}
    $capture = Get-MonitorNativeCaptureConfiguration -Root $repo -Window $captureWindow
    $legacy = Get-MonitorManifest -Root $repo
    Assert-Fixture ($legacy.response_plan.properties.alertId -match '/alerts/') 'Capture changed historical discovery.'
    Assert-Fixture ($capture.plan.properties.alertId -ceq '' -and -not $capture.plan.properties.isEnabled -and
        $capture.plan.properties.agentMode -ceq 'Review') 'Candidate filter did not clear the old ID or preserve Review/disabled source.'
    Assert-MonitorExact @{targetResourceType='Microsoft.OperationalInsights/workspaces';targetResource=$legacy.scope.workspace_resource_id} $capture.plan.properties.azMonitorFilterSettings
    foreach ($key in @('tools','mcpTools','commonTools','handoffs','connectors','agentsAsTools','allowedSkills')) {
        Assert-Fixture ($capture.agent.properties[$key].Count -eq 0) "Capture grants $key"
    }
    Assert-Fixture (-not $capture.agent.properties.enableSkills -and -not $capture.agent.properties.addSystemSkills -and
        $capture.agent.properties.hooks.PreToolUse[0].script.Contains('sys.exit(2)') -and
        $capture.agent.properties.instructions.Contains('Candidate routing is NOT eligibility')) 'Capture lost unconditional deny-all or candidate distinction.'
    $query = @"
Perf
| where _ResourceId =~ "$($legacy.scope.resource_id)"
| where ObjectName == "Logical Disk" and CounterName == "% Used Space"
| where InstanceName == "/srv/asre-demo"
| summarize Value = max(CounterValue) by _ResourceId
| where Value >= 80
"@
    $rule = @{id=$legacy.scope.rule_id;properties=@{enabled=$true;scopes=@($legacy.scope.workspace_resource_id);
        severity=1;autoMitigate=$true;evaluationFrequency='PT1M';windowSize='PT10M';
        criteria=@{allOf=@(@{query=$query;timeAggregation='Count';operator='GreaterThan';threshold=0;
            failingPeriods=@{numberOfEvaluationPeriods=1;minFailingPeriodsToAlert=1}})}}}
    Assert-MonitorNativeCaptureRule $rule $legacy.scope
    $actualRule=Copy-Fixture $rule
    $actualRule.properties.criteria.allOf[0].threshold=0.0
    Assert-MonitorNativeCaptureRule $actualRule $legacy.scope
    foreach($threshold in @($null,$false,$true,'0',1,-1,0.1,[double]::NaN,[double]::PositiveInfinity,@(),@{})){
        $bad=Copy-Fixture $rule
        $bad.properties.criteria.allOf[0].threshold=$threshold
        Assert-Rejected {Assert-MonitorNativeCaptureRule $bad $legacy.scope} 'numeric zero'
    }
    foreach ($key in @('enabled','evaluationFrequency','windowSize')) {
        $bad=Copy-Fixture $rule
        $bad.properties[$key]=$(if($key -ceq 'enabled'){$false}else{'PT1H'})
        Assert-Rejected { Assert-MonitorNativeCaptureRule $bad $legacy.scope } 'Capture|capture|Strict readback'
    }
    $bad=Copy-Fixture $rule
    $bad.properties.criteria.allOf[0].query=$query.Replace('/srv/asre-demo','/')
    Assert-Rejected {Assert-MonitorNativeCaptureRule $bad $legacy.scope} 'dedicated volume'
    $bad=Copy-Fixture $rule
    $bad.properties.criteria.allOf[0].ready=$true
    Assert-Rejected {Assert-MonitorNativeCaptureRule $bad $legacy.scope} 'Unsupported'
    $script:CaptureCalls=@()
    $script:CaptureFixture=$capture
    $script:CaptureRule=$rule
    function Get-MonitorNativeContext {
        param($Root,$Action,$AuthorityWindow)
        return @{root=$Root;action=$Action;authorityAction=$Action;authorityWindow=$AuthorityWindow;readOnly=($Action -ceq 'Preflight')}
    }
    function Read-MonitorPublicationArtifact { param($Directory,$Root); return @{} }
    function Get-MonitorProtectedPublicationRun { param($Artifact); return @{source_only_fixture=$true} }
    function Assert-MonitorCaptureArtifact { param($Artifact) }
    function Get-MonitorCapturePublicationProof { param($Artifact,$Capture,$PublicationRunId,$Root,$Window); return @{} }
    function Get-MonitorNativeState {
        param($Context)
        return @{permissions=@{allow=@();ask=@();deny=@()};agentPermissions=@{allow=@()};
            permissionsEtag='"global"';agentPermissionsEtag='"agent"';
            platformConfiguration=@{type='AzMonitor';connectionName='azmonitor';oboUser=''};
            filters=@($script:CaptureFixture.plan)}
    }
    function Invoke-MonitorNativeRequest {
        param($Context,$Service,$Method,$Path,$Body,$IfMatch)
        $script:CaptureCalls+=@{method=$Method;path=$Path;body=$Body}
        if($Method -cne 'Get'){throw 'inert lost activation response'}
        if($Path -like '*checkConnectivityDetailed'){return @{data=@{success=$true;incidentType='AzMonitor'}}}
        if($Path -like '*scheduledQueryRules*'){return @{data=$script:CaptureRule;etag='"rule"'}}
        if($Path -like '*incidentFilters/*'){return @{data=$script:CaptureFixture.plan;etag='"filter"'}}
        return @{data=$script:CaptureFixture.agent;etag='"handler"'}
    }
    $failed=Invoke-MonitorNativeCapture -Action Enable -Root $repo -CaptureWindow $captureWindow -PublicationArtifactDirectory 'inert'
    Assert-Fixture ($failed.status -ceq 'UNKNOWN' -and $failed.stop_further_injection -and $failed.reconcile_required) 'Uncertain capture activation did not retain UNKNOWN.'
    $writes=@($script:CaptureCalls|Where-Object method -CNE Get)
    Assert-Fixture ($writes.Count -eq 1 -and $writes[0].body.properties.isEnabled -and $writes[0].method -ceq 'Patch') 'Capture disabled/paused/compensated or retried after uncertainty.'
    $script:CaptureFixture.plan.properties.isEnabled=$true
    $ctx=@{root=$repo;action='Preflight';authorityAction='Preflight';readOnly=$true;nativeCapture=$true}
    $proof=Test-MonitorNativeCaptureReadiness $ctx $captureWindow
    Assert-Fixture ($proof.status -ceq 'verified' -and $proof.version -ceq 'asre-native-capture-readiness/v1') 'Real capture verifier did not inspect fixed native resources.'
    $script:CaptureRule.properties.enabled=$false
    Assert-Rejected {Test-MonitorNativeCaptureReadiness $ctx $captureWindow} 'enabled rule'
    # Exercise the REAL final native request gate, not the lifecycle mock.
    Set-Item Function:Invoke-MonitorNativeRequest $captureSavedRequest
    $guardContext=@{root=$repo;authorityAction='Enable';authorityWindow=$captureWindow;readOnly=$false;nativeCapture=$true;
        agent='/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.App/agents/asre-demo-cus';
        endpoint='https://inert.invalid';armHeaders=@{};sreHeaders=@{}}
    $disabled=Get-MonitorNativeCaptureConfiguration -Root $repo -Window $captureWindow
    $body=@{name=$disabled.plan.name;type=$disabled.plan.type;properties=$disabled.plan.properties}
    Assert-Rejected {Invoke-MonitorNativeRequest $guardContext Sre Patch '/api/v2/incidentManagement/incidentFilters/asre-azure-monitor-review' -Body $body -IfMatch '"actual"'} 'capture-source-exact-write'
    $body.properties.isEnabled=$true
    Assert-Rejected {Invoke-MonitorNativeRequest $guardContext Sre Patch '/api/v2/incidentManagement/incidentFilters/asre-azure-monitor-review' -Body $body} 'requires an actual ETag'
    $guardContext.authorityWindow=@{not_before=[DateTimeOffset]::UtcNow.AddMinutes(-5).ToString('o');expires_at=[DateTimeOffset]::UtcNow.AddMinutes(-1).ToString('o')}
    Assert-Rejected {Invoke-MonitorNativeRequest $guardContext Sre Patch '/api/v2/incidentManagement/incidentFilters/asre-azure-monitor-review' -Body $body -IfMatch '"actual"'} 'not currently valid'
} finally {
    Set-Item Function:Get-MonitorManifest $captureSavedManifest
    Set-Item Function:Get-MonitorNativeContext $captureSavedContext
    Set-Item Function:Invoke-MonitorNativeRequest $captureSavedRequest
    Set-Item Function:Get-MonitorNativeState $captureSavedState
    Set-Item Function:Read-MonitorPublicationArtifact $captureSavedArtifact
    Set-Item Function:Get-MonitorProtectedPublicationRun $captureSavedRun
    Set-Item Function:Get-MonitorCapturePublicationProof $captureSavedProof
    Set-Item Function:Assert-MonitorCaptureArtifact $captureSavedAssert
}
Write-Output "$($script:Checks - $captureStart) fresh-capture candidate/readiness/non-shutdown checks passed; offline only."
$intakeStart=$script:Checks
$intakeSaved=@{}
foreach($name in @('az','git','Invoke-WebRequest','Get-MonitorNativeContext','Invoke-MonitorNativeRequest')){
    $function=Get-Item "Function:$name" -ErrorAction Ignore
    $intakeSaved[$name]=$(if($null -ne $function){$function.ScriptBlock}else{$null})
}
$intakeTemp=Join-Path ([IO.Path]::GetTempPath()) ('asre-intake-proof-'+[guid]::NewGuid().ToString('N'))
$intakeEnvironment=@{}
foreach($name in @('BUILD_BUILDID','BUILD_REASON','BUILD_SOURCEBRANCH','SYSTEM_COLLECTIONURI','SYSTEM_DEFINITIONID')){
    $intakeEnvironment[$name]=[Environment]::GetEnvironmentVariable($name)
    [Environment]::SetEnvironmentVariable($name,$null)
}
try {
    $w=@{not_before=[DateTimeOffset]::UtcNow.AddSeconds(-5).ToString('o');expires_at=[DateTimeOffset]::UtcNow.AddMinutes(8).ToString('o')}
    $w2=@{not_before=[DateTimeOffset]::UtcNow.AddSeconds(-2).ToString('o');expires_at=[DateTimeOffset]::UtcNow.AddMinutes(7).ToString('o')}
    $a=Get-MonitorNativeCaptureConfiguration $repo $w;$a2=Get-MonitorNativeCaptureConfiguration $repo $w2
    # The preceding negative rule probe intentionally disabled its fixture.
    $rule=Copy-Fixture $rule
    $rule.properties.enabled=$true
    Assert-Fixture ((Get-MonitorObjectSha256 $a.agent) -ceq (Get-MonitorObjectSha256 $a2.agent) -and
        (Get-MonitorObjectSha256 $a.plan) -ceq (Get-MonitorObjectSha256 $a2.plan)) 'Activation window leaked into deployed definitions.'
    Assert-Fixture (-not $a.agent.properties.instructions.Contains('ASRE_NATIVE_CAPTURE_WINDOW=')) 'Static intake carries a fault window.'
    $b=Get-MonitorCaptureReleaseBinding
    $now=[DateTimeOffset]::UtcNow
    $rc=@{collection_uri=$b.collection_uri;project_id=$b.project_id;definition_id='8';repository_id=$b.repository_id
        template_repository='alz-landingzone/alz-mgmt-templates';source_commit=('a'*40);template_commit=$b.template_commit
        source_branch='refs/heads/master';validation_run_id='101';created_at=$now.AddMinutes(-10).ToString('o')}
    $script:IntakeArtifact=@{release_context=$rc;source_commit=$rc.source_commit;template_commit=$b.template_commit;manifest_sha256=('b'*64)}
    $script:IntakeResponses=@{}
    $script:IntakeResponses['/build/definitions/8']=@{id=8;revision=13;queueStatus='enabled';project=@{id=$b.project_id};repository=@{id=$b.repository_id}}
    foreach($id in @(101,102)){
        $script:IntakeResponses["/build/builds/$id"]=@{id=$id;status='completed';result='succeeded';reason='manual';sourceBranch='refs/heads/master'
            sourceVersion=$rc.source_commit;project=@{id=$b.project_id};definition=@{id=8}
            repository=@{id=$b.repository_id;type='TfsGit'}
            startTime=$now.AddMinutes($(if($id -eq 101){-12}else{-8})).ToString('o')
            finishTime=$now.AddMinutes($(if($id -eq 101){-9}else{-1})).ToString('o')}
        $script:IntakeResponses["/pipelines/8/runs/$id"]=@{id=$id;pipeline=@{id=8;revision=13};state='completed';result='succeeded'
            resources=@{repositories=@{self=@{repository=@{id=$b.repository_id;type='azureReposGit'};refName='refs/heads/master';version=$rc.source_commit}
                templates=@{repository=@{id=$b.template_repository_id;type='azureReposGit'};refName=$b.template_ref;version=$b.template_commit}}}}
    }
    $script:IntakeResponses['/build/builds/102/timeline']=@{records=@(
        @{id='stage';parentId=$null;type='Stage';name='Protected disabled publication and strict readback only';state='completed';result='succeeded'},
        @{id='job';parentId='stage';type='Job';name='ConfigurationPublishDisabled';state='completed';result='succeeded'},
        @{id='guard';parentId='job';type='Task';name='Guard successful exact-source configuration-only provenance before cloud';state='completed';result='succeeded'},
        @{id='verify';parentId='job';type='Task';name='Verify all artifact bytes and context before cloud login';state='completed';result='succeeded'},
        @{id='publisher';parentId='job';type='Task';name='Delegate only disabled publication and strict readback to the frozen publisher';state='completed';result='succeeded';log=@{id=23}})}
    function Set-IntakeLog {
        $receipt=New-MonitorCapturePublicationReceipt $script:IntakeArtifact $script:IntakeConfig '102'
        $ctx=@{version='asre-native-capture-context/v1';receipt_sha256=(Get-MonitorObjectSha256 $receipt);release_context=$script:IntakeArtifact.release_context}
        $script:IntakeLog="ASRE_NATIVE_CAPTURE_PUBLICATION_V1=$($receipt|ConvertTo-Json -Depth 40 -Compress)`nASRE_NATIVE_CAPTURE_CONTEXT_V1=$($ctx|ConvertTo-Json -Depth 40 -Compress)"
    }
    $script:IntakeConfig=$a;Set-IntakeLog
    $script:IntakeRequests=@();$script:IntakeTokenChange=@{}
    function git {$global:LASTEXITCODE=0;return ('a'*40)}
    function az {
        $global:LASTEXITCODE=0
        if($args -contains '--tenant' -or @($args|Where-Object {$_ -ceq '--subscription'}).Count -ne 1){throw 'Incompatible or implicit subscription argv'}
        $authority=(Get-MonitorManifest -Root $repo).activation_authority
        $c=@{tid=$authority.tenant_id;oid=$authority.object_id;appid=$authority.client_id
            aud='499b84ac-1321-427f-aa17-267ca6975798';exp=[DateTimeOffset]::UtcNow.AddHours(1).ToUnixTimeSeconds();nbf=0}
        foreach($key in $script:IntakeTokenChange.Keys){$c[$key]=$script:IntakeTokenChange[$key]}
        $middle=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($c|ConvertTo-Json -Compress))).TrimEnd('=').Replace('+','-').Replace('/','_')
        return "inert.$middle.synthetic"
    }
    function Invoke-WebRequest {
        param($Uri,$Method,$Headers,$MaximumRedirection,$TimeoutSec,[switch]$SkipHttpErrorCheck,$ErrorAction)
        $script:IntakeRequests+=@($Uri)
        $prefix='https://dev.azure.com/example-org/00000016-000a-4000-8000-000000000016/_apis'
        if(-not $Uri.StartsWith($prefix,[StringComparison]::Ordinal) -or $Method -cne 'Get' -or $MaximumRedirection -ne 0){throw 'Unfixed ADO request'}
        $key=$Uri.Substring($prefix.Length).Split('?')[0]
        $text=$(if($key -ceq '/build/builds/102/logs/23'){$script:IntakeLog}else{$script:IntakeResponses[$key]|ConvertTo-Json -Depth 40 -Compress})
        return @{StatusCode=200;Content=$text;Headers=@{}}
    }
    $proof=Get-MonitorCapturePublicationProof $script:IntakeArtifact $a 102 $repo $w
    Assert-Fixture ($proof.publication_run_id -ceq '102' -and $script:IntakeRequests.Count -eq 7) 'Actual fixed-read verifier did not prove publication.'
    $directTimeline=Copy-Fixture $script:IntakeResponses['/build/builds/102/timeline']
    $phase=@{id='phase';parentId='stage';type='Phase';name='ConfigurationPublishDisabled';state='completed';result='succeeded'}
    $script:IntakeResponses['/build/builds/102/timeline'].records+=@($phase)
    $script:IntakeResponses['/build/builds/102/timeline'].records[1].parentId='phase'
    $proof=Get-MonitorCapturePublicationProof $script:IntakeArtifact $a 102 $repo $w
    Assert-Fixture ($proof.publication_run_id -ceq '102') 'Actual Stage/Phase/Job deployment ancestry was rejected.'
    $phase.result='skipped'
    Assert-Rejected {Get-MonitorCapturePublicationProof $script:IntakeArtifact $a 102 $repo $w} 'Publication was skipped'
    $phase.result='succeeded';$phase.parentId='foreign-stage'
    Assert-Rejected {Get-MonitorCapturePublicationProof $script:IntakeArtifact $a 102 $repo $w} 'job missing'
    $phase.parentId='stage'
    $script:IntakeResponses['/build/builds/102/timeline'].records+=@(Copy-Fixture $phase)
    Assert-Rejected {Get-MonitorCapturePublicationProof $script:IntakeArtifact $a 102 $repo $w} 'phase is ambiguous'
    $script:IntakeResponses['/build/builds/102/timeline']=$directTimeline
    foreach($name in $intakeEnvironment.Keys){[Environment]::SetEnvironmentVariable($name,'spoofed-protected-environment')}
    $script:IntakeResponses['/build/builds/102/timeline'].records[4].result='skipped'
    Assert-Rejected {Get-MonitorCapturePublicationProof $script:IntakeArtifact $a 102 $repo $w} 'Publication was skipped'
    $script:IntakeResponses['/build/builds/102/timeline'].records[4].result='succeeded'
    foreach($name in $intakeEnvironment.Keys){[Environment]::SetEnvironmentVariable($name,$null)}
    Assert-Rejected {Invoke-MonitorCaptureAdoRead 'https://foreign.invalid/api' @{} $w} 'escaped'
    foreach($change in @(@{tid='foreign'},@{appid='foreign'},@{aud='foreign'},@{exp=0},@{nbf=[DateTimeOffset]::UtcNow.AddHours(1).ToUnixTimeSeconds()})){
        $script:IntakeTokenChange=$change
        Assert-Rejected {Get-MonitorCaptureAdoHeaders $repo $w} 'authentication failed'
    }
    $script:IntakeTokenChange=@{}
    $originalResponses=Copy-Fixture $script:IntakeResponses
    foreach($mutation in @('origin','run','repo','branch','commit','definition','template','stage','job','step','context','receipt')){
        $script:IntakeResponses=Copy-Fixture $originalResponses
        $artifact=Copy-Fixture $script:IntakeArtifact
        Set-IntakeLog
        switch($mutation){
            origin {$artifact.release_context.collection_uri='https://foreign.invalid/'}
            run {$script:IntakeResponses['/build/builds/102'].id=103}
            repo {$script:IntakeResponses['/build/builds/102'].repository.id='foreign'}
            branch {$script:IntakeResponses['/build/builds/102'].sourceBranch='refs/heads/main'}
            commit {$script:IntakeResponses['/pipelines/8/runs/102'].resources.repositories.self.version='c'*40}
            definition {$script:IntakeResponses['/build/definitions/8'].revision=14}
            template {$script:IntakeResponses['/pipelines/8/runs/102'].resources.repositories.templates.version='c'*40}
            stage {$script:IntakeResponses['/build/builds/102/timeline'].records[0].result='skipped'}
            job {$script:IntakeResponses['/build/builds/102/timeline'].records[1].result='skipped'}
            step {$script:IntakeResponses['/build/builds/102/timeline'].records[4].result='skipped'}
            context {$script:IntakeLog=$script:IntakeLog.Replace('"asre-native-capture-context/v1"','"foreign"')}
            receipt {$script:IntakeLog=$script:IntakeLog.Replace('"publication_run_id":"102"','"publication_run_id":"103"')}
        }
        Assert-Rejected {Get-MonitorCapturePublicationProof $artifact $a 102 $repo $w} 'binding|Actual|Fixed|Publication|Bootstrap'
    }
    $script:IntakeResponses=Copy-Fixture $originalResponses;Set-IntakeLog
    # Complete genuine-format disposable artifact: production file validator
    # remains real, while templates/generated content are inert fixture bytes.
    $null=New-Item -ItemType Directory -Path $intakeTemp
    $tokens=$null;$errors=$null
    $releaseAst=[Management.Automation.Language.Parser]::ParseFile((Join-Path $repo 'scripts/Invoke-IncidentRelease.ps1'),[ref]$tokens,[ref]$errors)
    $files=@{}
    foreach($var in @('SourceFiles','TemplateFiles')){
        $assignment=$releaseAst.Find({param($n)$n -is [Management.Automation.Language.AssignmentStatementAst] -and $n.Left.ToString() -ceq ('$'+$var)}.GetNewClosure(),$true)
        foreach($name in $assignment.Right.Expression.SafeGetValue()){
            $relative=$(if($var -ceq 'SourceFiles'){'source/'}else{'templates/'})+$name
            $path=Join-Path $intakeTemp $relative;$null=New-Item -ItemType Directory -Path (Split-Path $path -Parent) -Force
            if($var -ceq 'SourceFiles'){Copy-Item -LiteralPath (Join-Path $repo $name) -Destination $path}else{[IO.File]::WriteAllText($path,'inert template fixture')}
            $files[$relative]=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    $generated=Join-Path $intakeTemp 'generated/case-tool.py';$null=New-Item -ItemType Directory -Path (Split-Path $generated -Parent)
    [IO.File]::WriteAllText($generated,'# inert qualification artifact')
    $files['generated/case-tool.py']=(Get-FileHash $generated).Hash.ToLowerInvariant()
    $rc.artifact_kind='asre-configuration/v1';$rc.profile='asre-configuration';$rc.publication_action='PublishDisabled';$rc.runtime_accepted=$false
    $rc.agent_resource_id='/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.App/agents/asre-demo-cus'
    $rc.agent_endpoint='https://asre-demo-cus--0a1b2c3d.4e5f6a7b.centralus.azuresre.ai';$rc.tenant_id='00000002-000a-4000-9000-000000000002'
    $rc.subscription_id='00000001-000a-4000-a000-000000000001';$rc.files=$files
    $rc|ConvertTo-Json -Depth 40|Set-Content (Join-Path $intakeTemp 'configuration-manifest.json') -Encoding utf8
    $script:IntakeArtifact=Read-MonitorPublicationArtifact $intakeTemp $repo;Set-IntakeLog
    $script:IntakePlan=Copy-Fixture $a.plan;$script:IntakeWrites=@();$script:IntakeBad='';$script:IntakeLost=$false
    function Get-MonitorNativeContext {
        param($Action,$Root,$AuthorityWindow)
        return @{root=$Root;authorityAction=$Action;authorityWindow=$AuthorityWindow;readOnly=($Action -ceq 'Preflight')
            agent=$script:IntakeArtifact.release_context.agent_resource_id;endpoint=$script:IntakeArtifact.release_context.agent_endpoint}
    }
    function Invoke-MonitorNativeRequest {
        param($Context,$Service,$Method,$Path,$Body,$IfMatch,[switch]$AllowNotFound)
        if($Method -cne 'Get'){
            $script:IntakeWrites+=@{method=$Method;body=$Body;etag=$IfMatch}
            if($script:IntakeLost){throw 'synthetic lost PATCH response'}
            $script:IntakePlan.properties.isEnabled=$Body.properties.isEnabled
        }
        $data=$null
        if($Path -like '*checkConnectivityDetailed'){$data=@{success=$true;incidentType='AzMonitor'}}
        elseif($Path -like '*scheduledQueryRules*'){$data=Copy-Fixture $rule;if($script:IntakeBad -ceq 'rule'){$data.properties.enabled=$false}}
        elseif($Path -like '*/settings/global'){$data=@{permissions=@{allow=@();ask=@();deny=@()}};if($script:IntakeBad -ceq 'policy'){$data.permissions.allow=@('foreign')}}
        elseif($Path -ceq '/api/v2/incidentManagement/incidentFilters'){
            $data=@{value=@($script:IntakePlan);nextLink=$null}
            if($script:IntakeBad -ceq 'overlap'){$data.value+=@(@{name='foreign';properties=@{isEnabled=$true}})}
        }elseif($Path -like '*incidentFilters/*'){$data=$script:IntakePlan}
        elseif($Path -like '*/permissions'){$data=@{allow=@()}}
        elseif($Path -like '*extendedAgent/agents/*'){$data=$script:IntakeConfig.agent}
        else{$data=@{id=$Context.agent;properties=@{agentEndpoint=$Context.endpoint;actionConfiguration=@{mode='Review';accessLevel='Low'}
            incidentManagementConfiguration=@{type='AzMonitor';connectionName='azmonitor';oboUser=''}}}}
        return @{data=$data;etag='"actual-inert-revision"'}
    }
    foreach($bad in @('overlap','policy','rule')){
        $script:IntakeBad=$bad
        Assert-Rejected {Invoke-MonitorNativeCapture Enable $repo $w $intakeTemp -PublicationRunId 102} 'Capture|capture|Strict'
        Assert-Fixture ($script:IntakeWrites.Count -eq 0) 'Rejected intake state reached PATCH.'
    }
    $script:IntakeBad='';$script:IntakeLost=$true
    $expired=@{not_before=$now.AddMinutes(-5).ToString('o');expires_at=$now.AddMinutes(-1).ToString('o')}
    $beforeRequests=$script:IntakeRequests.Count
    Assert-Rejected {Invoke-MonitorNativeCapture Enable $repo $expired $intakeTemp -PublicationRunId 102} 'not currently valid'
    Assert-Fixture ($script:IntakeRequests.Count -eq $beforeRequests -and $script:IntakeWrites.Count -eq 0) 'Expired control reached authentication/ADO/PATCH.'
    $unknown=Invoke-MonitorNativeCapture Enable $repo $w $intakeTemp -PublicationRunId 102
    Assert-Fixture ($unknown.status -ceq 'UNKNOWN' -and $unknown.reconcile_required -and $script:IntakeWrites.Count -eq 1) 'Lost response retried or escaped UNKNOWN.'
    $script:IntakeWrites=@();$script:IntakeLost=$false
    $enabled=Invoke-MonitorNativeCapture Enable $repo $w $intakeTemp -PublicationRunId 102
    Assert-Fixture ($enabled.status -ceq 'capture-enabled' -and -not $enabled.runtime_accepted -and
        $script:IntakeWrites.Count -eq 1 -and $script:IntakeWrites[0].body.properties.isEnabled -and $script:IntakeWrites[0].etag) 'Desktop Enable did not perform one conditional enabling PATCH.'
    # Actual metadata contract: real request adapter and orchestration;
    # only authentication/HTTP boundaries are replaced.
    $metadataStart=$script:Checks
    $script:MetadataAdoHttp=${function:Invoke-WebRequest}
    Set-Item Function:Invoke-MonitorNativeRequest $intakeSaved['Invoke-MonitorNativeRequest']
    foreach($pair in @(@('SYSTEM_COLLECTIONURI',$rc.collection_uri),@('SYSTEM_TEAMPROJECTID',$rc.project_id),
        @('SYSTEM_DEFINITIONID','8'),@('BUILD_REPOSITORY_ID',$rc.repository_id),
        @('BUILD_SOURCEVERSION',$rc.source_commit),@('TEMPLATE_SOURCE_COMMIT',$rc.template_commit),
        @('BUILD_SOURCEBRANCH','refs/heads/master'),@('BUILD_REASON','Manual'),
        @('BUILD_REPOSITORY_PROVIDER','TfsGit'),@('BUILD_BUILDID','102'))){
        if(-not $intakeEnvironment.ContainsKey($pair[0])){$intakeEnvironment[$pair[0]]=[Environment]::GetEnvironmentVariable($pair[0])}
        [Environment]::SetEnvironmentVariable($pair[0],$pair[1])
    }
    function Get-MonitorNativeContext {
        param($Action,$Root,$AuthorityWindow)
        $script:MetadataContext=@{root=$Root;authorityAction=$Action;authorityWindow=$AuthorityWindow
            readOnly=($Action -ceq 'Preflight');armHeaders=@{};sreHeaders=@{}
            agent=$script:IntakeArtifact.release_context.agent_resource_id;endpoint=$script:IntakeArtifact.release_context.agent_endpoint}
        return $script:MetadataContext
    }
    function Reset-MetadataFixture {
        param($State='legacy',$Fault='')
        $script:MetadataHandler=Copy-Fixture $(if($State -ceq 'legacy'){@(Get-MonitorReviewConfiguration -Root $repo)[0]}else{$a.agent})
        if($State -ceq 'unknown'){$script:MetadataHandler.properties.instructions+=' foreign'}
        $script:MetadataPlan=Copy-Fixture $a.plan
        $script:MetadataPlan.properties.isEnabled=$false
        $script:MetadataCalls=@();$script:MetadataFault=$Fault
        $script:MetadataHeader=$null;$script:MetadataBody='"opaque-filter-actual"'
    }
    function Invoke-WebRequest {
        param($Uri,$Method,$Headers,$MaximumRedirection,$TimeoutSec,[switch]$SkipHttpErrorCheck,$ErrorAction,$Body,$ContentType)
        if($Uri.StartsWith('https://dev.azure.com/',[StringComparison]::Ordinal)){
            return & $script:MetadataAdoHttp -Uri $Uri -Method $Method -Headers $Headers -MaximumRedirection $MaximumRedirection -TimeoutSec $TimeoutSec -SkipHttpErrorCheck -ErrorAction $ErrorAction
        }
        $path=([uri]$Uri).AbsolutePath;$data=$null;$etag='"policy-actual"';$status=200
        $script:MetadataCalls+=@{method=$Method;path=$path;headers=$Headers.Clone();body=$Body}
        $handler='/api/v2/extendedAgent/agents/asre-azure-monitor-review'
        $filter='/api/v2/incidentManagement/incidentFilters/asre-azure-monitor-review'
        if($path -ceq $handler){
            $etag=$null
            if($Method -ceq 'Put'){
                if(-not $script:MetadataContext.captureHandlerAttempted){throw 'Attempt not consumed before HTTP'}
                $script:MetadataHandler=$Body|ConvertFrom-Json -AsHashtable
                if($script:MetadataFault -ceq 'lost'){throw 'synthetic lost handler response'}
            }
            $submitted=@($script:MetadataCalls|Where-Object {$_.path -ceq $handler -and $_.method -ceq 'Put'}).Count
            if($Method -ceq 'Get' -and $submitted -and $script:MetadataFault -ceq 'readfail'){throw 'synthetic readback failure'}
            $data=Copy-Fixture $script:MetadataHandler
            if($Method -ceq 'Get' -and $submitted -and $script:MetadataFault -ceq 'mismatch'){$data.properties.instructions+=' foreign'}
        }elseif($path -ceq $filter){
            $etag=$script:MetadataHeader
            if($Method -ceq 'Patch'){
                if($Headers['If-Match'] -cne '"opaque-filter-actual"'){throw 'Actual opaque If-Match changed'}
                if($script:MetadataFault -ceq 'precondition'){$status=412}
                else{$script:MetadataPlan=$Body|ConvertFrom-Json -AsHashtable}
            }
            $data=Copy-Fixture $script:MetadataPlan
            if($null -ne $script:MetadataBody){$data.properties.etag=$script:MetadataBody}
        }elseif($path -ceq '/api/v2/incidentManagement/incidentFilters'){
            $data=@{value=@($script:MetadataPlan);nextLink=$null}
        }elseif($path -like '*/settings/global'){$data=@{permissions=@{allow=@();ask=@();deny=@()}}}
        elseif($path -like '*/permissions'){$data=@{allow=@()}}
        elseif($path -like '*checkConnectivityDetailed'){$data=@{success=$true;incidentType='AzMonitor'}}
        elseif($path -like '*scheduledQueryRules*'){$data=$rule;$etag=$null}
        else{$data=@{id=$script:MetadataContext.agent;properties=@{agentEndpoint=$script:MetadataContext.endpoint
            actionConfiguration=@{mode='Review';accessLevel='Low'};incidentManagementConfiguration=@{type='AzMonitor';connectionName='azmonitor';oboUser=''}}}}
        $h=@{};if($null -ne $etag){$h.ETag=$etag}
        return @{StatusCode=$status;Content=($data|ConvertTo-Json -Depth 40 -Compress);Headers=$h}
    }
    Reset-MetadataFixture
    $published=Invoke-MonitorNativeCapture PublishDisabled $repo $w $intakeTemp
    $puts=@($script:MetadataCalls|Where-Object method -CEQ Put)
    Assert-Fixture ($published.status -ceq 'published-disabled' -and -not $published.runtime_accepted -and $puts.Count -eq 1) 'Revisionless exact legacy handler publication failed.'
    Assert-MonitorExact @{name=$a.agent.name;type=$a.agent.type;properties=$a.agent.properties;tags=@()} ($puts[0].body|ConvertFrom-Json -AsHashtable)
    Assert-Fixture (-not $puts[0].headers.ContainsKey('If-Match')) 'Handler invented a revision.'
    $putIndex=[array]::IndexOf($script:MetadataCalls,$puts[0])
    Assert-Fixture ($script:MetadataCalls[$putIndex+1].method -ceq 'Get' -and
        $script:MetadataCalls[$putIndex+1].path -ceq $puts[0].path) 'Handler readback was not immediate.'
    Assert-Fixture (@($script:MetadataCalls|Where-Object method -CEQ Patch)[0].headers['If-Match'] -ceq '"opaque-filter-actual"') 'Publication lost body ETag.'
    Reset-MetadataFixture desired
    $null=Invoke-MonitorNativeCapture PublishDisabled $repo $w $intakeTemp
    Assert-Fixture (@($script:MetadataCalls|Where-Object method -CEQ Put).Count -eq 0) 'Desired handler was not a no-op.'
    Reset-MetadataFixture unknown
    Assert-Rejected {Invoke-MonitorNativeCapture PublishDisabled $repo $w $intakeTemp} 'Strict'
    Assert-Fixture (@($script:MetadataCalls|Where-Object method -CNE Get).Count -eq 0) 'Unknown preimage reached mutation.'
    foreach($fault in @('lost','readfail','mismatch')){
        Reset-MetadataFixture legacy $fault
        Assert-Rejected {Invoke-MonitorNativeCapture PublishDisabled $repo $w $intakeTemp} 'UNKNOWN.*parent reconciliation'
        Assert-Fixture (@($script:MetadataCalls|Where-Object method -CEQ Put).Count -eq 1 -and
            @($script:MetadataCalls|Where-Object method -CEQ Patch).Count -eq 0) 'Uncertain handler retried or reached filter.'
        $handlerBody=@{name=$a.agent.name;type=$a.agent.type;properties=$a.agent.properties;tags=@()}
        Assert-Rejected {Invoke-MonitorNativeRequest $script:MetadataContext Sre Put '/api/v2/extendedAgent/agents/asre-azure-monitor-review' -Body $handlerBody} 'already attempted'
    }
    Reset-MetadataFixture desired
    $ctx=Get-MonitorNativeContext Enable $repo $w;$ctx.nativeCapture=$true
    foreach($pair in @(@($null,'"opaque-filter-actual"'),@('"opaque-filter-actual"',$null),@('"opaque-filter-actual"','"opaque-filter-actual"'))){
        $script:MetadataHeader=$pair[0];$script:MetadataBody=$pair[1]
        $actual=Invoke-MonitorNativeRequest $ctx Sre Get '/api/v2/incidentManagement/incidentFilters/asre-azure-monitor-review'
        Assert-Fixture ($actual.etag -ceq '"opaque-filter-actual"') 'Valid actual filter revision rejected/changed.'
    }
    foreach($pair in @(@($null,$null),@('"other"','"opaque-filter-actual"'),@('W/"weak"',$null),@('*',$null),
        @('"one", "two"',$null),@(@('"one"','"two"'),$null),@($null,'bad'),@($null,1),@($null,'W/"weak"'))){
        $script:MetadataHeader=$pair[0];$script:MetadataBody=$pair[1]
        Assert-Rejected {Invoke-MonitorNativeRequest $ctx Sre Get '/api/v2/incidentManagement/incidentFilters/asre-azure-monitor-review'} 'revision|ETag'
    }
    Reset-MetadataFixture desired
    $enabled=Invoke-MonitorNativeCapture Enable $repo $w $intakeTemp -PublicationRunId 102
    Assert-Fixture ($enabled.status -ceq 'capture-enabled' -and -not $enabled.runtime_accepted -and
        @($script:MetadataCalls|Where-Object method -CEQ Patch).Count -eq 1) 'Revisionless readiness/body-revision Enable failed.'
    Reset-MetadataFixture desired precondition
    $failed=Invoke-MonitorNativeCapture Enable $repo $w $intakeTemp -PublicationRunId 102
    Assert-Fixture ($failed.status -ceq 'UNKNOWN' -and $failed.reconcile_required -and
        @($script:MetadataCalls|Where-Object method -CEQ Patch).Count -eq 1 -and -not $script:MetadataPlan.properties.isEnabled) '412 retried or disabled intake.'
    $ctx=Get-MonitorNativeContext PublishDisabled $repo $w;$ctx.nativeCapture=$true
    $before=$script:MetadataCalls.Count
    foreach($target in @(@('Sre','Put','/api/v2/incidentManagement/incidentFilters/asre-azure-monitor-review'),
        @('Sre','Put','/api/v2/extendedAgent/asre-azure-monitor-review/permissions'),
        @('Arm','Patch',"$($ctx.agent)?api-version=2026-01-01"),
        @('Sre','Put','/api/v2/extendedAgent/tools/asre-review-evidence'))){
        Assert-Rejected {Invoke-MonitorNativeRequest $ctx $target[0] $target[1] $target[2] -Body $handlerBody} 'Capture forbids|capture-source'
    }
    Assert-Fixture ($script:MetadataCalls.Count -eq $before) 'Handler exception escaped to another write.'
    Write-Output "$($script:Checks-$metadataStart) actual-metadata/revisionless-handler checks passed; real adapter/orchestration, HTTP/auth mocked."
} finally {
    foreach($name in $intakeEnvironment.Keys){[Environment]::SetEnvironmentVariable($name,$intakeEnvironment[$name])}
    foreach($name in $intakeSaved.Keys){
        if($null -ne $intakeSaved[$name]){Set-Item "Function:$name" $intakeSaved[$name]}
        else{Remove-Item "Function:$name" -ErrorAction Ignore}
    }
    if(Test-Path -LiteralPath $intakeTemp){Remove-Item -LiteralPath $intakeTemp -Recurse -Force}
}
Write-Output "$($script:Checks-$intakeStart) static-intake/fixed-ADO/publication-proof checks passed; auth/HTTP mocked."
Write-Output "$($script:Checks) total workflow/default-adapter checks passed; all CLI and HTTP mocked."
