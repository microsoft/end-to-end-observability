$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repo = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot 'TestContext.Common.ps1')
. (Join-Path $repo 'scripts\Initialize-OperationsConnector.ps1') -DefinitionsOnly
$script:assertions = 0
function Assert-Connector {
    param([bool]$Condition, [string]$Message)
    $script:assertions++
    if (-not $Condition) { throw $Message }
}
function Assert-ConnectorRejected {
    param([scriptblock]$Run, [string]$Pattern)
    $rejected = $false
    $message = 'accepted'
    try { & $Run | Out-Null }
    catch { $message = $_.Exception.Message; $rejected = $message -match $Pattern }
    Assert-Connector $rejected "Expected rejection '$Pattern' but got: $message"
}

function New-TestPlane {
    param($Context, [bool]$ReadOnly = $false)
    @{
        agent = $Context.agent.resource_id; readOnly = $ReadOnly
        armHeaders = @{ Authorization = 'Bearer arm-header-for-test' }
        sreHeaders = @{ Authorization = 'Bearer sre-header-for-test' }
        sreEndpoint = 'https://test-agent.centralus.azuresre.ai'; context = $Context
    }
}
function New-TestWorkspace {
    param($Context)
    @{ id = $Context.log_analytics.resource_id; name = $Context.log_analytics.name; properties = @{ customerId = $Context.log_analytics.customer_id } }
}
function New-TestAgent {
    param($Context)
    @{ properties = @{
            agentEndpoint = $(if ($Context.agent.endpoint) { $Context.agent.endpoint } else { 'https://test-agent.centralus.azuresre.ai' })
            actionConfiguration = @{ identity = $Context.identities.agent.resource_id; mode = 'Review'; accessLevel = 'Low' }
        } }
}

