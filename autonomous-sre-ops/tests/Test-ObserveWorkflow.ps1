$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repo = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot 'TestContext.Common.ps1')
$script:assertions = 0
function Assert-Observe {
    param([bool]$Condition, [string]$Message)
    $script:assertions++
    if (-not $Condition) { throw "Observe workflow test failed: $Message" }
}

$publish = Join-Path $repo 'scripts\Publish-ObserveWorkflow.ps1'
$declFile = Join-Path $repo 'agent\workflows\azure-monitor-observe.json'
$instrFile = Join-Path $repo 'agent\instructions\azure-monitor-observe.md'
$fixtures = Join-Path $PSScriptRoot 'fixtures'
$tempDir = Join-Path ([System.IO.Path]::GetTempPath()) ('observe-workflow-test-' + [guid]::NewGuid().ToString('N'))
[void][System.IO.Directory]::CreateDirectory($tempDir)

# State shared with the shadows lives in one global container (a shadow runs in the scope of its caller).
$global:ObsTest = @{
    Sim         = $null
    Calls       = [System.Collections.Generic.List[object]]::new()
    Sleeps      = [System.Collections.Generic.List[int]]::new()
    Az          = [System.Collections.Generic.List[string]]::new()
    Unexpected  = [System.Collections.Generic.List[string]]::new()
    AllCalls    = [System.Collections.Generic.List[object]]::new()
    AllText     = [System.Collections.Generic.List[string]]::new()
    VerboseText = [System.Collections.Generic.List[string]]::new()
    Token       = 'faketoken-' + [guid]::NewGuid().ToString('N')
    Marker      = 'responsebody-' + [guid]::NewGuid().ToString('N')
}

# ---------------------------------------------------------------------------------------------------------------
# Shadows: az, Invoke-WebRequest and Start-Sleep. The real Invoke-AsreAgentPlaneRequest runs on top of them.
# ---------------------------------------------------------------------------------------------------------------
function az {
    $a = @($args | ForEach-Object { [string]$_ })
    $global:LASTEXITCODE = 0
    $global:ObsTest.Az.Add(($a -join ' '))
    $sim = $global:ObsTest.Sim
    if ($a.Count -ge 2 -and $a[0] -ceq 'account' -and $a[1] -ceq 'show') {
        return (@{ id = $sim.Account.id; tenantId = $sim.Account.tenantId; state = 'Enabled' } | ConvertTo-Json -Compress)
    }
    if ($a.Count -ge 2 -and $a[0] -ceq 'account' -and $a[1] -ceq 'get-access-token') { return $global:ObsTest.Token }
    $global:ObsTest.Unexpected.Add('az call')
    $global:LASTEXITCODE = 1
    throw 'Unexpected az call.'
}

function Start-Sleep {
    param([int]$Seconds = 0, [int]$Milliseconds = 0)
    $global:ObsTest.Sleeps.Add($Seconds)
}

function Copy-ObsJson {
    param($Value)
    $copy = ConvertTo-Json -InputObject $Value -Depth 40 | ConvertFrom-Json -AsHashtable -NoEnumerate -DateKind String
    # The unary comma keeps an empty or single-item array intact; a plain return would enumerate it.
    return , $copy
}

function New-ObsResponse {
    param([int]$Status = 200, $Data = $null, [string]$Etag)
    $headers = @{}
    if ($Etag) { $headers['ETag'] = @($Etag) }
    $content = if ($null -ne $Data) { ConvertTo-Json -InputObject $Data -Depth 40 -Compress } else { '' }
    return [pscustomobject]@{ StatusCode = $Status; Content = $content; Headers = $headers }
}

function New-ObsError {
    param([int]$Status, [string]$Code = 'SimulatedError')
    return (New-ObsResponse -Status $Status -Data @{ error = @{ code = $Code; message = $global:ObsTest.Marker } })
}

function Get-ObsEtag {
    $sim = $global:ObsTest.Sim
    $sim['Counter'] = [int]$sim['Counter'] + 1
    return ('"v{0}"' -f $sim['Counter'])
}

$global:ObsTest.HandlerDefaults = [ordered]@{
    connectors = $null; allowParallelToolCalls = $null; agentsAsTools = $null; maxReflectionCount = $null
    criticPromptPath = $null; criticOnHandOff = $null; customReflectionNote = $null; commonPrompts = $null
    disableDocumentRetrieval = $null; enableHandoffPromptOverride = $null; userPromptOverride = $null
    handoffPromptOverride = $null; instructionsOverride = $null; temperature = $null; llmModelName = $null
    outputType = $null; enableVanillaMode = $false; hooks = @()
}
$global:ObsTest.FilterDefaults = [ordered]@{
    name = ''; impactedService = ''; state = ''; category = ''; subcategory = ''; configurationItem = ''; callerId = ''
    location = ''; impact = ''; urgency = ''; severity = ''; incidentType = ''; alertId = ''; owningTeamId = ''
    assignmentGroups = @(); customFields = @(); incidentTypes = @(); titleContainsAll = @(); titleContainsAny = @()
    titleNotContains = @(); owningTeamIds = @(); handlingAgents = $null; icmFilterSettings = $null; configurationWarning = $null
}
# What the live service reads back in addition to the three sent keys of the platform binding.
$global:ObsTest.IncidentDefaults = [ordered]@{ apiConnectionName = $null; connectionKey = ''; connectionUrl = $null }

function New-ObsIncident {
    # The bound platform as the service reads it back: what was sent plus its own default fields.
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Sent)
    $incident = [ordered]@{}
    foreach ($key in $Sent.Keys) { $incident[[string]$key] = $Sent[$key] }
    foreach ($key in $global:ObsTest.IncidentDefaults.Keys) { if (-not $incident.Contains($key)) { $incident[$key] = $global:ObsTest.IncidentDefaults[$key] } }
    return $incident
}

function New-ObsResource {
    # The service echoes what it was sent plus its own defaults and metadata.
    param([string]$Kind, $Body)
    $defaults = if ($Kind -ceq 'handler') { $global:ObsTest.HandlerDefaults } else { $global:ObsTest.FilterDefaults }
    $properties = [ordered]@{}
    foreach ($key in $Body['properties'].Keys) { $properties[[string]$key] = $Body['properties'][$key] }
    foreach ($key in $defaults.Keys) { if (-not $properties.Contains($key)) { $properties[$key] = Copy-ObsJson $defaults[$key] } }
    $properties['etag'] = 'meta-etag'
    $properties['createdAt'] = '2026-01-01T00:00:00Z'
    $properties['updatedAt'] = '2026-01-02T00:00:00Z'
    return [ordered]@{ name = $Body['name']; type = $Body['type']; kind = $(if ($Kind -ceq 'handler') { 'agents' } else { 'incidentFilters' }); tags = @(); properties = $properties }
}

function Invoke-ObsRoute {
    param($Call)
    $t = $global:ObsTest
    $sim = $t.Sim
    $m = $Call.Method
    $p = $Call.Path
    $rule = $Call.Rule
    if ($Call.Host -ceq 'management.azure.com' -and $p.Equals($sim.AgentId, [System.StringComparison]::OrdinalIgnoreCase)) {
        if ($m -ceq 'GET') {
            $state = $sim.Provisioning
            if ([int]$sim.Pending -gt 0) { $sim.Pending = [int]$sim.Pending - 1; $state = 'InProgress' }
            $reported = if ($sim.ReportedId) { $sim.ReportedId } else { $sim.AgentId }
            return (New-ObsResponse -Data ([ordered]@{
                        id = $reported; name = 'agent'; type = 'Microsoft.App/agents'
                        properties = [ordered]@{
                            provisioningState = $state; agentEndpoint = $sim.Endpoint
                            actionConfiguration = [ordered]@{ mode = $sim.Mode; accessLevel = $sim.Access }
                            incidentManagementConfiguration = $sim.Incident
                        }
                    }))
        }
        if ($m -ceq 'PATCH') {
            $sim.Incident = New-ObsIncident -Sent $Call.Body['properties']['incidentManagementConfiguration']
            $sim.Pending = [int]$sim.PendingAfterBind
            if ($sim.StayInProgress) { $sim.Provisioning = 'InProgress' }
            return (New-ObsResponse -Data @{ id = $sim.AgentId })
        }
    }
    elseif ($Call.Host -ceq $sim.EndpointHost) {
        if ($m -ceq 'GET' -and $p -ceq '/api/v2/agent/settings/global') { return (New-ObsResponse -Data @{ name = 'global'; permissions = (Copy-ObsJson $sim.GlobalPermissions) }) }
        if ($m -ceq 'GET' -and $p -ceq '/api/v2/incidentManagement/checkConnectivityDetailed') {
            $dto = Copy-ObsJson $sim.Connectivity
            if ([int]$sim.ConnectivityFalseFor -gt 0) { $sim.ConnectivityFalseFor = [int]$sim.ConnectivityFalseFor - 1; $dto['success'] = $false }
            return (New-ObsResponse -Data $dto)
        }
        if ($m -ceq 'GET' -and $p -ceq "/api/v2/extendedAgent/tools/asre-review-evidence") {
            if ($null -eq $sim.Tool) { return (New-ObsError -Status 404 -Code 'NotFound') }
            return (New-ObsResponse -Data $sim.Tool)
        }
        if ($p -ceq "/api/v2/extendedAgent/agents/$($sim.HandlerName)") {
            if ($m -ceq 'GET') {
                if ($null -eq $sim.Handler) { return (New-ObsError -Status 404 -Code 'NotFound') }
                $r = Copy-ObsJson $sim.Handler.resource
                $r['properties']['permissions'] = $(if ($null -ne $sim.Policy) { Copy-ObsJson $sim.Policy.body } else { $null })
                return (New-ObsResponse -Data $r -Etag $sim.Handler.etag)
            }
            if ($m -ceq 'PUT') {
                if ($null -ne $sim.Handler -and $Call.IfMatch -cne $sim.Handler.etag) { return (New-ObsError -Status 412 -Code 'PreconditionFailed') }
                $res = New-ObsResource -Kind 'handler' -Body $Call.Body
                $sim.Handler = @{ resource = $res; etag = (Get-ObsEtag) }
                if ($rule -and $rule.After) { & $rule.After $res }
                return (New-ObsResponse -Data $res -Etag $sim.Handler.etag)
            }
        }
        if ($p -ceq "/api/v2/extendedAgent/$($sim.HandlerName)/permissions") {
            if ($m -ceq 'GET') {
                if ($null -eq $sim.Policy) { return (New-ObsError -Status 404 -Code 'NotFound') }
                return (New-ObsResponse -Data (Copy-ObsJson $sim.Policy.body) -Etag $(if ($sim.NoPolicyEtag) { '' } else { $sim.Policy.etag }))
            }
            if ($m -ceq 'PUT') {
                if ($null -ne $sim.Policy -and $Call.IfMatch -cne $sim.Policy.etag) { return (New-ObsError -Status 412 -Code 'PreconditionFailed') }
                $body = [ordered]@{ allow = $Call.Body['allow']; ask = @(); deny = @() }
                $sim.Policy = @{ body = $body; etag = (Get-ObsEtag) }
                if ($rule -and $rule.After) { & $rule.After $body }
                return (New-ObsResponse -Data $body -Etag $sim.Policy.etag)
            }
        }
        if ($m -ceq 'GET' -and $p -ceq '/api/v2/incidentManagement/incidentFilters') {
            $items = @($sim.Filters.Values | ForEach-Object { Copy-ObsJson $_.resource })
            $page = [ordered]@{ value = $items }
            if ($sim.NextLink) { $page['nextLink'] = $sim.NextLink }
            return (New-ObsResponse -Data $page)
        }
        if ($p -ceq "/api/v2/incidentManagement/incidentFilters/$($sim.FilterName)") {
            $current = if ($sim.Filters.Contains($sim.FilterName)) { $sim.Filters[$sim.FilterName] } else { $null }
            if ($m -ceq 'GET') {
                if ($null -eq $current) { return (New-ObsError -Status 404 -Code 'NotFound') }
                return (New-ObsResponse -Data (Copy-ObsJson $current.resource) -Etag $current.etag)
            }
            if ($m -ceq 'PUT') {
                if ($null -ne $current -and $Call.IfMatch -cne $current.etag) { return (New-ObsError -Status 412 -Code 'PreconditionFailed') }
                $res = New-ObsResource -Kind 'filter' -Body $Call.Body
                $sim.Filters[$sim.FilterName] = @{ resource = $res; etag = (Get-ObsEtag) }
                if ($rule -and $rule.After) { & $rule.After $res }
                return (New-ObsResponse -Data $res -Etag $sim.Filters[$sim.FilterName].etag)
            }
            if ($m -ceq 'PATCH') {
                if ($null -eq $current) { return (New-ObsError -Status 404 -Code 'NotFound') }
                if ($Call.IfMatch -cne $current.etag) { return (New-ObsError -Status 412 -Code 'PreconditionFailed') }
                $res = $current.resource
                if ($sim.Sticky) { $res['properties']['isEnabled'] = $Call.Body['properties']['isEnabled'] }
                else { foreach ($key in $Call.Body['properties'].Keys) { $res['properties'][[string]$key] = $Call.Body['properties'][$key] } }
                $current.etag = (Get-ObsEtag)
                if ($rule -and $rule.After) { & $rule.After $res }
                return (New-ObsResponse -Data $res -Etag $current.etag)
            }
        }
    }
    $t.Unexpected.Add("$m $($Call.Host)$p")
    throw 'Unexpected route in the fake service.'
}

