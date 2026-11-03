#Requires -Version 7.5
<#
.SYNOPSIS
Declares, binds, publishes, verifies, enables and pauses the observe-only Azure Monitor incident handler.

.DESCRIPTION
The handler opens an automatic Review-mode investigation thread for an Azure Monitor alert of the demo
environment. It has one read-only evidence tool and no case tool, hook, skill or repair capability. The
declaration lives in agent/workflows/azure-monitor-observe.json and agent/instructions/azure-monitor-observe.md;
every deployment identity comes from the deployment context, never from those files.

Modes:
  Validate         Offline. Checks the two asset files against the pinned declaration. No context, no network.
  Bind             Binds the agent to Azure Monitor (one ARM PATCH on the agent), waits for readiness and lists the
                   other incident filters. Requires -ConfirmAgentName.
  PublishDisabled  Publishes the handler, its permission policy and its DISABLED incident filter after read-only checks.
  Verify           Read-only. Strict comparison of everything published; -ExpectedState Any|Disabled|Enabled.
  Enable           Verifies, then enables the filter. Compensates by pausing it when the outcome is not verified.
                   Requires -ConfirmAgentName. Refuses while another incident filter is enabled.
  Pause            Emergency lever: disables the filter (never creates it, never touches a foreign filter).

The evidence tool, common prompt and skills are published by Initialize-ReviewAgent.ps1 -Profile Common, never here.
All traffic goes through Invoke-AsreAgentPlaneRequest. Failures name the step, category, HTTP status and error code
only; no token, header, response body or deployment identifier is written to any stream.

.PARAMETER Mode
Validate, Bind, PublishDisabled, Verify, Enable or Pause (case-sensitive).

.PARAMETER ContextPath
Deployment context file. Defaults to the loader's own search order.

.PARAMETER AssetRoot
Directory laid out like the subproject root. A non-default root must be byte-identical (after CRLF to LF) to the
subproject's own copies of the two asset files.

.PARAMETER ExpectedState
Verify only: Any (default), Disabled or Enabled.

.PARAMETER ConfirmAgentName
Required by Bind and Enable: the agent name of the deployment context, case-sensitive.

.PARAMETER ReadinessTimeoutSeconds
Bind only: how long to wait for the platform binding to become ready (30 to 900, default 300).

.PARAMETER BindApiVersion
ARM API version used to bind and to read the platform binding.

.PARAMETER DefinitionsOnly
Defines every function and returns (used by the offline test).

.EXAMPLE
.\Publish-ObserveWorkflow.ps1 -Mode Validate

.EXAMPLE
.\Publish-ObserveWorkflow.ps1 -Mode Bind -ConfirmAgentName asre-demo-cus -WhatIf

.EXAMPLE
.\Publish-ObserveWorkflow.ps1 -Mode PublishDisabled

.EXAMPLE
.\Publish-ObserveWorkflow.ps1 -Mode Verify -ExpectedState Disabled

.EXAMPLE
.\Publish-ObserveWorkflow.ps1 -Mode Enable -ConfirmAgentName asre-demo-cus

