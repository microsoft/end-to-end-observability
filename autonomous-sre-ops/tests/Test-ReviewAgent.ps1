$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repo = Split-Path $PSScriptRoot -Parent
& (Join-Path $repo 'tests\Test-OperationsConnector.ps1')
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    (Join-Path $repo 'scripts/Initialize-ReviewAgent.ps1'), [ref]$tokens, [ref]$errors
)
if ($errors.Count) { throw 'The agent delivery script did not parse.' }
foreach ($name in @('Get-ReviewConfiguration', 'Assert-ReviewValue', 'Get-NativeAutomationConfiguration', 'Get-ReviewKnowledge')) {
    $definition = @($ast.FindAll({
        param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name
    }, $true))
    if ($definition.Count -ne 1) { throw "The production function is not unique: $name" }
    . ([scriptblock]::Create($definition[0].Extent.Text))
}
$items = @(Get-ReviewConfiguration -Root $repo)
if ($items.Count -ne 9) { throw 'Expected one tool, four trusted skills, one prompt and three explicitly restricted agents.' }
foreach ($item in $items) {
    $actual = $item.properties | ConvertTo-Json -Depth 20 | ConvertFrom-Json
    Assert-ReviewValue -Expected $item.properties -Actual $actual -Path $item.name
    if ($item.kind -notin @('tools', 'skills', 'commonprompts', 'agents')) { throw 'Unexpected deployment surface.' }
    $type = @{ tools = 'ExtendedAgentTool'; skills = 'Skill'; commonprompts = 'CommonPrompt'; agents = 'ExtendedAgent' }[$item.kind]
    if ($item.type -cne $type -or $item.properties.ContainsKey('name')) {
        throw 'Use the supported resource discriminator and the outer resource name, not frontend-only fields.'
    }
}
$agent = $items | Where-Object name -eq asre-review-investigator
if (($agent.properties.tools -join ',') -ne 'ReadFile' -or
    ($agent.properties.mcpTools -join ',') -ne 'asre-review-evidence' -or
    $agent.properties.commonTools.Count -ne 0 -or $agent.properties.handoffs.Count -ne 0 -or
    $agent.properties.ContainsKey('enableMemory') -or -not $agent.properties.enableSkills -or
    $agent.properties.allowedSkills.Count -ne 4 -or
    $agent.properties.instructions -notmatch 'No ServiceNow case tool is currently bound') {
    throw 'The Review investigator inherited unrestricted tools, delegation or fabricated case connectivity.'
}
foreach ($name in @('asre-incident-review', 'asre-proactive-review')) {
    $native = $items | Where-Object name -eq $name
    $expected = if ($name -eq 'asre-incident-review') {
        'ReadFile,SearchMemory,GetServiceNowIncident,PostServiceNowDiscussionEntry'
    } else { 'ReadFile,SearchMemory' }
    if (($native.properties.tools -join ',') -cne $expected -or
        ($native.properties.mcpTools -join ',') -cne 'asre-review-evidence' -or
        $native.properties.handoffs.Count -ne 0 -or $native.properties.commonTools.Count -ne 0 -or
        $native.properties.ContainsKey('enableMemory') -or $native.properties.allowedSkills.Count -ne 4 -or
        $native.properties.hooks.PreToolUse.Count -ne 1 -or
        $native.properties.hooks.PreToolUse[0].failMode -cne 'block' -or
        $native.properties.hooks.PreToolUse[0].script -cne (Get-Content -LiteralPath (Join-Path $repo 'agent\hooks\review-case-policy.py') -Raw)) {
        throw "The $name configuration widened tools, delegation, knowledge writing or the fail-closed pre-tool policy."
    }
}
$automations = @(Get-NativeAutomationConfiguration -Root $repo)
if ($automations.Count -ne 2 -or $automations[0].properties.isEnabled -ne $false -or
    $automations[0].properties.agentMode -cne 'Review' -or
    $automations[0].properties.handlingAgent -cne 'asre-incident-review' -or
    $automations[0].properties.titleContains -cne '[ASRE Demo] Synthetic readiness test (linux); no real Azure alert' -or
    $automations[1].properties.status -cne 'Paused' -or
    $automations[1].properties.ContainsKey('isEnabled') -or
    $automations[1].properties.agentMode -cne 'review' -or
    $automations[1].properties.ContainsKey('name') -or
    $automations[1].properties.agent -cne 'asre-proactive-review') {
    throw 'Native automation defaults or exact pilot routing differ from the safe source contract.'
}
$enabled = @(Get-NativeAutomationConfiguration -Root $repo -EnablePilot)
if ($enabled[0].properties.isEnabled -ne $true -or $enabled[1].properties.status -cne 'Paused') {
    throw 'Explicit pilot activation changed the proactive schedule or did not select the Review pilot.'
}
$knowledge = @(Get-ReviewKnowledge -Root $repo)
if ($knowledge.Count -ne 5 -or @($knowledge | Where-Object {
    $_.source_sha256 -cnotmatch '^[0-9a-f]{64}$' -or $_.source_bytes -le 0 -or $_.name -notlike '*.md'
}).Count) { throw 'Knowledge publication is not the five explicitly selected, hashed source documents.' }
$reportSource = @($knowledge | Where-Object name -ceq 'asre-live-report-sources-v1.md')
if ($reportSource.Count -ne 1) { throw 'The trusted Live Report query guide is missing.' }
foreach ($table in @('AlertsManagementResources', 'PatchInstallationResources', 'RecoveryServicesResources')) {
    if (-not $reportSource[0].content.Contains('arg("").' + $table)) {
        throw "The Live Report guide omitted its actual ARG provider source: $table."
    }
}
$releaseSource = Get-Content -LiteralPath (Join-Path $repo 'scripts\Invoke-IncidentRelease.ps1') -Raw
foreach ($document in @('index.json') + @($knowledge.name)) {
    if (-not $releaseSource.Contains("'agent/knowledge/$document'")) {
        throw "A trusted knowledge dependency is absent from the frozen artifact: $document."
    }
}
$tool = $items | Where-Object kind -eq tools
if ($tool.properties.functionCode -cne (Get-Content -LiteralPath (Join-Path $repo 'agent/tools/review_evidence.py') -Raw) -or
    $tool.properties.ContainsKey('authEnabled') -or $tool.properties.ContainsKey('authScopes') -or
    $tool.properties.functionCode -notmatch 'DefaultAzureCredential\(\)\.get_token\("https://storage\.azure\.com/\.default"\)' -or
    $tool.properties.parameters.Count -ne 4 -or $tool.properties.parameters[3].type -ne 'int') {
    throw 'The deployed tool does not match its actual source/authentication/paging contract.'
}
$bad = $agent.properties | ConvertTo-Json -Depth 20 | ConvertFrom-Json
$bad.tools = @()
$rejected = $false
try { Assert-ReviewValue -Expected $agent.properties -Actual $bad -Path agent }
catch { $rejected = $_.Exception.Message -match 'Agent readback changed' }
if (-not $rejected) { throw 'An empty tool list that inherits workspace tools was accepted.' }
$bad = $agent.properties | ConvertTo-Json -Depth 20 | ConvertFrom-Json
$bad.mcpTools = @('unrelated-tool')
$rejected = $false
try { Assert-ReviewValue -Expected $agent.properties -Actual $bad -Path agent }
catch { $rejected = $_.Exception.Message -match 'Agent readback changed' }
if (-not $rejected) { throw 'An unexpected custom tool binding was accepted.' }
Write-Output 'Review agent envelope and strict readback fixtures passed; no Azure operation was executed.'