function Invoke-WebRequest {
    [CmdletBinding()]
    param([string]$Uri, [string]$Method, $Headers, [int]$MaximumRedirection, [int]$TimeoutSec, [switch]$SkipHttpErrorCheck, $Body, [string]$ContentType)
    $t = $global:ObsTest
    $u = [System.Uri]$Uri
    $hasBody = $PSBoundParameters.ContainsKey('Body') -and -not [string]::IsNullOrEmpty($Body)
    $call = @{
        Method = $Method.ToUpperInvariant(); Host = $u.Host; Path = $u.AbsolutePath; Query = $u.Query
        Raw = $(if ($hasBody) { [string]$Body } else { '' })
        Body = $(if ($hasBody) { $Body | ConvertFrom-Json -AsHashtable -DateKind String } else { $null })
        IfMatch = $(if ($Headers -and $Headers.ContainsKey('If-Match')) { [string]$Headers['If-Match'] } else { $null })
        Auth = $(if ($Headers -and $Headers.ContainsKey('Authorization')) { [string]$Headers['Authorization'] } else { '' })
        Redirects = $MaximumRedirection; Rule = $null
    }
    $t.Calls.Add($call)
    $t.AllCalls.Add($call)
    if (-not $call.Auth.Contains($t.Token)) { return (New-ObsError -Status 401 -Code 'Unauthorized') }
    $sim = $t.Sim
    foreach ($rule in $sim.Rules) {
        if ($rule.Remaining -eq 0 -or $rule.Method -cne $call.Method -or $call.Path -notmatch $rule.Pattern) { continue }
        if ($rule.Skip -gt 0) { $rule.Skip = $rule.Skip - 1; continue }
        if ($rule.Remaining -gt 0) { $rule.Remaining = $rule.Remaining - 1 }
        $call.Rule = $rule
        break
    }
    if ($call.Rule) {
        if ($call.Rule.Transport) { throw 'simulated transport failure' }
        if ($call.Rule.Status -gt 0) { return (New-ObsError -Status $call.Rule.Status -Code $call.Rule.Code) }
    }
    $response = Invoke-ObsRoute -Call $call
    if ($call.Rule -and $call.Rule.ApplyThenThrow) { throw 'simulated lost response' }
    return $response
}

# The definitions of the script under test (functions only, no side effects).
. $publish -DefinitionsOnly

# ---------------------------------------------------------------------------------------------------------------
# Simulation and run helpers
# ---------------------------------------------------------------------------------------------------------------
$decl = Get-Content -LiteralPath $declFile -Raw | ConvertFrom-Json -AsHashtable
$handlerName = [string]$decl['handler']['name']
$filterName = [string]$decl['response_plan']['name']
$expectedInstructions = [System.IO.File]::ReadAllText($instrFile).Replace("`r`n", "`n")
$expectedHash = [System.Convert]::ToHexString([System.Security.Cryptography.SHA256]::HashData([System.Text.Encoding]::UTF8.GetBytes($expectedInstructions))).ToLowerInvariant()

function New-ObsSim {
    param([Parameter(Mandatory)]$Ctx, [switch]$Unbound)
    $endpoint = $Ctx['agent']['endpoint']
    if ($endpoint -isnot [string]) { $endpoint = "https://$($Ctx['agent']['name'])--testrun.localtest.centralus.azuresre.ai" }
    return @{
        Ctx = $Ctx; AgentId = [string]$Ctx['agent']['resource_id']; Endpoint = $endpoint; EndpointHost = ([System.Uri]$endpoint).Host
        Account = @{ id = [string]$Ctx['subscription_id']; tenantId = [string]$Ctx['tenant_id'] }
        Counter = 0; Provisioning = 'Succeeded'; Mode = 'Review'; Access = 'Low'
        Incident = $(if ($Unbound) { $null } else { New-ObsIncident -Sent ([ordered]@{ type = 'AzMonitor'; connectionName = 'azmonitor'; oboUser = '' }) })
        Pending = 0; PendingAfterBind = 0; StayInProgress = $false; ReportedId = $null
        GlobalPermissions = $null
        Connectivity = [ordered]@{ success = $true; incidentType = 'AzMonitor'; errorMessage = $null; observedConfigurationRevision = 'rev-1'; observedApiConnectionName = 'azmonitor'; observedConnectionStatus = 'Connected' }
        ConnectivityFalseFor = 0
        Tool = [ordered]@{ name = 'asre-review-evidence'; type = 'ExtendedAgentTool'; properties = [ordered]@{ type = 'PythonFunctionTool' } }
        Handler = $null; Policy = $null; Filters = [ordered]@{}; NextLink = $null; Sticky = $false; NoPolicyEtag = $false
        Rules = [System.Collections.Generic.List[object]]::new()
        HandlerName = $handlerName; FilterName = $filterName
    }
}

function Add-ObsRule {
    param($Sim, [string]$Method, [string]$Pattern, [int]$Status = 0, [string]$Code = '', [switch]$Transport, [switch]$ApplyThenThrow,
        [int]$Skip = 0, [int]$Remaining = -1, [scriptblock]$After)
    $Sim.Rules.Add(@{ Method = $Method; Pattern = $Pattern; Status = $Status; Code = $Code; Transport = [bool]$Transport
            ApplyThenThrow = [bool]$ApplyThenThrow; Skip = $Skip; Remaining = $Remaining; After = $After })
}

function Add-ObsFilter {
    param($Sim, [string]$Name, $Enabled, [string]$HandlingAgent = 'someone-else', [string]$Platform = 'AzMonitor')
    $Sim.Filters[$Name] = @{ etag = '"foreign"'; resource = [ordered]@{ name = $Name; type = 'IncidentFilter'
            properties = [ordered]@{ incidentPlatform = $Platform; isEnabled = $Enabled; handlingAgent = $HandlingAgent } } }
}

function Get-ObsHandlerProps { param($Sim) return $Sim.Handler['resource']['properties'] }
function Get-ObsFilterProps { param($Sim) return $Sim.Filters[$Sim.FilterName]['resource']['properties'] }

function Invoke-Observe {
    param([Parameter(Mandatory)][hashtable]$Params, [Parameter(Mandatory)]$Sim, [string]$ContextFile, [switch]$WithVerbose)
    $t = $global:ObsTest
    $t.Sim = $Sim
    $t.Calls = [System.Collections.Generic.List[object]]::new()
    $t.Sleeps = [System.Collections.Generic.List[int]]::new()
    $t.Az = [System.Collections.Generic.List[string]]::new()
    $invokeParams = @{}
    foreach ($key in $Params.Keys) { $invokeParams[$key] = $Params[$key] }
    if ($WithVerbose) { $invokeParams['Verbose'] = $true }
    if ($ContextFile) { [void](Use-AsreTestContext -Path $ContextFile) } else { [void](Use-AsreTestContext -Name synthetic) }
    $captured = [System.Collections.Generic.List[object]]::new()
    $failure = $null
    try { & $publish @invokeParams *>&1 | ForEach-Object { $captured.Add($_) } }
    catch { $failure = $_ }
    finally { Clear-AsreTestContext }
    $parts = [System.Collections.Generic.List[string]]::new()
    $result = $null
    foreach ($item in $captured) {
        if ($item -is [System.Collections.IDictionary]) { $result = $item; $parts.Add((ConvertTo-Json -InputObject $item -Depth 6 -Compress)) }
        else { $parts.Add([string]$item) }
    }
    if ($failure) { $parts.Add($failure.Exception.Message); $parts.Add($failure.ToString()) }
    $text = $parts -join "`n"
    if ($WithVerbose) { $t.VerboseText.Add($text) } else { $t.AllText.Add($text) }
    Assert-Observe ($t.Unexpected.Count -eq 0) 'The fake service must never see an unexpected route or az call.'
    return @{
        Ok = ($null -eq $failure); Result = $result; Error = $(if ($failure) { $failure.Exception.Message } else { '' }); Text = $text
        Calls = $t.Calls; Writes = @($t.Calls | Where-Object { $_.Method -cne 'GET' }); Sleeps = $t.Sleeps; Az = $t.Az
    }
}

function Get-ObsKind {
    param($Call)
    $p = $Call.Path
    $what = if ($Call.Host -ceq 'management.azure.com') { 'agent' }
    elseif ($p -match '/extendedAgent/agents/') { 'handler' }
    elseif ($p -match '/permissions\z') { 'permissions' }
    elseif ($p -match '/incidentFilters') { 'filter' }
    else { 'other' }
    return "$($Call.Method) $what"
}
function Get-ObsWriteKinds { param($Run) return ((@($Run.Writes | ForEach-Object { Get-ObsKind $_ })) -join ', ') }

function Assert-ObsOk {
    param($Run, [string]$Label)
    Assert-Observe $Run.Ok "$Label must succeed (error: $($Run.Error))"
}
function Assert-ObsFail {
    param($Run, [string]$Label, [string]$Pattern, [int]$Writes = -1)
    Assert-Observe (-not $Run.Ok) "$Label must fail"
    Assert-Observe ($Run.Error -match $Pattern) "$Label must fail with /$Pattern/ (got: $($Run.Error))"
    if ($Writes -ge 0) { Assert-Observe ($Run.Writes.Count -eq $Writes) "$Label must send $Writes write(s) (sent: $(Get-ObsWriteKinds $Run))" }
}

$synFile = Join-Path $fixtures 'context.synthetic.json'
$altFile = Join-Path $fixtures 'context.alt.json'
$synCtx = Get-AsreTestContext -Path $synFile
$altCtx = Get-AsreTestContext -Path $altFile
$agentName = [string]$synCtx['agent']['name']

function New-BoundSim { return (New-ObsSim -Ctx $synCtx) }
function New-PublishedSim {
    $s = New-BoundSim
    $r = Invoke-Observe -Params @{ Mode = 'PublishDisabled' } -Sim $s
    Assert-ObsOk $r 'baseline publish'
    return $s
}