.EXAMPLE
.\Publish-ObserveWorkflow.ps1 -Mode Pause
#>
[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'Medium', DefaultParameterSetName = 'Run')]
param(
    [Parameter(ParameterSetName = 'Run', Mandatory)]
    [ValidateSet('Validate', 'Bind', 'PublishDisabled', 'Verify', 'Enable', 'Pause', IgnoreCase = $false)]
    [string]$Mode,

    [Parameter(ParameterSetName = 'Definitions', Mandatory)]
    [switch]$DefinitionsOnly,

    [Parameter(ParameterSetName = 'Run')][string]$ContextPath,
    [Parameter(ParameterSetName = 'Run')][string]$AssetRoot,
    [Parameter(ParameterSetName = 'Run')][ValidateSet('Any', 'Disabled', 'Enabled', IgnoreCase = $false)][string]$ExpectedState = 'Any',
    [Parameter(ParameterSetName = 'Run')][string]$ConfirmAgentName,
    [Parameter(ParameterSetName = 'Run')][ValidateRange(30, 900)][int]$ReadinessTimeoutSeconds = 300,
    [Parameter(ParameterSetName = 'Run')][ValidatePattern('\A\d{4}-\d{2}-\d{2}(-preview)?\z')][string]$BindApiVersion = '2025-05-01-preview'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if (-not (Get-Command Invoke-AsreAgentPlaneRequest -ErrorAction SilentlyContinue)) { . (Join-Path $PSScriptRoot 'AgentPlane.Common.ps1') }

# ----------------------------------------------------------------------------------------------------------------
# Pinned names and tables (the declaration files must agree with them; Get-ObserveAssets enforces it).
# ----------------------------------------------------------------------------------------------------------------
$script:ObserveRoot = Split-Path $PSScriptRoot -Parent
$script:ObserveHandlerName = 'asre-azure-monitor-observe'
$script:ObserveFilterName = 'asre-azure-monitor-observe'
$script:ObserveToolName = 'asre-review-evidence'
$script:ObserveDeclarationPath = 'agent/workflows/azure-monitor-observe.json'
$script:ObserveInstructionsPath = 'agent/instructions/azure-monitor-observe.md'
$script:ObserveMaxInstructionBytes = 16384
$script:ObserveMaxDeclarationBytes = 65536
$script:ObservePolicyNames = @('QueryLogAnalyticsByResourceId', 'ReadFile', 'SearchMemory', 'asre-review-evidence')
$script:ObserveHeadings = @('Summary', 'Alert and scope', 'Observation window (UTC)', 'Evidence (request ids and receipts)',
    'Findings and confidence', 'Gaps and missing evidence', 'Suggested next steps (not executed)')

# Text that must never appear in the assets. The ticketing words are assembled from parts so that this script
# itself carries no ticketing-product text.
$script:ObserveForbiddenText = @(
    ('(?i)service' + '[- ]?' + 'now'),
    '(?i)autonomous',
    '(?i)/subscriptions/',
    '(?i)azuresre',
    '://',
    '[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}',
    '(?<![0-9A-Za-z])[0-9A-Fa-f]{32}(?![0-9A-Za-z])',
    '(?<![0-9A-Za-z])[0-9A-Fa-f]{64}(?![0-9A-Za-z])',
    '(?<![0-9A-Za-z])INC[0-9]{3,}(?![0-9A-Za-z])',
    '(?i)-cus--',
    '(?i)(stasre|acrasre|kv-asre-)[0-9a-f]{10}',
    '\{\{|\}\}|\$\{|__[A-Z0-9_]{2,}__',
    '<[A-Za-z][^<>\r\n]{0,60}>'
)

# Server defaults tolerated on readback when a property is not part of the expected definition. They are copied
# from the retired data-plane contract, which is the only source of defaults. Nothing else is tolerated.
$script:ObserveHandlerDefaults = [hashtable]::new([System.StringComparer]::Ordinal)
foreach ($name in 'connectors', 'allowParallelToolCalls', 'agentsAsTools', 'maxReflectionCount', 'criticPromptPath',
    'criticOnHandOff', 'customReflectionNote', 'commonPrompts', 'disableDocumentRetrieval', 'enableHandoffPromptOverride',
    'userPromptOverride', 'handoffPromptOverride', 'instructionsOverride', 'temperature', 'llmModelName', 'outputType',
    'permissions') { $script:ObserveHandlerDefaults[$name] = $null }
$script:ObserveHandlerDefaults['enableVanillaMode'] = $false

$script:ObserveFilterDefaults = [hashtable]::new([System.StringComparer]::Ordinal)
foreach ($name in 'name', 'impactedService', 'state', 'category', 'subcategory', 'configurationItem', 'callerId', 'location',
    'impact', 'urgency', 'severity', 'incidentType', 'alertId', 'owningTeamId') { $script:ObserveFilterDefaults[$name] = '' }
foreach ($name in 'assignmentGroups', 'customFields', 'incidentTypes', 'titleContainsAll', 'titleContainsAny',
    'titleNotContains', 'owningTeamIds') { $script:ObserveFilterDefaults[$name] = @() }
foreach ($name in 'handlingAgents', 'icmFilterSettings', 'configurationWarning') { $script:ObserveFilterDefaults[$name] = $null }
$script:ObserveFilterDefaults[('requiresService' + 'NowPlatformFeatures')] = $false

# Message prefixes of the frozen helpers: they carry fixed text or property paths only, never values.
$script:ObserveHelperMessage = '\A(Strict readback (changed|omitted) |The agent plane|ARM (requests|writes) |SRE requests |A read-only agent plane|Only PATCH may|The If-Match|Azure CLI cannot|The Azure CLI account|ARM authentication|SRE authentication|The agent has no valid|The agent endpoint|The deployment context|Invalid deployment context)'

# ----------------------------------------------------------------------------------------------------------------
# Errors, sanitising and failure mapping
# ----------------------------------------------------------------------------------------------------------------
function New-ObserveError {
    # An error whose text is fixed by this script (names only, never values).
    param([Parameter(Mandatory)][string]$Message)
    $failure = [System.InvalidOperationException]::new($Message)
    $failure.Data['AsreObserveSafe'] = $true
    return $failure
}

function Get-ObserveSafeName {
    param($Value)
    if ($Value -is [string] -and [regex]::IsMatch($Value, '\A[A-Za-z0-9._ -]{1,64}\z')) { return $Value }
    return '<unprintable>'
}

function ConvertTo-ObserveFailure {
    # Maps any exception to a safe one: step, category, HTTP status and error-code token plus a fixed hint.
    param([Parameter(Mandatory)][string]$Step, [Parameter(Mandatory)][System.Management.Automation.ErrorRecord]$ErrorRecord)
    $exception = $ErrorRecord.Exception
    $data = $exception.Data
    if ($data.Contains('AsrePlaneCategory')) {
        $category = [string]$data['AsrePlaneCategory']
        $status = if ($data.Contains('AsrePlaneStatus')) { $data['AsrePlaneStatus'] } else { $null }
        $code = if ($data.Contains('AsrePlaneErrorCode')) { [string]$data['AsrePlaneErrorCode'] } else { $null }
        $method = if ($data.Contains('AsrePlaneMethod')) { [string]$data['AsrePlaneMethod'] } else { 'Get' }
        $detail = $category
        if ($status -is [int]) { $detail += ", HTTP $status" }
        if ($code) { $detail += ", $code" }
        $message = "$Step failed ($detail)."
        if ($category -ceq 'TransportFailure') { $message += ' Check the agent endpoint and network access.' }
        elseif ($status -eq 400 -and $code -ceq 'IncidentPlatformConfigurationPending' -and $Step -like 'filter*') {
            $message += ' The platform is not ready: platform not ready, run -Mode Bind and wait.'
        }
        elseif ($status -eq 412) { $message += ' The resource changed concurrently, re-read it.' }
        elseif ($status -eq 403) { $message += " Check the operator's role on the agent and allow for propagation." }
        if ($method -cne 'Get') { $message += ' The outcome of the write is not confirmed: re-read before retrying.' }
        return (New-ObserveError -Message $message)
    }
    if ($data.Contains('AsreObserveSafe')) { return $exception }
    if ([regex]::IsMatch($exception.Message, $script:ObserveHelperMessage)) {
        return (New-ObserveError -Message "$Step failed: $($exception.Message)")
    }
    return (New-ObserveError -Message "$Step failed with an unexpected $($exception.GetType().Name) error; details are suppressed.")
}

function Invoke-ObserveRequest {
    # The only way this script talks to Azure: the frozen plane helper, with a write gate in front of it.
    param(
        [Parameter(Mandatory)][System.Collections.IDictionary]$Run,
        [Parameter(Mandatory)][string]$Step,
        [Parameter(Mandatory)][ValidateSet('Arm', 'Sre', IgnoreCase = $false)][string]$Service,
        [ValidateSet('Get', 'Put', 'Patch', IgnoreCase = $false)][string]$Method = 'Get',
        [Parameter(Mandatory)][string]$Path,
        $Body,
        [string]$IfMatch,
        [switch]$AllowNotFound
    )
    if ($Method -cne 'Get' -and -not $Run['Authorised']) { throw (New-ObserveError -Message "$Step was not authorised; nothing was sent.") }
    $request = @{ Plane = $Run['Plane']; Service = $Service; Method = $Method; Path = $Path }
    if ($PSBoundParameters.ContainsKey('Body')) { $request['Body'] = $Body }
    if ($IfMatch) { $request['IfMatch'] = $IfMatch }
    if ($AllowNotFound) { $request['AllowNotFound'] = $true }
    try { return (Invoke-AsreAgentPlaneRequest @request) }
    catch { throw (ConvertTo-ObserveFailure -Step $Step -ErrorRecord $_) }
}

# ----------------------------------------------------------------------------------------------------------------
# Assets (offline)
# ----------------------------------------------------------------------------------------------------------------
function Read-ObserveAssetText {
    # Bounded, strict UTF-8, no byte-order mark, no control characters, CRLF normalised to LF.
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][int]$MaxBytes, [Parameter(Mandatory)][string]$Check)
    if (-not [System.IO.File]::Exists($Path)) { throw (New-ObserveError -Message "The observe handler assets are invalid ($Check is missing).") }
    $bytes = $null
    try {
        $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
        try {
            $buffer = [byte[]]::new($MaxBytes * 2 + 1)
            $count = 0
            while ($count -lt $buffer.Length) {
                $read = $stream.Read($buffer, $count, $buffer.Length - $count)
                if ($read -le 0) { break }
                $count += $read
            }
            $bytes = [byte[]]::new($count)
            [System.Array]::Copy($buffer, $bytes, $count)
        }
        finally { $stream.Dispose() }
    }
    catch { throw (New-ObserveError -Message "The observe handler assets are invalid ($Check cannot be read).") }
    if ($bytes.Length -eq 0 -or $bytes.Length -gt ($MaxBytes * 2)) { throw (New-ObserveError -Message "The observe handler assets are invalid ($Check is empty or oversized).") }
    try { $text = [System.Text.UTF8Encoding]::new($false, $true).GetString($bytes) }
    catch { throw (New-ObserveError -Message "The observe handler assets are invalid ($Check is not UTF-8).") }
    $text = $text.Replace("`r`n", "`n")
    if ($text.Length -eq 0 -or $text[0] -eq [char]0xFEFF -or $text.Contains("`r") -or
        [regex]::IsMatch($text, '[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]')) {
        throw (New-ObserveError -Message "The observe handler assets are invalid ($Check has a byte-order mark or unsupported characters).")
    }
    if ([System.Text.Encoding]::UTF8.GetByteCount($text) -gt $MaxBytes) { throw (New-ObserveError -Message "The observe handler assets are invalid ($Check is oversized).") }
    return $text
}

function Test-ObserveForbiddenText {
    param([Parameter(Mandatory)][string]$Text)
    foreach ($pattern in $script:ObserveForbiddenText) { if ([regex]::IsMatch($Text, $pattern)) { return $true } }
    return $false
}

function Get-ObserveSha256 {
    param([Parameter(Mandatory)][string]$Text)
    return [System.Convert]::ToHexString([System.Security.Cryptography.SHA256]::HashData([System.Text.Encoding]::UTF8.GetBytes($Text))).ToLowerInvariant()
}