. (Join-Path $repo 'scripts/ReviewAgent.Common.ps1')
$common = @(Get-ReviewConfiguration -Root $repo -Profile Common)
if ($common.Count -ne 7 -or @($common | Where-Object name -in @('asre-incident-review', 'asre-proactive-review')).Count) {
    throw 'Common publication inherited native ServiceNow agents or prerequisites.'
}
$monitor = @(Get-MonitorReviewConfiguration -Root $repo)
$declaration = Get-MonitorCaseDeclaration -Root $repo
$phase = (Get-MonitorManifest -Root $repo).phase
if ($phase -ceq 'SchemaDiscovery') {
    if ($monitor.Count -ne 1 -or $monitor[0].kind -cne 'agents') { throw 'Discovery published case/evidence tools or extra resources.' }
    $p = $monitor[0].properties
    if ($p.tools.Count -ne 0 -or $p.mcpTools.Count -ne 0 -or $p.commonTools.Count -ne 0 -or
        $p.handoffs.Count -ne 0 -or $p.connectors.Count -ne 0 -or $p.agentsAsTools.Count -ne 0 -or
        $p.enableSkills -ne $false -or $p.allowedSkills.Count -ne 0 -or $p.addSystemSkills -ne $false -or
        $p.disableDocumentRetrieval -ne $true -or $p.hooks.PreToolUse[0].failMode -cne 'block' -or
        $p.instructions -notmatch 'ASRE_NATIVE_WORKFLOW_PHASE=SchemaDiscovery') {
        throw 'Discovery relies only on inherited empty tools or exposes execution/skills/delegation.'
    }
    foreach ($toolName in @('ReadFile','RunShell','asre-servicenow-case','inherited.AnyTool')) {
        # Ignore expected static stderr here; native ABI and stderr equality
        # are independently asserted by the Python policy CLI fixtures.
        $reply = (@{ hook_event_name = 'PreToolUse'; tool_name = $toolName; tool_input = @{}; call_id = 'fixture-call' } | ConvertTo-Json -Compress) |
            & python -c $p.hooks.PreToolUse[0].script 2>$null
        if ($LASTEXITCODE -ne 2) { throw 'Discovery deny-all hook did not return native blocking exit2.' }
        $denial = $reply | ConvertFrom-Json
        if ($denial.decision -cne 'block' -or $denial.reason -cne 'ASRE SchemaDiscovery denies ALL tools.') {
            throw 'An inherited/native/custom tool escaped Discovery deny-all.'
        }
    }
    foreach ($malformed in @('', 'not-json', '[]', '{"tool_name":"ReadFile","tool_name":"RunShell"}', ('x' * 131073))) {
        $reply = $malformed | & python -c $p.hooks.PreToolUse[0].script 2>$null
        if ($LASTEXITCODE -ne 2 -or ($reply | ConvertFrom-Json).decision -cne 'block') {
            throw 'Discovery parsed malformed input before unconditional native exit2 denial.'
        }
    }
    Assert-MonitorResource $monitor[0] ($monitor[0] | ConvertTo-Json -Depth 40 | ConvertFrom-Json)
    Write-Output 'SchemaDiscovery: one strict agent readback, no case binding, all inherited tools denied; Production requires a second protected publication.'
    return
}
$caseTool = @($monitor | Where-Object name -CEQ $declaration.name)
if ($caseTool.Count -ne 1 -or $caseTool[0].properties.functionCode -cne (& (Join-Path $repo $declaration.buildScript) -PassThru)) {
    throw 'Monitor publication did not consume the deterministic B package.'
}
foreach ($parameter in $declaration.properties.parameters) { $parameter.value = $null }
Assert-MonitorExact $declaration.properties.parameters $caseTool[0].properties.parameters 'B-parameters-and-native-null-values'
Assert-MonitorExact $declaration.dependencies $caseTool[0].properties.dependencies 'B-supported-dependencies'
$investigator = @($monitor | Where-Object kind -EQ agents)
if ($investigator.Count -ne 1 -or $investigator[0].name -cne 'asre-azure-monitor-review' -or
    ($investigator[0].properties.tools -join ',') -cne 'QueryLogAnalyticsByResourceId,ReadFile,SearchMemory' -or
    ($investigator[0].properties.mcpTools -join ',') -cne "asre-review-evidence,$($declaration.name)" -or
    $investigator[0].properties.commonTools.Count -ne 0 -or $investigator[0].properties.handoffs.Count -ne 0 -or
    $investigator[0].properties.enableSkills -ne $true -or
    ($investigator[0].properties.allowedSkills -join ',') -cne 'asre-linux-rca,asre-patch-backup-recovery,asre-case-and-verification,asre-proactive-analysis' -or
    $investigator[0].properties.instructions -notmatch 'Embedded shared evidence contract' -or
    $investigator[0].properties.hooks.PostToolUse[0].failMode -cne 'allow' -or
    $investigator[0].properties.hooks.PostToolUse[0].script -match '__ASRE_MONITOR_CONFIG__' -or
    $investigator[0].properties.hooks.ContainsKey('PreToolUse')) {
    throw 'Monitor selected tools/skills/delegation or supported result audit changed.'
}
if ($monitor.Count -ne 7 -or @($monitor | Where-Object {
    ($_.kind -eq 'skills' -and $_.name -cnotin @('asre-linux-rca', 'asre-patch-backup-recovery', 'asre-case-and-verification', 'asre-proactive-analysis')) -or
    $_.name -in @('asre-incident-review', 'asre-proactive-review', 'asre-review-investigator')
}).Count) {
    throw 'Monitor configuration leaked historical investigators or extra selected skills.'
}
foreach ($item in $monitor) {
    $actual = $item | ConvertTo-Json -Depth 40 | ConvertFrom-Json
    Assert-MonitorResource $item $actual
}
Write-Output 'Monitor package, four selected native skills, supported result audit and seven strict resource readbacks passed offline.'
