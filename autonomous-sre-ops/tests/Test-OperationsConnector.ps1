$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repo = Split-Path $PSScriptRoot -Parent
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
    try { & $Run | Out-Null }
    catch { $rejected = $_.Exception.Message -match $Pattern }
    Assert-Connector $rejected "Expected rejection: $Pattern"
}
$script:agentId = '/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.App/agents/asre-demo-cus'
$script:identity = '/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-asre-agent-cus'
$script:workspace = @{
    id = '/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.OperationalInsights/workspaces/law-asre-cus'
    name = 'law-asre-cus'; properties = @{ customerId = '11111111-2222-3333-4444-555555555555' }
}
$script:agent = @{ properties = @{ actionConfiguration = @{ identity = $script:identity; mode = 'Review'; accessLevel = 'Low' } } }
$context = @{ agent = $script:agentId; readOnly = $false; armHeaders = @{} }
$definitions = @(Get-OperationsConnectorDefinitions $script:workspace $script:identity)
Assert-Connector ($definitions.Count -eq 2) 'Expected native workspace plus Monitor backend, not four servers.'
Assert-Connector ($definitions[0].dataConnectorType -ceq 'LogAnalytics') 'Log Analytics must not be declared as an ADX cluster.'
Assert-Connector ($definitions[0].extendedProperties.workspace.customerId -ceq $script:workspace.properties.customerId) 'Use the actual workspace customer ID.'
Assert-Connector ($definitions[1].name -ceq 'monitor-client' -and $definitions[1].dataConnectorType -ceq 'MonitorClient') 'Use the native companion resource.'
Assert-Connector ($definitions[1].identity -ceq $script:identity) 'The MCP backend must use the existing action identity.'

$script:wireStatus = 200
$script:wireContent = '{"properties":{}}'
$script:wireGetContent = $null
$script:wireCalls = @()
function Invoke-WebRequest {
    param($Uri, $Method, $Headers, $MaximumRedirection, $TimeoutSec, $SkipHttpErrorCheck, $Body, $ContentType)
    $script:wireCalls += @{ uri = $Uri; method = $Method; body = $Body }
    @{ StatusCode = $script:wireStatus; Content = $(if ($Method -eq 'Get' -and $script:wireGetContent) { $script:wireGetContent } else { $script:wireContent }) }
}
$request = ${function:Invoke-OperationsConnectorRequest}
Assert-ConnectorRejected { & $request -Context @{ agent = 'foreign' } -Resource Agent } 'restricted'
Assert-Connector ($script:wireCalls.Count -eq 0) 'A foreign scope must fail before HTTP.'
Assert-ConnectorRejected { & $request -Context $context -Resource Agent -Method Put -Properties @{name='Agent'} } 'Only the two'
Assert-ConnectorRejected { & $request -Context @{agent=$script:agentId;readOnly=$true} -Resource monitor-client -Method Put -Properties $definitions[1] } 'Only the two'
Assert-ConnectorRejected { & $request -Context $context -Resource monitor-client -Method Put -Properties $definitions[0] } 'Only the two'
& $request -Context $context -Resource asre-operations-logs -Method Put -Properties $definitions[0]
$wire = $script:wireCalls[-1]
Assert-Connector ($wire.uri -ceq "https://management.azure.com$script:agentId/DataConnectors/asre-operations-logs?api-version=2025-05-01-preview") 'Unexpected connector API scope/version.'
$body = $wire.body | ConvertFrom-Json -AsHashtable
Assert-Connector ($body.Keys.Count -eq 1 -and $body.properties.dataConnectorType -ceq 'LogAnalytics') 'Use the supported ARM properties envelope.'
$script:wireContent = @{properties=$definitions[0]} | ConvertTo-Json -Depth 12
$masked = $script:wireContent | ConvertFrom-Json -AsHashtable
$masked.properties.dataSource = $null
$masked.properties.extendedProperties = @{workspace=$null;armResourceId=$null}
$script:wireGetContent = $masked | ConvertTo-Json -Depth 12
$hydrated = & $request -Context $context -Resource asre-operations-logs
Assert-Connector ($hydrated.properties.extendedProperties.workspace.customerId -ceq $script:workspace.properties.customerId) 'Hydrate the redacted workspace descriptor.'
Assert-Connector ($script:wireCalls[-1].method -ceq 'Post' -and $script:wireCalls[-1].uri.EndsWith('/asre-operations-logs/ListSecrets?api-version=2025-05-01-preview')) 'Read configuration only for the named owned connector.'
Assert-ConnectorRejected { & $request -Context $context -Resource Agent -Method ReadConfiguration } 'restricted'
$masked.properties.identity = 'foreign'
$script:wireGetContent = $masked | ConvertTo-Json -Depth 12
$before = $script:wireCalls.Count
Assert-ConnectorRejected { & $request -Context $context -Resource asre-operations-logs } 'foreign connector'
Assert-Connector ($script:wireCalls.Count -eq $before+1) 'Foreign configuration must not be hydrated.'
$script:wireGetContent = $null
$script:wireStatus = 404
Assert-Connector ($null -eq (& $request -Context $context -Resource asre-operations-logs -AllowNotFound)) 'Only an absent named connector is allowed.'
Assert-ConnectorRejected { & $request -Context $context -Resource Workspace -AllowNotFound } 'HTTP 404'
$script:wireStatus = 403
Assert-ConnectorRejected { & $request -Context $context -Resource asre-operations-logs -AllowNotFound } 'HTTP 403'
$script:wireStatus = 200
$script:wireContent = 'not-json'
Assert-ConnectorRejected { & $request -Context $context -Resource Agent } 'JSON'