# ------------------------------------------------------------------ wire level: the real request function over the real plane
function Test-ConnectorWire {
    param([string]$Name)
    $ctx = Get-AsreContext
    $agentId = $ctx.agent.resource_id
    $identity = $ctx.identities.agent.resource_id
    $workspace = New-TestWorkspace $ctx
    $plane = New-TestPlane $ctx
    $operation = New-OperationsConnectorContext -Context $ctx -Plane $plane
    Assert-Connector ($operation.agent -ceq $agentId -and $operation.identity -ceq $identity -and
        $operation.workspaceId -ceq $ctx.log_analytics.resource_id -and $operation.workspaceName -ceq $ctx.log_analytics.name -and -not $operation.readOnly) "[$Name] The operation context must come from the deployment context."
    Assert-ConnectorRejected { New-OperationsConnectorContext -Context $ctx -Plane @{ agent = 'foreign' } } 'restricted'
    Assert-ConnectorRejected { New-OperationsConnectorContext -Context @{ agent = @{ resource_id = $agentId } } -Plane $plane } 'needs the agent'

    $definitions = @(Get-OperationsConnectorDefinitions $workspace $identity)
    Assert-Connector ($definitions.Count -eq 2) "[$Name] Expected native workspace plus Monitor backend, not four servers."
    Assert-Connector ($definitions[0].dataConnectorType -ceq 'LogAnalytics') "[$Name] Log Analytics must not be declared as an ADX cluster."
    Assert-Connector ($definitions[0].extendedProperties.workspace.customerId -ceq $ctx.log_analytics.customer_id) "[$Name] Use the actual workspace customer ID."
    Assert-Connector ($definitions[0].dataSource -ceq $ctx.log_analytics.resource_id -and $definitions[0].extendedProperties.workspace.name -ceq $ctx.log_analytics.name) "[$Name] The workspace descriptor must come from the context workspace."
    Assert-Connector ($definitions[1].name -ceq 'monitor-client' -and $definitions[1].dataConnectorType -ceq 'MonitorClient') "[$Name] Use the native companion resource."
    Assert-Connector ($definitions[1].identity -ceq $identity -and $definitions[0].identity -ceq $identity) "[$Name] The MCP backend must use the existing action identity."

    $script:wireStatus = 200
    $script:wireContent = '{"properties":{}}'
    $script:wireGetContent = $null
    $script:wireCalls = @()
    function Invoke-WebRequest {
        [CmdletBinding()]
        param($Uri, $Method, $Headers, $MaximumRedirection, $TimeoutSec, $SkipHttpErrorCheck, $Body, $ContentType)
        $script:wireCalls += @{ uri = $Uri; method = $Method; body = $Body; redirects = $MaximumRedirection }
        @{ StatusCode = $script:wireStatus; Content = $(if ($Method -eq 'Get' -and $script:wireGetContent) { $script:wireGetContent } else { $script:wireContent }) }
    }
    Assert-ConnectorRejected { Invoke-OperationsConnectorRequest -Context @{ agent = 'foreign'; plane = $plane } -Resource Agent } 'restricted'
    Assert-ConnectorRejected { Invoke-OperationsConnectorRequest -Context @{ agent = $agentId } -Resource Agent } 'restricted'
    Assert-Connector ($script:wireCalls.Count -eq 0) "[$Name] A foreign scope must fail before HTTP."
    Assert-ConnectorRejected { Invoke-OperationsConnectorRequest -Context $operation -Resource Agent -Method Put -Properties @{ name = 'Agent' } } 'Only the two'
    $readOnlyOperation = New-OperationsConnectorContext -Context $ctx -Plane (New-TestPlane $ctx $true)
    Assert-Connector $readOnlyOperation.readOnly "[$Name] A read-only plane must mark the operation context read-only."
    Assert-ConnectorRejected { Invoke-OperationsConnectorRequest -Context $readOnlyOperation -Resource monitor-client -Method Put -Properties $definitions[1] } 'Only the two'
    Assert-ConnectorRejected { Invoke-OperationsConnectorRequest -Context @{ agent = $agentId; readOnly = $true; plane = $plane } -Resource monitor-client -Method Put -Properties $definitions[1] } 'Only the two'
    Assert-ConnectorRejected { Invoke-OperationsConnectorRequest -Context $operation -Resource monitor-client -Method Put -Properties $definitions[0] } 'Only the two'
    Assert-ConnectorRejected { Invoke-OperationsConnectorRequest -Context $operation -Resource Workspace -Method Put -Properties @{ name = 'Workspace' } } 'Only the two'
    Assert-Connector ($script:wireCalls.Count -eq 0) "[$Name] Rejected writes must not reach the wire."

    Invoke-OperationsConnectorRequest -Context $operation -Resource asre-operations-logs -Method Put -Properties $definitions[0]
    $wire = $script:wireCalls[-1]
    Assert-Connector ($wire.method -ceq 'Put' -and $wire.uri -ceq "https://management.azure.com$agentId/DataConnectors/asre-operations-logs?api-version=2025-05-01-preview") "[$Name] Unexpected connector API scope/version."
    Assert-Connector ($wire.redirects -eq 0) "[$Name] Redirects must stay disabled."
    $body = $wire.body | ConvertFrom-Json -AsHashtable
    Assert-Connector ($body.Keys.Count -eq 1 -and $body.properties.dataConnectorType -ceq 'LogAnalytics' -and $body.properties.identity -ceq $identity) "[$Name] Use the supported ARM properties envelope."

    $script:wireContent = @{ properties = $definitions[0] } | ConvertTo-Json -Depth 12
    $masked = $script:wireContent | ConvertFrom-Json -AsHashtable
    $masked.properties.dataSource = $null
    $masked.properties.extendedProperties = @{ workspace = $null; armResourceId = $null }
    $script:wireGetContent = $masked | ConvertTo-Json -Depth 12
    $hydrated = Invoke-OperationsConnectorRequest -Context $operation -Resource asre-operations-logs
    Assert-Connector ($hydrated.properties.extendedProperties.workspace.customerId -ceq $ctx.log_analytics.customer_id) "[$Name] Hydrate the redacted workspace descriptor."
    Assert-Connector ($script:wireCalls[-1].method -ceq 'Post' -and $script:wireCalls[-1].uri -ceq "https://management.azure.com$agentId/DataConnectors/asre-operations-logs/ListSecrets?api-version=2025-05-01-preview") "[$Name] Read configuration only for the named owned connector."
    Assert-Connector ($script:wireCalls[-1].body -ceq '{}') "[$Name] ListSecrets must post an empty object."
    # A read-only plane still hydrates (ListSecrets reads), but never sends the PUT.
    $count = $script:wireCalls.Count
    $hydrated = Invoke-OperationsConnectorRequest -Context $readOnlyOperation -Resource asre-operations-logs
    Assert-Connector ($hydrated.properties.extendedProperties.workspace.customerId -ceq $ctx.log_analytics.customer_id -and $script:wireCalls.Count -eq $count + 2) "[$Name] A read-only plane must hydrate with one GET and one ListSecrets POST."
    Assert-Connector ($readOnlyOperation.plane['readOnly'] -eq $true -and $script:wireCalls[-1].method -ceq 'Post') "[$Name] Hydration must not make the read-only plane writable."
    Assert-ConnectorRejected { Invoke-OperationsConnectorRequest -Context $operation -Resource Agent -Method ReadConfiguration } 'restricted'
    Assert-ConnectorRejected { Invoke-OperationsConnectorRequest -Context $operation -Resource Workspace -Method ReadConfiguration } 'restricted'

    $masked.properties.identity = 'foreign'
    $script:wireGetContent = $masked | ConvertTo-Json -Depth 12
    $before = $script:wireCalls.Count
    Assert-ConnectorRejected { Invoke-OperationsConnectorRequest -Context $operation -Resource asre-operations-logs } 'foreign connector'
    Assert-Connector ($script:wireCalls.Count -eq $before + 1) "[$Name] Foreign configuration must not be hydrated."
    $masked.properties.identity = $identity
    $masked.properties.dataConnectorType = 'MonitorClient'
    $script:wireGetContent = $masked | ConvertTo-Json -Depth 12
    Assert-ConnectorRejected { Invoke-OperationsConnectorRequest -Context $operation -Resource asre-operations-logs } 'foreign connector'

    # the plane keeps every ARM call inside the deployment subscription and the agent resource
    $other = $operation.Clone()
    $other.workspaceId = $ctx.log_analytics.resource_id.Replace($ctx.subscription_id, [guid]::NewGuid().ToString())
    $before = $script:wireCalls.Count
    Assert-ConnectorRejected { Invoke-OperationsConnectorRequest -Context $other -Resource Workspace } 'inside the deployment subscription'
    $other.workspaceId = '/subscriptions/x/resourceGroups/y/providers/Microsoft.Storage/storageAccounts/z'
    Assert-ConnectorRejected { Invoke-OperationsConnectorRequest -Context $other -Resource Workspace } 'workspace of the deployment context'
    Assert-Connector ($script:wireCalls.Count -eq $before) "[$Name] A foreign workspace must fail before HTTP."

    $script:wireGetContent = $null
    $script:wireStatus = 404
    Assert-Connector ($null -eq (Invoke-OperationsConnectorRequest -Context $operation -Resource asre-operations-logs -AllowNotFound)) "[$Name] Only an absent named connector is allowed."
    Assert-ConnectorRejected { Invoke-OperationsConnectorRequest -Context $operation -Resource asre-operations-logs } 'HTTP 404'
    Assert-ConnectorRejected { Invoke-OperationsConnectorRequest -Context $operation -Resource Workspace -AllowNotFound } 'HTTP 404'
    Assert-ConnectorRejected { Invoke-OperationsConnectorRequest -Context $operation -Resource Agent -AllowNotFound } 'HTTP 404'
    $script:wireStatus = 403
    $script:wireContent = '{"error":{"code":"AuthorizationFailed","message":"leaky-body-marker"}}'
    Assert-ConnectorRejected { Invoke-OperationsConnectorRequest -Context $operation -Resource asre-operations-logs -AllowNotFound } 'HTTP 403'
    $text = ''
    try { Invoke-OperationsConnectorRequest -Context $operation -Resource asre-operations-logs -AllowNotFound } catch { $text = $_.Exception.Message }
    Assert-Connector ($text -and -not $text.Contains('leaky-body-marker') -and -not $text.Contains('arm-header-for-test')) "[$Name] Failures must not echo response bodies or tokens."
    $script:wireStatus = 200
    $script:wireContent = 'not-json'
    Assert-ConnectorRejected { Invoke-OperationsConnectorRequest -Context $operation -Resource Agent } 'JSON'
    $script:wireContent = '[]'
    Assert-ConnectorRejected { Invoke-OperationsConnectorRequest -Context $operation -Resource Agent } 'JSON object'
}