try {
    # =========================================================================================================
    # Case 1 (P-0, P-6, P-17): capability pins on the script AST
    # =========================================================================================================
    $tokens = $null
    $parseErrors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($publish, [ref]$tokens, [ref]$parseErrors)
    Assert-Observe (@($parseErrors).Count -eq 0) 'The publisher must parse without errors.'
    $commands = @($ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.CommandAst] }, $true))
    $commandNames = @($commands | ForEach-Object { $_.GetCommandName() } | Where-Object { $_ })
    foreach ($banned in 'az', 'Invoke-WebRequest', 'Invoke-RestMethod', 'Invoke-Expression', 'iex', 'Start-Process', 'curl', 'wget', 'Write-Host') {
        Assert-Observe ($commandNames -notcontains $banned) "The publisher must not call $banned."
    }
    $literals = @($ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.StringConstantExpressionAst] }, $true) | ForEach-Object { $_.Value })
    foreach ($verb in 'Delete', 'Post') { Assert-Observe (@($literals | Where-Object { $_ -ieq $verb }).Count -eq 0) "The publisher must have no $verb method literal." }
    $scriptText = [System.IO.File]::ReadAllText($publish)
    Assert-Observe ($scriptText -notmatch '(?i)servicenow|service-now') 'The publisher must not reference the ticketing integration.'
    $dotSources = @($commands | Where-Object { $_.InvocationOperator -eq [System.Management.Automation.Language.TokenKind]::Dot })
    Assert-Observe ($dotSources.Count -eq 1 -and $dotSources[0].Extent.Text -match 'AgentPlane\.Common\.ps1') 'The publisher must dot-source only the agent plane helper.'
    Assert-Observe ($scriptText -match [regex]::Escape('if (-not (Get-Command Invoke-AsreAgentPlaneRequest -ErrorAction SilentlyContinue)) { . (Join-Path $PSScriptRoot ''AgentPlane.Common.ps1'') }')) 'The publisher must use the guard idiom.'
    $parameterNames = @($ast.ParamBlock.Parameters | ForEach-Object { $_.Name.VariablePath.UserPath })
    foreach ($required in 'Mode', 'ContextPath', 'AssetRoot', 'ExpectedState', 'ConfirmAgentName', 'ReadinessTimeoutSeconds', 'BindApiVersion', 'DefinitionsOnly') {
        Assert-Observe ($parameterNames -contains $required) "The publisher must declare -$required."
    }
    $binding = @($ast.ParamBlock.Attributes | Where-Object { $_.TypeName.Name -eq 'CmdletBinding' })[0].Extent.Text
    Assert-Observe ($binding -match 'SupportsShouldProcess' -and $binding -match 'ConfirmImpact') 'The publisher must support ShouldProcess.'
    Assert-Observe ($scriptText -notmatch '\$ctx\.servicenow|\[''servicenow''\]') 'The publisher must not read the ticketing section of the context.'

    # =========================================================================================================
    # Case 2 (P-1, P-5, P-7, P-15): Validate and assets
    # =========================================================================================================
    $assets = Get-ObserveAssets -Root $repo
    Assert-Observe ($assets['declaration']['handler']['name'] -ceq $handlerName) 'Valid assets must return the declaration.'
    Assert-Observe ($assets['instructions'] -ceq $expectedInstructions -and $assets['instructions_sha256'] -ceq $expectedHash) 'Valid assets must return the LF instruction text and its hash.'
    Assert-Observe (-not $assets['instructions'].Contains("`r")) 'The returned instructions must be LF only.'

    $validated = Invoke-Observe -Params @{ Mode = 'Validate' } -Sim (New-BoundSim)
    Assert-ObsOk $validated 'Validate'
    Assert-Observe ($validated.Result['status'] -ceq 'validated' -and $validated.Result['instructions_sha256'] -ceq $expectedHash) 'Validate must report validated and the hash.'
    Assert-Observe ($validated.Calls.Count -eq 0 -and $validated.Az.Count -eq 0) 'Validate must not call az or the network.'

    $pwshExe = (Get-Process -Id $PID).Path
    Push-Location $tempDir
    try { $processOutput = (& $pwshExe -NoProfile -ExecutionPolicy Bypass -File $publish -Mode Validate 2>&1 | Out-String); $processExit = $LASTEXITCODE }
    finally { Pop-Location }
    Assert-Observe ($processExit -eq 0 -and $processOutput -match 'validated') 'A separate pwsh run of -Mode Validate from another directory must exit 0.'

    $treeCounter = 0
    function New-ObsAssetTree {
        param([scriptblock]$MutateDeclaration, [scriptblock]$MutateText, [scriptblock]$MutateInstructions, [switch]$DropDeclaration, [switch]$DropInstructions, [switch]$Crlf)
        $script:treeCounter++
        $root = Join-Path $tempDir ("tree{0}" -f $script:treeCounter)
        [void][System.IO.Directory]::CreateDirectory((Join-Path $root 'agent\workflows'))
        [void][System.IO.Directory]::CreateDirectory((Join-Path $root 'agent\instructions'))
        $declText = [System.IO.File]::ReadAllText($declFile)
        $instrText = [System.IO.File]::ReadAllText($instrFile)
        if ($MutateDeclaration) {
            $d = $declText | ConvertFrom-Json -AsHashtable
            & $MutateDeclaration $d | Out-Null
            $declText = ConvertTo-Json -InputObject $d -Depth 20
        }
        if ($MutateText) { $declText = & $MutateText $declText }
        if ($MutateInstructions) { $instrText = & $MutateInstructions $instrText }
        if ($Crlf) { $declText = $declText.Replace("`r`n", "`n").Replace("`n", "`r`n"); $instrText = $instrText.Replace("`r`n", "`n").Replace("`n", "`r`n") }
        $utf8 = [System.Text.UTF8Encoding]::new($false)
        if (-not $DropDeclaration) { [System.IO.File]::WriteAllText((Join-Path $root 'agent\workflows\azure-monitor-observe.json'), $declText, $utf8) }
        if (-not $DropInstructions) { [System.IO.File]::WriteAllText((Join-Path $root 'agent\instructions\azure-monitor-observe.md'), $instrText, $utf8) }
        return $root
    }
    function Assert-ObsAssetsRejected {
        param([string]$Label, [string]$Root, [string]$Leak = '')
        $message = $null
        try { [void](Get-ObserveAssets -Root $Root) } catch { $message = $_.Exception.Message }
        Assert-Observe ($null -ne $message -and $message -cmatch '\AThe observe handler assets are invalid \(') "Mutant '$Label' must be rejected with the fixed message (got: $message)."
        if ($Leak) { Assert-Observe (-not $message.Contains($Leak)) "The rejection of '$Label' must not echo the offending value." }
    }
    $declarationMutants = [ordered]@{
        'extra tool'          = { param($d) $d['handler']['properties']['tools'] += 'RunAzCliCommand' }
        'missing tool'        = { param($d) $d['handler']['properties']['tools'] = @('ReadFile', 'SearchMemory') }
        'bound case tool'     = { param($d) $d['handler']['properties']['mcpTools'] += 'asre-case-update' }
        'no evidence tool'    = { param($d) $d['handler']['properties']['mcpTools'] = @() }
        'filter enabled'      = { param($d) $d['response_plan']['properties']['isEnabled'] = $true }
        'other agent mode'    = { param($d) $d['response_plan']['properties']['agentMode'] = 'Strict' }
        'other handler'       = { param($d) $d['response_plan']['properties']['handlingAgent'] = 'someone-else' }
        'hook'                = { param($d) $d['handler']['properties']['hooks'] = @{ pre = 'x' } }
        'skills on'           = { param($d) $d['handler']['properties']['enableSkills'] = $true }
        'allowed skill'       = { param($d) $d['handler']['properties']['allowedSkills'] = @('skill') }
        'system skills'       = { param($d) $d['handler']['properties']['addSystemSkills'] = $true }
        'extra top key'       = { param($d) $d['extra'] = 1 }
        'extra handler key'   = { param($d) $d['handler']['properties']['connectors'] = @('x') }
        'missing key'         = { param($d) $d.Remove('platform_binding') }
        'missing permissions' = { param($d) $d.Remove('permissions') }
        'extra permission'    = { param($d) $d['permissions']['allow'] += 'RunShellCommand' }
        'wildcard permission' = { param($d) $d['permissions']['allow'] = @('*') }
        'duplicate permission' = { param($d) $d['permissions']['allow'] = @('ReadFile', 'ReadFile', 'SearchMemory', 'asre-review-evidence') }
        'priorities'          = { param($d) $d['response_plan']['properties']['priorities'] = @('Sev0') }
        'title filter'        = { param($d) $d['response_plan']['properties']['titleContains'] = '' }
        'attempts'            = { param($d) $d['response_plan']['properties']['maxAutomatedInvestigationAttempts'] = 5 }
        'target in file'      = { param($d) $d['response_plan']['properties']['azMonitorFilterSettings']['targetResource'] = 'x' }
        'binding connection'  = { param($d) $d['platform_binding']['connectionName'] = 'other' }
        'binding type'        = { param($d) $d['platform_binding']['type'] = 'ServiceX' }
        'wrong format'        = { param($d) $d['format'] = 'other/v2' }
        'description empty'   = { param($d) $d['description'] = ' ' }
        'guid'                = { param($d) $d['description'] = 'x 11111111-2222-3333-4444-555555555555' }
        'subscription path'   = { param($d) $d['handler']['properties']['handoffDescription'] = 'see /subscriptions/abc' }
        'endpoint'            = { param($d) $d['description'] = 'see https://host.example' }
        'ticketing string'    = { param($d) $d['description'] = ('Service' + 'Now case') }
        'case-tool name'      = { param($d) $d['description'] = 'uses asre-' + ('service' + 'now') + '-case' }
        'autonomous'          = { param($d) $d['description'] = 'Autonomous mode' }
    }
    foreach ($label in $declarationMutants.Keys) {
        Assert-ObsAssetsRejected -Label $label -Root (New-ObsAssetTree -MutateDeclaration $declarationMutants[$label])
    }
    Assert-ObsAssetsRejected -Label 'duplicate key' -Root (New-ObsAssetTree -MutateText { param($text) $text.Replace('{', '{ "format": "asre-observe-workflow/v1",', [System.StringComparison]::Ordinal).Substring(0) -replace '\A\{ "format": "asre-observe-workflow/v1",', '{ "format": "asre-observe-workflow/v1", "format": "asre-observe-workflow/v1",' } )
    Assert-ObsAssetsRejected -Label 'not json' -Root (New-ObsAssetTree -MutateText { param($text) '{ nope' })
    Assert-ObsAssetsRejected -Label 'json array' -Root (New-ObsAssetTree -MutateText { param($text) '[]' })
    Assert-ObsAssetsRejected -Label 'missing declaration' -Root (New-ObsAssetTree -DropDeclaration)
    Assert-ObsAssetsRejected -Label 'missing instructions' -Root (New-ObsAssetTree -DropInstructions)
    Assert-ObsAssetsRejected -Label 'empty instructions' -Root (New-ObsAssetTree -MutateInstructions { param($text) '' })
    Assert-ObsAssetsRejected -Label 'oversized instructions' -Root (New-ObsAssetTree -MutateInstructions { param($text) $text + ('a' * 17000) })
    Assert-ObsAssetsRejected -Label 'instruction guid' -Root (New-ObsAssetTree -MutateInstructions { param($text) $text + "`n11111111-2222-3333-4444-555555555555`n" }) -Leak '11111111-2222'
    Assert-ObsAssetsRejected -Label 'instruction subscription path' -Root (New-ObsAssetTree -MutateInstructions { param($text) $text + "`n/subscriptions/abc`n" })
    Assert-ObsAssetsRejected -Label 'instruction endpoint' -Root (New-ObsAssetTree -MutateInstructions { param($text) $text + "`nhttps://x.example`n" })
    Assert-ObsAssetsRejected -Label 'instruction ticketing' -Root (New-ObsAssetTree -MutateInstructions { param($text) $text + "`nService" + "Now`n" })
    Assert-ObsAssetsRejected -Label 'instruction placeholder' -Root (New-ObsAssetTree -MutateInstructions { param($text) $text + "`n{{value}}`n" })
    Assert-ObsAssetsRejected -Label 'instruction angle placeholder' -Root (New-ObsAssetTree -MutateInstructions { param($text) $text + "`n<resource-id>`n" })
    Assert-ObsAssetsRejected -Label 'instruction heading' -Root (New-ObsAssetTree -MutateInstructions { param($text) $text.Replace('Gaps and missing evidence', 'Gaps') })
    Assert-ObsAssetsRejected -Label 'instruction tool name' -Root (New-ObsAssetTree -MutateInstructions { param($text) $text.Replace('asre-review-evidence', 'the evidence tool') })
    Assert-ObsAssetsRejected -Label 'instruction lone CR' -Root (New-ObsAssetTree -MutateInstructions { param($text) $text + "a`rb`n" })
    Assert-ObsAssetsRejected -Label 'instruction control character' -Root (New-ObsAssetTree -MutateInstructions { param($text) $text + [string][char]7 })
    Assert-ObsAssetsRejected -Label 'instruction BOM' -Root (New-ObsAssetTree -MutateInstructions { param($text) [string][char]0xFEFF + $text })
    Assert-ObsAssetsRejected -Label 'instruction hash 64' -Root (New-ObsAssetTree -MutateInstructions { param($text) $text + "`n" + ('ab' * 32) + "`n" })

    $crlfRoot = New-ObsAssetTree -Crlf
    $crlfAssets = Get-ObserveAssets -Root $crlfRoot
    Assert-Observe ($crlfAssets['instructions_sha256'] -ceq $expectedHash) 'CRLF assets must normalise to the same instruction text and hash.'
    Assert-ObserveAssetRootIdentity -Root $repo
    Assert-ObserveAssetRootIdentity -Root $crlfRoot
    Assert-Observe $true 'The default root and a CRLF copy are identical.'
    $modifiedRoot = New-ObsAssetTree -MutateDeclaration { param($d) $d['description'] = 'A different but identity-free description of the observe handler.' }
    Assert-Observe (@(Get-ObserveAssets -Root $modifiedRoot).Count -eq 1) 'A harmless text change still passes content validation.'
    $identityFailed = $false
    try { Assert-ObserveAssetRootIdentity -Root $modifiedRoot } catch { $identityFailed = $_.Exception.Message -cmatch 'differs from the subproject assets' }
    Assert-Observe $identityFailed 'A modified copy must fail the identity check.'
    $modifiedInstructions = New-ObsAssetTree -MutateInstructions { param($text) $text + "`nOne more sentence.`n" }
    $identityFailed = $false
    try { Assert-ObserveAssetRootIdentity -Root $modifiedInstructions } catch { $identityFailed = $true }
    Assert-Observe $identityFailed 'A modified instruction copy must fail the identity check.'
    $viaRoot = Invoke-Observe -Params @{ Mode = 'Validate'; AssetRoot = $crlfRoot } -Sim (New-BoundSim)
    Assert-ObsOk $viaRoot 'Validate with an identical asset root'
    $viaModified = Invoke-Observe -Params @{ Mode = 'Validate'; AssetRoot = $modifiedRoot } -Sim (New-BoundSim)
    Assert-ObsFail $viaModified 'Validate with a modified asset root' 'differs from the subproject assets'
    $viaInvalid = Invoke-Observe -Params @{ Mode = 'Validate'; AssetRoot = (New-ObsAssetTree -MutateDeclaration $declarationMutants['extra tool']) } -Sim (New-BoundSim)
    Assert-ObsFail $viaInvalid 'Validate with an invalid asset root' 'assets are invalid'

    # =========================================================================================================
    # Case 3 (P-0, P-1): synthetic, alternative and minimal contexts complete Bind, PublishDisabled and Verify
    # =========================================================================================================
    function Invoke-ObsCycle {
        param([Parameter(Mandatory)][string]$ContextFile, [string]$ExplicitPath)
        $ctx = Get-AsreTestContext -Path $ContextFile
        $sim = New-ObsSim -Ctx $ctx -Unbound
        $extra = @{}
        if ($ExplicitPath) { $extra['ContextPath'] = $ExplicitPath }
        $confirm = [string]$ctx['agent']['name']
        $bind = Invoke-Observe -Params ($extra + @{ Mode = 'Bind'; ConfirmAgentName = $confirm }) -Sim $sim -ContextFile $ContextFile
        Assert-ObsOk $bind "Bind under $ContextFile"
        Assert-Observe ($bind.Result['status'] -ceq 'bound' -and $bind.Result['agent'] -ceq $confirm) 'Bind must report bound.'
        $pub = Invoke-Observe -Params ($extra + @{ Mode = 'PublishDisabled' }) -Sim $sim -ContextFile $ContextFile
        Assert-ObsOk $pub "PublishDisabled under $ContextFile"
        Assert-Observe ($pub.Result['status'] -ceq 'published-disabled') 'PublishDisabled must report published-disabled.'
        $ver = Invoke-Observe -Params ($extra + @{ Mode = 'Verify'; ExpectedState = 'Disabled' }) -Sim $sim -ContextFile $ContextFile
        Assert-ObsOk $ver "Verify under $ContextFile"
        Assert-Observe ($ver.Result['status'] -ceq 'verified' -and $ver.Result['filter_enabled'] -eq $false) 'Verify must report the disabled filter.'
        $filterPut = @($pub.Writes | Where-Object { (Get-ObsKind $_) -ceq 'PUT filter' })[0]
        Assert-Observe ($filterPut.Body['properties']['azMonitorFilterSettings']['targetResource'] -ceq [string]$ctx['log_analytics']['resource_id']) 'The filter target must be the workspace of the context.'
        $agentPatch = @($bind.Writes | Where-Object { $_.Host -ceq 'management.azure.com' })[0]
        Assert-Observe ($agentPatch.Path -ceq [string]$ctx['agent']['resource_id']) 'The bind must patch the agent of the context.'
        $all = @($bind.Calls) + @($pub.Calls) + @($ver.Calls)
        $text = ((@($all | ForEach-Object { "$($_.Host)$($_.Path)?$($_.Query)$($_.Raw)" })) + @($bind.Text, $pub.Text, $ver.Text)) -join "`n"
        return @{ Ctx = $ctx; Text = $text; Calls = $all }
    }
    $synCycle = Invoke-ObsCycle -ContextFile $synFile
    $altCycle = Invoke-ObsCycle -ContextFile $altFile
    $synLeaves = Get-AsreContextLeaf -Context $synCtx
    $altLeaves = Get-AsreContextLeaf -Context $altCtx
    $altValues = [System.Collections.Generic.HashSet[string]]::new([string[]]@($altLeaves.Values), [System.StringComparer]::Ordinal)
    $syntheticOnly = @($synLeaves.Values | Where-Object { $_.Length -ge 8 -and -not $altValues.Contains($_) } | Sort-Object -Unique)
    Assert-Observe ($syntheticOnly.Count -ge 5) 'The fixtures must differ in several values.'
    foreach ($value in $syntheticOnly) { Assert-Observe (-not $altCycle.Text.Contains($value)) 'A cycle under the alternative context must carry none of the synthetic-only values.' }
    Assert-Observe ($altCycle.Text.Contains([string]$altCtx['subscription_id']) -and $altCycle.Text.Contains([string]$altCtx['agent']['resource_id'])) 'The alternative cycle must carry the alternative identities.'
    Assert-Observe ($synCycle.Text.Contains([string]$synCtx['subscription_id'])) 'The synthetic cycle must carry the synthetic identities.'

    $minimalFile = Join-Path $tempDir 'context.minimal.json'
    $minimal = [System.IO.File]::ReadAllText($altFile) | ConvertFrom-Json -AsHashtable
    $minimal['servicenow'] = $null
    $minimal['agent']['endpoint'] = $null
    $minimal['operator_object_id'] = $null
    [System.IO.File]::WriteAllText($minimalFile, (ConvertTo-Json -InputObject $minimal -Depth 20), [System.Text.UTF8Encoding]::new($false))
    $minimalCtx = Get-AsreTestContext -Path $minimalFile
    Assert-Observe ($null -eq $minimalCtx['servicenow'] -and $null -eq $minimalCtx['agent']['endpoint'] -and $null -eq $minimalCtx['operator_object_id']) 'The minimal context must carry null ticketing, endpoint and operator.'
    $null = Invoke-ObsCycle -ContextFile $minimalFile -ExplicitPath $minimalFile

    # =========================================================================================================
    # Case 4 (P-6, P-9): Bind
    # =========================================================================================================
    $payload = [ordered]@{ type = 'AzMonitor'; connectionName = 'azmonitor'; oboUser = '' }
    $unbound = New-ObsSim -Ctx $synCtx -Unbound
    $r = Invoke-Observe -Params @{ Mode = 'Bind'; ConfirmAgentName = $agentName } -Sim $unbound
    Assert-ObsOk $r 'Bind (unbound)'
    Assert-Observe ($r.Writes.Count -eq 1 -and (Get-ObsKind $r.Writes[0]) -ceq 'PATCH agent') 'Bind must send exactly one ARM PATCH.'
    Assert-Observe ($r.Writes[0].Path -ceq [string]$synCtx['agent']['resource_id'] -and $r.Writes[0].Query -match 'api-version=2025-05-01-preview' -and $null -eq $r.Writes[0].IfMatch) 'The bind PATCH must target the agent at the bind API version without If-Match.'
    Assert-Observe ($r.Writes[0].Body['properties'].Count -eq 1 -and ((ConvertTo-Json -InputObject $r.Writes[0].Body['properties']['incidentManagementConfiguration'] -Compress) -ceq (ConvertTo-Json -InputObject $payload -Compress))) 'The bind body must be exactly the binding payload.'
    Assert-Observe ($r.Result['status'] -ceq 'bound' -and $r.Result['platform'] -ceq 'bound' -and $r.Result['api_version'] -ceq '2025-05-01-preview') 'Bind must report bound.'
    Assert-Observe ($r.Az.Count -eq 3) 'Bind must use az only to read the account and two tokens.'

    $s = New-ObsSim -Ctx $synCtx -Unbound; $s.PendingAfterBind = 1
    $r = Invoke-Observe -Params @{ Mode = 'Bind'; ConfirmAgentName = $agentName } -Sim $s
    Assert-ObsOk $r 'Bind (InProgress then Succeeded)'
    Assert-Observe ($r.Sleeps.Count -eq 1 -and $r.Sleeps[0] -eq 5) 'An InProgress agent must be polled every 5 seconds.'
    $s = New-ObsSim -Ctx $synCtx -Unbound; $s.ConnectivityFalseFor = 2
    $r = Invoke-Observe -Params @{ Mode = 'Bind'; ConfirmAgentName = $agentName } -Sim $s
    Assert-ObsOk $r 'Bind (connectivity false then true)'
    Assert-Observe ($r.Sleeps.Count -eq 2) 'A connection that is not ready must be polled.'
    $s = New-ObsSim -Ctx $synCtx -Unbound; $s.StayInProgress = $true; $s.Connectivity['observedConnectionStatus'] = 'Connecting'; $s.Connectivity['success'] = $false
    $r = Invoke-Observe -Params @{ Mode = 'Bind'; ConfirmAgentName = $agentName; ReadinessTimeoutSeconds = 30 } -Sim $s
    Assert-ObsFail $r 'Bind (timeout)' 'did not become ready.*Connecting'
    Assert-Observe ($r.Sleeps.Count -eq 5) 'A 30 second timeout must poll 6 times with 5 sleeps.'
    Assert-Observe ($null -eq $r.Result) 'A timed-out bind must not report a result.'
    $s = New-ObsSim -Ctx $synCtx -Unbound; $s.PendingAfterBind = 0; $s.Connectivity['surprise'] = 'x'
    $r = Invoke-Observe -Params @{ Mode = 'Bind'; ConfirmAgentName = $agentName } -Sim $s
    Assert-ObsFail $r 'Bind (unknown DTO field)' 'unsupported connectivity DTO field'
    Assert-Observe ($r.Sleeps.Count -eq 0) 'A DTO violation must not be polled.'
    Assert-Observe ($r.Error -cmatch 'The platform binding was already sent; run -Mode Bind again to continue waiting\.\z') 'A failure after the bind PATCH must say that the binding was already sent.'
    $s = New-ObsSim -Ctx $synCtx -Unbound; $s.Connectivity['errorMessage'] = 5
    $r = Invoke-Observe -Params @{ Mode = 'Bind'; ConfirmAgentName = $agentName } -Sim $s
    Assert-ObsFail $r 'Bind (non-string DTO member)' 'unsupported connectivity DTO type'
    $s = New-ObsSim -Ctx $synCtx -Unbound; $s.Provisioning = 'Succeeded'
    Add-ObsRule -Sim $s -Method 'PATCH' -Pattern '/agents/' -Status 200
    $s.Incident = [ordered]@{ type = 'OtherPlatform'; connectionName = 'secretish-connection'; oboUser = '' }
    $r = Invoke-Observe -Params @{ Mode = 'Bind'; ConfirmAgentName = $agentName } -Sim $s
    Assert-ObsFail $r 'Bind (foreign platform)' 'another incident platform configuration.*OtherPlatform' 0
    Assert-Observe (-not $r.Text.Contains('secretish-connection')) 'A foreign platform refusal must report names only.'
    Assert-Observe ($r.Error -cnotmatch 'already sent') 'A refusal before the bind PATCH must not claim that the binding was sent.'
    $s = New-ObsSim -Ctx $synCtx
    Assert-Observe ($s.Incident.Count -eq 6 -and $null -eq $s.Incident['apiConnectionName'] -and $s.Incident['connectionKey'] -ceq '' -and $null -eq $s.Incident['connectionUrl']) 'The simulator must read the binding back with the live service defaults.'
    $r = Invoke-Observe -Params @{ Mode = 'Bind'; ConfirmAgentName = $agentName } -Sim $s
    Assert-ObsOk $r 'Bind (already bound)'
    Assert-Observe ($r.Writes.Count -eq 0 -and $r.Result['platform'] -ceq 'unchanged' -and $r.Result['status'] -ceq 'bound') 'An already bound agent must be unchanged with zero writes.'
    $s = New-ObsSim -Ctx $synCtx; $s.Incident = [ordered]@{ type = 'AzMonitor'; connectionName = 'azmonitor'; oboUser = '' }
    $r = Invoke-Observe -Params @{ Mode = 'Bind'; ConfirmAgentName = $agentName } -Sim $s
    Assert-Observe ($r.Ok -and $r.Writes.Count -eq 0 -and $r.Result['platform'] -ceq 'unchanged') 'A binding read back without service defaults must also be unchanged.'
    # The live sequence: bind, then run Bind again. The PATCH echo carries the service defaults and must count as bound.
    $s = New-ObsSim -Ctx $synCtx -Unbound
    Assert-ObsOk (Invoke-Observe -Params @{ Mode = 'Bind'; ConfirmAgentName = $agentName } -Sim $s) 'Bind (first run)'
    Assert-Observe ($s.Incident.Count -eq 6) 'The bind echo must carry the service defaults.'
    $r = Invoke-Observe -Params @{ Mode = 'Bind'; ConfirmAgentName = $agentName } -Sim $s
    Assert-ObsOk $r 'Bind (second run)'
    Assert-Observe ($r.Writes.Count -eq 0 -and $r.Result['platform'] -ceq 'unchanged' -and $r.Result['status'] -ceq 'bound') 'A second Bind must be unchanged with zero writes.'
    # An extra field is tolerated only while it is null or empty; everything else stays another platform configuration.
    foreach ($extra in @(
            @{ Name = 'connectionKey'; Value = 'secretish-key' }, @{ Name = 'apiConnectionName'; Value = 'secretish-api' },
            @{ Name = 'connectionUrl'; Value = 'secretish-url' }, @{ Name = 'connectionKey'; Value = ' ' },
            @{ Name = 'surprise'; Value = $false }, @{ Name = 'surprise'; Value = 0 }, @{ Name = 'surprise'; Value = @() })) {
        $s = New-ObsSim -Ctx $synCtx; $s.Incident[$extra.Name] = $extra.Value
        $r = Invoke-Observe -Params @{ Mode = 'Bind'; ConfirmAgentName = $agentName } -Sim $s
        Assert-ObsFail $r "Bind (extra field $($extra.Name))" 'another incident platform configuration.*type: AzMonitor' 0
        Assert-Observe (-not $r.Text.Contains('secretish')) 'A refusal must report names only.'
    }
    foreach ($missing in 'type', 'connectionName', 'oboUser') {
        $s = New-ObsSim -Ctx $synCtx; $s.Incident.Remove($missing)
        Assert-ObsFail (Invoke-Observe -Params @{ Mode = 'Bind'; ConfirmAgentName = $agentName } -Sim $s) "Bind (missing $missing)" 'another incident platform configuration' 0
    }
    $s = New-ObsSim -Ctx $synCtx; $s.Incident['oboUser'] = $null
    Assert-ObsFail (Invoke-Observe -Params @{ Mode = 'Bind'; ConfirmAgentName = $agentName } -Sim $s) 'Bind (oboUser changed type)' 'another incident platform configuration' 0
    $equalCases = [ordered]@{
        'exact three keys'      = @{ Actual = [ordered]@{ type = 'AzMonitor'; connectionName = 'azmonitor'; oboUser = '' }; Equal = $true }
        'service defaults'      = @{ Actual = (New-ObsIncident -Sent $payload); Equal = $true }
        'some defaults'         = @{ Actual = [ordered]@{ type = 'AzMonitor'; connectionName = 'azmonitor'; oboUser = ''; connectionUrl = $null }; Equal = $true }
        'extra text'            = @{ Actual = [ordered]@{ type = 'AzMonitor'; connectionName = 'azmonitor'; oboUser = ''; connectionKey = 'x' }; Equal = $false }
        'extra blank'           = @{ Actual = [ordered]@{ type = 'AzMonitor'; connectionName = 'azmonitor'; oboUser = ''; connectionKey = ' ' }; Equal = $false }
        'extra false'           = @{ Actual = [ordered]@{ type = 'AzMonitor'; connectionName = 'azmonitor'; oboUser = ''; flag = $false }; Equal = $false }
        'extra empty array'     = @{ Actual = [ordered]@{ type = 'AzMonitor'; connectionName = 'azmonitor'; oboUser = ''; list = @() }; Equal = $false }
        'extra empty object'    = @{ Actual = [ordered]@{ type = 'AzMonitor'; connectionName = 'azmonitor'; oboUser = ''; bag = @{} }; Equal = $false }
        'missing type'          = @{ Actual = [ordered]@{ connectionName = 'azmonitor'; oboUser = ''; connectionKey = '' }; Equal = $false }
        'missing connection'    = @{ Actual = [ordered]@{ type = 'AzMonitor'; oboUser = ''; connectionKey = '' }; Equal = $false }
        'missing oboUser'       = @{ Actual = [ordered]@{ type = 'AzMonitor'; connectionName = 'azmonitor'; connectionKey = '' }; Equal = $false }
        'changed type'          = @{ Actual = [ordered]@{ type = 'Other'; connectionName = 'azmonitor'; oboUser = ''; connectionKey = '' }; Equal = $false }
        'changed case'          = @{ Actual = [ordered]@{ type = 'azmonitor'; connectionName = 'azmonitor'; oboUser = '' }; Equal = $false }
        'changed connection'    = @{ Actual = [ordered]@{ type = 'AzMonitor'; connectionName = 'other'; oboUser = ''; connectionKey = '' }; Equal = $false }
        'changed oboUser'       = @{ Actual = [ordered]@{ type = 'AzMonitor'; connectionName = 'azmonitor'; oboUser = 'someone' }; Equal = $false }
        'oboUser null'          = @{ Actual = [ordered]@{ type = 'AzMonitor'; connectionName = 'azmonitor'; oboUser = $null }; Equal = $false }
        'connection number'     = @{ Actual = [ordered]@{ type = 'AzMonitor'; connectionName = 1; oboUser = '' }; Equal = $false }
        'null configuration'    = @{ Actual = $null; Equal = $false }
        'string configuration'  = @{ Actual = 'AzMonitor'; Equal = $false }
        'array configuration'   = @{ Actual = @($payload); Equal = $false }
        'empty configuration'   = @{ Actual = @{}; Equal = $false }
    }
    foreach ($label in $equalCases.Keys) {
        $case = $equalCases[$label]
        Assert-Observe ((Test-ObserveBindingEqual -Configuration $case.Actual -Binding $payload) -eq $case.Equal) "The binding comparison '$label' must be $($case.Equal)."
    }
    Assert-Observe ((Get-ObservePlatformKind -Configuration (New-ObsIncident -Sent $payload) -Binding $payload)['kind'] -ceq 'bound') 'The binding with service defaults must be classified as bound.'
    Assert-Observe ((Get-ObservePlatformKind -Configuration (New-ObsIncident -Sent ([ordered]@{ type = 'None'; connectionName = ''; oboUser = '' })) -Binding $payload)['kind'] -ceq 'unbound') 'An empty platform with service defaults must be classified as unbound.'
    Assert-Observe ((Get-ObservePlatformKind -Configuration (New-ObsIncident -Sent ([ordered]@{ type = 'AzMonitor'; connectionName = 'other'; oboUser = '' })) -Binding $payload)['kind'] -ceq 'foreign') 'Another connection must be classified as foreign.'
    foreach ($shape in @($null, @{}, [ordered]@{ type = 'None'; connectionName = ''; oboUser = '' }, [ordered]@{ type = $null }, (New-ObsIncident -Sent ([ordered]@{ type = 'None'; connectionName = ''; oboUser = '' })))) {
        $s = New-ObsSim -Ctx $synCtx -Unbound; $s.Incident = $shape
        $r = Invoke-Observe -Params @{ Mode = 'Bind'; ConfirmAgentName = $agentName } -Sim $s
        Assert-ObsOk $r 'Bind (unbound shape)'
        Assert-Observe ($r.Writes.Count -eq 1) 'Every unbound shape must be bound with one write.'
    }
    $s = New-ObsSim -Ctx $synCtx -Unbound; $s.Incident = [ordered]@{ type = 'None'; connectionName = 'real-connection'; oboUser = '' }
    $r = Invoke-Observe -Params @{ Mode = 'Bind'; ConfirmAgentName = $agentName } -Sim $s
    Assert-ObsFail $r 'Bind (None type with a connection)' 'another incident platform configuration' 0
    $s = New-ObsSim -Ctx $synCtx -Unbound
    $r = Invoke-Observe -Params @{ Mode = 'Bind'; ConfirmAgentName = $agentName; WhatIf = $true } -Sim $s
    Assert-ObsOk $r 'Bind -WhatIf'
    Assert-Observe ($r.Result['status'] -ceq 'whatif' -and $r.Result['platform'] -ceq 'would-bind' -and $r.Writes.Count -eq 0 -and $null -eq $s.Incident -and $r.Sleeps.Count -eq 0) 'Bind -WhatIf must plan only.'
    foreach ($bad in @('', 'ASRE-DEMO-CUS', ($agentName + 'x'), 'x')) {
        $r = Invoke-Observe -Params @{ Mode = 'Bind'; ConfirmAgentName = $bad } -Sim (New-ObsSim -Ctx $synCtx -Unbound)
        Assert-ObsFail $r 'Bind (bad confirmation)' 'confirmed agent name'
        Assert-Observe ($r.Az.Count -eq 0 -and $r.Calls.Count -eq 0) 'A bad confirmation must be refused before any az or HTTP call.'
    }
    $r = Invoke-Observe -Params @{ Mode = 'Bind' } -Sim (New-ObsSim -Ctx $synCtx -Unbound)
    Assert-ObsFail $r 'Bind (no confirmation)' 'confirmed agent name'
    Assert-Observe ($r.Az.Count -eq 0 -and $r.Calls.Count -eq 0) 'A missing confirmation must be refused before any az or HTTP call.'
    $r = Invoke-Observe -Params @{ Mode = 'Bind'; ConfirmAgentName = $agentName; ExpectedState = 'Disabled' } -Sim (New-ObsSim -Ctx $synCtx -Unbound)
    Assert-ObsFail $r 'Bind with -ExpectedState' 'ExpectedState applies to -Mode Verify only'
    $r = Invoke-Observe -Params @{ Mode = 'Bind'; ConfirmAgentName = $agentName; BindApiVersion = '2025-06-01-preview' } -Sim (New-ObsSim -Ctx $synCtx -Unbound)
    Assert-ObsOk $r 'Bind (custom API version)'
    Assert-Observe ($r.Writes[0].Query -match 'api-version=2025-06-01-preview' -and $r.Result['api_version'] -ceq '2025-06-01-preview') 'The bind API version must be honoured.'
    $r = Invoke-Observe -Params @{ Mode = 'Bind'; ConfirmAgentName = $agentName; BindApiVersion = '../x' } -Sim (New-ObsSim -Ctx $synCtx -Unbound)
    Assert-ObsFail $r 'Bind with a malformed API version' 'BindApiVersion' 0
    Assert-Observe ($r.Calls.Count -eq 0) 'A malformed bind API version must be rejected before any request.'
    $s = New-ObsSim -Ctx $synCtx -Unbound
    Add-ObsFilter -Sim $s -Name 'quickstart-azmonitor' -Enabled $true
    Add-ObsFilter -Sim $s -Name 'odd name!' -Enabled $false
    Add-ObsFilter -Sim $s -Name 'mystery' -Enabled 'maybe'
    $r = Invoke-Observe -Params @{ Mode = 'Bind'; ConfirmAgentName = $agentName } -Sim $s
    Assert-ObsOk $r 'Bind (other filters)'
    $others = @($r.Result['other_filters'])
    Assert-Observe ($others.Count -eq 3) 'Bind must list every other filter.'
    Assert-Observe (@($others | Where-Object { $_['name'] -ceq 'quickstart-azmonitor' -and $_['enabled'] -eq $true }).Count -eq 1) 'An enabled filter must be reported as enabled.'
    Assert-Observe (@($others | Where-Object { $_['name'] -ceq '<unprintable>' -and $_['enabled'] -eq $false }).Count -eq 1) 'A filter name outside the safe set must be sanitised.'
    Assert-Observe (@($others | Where-Object { $_['name'] -ceq 'mystery' -and $_['enabled'] -ceq 'unknown' }).Count -eq 1) 'An unreadable filter state must be reported as unknown.'

    # The data plane may answer 502, 503 or 504 on the first connectivity reads while it reconfigures after the bind PATCH.
    function Get-ObsConnectivityReads { param($Run) return @($Run.Calls | Where-Object { $_.Method -ceq 'GET' -and $_.Path -ceq '/api/v2/incidentManagement/checkConnectivityDetailed' }).Count }
    $boundHint = 'The platform binding was already sent; run -Mode Bind again to continue waiting\.\z'
    foreach ($transient in 502, 503, 504) {
        $s = New-ObsSim -Ctx $synCtx -Unbound
        Add-ObsRule -Sim $s -Method 'GET' -Pattern 'checkConnectivityDetailed' -Status $transient -Code 'Unavailable' -Remaining 2
        $r = Invoke-Observe -Params @{ Mode = 'Bind'; ConfirmAgentName = $agentName } -Sim $s
        Assert-ObsOk $r "Bind (connectivity HTTP $transient twice, then ready)"
        Assert-Observe ($r.Result['status'] -ceq 'bound' -and $r.Result['platform'] -ceq 'bound') "Bind must complete after HTTP $transient."
        Assert-Observe ($r.Writes.Count -eq 1 -and (Get-ObsKind $r.Writes[0]) -ceq 'PATCH agent') 'A transient connectivity failure must not repeat the bind PATCH.'
        Assert-Observe ($r.Sleeps.Count -eq 2 -and @($r.Sleeps | Where-Object { $_ -ne 5 }).Count -eq 0) 'A transient connectivity failure must be polled every 5 seconds.'
        Assert-Observe ((Get-ObsConnectivityReads $r) -eq 3) 'Two transient failures and the ready read are three connectivity reads.'
    }
    $s = New-ObsSim -Ctx $synCtx
    Add-ObsRule -Sim $s -Method 'GET' -Pattern 'checkConnectivityDetailed' -Status 502 -Code 'BadGateway' -Remaining 1
    $r = Invoke-Observe -Params @{ Mode = 'Bind'; ConfirmAgentName = $agentName } -Sim $s
    Assert-Observe ($r.Ok -and $r.Writes.Count -eq 0 -and $r.Sleeps.Count -eq 1 -and $r.Result['platform'] -ceq 'unchanged' -and $r.Result['status'] -ceq 'bound') 'A transient failure on an already bound agent must be waited out without a write.'
    foreach ($transient in 502, 503, 504) {
        $s = New-ObsSim -Ctx $synCtx -Unbound
        Add-ObsRule -Sim $s -Method 'GET' -Pattern 'checkConnectivityDetailed' -Status $transient -Code 'Unavailable'
        $r = Invoke-Observe -Params @{ Mode = 'Bind'; ConfirmAgentName = $agentName; ReadinessTimeoutSeconds = 30 } -Sim $s
        Assert-ObsFail $r "Bind (HTTP $transient for the whole timeout)" "\ABind did not become ready within the timeout \(last connection status: unavailable HTTP $transient\); run -Mode Bind again to keep waiting\." 1
        Assert-Observe ($r.Sleeps.Count -eq 5 -and (Get-ObsConnectivityReads $r) -eq 6) 'A 30 second timeout must poll the connectivity 6 times with 5 sleeps.'
        Assert-Observe ($null -eq $r.Result -and -not $r.Text.Contains($global:ObsTest.Marker)) 'A timed-out bind must report neither a result nor a response body.'
    }
    $s = New-ObsSim -Ctx $synCtx -Unbound; $s.StayInProgress = $true; $s.Connectivity['observedConnectionStatus'] = 'Connecting'; $s.Connectivity['success'] = $false
    Add-ObsRule -Sim $s -Method 'GET' -Pattern 'checkConnectivityDetailed' -Status 502 -Code 'BadGateway' -Remaining 1
    $r = Invoke-Observe -Params @{ Mode = 'Bind'; ConfirmAgentName = $agentName; ReadinessTimeoutSeconds = 30 } -Sim $s
    Assert-ObsFail $r 'Bind (HTTP 502 then not ready)' 'last connection status: Connecting\)' 1
    # Everything else about the connectivity read stays fatal at once, and says that the bind PATCH was already sent.
    foreach ($fatal in 400, 401, 403, 404, 429, 500, 501) {
        $s = New-ObsSim -Ctx $synCtx -Unbound
        Add-ObsRule -Sim $s -Method 'GET' -Pattern 'checkConnectivityDetailed' -Status $fatal -Code 'Failed'
        $r = Invoke-Observe -Params @{ Mode = 'Bind'; ConfirmAgentName = $agentName } -Sim $s
        Assert-ObsFail $r "Bind (connectivity HTTP $fatal)" "connectivity-read failed \(HttpFailure, HTTP $fatal, Failed\).*$boundHint" 1
        Assert-Observe ($r.Sleeps.Count -eq 0 -and (Get-ObsConnectivityReads $r) -eq 1) "HTTP $fatal on the connectivity read must not be polled."
        Assert-Observe ($null -eq $r.Result -and -not $r.Text.Contains($global:ObsTest.Marker)) 'A failed bind must report neither a result nor a response body.'
    }
    $s = New-ObsSim -Ctx $synCtx -Unbound
    Add-ObsRule -Sim $s -Method 'GET' -Pattern 'checkConnectivityDetailed' -Transport
    $r = Invoke-Observe -Params @{ Mode = 'Bind'; ConfirmAgentName = $agentName } -Sim $s
    Assert-ObsFail $r 'Bind (connectivity transport failure)' "connectivity-read failed \(TransportFailure\)\. Check the agent endpoint and network access\..*$boundHint" 1
    Assert-Observe ($r.Sleeps.Count -eq 0 -and (Get-ObsConnectivityReads $r) -eq 1) 'A transport failure on the connectivity read must not be polled.'
    # The ARM read of the wait loop keeps its behaviour: a 502 there is fatal (ARM reads 1 to 3 are the sign-in, the agent check and the platform check).
    $s = New-ObsSim -Ctx $synCtx -Unbound
    Add-ObsRule -Sim $s -Method 'GET' -Pattern '/agents/' -Skip 3 -Status 502 -Code 'BadGateway' -Remaining 1
    $r = Invoke-Observe -Params @{ Mode = 'Bind'; ConfirmAgentName = $agentName } -Sim $s
    Assert-ObsFail $r 'Bind (ARM platform-read HTTP 502 after the PATCH)' "platform-read failed \(HttpFailure, HTTP 502, BadGateway\).*$boundHint" 1
    Assert-Observe ($r.Sleeps.Count -eq 0 -and (Get-ObsConnectivityReads $r) -eq 0) 'An ARM read failure must not be polled.'
    # Without a PATCH in this run there is nothing to hint at.
    foreach ($fatal in 403, 404) {
        $s = New-ObsSim -Ctx $synCtx
        Add-ObsRule -Sim $s -Method 'GET' -Pattern 'checkConnectivityDetailed' -Status $fatal -Code 'Failed'
        $r = Invoke-Observe -Params @{ Mode = 'Bind'; ConfirmAgentName = $agentName } -Sim $s
        Assert-ObsFail $r "Bind (already bound, connectivity HTTP $fatal)" "connectivity-read failed \(HttpFailure, HTTP $fatal, Failed\)" 0
        Assert-Observe ($r.Error -cnotmatch 'already sent' -and (Get-ObsConnectivityReads $r) -eq 1 -and $r.Sleeps.Count -eq 0) 'A failure without a bind PATCH must not claim that the binding was sent.'
    }
    $s = New-ObsSim -Ctx $synCtx
    Add-ObsRule -Sim $s -Method 'GET' -Pattern 'checkConnectivityDetailed' -Transport
    $r = Invoke-Observe -Params @{ Mode = 'Bind'; ConfirmAgentName = $agentName } -Sim $s
    Assert-ObsFail $r 'Bind (already bound, connectivity transport failure)' 'TransportFailure' 0
    Assert-Observe ($r.Error -cnotmatch 'already sent') 'A transport failure without a bind PATCH must not claim that the binding was sent.'
    # Only the Bind wait tolerates it: the checks of the other modes keep failing at once.
    $s = New-PublishedSim
    Add-ObsRule -Sim $s -Method 'GET' -Pattern 'checkConnectivityDetailed' -Status 502 -Code 'BadGateway'
    $r = Invoke-Observe -Params @{ Mode = 'Verify' } -Sim $s
    Assert-ObsFail $r 'Verify (connectivity HTTP 502)' 'connectivity-read failed \(HttpFailure, HTTP 502, BadGateway\)' 0
    Assert-Observe ((Get-ObsConnectivityReads $r) -eq 1 -and $r.Sleeps.Count -eq 0 -and $r.Error -cnotmatch 'already sent') 'Verify must not wait for the connectivity.'

    # =========================================================================================================
    # Case 5 (P-2, P-3, P-4, P-10, P-15): PublishDisabled
    # =========================================================================================================
    $s = New-BoundSim
    $r = Invoke-Observe -Params @{ Mode = 'PublishDisabled' } -Sim $s
    Assert-ObsOk $r 'PublishDisabled (create)'
    Assert-Observe ((Get-ObsWriteKinds $r) -ceq 'PUT handler, PUT permissions, PUT filter') "The create path must write handler, permissions, filter in order (wrote: $(Get-ObsWriteKinds $r))."
    Assert-Observe (@($r.Writes | Where-Object { $null -ne $_.IfMatch }).Count -eq 0) 'Creates must carry no If-Match.'
    Assert-Observe ($r.Result['status'] -ceq 'published-disabled' -and $r.Result['handler'] -ceq 'created' -and $r.Result['permissions'] -ceq 'created' -and $r.Result['filter'] -ceq 'created' -and $r.Result['filter_enabled'] -eq $false) 'The create path must report created and disabled.'
    Assert-Observe ($r.Result['instructions_sha256'] -ceq $expectedHash -and $r.Result['agent'] -ceq $agentName) 'The result must carry the instruction hash and the agent name.'
    $handlerPut = $r.Writes[0].Body
    $hp = $handlerPut['properties']
    Assert-Observe ($handlerPut['name'] -ceq $handlerName -and $handlerPut['type'] -ceq 'ExtendedAgent' -and @($handlerPut['tags']).Count -eq 0) 'The handler body must carry the name, type and empty tags.'
    Assert-Observe ($hp['instructions'] -ceq $expectedInstructions) 'The handler must carry the LF instruction text.'
    Assert-Observe ((@($hp['tools']) -join ',') -ceq 'QueryLogAnalyticsByResourceId,ReadFile,SearchMemory' -and (@($hp['mcpTools']) -join ',') -ceq 'asre-review-evidence') 'The handler tools must be the three platform tools and the evidence tool only.'
    Assert-Observe (@($hp['commonTools']).Count -eq 0 -and @($hp['handoffs']).Count -eq 0 -and $hp['enableSkills'] -eq $false -and @($hp['allowedSkills']).Count -eq 0 -and $hp['addSystemSkills'] -eq $false) 'The handler must have no skills, handoffs or common tools.'
    foreach ($forbidden in 'hooks', 'commonPrompts', 'connectors', 'agentsAsTools', 'permissions', 'instructionsOverride', 'userPromptOverride', 'handoffPromptOverride') {
        Assert-Observe (-not $hp.ContainsKey($forbidden)) "The handler body must not carry $forbidden."
    }
    Assert-Observe ($hp.Count -eq 9) 'The handler body must carry exactly nine properties.'
    $policyPut = $r.Writes[1].Body
    $policyNames = @($policyPut['allow'])
    $policyExpected = @('QueryLogAnalyticsByResourceId', 'ReadFile', 'SearchMemory', 'asre-review-evidence')
    Assert-Observe ($policyPut.Count -eq 1 -and $policyNames.Count -eq 4 -and @($policyExpected | Where-Object { $policyNames -ccontains $_ }).Count -eq 4) 'The permission policy must be exactly the four names.'
    $fp = $r.Writes[2].Body['properties']
    Assert-Observe ($fp['isEnabled'] -eq $false -and $fp['incidentPlatform'] -ceq 'AzMonitor' -and $fp['agentMode'] -ceq 'Review' -and $fp['handlingAgent'] -ceq $handlerName) 'The filter must be pinned, Review mode and disabled.'
    Assert-Observe ($fp['azMonitorFilterSettings']['targetResource'] -ceq [string]$synCtx['log_analytics']['resource_id'] -and $fp['azMonitorFilterSettings']['targetResourceType'] -ceq 'Microsoft.OperationalInsights/workspaces') 'The filter target must be filled from the context.'
    Assert-Observe ((@($fp['priorities']) -join ',') -ceq 'Sev1,Sev2' -and $fp['titleContains'] -ceq 'asre-demo-' -and $fp['maxAutomatedInvestigationAttempts'] -eq 1 -and $fp['mergeEnabled'] -eq $false -and $fp['mergeWindowHours'] -eq 3) 'The filter must carry the declared values.'
    Assert-Observe (-not $r.Writes[2].Body.ContainsKey('tags')) 'The filter body must carry no tags.'
    Assert-Observe (@($r.Writes | Where-Object { $_.Redirects -ne 0 }).Count -eq 0) 'No request may follow redirects.'

    $r2 = Invoke-Observe -Params @{ Mode = 'PublishDisabled' } -Sim $s
    Assert-ObsOk $r2 'PublishDisabled (second run)'
    Assert-Observe ($r2.Writes.Count -eq 0) 'A second run must send zero non-GET requests.'
    Assert-Observe ($r2.Result['handler'] -ceq 'unchanged' -and $r2.Result['permissions'] -ceq 'unchanged' -and $r2.Result['filter'] -ceq 'unchanged' -and $r2.Result['status'] -ceq 'published-disabled') 'A second run must report unchanged.'

    # Global policy: null permissions and the empty triple are both accepted.
    $s = New-BoundSim; $s.GlobalPermissions = [ordered]@{ allow = @(); ask = @(); deny = @() }
    Assert-ObsOk (Invoke-Observe -Params @{ Mode = 'PublishDisabled' } -Sim $s) 'PublishDisabled (empty global triple)'
    $s = New-BoundSim; $s.GlobalPermissions = [ordered]@{ allow = @() }
    Assert-ObsOk (Invoke-Observe -Params @{ Mode = 'PublishDisabled' } -Sim $s) 'PublishDisabled (missing global keys)'

    # Drift is corrected with If-Match.
    $s = New-PublishedSim
    $hpDrift = Get-ObsHandlerProps $s; $hpDrift['tools'] = @('ReadFile'); $hpDrift['enableSkills'] = $true
    $s.Policy.body['allow'] = @('ReadFile', 'RunShellCommand')
    $fpDrift = Get-ObsFilterProps $s; $fpDrift['priorities'] = @('Sev0')
    $handlerEtag = $s.Handler.etag; $policyEtag = $s.Policy.etag; $filterEtag = $s.Filters[$filterName].etag
    $r = Invoke-Observe -Params @{ Mode = 'PublishDisabled' } -Sim $s
    Assert-ObsOk $r 'PublishDisabled (drift)'
    Assert-Observe ((Get-ObsWriteKinds $r) -ceq 'PUT handler, PUT permissions, PATCH filter') "Drift must be corrected in order (wrote: $(Get-ObsWriteKinds $r))."
    Assert-Observe ($r.Writes[0].IfMatch -ceq $handlerEtag -and $r.Writes[1].IfMatch -ceq $policyEtag -and $r.Writes[2].IfMatch -ceq $filterEtag) 'Drift corrections must carry the ETag of the preceding read.'
    Assert-Observe ($r.Result['handler'] -ceq 'updated' -and $r.Result['permissions'] -ceq 'updated' -and $r.Result['filter'] -ceq 'updated') 'Drift corrections must report updated.'
    Assert-Observe ($r.Writes[2].Body['properties']['isEnabled'] -eq $false) 'The filter correction must keep the filter disabled.'
    Assert-Observe ((Invoke-Observe -Params @{ Mode = 'PublishDisabled' } -Sim $s).Writes.Count -eq 0) 'After a correction a further run must write nothing.'

    # 412 on the handler PUT stops everything.
    $s = New-PublishedSim
    (Get-ObsHandlerProps $s)['tools'] = @('ReadFile')
    Add-ObsRule -Sim $s -Method 'PUT' -Pattern '/extendedAgent/agents/' -Status 412 -Code 'PreconditionFailed'
    $r = Invoke-Observe -Params @{ Mode = 'PublishDisabled' } -Sim $s
    Assert-ObsFail $r 'PublishDisabled (412)' 'HTTP 412.*changed concurrently, re-read' 1
    Assert-Observe ((Get-ObsKind $r.Writes[0]) -ceq 'PUT handler') 'Only the handler PUT may have been attempted.'

    # An existing policy without an ETag fails closed.
    $s = New-PublishedSim
    $s.Policy.body['allow'] = @('ReadFile'); $s.NoPolicyEtag = $true
    $r = Invoke-Observe -Params @{ Mode = 'PublishDisabled' } -Sim $s
    Assert-ObsFail $r 'PublishDisabled (policy without ETag)' 'no ETag' 0

    # Refusals before the first write.
    $refusals = [ordered]@{
        'not Review mode'      = @{ Setup = { param($x) $x.Mode = 'Autonomous' }; Pattern = 'Review mode' }
        'access level'         = @{ Setup = { param($x) $x.Access = 'High' }; Pattern = 'access level is not Low' }
        'provisioning'         = @{ Setup = { param($x) $x.Provisioning = 'Updating' }; Pattern = 'provisioningState is not Succeeded' }
        'agent id'             = @{ Setup = { param($x) $x.ReportedId = $x.AgentId + 'x' }; Pattern = 'not the one of the deployment context' }
        'global allow'         = @{ Setup = { param($x) $x.GlobalPermissions = [ordered]@{ allow = @('X'); ask = @(); deny = @() } }; Pattern = 'global permission policy' }
        'global deny'          = @{ Setup = { param($x) $x.GlobalPermissions = [ordered]@{ deny = @('X') } }; Pattern = 'global permission policy' }
        'global unknown key'   = @{ Setup = { param($x) $x.GlobalPermissions = [ordered]@{ surprise = @() } }; Pattern = 'global permission policy' }
        'platform unbound'     = @{ Setup = { param($x) $x.Incident = $null }; Pattern = 'run -Mode Bind first' }
        'platform changed'     = @{ Setup = { param($x) $x.Incident['connectionName'] = 'other' }; Pattern = 'run -Mode Bind first' }
        'platform extra field' = @{ Setup = { param($x) $x.Incident['connectionKey'] = 'x' }; Pattern = 'run -Mode Bind first' }
        'platform missing key' = @{ Setup = { param($x) $x.Incident.Remove('oboUser') }; Pattern = 'run -Mode Bind first' }
        'not ready'            = @{ Setup = { param($x) $x.Connectivity['success'] = $false }; Pattern = 'connection is not ready' }
        'wrong incident type'  = @{ Setup = { param($x) $x.Connectivity['incidentType'] = 'Other' }; Pattern = 'connection is not ready' }
        'unknown DTO field'    = @{ Setup = { param($x) $x.Connectivity['surprise'] = 'x' }; Pattern = 'unsupported connectivity DTO field' }
        'tool missing'         = @{ Setup = { param($x) $x.Tool = $null }; Pattern = 'publish the Common profile first' }
        'tool wrong type'      = @{ Setup = { param($x) $x.Tool['type'] = 'Other' }; Pattern = 'publish the Common profile first' }
        'tool wrong inner'     = @{ Setup = { param($x) $x.Tool['properties']['type'] = 'Other' }; Pattern = 'publish the Common profile first' }
        'tool wrong name'      = @{ Setup = { param($x) $x.Tool['name'] = 'other' }; Pattern = 'publish the Common profile first' }
        'paged filter list'    = @{ Setup = { param($x) $x.NextLink = 'next-page' }; Pattern = 'incomplete' }
        'foreign filter'       = @{ Setup = { param($x) Add-ObsFilter -Sim $x -Name $x.FilterName -Enabled $false -HandlingAgent 'someone-else' }; Pattern = 'not owned' }
        'foreign platform'     = @{ Setup = { param($x) Add-ObsFilter -Sim $x -Name $x.FilterName -Enabled $false -HandlingAgent $x.HandlerName -Platform 'Other' }; Pattern = 'not owned' }
        'owned enabled filter' = @{ Setup = { param($x) Add-ObsFilter -Sim $x -Name $x.FilterName -Enabled $true -HandlingAgent $x.HandlerName }; Pattern = 'pause first' }
        'owned unreadable'     = @{ Setup = { param($x) Add-ObsFilter -Sim $x -Name $x.FilterName -Enabled 'maybe' -HandlingAgent $x.HandlerName }; Pattern = 'not readable' }
    }
    foreach ($label in $refusals.Keys) {
        $s = New-BoundSim
        & $refusals[$label].Setup $s
        $r = Invoke-Observe -Params @{ Mode = 'PublishDisabled' } -Sim $s
        Assert-ObsFail $r "PublishDisabled refusal '$label'" $refusals[$label].Pattern 0
    }

    # A platform that is not ready answers the filter PUT with 400.
    $s = New-BoundSim
    Add-ObsRule -Sim $s -Method 'PUT' -Pattern '/incidentFilters/' -Status 400 -Code 'IncidentPlatformConfigurationPending'
    $r = Invoke-Observe -Params @{ Mode = 'PublishDisabled' } -Sim $s
    Assert-ObsFail $r 'PublishDisabled (400 pending)' 'HTTP 400.*IncidentPlatformConfigurationPending.*platform not ready, run -Mode Bind and wait'
    Assert-Observe (-not $r.Text.Contains($global:ObsTest.Marker)) 'An error body must never be echoed.'
    $s = New-BoundSim
    Add-ObsRule -Sim $s -Method 'PUT' -Pattern '/permissions' -Transport
    $r = Invoke-Observe -Params @{ Mode = 'PublishDisabled' } -Sim $s
    Assert-ObsFail $r 'PublishDisabled (transport failure)' 'TransportFailure.*Check the agent endpoint and network access'
    Assert-Observe ((Get-ObsWriteKinds $r) -ceq 'PUT handler, PUT permissions') 'A transport failure must stop the sequence.'
    $s = New-BoundSim
    Add-ObsRule -Sim $s -Method 'PUT' -Pattern '/extendedAgent/agents/' -Status 403 -Code 'Forbidden'
    Assert-ObsFail (Invoke-Observe -Params @{ Mode = 'PublishDisabled' } -Sim $s) 'PublishDisabled (403)' 'HTTP 403.*operator''s role on the agent and allow for propagation'
    $s = New-BoundSim; $s.Sticky = $false
    Add-ObsRule -Sim $s -Method 'PUT' -Pattern '/incidentFilters/' -After { param($res) $res['properties']['isEnabled'] = $true }
    $r = Invoke-Observe -Params @{ Mode = 'PublishDisabled' } -Sim $s
    Assert-ObsFail $r 'PublishDisabled (service enables the filter)' 'not an owned, disabled filter'

    # WhatIf.
    $s = New-BoundSim
    $r = Invoke-Observe -Params @{ Mode = 'PublishDisabled'; WhatIf = $true } -Sim $s
    Assert-ObsOk $r 'PublishDisabled -WhatIf'
    Assert-Observe ($r.Writes.Count -eq 0 -and $r.Result['status'] -ceq 'whatif' -and $r.Result['handler'] -ceq 'would-create' -and $r.Result['permissions'] -ceq 'would-create' -and $r.Result['filter'] -ceq 'would-create' -and $r.Sleeps.Count -eq 0) 'PublishDisabled -WhatIf must plan creates and write nothing.'
    Assert-Observe ($null -eq $s.Handler -and $null -eq $s.Policy -and $s.Filters.Count -eq 0) 'PublishDisabled -WhatIf must change nothing.'
    $s = New-PublishedSim
    (Get-ObsHandlerProps $s)['tools'] = @('ReadFile')
    $r = Invoke-Observe -Params @{ Mode = 'PublishDisabled'; WhatIf = $true } -Sim $s
    Assert-Observe ($r.Writes.Count -eq 0 -and $r.Result['handler'] -ceq 'would-update' -and $r.Result['permissions'] -ceq 'unchanged' -and $r.Result['filter'] -ceq 'unchanged') 'PublishDisabled -WhatIf must plan updates.'

    # =========================================================================================================
    # Case 6 (P-11, P-14): Verify
    # =========================================================================================================
    $s = New-PublishedSim
    $r = Invoke-Observe -Params @{ Mode = 'Verify' } -Sim $s
    Assert-ObsOk $r 'Verify'
    Assert-Observe ($r.Writes.Count -eq 0 -and $r.Result['status'] -ceq 'verified' -and $r.Result['filter_enabled'] -eq $false) 'Verify must be read-only and report the disabled filter.'
    Assert-ObsOk (Invoke-Observe -Params @{ Mode = 'Verify'; ExpectedState = 'Disabled' } -Sim $s) 'Verify -ExpectedState Disabled'
    Assert-ObsFail (Invoke-Observe -Params @{ Mode = 'Verify'; ExpectedState = 'Enabled' } -Sim $s) 'Verify -ExpectedState Enabled on a disabled filter' 'disabled but Enabled was expected' 0
    (Get-ObsFilterProps $s)['isEnabled'] = $true
    $r = Invoke-Observe -Params @{ Mode = 'Verify' } -Sim $s
    Assert-Observe ($r.Ok -and $r.Result['filter_enabled'] -eq $true) 'Verify with Any must accept and report an enabled filter.'
    Assert-ObsOk (Invoke-Observe -Params @{ Mode = 'Verify'; ExpectedState = 'Enabled' } -Sim $s) 'Verify -ExpectedState Enabled'
    Assert-ObsFail (Invoke-Observe -Params @{ Mode = 'Verify'; ExpectedState = 'Disabled' } -Sim $s) 'Verify -ExpectedState Disabled on an enabled filter' 'enabled but Disabled was expected'
    $readOnly = Invoke-Observe -Params @{ Mode = 'Verify'; WhatIf = $true } -Sim $s
    Assert-Observe ($readOnly.Ok -and $readOnly.Result['status'] -ceq 'verified') 'Verify is unaffected by -WhatIf.'

    $drifts = [ordered]@{
        'extra tool'          = @{ Mutate = { param($x) $p = Get-ObsHandlerProps $x; $p['tools'] += 'RunAzCliCommand' }; Pattern = 'handler differs from the declaration \(Strict readback changed properties\.tools' }
        'case tool'           = @{ Mutate = { param($x) $p = Get-ObsHandlerProps $x; $p['mcpTools'] += 'asre-case-update' }; Pattern = 'handler differs.*properties\.mcpTools' }
        'skills on'           = @{ Mutate = { param($x) (Get-ObsHandlerProps $x)['enableSkills'] = $true }; Pattern = 'handler differs.*properties\.enableSkills' }
        'allowed skill'       = @{ Mutate = { param($x) (Get-ObsHandlerProps $x)['allowedSkills'] = @('s') }; Pattern = 'handler differs.*properties\.allowedSkills' }
        'hook present'        = @{ Mutate = { param($x) (Get-ObsHandlerProps $x)['hooks'] = @{ pre = 'x' } }; Pattern = 'handler differs.*properties\.hooks' }
        'unknown property'    = @{ Mutate = { param($x) (Get-ObsHandlerProps $x)['surprise'] = 'x' }; Pattern = 'handler differs.*properties\.surprise' }
        'instructions'        = @{ Mutate = { param($x) (Get-ObsHandlerProps $x)['instructions'] = 'x' }; Pattern = 'handler differs.*properties\.instructions' }
        'system skills null'  = @{ Mutate = { param($x) (Get-ObsHandlerProps $x)['addSystemSkills'] = $null }; Pattern = 'handler differs.*properties\.addSystemSkills' }
        'default changed'     = @{ Mutate = { param($x) (Get-ObsHandlerProps $x)['temperature'] = 0.7 }; Pattern = 'handler differs.*properties\.temperature' }
        'vanilla mode'        = @{ Mutate = { param($x) (Get-ObsHandlerProps $x)['enableVanillaMode'] = $true }; Pattern = 'handler differs.*properties\.enableVanillaMode' }
        'handler tags'        = @{ Mutate = { param($x) $x.Handler['resource']['tags'] = @{ env = 'x' } }; Pattern = 'handler differs.*envelope\.tags' }
        'handler envelope'    = @{ Mutate = { param($x) $x.Handler['resource']['surprise'] = 'x' }; Pattern = 'handler differs.*envelope\.surprise' }
        'handler name'        = @{ Mutate = { param($x) $x.Handler['resource']['name'] = 'other' }; Pattern = 'handler differs.*identity' }
        'metadata not string' = @{ Mutate = { param($x) (Get-ObsHandlerProps $x)['etag'] = 5 }; Pattern = 'handler differs.*properties\.etag' }
        'extra permission'    = @{ Mutate = { param($x) $x.Policy.body['allow'] += 'RunShellCommand' }; Pattern = 'permission policy differs' }
        'missing permission'  = @{ Mutate = { param($x) $x.Policy.body['allow'] = @('ReadFile') }; Pattern = 'permission policy differs' }
        'ask permission'      = @{ Mutate = { param($x) $x.Policy.body['ask'] = @('ReadFile') }; Pattern = 'permission policy differs' }
        'policy unknown key'  = @{ Mutate = { param($x) $x.Policy.body['surprise'] = @() }; Pattern = 'permission policy differs' }
        'priorities'          = @{ Mutate = { param($x) (Get-ObsFilterProps $x)['priorities'] = @('Sev0') }; Pattern = 'filter differs.*properties\.priorities' }
        'filter title'        = @{ Mutate = { param($x) (Get-ObsFilterProps $x)['titleContains'] = 'other' }; Pattern = 'filter differs.*properties\.titleContains' }
        'filter extra'        = @{ Mutate = { param($x) (Get-ObsFilterProps $x)['surprise'] = 'x' }; Pattern = 'filter differs.*properties\.surprise' }
        'filter target'       = @{ Mutate = { param($x) (Get-ObsFilterProps $x)['azMonitorFilterSettings']['targetResource'] = 'other' }; Pattern = 'filter differs.*targetResource' }
        'filter default'      = @{ Mutate = { param($x) (Get-ObsFilterProps $x)['impact'] = 'high' }; Pattern = 'filter differs.*properties\.impact' }
        'filter merge'        = @{ Mutate = { param($x) (Get-ObsFilterProps $x)['mergeEnabled'] = $true }; Pattern = 'filter differs.*properties\.mergeEnabled' }
        'filter owner'        = @{ Mutate = { param($x) (Get-ObsFilterProps $x)['handlingAgent'] = 'someone-else' }; Pattern = 'not owned by this handler' }
        'filter state'        = @{ Mutate = { param($x) (Get-ObsFilterProps $x)['isEnabled'] = 'maybe' }; Pattern = 'not readable' }
        'platform changed'    = @{ Mutate = { param($x) $x.Incident['connectionName'] = 'other' }; Pattern = 'Platform check failed.*run -Mode Bind first' }
        'platform extra field' = @{ Mutate = { param($x) $x.Incident['connectionKey'] = 'x' }; Pattern = 'Platform check failed.*run -Mode Bind first' }
        'platform missing key' = @{ Mutate = { param($x) $x.Incident.Remove('oboUser') }; Pattern = 'Platform check failed.*run -Mode Bind first' }
        'platform null user'  = @{ Mutate = { param($x) $x.Incident['oboUser'] = $null }; Pattern = 'Platform check failed.*run -Mode Bind first' }
        'platform unbound'    = @{ Mutate = { param($x) $x.Incident = $null }; Pattern = 'Platform check failed' }
        'not ready'           = @{ Mutate = { param($x) $x.Connectivity['success'] = $false }; Pattern = 'connection is not ready' }
        'tool missing'        = @{ Mutate = { param($x) $x.Tool = $null }; Pattern = 'publish the Common profile first' }
        'handler missing'     = @{ Mutate = { param($x) $x.Handler = $null }; Pattern = 'handler is not published' }
        'policy missing'      = @{ Mutate = { param($x) $x.Policy = $null }; Pattern = 'permission policy is not published' }
        'filter missing'      = @{ Mutate = { param($x) $x.Filters.Remove($x.FilterName) }; Pattern = 'incident filter is not published' }
        'global policy'       = @{ Mutate = { param($x) $x.GlobalPermissions = [ordered]@{ allow = @('X') } }; Pattern = 'global permission policy' }
        'agent mode'          = @{ Mutate = { param($x) $x.Mode = 'Autonomous' }; Pattern = 'Review mode' }
    }
    foreach ($label in $drifts.Keys) {
        $s = New-PublishedSim
        & $drifts[$label].Mutate $s
        Assert-ObsFail (Invoke-Observe -Params @{ Mode = 'Verify' } -Sim $s) "Verify drift '$label'" $drifts[$label].Pattern 0
    }
    # Tolerated server defaults: absent metadata, absent hooks, an explicit empty hooks object, absent default properties.
    $s = New-PublishedSim
    $props = Get-ObsHandlerProps $s
    foreach ($key in 'etag', 'createdAt', 'updatedAt', 'hooks', 'connectors', 'temperature', 'enableVanillaMode') { $props.Remove($key) }
    Assert-ObsOk (Invoke-Observe -Params @{ Mode = 'Verify' } -Sim $s) 'Verify (absent defaults)'
    $props['hooks'] = @{}
    Assert-ObsOk (Invoke-Observe -Params @{ Mode = 'Verify' } -Sim $s) 'Verify (empty hooks object)'
    $props['hooks'] = $null
    Assert-ObsOk (Invoke-Observe -Params @{ Mode = 'Verify' } -Sim $s) 'Verify (null hooks)'
    $fprops = Get-ObsFilterProps $s
    foreach ($key in 'name', 'impactedService', 'assignmentGroups', 'handlingAgents', 'etag') { $fprops.Remove($key) }
    Assert-ObsOk (Invoke-Observe -Params @{ Mode = 'Verify' } -Sim $s) 'Verify (absent filter defaults)'
    $s.Handler['resource'].Remove('tags'); $s.Handler['resource'].Remove('kind')
    Assert-ObsOk (Invoke-Observe -Params @{ Mode = 'Verify' } -Sim $s) 'Verify (absent envelope extras)'

    # =========================================================================================================
    # Case 7 (P-12, P-13): Enable and Pause
    # =========================================================================================================
    $s = New-PublishedSim
    Add-ObsFilter -Sim $s -Name 'quickstart-azmonitor' -Enabled $true
    Add-ObsFilter -Sim $s -Name 'quickstart-second' -Enabled $false
    $r = Invoke-Observe -Params @{ Mode = 'Enable'; ConfirmAgentName = $agentName } -Sim $s
    Assert-ObsFail $r 'Enable (another filter enabled)' 'other incident filters are enabled or unreadable \(quickstart-azmonitor\)' 0
    Assert-Observe (-not $r.Error.Contains('quickstart-second')) 'Only the enabled filters may be listed.'
    Assert-Observe ((Get-ObsFilterProps $s)['isEnabled'] -eq $false) 'A refused Enable must leave the filter disabled.'
    $s.Filters['quickstart-azmonitor']['resource']['properties']['isEnabled'] = 'maybe'
    Assert-ObsFail (Invoke-Observe -Params @{ Mode = 'Enable'; ConfirmAgentName = $agentName } -Sim $s) 'Enable (another filter unreadable)' 'enabled or unreadable' 0
    $s.Filters['quickstart-azmonitor']['resource']['properties']['isEnabled'] = $false
    $r = Invoke-Observe -Params @{ Mode = 'Enable'; ConfirmAgentName = $agentName; WhatIf = $true } -Sim $s
    Assert-Observe ($r.Ok -and $r.Result['status'] -ceq 'whatif' -and $r.Writes.Count -eq 0 -and (Get-ObsFilterProps $s)['isEnabled'] -eq $false) 'Enable -WhatIf must plan only.'
    $filterEtag = $s.Filters[$filterName].etag
    $r = Invoke-Observe -Params @{ Mode = 'Enable'; ConfirmAgentName = $agentName } -Sim $s
    Assert-ObsOk $r 'Enable'
    Assert-Observe ($r.Writes.Count -eq 1 -and (Get-ObsKind $r.Writes[0]) -ceq 'PATCH filter' -and $r.Writes[0].IfMatch -ceq $filterEtag) 'Enable must send one filter PATCH with the fresh ETag.'
    Assert-Observe ($r.Writes[0].Body['properties']['isEnabled'] -eq $true -and $r.Writes[0].Body['properties']['handlingAgent'] -ceq $handlerName) 'Enable must PATCH the full definition with isEnabled true.'
    Assert-Observe ($r.Result['status'] -ceq 'enabled' -and $r.Result['filter_enabled'] -eq $true) 'Enable must report enabled.'
    Assert-ObsOk (Invoke-Observe -Params @{ Mode = 'Verify'; ExpectedState = 'Enabled' } -Sim $s) 'Verify after Enable'
    Assert-ObsFail (Invoke-Observe -Params @{ Mode = 'Enable'; ConfirmAgentName = $agentName } -Sim $s) 'Enable (already enabled)' 'enabled but Disabled was expected' 0

    $s = New-PublishedSim
    (Get-ObsHandlerProps $s)['tools'] = @('ReadFile')
    Assert-ObsFail (Invoke-Observe -Params @{ Mode = 'Enable'; ConfirmAgentName = $agentName } -Sim $s) 'Enable (handler drift)' 'handler differs from the declaration' 0
    $s = New-PublishedSim; $s.Tool = $null
    Assert-ObsFail (Invoke-Observe -Params @{ Mode = 'Enable'; ConfirmAgentName = $agentName } -Sim $s) 'Enable (evidence tool missing)' 'publish the Common profile first' 0
    foreach ($bad in @('', 'ASRE-DEMO-CUS')) {
        $r = Invoke-Observe -Params @{ Mode = 'Enable'; ConfirmAgentName = $bad } -Sim (New-PublishedSim)
        Assert-ObsFail $r 'Enable (bad confirmation)' 'confirmed agent name'
        Assert-Observe ($r.Az.Count -eq 0 -and $r.Calls.Count -eq 0) 'Enable must refuse a bad confirmation before any az or HTTP call.'
    }

    # Readback drift after the PATCH: compensating pause.
    $s = New-PublishedSim
    Add-ObsRule -Sim $s -Method 'PATCH' -Pattern '/incidentFilters/' -Remaining 1 -After { param($res) $res['properties']['priorities'] = @('Sev0') }
    $r = Invoke-Observe -Params @{ Mode = 'Enable'; ConfirmAgentName = $agentName } -Sim $s
    Assert-ObsFail $r 'Enable (readback drift)' 'the filter was paused and verified' 2
    Assert-Observe ($r.Writes[0].Body['properties']['isEnabled'] -eq $true -and $r.Writes[1].Body['properties']['isEnabled'] -eq $false -and $null -ne $r.Writes[1].IfMatch -and $r.Writes[1].IfMatch -cne $r.Writes[0].IfMatch) 'The compensation must pause the filter with the new ETag.'
    Assert-Observe ((Get-ObsFilterProps $s)['isEnabled'] -eq $false) 'After compensation the filter must be disabled.'
    # A lost response (applied, then transport failure).
    $s = New-PublishedSim
    Add-ObsRule -Sim $s -Method 'PATCH' -Pattern '/incidentFilters/' -ApplyThenThrow -Remaining 1
    $r = Invoke-Observe -Params @{ Mode = 'Enable'; ConfirmAgentName = $agentName } -Sim $s
    Assert-ObsFail $r 'Enable (lost response)' 'the filter was paused and verified' 2
    Assert-Observe ((Get-ObsFilterProps $s)['isEnabled'] -eq $false) 'After a lost response the filter must be disabled again.'
    # A non-2xx answer to the PATCH (nothing applied).
    $s = New-PublishedSim
    Add-ObsRule -Sim $s -Method 'PATCH' -Pattern '/incidentFilters/' -Status 500 -Code 'InternalError' -Remaining 1
    $r = Invoke-Observe -Params @{ Mode = 'Enable'; ConfirmAgentName = $agentName } -Sim $s
    Assert-ObsFail $r 'Enable (HTTP 500)' 'the filter was paused and verified'
    Assert-Observe ((Get-ObsFilterProps $s)['isEnabled'] -eq $false) 'After a failed PATCH the filter must still be disabled.'
    # A compensation that cannot be verified.
    $s = New-PublishedSim
    Add-ObsRule -Sim $s -Method 'PATCH' -Pattern '/incidentFilters/' -Remaining 1 -After { param($res) $res['properties']['priorities'] = @('Sev0') }
    Add-ObsRule -Sim $s -Method 'PATCH' -Pattern '/incidentFilters/' -Status 500 -Code 'InternalError'
    $r = Invoke-Observe -Params @{ Mode = 'Enable'; ConfirmAgentName = $agentName } -Sim $s
    Assert-ObsFail $r 'Enable (failing compensation)' 'PAUSE COULD NOT BE VERIFIED: pause the filter in the portal now'
    Assert-Observe (-not ($r.Error -match 'paused and verified') -and (Get-ObsFilterProps $s)['isEnabled'] -eq $true) 'A failed compensation must be reported loudly and not as verified.'
    # A compensation whose readback is sticky (still enabled).
    $s = New-PublishedSim
    Add-ObsRule -Sim $s -Method 'PATCH' -Pattern '/incidentFilters/' -Remaining 1 -After { param($res) $res['properties']['priorities'] = @('Sev0') }
    Add-ObsRule -Sim $s -Method 'PATCH' -Pattern '/incidentFilters/' -After { param($res) $res['properties']['isEnabled'] = $true }
    Assert-ObsFail (Invoke-Observe -Params @{ Mode = 'Enable'; ConfirmAgentName = $agentName } -Sim $s) 'Enable (compensation readback still enabled)' 'portal now'

    # Pause.
    $s = New-PublishedSim
    (Get-ObsFilterProps $s)['isEnabled'] = $true
    $filterEtag = $s.Filters[$filterName].etag
    $r = Invoke-Observe -Params @{ Mode = 'Pause' } -Sim $s
    Assert-ObsOk $r 'Pause (enabled)'
    Assert-Observe ($r.Writes.Count -eq 1 -and (Get-ObsKind $r.Writes[0]) -ceq 'PATCH filter' -and $r.Writes[0].IfMatch -ceq $filterEtag -and $r.Writes[0].Body['properties']['isEnabled'] -eq $false) 'Pause must PATCH isEnabled false with the fresh ETag.'
    Assert-Observe ($r.Result['status'] -ceq 'paused' -and $r.Result['filter_enabled'] -eq $false -and $r.Result['definition_exact'] -eq $true -and $r.Result['filter'] -ceq 'updated') 'Pause must report paused and an exact definition.'
    $r = Invoke-Observe -Params @{ Mode = 'Pause' } -Sim $s
    Assert-Observe ($r.Ok -and $r.Writes.Count -eq 0 -and $r.Result['status'] -ceq 'paused' -and $r.Result['filter'] -ceq 'unchanged' -and $r.Result['filter_enabled'] -eq $false) 'Pause of a disabled filter must be unchanged.'
    $r = Invoke-Observe -Params @{ Mode = 'Pause' } -Sim (New-BoundSim)
    Assert-Observe ($r.Ok -and $r.Writes.Count -eq 0 -and $r.Result['status'] -ceq 'absent' -and $r.Result['filter_enabled'] -ceq 'absent') 'Pause of an absent filter must report absent and never create.'
    $s = New-BoundSim; Add-ObsFilter -Sim $s -Name $filterName -Enabled $true -HandlingAgent 'someone-else'
    Assert-ObsFail (Invoke-Observe -Params @{ Mode = 'Pause' } -Sim $s) 'Pause (foreign)' 'not owned by this handler' 0
    $s = New-PublishedSim
    $fd = Get-ObsFilterProps $s; $fd['isEnabled'] = $true; $fd['priorities'] = @('Sev0'); $s.Sticky = $true
    $r = Invoke-Observe -Params @{ Mode = 'Pause' } -Sim $s
    Assert-Observe ($r.Ok -and $r.Result['status'] -ceq 'paused' -and $r.Result['definition_exact'] -eq $false -and $r.Result['filter_enabled'] -eq $false) 'A drifted filter must be paused with definition_exact false.'
    # Minimal preconditions: no tool, no handler, no platform needed.
    $s = New-PublishedSim
    (Get-ObsFilterProps $s)['isEnabled'] = $true
    $s.Tool = $null; $s.Handler = $null; $s.Policy = $null; $s.Incident = $null
    $r = Invoke-Observe -Params @{ Mode = 'Pause' } -Sim $s
    Assert-Observe ($r.Ok -and $r.Result['status'] -ceq 'paused' -and $r.Writes.Count -eq 1) 'Pause must work with minimal preconditions.'
    $s = New-PublishedSim
    (Get-ObsFilterProps $s)['isEnabled'] = $true
    Add-ObsRule -Sim $s -Method 'PATCH' -Pattern '/incidentFilters/' -Status 500 -Code 'InternalError'
    Assert-ObsFail (Invoke-Observe -Params @{ Mode = 'Pause' } -Sim $s) 'Pause (HTTP 500)' 'pause it in the portal now'
    $s = New-PublishedSim
    (Get-ObsFilterProps $s)['isEnabled'] = $true
    Add-ObsRule -Sim $s -Method 'PATCH' -Pattern '/incidentFilters/' -After { param($res) $res['properties']['isEnabled'] = $true }
    Assert-ObsFail (Invoke-Observe -Params @{ Mode = 'Pause' } -Sim $s) 'Pause (not verified)' 'could not be verified: pause the filter in the portal now'
    $s = New-PublishedSim
    (Get-ObsFilterProps $s)['isEnabled'] = $true
    $r = Invoke-Observe -Params @{ Mode = 'Pause'; WhatIf = $true } -Sim $s
    Assert-Observe ($r.Ok -and $r.Result['status'] -ceq 'whatif' -and $r.Writes.Count -eq 0 -and (Get-ObsFilterProps $s)['isEnabled'] -eq $true) 'Pause -WhatIf must plan only.'
    $s = New-PublishedSim
    $s.Filters[$filterName]['resource']['properties']['isEnabled'] = 'maybe'
    Assert-ObsFail (Invoke-Observe -Params @{ Mode = 'Pause' } -Sim $s) 'Pause (unreadable)' 'not readable; pause it in the portal' 0

    # =========================================================================================================
    # Case 8 (P-10): identity is checked before any HTTP call
    # =========================================================================================================
    $s = New-BoundSim; $s.Account.id = [string]$altCtx['subscription_id']
    $r = Invoke-Observe -Params @{ Mode = 'Verify' } -Sim $s
    Assert-ObsFail $r 'Verify (other subscription)' 'sign-in failed.*not the enabled deployment subscription'
    Assert-Observe ($r.Calls.Count -eq 0) 'A different subscription must be refused before any HTTP call.'
    $s = New-BoundSim; $s.Account.tenantId = [string]$altCtx['tenant_id']
    $r = Invoke-Observe -Params @{ Mode = 'PublishDisabled' } -Sim $s
    Assert-ObsFail $r 'PublishDisabled (other tenant)' 'sign-in failed' 0
    Assert-Observe ($r.Calls.Count -eq 0) 'A different tenant must be refused before any HTTP call.'
    $r = Invoke-Observe -Params @{ Mode = 'Pause' } -Sim (New-ObsSim -Ctx $synCtx)
    Assert-Observe ($r.Ok) 'Pause on a clean sim must work.'
    $s = New-BoundSim; $s.Account.id = [string]$altCtx['subscription_id']
    $r = Invoke-Observe -Params @{ Mode = 'Pause' } -Sim $s
    Assert-ObsFail $r 'Pause (other subscription)' 'sign-in failed'
    Assert-Observe ($r.Calls.Count -eq 0) 'Pause must also refuse a different subscription before any HTTP call.'

    $global:ObsTest.OriginalLoader = (Get-Item -Path 'Function:\Get-AsreTestContext').ScriptBlock
    function global:Get-AsreTestContext {
        [CmdletBinding()]
        param([string]$Path, [switch]$AllowSynthetic)
        $loaded = & $global:ObsTest.OriginalLoader @PSBoundParameters
        if ($global:ObsTest.AgentOverride) { $loaded['agent']['resource_id'] = $loaded['agent']['resource_id'] + 'x' }
        return $loaded
    }
    try {
        $global:ObsTest.AgentOverride = $true
        $r = Invoke-Observe -Params @{ Mode = 'Verify' } -Sim (New-BoundSim)
        Assert-ObsFail $r 'Verify (agent id differs from the context)' 'sign-in failed.*The agent plane needs a context'
        Assert-Observe ($r.Calls.Count -eq 0 -and $r.Az.Count -eq 0) 'An agent id that differs from the context must be refused before any az or HTTP call.'
    }
    finally {
        $global:ObsTest.AgentOverride = $false
        Set-Item -Path 'Function:\global:Get-AsreTestContext' -Value $global:ObsTest.OriginalLoader
    }
    $s = New-BoundSim; $s.Endpoint = 'https://other-agent.localtest.centralus.azuresre.ai'; $s.EndpointHost = 'other-agent.localtest.centralus.azuresre.ai'
    $r = Invoke-Observe -Params @{ Mode = 'Verify' } -Sim $s
    Assert-ObsFail $r 'Verify (endpoint differs from the context)' 'The agent endpoint differs from the deployment context'
    Assert-Observe ($r.Calls.Count -eq 1) 'An endpoint mismatch must stop after the agent read.'

    # =========================================================================================================
    # Case 9 (P-0, P-15): confinement and hygiene
    # =========================================================================================================
    $verboseRun = Invoke-Observe -Params @{ Mode = 'PublishDisabled' } -Sim (New-BoundSim) -WithVerbose
    Assert-ObsOk $verboseRun 'PublishDisabled -Verbose'
    $allWrites = @($global:ObsTest.AllCalls | Where-Object { $_.Method -cne 'GET' })
    Assert-Observe ($allWrites.Count -gt 40) 'The scenarios must have exercised many writes.'
    $agentIds = @($synCtx['agent']['resource_id'], $altCtx['agent']['resource_id'], $minimalCtx['agent']['resource_id'])
    foreach ($call in $allWrites) {
        $kind = Get-ObsKind $call
        $allowed = switch -CaseSensitive ($kind) {
            'PATCH agent' { $agentIds -contains $call.Path }
            'PUT handler' { $call.Path -cmatch "\A/api/v2/extendedAgent/agents/$([regex]::Escape($handlerName))\z" }
            'PUT permissions' { $call.Path -cmatch "\A/api/v2/extendedAgent/$([regex]::Escape($handlerName))/permissions\z" }
            'PUT filter' { $call.Path -cmatch "\A/api/v2/incidentManagement/incidentFilters/$([regex]::Escape($filterName))\z" }
            'PATCH filter' { $call.Path -cmatch "\A/api/v2/incidentManagement/incidentFilters/$([regex]::Escape($filterName))\z" }
            default { $false }
        }
        Assert-Observe ([bool]$allowed) "Every write must be an allowed route (got: $kind)."
        Assert-Observe ($call.Method -cne 'DELETE' -and $call.Path -notmatch '/(tools|skills|commonprompts)(/|\z)') 'No write may delete or touch tools, skills or common prompts.'
    }
    Assert-Observe (@($global:ObsTest.AllCalls | Where-Object { $_.Method -cnotin @('GET', 'PUT', 'PATCH') }).Count -eq 0) 'Only GET, PUT and PATCH may be used.'
    Assert-Observe (@($global:ObsTest.AllCalls | Where-Object { $_.Host -cne 'management.azure.com' -and $_.Host -cnotmatch '\.azuresre\.ai\z|\.localtest\.centralus\.azuresre\.ai\z' }).Count -eq 0) 'Every request must go to ARM or the agent endpoint.'
    Assert-Observe ((Get-Command Invoke-WebRequest).CommandType -eq 'Function' -and (Get-Command az).CommandType -eq 'Function') 'Every call must have been served by the shadows.'
    $everything = ($global:ObsTest.AllText -join "`n")
    $secrets = @($global:ObsTest.Token, $global:ObsTest.Marker, 'Bearer')
    foreach ($secret in $secrets) {
        Assert-Observe (-not $everything.Contains($secret)) 'No token or response body marker may appear in any output stream.'
        Assert-Observe (-not (($global:ObsTest.VerboseText -join "`n").Contains($secret))) 'No token or response body marker may appear in verbose output either.'
    }
    foreach ($ctxCheck in @($synCtx, $altCtx)) {
        foreach ($value in @($ctxCheck['subscription_id'], $ctxCheck['tenant_id'], $ctxCheck['log_analytics']['resource_id'], $ctxCheck['agent']['resource_id'])) {
            Assert-Observe (-not $everything.Contains([string]$value)) 'No context identifier may appear in any default output stream.'
        }
    }
    Assert-Observe ($global:ObsTest.Unexpected.Count -eq 0) 'No scenario may have reached an unexpected route.'
    Assert-Observe ($null -eq (Get-Command -Name Get-AsreContext -CommandType Alias -ErrorAction SilentlyContinue)) 'The test alias must be gone.'
}
finally {
    Clear-AsreTestContext
    if ($global:ObsTest.ContainsKey('OriginalLoader') -and $global:ObsTest.OriginalLoader) {
        Set-Item -Path 'Function:\global:Get-AsreTestContext' -Value $global:ObsTest.OriginalLoader
    }
    Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Variable -Name ObsTest -Scope Global -ErrorAction SilentlyContinue
}

Write-Output "Observe workflow tests passed ($($script:assertions) assertions)."