function Get-ObserveAssets {
    # Validates the two asset files under -Root against the pinned declaration; returns the declaration and the
    # LF-normalised instruction text. Any deviation throws one fixed message naming the failed check, never a value.
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Root)
    $declarationText = Read-ObserveAssetText -Path (Join-Path $Root $script:ObserveDeclarationPath) -MaxBytes $script:ObserveMaxDeclarationBytes -Check 'declaration file'
    $instructions = Read-ObserveAssetText -Path (Join-Path $Root $script:ObserveInstructionsPath) -MaxBytes $script:ObserveMaxInstructionBytes -Check 'instruction file'
    if ((Test-ObserveForbiddenText -Text $declarationText) -or (Test-ObserveForbiddenText -Text $instructions)) {
        throw (New-ObserveError -Message 'The observe handler assets are invalid (forbidden content: identity, endpoint, ticketing or placeholder text).')
    }
    foreach ($heading in $script:ObserveHeadings) {
        if (-not $instructions.Contains($heading)) { throw (New-ObserveError -Message 'The observe handler assets are invalid (the instructions miss a fixed report heading).') }
    }
    if (-not $instructions.Contains($script:ObserveToolName)) { throw (New-ObserveError -Message 'The observe handler assets are invalid (the instructions do not name the evidence tool).') }
    try { $declaration = ConvertFrom-AsreStrictJson -Json $declarationText }
    catch { throw (New-ObserveError -Message 'The observe handler assets are invalid (the declaration is not strict JSON without duplicate keys).') }
    $description = Get-AsreMember -Value $declaration -Path @('description')
    $handoff = Get-AsreMember -Value $declaration -Path @('handler', 'properties', 'handoffDescription')
    if ($description -isnot [string] -or $description.Trim().Length -eq 0 -or $description.Length -gt 400 -or
        $handoff -isnot [string] -or $handoff.Trim().Length -eq 0 -or $handoff.Length -gt 200) {
        throw (New-ObserveError -Message 'The observe handler assets are invalid (description text).')
    }
    $policy = [object[]]@($script:ObservePolicyNames)
    $expected = [ordered]@{
        format           = 'asre-observe-workflow/v1'
        description      = $description
        handler          = [ordered]@{
            kind              = 'agents'
            name              = $script:ObserveHandlerName
            type              = 'ExtendedAgent'
            instructions_file = $script:ObserveInstructionsPath
            properties        = [ordered]@{
                handoffDescription = $handoff
                tools              = [object[]]@('QueryLogAnalyticsByResourceId', 'ReadFile', 'SearchMemory')
                mcpTools           = [object[]]@($script:ObserveToolName)
                commonTools        = [object[]]@()
                handoffs           = [object[]]@()
                enableSkills       = $false
                allowedSkills      = [object[]]@()
                addSystemSkills    = $false
            }
        }
        permissions      = [ordered]@{ allow = $policy }
        response_plan    = [ordered]@{
            kind       = 'incidentFilters'
            name       = $script:ObserveFilterName
            type       = 'IncidentFilter'
            properties = [ordered]@{
                incidentPlatform                  = 'AzMonitor'
                isEnabled                         = $false
                agentMode                         = 'Review'
                handlingAgent                     = $script:ObserveHandlerName
                priorities                        = [object[]]@('Sev1', 'Sev2')
                titleContains                     = 'asre-demo-'
                maxAutomatedInvestigationAttempts = [double]1
                mergeEnabled                      = $false
                mergeWindowHours                  = [double]3
                azMonitorFilterSettings           = [ordered]@{ targetResourceType = 'Microsoft.OperationalInsights/workspaces' }
            }
        }
        platform_binding = [ordered]@{ type = 'AzMonitor'; connectionName = 'azmonitor'; oboUser = '' }
    }
    try { Assert-AsreExact -Expected $expected -Actual $declaration -Path 'declaration' }
    catch { throw (New-ObserveError -Message 'The observe handler assets are invalid (the declaration deviates from the pinned definition).') }
    return [ordered]@{ declaration = $declaration; instructions = $instructions; instructions_sha256 = (Get-ObserveSha256 -Text $instructions) }
}

function Assert-ObserveAssetRootIdentity {
    # A non-default asset root is accepted only when both files equal the subproject's own copies (after CRLF to LF).
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Root)
    foreach ($relative in @($script:ObserveDeclarationPath, $script:ObserveInstructionsPath)) {
        $own = Join-Path $script:ObserveRoot $relative
        $other = Join-Path $Root $relative
        if (-not [System.IO.File]::Exists($own) -or -not [System.IO.File]::Exists($other)) {
            throw (New-ObserveError -Message 'The asset root differs from the subproject assets (identity check).')
        }
        $strict = [System.Text.UTF8Encoding]::new($false, $true)
        try {
            $ownText = $strict.GetString([System.IO.File]::ReadAllBytes($own)).Replace("`r`n", "`n")
            $otherText = $strict.GetString([System.IO.File]::ReadAllBytes($other)).Replace("`r`n", "`n")
        }
        catch { throw (New-ObserveError -Message 'The asset root differs from the subproject assets (identity check).') }
        if (-not [string]::Equals($ownText, $otherText, [System.StringComparison]::Ordinal)) {
            throw (New-ObserveError -Message 'The asset root differs from the subproject assets (identity check).')
        }
    }
}

# ----------------------------------------------------------------------------------------------------------------
# Expected resources
# ----------------------------------------------------------------------------------------------------------------
function New-ObserveHandlerProperties {
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Assets)
    $p = $Assets['declaration']['handler']['properties']
    return [ordered]@{
        instructions       = [string]$Assets['instructions']
        handoffDescription = [string]$p['handoffDescription']
        tools              = [object[]]@($p['tools'] | ForEach-Object { [string]$_ })
        mcpTools           = [object[]]@($p['mcpTools'] | ForEach-Object { [string]$_ })
        commonTools        = [object[]]@()
        handoffs           = [object[]]@()
        enableSkills       = $false
        allowedSkills      = [object[]]@()
        addSystemSkills    = $false
    }
}

function New-ObserveHandlerBody {
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Run)
    return [ordered]@{ name = $Run['HandlerName']; type = 'ExtendedAgent'; properties = $Run['HandlerProperties']; tags = [object[]]@() }
}

function New-ObservePolicy {
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Assets)
    $names = [string[]]@($Assets['declaration']['permissions']['allow'] | ForEach-Object { [string]$_ })
    [System.Array]::Sort($names, [System.StringComparer]::Ordinal)
    return [ordered]@{ allow = [object[]]$names }
}

function New-ObserveBinding {
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Assets)
    $b = $Assets['declaration']['platform_binding']
    return [ordered]@{ type = [string]$b['type']; connectionName = [string]$b['connectionName']; oboUser = [string]$b['oboUser'] }
}

function New-ObserveFilterProperties {
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Run, [Parameter(Mandatory)][bool]$Enabled)
    $p = $Run['Assets']['declaration']['response_plan']['properties']
    return [ordered]@{
        incidentPlatform                  = [string]$p['incidentPlatform']
        isEnabled                         = $Enabled
        agentMode                         = [string]$p['agentMode']
        handlingAgent                     = [string]$p['handlingAgent']
        priorities                        = [object[]]@($p['priorities'] | ForEach-Object { [string]$_ })
        titleContains                     = [string]$p['titleContains']
        maxAutomatedInvestigationAttempts = [int]$p['maxAutomatedInvestigationAttempts']
        mergeEnabled                      = [bool]$p['mergeEnabled']
        mergeWindowHours                  = [int]$p['mergeWindowHours']
        azMonitorFilterSettings           = [ordered]@{
            targetResourceType = [string]$p['azMonitorFilterSettings']['targetResourceType']
            targetResource     = [string]$Run['Ctx']['log_analytics']['resource_id']
        }
    }
}

function New-ObserveFilterBody {
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Run, [Parameter(Mandatory)][bool]$Enabled)
    return [ordered]@{ name = $Run['FilterName']; type = 'IncidentFilter'; properties = (New-ObserveFilterProperties -Run $Run -Enabled $Enabled) }
}

# ----------------------------------------------------------------------------------------------------------------
# Strict readback with tolerated server defaults
# ----------------------------------------------------------------------------------------------------------------
function ConvertTo-ObservePolicy {
    # Normalises a permission policy readback: allow plus optional EMPTY ask/deny arrays; allow sorted for set equality.
    param($Value)
    if ($Value -isnot [System.Collections.IDictionary] -or -not $Value.Contains('allow') -or $Value['allow'] -isnot [array]) {
        throw (New-ObserveError -Message 'Strict readback changed permissions.allow')
    }
    foreach ($key in @($Value.Keys)) {
        $name = [string]$key
        if (@('allow', 'ask', 'deny') -cnotcontains $name -or $Value[$key] -isnot [array] -or ($name -cne 'allow' -and $Value[$key].Count -gt 0)) {
            throw (New-ObserveError -Message "Strict readback changed permissions.$(Get-ObserveSafeName $name)")
        }
    }
    $names = [System.Collections.Generic.List[string]]::new()
    foreach ($item in $Value['allow']) {
        if ($item -isnot [string]) { throw (New-ObserveError -Message 'Strict readback changed permissions.allow') }
        $names.Add($item)
    }
    $sorted = $names.ToArray()
    [System.Array]::Sort($sorted, [System.StringComparer]::Ordinal)
    return [ordered]@{ allow = [object[]]$sorted }
}

function Test-ObserveDefaultValue {
    param($Default, $Value)
    if ($null -eq $Default) { return ($null -eq $Value) }
    if ($Default -is [string]) { return (($Value -is [string]) -and ($Value -ceq $Default)) }
    if ($Default -is [bool]) { return (($Value -is [bool]) -and ($Value -eq $Default)) }
    if ($Default -is [array]) { return (($Value -is [array]) -and $Value.Count -eq 0) }
    return $false
}