# ------------------------------------------------------------------ logic level: the initializer over a faked request function
function Test-ConnectorLogic {
    param([string]$Name)
    $ctx = Get-AsreContext
    $identity = $ctx.identities.agent.resource_id
    $script:workspace = New-TestWorkspace $ctx
    $script:agent = New-TestAgent $ctx
    $operation = New-OperationsConnectorContext -Context $ctx -Plane (New-TestPlane $ctx)
    $definitions = @(Get-OperationsConnectorDefinitions $script:workspace $identity)
    $script:store = @{}
    $script:writes = @()
    $script:written = @()
    $script:corruptReadback = $false
    function Invoke-OperationsConnectorRequest {
        param($Context, $Resource, $Method = 'Get', $Properties, [switch]$AllowNotFound)
        if ($Method -eq 'Put') {
            $script:writes += $Resource
            $copy = $Properties | ConvertTo-Json -Depth 12 | ConvertFrom-Json -AsHashtable
            $script:written += ($copy | ConvertTo-Json -Depth 12)
            if ($script:corruptReadback) { $copy.identity = 'wrong' }
            $copy.provisioningState = 'Succeeded'
            $script:store[$Resource] = @{ properties = $copy }
            return
        }
        switch ($Resource) {
            Agent { return $script:agent }
            Workspace { return $script:workspace }
            default { if ($script:store.ContainsKey($Resource)) { return $script:store[$Resource] }; return $null }
        }
    }
    function Start-Sleep { param($Seconds) }
    $result = Initialize-OperationsConnector -Context $operation
    Assert-Connector ($result.status -ceq 'configuration-verified' -and $script:writes.Count -eq 2) "[$Name] Both resources must be published and read back."
    Assert-Connector (-not $result.runtime_verified) "[$Name] Metadata is not a successful query or report."
    Assert-Connector ($result.workspace_resource_id -ceq $ctx.log_analytics.resource_id -and $result.workspace_customer_id -ceq $ctx.log_analytics.customer_id -and $result.identity_resource_id -ceq $identity) "[$Name] The result must report the context workspace and identity."
    Assert-Connector ((($script:written -join ' ').Contains($identity)) -and (($script:written -join ' ').Contains($ctx.log_analytics.resource_id))) "[$Name] Published connectors must carry the context identity and workspace."
    $script:writes = @()
    $result = Initialize-OperationsConnector -Context $operation
    Assert-Connector ($result.changed.Count -eq 0 -and $script:writes.Count -eq 0) "[$Name] Re-running must be a no-op."
    $result = Initialize-OperationsConnector -Context $operation -VerifyOnly
    Assert-Connector ($result.status -ceq 'configuration-verified' -and $script:writes.Count -eq 0) "[$Name] Verify must never publish."
    $script:store['asre-operations-logs'].properties.Remove('dataSource')
    Assert-Connector (Test-OperationsConnectorProperties $definitions[0] $script:store['asre-operations-logs'].properties) "[$Name] Redacted dataSource must use the complete verified workspace descriptor."
    $script:store['asre-operations-logs'].properties.extendedProperties.workspace.customerId = '66666666-2222-3333-4444-555555555555'
    Assert-ConnectorRejected { Initialize-OperationsConnector -Context $operation -VerifyOnly } 'missing or differs'
    $result = Initialize-OperationsConnector -Context $operation
    Assert-Connector (($script:writes -join ',') -ceq 'asre-operations-logs') "[$Name] Only the changed customer ID should be republished."
    $script:writes = @()
    foreach ($field in @('identity', 'dataConnectorType')) {
        $original = $script:store['monitor-client'].properties[$field]
        $script:store['monitor-client'].properties[$field] = 'foreign'
        Assert-ConnectorRejected { Initialize-OperationsConnector -Context $operation } 'foreign connector'
        Assert-Connector ($script:writes.Count -eq 0) "[$Name] Never replace a foreign connector."
        $script:store['monitor-client'].properties[$field] = $original
    }
    $original = $script:store['asre-operations-logs'].properties.extendedProperties.armResourceId
    $script:store['asre-operations-logs'].properties.extendedProperties.armResourceId = $ctx.log_analytics.resource_id + '-foreign'
    Assert-ConnectorRejected { Initialize-OperationsConnector -Context $operation } 'foreign connector'
    Assert-Connector ($script:writes.Count -eq 0) "[$Name] A connector bound to another workspace must not be replaced."
    $script:store['asre-operations-logs'].properties.extendedProperties.armResourceId = $original
    $script:store['monitor-client'].properties.provisioningState = 'Failed'
    Assert-ConnectorRejected { Initialize-OperationsConnector -Context $operation -VerifyOnly } 'provisioning failed'
    $script:store['monitor-client'].properties.provisioningState = 'Succeeded'
    foreach ($case in @(
            @{ field = 'identity'; value = 'foreign' }, @{ field = 'mode'; value = 'Autonomous' }, @{ field = 'accessLevel'; value = 'High' })) {
        $original = $script:agent.properties.actionConfiguration[$case.field]
        $script:agent.properties.actionConfiguration[$case.field] = $case.value
        Assert-ConnectorRejected { Initialize-OperationsConnector -Context $operation } 'existing Review/Low'
        $script:agent.properties.actionConfiguration[$case.field] = $original
    }
    $original = $script:workspace.id
    $script:workspace.id = $original + '-foreign'
    Assert-ConnectorRejected { Initialize-OperationsConnector -Context $operation } 'existing Review/Low'
    $script:workspace.id = $original
    $original = $script:workspace.name
    $script:workspace.name = 'foreign-workspace'
    Assert-ConnectorRejected { Initialize-OperationsConnector -Context $operation } 'existing Review/Low'
    $script:workspace.name = $original
    Assert-ConnectorRejected { Initialize-OperationsConnector -Context @{ agent = $ctx.agent.resource_id } } 'needs the agent'
    $script:store = @{}
    $result = Initialize-OperationsConnector -Context $operation -WhatIf
    Assert-Connector ($result.status -ceq 'planned' -and $script:writes.Count -eq 0) "[$Name] WhatIf must not claim publication or write."
    Assert-ConnectorRejected { Initialize-OperationsConnector -Context $operation -VerifyOnly } 'missing or differs'
    $script:corruptReadback = $true
    Assert-ConnectorRejected { Initialize-OperationsConnector -Context $operation } 'readback was not verified'
    $original = $script:workspace.properties.customerId
    $script:workspace.properties.customerId = 'not-a-guid'
    Assert-ConnectorRejected { Get-OperationsConnectorDefinitions $script:workspace $identity } 'valid customer ID'
    $script:workspace.properties.customerId = $original
}