$script:store = @{}
$script:writes = @()
$script:corruptReadback = $false
function Invoke-OperationsConnectorRequest {
    param($Context, $Resource, $Method = 'Get', $Properties, [switch]$AllowNotFound)
    if ($Method -eq 'Put') {
        $script:writes += $Resource
        $copy = $Properties | ConvertTo-Json -Depth 12 | ConvertFrom-Json -AsHashtable
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
$result = Initialize-OperationsConnector -Context $context
Assert-Connector ($result.status -ceq 'configuration-verified' -and $script:writes.Count -eq 2) 'Both resources must be published and read back.'
Assert-Connector (-not $result.runtime_verified) 'Metadata is not a successful query or report.'
$script:writes = @()
$result = Initialize-OperationsConnector -Context $context
Assert-Connector ($result.changed.Count -eq 0 -and $script:writes.Count -eq 0) 'Re-running must be a no-op.'
$result = Initialize-OperationsConnector -Context $context -VerifyOnly
Assert-Connector ($result.status -ceq 'configuration-verified' -and $script:writes.Count -eq 0) 'Verify must never publish.'
$script:store['asre-operations-logs'].properties.Remove('dataSource')
Assert-Connector (Test-OperationsConnectorProperties $definitions[0] $script:store['asre-operations-logs'].properties) 'Redacted dataSource must use the complete verified workspace descriptor.'
$script:store['asre-operations-logs'].properties.extendedProperties.workspace.customerId = '66666666-2222-3333-4444-555555555555'
Assert-ConnectorRejected { Initialize-OperationsConnector -Context $context -VerifyOnly } 'missing or differs'
$result = Initialize-OperationsConnector -Context $context
Assert-Connector (($script:writes -join ',') -ceq 'asre-operations-logs') 'Only the changed customer ID should be republished.'
$script:writes = @()
foreach ($field in @('identity', 'dataConnectorType')) {
    $original = $script:store['monitor-client'].properties[$field]
    $script:store['monitor-client'].properties[$field] = 'foreign'
    Assert-ConnectorRejected { Initialize-OperationsConnector -Context $context } 'foreign connector'
    Assert-Connector ($script:writes.Count -eq 0) 'Never replace a foreign connector.'
    $script:store['monitor-client'].properties[$field] = $original
}
$script:store['monitor-client'].properties.provisioningState = 'Failed'
Assert-ConnectorRejected { Initialize-OperationsConnector -Context $context -VerifyOnly } 'provisioning failed'
$script:store['monitor-client'].properties.provisioningState = 'Succeeded'
$original = $script:agent.properties.actionConfiguration.identity
$script:agent.properties.actionConfiguration.identity = 'foreign'
Assert-ConnectorRejected { Initialize-OperationsConnector -Context $context } 'existing Review/Low'
$script:agent.properties.actionConfiguration.identity = $original
$script:store = @{}
$result = Initialize-OperationsConnector -Context $context -WhatIf
Assert-Connector ($result.status -ceq 'planned' -and $script:writes.Count -eq 0) 'WhatIf must not claim publication or write.'
Assert-ConnectorRejected { Initialize-OperationsConnector -Context $context -VerifyOnly } 'missing or differs'
$script:corruptReadback = $true
Assert-ConnectorRejected { Initialize-OperationsConnector -Context $context } 'readback was not verified'
$original = $script:workspace.properties.customerId
$script:workspace.properties.customerId = 'not-a-guid'
Assert-ConnectorRejected { Get-OperationsConnectorDefinitions $script:workspace $script:identity } 'valid customer ID'
$script:workspace.properties.customerId = $original
$initializer = Get-Content -LiteralPath (Join-Path $repo 'scripts\Initialize-ReviewAgent.ps1') -Raw
$release = Get-Content -LiteralPath (Join-Path $repo 'scripts\Invoke-IncidentRelease.ps1') -Raw
Assert-Connector ($initializer.Contains("'Initialize-OperationsConnector.ps1'")) 'Full deployment must configure connectors.'
Assert-Connector ($release.Contains("'scripts/Initialize-OperationsConnector.ps1'")) 'The configuration artifact must include the helper.'
Write-Output "Operations connector fixtures passed: $script:assertions assertions; no Azure calls."