function ConvertTo-ObserveComparableProperties {
    # Envelope and server-default normalisation; the returned properties are compared with Assert-AsreExact.
    param(
        [Parameter(Mandatory)][ValidateSet('handler', 'filter', IgnoreCase = $false)][string]$Kind,
        $Resource, [Parameter(Mandatory)][string]$Name, [Parameter(Mandatory)][string]$Type,
        [Parameter(Mandatory)][System.Collections.IDictionary]$ExpectedProperties,
        [Parameter(Mandatory)][System.Collections.IDictionary]$Policy,
        [switch]$IgnoreEmbeddedPermissions
    )
    if ($Resource -isnot [System.Collections.IDictionary]) { throw (New-ObserveError -Message 'Strict readback changed resource') }
    foreach ($key in @($Resource.Keys)) {
        if (@('name', 'type', 'tags', 'properties', 'kind') -cnotcontains [string]$key) {
            throw (New-ObserveError -Message "Strict readback changed envelope.$(Get-ObserveSafeName ([string]$key))")
        }
    }
    if ($Resource['name'] -cne $Name -or $Resource['type'] -cne $Type) { throw (New-ObserveError -Message 'Strict readback changed identity') }
    if ($Resource.Contains('kind') -and $null -ne $Resource['kind'] -and $Resource['kind'] -isnot [string]) { throw (New-ObserveError -Message 'Strict readback changed envelope.kind') }
    if ($Resource.Contains('tags') -and $null -ne $Resource['tags']) {
        $tags = $Resource['tags']
        if (-not (($tags -is [array] -or $tags -is [System.Collections.IDictionary]) -and $tags.Count -eq 0)) { throw (New-ObserveError -Message 'Strict readback changed envelope.tags') }
    }
    if ($Resource['properties'] -isnot [System.Collections.IDictionary]) { throw (New-ObserveError -Message 'Strict readback changed properties') }
    $properties = [hashtable]::new([System.StringComparer]::Ordinal)
    foreach ($key in $Resource['properties'].Keys) { $properties[[string]$key] = $Resource['properties'][$key] }
    foreach ($meta in 'etag', 'createdAt', 'updatedAt') {
        if ($properties.ContainsKey($meta)) {
            if ($properties[$meta] -isnot [string] -or [string]::IsNullOrWhiteSpace($properties[$meta])) { throw (New-ObserveError -Message "Strict readback changed properties.$meta") }
            $properties.Remove($meta)
        }
    }
    $expectedKeys = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
    foreach ($key in $ExpectedProperties.Keys) { [void]$expectedKeys.Add([string]$key) }
    $defaults = if ($Kind -ceq 'handler') { $script:ObserveHandlerDefaults } else { $script:ObserveFilterDefaults }
    foreach ($key in @($properties.Keys)) {
        if ($expectedKeys.Contains($key)) { continue }
        $value = $properties[$key]
        if ($Kind -ceq 'handler' -and $key -ceq 'hooks') {
            # Assumption: a hook property that is absent, null or empty means "no hook".
            if ($null -eq $value -or (($value -is [array] -or $value -is [System.Collections.IDictionary]) -and $value.Count -eq 0)) { [void]$properties.Remove($key); continue }
        }
        elseif ($Kind -ceq 'handler' -and $key -ceq 'permissions') {
            # The policy is a separate resource, compared on its own. An embedded copy is enforced strictly unless the
            # caller is about to (re)write that resource; its shape is checked either way.
            if ($null -ne $value -and $value -isnot [System.Collections.IDictionary]) { throw (New-ObserveError -Message 'Strict readback changed properties.permissions') }
            if ($null -ne $value -and -not $IgnoreEmbeddedPermissions) {
                try { Assert-AsreExact -Expected $Policy -Actual (ConvertTo-ObservePolicy -Value $value) -Path 'permissions' }
                catch { throw (New-ObserveError -Message 'Strict readback changed properties.permissions') }
            }
            [void]$properties.Remove($key)
            continue
        }
        elseif ($defaults.ContainsKey($key) -and (Test-ObserveDefaultValue -Default $defaults[$key] -Value $value)) { [void]$properties.Remove($key); continue }
        throw (New-ObserveError -Message "Strict readback changed properties.$(Get-ObserveSafeName $key)")
    }
    return $properties
}

function Test-ObserveResource {
    # Returns @{ equal; reason }; the reason is a fixed text or a property path, never a value.
    param(
        [Parameter(Mandatory)][ValidateSet('handler', 'filter', IgnoreCase = $false)][string]$Kind,
        $Actual, [Parameter(Mandatory)][string]$Name, [Parameter(Mandatory)][string]$Type,
        [Parameter(Mandatory)][System.Collections.IDictionary]$ExpectedProperties,
        [Parameter(Mandatory)][System.Collections.IDictionary]$Policy,
        [switch]$IgnoreEmbeddedPermissions
    )
    try {
        $actualProperties = ConvertTo-ObserveComparableProperties -Kind $Kind -Resource $Actual -Name $Name -Type $Type -ExpectedProperties $ExpectedProperties -Policy $Policy -IgnoreEmbeddedPermissions:$IgnoreEmbeddedPermissions
        Assert-AsreExact -Expected $ExpectedProperties -Actual $actualProperties -Path 'properties'
        return @{ equal = $true; reason = $null }
    }
    catch {
        $message = $_.Exception.Message
        $safe = $_.Exception.Data.Contains('AsreObserveSafe') -or [regex]::IsMatch($message, '\AStrict readback (changed|omitted) [A-Za-z0-9._\[\]]{1,200}\z')
        return @{ equal = $false; reason = $(if ($safe) { $message } else { 'unreadable' }) }
    }
}

# ----------------------------------------------------------------------------------------------------------------
# Shared read-only assertions (P-8)
# ----------------------------------------------------------------------------------------------------------------
function Assert-ObserveAgent {
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Run)
    $response = Invoke-ObserveRequest -Run $Run -Step 'agent-read' -Service Arm -Path "$($Run['AgentId'])?api-version=2026-01-01"
    $id = Get-AsreMember -Value $response['data'] -Path @('id')
    if ($id -isnot [string] -or -not [string]::Equals($id, $Run['AgentId'], [System.StringComparison]::OrdinalIgnoreCase)) {
        throw (New-ObserveError -Message 'Agent check failed: the agent resource is not the one of the deployment context.')
    }
    if ((Get-AsreMember -Value $response['data'] -Path @('properties', 'provisioningState')) -cne 'Succeeded') {
        throw (New-ObserveError -Message 'Agent check failed: provisioningState is not Succeeded.')
    }
    if ((Get-AsreMember -Value $response['data'] -Path @('properties', 'actionConfiguration', 'mode')) -cne 'Review') {
        throw (New-ObserveError -Message 'Agent check failed: the agent is not in Review mode.')
    }
    if ((Get-AsreMember -Value $response['data'] -Path @('properties', 'actionConfiguration', 'accessLevel')) -cne 'Low') {
        throw (New-ObserveError -Message 'Agent check failed: the agent access level is not Low.')
    }
}

function Assert-ObserveGlobalPolicy {
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Run)
    $response = Invoke-ObserveRequest -Run $Run -Step 'global-policy-read' -Service Sre -Path '/api/v2/agent/settings/global'
    if ($response['data'] -isnot [System.Collections.IDictionary]) { throw (New-ObserveError -Message 'Global policy check failed: unreadable settings.') }
    $permissions = Get-AsreMember -Value $response['data'] -Path @('permissions')
    if ($null -eq $permissions) { return }
    if ($permissions -isnot [System.Collections.IDictionary]) { throw (New-ObserveError -Message 'Global policy check failed: unreadable permissions.') }
    foreach ($key in @($permissions.Keys)) {
        $value = $permissions[$key]
        if (@('allow', 'ask', 'deny') -cnotcontains [string]$key -or ($null -ne $value -and ($value -isnot [array] -or $value.Count -gt 0))) {
            throw (New-ObserveError -Message 'Global policy check failed: the global permission policy is not the empty triple.')
        }
    }
}

function Get-ObserveConnectivity {
    # Strict DTO check; returns @{ ready; status } where status is a sanitised token.
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Run)
    $response = Invoke-ObserveRequest -Run $Run -Step 'connectivity-read' -Service Sre -Path '/api/v2/incidentManagement/checkConnectivityDetailed'
    $dto = $response['data']
    if ($dto -isnot [System.Collections.IDictionary] -or -not $dto.Contains('success') -or -not $dto.Contains('incidentType') -or
        $dto['success'] -isnot [bool] -or $dto['incidentType'] -isnot [string]) {
        throw (New-ObserveError -Message 'Connectivity check failed: unsupported connectivity DTO types.')
    }
    $allowed = @('success', 'incidentType', 'errorMessage', 'observedConfigurationRevision', 'observedApiConnectionName', 'observedConnectionStatus')
    foreach ($key in @($dto.Keys)) {
        if ($allowed -cnotcontains [string]$key) { throw (New-ObserveError -Message 'Connectivity check failed: unsupported connectivity DTO field.') }
        if ([string]$key -cne 'success' -and $null -ne $dto[$key] -and $dto[$key] -isnot [string]) { throw (New-ObserveError -Message 'Connectivity check failed: unsupported connectivity DTO type.') }
    }
    $status = if ($dto.Contains('observedConnectionStatus') -and $dto['observedConnectionStatus'] -is [string]) { Get-ObserveSafeName $dto['observedConnectionStatus'] } else { 'unknown' }
    return @{ ready = (($dto['incidentType'] -ceq 'AzMonitor') -and ($dto['success'] -eq $true)); status = $status }
}