# ------------------------------------------------------------------ script level: Get-AsreContext, the plane, mocked az and HTTP
function Test-ConnectorScript {
    param([string]$Name, [hashtable]$Other)
    $ctx = Get-AsreContext
    # The initializer runs as its own script file, so a mock must not rely on $script: (that is the child's scope).
    $global:ConnectorTestState = @{
        context = $ctx; connectors = @{}
        azCalls = [System.Collections.Generic.List[string]]::new()
        wire = [System.Collections.Generic.List[hashtable]]::new()
        agentDoc = New-TestAgent $ctx; workspaceDoc = New-TestWorkspace $ctx
    }
    $state = $global:ConnectorTestState
    $agentId = $ctx.agent.resource_id
    $identity = $ctx.identities.agent.resource_id
    function az {
        $shared = $global:ConnectorTestState
        $shared.azCalls.Add(($args -join ' '))
        $global:LASTEXITCODE = 0
        if ($args[0] -ceq 'account' -and $args[1] -ceq 'show') {
            return (@{ id = $shared.context.subscription_id; tenantId = $shared.context.tenant_id; state = 'Enabled' } | ConvertTo-Json)
        }
        if ($args[0] -ceq 'account' -and $args[1] -ceq 'get-access-token') { return 'test-token-value' }
        $global:LASTEXITCODE = 1
    }
    function Start-Sleep { param($Seconds) }
    function Invoke-WebRequest {
        [CmdletBinding()]
        param($Uri, $Method, $Headers, $MaximumRedirection, $TimeoutSec, $SkipHttpErrorCheck, $Body, $ContentType)
        $shared = $global:ConnectorTestState
        $shared.wire.Add(@{ uri = $Uri; method = $Method; body = $Body; authorized = ($Headers -is [hashtable] -and $Headers.ContainsKey('Authorization')) })
        $path = ([uri]$Uri).AbsolutePath
        $ok = { param($Value) @{ StatusCode = 200; Content = ($Value | ConvertTo-Json -Depth 12); Headers = @{} } }
        if ($Method -ceq 'Get' -and $path -ceq $shared.context.agent.resource_id) { return & $ok $shared.agentDoc }
        if ($Method -ceq 'Get' -and $path -ceq $shared.context.log_analytics.resource_id) { return & $ok $shared.workspaceDoc }
        if ($path -match '/DataConnectors/(?<name>[^/]+?)(?<list>/ListSecrets)?\z') {
            $connector = $Matches['name']
            $listing = [bool]$Matches['list']
            if ($Method -ceq 'Put') {
                $properties = ($Body | ConvertFrom-Json -AsHashtable).properties
                $properties.provisioningState = 'Succeeded'
                $shared.connectors[$connector] = @{ properties = $properties }
                return & $ok @{}
            }
            if (-not $shared.connectors.ContainsKey($connector)) { return @{ StatusCode = 404; Content = '{"error":{"code":"NotFound"}}'; Headers = @{} } }
            if ($Method -ceq 'Get') {
                $masked = $shared.connectors[$connector] | ConvertTo-Json -Depth 12 | ConvertFrom-Json -AsHashtable
                if ($masked.properties.ContainsKey('extendedProperties')) { $masked.properties.extendedProperties = @{ workspace = $null; armResourceId = $null } }
                return & $ok $masked
            }
            if ($Method -ceq 'Post' -and $listing) { return & $ok $shared.connectors[$connector] }
        }
        return @{ StatusCode = 500; Content = '{}'; Headers = @{} }
    }
    $scriptPath = Join-Path $repo 'scripts\Initialize-OperationsConnector.ps1'
    $mutations = { @($state.wire | Where-Object { $_.method -cne 'Get' }) }
    $last = { param($Output) @($Output | Where-Object { $_ -is [pscustomobject] })[-1] }

    # -VerifyOnly with nothing published fails and sends nothing but reads
    Assert-ConnectorRejected { & $scriptPath -VerifyOnly } 'missing or differs'
    Assert-Connector (@(& $mutations).Count -eq 0 -and $state.connectors.Count -eq 0) "[$Name] -VerifyOnly must not publish."
    Assert-Connector (@($state.wire | Where-Object { -not $_.authorized }).Count -eq 0 -and $state.wire.Count -ge 3) "[$Name] Every request must carry the plane credentials."
    # -WhatIf plans, never writes
    $state.wire.Clear()
    $result = & $last (& $scriptPath -WhatIf)
    Assert-Connector ($result.status -ceq 'planned' -and @(& $mutations).Count -eq 0 -and $state.connectors.Count -eq 0) "[$Name] -WhatIf must plan without writing."
    Assert-Connector ($result.workspace_resource_id -ceq $ctx.log_analytics.resource_id -and $result.workspace_customer_id -ceq $ctx.log_analytics.customer_id -and $result.identity_resource_id -ceq $identity) "[$Name] The plan must report the context workspace and identity."
    # a real run publishes both connectors under the agent resource
    $state.wire.Clear()
    $env:servicePrincipalId = 'ignored-pipeline-value'
    try { $result = & $last (& $scriptPath) }
    finally { Remove-Item Env:\servicePrincipalId -ErrorAction SilentlyContinue }
    Assert-Connector ($result.status -ceq 'configuration-verified' -and $state.connectors.Count -eq 2 -and @($result.changed).Count -eq 2) "[$Name] A run must publish and verify both connectors."
    $puts = @($state.wire | Where-Object { $_.method -ceq 'Put' })
    Assert-Connector ($puts.Count -eq 2 -and @($puts | Where-Object { $_.uri.StartsWith("https://management.azure.com$agentId/DataConnectors/") -and $_.uri.EndsWith('?api-version=2025-05-01-preview') }).Count -eq 2) "[$Name] Connectors must be published only under the agent resource."
    foreach ($connector in $state.connectors.Values) { Assert-Connector ($connector.properties.identity -ceq $identity) "[$Name] Connectors must use the context action identity." }
    $posts = @($state.wire | Where-Object { $_.method -notin @('Get', 'Put') })
    Assert-Connector (@($posts | Where-Object { $_.method -cne 'Post' -or $_.uri -notmatch '/ListSecrets\?' }).Count -eq 0) "[$Name] The only POST allowed is ListSecrets."
    # verification and a second run are read-only
    $state.wire.Clear()
    $result = & $last (& $scriptPath -VerifyOnly)
    Assert-Connector ($result.status -ceq 'configuration-verified' -and @($state.wire | Where-Object { $_.method -ceq 'Put' }).Count -eq 0) "[$Name] -VerifyOnly must verify without publishing."
    Assert-Connector (@($state.wire | Where-Object { $_.method -notin @('Get', 'Post') -or ($_.method -ceq 'Post' -and $_.uri -notmatch '/ListSecrets\?') }).Count -eq 0) "[$Name] -VerifyOnly may only read and hydrate."
    Assert-Connector (@($state.wire | Where-Object { $_.method -ceq 'Post' }).Count -eq 4) "[$Name] -VerifyOnly must hydrate each connector for the check and again for the readback."
    $state.wire.Clear()
    $result = & $last (& $scriptPath)
    Assert-Connector (@($result.changed).Count -eq 0 -and @($state.wire | Where-Object { $_.method -ceq 'Put' }).Count -eq 0) "[$Name] Re-running must be a no-op."
    # a wrong agent mode fails before any connector read
    $state.agentDoc.properties.actionConfiguration.mode = 'Autonomous'
    Assert-ConnectorRejected { & $scriptPath -VerifyOnly } 'existing Review/Low'
    $state.agentDoc.properties.actionConfiguration.mode = 'Review'

    # every az call is bound to the context subscription; nothing but the CLI sign-in and the plane is used
    foreach ($call in $state.azCalls) {
        Assert-Connector ($call -cmatch '\Aaccount (show|get-access-token) ' -and $call.Contains("--subscription $($ctx.subscription_id)")) "[$Name] Every az call must be an account call bound to the context subscription."
    }
    Assert-Connector ($state.azCalls.Count -ge 5) "[$Name] The script must sign in through the CLI."
    $everything = (@($state.azCalls) + @($state.wire | ForEach-Object { "$($_.uri) $($_.body)" })) -join "`n"
    Assert-Connector (-not $everything.Contains('test-token-value')) "[$Name] A token must never appear in a request, a body or a log."
    if ($Other) {
        $leaf = Get-AsreContextLeaf -Context $ctx
        $otherLeaf = Get-AsreContextLeaf -Context (ConvertTo-AsreContext -Json (ConvertTo-Json -InputObject $Other -Depth 10))
        $lower = $everything.ToLowerInvariant()
        foreach ($entry in $otherLeaf.GetEnumerator()) {
            if (-not $leaf.ContainsKey($entry.Key) -or $leaf[$entry.Key] -cne $entry.Value) {
                Assert-Connector (-not $lower.Contains($entry.Value.ToLowerInvariant())) "[$Name] A value of the other context ($($entry.Key)) leaked into the requests."
            }
        }
    }
}

