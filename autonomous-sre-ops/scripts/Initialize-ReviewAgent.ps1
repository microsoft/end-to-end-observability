[CmdletBinding(DefaultParameterSetName = 'Publish')]
param(
    [Parameter(Mandatory, ParameterSetName = 'Publish')][string]$OutputsFile,
    [Parameter(Mandatory, ParameterSetName = 'Publish')][string]$RuntimeProofFile,
    [Parameter(ParameterSetName = 'Publish')][switch]$EnableIncidentPilot,
    [Parameter(ParameterSetName = 'Publish')][ValidateSet('Native', 'Common')][string]$Profile = 'Native',
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
                @{ name = 'resource_id'; type = 'string'; required = $true; description = 'One of the three exact lab VM resource IDs.' },
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
            handoffDescription = 'Read-only evidence-backed investigation of the three isolated ASRE lab VMs.'
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
        $instructions = Get-Content -LiteralPath (Join-Path $Root "agent\instructions\$scenario-review.md") -Raw
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
                        script = Get-Content -LiteralPath (Join-Path $Root 'agent\hooks\review-case-policy.py') -Raw
                    })
                }
            }
        }
    }
}

function Get-NativeAutomationConfiguration {
    param([string]$Root, [switch]$EnablePilot)
    $manifest = Get-Content -LiteralPath (Join-Path $Root 'agent\workflows\native-review.json') -Raw | ConvertFrom-Json -AsHashtable
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

function Assert-NativeAutomationDisabled {
    param([string]$Endpoint, [hashtable]$Headers, [hashtable]$Schedule)
    $filters = @(Get-NativeIncidentFilters -Endpoint $Endpoint -Headers $Headers)
    $owned = @($filters | Where-Object { Test-OwnedReviewPilot -Filter $_ })
    if ($owned.Count -ne 1 -or $owned[0].properties.isEnabled -ne $false -or
        $owned[0].properties.agentMode -cne 'Review' -or $owned[0].properties.handlingAgent -cne 'asre-incident-review') {
        throw 'The native incident engine did not independently confirm the disabled Review pilot.'
    }
    $tasks = Invoke-RestMethod -Uri "$Endpoint/api/v1/scheduledtasks" `
        -Headers $Headers -MaximumRedirection 0 -TimeoutSec 60
    if ($tasks -isnot [array]) { throw 'Native scheduler readback did not return its root array.' }
    $ownedTasks = @($tasks | Where-Object { $_.name -ceq 'asre-proactive-review' })
    if ($ownedTasks.Count -ne 1 -or $ownedTasks[0].status -cne 'Paused' -or
        $ownedTasks[0].agent -cne $Schedule.agent -or $ownedTasks[0].cronExpression -cne $Schedule.cronExpression -or
        $ownedTasks[0].agentMode -cne 'review') {
        throw 'The native scheduler did not independently confirm the exact disabled proactive task.'
    }
}

function Get-NativeIncidentFilters {
    param([string]$Endpoint, [hashtable]$Headers)
    $result = Invoke-RestMethod -Uri "$Endpoint/api/v2/incidentManagement/incidentFilters" `
        -Headers $Headers -MaximumRedirection 0 -TimeoutSec 60
    if ($result.value -isnot [array]) { throw 'Native response-plan enumeration omitted its value collection.' }
    foreach ($entry in $result.value) {
        if (-not $entry.PSObject.Properties['name'] -or
            -not $entry.PSObject.Properties['type'] -or
            -not $entry.PSObject.Properties['properties'] -or
            $entry.type -cne 'IncidentFilter' -or [string]::IsNullOrWhiteSpace($entry.name) -or
            -not $entry.properties.PSObject.Properties['isEnabled'] -or
            $entry.properties.isEnabled -isnot [bool]) {
            throw 'Native response plans must return named IncidentFilter resource envelopes with typed properties.'
        }
        $entry
    }
}

function Get-ReviewKnowledge {
    param([string]$Root)
    $folder = Join-Path $Root 'agent\knowledge'
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

function Invoke-NativeSystemTool {
    param([string]$Endpoint, [hashtable]$Headers, [string]$Name, [string]$Plugin, [hashtable]$Parameters)
    $allowed = @{ GetServiceNowIncident = 'ServiceNowPluginDefinition'; UploadKnowledgeDocument = 'AgentMemoryPluginDefinition' }
    if (-not $allowed.ContainsKey($Name) -or $Plugin -cne $allowed[$Name]) {
        throw 'Deployment may only read the pilot case and publish approved knowledge, not append or mutate cases.'
    }
    $body = @{ toolName = $Name; pluginName = $Plugin; parameters = $Parameters } | ConvertTo-Json -Depth 8
    $result = Invoke-RestMethod -Method Post -Uri "$Endpoint/api/v1/extendedAgent/systemTool/execute" `
        -Headers $Headers -Body $body -ContentType application/json -MaximumRedirection 0 -TimeoutSec 120
    if ($result.success -ne $true) { throw "The native $Name tool reported failure; no successful operation is claimed." }
    return $result.data
}

function Assert-NativePilotScope {
    param([string]$Endpoint, [hashtable]$Headers)
    $config = Invoke-RestMethod -Uri "$Endpoint/api/v2/incidents/indexing/servicenow/configuration" `
        -Headers $Headers -MaximumRedirection 0 -TimeoutSec 60
    if ($config.assignmentGroup -cne 'ASRE Demo Operations') {
        throw 'The native incoming ServiceNow scope differs from the approved demo assignment group.'
    }
    $case = Invoke-NativeSystemTool -Endpoint $Endpoint -Headers $Headers -Name GetServiceNowIncident `
        -Plugin ServiceNowPluginDefinition -Parameters @{ incidentSystemId = '0000000000000000000000000000010a' }
    if ($case.sys_id -cne '0000000000000000000000000000010a' -or $case.number -cne 'INC0900001' -or
        $case.assignment_group -cne '0000000000000000000000000000007a' -or
        $case.cmdb_ci -cne '0000000000000000000000000000014a' -or $case.state -notin @('1', '2', '3') -or
        $case.priority -cne '4' -or
        $case.short_description -cne '[ASRE Demo] Synthetic readiness test (linux); no real Azure alert') {
        throw 'The authenticated native pilot case does not match its fixed ID, group, CI, open state and synthetic title.'
    }
}

function Publish-ReviewKnowledge {
    param([string]$Root, [string]$Endpoint, [hashtable]$Headers)
    $documents = @(Get-ReviewKnowledge -Root $Root)
    foreach ($document in $documents) {
        $null = Invoke-NativeSystemTool -Endpoint $Endpoint -Headers $Headers -Name UploadKnowledgeDocument `
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
            if ($clock.Elapsed.TotalMinutes -ge 6) { throw 'Knowledge index verification exceeded its bounded observation window.' }
            $uri = "$Endpoint/api/v1/agentmemory/files?pageSize=100"
            if ($cursor) { $uri += "&continuationToken=$([uri]::EscapeDataString($cursor))" }
            $page = Invoke-RestMethod -Uri $uri -Headers $Headers -MaximumRedirection 0 -TimeoutSec 60
            if ($page.files -isnot [array]) { throw 'Knowledge readback omitted its native files collection.' }
            $files += $page.files
            $next = [string]$page.continuationToken
            if ($next -and -not $cursors.Add($next)) { throw 'Knowledge readback repeated its continuation token.' }
            $cursor = $next
        } while ($cursor)
        $pending = @()
        foreach ($document in $documents) {
            $match = @($files | Where-Object name -ceq $document.name)
            if ($match.Count -gt 1) { throw "Knowledge readback is ambiguous: $($document.name)" }
            if ($match.Count -eq 0 -or $match[0].isIndexed -ne $true) { $pending += $document.name }
        }
        if ($pending.Count -eq 0) {
            return @($documents | ForEach-Object {
                @{ filename = $_.name; source_sha256 = $_.source_sha256; source_bytes = $_.source_bytes; indexed = $true }
            })
        }
        if ($clock.Elapsed.TotalMinutes -ge 6) { throw "Approved knowledge is not indexed: $($pending -join ', ')" }
        Start-Sleep -Seconds 10
    } while ($true)
}

