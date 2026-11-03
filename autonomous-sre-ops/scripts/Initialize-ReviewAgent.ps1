#Requires -Version 7.5
<#
.SYNOPSIS
Publishes the bounded Review configuration (evidence tool, skills, common prompt and agents) to the demo agent.

.DESCRIPTION
Publishes only from a directory made by Render-AgentAssets.ps1 for the current deployment context (-AssetRoot).
Before the first request the rendered tree is re-rendered in memory and must match byte for byte, so the unrendered
repository tree (which carries synthetic values) is refused. Every deployment identity comes from the deployment
context (Get-AsreContext); every ARM and data-plane call goes through Invoke-AsreAgentPlaneRequest. Failures name
the step, HTTP status and error code only; no token, header or response body is written to any stream.

Profiles:
  Common  Publishes seven resources: the evidence tool, four skills, the operating-boundaries prompt and one
          Review investigator. It never reads an inert asset and needs no ServiceNow section in the context.
  Native  Adds the two ServiceNow-backed Review agents, the disabled response plan and the paused proactive task.
          Needs the servicenow section of the context, -PilotCaseSysId and -PilotCaseNumber.

-WhatIf runs every read-only check (context, rendered assets, runtime proof, agent mode, pilot scope, connector
plan) and stops before the first write; nothing is published and no result file is written.

.PARAMETER OutputsFile
Terraform outputs JSON. agent_resource_id.value must equal the context agent; an empty value means the agent is
deliberately disabled and nothing is attempted.

.PARAMETER RuntimeProofFile
Runtime acceptance proof: verified, mode Review, three targets, faults disabled, checked within 15 minutes.

.PARAMETER AssetRoot
A directory made by Render-AgentAssets.ps1 for the same deployment context.

.PARAMETER EnableIncidentPilot
Native only. Enables the Review-mode response plan after every check passes.

.PARAMETER Profile
Common (default) or Native.

.PARAMETER PilotCaseSysId
Native only. The 32-character sys_id of the synthetic pilot case.

.PARAMETER PilotCaseNumber
Native only. The pilot case number, INC followed by seven digits.

