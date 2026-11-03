$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repo = Split-Path $PSScriptRoot -Parent
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    (Join-Path $repo 'scripts\Initialize-ReviewAgent.ps1'), [ref]$tokens, [ref]$errors
)
if ($errors.Count) { throw 'The native workflow initializer did not parse.' }
foreach ($name in @('Get-ReviewKnowledge', 'Invoke-NativeSystemTool', 'Assert-NativePilotScope', 'Publish-ReviewKnowledge', 'Test-OwnedReviewPilot', 'Get-NativeIncidentFilters', 'Assert-NativeAutomationDisabled')) {
    $definition = @($ast.FindAll({
        param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name
    }, $true))
    if ($definition.Count -ne 1) { throw "The production function is not unique: $name" }
    . ([scriptblock]::Create($definition[0].Extent.Text))
}
$script:CaseGroup = '0000000000000000000000000000007a'
$script:IncomingGroup = 'ASRE Demo Operations'
$script:ToolSuccess = $true
$script:CaseOverrides = @{}
$script:OmitCaseCi = $false
$script:Uploads = @()
$script:Listings = 0
$script:PendingOnce = $false
$script:BadCollection = $false
$script:RepeatedCursor = $false
$script:ScheduleStatus = 'Paused'
$script:ScheduleMode = 'review'
$script:MissingTask = $false
$script:EnabledPilot = $false
$script:FlatFilter = $false
function Invoke-RestMethod {
    param($Uri, $Method = 'Get', $Headers, $Body, $ContentType, $MaximumRedirection, $TimeoutSec)
    if ($Uri -like 'https://fixture.invalid/api/v1/extendedAgent/systemTool/execute') {
        $request = $Body | ConvertFrom-Json
        if ($request.toolName -eq 'GetServiceNowIncident') {
            if ($request.parameters.incidentSystemId -cne '0000000000000000000000000000010a') { throw 'Case read escaped its fixed scope.' }
            $case = [ordered]@{
                sys_id = '0000000000000000000000000000010a'; number = 'INC0900001'
                assignment_group = $script:CaseGroup; cmdb_ci = '0000000000000000000000000000014a'
                state = '1'; priority = '4'
                short_description = '[ASRE Demo] Synthetic readiness test (linux); no real Azure alert'
            }
            foreach ($field in $script:CaseOverrides.Keys) { $case[$field] = $script:CaseOverrides[$field] }
            if ($script:OmitCaseCi) { $case.Remove('cmdb_ci') }
            return [pscustomobject]@{ success = $script:ToolSuccess; data = [pscustomobject]$case }
        }
        if ($request.toolName -ne 'UploadKnowledgeDocument' -or $request.pluginName -ne 'AgentMemoryPluginDefinition') {
            throw 'The fixture admitted an unexpected deployment tool.'
        }
        if ($request.parameters.triggerIndexing -isnot [bool] -or -not $request.parameters.triggerIndexing) {
            throw 'Native knowledge indexing requires a JSON Boolean, not a string.'
        }
        $script:Uploads += $request.parameters
        return [pscustomobject]@{ success = $script:ToolSuccess; data = 'Fixture upload, not live acceptance.' }
    }
    if ($Uri -like 'https://fixture.invalid/api/v2/incidents/indexing/servicenow/configuration') {
        # Fixture-only decoy: a configured CI must never supply the actual native case field.
        return [pscustomobject]@{
            assignmentGroup = $script:IncomingGroup; cmdb_ci = '0000000000000000000000000000014a'
        }
    }
    if ($Uri -eq 'https://fixture.invalid/api/v2/incidentManagement/incidentFilters') {
        if ($script:FlatFilter) {
            return [pscustomobject]@{ value = @([pscustomobject]@{ name = 'asre-servicenow-review-pilot'; isEnabled = $false }) }
        }
        return [pscustomobject]@{ value = @([pscustomobject]@{
            name = 'asre-servicenow-review-pilot'; type = 'IncidentFilter'; tags = @()
            properties = [pscustomobject]@{
                isEnabled = $script:EnabledPilot; agentMode = 'Review'; handlingAgent = 'asre-incident-review'
            }
        }) }
    }
    if ($Uri -eq 'https://fixture.invalid/api/v1/scheduledtasks') {
        $tasks = @()
        if (-not $script:MissingTask) {
            $tasks += [pscustomobject]@{
                name = 'asre-proactive-review'; status = $script:ScheduleStatus
                agent = 'asre-proactive-review'; cronExpression = '0 */6 * * *'
                agentMode = $script:ScheduleMode
            }
        }
        return ,$tasks
    }
    if ($Uri -like 'https://fixture.invalid/api/v1/agentmemory/files*') {
        $script:Listings++
        if ($script:BadCollection) { return [pscustomobject]@{ files = 'invalid'; continuationToken = '' } }
        $entries = @(Get-ReviewKnowledge -Root $repo | ForEach-Object {
            [pscustomobject]@{ name = $_.name; isIndexed = -not ($script:PendingOnce -and $script:Listings -eq 1) }
        })
        return [pscustomobject]@{
            files = $entries
            continuationToken = $(if ($script:RepeatedCursor) { 'fixture-repeated-cursor' } else { '' })
        }
    }
    throw 'Unexpected native fixture endpoint.'
}
function Start-Sleep { param($Seconds) }
function Assert-Fixture {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}
$context = @{ Endpoint = 'https://fixture.invalid'; Headers = @{} }
$schedule = @{ agent = 'asre-proactive-review'; cronExpression = '0 */6 * * *' }
Assert-NativeAutomationDisabled @context -Schedule $schedule
$script:ScheduleStatus = 'Active'
$rejected = $false
try { Assert-NativeAutomationDisabled @context -Schedule $schedule }
catch { $rejected = $_.Exception.Message -match 'native scheduler' }
Assert-Fixture $rejected 'An active native task was reported as disabled.'
$script:ScheduleStatus = 'Paused'
$script:ScheduleMode = 'autonomous'
$rejected = $false
try { Assert-NativeAutomationDisabled @context -Schedule $schedule }
catch { $rejected = $_.Exception.Message -match 'native scheduler' }
Assert-Fixture $rejected 'An unexpected scheduled execution mode was accepted.'
$script:ScheduleMode = 'review'
$script:MissingTask = $true
$rejected = $false
try { Assert-NativeAutomationDisabled @context -Schedule $schedule }
catch { $rejected = $_.Exception.Message -match 'native scheduler' }
Assert-Fixture $rejected 'Missing native task publication was accepted.'
$script:MissingTask = $false
$script:EnabledPilot = $true
$rejected = $false
try { Assert-NativeAutomationDisabled @context -Schedule $schedule }
catch { $rejected = $_.Exception.Message -match 'native incident engine' }
Assert-Fixture $rejected 'An active native response plan was reported as disabled.'
$script:EnabledPilot = $false
$script:FlatFilter = $true
$rejected = $false
try { Assert-NativeAutomationDisabled @context -Schedule $schedule }
catch { $rejected = $_.Exception.Message -match 'resource envelopes' }
Assert-Fixture $rejected 'Frontend-only flattened filter metadata was accepted as native operational proof.'
$script:FlatFilter = $false
Assert-Fixture (Test-OwnedReviewPilot ([pscustomobject]@{ id = 'asre-servicenow-review-pilot' })) 'An ID-only native plan was rejected.'
Assert-Fixture (Test-OwnedReviewPilot ([pscustomobject]@{ name = 'asre-servicenow-review-pilot' })) 'A name-only native plan was rejected.'
Assert-Fixture (-not (Test-OwnedReviewPilot ([pscustomobject]@{ id = 'unrelated' }))) 'An unrelated native plan was treated as owned.'
Assert-NativePilotScope @context
$nativeContextFixtures = @(
    @{ Name = 'missing native CI despite configured CI'; Fields = @{}; OmitCi = $true; Error = "property 'cmdb_ci' cannot be found" }
    @{ Name = 'null native CI'; Fields = @{ cmdb_ci = $null }; Error = 'does not match' }
    @{ Name = 'empty native CI'; Fields = @{ cmdb_ci = '' }; Error = 'does not match' }
    @{ Name = 'whitespace native CI'; Fields = @{ cmdb_ci = '   ' }; Error = 'does not match' }
    @{ Name = 'display-label native CI'; Fields = @{ cmdb_ci = 'Synthetic Linux CI display label' }; Error = 'does not match' }
    @{ Name = 'wrong native CI'; Fields = @{ cmdb_ci = '00000000000000000000000000000000' }; Error = 'does not match' }
    @{ Name = 'wrong native case ID'; Fields = @{ sys_id = '00000000000000000000000000000000' }; Error = 'does not match' }
    @{ Name = 'wrong native case number'; Fields = @{ number = 'INC9999999' }; Error = 'does not match' }
    @{ Name = 'closed native case state'; Fields = @{ state = '6' }; Error = 'does not match' }
    @{ Name = 'wrong native case priority'; Fields = @{ priority = '1' }; Error = 'does not match' }
    @{ Name = 'wrong native case title'; Fields = @{ short_description = 'Unrelated incident' }; Error = 'does not match' }
    @{ Name = 'failed native case tool'; Fields = @{}; ToolSuccess = $false; Error = 'reported failure' }
)
foreach ($fixture in $nativeContextFixtures) {
    try {
        $script:CaseOverrides = $fixture.Fields
        $script:OmitCaseCi = $fixture['OmitCi'] -eq $true
        $script:ToolSuccess = $fixture['ToolSuccess'] -ne $false
        $rejected = $false
        try { Assert-NativePilotScope @context } catch { $rejected = $_.Exception.Message -match $fixture.Error }
        Assert-Fixture $rejected "Native scope admitted $($fixture.Name), or failed for an unexpected reason."
    }
    finally {
        $script:CaseOverrides = @{}
        $script:OmitCaseCi = $false
        $script:ToolSuccess = $true
    }
    Assert-NativePilotScope @context
}
$script:CaseGroup = 'unrelated'
$rejected = $false
try { Assert-NativePilotScope @context } catch { $rejected = $_.Exception.Message -match 'does not match' }
Assert-Fixture $rejected 'A real case identity/group mismatch was accepted.'
$script:CaseGroup = '0000000000000000000000000000007a'
$script:IncomingGroup = 'unrelated'
$rejected = $false
try { Assert-NativePilotScope @context } catch { $rejected = $_.Exception.Message -match 'incoming ServiceNow scope' }
Assert-Fixture $rejected 'An unscoped incoming platform was accepted.'
$script:IncomingGroup = 'ASRE Demo Operations'
$rejected = $false
try { $null = Invoke-NativeSystemTool @context -Name PostServiceNowDiscussionEntry -Plugin ServiceNowPluginDefinition -Parameters @{} }
catch { $rejected = $_.Exception.Message -match 'not append or mutate cases' }
Assert-Fixture $rejected 'Deployment bypassed Review by appending through the playground API.'
$script:PendingOnce = $true
$report = @(Publish-ReviewKnowledge @context -Root $repo)
$expectedKnowledge = @((Get-Content -LiteralPath (Join-Path $repo 'agent\knowledge\index.json') -Raw | ConvertFrom-Json).files)
Assert-Fixture ($report.Count -eq $expectedKnowledge.Count -and $script:Uploads.Count -eq $expectedKnowledge.Count -and $script:Listings -eq 2) 'Knowledge did not require independent indexed readback.'
Assert-Fixture (@(Compare-Object $expectedKnowledge @($report.filename) -CaseSensitive).Count -eq 0) 'Indexed readback omitted or substituted a selected knowledge document.'
Assert-Fixture (@($script:Uploads | Where-Object { $_.triggerIndexing -isnot [bool] -or -not $_.triggerIndexing }).Count -eq 0) 'Knowledge publication omitted typed explicit indexing.'
$script:ToolSuccess = $false
$rejected = $false
try { $null = Publish-ReviewKnowledge @context -Root $repo } catch { $rejected = $_.Exception.Message -match 'reported failure' }
Assert-Fixture $rejected 'A failed native upload was reported as published.'
$script:ToolSuccess = $true
$script:BadCollection = $true
$rejected = $false
try { $null = Publish-ReviewKnowledge @context -Root $repo } catch { $rejected = $_.Exception.Message -match 'files collection' }
Assert-Fixture $rejected 'An invalid native collection was treated as an empty successful index.'
$script:BadCollection = $false
$script:RepeatedCursor = $true
$rejected = $false
try { $null = Publish-ReviewKnowledge @context -Root $repo } catch { $rejected = $_.Exception.Message -match 'continuation token' }
Assert-Fixture $rejected 'A repeated knowledge cursor could loop without a terminal failure.'
Write-Output "$($nativeContextFixtures.Count) strict native-context negative fixtures passed; the valid baseline was restored after each."
Write-Output 'Native scope, deployment tool restrictions and indexed-knowledge fixtures passed; no Azure or ServiceNow operation was executed.'

# Importing common publication definitions must not execute any legacy setup,
# token acquisition, pilot reads or case writes. The existing fixtures above
# continue to exercise all historical native guards unchanged.
$beforeUploads = $script:Uploads.Count
. (Join-Path $repo 'scripts/ReviewAgent.Common.ps1')
$null = @(Get-ReviewConfiguration -Root $repo -Profile Common)
if ($script:Uploads.Count -ne $beforeUploads) { throw 'Common definition import performed an external operation.' }
$legacy = @(Get-NativeAutomationConfiguration -Root $repo)
if ($legacy[0].properties.isEnabled -ne $false -or $legacy[0].properties.incidentPlatform -cne 'ServiceNow' -or
    $legacy[1].properties.status -cne 'Paused') { throw 'Monitor implementation altered historical native defaults.' }
Write-Output 'Common definitions require no ServiceNow pilot access; historical disabled native defaults remain intact.'