function Assert-ReviewValue {
    param($Expected, $Actual, [string]$Path)
    if ($Expected -is [Collections.IDictionary]) {
        foreach ($key in $Expected.Keys) {
            $property = $Actual.PSObject.Properties[$key]
            if (-not $property) { throw "Agent readback omitted $Path.$key" }
            Assert-ReviewValue -Expected $Expected[$key] -Actual $property.Value -Path "$Path.$key"
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
        $property = $Filter.PSObject.Properties[$key]
        if ($property -and $property.Value -ceq 'asre-servicenow-review-pilot') { return $true }
    }
    return $false
}

if ($DefinitionsOnly) { return }
if ($Profile -eq 'Common' -and $EnableIncidentPilot) {
    throw 'Common publication cannot activate the native ServiceNow pilot.'
}
Write-Output '##vso[task.setvariable variable=AgentConfigured]false'
$outputs = Get-Content -LiteralPath $OutputsFile -Raw | ConvertFrom-Json
if (-not $outputs.agent_resource_id.value) {
    Write-Output 'The managed agent is deliberately disabled; no agent configuration was attempted.'
    return
}
$subscription = '00000001-000a-4000-a000-000000000001'
$agent = "/subscriptions/$subscription/resourceGroups/rg-asre-control-cus/providers/Microsoft.App/agents/asre-demo-cus"
if ($outputs.agent_resource_id.value -ne $agent -or
    $env:servicePrincipalId -ne '00000018-000a-4000-b000-000000000018' -or
    $env:tenantId -ne '00000002-000a-4000-9000-000000000002' -or
    [string]::IsNullOrWhiteSpace($env:idToken)) {
    throw 'Use only the approved protected Apply connection and the exact deployed lab agent.'
}
$proof = Get-Content -LiteralPath $RuntimeProofFile -Raw | ConvertFrom-Json
$age = [DateTimeOffset]::UtcNow - [DateTimeOffset]::Parse($proof.checked_at)
if ($proof.status -ne 'verified' -or $proof.mode -ne 'Review' -or
    $proof.targets -ne 3 -or $proof.faults_enabled -ne $false -or
    $age.TotalSeconds -lt -30 -or $age.TotalMinutes -gt 15) {
    throw 'Agent binding requires fresh real three-target private SSH acceptance with faults disabled.'
}
$json = & az rest --method get --url "https://management.azure.com${agent}?api-version=2026-01-01" --subscription $subscription --only-show-errors --output json
if ($LASTEXITCODE -ne 0) { throw 'Cannot read the actual managed-agent configuration.' }
$resource = $json | ConvertFrom-Json
$endpoint = [uri]$resource.properties.agentEndpoint
if ($resource.properties.actionConfiguration.mode -ne 'Review' -or
    $resource.properties.actionConfiguration.accessLevel -ne 'Low' -or
    $endpoint.Scheme -ne 'https' -or -not $endpoint.Host.EndsWith('.azuresre.ai') -or
    $endpoint.UserInfo -or $endpoint.AbsolutePath -ne '/' -or $endpoint.Query -or
    -not $endpoint.IsDefaultPort) {
    throw 'The actual agent is not the expected Review/Low HTTPS data plane.'
}
$token = & az account get-access-token --resource https://azuresre.dev --subscription $subscription --query accessToken --output tsv --only-show-errors
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($token)) { throw 'Cannot authenticate to the SRE Agent data plane.' }
$headers = @{ Authorization = "Bearer $token" }
$root = Split-Path $PSScriptRoot -Parent
$base = $endpoint.AbsoluteUri.TrimEnd('/')
if ($Profile -eq 'Native') {
    if ($resource.properties.incidentManagementConfiguration.type -cne 'ServiceNow') {
        throw 'Preserve the user-configured native ServiceNow platform; do not configure a second incident scanner.'
    }
    Assert-NativePilotScope -Endpoint $base -Headers $headers
    $filters = @(Get-NativeIncidentFilters -Endpoint $base -Headers $headers)
    if ($EnableIncidentPilot) {
        $overlap = @($filters | Where-Object {
            $_.properties.isEnabled -and -not (Test-OwnedReviewPilot -Filter $_)
        })
        if ($overlap.Count) { throw 'Review and disable overlapping/quickstart response plans before activating the fixed pilot.' }
    }
}
$connectors = & (Join-Path $PSScriptRoot 'Initialize-OperationsConnector.ps1')
if ($connectors.status -cne 'configuration-verified') { throw 'Native telemetry connector configuration was not verified.' }
$knowledge = @(Publish-ReviewKnowledge -Root $root -Endpoint $base -Headers $headers)
$deadline = [DateTimeOffset]::UtcNow.AddMinutes(6)
$names = @()
$configuration = @(Get-ReviewConfiguration -Root $root -Profile $Profile)
if ($Profile -eq 'Native') { $configuration += @(Get-NativeAutomationConfiguration -Root $root) }
foreach ($item in $configuration) {
    $uri = $endpoint.AbsoluteUri.TrimEnd('/') + "/api/v2/extendedAgent/$($item.kind)/$($item.name)"
    $body = @{ name = $item.name; type = $item.type; tags = @(); properties = $item.properties } | ConvertTo-Json -Depth 20
    do {
        try {
            $null = Invoke-RestMethod -Method Put -Uri $uri -Headers $headers -Body $body -ContentType application/json -MaximumRedirection 0 -TimeoutSec 60
            break
        }
        catch {
            $response = $_.Exception.PSObject.Properties['Response']
            if (-not $response -or -not $response.Value -or [int]$response.Value.StatusCode -ne 403 -or [DateTimeOffset]::UtcNow -ge $deadline) { throw }
            Write-Information 'Waiting for the agent-scoped delivery role to propagate.' -InformationAction Continue
            Start-Sleep -Seconds 15
        }
    } while ($true)
    $actual = Invoke-RestMethod -Method Get -Uri $uri -Headers $headers -MaximumRedirection 0 -TimeoutSec 60
    Assert-ReviewValue -Expected $item.name -Actual $actual.name -Path "$($item.kind).name"
    Assert-ReviewValue -Expected $item.type -Actual $actual.type -Path "$($item.kind).type"
    Assert-ReviewValue -Expected $item.properties -Actual $actual.properties -Path $item.name
    $names += "$($item.kind)/$($item.name)"
    Write-Output "Published and independently read back bounded Review configuration: $($names[-1])"
}
if ($Profile -eq 'Native') {
    $schedule = @(Get-NativeAutomationConfiguration -Root $root)[1]
    Assert-NativeAutomationDisabled -Endpoint $base -Headers $headers -Schedule $schedule.properties
}
if ($EnableIncidentPilot) {
    Assert-NativePilotScope -Endpoint $base -Headers $headers
    $pilot = @(Get-NativeAutomationConfiguration -Root $root -EnablePilot)[0]
    $uri = "$base/api/v2/extendedAgent/incidentFilters/$($pilot.name)"
    $body = @{ name = $pilot.name; type = $pilot.type; tags = @(); properties = $pilot.properties } | ConvertTo-Json -Depth 20
    try {
        $null = Invoke-RestMethod -Method Put -Uri $uri -Headers $headers -Body $body -ContentType application/json -MaximumRedirection 0 -TimeoutSec 60
        $actual = Invoke-RestMethod -Uri $uri -Headers $headers -MaximumRedirection 0 -TimeoutSec 60
        Assert-ReviewValue -Expected $pilot.properties -Actual $actual.properties -Path 'active-review-pilot'
        $live = @(Get-NativeIncidentFilters -Endpoint $base -Headers $headers)
        $active = @($live | Where-Object { Test-OwnedReviewPilot -Filter $_ })
        if ($active.Count -ne 1 -or $active[0].properties.agentMode -cne 'Review' -or
            $active[0].properties.isEnabled -ne $true -or $active[0].properties.handlingAgent -cne 'asre-incident-review' -or
            $active[0].properties.titleContains -cne $pilot.properties.titleContains) {
            throw 'The native incident engine did not independently confirm the exact active Review pilot.'
        }
    }
    catch {
        $activationError = $_
        $draft = @(Get-NativeAutomationConfiguration -Root $root)[0]
        $disabled = @{ name = $draft.name; type = $draft.type; tags = @(); properties = $draft.properties } | ConvertTo-Json -Depth 20
        $null = Invoke-RestMethod -Method Put -Uri $uri -Headers $headers -Body $disabled -ContentType application/json -MaximumRedirection 0 -TimeoutSec 60
        $readback = Invoke-RestMethod -Uri $uri -Headers $headers -MaximumRedirection 0 -TimeoutSec 60
        Assert-ReviewValue -Expected $draft.properties -Actual $readback.properties -Path 'failed-pilot-disabled'
        throw $activationError
    }
}
@{
    status = 'configured'; mode = 'Review'; resources = $names
    endpoint = $endpoint.AbsoluteUri; source_commit = $env:BUILD_SOURCEVERSION
    runtime_evidence_uri = $proof.evidence_uri
    servicenow_bound = $false; faults_enabled = $false
    native_review_tools_bound = ($Profile -eq 'Native')
    native_review_round_trip_accepted = $false
    trusted_case_audit_bound = $false
    review_pilot_enabled = [bool]$EnableIncidentPilot
    proactive_schedule_enabled = $false
    knowledge = $knowledge
    checked_at = [DateTimeOffset]::UtcNow.ToString('o')
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path (Split-Path $OutputsFile -Parent) 'agent-review-configuration.json') -Encoding utf8
Write-Output '##vso[task.setvariable variable=AgentConfigured]true'