function Test-ObserveBindingEqual {
    param($Configuration, [Parameter(Mandatory)][System.Collections.IDictionary]$Binding)
    try { Assert-AsreExact -Expected $Binding -Actual $Configuration -Path 'incidentManagementConfiguration'; return $true }
    catch { return $false }
}

function Assert-ObservePlatform {
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Run)
    $response = Invoke-ObserveRequest -Run $Run -Step 'platform-read' -Service Arm -Path "$($Run['AgentId'])?api-version=$($Run['BindApiVersion'])"
    if ((Get-AsreMember -Value $response['data'] -Path @('properties', 'provisioningState')) -cne 'Succeeded') {
        throw (New-ObserveError -Message 'Platform check failed: provisioningState is not Succeeded; run -Mode Bind first.')
    }
    $configuration = Get-AsreMember -Value $response['data'] -Path @('properties', 'incidentManagementConfiguration')
    if (-not (Test-ObserveBindingEqual -Configuration $configuration -Binding $Run['Binding'])) {
        throw (New-ObserveError -Message 'Platform check failed: the agent is not bound to Azure Monitor with the exact binding; run -Mode Bind first.')
    }
    if (-not (Get-ObserveConnectivity -Run $Run)['ready']) {
        throw (New-ObserveError -Message 'Platform check failed: the Azure Monitor connection is not ready; run -Mode Bind and wait.')
    }
}

function Assert-ObserveEvidenceTool {
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Run)
    $response = Invoke-ObserveRequest -Run $Run -Step 'evidence-tool-read' -Service Sre -Path "/api/v2/extendedAgent/tools/$($script:ObserveToolName)" -AllowNotFound
    $data = $response['data']
    if ($response['status'] -eq 404 -or $data -isnot [System.Collections.IDictionary] -or
        (Get-AsreMember -Value $data -Path @('name')) -cne $script:ObserveToolName -or
        (Get-AsreMember -Value $data -Path @('type')) -cne 'ExtendedAgentTool' -or
        (Get-AsreMember -Value $data -Path @('properties', 'type')) -cne 'PythonFunctionTool') {
        throw (New-ObserveError -Message 'Evidence tool check failed: publish the Common profile first.')
    }
}

function Get-ObserveFilterState {
    # Complete filter list plus a direct read of our filter. state is absent, owned or foreign.
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Run)
    $list = Invoke-ObserveRequest -Run $Run -Step 'filter-list' -Service Sre -Path '/api/v2/incidentManagement/incidentFilters'
    # Read the member directly: Get-AsreMember unrolls arrays on return (an empty list becomes $null, a single item a scalar).
    $listData = $list['data']
    $value = $null
    if ($listData -is [System.Collections.IDictionary] -and $listData.Contains('value')) { $value = $listData['value'] }
    if ($value -isnot [array]) { throw (New-ObserveError -Message 'Filter check failed: the filter list is not a complete value collection.') }
    if ($listData.Contains('nextLink') -and -not [string]::IsNullOrEmpty([string]$listData['nextLink'])) {
        throw (New-ObserveError -Message 'Filter check failed: the filter list is incomplete (paged).')
    }
    $others = [System.Collections.Generic.List[object]]::new()
    $listed = $false
    foreach ($item in $value) {
        $itemName = Get-AsreMember -Value $item -Path @('name')
        if ($itemName -is [string] -and [string]::Equals($itemName, $Run['FilterName'], [System.StringComparison]::OrdinalIgnoreCase)) { $listed = $true; continue }
        $enabled = Get-AsreMember -Value $item -Path @('properties', 'isEnabled')
        $others.Add([ordered]@{ name = (Get-ObserveSafeName $itemName); enabled = $(if ($enabled -is [bool]) { $enabled } else { 'unknown' }) })
    }
    $direct = Invoke-ObserveRequest -Run $Run -Step 'filter-read' -Service Sre -Path "/api/v2/incidentManagement/incidentFilters/$($Run['FilterName'])" -AllowNotFound
    $state = @{ state = 'absent'; data = $null; etag = $null; enabled = $null; others = $others.ToArray() }
    if ($direct['status'] -eq 404) {
        if ($listed) { throw (New-ObserveError -Message 'Filter check failed: the filter list and the direct read disagree; re-read.') }
        return $state
    }
    if (-not $listed) { throw (New-ObserveError -Message 'Filter check failed: the filter list and the direct read disagree; re-read.') }
    $data = $direct['data']
    $owned = ($data -is [System.Collections.IDictionary]) -and
        ((Get-AsreMember -Value $data -Path @('name')) -ceq $Run['FilterName']) -and
        ((Get-AsreMember -Value $data -Path @('properties', 'incidentPlatform')) -ceq 'AzMonitor') -and
        ((Get-AsreMember -Value $data -Path @('properties', 'handlingAgent')) -ceq $Run['HandlerName'])
    $state['state'] = $(if ($owned) { 'owned' } else { 'foreign' })
    $state['data'] = $data
    $state['etag'] = $direct['etag']
    $enabledValue = Get-AsreMember -Value $data -Path @('properties', 'isEnabled')
    $state['enabled'] = $(if ($enabledValue -is [bool]) { $enabledValue } else { $null })
    return $state
}

function Test-ObserveFilterEqual {
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Run, [Parameter(Mandatory)][System.Collections.IDictionary]$State, [Parameter(Mandatory)][bool]$Enabled)
    return (Test-ObserveResource -Kind 'filter' -Actual $State['data'] -Name $Run['FilterName'] -Type 'IncidentFilter' `
            -ExpectedProperties (New-ObserveFilterProperties -Run $Run -Enabled $Enabled) -Policy $Run['Policy'])
}

function Get-ObserveHandlerState {
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Run, [switch]$IgnoreEmbeddedPermissions)
    $response = Invoke-ObserveRequest -Run $Run -Step 'handler-read' -Service Sre -Path "/api/v2/extendedAgent/agents/$($Run['HandlerName'])" -AllowNotFound
    if ($response['status'] -eq 404) { return @{ exists = $false; etag = $null; equal = $false; reason = 'absent' } }
    $comparison = Test-ObserveResource -Kind 'handler' -Actual $response['data'] -Name $Run['HandlerName'] -Type 'ExtendedAgent' `
        -ExpectedProperties $Run['HandlerProperties'] -Policy $Run['Policy'] -IgnoreEmbeddedPermissions:$IgnoreEmbeddedPermissions
    return @{ exists = $true; etag = $response['etag']; equal = $comparison['equal']; reason = $comparison['reason'] }
}

function Get-ObservePolicyState {
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Run)
    $response = Invoke-ObserveRequest -Run $Run -Step 'permissions-read' -Service Sre -Path "/api/v2/extendedAgent/$($Run['HandlerName'])/permissions" -AllowNotFound
    if ($response['status'] -eq 404) { return @{ exists = $false; etag = $null; equal = $false; reason = 'absent' } }
    try {
        Assert-AsreExact -Expected $Run['Policy'] -Actual (ConvertTo-ObservePolicy -Value $response['data']) -Path 'permissions'
        return @{ exists = $true; etag = $response['etag']; equal = $true; reason = $null }
    }
    catch {
        $message = $_.Exception.Message
        $safe = $_.Exception.Data.Contains('AsreObserveSafe') -or [regex]::IsMatch($message, '\AStrict readback (changed|omitted) [A-Za-z0-9._\[\]]{1,200}\z')
        return @{ exists = $true; etag = $response['etag']; equal = $false; reason = $(if ($safe) { $message } else { 'unreadable' }) }
    }
}