.PARAMETER DefinitionsOnly
Defines every function and returns (used by the offline test).
#>
[CmdletBinding(DefaultParameterSetName = 'Publish', SupportsShouldProcess)]
param(
    [Parameter(Mandatory, ParameterSetName = 'Publish')][string]$OutputsFile,
    [Parameter(Mandatory, ParameterSetName = 'Publish')][string]$RuntimeProofFile,
    [Parameter(Mandatory, ParameterSetName = 'Publish')][string]$AssetRoot,
    [Parameter(ParameterSetName = 'Publish')][switch]$EnableIncidentPilot,
    [Parameter(ParameterSetName = 'Publish')][ValidateSet('Native', 'Common', IgnoreCase = $false)][string]$Profile = 'Common',
    [Parameter(ParameterSetName = 'Publish')][ValidatePattern('(?-i)\A[0-9a-f]{32}\z')][string]$PilotCaseSysId,
    [Parameter(ParameterSetName = 'Publish')][ValidatePattern('(?-i)\AINC[0-9]{7}\z')][string]$PilotCaseNumber,
    [Parameter(Mandatory, ParameterSetName = 'Definitions')][switch]$DefinitionsOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Get-ReviewConfiguration {
    param([string]$Root, [ValidateSet('Native', 'Common')][string]$Profile = 'Native')
    $tool = 'asre-review-evidence'
    @{
        kind = 'tools'; name = $tool; type = 'ExtendedAgentTool'
        properties = @{
            type = 'PythonFunctionTool'
            description = 'Four fixed Review evidence commands and immutable result lookup. Result page zero waits up to 90 seconds for completion; large results require every numbered receipt page. Never infer recovery from acceptance.'
            functionCode = Get-Content -LiteralPath (Join-Path $Root 'agent/tools/review_evidence.py') -Raw
            timeoutSeconds = 120
            parameters = @(
                @{ name = 'action'; type = 'string'; required = $true; description = 'inspect, telemetry, patch_jobs, backup_jobs, or result.' },
                @{ name = 'resource_id'; type = 'string'; required = $true; description = 'One of the three exact demo VM resource IDs.' },
                @{ name = 'request_id'; type = 'string'; required = $false; description = 'Canonical UUID from acceptance; result lookup only.' },
                @{ name = 'page'; type = 'int'; required = $false; description = 'Zero-based result page. Concatenate every receipt_fragment before interpreting large evidence.' }
            )
        }
    }
    $skills = @()
    foreach ($folder in @('linux-rca', 'proactive-analysis', 'patch-backup-recovery', 'case-and-verification')) {
        $content = Get-Content -LiteralPath (Join-Path $Root "agent/skills/$folder/SKILL.md") -Raw
        $metadata = [regex]::Match($content, '\A---\r?\nname: (?<name>[a-z0-9-]+)\r?\ndescription: (?<description>[^\r\n]+)\r?\n---\r?\n')
        if (-not $metadata.Success) { throw "Unexpected trusted skill metadata: $folder" }
        $name = $metadata.Groups['name'].Value
        $skills += $name
        if ($folder -eq 'case-and-verification') {
            $schema = Get-Content -LiteralPath (Join-Path $Root 'contracts/servicenow-case.schema.json') -Raw
            $content += "`n## Shared evidence contract (not a ServiceNow API payload)`n~~~json`n$schema`n~~~`n"
        }
        @{
            kind = 'skills'; name = $name; type = 'Skill'
            properties = @{
                description = $metadata.Groups['description'].Value
                tools = @($tool); skillContent = $content; additionalFiles = @()
            }
        }
    }
    $boundaries = Get-Content -LiteralPath (Join-Path $Root 'agent/knowledge/operating-boundaries.md') -Raw
    @{
        kind = 'commonprompts'; name = 'asre-operating-boundaries'; type = 'CommonPrompt'
        properties = @{ prompt = $boundaries }
    }
    @{
        kind = 'agents'; name = 'asre-review-investigator'; type = 'ExtendedAgent'
        properties = @{
            instructions = @"
You are the isolated ASRE Review-only investigator. Use asre-review-evidence for
evidence and ReadFile only to read the four selected trusted skills. Do not read
other files. Authentication is implemented inside the fixed evidence function.
Submit a fixed evidence command, retain its request UUID, and retrieve its result.
Result page zero waits up to 90 seconds for the scheduled worker. If the bound
expires, retain the UUID and report pending; never resubmit to force completion.
If representation is receipt_fragment, retrieve every numbered page, concatenate
receipt_fragment values in page order and parse the complete JSON. A fragment is
not complete evidence. A completed evidence request is not a recovered incident.
No ServiceNow case tool is currently bound: report this blocker, never fabricate
a ticket, case audit or resolution. Never repair, execute shell, schedule work,
enable faults or delegate to another agent. Do not change workspace settings.

$boundaries
"@
            handoffDescription = 'Read-only evidence-backed investigation of the three isolated ASRE demo VMs.'
            tools = @('ReadFile'); mcpTools = @($tool); commonTools = @(); handoffs = @()
            enableSkills = $true; allowedSkills = $skills
        }
    }
    if ($Profile -eq 'Common') { return }
    foreach ($scenario in @('incident', 'proactive')) {
        $tools = @('ReadFile', 'SearchMemory')
        if ($scenario -eq 'incident') {
            $tools += @('GetServiceNowIncident', 'PostServiceNowDiscussionEntry')
        }
        $instructions = Get-Content -LiteralPath (Join-Path $Root "agent/instructions/$scenario-review.md") -Raw
        @{
            kind = 'agents'; name = "asre-$scenario-review"; type = 'ExtendedAgent'
            properties = @{
                instructions = "$instructions`n`n$boundaries"
                handoffDescription = "Bounded ASRE $scenario Review investigation using curated knowledge and immutable evidence."
                tools = $tools; mcpTools = @($tool); commonTools = @(); handoffs = @()
                enableSkills = $true; allowedSkills = $skills
                hooks = @{
                    PreToolUse = @(@{
                        type = 'command'; matcher = '(?s:.*)'; timeout = 15; failMode = 'block'
                        script = Get-Content -LiteralPath (Join-Path $Root 'agent/hooks/review-case-policy.py') -Raw
                    })
                }
            }
        }
    }
}

function Get-NativeAutomationConfiguration {
    param([string]$Root, [switch]$EnablePilot)
    $manifest = Get-Content -LiteralPath (Join-Path $Root 'agent/workflows/native-review.json') -Raw | ConvertFrom-Json -AsHashtable
    $plan = $manifest.response_plan
    $schedule = $manifest.proactive_schedule
    if ($manifest.platform -cne 'ServiceNow' -or $plan.properties.isEnabled -ne $false -or
        $plan.properties.agentMode -cne 'Review' -or $schedule.properties.status -cne 'Paused' -or
        $schedule.properties.agent -cne 'asre-proactive-review' -or
        $schedule.properties.ContainsKey('isEnabled') -or $schedule.properties.ContainsKey('name') -or
        $schedule.properties.agentMode -cne 'review') {
        throw 'Native automation source must be explicit Review and disabled by default.'
    }
    $plan.properties.isEnabled = [bool]$EnablePilot
    $plan
    $schedule
}

function Get-ReviewKnowledge {
    param([string]$Root)
    $folder = Join-Path $Root 'agent/knowledge'
    $manifest = Get-Content -LiteralPath (Join-Path $folder 'index.json') -Raw | ConvertFrom-Json
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($name in $manifest.files) {
        if ($name -cnotmatch '^[a-z0-9][a-z0-9-]*\.md$' -or -not $seen.Add($name)) {
            throw 'Knowledge publication requires unique, local, explicitly selected Markdown filenames.'
        }
        $content = (Get-Content -LiteralPath (Join-Path $folder $name) -Raw).Replace("`r`n", "`n")
        $bytes = [Text.Encoding]::UTF8.GetBytes($content)
        if ($bytes.Length -eq 0 -or $bytes.Length -gt 16MB) { throw "Invalid approved knowledge size: $name" }
        $hash = [Security.Cryptography.SHA256]::Create()
        try { $sha = [BitConverter]::ToString($hash.ComputeHash($bytes)).Replace('-', '').ToLowerInvariant() }
        finally { $hash.Dispose() }
        @{ name = $name; content = $content; source_sha256 = $sha; source_bytes = $bytes.Length }
    }
}

function Assert-ReviewValue {
    # Strict recursive readback: every expected field must be present with the same type and value. The actual
    # side may be a parsed JSON object (PSCustomObject) or a dictionary (the agent plane returns dictionaries).
    param($Expected, $Actual, [string]$Path)
    if ($Expected -is [Collections.IDictionary]) {
        foreach ($key in $Expected.Keys) {
            if ($Actual -is [Collections.IDictionary]) {
                if (-not $Actual.Contains($key)) { throw "Agent readback omitted $Path.$key" }
                $value = $Actual[$key]
            }
            else {
                $property = if ($null -ne $Actual) { $Actual.PSObject.Properties[$key] } else { $null }
                if (-not $property) { throw "Agent readback omitted $Path.$key" }
                $value = $property.Value
            }
            Assert-ReviewValue -Expected $Expected[$key] -Actual $value -Path "$Path.$key"
        }
    }
    elseif ($Expected -is [array]) {
        if ($Actual -isnot [array] -or $Expected.Count -ne $Actual.Count) { throw "Agent readback changed $Path" }
        for ($index = 0; $index -lt $Expected.Count; $index++) {
            Assert-ReviewValue -Expected $Expected[$index] -Actual $Actual[$index] -Path "$Path[$index]"
        }
    }
    elseif ($Expected -cne $Actual) { throw "Agent readback changed $Path" }
}

function Test-OwnedReviewPilot {
    param($Filter)
    foreach ($key in @('id', 'name')) {
        $value = Get-AsreMember -Value $Filter -Path @($key)
        if ($value -is [string] -and $value -ceq 'asre-servicenow-review-pilot') { return $true }
    }
    return $false
}

function Get-ReviewSourceState {
    # source_commit / source_dirty for the result file. Unknown (not a repository, no git) is reported as dirty.
    param([Parameter(Mandatory)][string]$Root)
    $commit = 'unknown'
    $dirty = $true
    $git = Get-Command -Name git -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $git) {
        $head = @(& $git.Source --no-optional-locks -C $Root rev-parse HEAD 2>$null)
        if ($LASTEXITCODE -eq 0 -and $head.Count -eq 1 -and ([string]$head[0]) -cmatch '\A[0-9a-f]{40}\z') {
            $commit = [string]$head[0]
            $status = @(& $git.Source --no-optional-locks -C $Root status --porcelain --untracked-files=normal 2>$null)
            if ($LASTEXITCODE -eq 0) { $dirty = ($status.Count -gt 0) }
        }
    }
    return @{ commit = $commit; dirty = $dirty }
}