try {
    $synthetic = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'fixtures\context.synthetic.json') -Raw | ConvertFrom-Json -AsHashtable
    $alternative = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'fixtures\context.alt.json') -Raw | ConvertFrom-Json -AsHashtable
    foreach ($pair in @(@('synthetic', $alternative), @('alt', $synthetic))) {
        Use-AsreTestContext -Name $pair[0] | Out-Null
        Test-ConnectorWire -Name $pair[0]
        Test-ConnectorLogic -Name $pair[0]
        Test-ConnectorScript -Name $pair[0] -Other $pair[1]
    }
}
finally {
    Clear-AsreTestContext
    Remove-Item Env:\servicePrincipalId -ErrorAction SilentlyContinue
}

$initializer = Get-Content -LiteralPath (Join-Path $repo 'scripts\Initialize-ReviewAgent.ps1') -Raw
Assert-Connector ($initializer.Contains("'Initialize-OperationsConnector.ps1'")) 'Full deployment must configure connectors.'
$connectorSource = Get-Content -LiteralPath (Join-Path $repo 'scripts\Initialize-OperationsConnector.ps1') -Raw
foreach ($forbidden in @('servicePrincipalId', 'idToken', 'ReviewAgent.Common', 'Invoke-Expression', 'az rest', '##vso')) {
    Assert-Connector (-not $connectorSource.Contains($forbidden)) "The connector script must not use $forbidden."
}
Assert-Connector (-not ($connectorSource -cmatch '(?i)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')) 'The connector script must not carry a GUID literal.'
Assert-Connector ($connectorSource.Contains('Get-AsreContext') -and $connectorSource.Contains('Get-AsreAgentPlane') -and $connectorSource.Contains('Clear-AsreAgentPlane')) 'The connector script must use the deployment context and the agent plane.'
Write-Output "Operations connector fixtures passed: $script:assertions assertions; no Azure calls."