function Invoke-ObserveVerifyCore {
    # Read-only: P-8 (a) to (e) plus strict equality of handler, permissions and filter. Returns the filter state.
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Run, [Parameter(Mandatory)][ValidateSet('Any', 'Disabled', 'Enabled', IgnoreCase = $false)][string]$ExpectedState)
    Assert-ObserveAgent -Run $Run
    Assert-ObserveGlobalPolicy -Run $Run
    Assert-ObservePlatform -Run $Run
    Assert-ObserveEvidenceTool -Run $Run
    $filters = Get-ObserveFilterState -Run $Run
    $handler = Get-ObserveHandlerState -Run $Run
    if (-not $handler['exists']) { throw (New-ObserveError -Message 'Verify failed: the handler is not published; run -Mode PublishDisabled.') }
    # The permission policy is its own resource: judge it first so a policy drift is reported as such, even when the
    # handler readback embeds a copy of it.
    $policy = Get-ObservePolicyState -Run $Run
    if (-not $policy['exists']) { throw (New-ObserveError -Message 'Verify failed: the permission policy is not published; run -Mode PublishDisabled.') }
    if (-not $policy['equal']) { throw (New-ObserveError -Message "Verify failed: the permission policy differs from the declaration ($($policy['reason']))." ) }
    if (-not $handler['equal']) { throw (New-ObserveError -Message "Verify failed: the handler differs from the declaration ($($handler['reason']))." ) }
    if ($filters['state'] -ceq 'absent') { throw (New-ObserveError -Message 'Verify failed: the incident filter is not published; run -Mode PublishDisabled.') }
    if ($filters['state'] -ceq 'foreign') { throw (New-ObserveError -Message 'Verify failed: an incident filter with the declared name exists but is not owned by this handler.') }
    if ($filters['enabled'] -isnot [bool]) { throw (New-ObserveError -Message 'Verify failed: the incident filter state is not readable.') }
    if (($ExpectedState -ceq 'Disabled' -and $filters['enabled']) -or ($ExpectedState -ceq 'Enabled' -and -not $filters['enabled'])) {
        throw (New-ObserveError -Message "Verify failed: the incident filter is $(if ($filters['enabled']) { 'enabled' } else { 'disabled' }) but $ExpectedState was expected.")
    }
    $comparison = Test-ObserveFilterEqual -Run $Run -State $filters -Enabled $filters['enabled']
    if (-not $comparison['equal']) { throw (New-ObserveError -Message "Verify failed: the incident filter differs from the declaration ($($comparison['reason']))." ) }
    return $filters
}

# ----------------------------------------------------------------------------------------------------------------
# Write helpers (callers hold the ShouldProcess decision in $Run['Authorised'])
# ----------------------------------------------------------------------------------------------------------------
function Write-ObserveHandler {
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Run, [Parameter(Mandatory)][System.Collections.IDictionary]$State)
    $request = @{ Run = $Run; Step = 'handler-put'; Service = 'Sre'; Method = 'Put'; Path = "/api/v2/extendedAgent/agents/$($Run['HandlerName'])"; Body = (New-ObserveHandlerBody -Run $Run) }
    if ($State['exists']) {
        if ([string]::IsNullOrWhiteSpace($State['etag'])) { throw (New-ObserveError -Message 'The handler has no ETag; re-read before retrying.') }
        $request['IfMatch'] = $State['etag']
    }
    [void](Invoke-ObserveRequest @request)
    # The permission policy is written next, so an embedded copy of it is not judged here; the handler is re-read
    # strictly once the policy is set.
    $readback = Get-ObserveHandlerState -Run $Run -IgnoreEmbeddedPermissions
    if (-not $readback['exists'] -or -not $readback['equal']) { throw (New-ObserveError -Message "The handler readback differs from the declaration ($($readback['reason'])); re-read before retrying.") }
}

function Write-ObservePolicy {
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Run, [Parameter(Mandatory)][System.Collections.IDictionary]$State)
    $request = @{ Run = $Run; Step = 'permissions-put'; Service = 'Sre'; Method = 'Put'; Path = "/api/v2/extendedAgent/$($Run['HandlerName'])/permissions"; Body = $Run['Policy'] }
    if ($State['exists']) {
        if ([string]::IsNullOrWhiteSpace($State['etag'])) { throw (New-ObserveError -Message 'The permission policy has no ETag; failing closed. Re-read before retrying.') }
        $request['IfMatch'] = $State['etag']
    }
    [void](Invoke-ObserveRequest @request)
    $readback = Get-ObservePolicyState -Run $Run
    if (-not $readback['exists'] -or -not $readback['equal']) { throw (New-ObserveError -Message "The permission policy readback differs from the declaration ($($readback['reason'])); re-read before retrying.") }
}

function Write-ObserveFilter {
    # Creates (PUT, no If-Match) when the filter is absent, otherwise PATCHes the full definition with the ETag of a
    # fresh read. Returns the readback state.
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Run, [Parameter(Mandatory)][bool]$Enabled, [Parameter(Mandatory)][bool]$Create)
    $path = "/api/v2/incidentManagement/incidentFilters/$($Run['FilterName'])"
    $body = New-ObserveFilterBody -Run $Run -Enabled $Enabled
    if ($Create) { [void](Invoke-ObserveRequest -Run $Run -Step 'filter-put' -Service Sre -Method Put -Path $path -Body $body) }
    else {
        $fresh = Get-ObserveFilterState -Run $Run
        if ($fresh['state'] -cne 'owned') { throw (New-ObserveError -Message 'The incident filter is no longer owned by this handler; re-read before retrying.') }
        if ([string]::IsNullOrWhiteSpace($fresh['etag'])) { throw (New-ObserveError -Message 'The incident filter has no ETag; re-read before retrying.') }
        [void](Invoke-ObserveRequest -Run $Run -Step 'filter-patch' -Service Sre -Method Patch -Path $path -Body $body -IfMatch $fresh['etag'])
    }
    return (Get-ObserveFilterState -Run $Run)
}

# ----------------------------------------------------------------------------------------------------------------
# Modes
# ----------------------------------------------------------------------------------------------------------------
function New-ObserveResult {
    param([Parameter(Mandatory)][string]$Mode)
    return [ordered]@{
        mode               = $Mode
        status             = $null
        agent              = $null
        platform           = $null
        handler            = $null
        permissions        = $null
        filter             = $null
        filter_enabled     = $null
        other_filters      = @()
        definition_exact   = $null
        instructions_sha256 = $null
        api_version        = $null
    }
}

function Invoke-ObservePublish {
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Run, [Parameter(Mandatory)][System.Collections.IDictionary]$Result)
    Assert-ObserveAgent -Run $Run
    Assert-ObserveGlobalPolicy -Run $Run
    Assert-ObservePlatform -Run $Run
    Assert-ObserveEvidenceTool -Run $Run
    $filters = Get-ObserveFilterState -Run $Run
    $Result['other_filters'] = @($filters['others'])
    if ($filters['state'] -ceq 'foreign') { throw (New-ObserveError -Message 'An incident filter with the declared name exists but is not owned by this handler; it was not touched.') }
    if ($filters['state'] -ceq 'owned' -and $filters['enabled'] -isnot [bool]) { throw (New-ObserveError -Message 'The owned incident filter state is not readable; nothing was written.') }
    if ($filters['state'] -ceq 'owned' -and $filters['enabled']) { throw (New-ObserveError -Message 'The owned incident filter is enabled: pause first (-Mode Pause), then publish again.') }
    $handler = Get-ObserveHandlerState -Run $Run -IgnoreEmbeddedPermissions
    $policy = Get-ObservePolicyState -Run $Run
    $filterEqual = $false
    if ($filters['state'] -ceq 'owned') { $filterEqual = (Test-ObserveFilterEqual -Run $Run -State $filters -Enabled $false)['equal'] }
    $plan = @{
        handler     = $(if (-not $handler['exists']) { 'would-create' } elseif ($handler['equal']) { 'unchanged' } else { 'would-update' })
        permissions = $(if (-not $policy['exists']) { 'would-create' } elseif ($policy['equal']) { 'unchanged' } else { 'would-update' })
        filter      = $(if ($filters['state'] -ceq 'absent') { 'would-create' } elseif ($filterEqual) { 'unchanged' } else { 'would-update' })
    }
    $Result['filter_enabled'] = $(if ($filters['state'] -ceq 'absent') { 'absent' } else { $filters['enabled'] })
    if (-not $Run['Cmdlet'].ShouldProcess('the observe handler, its permission policy and its disabled incident filter', 'Publish')) {
        $Result['status'] = 'whatif'
        $Result['handler'] = $plan['handler']; $Result['permissions'] = $plan['permissions']; $Result['filter'] = $plan['filter']
        return
    }
    $Run['Authorised'] = $true
    # (1) handler, (2) permissions, (3) handler again, (4) filter - never a retry of a write.
    if ($plan['handler'] -ceq 'unchanged') { $Result['handler'] = 'unchanged' }
    else { Write-ObserveHandler -Run $Run -State $handler; $Result['handler'] = $(if ($handler['exists']) { 'updated' } else { 'created' }) }
    if ($plan['permissions'] -ceq 'unchanged') { $Result['permissions'] = 'unchanged' }
    else { Write-ObservePolicy -Run $Run -State $policy; $Result['permissions'] = $(if ($policy['exists']) { 'updated' } else { 'created' }) }
    $again = Get-ObserveHandlerState -Run $Run
    if (-not $again['exists'] -or -not $again['equal']) { throw (New-ObserveError -Message "The handler differs from the declaration after the permission policy was set ($($again['reason'])); re-read before retrying.") }
    if ($plan['filter'] -ceq 'unchanged') { $Result['filter'] = 'unchanged' }
    else {
        $readback = Write-ObserveFilter -Run $Run -Enabled $false -Create ($filters['state'] -ceq 'absent')
        if ($readback['state'] -cne 'owned' -or $readback['enabled'] -isnot [bool] -or $readback['enabled']) {
            throw (New-ObserveError -Message 'The incident filter readback is not an owned, disabled filter; re-read before retrying.')
        }
        $comparison = Test-ObserveFilterEqual -Run $Run -State $readback -Enabled $false
        if (-not $comparison['equal']) { throw (New-ObserveError -Message "The incident filter readback differs from the declaration ($($comparison['reason'])); re-read before retrying.") }
        $Result['filter'] = $(if ($filters['state'] -ceq 'absent') { 'created' } else { 'updated' })
    }
    $final = Invoke-ObserveVerifyCore -Run $Run -ExpectedState 'Disabled'
    $Result['status'] = 'published-disabled'
    $Result['filter_enabled'] = $final['enabled']
    $Result['other_filters'] = @($final['others'])
}