function Assert-ReviewRuntimeProof {
    # The runtime acceptance proof must be verified, Review mode, three targets, faults disabled and fresh.
    param([Parameter(Mandatory)][string]$Path)
    $message = 'Agent binding requires fresh real three-target private SSH acceptance with faults disabled.'
    $proof = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json -AsHashtable -DateKind String
    $checked = Get-AsreMember -Value $proof -Path @('checked_at')
    $moment = [DateTimeOffset]::MinValue
    if ($checked -isnot [string] -or -not [DateTimeOffset]::TryParse($checked, [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::AssumeUniversal, [ref]$moment)) { throw $message }
    $age = [DateTimeOffset]::UtcNow - $moment
    $targets = Get-AsreMember -Value $proof -Path @('targets')
    $faults = Get-AsreMember -Value $proof -Path @('faults_enabled')
    if ((Get-AsreMember -Value $proof -Path @('status')) -cne 'verified' -or
        (Get-AsreMember -Value $proof -Path @('mode')) -cne 'Review' -or
        ($targets -isnot [int] -and $targets -isnot [long]) -or $targets -ne 3 -or
        $faults -isnot [bool] -or $faults -ne $false -or
        $age.TotalSeconds -lt -30 -or $age.TotalMinutes -gt 15) {
        throw $message
    }
    return $proof
}

function Invoke-ReviewRequest {
    # One data-plane request through the agent plane; returns the parsed response data.
    param(
        [Parameter(Mandatory)][hashtable]$Plane,
        [ValidateSet('Get', 'Put', 'Post', IgnoreCase = $false)][string]$Method = 'Get',
        [Parameter(Mandatory)][string]$Path,
        $Body
    )
    $arguments = @{ Plane = $Plane; Service = 'Sre'; Method = $Method; Path = $Path }
    if ($null -ne $Body) { $arguments['Body'] = $Body }
    $response = Invoke-AsreAgentPlaneRequest @arguments
    return $response['data']
}

function Get-NativeIncidentFilters {
    param([Parameter(Mandatory)][hashtable]$Plane)
    $result = Invoke-ReviewRequest -Plane $Plane -Path '/api/v2/incidentManagement/incidentFilters'
    # Read the member directly: returning it from a helper would unroll a one-element array.
    $values = $null
    if ($result -is [Collections.IDictionary] -and $result.Contains('value')) { $values = $result['value'] }
    if ($values -isnot [array]) { throw 'Native response-plan enumeration omitted its value collection.' }
    foreach ($entry in $values) {
        $entryProperties = Get-AsreMember -Value $entry -Path @('properties')
        $entryName = Get-AsreMember -Value $entry -Path @('name')
        if ($entry -isnot [Collections.IDictionary] -or -not $entry.Contains('name') -or
            -not $entry.Contains('type') -or -not $entry.Contains('properties') -or
            $entry['type'] -cne 'IncidentFilter' -or $entryName -isnot [string] -or [string]::IsNullOrWhiteSpace($entryName) -or
            $entryProperties -isnot [Collections.IDictionary] -or -not $entryProperties.Contains('isEnabled') -or
            $entryProperties['isEnabled'] -isnot [bool]) {
            throw 'Native response plans must return named IncidentFilter resource envelopes with typed properties.'
        }
        $entry
    }
}

function Assert-NativeAutomationDisabled {
    param([Parameter(Mandatory)][hashtable]$Plane, [hashtable]$Schedule)
    $filters = @(Get-NativeIncidentFilters -Plane $Plane)
    $owned = @($filters | Where-Object { Test-OwnedReviewPilot -Filter $_ })
    if ($owned.Count -ne 1 -or $owned[0]['properties']['isEnabled'] -ne $false -or
        $owned[0]['properties']['agentMode'] -cne 'Review' -or $owned[0]['properties']['handlingAgent'] -cne 'asre-incident-review') {
        throw 'The native incident engine did not independently confirm the disabled Review pilot.'
    }
    # The plane parses a root JSON array like ConvertFrom-Json: a single task arrives as one object.
    $tasks = Invoke-ReviewRequest -Plane $Plane -Path '/api/v1/scheduledtasks'
    $ownedTasks = @(@($tasks) | Where-Object { $_ -is [Collections.IDictionary] -and $_['name'] -ceq 'asre-proactive-review' })
    if ($ownedTasks.Count -ne 1 -or $ownedTasks[0]['status'] -cne 'Paused' -or
        $ownedTasks[0]['agent'] -cne $Schedule.agent -or $ownedTasks[0]['cronExpression'] -cne $Schedule.cronExpression -or
        $ownedTasks[0]['agentMode'] -cne 'review') {
        throw 'The native scheduler did not independently confirm the exact disabled proactive task.'
    }
}

function Invoke-NativeSystemTool {
    param([Parameter(Mandatory)][hashtable]$Plane, [string]$Name, [string]$Plugin, [hashtable]$Parameters)
    $allowed = @{ GetServiceNowIncident = 'ServiceNowPluginDefinition'; UploadKnowledgeDocument = 'AgentMemoryPluginDefinition' }
    if (-not $allowed.ContainsKey($Name) -or $Plugin -cne $allowed[$Name]) {
        throw 'Deployment may only read the pilot case and publish approved knowledge, not append or mutate cases.'
    }
    $effective = $Plane
    if ($Name -ceq 'GetServiceNowIncident') {
        # Reading a case is a POST on this route. A read-only plane would refuse it, so the one fixed read uses a
        # writable shallow copy; the upload below still needs a plane that is not read-only.
        $effective = @{} + $Plane
        $effective['readOnly'] = $false
    }
    $result = Invoke-ReviewRequest -Plane $effective -Method Post -Path '/api/v1/extendedAgent/systemTool/execute' `
        -Body @{ toolName = $Name; pluginName = $Plugin; parameters = $Parameters }
    if ($result -isnot [Collections.IDictionary] -or -not $result.Contains('success') -or $result['success'] -ne $true) {
        throw "The native $Name tool reported failure; no successful operation is claimed."
    }
    return Get-AsreMember -Value $result -Path @('data')
}

function Assert-NativePilotScope {
    param(
        [Parameter(Mandatory)][hashtable]$Plane,
        [Parameter(Mandatory)][string]$CaseSysId,
        [Parameter(Mandatory)][string]$CaseNumber,
        [Parameter(Mandatory)][System.Collections.IDictionary]$ServiceNow
    )
    $config = Invoke-ReviewRequest -Plane $Plane -Path '/api/v2/incidents/indexing/servicenow/configuration'
    if ((Get-AsreMember -Value $config -Path @('assignmentGroup')) -cne 'ASRE Demo Operations') {
        throw 'The native incoming ServiceNow scope differs from the approved demo assignment group.'
    }
    $case = Invoke-NativeSystemTool -Plane $Plane -Name GetServiceNowIncident `
        -Plugin ServiceNowPluginDefinition -Parameters @{ incidentSystemId = $CaseSysId }
    $linuxItem = Get-AsreMember -Value $ServiceNow -Path @('configuration_items', 'linux')
    $group = Get-AsreMember -Value $ServiceNow -Path @('assignment_group')
    if ($group -isnot [string] -or $linuxItem -isnot [string] -or $case -isnot [Collections.IDictionary] -or
        (Get-AsreMember -Value $case -Path @('sys_id')) -cne $CaseSysId -or
        (Get-AsreMember -Value $case -Path @('number')) -cne $CaseNumber -or
        (Get-AsreMember -Value $case -Path @('assignment_group')) -cne $group -or
        (Get-AsreMember -Value $case -Path @('cmdb_ci')) -cne $linuxItem -or
        (Get-AsreMember -Value $case -Path @('state')) -notin @('1', '2', '3') -or
        (Get-AsreMember -Value $case -Path @('priority')) -cne '4' -or
        (Get-AsreMember -Value $case -Path @('short_description')) -cne '[ASRE Demo] Synthetic readiness test (linux); no real Azure alert') {
        throw 'The authenticated native pilot case does not match its fixed ID, group, CI, open state and synthetic title.'
    }
}

function Publish-ReviewKnowledge {
    # Uploads the approved documents, then observes the native index for at most six minutes (the window can only
    # be shortened). A repeated continuation token, an ambiguous name or an unindexed document is a failure.
    param(
        [Parameter(Mandatory)][hashtable]$Plane,
        [Parameter(Mandatory)][object[]]$Documents,
        [ValidateRange(0.0001, 6)][double]$WindowMinutes = 6
    )
    foreach ($document in $Documents) {
        $null = Invoke-NativeSystemTool -Plane $Plane -Name UploadKnowledgeDocument `
            -Plugin AgentMemoryPluginDefinition -Parameters @{
                fileName = $document.name; content = $document.content; triggerIndexing = $true
            }
    }
    $clock = [Diagnostics.Stopwatch]::StartNew()
    do {
        $files = @()
        $cursor = ''
        $cursors = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
        do {
            if ($clock.Elapsed.TotalMinutes -ge $WindowMinutes) { throw 'Knowledge index verification exceeded its bounded observation window.' }
            $path = '/api/v1/agentmemory/files?pageSize=100'
            if ($cursor) { $path += "&continuationToken=$([uri]::EscapeDataString($cursor))" }
            $page = Invoke-ReviewRequest -Plane $Plane -Path $path
            $pageFiles = $null
            if ($page -is [Collections.IDictionary] -and $page.Contains('files')) { $pageFiles = $page['files'] }
            if ($pageFiles -isnot [array]) { throw 'Knowledge readback omitted its native files collection.' }
            $files += $pageFiles
            $next = Get-AsreMember -Value $page -Path @('continuationToken')
            $next = if ($null -eq $next) { '' } else { [string]$next }
            if ($next -and -not $cursors.Add($next)) { throw 'Knowledge readback repeated its continuation token.' }
            $cursor = $next
        } while ($cursor)
        $pending = @()
        foreach ($document in $Documents) {
            $match = @($files | Where-Object { $_ -is [Collections.IDictionary] -and $_['name'] -ceq $document.name })
            if ($match.Count -gt 1) { throw "Knowledge readback is ambiguous: $($document.name)" }
            if ($match.Count -eq 0 -or $match[0]['isIndexed'] -ne $true) { $pending += $document.name }
        }
        if ($pending.Count -eq 0) {
            return @($Documents | ForEach-Object {
                @{ filename = $_.name; source_sha256 = $_.source_sha256; source_bytes = $_.source_bytes; indexed = $true }
            })
        }
        if ($clock.Elapsed.TotalMinutes -ge $WindowMinutes) { throw "Approved knowledge is not indexed: $($pending -join ', ')" }
        Start-Sleep -Seconds 10
    } while ($true)
}

function Publish-ReviewResource {
    # PUT one resource (retrying a 403 while the agent-scoped delivery role propagates, until -Deadline), then read it
    # back and compare strictly.
    param(
        [Parameter(Mandatory)][hashtable]$Plane,
        [Parameter(Mandatory)][hashtable]$Item,
        [Parameter(Mandatory)][DateTimeOffset]$Deadline
    )
    $path = "/api/v2/extendedAgent/$($Item.kind)/$($Item.name)"
    $body = @{ name = $Item.name; type = $Item.type; tags = @(); properties = $Item.properties }
    do {
        try {
            $null = Invoke-ReviewRequest -Plane $Plane -Method Put -Path $path -Body $body
            break
        }
        catch {
            $status = $_.Exception.Data['AsrePlaneStatus']
            if ($status -ne 403 -or [DateTimeOffset]::UtcNow -ge $Deadline) { throw }
            Write-Information 'Waiting for the agent-scoped delivery role to propagate.' -InformationAction Continue
            Start-Sleep -Seconds 15
        }
    } while ($true)
    $actual = Invoke-ReviewRequest -Plane $Plane -Path $path
    Assert-ReviewValue -Expected $Item.name -Actual (Get-AsreMember -Value $actual -Path @('name')) -Path "$($Item.kind).name"
    Assert-ReviewValue -Expected $Item.type -Actual (Get-AsreMember -Value $actual -Path @('type')) -Path "$($Item.kind).type"
    Assert-ReviewValue -Expected $Item.properties -Actual (Get-AsreMember -Value $actual -Path @('properties')) -Path $Item.name
}

if ($DefinitionsOnly) { return }

if (-not (Get-Command Get-AsreContext -ErrorAction SilentlyContinue)) { . (Join-Path $PSScriptRoot 'Context.Common.ps1') }
if (-not (Get-Command Get-AsreAgentPlane -ErrorAction SilentlyContinue)) { . (Join-Path $PSScriptRoot 'AgentPlane.Common.ps1') }
if (-not (Get-Command Assert-AsreRenderedAssets -ErrorAction SilentlyContinue)) { . (Join-Path $PSScriptRoot 'AgentAssets.Common.ps1') }

if ($Profile -ceq 'Common' -and $EnableIncidentPilot) {
    throw 'Common publication cannot activate the native ServiceNow pilot.'
}
if ($Profile -ceq 'Common' -and ($PSBoundParameters.ContainsKey('PilotCaseSysId') -or $PSBoundParameters.ContainsKey('PilotCaseNumber'))) {
    throw 'The pilot case parameters apply only to the Native profile.'
}
if ($Profile -ceq 'Native' -and ([string]::IsNullOrEmpty($PilotCaseSysId) -or [string]::IsNullOrEmpty($PilotCaseNumber))) {
    throw 'Native publication needs -PilotCaseSysId and -PilotCaseNumber.'
}

$ctx = Get-AsreContext
$outputs = Get-Content -LiteralPath $OutputsFile -Raw | ConvertFrom-Json -AsHashtable -DateKind String
$agentOutput = Get-AsreMember -Value $outputs -Path @('agent_resource_id')
if ($agentOutput -isnot [Collections.IDictionary] -or -not $agentOutput.Contains('value')) {
    throw 'The outputs file does not declare agent_resource_id.'
}
$declaredAgent = $agentOutput['value']
if ($null -eq $declaredAgent -or ($declaredAgent -is [string] -and $declaredAgent.Length -eq 0)) {
    Write-Output 'The managed agent is deliberately disabled; no agent configuration was attempted.'
    return
}
if ($declaredAgent -isnot [string] -or $declaredAgent -cne $ctx['agent']['resource_id']) {
    throw 'Use only the deployed demo agent of the deployment context.'
}
if ($Profile -ceq 'Native' -and (Get-AsreMember -Value $ctx -Path @('servicenow')) -isnot [Collections.IDictionary]) {
    throw 'Native publication needs the servicenow section of the deployment context.'
}

# Offline checks first: the rendered tree must be exactly the render of this context, and the runtime proof fresh.
$assets = Assert-AsreRenderedAssets -Context $ctx -Root $AssetRoot
$root = $assets.Root
$proof = Assert-ReviewRuntimeProof -Path $RuntimeProofFile
$repository = Split-Path $PSScriptRoot -Parent
$source = Get-ReviewSourceState -Root $repository

Assert-AsreSubscription -Context $ctx
$plane = Get-AsreAgentPlane -Context $ctx -ReadOnly:$WhatIfPreference
try {
    $resource = (Invoke-AsreAgentPlaneRequest -Plane $plane -Service Arm -Method Get -Path "$($plane['agent'])?api-version=2026-01-01")['data']
    $agentEndpoint = Get-AsreMember -Value $resource -Path @('properties', 'agentEndpoint')
    $endpoint = $null
    if ($agentEndpoint -isnot [string] -or -not [uri]::TryCreate($agentEndpoint, [UriKind]::Absolute, [ref]$endpoint)) { $endpoint = $null }
    if ((Get-AsreMember -Value $resource -Path @('properties', 'actionConfiguration', 'mode')) -cne 'Review' -or
        (Get-AsreMember -Value $resource -Path @('properties', 'actionConfiguration', 'accessLevel')) -cne 'Low' -or
        $null -eq $endpoint -or $endpoint.Scheme -cne 'https' -or -not $endpoint.Host.EndsWith('.azuresre.ai') -or
        $endpoint.UserInfo -or $endpoint.AbsolutePath -ne '/' -or $endpoint.Query -or
        -not $endpoint.IsDefaultPort -or $endpoint.AbsoluteUri.TrimEnd('/') -cne $plane['sreEndpoint']) {
        throw 'The actual agent is not the expected Review/Low HTTPS data plane.'
    }
    if ($Profile -ceq 'Native') {
        if ((Get-AsreMember -Value $resource -Path @('properties', 'incidentManagementConfiguration', 'type')) -cne 'ServiceNow') {
            throw 'Preserve the user-configured native ServiceNow platform; do not configure a second incident scanner.'
        }
        Assert-NativePilotScope -Plane $plane -CaseSysId $PilotCaseSysId -CaseNumber $PilotCaseNumber -ServiceNow $ctx['servicenow']
        $filters = @(Get-NativeIncidentFilters -Plane $plane)
        if ($EnableIncidentPilot) {
            $overlap = @($filters | Where-Object {
                $_['properties']['isEnabled'] -and -not (Test-OwnedReviewPilot -Filter $_)
            })
            if ($overlap.Count) { throw 'Review and disable overlapping/quickstart response plans before activating the fixed pilot.' }
        }
    }

    $connectorOutput = @(& (Join-Path $PSScriptRoot 'Initialize-OperationsConnector.ps1') -WhatIf:$WhatIfPreference)
    $connectors = $null
    foreach ($candidate in $connectorOutput) { if ($candidate -is [pscustomobject]) { $connectors = $candidate } }
    $connectorStatus = if ($null -ne $connectors -and $null -ne $connectors.PSObject.Properties['status']) { $connectors.status } else { $null }
    $accepted = if ($WhatIfPreference) { @('planned', 'configuration-verified') } else { @('configuration-verified') }
    if ($connectorStatus -isnot [string] -or $connectorStatus -cnotin $accepted) {
        throw 'Native telemetry connector configuration was not verified.'
    }

    $skipped = $false
    $documents = @(Get-ReviewKnowledge -Root $root)
    $knowledge = @()
    if ($PSCmdlet.ShouldProcess("$($documents.Count) approved knowledge documents", 'Publish and verify indexing')) {
        $knowledge = @(Publish-ReviewKnowledge -Plane $plane -Documents $documents)
    }
    else { $skipped = $true }
    $deadline = [DateTimeOffset]::UtcNow.AddMinutes(6)
    $names = @()
    $configuration = @(Get-ReviewConfiguration -Root $root -Profile $Profile)
    if ($Profile -ceq 'Native') { $configuration += @(Get-NativeAutomationConfiguration -Root $root) }
    foreach ($item in $configuration) {
        if (-not $PSCmdlet.ShouldProcess("$($item.kind)/$($item.name)", 'Publish bounded Review configuration')) {
            $skipped = $true
            continue
        }
        Publish-ReviewResource -Plane $plane -Item $item -Deadline $deadline
        $names += "$($item.kind)/$($item.name)"
        Write-Output "Published and independently read back bounded Review configuration: $($names[-1])"
    }
    if ($Profile -ceq 'Native' -and -not $skipped) {
        $schedule = @(Get-NativeAutomationConfiguration -Root $root)[1]
        Assert-NativeAutomationDisabled -Plane $plane -Schedule $schedule.properties
    }
    if ($EnableIncidentPilot) {
        Assert-NativePilotScope -Plane $plane -CaseSysId $PilotCaseSysId -CaseNumber $PilotCaseNumber -ServiceNow $ctx['servicenow']
        $pilot = @(Get-NativeAutomationConfiguration -Root $root -EnablePilot)[0]
        $path = "/api/v2/extendedAgent/incidentFilters/$($pilot.name)"
        if ($PSCmdlet.ShouldProcess("incidentFilters/$($pilot.name)", 'Enable the Review-mode incident pilot')) {
            try {
                $null = Invoke-ReviewRequest -Plane $plane -Method Put -Path $path `
                    -Body @{ name = $pilot.name; type = $pilot.type; tags = @(); properties = $pilot.properties }
                $actual = Invoke-ReviewRequest -Plane $plane -Path $path
                Assert-ReviewValue -Expected $pilot.properties -Actual (Get-AsreMember -Value $actual -Path @('properties')) -Path 'active-review-pilot'
                $live = @(Get-NativeIncidentFilters -Plane $plane)
                $active = @($live | Where-Object { Test-OwnedReviewPilot -Filter $_ })
                if ($active.Count -ne 1 -or $active[0]['properties']['agentMode'] -cne 'Review' -or
                    $active[0]['properties']['isEnabled'] -ne $true -or $active[0]['properties']['handlingAgent'] -cne 'asre-incident-review' -or
                    $active[0]['properties']['titleContains'] -cne $pilot.properties.titleContains) {
                    throw 'The native incident engine did not independently confirm the exact active Review pilot.'
                }
            }
            catch {
                $activationError = $_
                $draft = @(Get-NativeAutomationConfiguration -Root $root)[0]
                $null = Invoke-ReviewRequest -Plane $plane -Method Put -Path $path `
                    -Body @{ name = $draft.name; type = $draft.type; tags = @(); properties = $draft.properties }
                $readback = Invoke-ReviewRequest -Plane $plane -Path $path
                Assert-ReviewValue -Expected $draft.properties -Actual (Get-AsreMember -Value $readback -Path @('properties')) -Path 'failed-pilot-disabled'
                throw $activationError
            }
        }
        else { $skipped = $true }
    }
    if ($skipped) { return }

    $directory = Split-Path -Parent $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputsFile)
    [ordered]@{
        status = 'configured'; mode = 'Review'; resources = $names
        endpoint = ([uri]$plane['sreEndpoint']).AbsoluteUri; source_commit = $source.commit; source_dirty = $source.dirty
        runtime_evidence_uri = Get-AsreMember -Value $proof -Path @('evidence_uri')
        servicenow_bound = $false; faults_enabled = $false
        native_review_tools_bound = ($Profile -ceq 'Native')
        native_review_round_trip_accepted = $false
        trusted_case_audit_bound = $false
        review_pilot_enabled = [bool]$EnableIncidentPilot
        proactive_schedule_enabled = $false
        knowledge = $knowledge
        checked_at = [DateTimeOffset]::UtcNow.ToString('o')
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $directory 'agent-review-configuration.json') -Encoding utf8
}
finally {
    Clear-AsreAgentPlane -Plane $plane
}