function Invoke-ObserveVerify {
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Run, [Parameter(Mandatory)][System.Collections.IDictionary]$Result, [Parameter(Mandatory)][string]$ExpectedState)
    $filters = Invoke-ObserveVerifyCore -Run $Run -ExpectedState $ExpectedState
    $Result['status'] = 'verified'
    $Result['handler'] = 'unchanged'; $Result['permissions'] = 'unchanged'; $Result['filter'] = 'unchanged'
    $Result['filter_enabled'] = $filters['enabled']
    $Result['other_filters'] = @($filters['others'])
}

function Invoke-ObserveEnable {
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Run, [Parameter(Mandatory)][System.Collections.IDictionary]$Result)
    $filters = Invoke-ObserveVerifyCore -Run $Run -ExpectedState 'Disabled'
    $Result['other_filters'] = @($filters['others'])
    # Anything other than a readable "disabled" blocks: enabled or unreadable foreign filters are never paused here.
    $blocking = @($filters['others'] | Where-Object { -not (($_['enabled'] -is [bool]) -and -not $_['enabled']) })
    if ($blocking.Count -gt 0) {
        throw (New-ObserveError -Message "Enable refused: other incident filters are enabled or unreadable ($((@($blocking | ForEach-Object { $_['name'] }) -join ', '))). Pause them yourself; this script never edits a foreign filter. Nothing was written.")
    }
    $Result['handler'] = 'unchanged'; $Result['permissions'] = 'unchanged'; $Result['filter_enabled'] = $false
    if (-not $Run['Cmdlet'].ShouldProcess('the observe incident filter', 'Enable')) { $Result['status'] = 'whatif'; $Result['filter'] = 'would-update'; return }
    $Run['Authorised'] = $true
    $fresh = Get-ObserveFilterState -Run $Run
    if ($fresh['state'] -cne 'owned' -or $fresh['enabled'] -isnot [bool] -or $fresh['enabled']) { throw (New-ObserveError -Message 'Enable refused: the incident filter is not an owned, disabled filter any more; nothing was written.') }
    if ([string]::IsNullOrWhiteSpace($fresh['etag'])) { throw (New-ObserveError -Message 'Enable refused: the incident filter has no ETag; nothing was written.') }
    $path = "/api/v2/incidentManagement/incidentFilters/$($Run['FilterName'])"
    $sent = $false
    try {
        $sent = $true
        [void](Invoke-ObserveRequest -Run $Run -Step 'filter-patch' -Service Sre -Method Patch -Path $path -Body (New-ObserveFilterBody -Run $Run -Enabled $true) -IfMatch $fresh['etag'])
        $readback = Get-ObserveFilterState -Run $Run
        if ($readback['state'] -cne 'owned' -or $readback['enabled'] -isnot [bool] -or -not $readback['enabled']) { throw (New-ObserveError -Message 'The enabled filter was not read back as enabled.') }
        $comparison = Test-ObserveFilterEqual -Run $Run -State $readback -Enabled $true
        if (-not $comparison['equal']) { throw (New-ObserveError -Message 'The enabled filter differs from the declaration.') }
    }
    catch {
        if (-not $sent) { throw }
        $paused = Restore-ObservePause -Run $Run
        if ($paused) { throw (New-ObserveError -Message 'Enable failed or its outcome is uncertain; the filter was paused and verified.') }
        throw (New-ObserveError -Message 'ENABLE FAILED AND THE PAUSE COULD NOT BE VERIFIED: pause the filter in the portal now.')
    }
    $Result['status'] = 'enabled'
    $Result['filter'] = 'updated'
    $Result['filter_enabled'] = $true
}

function Restore-ObservePause {
    # Compensation after an Enable that was sent: fresh read, PATCH isEnabled=false when needed, verified readback.
    # Returns $true only when the filter is verified as not enabled.
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Run)
    try {
        $state = Get-ObserveFilterState -Run $Run
        if ($state['state'] -ceq 'absent') { return $true }
        if ($state['state'] -cne 'owned') { return $false }
        if ($state['enabled'] -is [bool] -and -not $state['enabled']) { return $true }
        if ([string]::IsNullOrWhiteSpace($state['etag'])) { return $false }
        $path = "/api/v2/incidentManagement/incidentFilters/$($Run['FilterName'])"
        [void](Invoke-ObserveRequest -Run $Run -Step 'filter-compensate' -Service Sre -Method Patch -Path $path -Body (New-ObserveFilterBody -Run $Run -Enabled $false) -IfMatch $state['etag'])
        $readback = Get-ObserveFilterState -Run $Run
        return ($readback['state'] -ceq 'owned' -and $readback['enabled'] -is [bool] -and -not $readback['enabled'])
    }
    catch { return $false }
}

function Invoke-ObservePause {
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Run, [Parameter(Mandatory)][System.Collections.IDictionary]$Result)
    $state = Get-ObserveFilterState -Run $Run
    $Result['other_filters'] = @($state['others'])
    if ($state['state'] -ceq 'absent') { $Result['status'] = 'absent'; $Result['filter'] = 'absent'; $Result['filter_enabled'] = 'absent'; return }
    if ($state['state'] -ceq 'foreign') { throw (New-ObserveError -Message 'Pause refused: an incident filter with the declared name exists but is not owned by this handler; it was not touched.') }
    if ($state['enabled'] -isnot [bool]) { throw (New-ObserveError -Message 'Pause refused: the incident filter state is not readable; pause it in the portal.') }
    if (-not $state['enabled']) {
        $Result['status'] = 'paused'; $Result['filter'] = 'unchanged'; $Result['filter_enabled'] = $false
        $Result['definition_exact'] = (Test-ObserveFilterEqual -Run $Run -State $state -Enabled $false)['equal']
        return
    }
    $Result['filter_enabled'] = $true
    if (-not $Run['Cmdlet'].ShouldProcess('the observe incident filter', 'Pause')) { $Result['status'] = 'whatif'; $Result['filter'] = 'would-update'; return }
    $Run['Authorised'] = $true
    $readback = $null
    try { $readback = Write-ObserveFilter -Run $Run -Enabled $false -Create $false }
    catch { throw (New-ObserveError -Message "$($_.Exception.Message) If the filter stays enabled, pause it in the portal now.") }
    if ($readback['state'] -cne 'owned' -or $readback['enabled'] -isnot [bool] -or $readback['enabled']) {
        throw (New-ObserveError -Message 'The pause could not be verified: pause the filter in the portal now.')
    }
    $Result['status'] = 'paused'; $Result['filter'] = 'updated'; $Result['filter_enabled'] = $false
    $Result['definition_exact'] = (Test-ObserveFilterEqual -Run $Run -State $readback -Enabled $false)['equal']
    $Result['other_filters'] = @($readback['others'])
}

function Get-ObservePlatformKind {
    # unbound, bound (strictly the payload) or foreign; foreign details are names only.
    param($Configuration, [Parameter(Mandatory)][System.Collections.IDictionary]$Binding)
    if ($null -eq $Configuration -or ($Configuration -is [string] -and $Configuration.Length -eq 0)) { return @{ kind = 'unbound'; type = $null; keys = @() } }
    if ($Configuration -isnot [System.Collections.IDictionary]) { return @{ kind = 'foreign'; type = '<unprintable>'; keys = @() } }
    if ($Configuration.Count -eq 0) { return @{ kind = 'unbound'; type = $null; keys = @() } }
    if (Test-ObserveBindingEqual -Configuration $Configuration -Binding $Binding) { return @{ kind = 'bound'; type = [string]$Binding['type']; keys = @() } }
    $type = Get-AsreMember -Value $Configuration -Path @('type')
    $keys = @($Configuration.Keys | ForEach-Object { Get-ObserveSafeName ([string]$_) } | Sort-Object)
    $emptyType = $null -eq $type -or ($type -is [string] -and ($type -ceq '' -or $type -ceq 'None'))
    if ($emptyType) {
        $rest = @($Configuration.Keys | Where-Object { [string]$_ -cne 'type' -and -not ($null -eq $Configuration[$_] -or ($Configuration[$_] -is [string] -and $Configuration[$_].Length -eq 0)) })
        if ($rest.Count -eq 0) { return @{ kind = 'unbound'; type = $null; keys = $keys } }
    }
    return @{ kind = 'foreign'; type = (Get-ObserveSafeName $type); keys = $keys }
}

function Invoke-ObserveBind {
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Run, [Parameter(Mandatory)][System.Collections.IDictionary]$Result)
    Assert-ObserveAgent -Run $Run
    $path = "$($Run['AgentId'])?api-version=$($Run['BindApiVersion'])"
    $response = Invoke-ObserveRequest -Run $Run -Step 'platform-read' -Service Arm -Path $path
    $platform = Get-ObservePlatformKind -Configuration (Get-AsreMember -Value $response['data'] -Path @('properties', 'incidentManagementConfiguration')) -Binding $Run['Binding']
    if ($platform['kind'] -ceq 'foreign') {
        throw (New-ObserveError -Message "Bind refused: the agent already has another incident platform configuration (type: $($platform['type']); keys: $(@($platform['keys']) -join ', ')); it was not changed.")
    }
    $Result['api_version'] = $Run['BindApiVersion']
    $Result['platform'] = $(if ($platform['kind'] -ceq 'unbound') { 'would-bind' } else { 'unchanged' })
    if (-not $Run['Cmdlet'].ShouldProcess('the incident platform binding of the agent', 'Bind to Azure Monitor')) { $Result['status'] = 'whatif'; return }
    if ($platform['kind'] -ceq 'unbound') {
        $Run['Authorised'] = $true
        [void](Invoke-ObserveRequest -Run $Run -Step 'platform-bind' -Service Arm -Method Patch -Path $path -Body @{ properties = @{ incidentManagementConfiguration = $Run['Binding'] } })
        $Result['platform'] = 'bound'
    }
    $attempts = [int][math]::Ceiling($Run['TimeoutSeconds'] / 5)
    $lastStatus = 'unknown'
    $ready = $false
    for ($attempt = 1; $attempt -le $attempts; $attempt++) {
        $arm = Invoke-ObserveRequest -Run $Run -Step 'platform-read' -Service Arm -Path $path
        $provisioning = Get-AsreMember -Value $arm['data'] -Path @('properties', 'provisioningState')
        if ($provisioning -cne 'Succeeded' -and $provisioning -cne 'InProgress') { throw (New-ObserveError -Message 'Bind failed: the agent reported an unexpected provisioning state.') }
        $connectivity = Get-ObserveConnectivity -Run $Run
        $lastStatus = $connectivity['status']
        $configuration = Get-AsreMember -Value $arm['data'] -Path @('properties', 'incidentManagementConfiguration')
        if ($provisioning -ceq 'Succeeded' -and $connectivity['ready'] -and (Test-ObserveBindingEqual -Configuration $configuration -Binding $Run['Binding'])) { $ready = $true; break }
        if ($attempt -lt $attempts) { Start-Sleep -Seconds 5 }
    }
    if (-not $ready) {
        throw (New-ObserveError -Message "Bind did not become ready within the timeout (last connection status: $lastStatus); run -Mode Bind again to keep waiting.")
    }
    $filters = Get-ObserveFilterState -Run $Run
    $Result['status'] = 'bound'
    $Result['other_filters'] = @($filters['others'])
}

function Invoke-ObserveMain {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]$Cmdlet,
        [Parameter(Mandatory)][string]$Mode,
        [string]$ContextPath,
        [string]$AssetRoot,
        [string]$ExpectedState = 'Any',
        [string]$ConfirmAgentName,
        [int]$TimeoutSeconds = 300,
        [string]$BindApiVersion = '2025-05-01-preview',
        [bool]$PreviewOnly = $false
    )
    try {
        $result = New-ObserveResult -Mode $Mode
        $root = if ([string]::IsNullOrWhiteSpace($AssetRoot)) { $script:ObserveRoot } else { [System.IO.Path]::GetFullPath($AssetRoot) }
        $assets = Get-ObserveAssets -Root $root
        if (-not [string]::Equals($root.TrimEnd('\', '/'), $script:ObserveRoot.TrimEnd('\', '/'), [System.StringComparison]::Ordinal)) {
            Assert-ObserveAssetRootIdentity -Root $root
        }
        $result['instructions_sha256'] = $assets['instructions_sha256']
        if ($Mode -ceq 'Validate') { $result['status'] = 'validated'; return $result }

        try {
            if ([string]::IsNullOrWhiteSpace($ContextPath)) { $ctx = Get-AsreContext } else { $ctx = Get-AsreContext -Path $ContextPath }
            [void](Get-AsreContextValue -Context $ctx -Path 'log_analytics.resource_id')
            [void](Get-AsreContextValue -Context $ctx -Path 'agent.resource_id')
            [void](Get-AsreContextValue -Context $ctx -Path 'agent.name')
        }
        catch { throw (ConvertTo-ObserveFailure -Step 'context' -ErrorRecord $_) }
        $result['agent'] = $ctx['agent']['name']
        if ($Mode -ceq 'Bind' -or $Mode -ceq 'Enable') {
            if ([string]::IsNullOrEmpty($ConfirmAgentName) -or $ConfirmAgentName -cne $ctx['agent']['name']) {
                throw (New-ObserveError -Message 'The confirmed agent name is missing or is not the agent name of the deployment context (case-sensitive); nothing was sent.')
            }
        }
        $plane = $null
        try {
            try { $plane = Get-AsreAgentPlane -Context $ctx -ReadOnly:($Mode -ceq 'Verify' -or $PreviewOnly) }
            catch { throw (ConvertTo-ObserveFailure -Step 'sign-in' -ErrorRecord $_) }
            $declaration = $assets['declaration']
            $run = @{
                Ctx = $ctx; Plane = $plane; Assets = $assets; Cmdlet = $Cmdlet; Authorised = $false
                AgentId = [string]$ctx['agent']['resource_id']
                HandlerName = [string]$declaration['handler']['name']
                FilterName = [string]$declaration['response_plan']['name']
                HandlerProperties = (New-ObserveHandlerProperties -Assets $assets)
                Policy = (New-ObservePolicy -Assets $assets)
                Binding = (New-ObserveBinding -Assets $assets)
                BindApiVersion = $BindApiVersion
                TimeoutSeconds = $TimeoutSeconds
            }
            switch -CaseSensitive ($Mode) {
                'Bind' { Invoke-ObserveBind -Run $run -Result $result }
                'PublishDisabled' { Invoke-ObservePublish -Run $run -Result $result }
                'Verify' { Invoke-ObserveVerify -Run $run -Result $result -ExpectedState $ExpectedState }
                'Enable' { Invoke-ObserveEnable -Run $run -Result $result }
                'Pause' { Invoke-ObservePause -Run $run -Result $result }
            }
            if ($Mode -cne 'Bind' -and $null -eq $result['api_version']) { $result['api_version'] = $BindApiVersion }
        }
        finally { Clear-AsreAgentPlane -Plane $plane }
        return $result
    }
    catch { throw (ConvertTo-ObserveFailure -Step 'run' -ErrorRecord $_) }
}

if ($DefinitionsOnly) { return }

if ($PSBoundParameters.ContainsKey('ExpectedState') -and $Mode -cne 'Verify') { throw 'ExpectedState applies to -Mode Verify only.' }
Invoke-ObserveMain -Cmdlet $PSCmdlet -Mode $Mode -ContextPath $ContextPath -AssetRoot $AssetRoot -ExpectedState $ExpectedState `
    -ConfirmAgentName $ConfirmAgentName -TimeoutSeconds $ReadinessTimeoutSeconds -BindApiVersion $BindApiVersion `
    -PreviewOnly ([bool]($WhatIfPreference))
