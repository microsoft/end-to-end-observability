#requires -Version 7.5
[CmdletBinding(SupportsShouldProcess)]
param(
    [switch]$VerifyOnly,
    [switch]$DefinitionsOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Get-OperationsConnectorDefinitions {
    param([hashtable]$Workspace, [string]$Identity)
    $customerId = [guid]::Empty
    if (-not [guid]::TryParse($Workspace.properties.customerId, [ref]$customerId) -or
        $customerId -eq [guid]::Empty) {
        throw 'The actual workspace did not return a valid customer ID.'
    }
    @{
        name = 'asre-operations-logs'; dataConnectorType = 'LogAnalytics'
        dataSource = $Workspace.id; identity = $Identity
        extendedProperties = @{
            armResourceId = $Workspace.id
            workspace = @{ name = $Workspace.name; customerId = $customerId.ToString() }
        }
    }
    # The native workspace wizard also creates this read-only Azure Monitor MCP backend.
    @{
        name = 'monitor-client'; dataConnectorType = 'MonitorClient'
        dataSource = 'n/a'; identity = $Identity
    }
}

function Invoke-OperationsConnectorRequest {
    param(
        [hashtable]$Context,
        [ValidateSet('Agent', 'Workspace', 'asre-operations-logs', 'monitor-client')][string]$Resource,
        [ValidateSet('Get', 'Put', 'ReadConfiguration')][string]$Method = 'Get',
        [hashtable]$Properties,
        [switch]$AllowNotFound
    )
    $agent = '/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.App/agents/asre-demo-cus'
    if ($Context.agent -cne $agent) { throw 'Connector setup is restricted to the existing ASRE agent.' }
    switch ($Resource) {
        Agent { $path = "${agent}?api-version=2026-01-01" }
        Workspace {
            $path = '/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.OperationalInsights/workspaces/law-asre-cus?api-version=2023-09-01'
        }
        default { $path = "$agent/DataConnectors/${Resource}?api-version=2025-05-01-preview" }
    }
    if ($Method -eq 'ReadConfiguration') {
        if ($Resource -in @('Agent', 'Workspace')) { throw 'Configuration hydration is restricted to the two named connectors.' }
        $path = "$agent/DataConnectors/$Resource/ListSecrets?api-version=2025-05-01-preview"
    }
    if ($Method -eq 'Put' -and ($Context.readOnly -or $Resource -in @('Agent', 'Workspace') -or
        $null -eq $Properties -or $Properties.name -cne $Resource)) {
        throw 'Only the two named connector configurations may be published.'
    }
    $arguments = @{
        Uri = "https://management.azure.com$path"; Method = $(if ($Method -eq 'ReadConfiguration') { 'Post' } else { $Method })
        Headers = $Context.armHeaders; MaximumRedirection = 0; TimeoutSec = 60
        SkipHttpErrorCheck = $true
    }
    if ($Method -eq 'Put') {
        $arguments.Body = @{ properties = $Properties } | ConvertTo-Json -Depth 12
        $arguments.ContentType = 'application/json'
    }
    elseif ($Method -eq 'ReadConfiguration') {
        $arguments.Body = '{}'
        $arguments.ContentType = 'application/json'
    }
    $response = Invoke-WebRequest @arguments
    if ($response.StatusCode -eq 404 -and $AllowNotFound -and $Method -eq 'Get' -and
        $Resource -notin @('Agent', 'Workspace')) { return $null }
    if ($response.StatusCode -notin @(200, 201, 202)) {
        throw "Native connector $Method $Resource returned HTTP $($response.StatusCode)."
    }
    if ($Method -eq 'Put') { return }
    $data = $response.Content | ConvertFrom-Json -AsHashtable -DateKind String
    if ($Method -eq 'Get' -and $Resource -notin @('Agent', 'Workspace')) {
        $expectedType = if ($Resource -ceq 'asre-operations-logs') { 'LogAnalytics' } else { 'MonitorClient' }
        $identity = '/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-asre-agent-cus'
        if ($data.properties.name -cne $Resource -or $data.properties.dataConnectorType -cne $expectedType -or
            $data.properties.identity -cne $identity) {
            throw "Refusing to read configuration of a foreign connector: $Resource."
        }
        # GET redacts even the workspace descriptor. Hydrate only this owned MI connector, never the collection.
        return Invoke-OperationsConnectorRequest -Context $Context -Resource $Resource -Method ReadConfiguration
    }
    return $data
}

function Test-OperationsConnectorProperties {
    param([hashtable]$Expected, [hashtable]$Actual)
    foreach ($key in @('name', 'dataConnectorType', 'identity')) {
        if (-not $Actual.ContainsKey($key) -or $Actual[$key] -cne $Expected[$key]) { return $false }
    }
    # ARM can redact dataSource; the nonsecret workspace descriptor is also verified.
    if ($Actual.ContainsKey('dataSource') -and $Actual.dataSource -and
        $Actual.dataSource -cne $Expected.dataSource) { return $false }
    if ($Expected.dataConnectorType -ceq 'LogAnalytics') {
        if ($Actual.extendedProperties -isnot [hashtable] -or
            $Actual.extendedProperties.workspace -isnot [hashtable]) { return $false }
        $properties = $Actual.extendedProperties
        if ($properties.armResourceId -cne $Expected.extendedProperties.armResourceId -or
            $properties.workspace.name -cne $Expected.extendedProperties.workspace.name -or
            $properties.workspace.customerId -cne $Expected.extendedProperties.workspace.customerId) { return $false }
    }
    return $true
}

function Initialize-OperationsConnector {
    [CmdletBinding(SupportsShouldProcess)]
    param([hashtable]$Context, [switch]$VerifyOnly)
    $identity = '/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-asre-agent-cus'
    $workspaceId = '/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.OperationalInsights/workspaces/law-asre-cus'
    $agent = Invoke-OperationsConnectorRequest -Context $Context -Resource Agent
    $workspace = Invoke-OperationsConnectorRequest -Context $Context -Resource Workspace
    if ($agent.properties.actionConfiguration.identity -cne $identity -or
        $agent.properties.actionConfiguration.mode -cne 'Review' -or
        $agent.properties.actionConfiguration.accessLevel -cne 'Low' -or
        $workspace.id -ine $workspaceId -or $workspace.name -cne 'law-asre-cus') {
        throw 'Connector setup requires the existing Review/Low agent, action identity and workspace.'
    }
    $definitions = @(Get-OperationsConnectorDefinitions -Workspace $workspace -Identity $identity)
    $changes = @()
    foreach ($definition in $definitions) {
        $actual = Invoke-OperationsConnectorRequest -Context $Context -Resource $definition.name -AllowNotFound
        if ($null -ne $actual) {
            $properties = $actual.properties
            if ($properties.ContainsKey('provisioningState') -and
                $properties.provisioningState -in @('Failed', 'Canceled', 'Deleting')) {
                throw "Connector provisioning failed: $($definition.name)."
            }
            if ($properties.dataConnectorType -cne $definition.dataConnectorType -or
                $properties.identity -cne $identity -or
                ($definition.name -ceq 'asre-operations-logs' -and
                 $properties.extendedProperties.armResourceId -ine $workspaceId)) {
                throw "Refusing to replace a foreign connector: $($definition.name)."
            }
        }
        if ($null -eq $actual -or -not (Test-OperationsConnectorProperties $definition $actual.properties)) {
            if ($VerifyOnly) { throw "Connector configuration is missing or differs: $($definition.name)." }
            $changes += $definition.name
        }
    }
    foreach ($definition in $definitions) {
        if ($definition.name -in $changes) {
            if (-not $PSCmdlet.ShouldProcess($definition.name, 'Publish native read-only telemetry connector')) {
                if ($WhatIfPreference) { continue }
                throw "Connector publication was declined: $($definition.name)."
            }
            Invoke-OperationsConnectorRequest -Context $Context -Resource $definition.name -Method Put -Properties $definition
        }
        $ready = $false
        for ($attempt = 0; $attempt -lt 12; $attempt++) {
            $actual = Invoke-OperationsConnectorRequest -Context $Context -Resource $definition.name -AllowNotFound
            if ($null -ne $actual) {
                $properties = $actual.properties
                if ($properties.ContainsKey('provisioningState') -and
                    $properties.provisioningState -in @('Failed', 'Canceled', 'Deleting')) {
                    throw "Connector provisioning failed: $($definition.name)."
                }
                if ((Test-OperationsConnectorProperties $definition $properties) -and
                    (-not $properties.ContainsKey('provisioningState') -or $properties.provisioningState -ceq 'Succeeded')) {
                    $ready = $true
                    break
                }
            }
            if ($attempt -lt 11) { Start-Sleep -Seconds 5 }
        }
        if (-not $ready) { throw "Connector readback was not verified: $($definition.name)." }
    }
    $planned = $changes.Count -gt 0 -and $WhatIfPreference
    [pscustomobject]@{
        status = $(if ($planned) { 'planned' } else { 'configuration-verified' })
        connectors = @($definitions.name); changed = @($changes | Where-Object { -not $planned })
        workspace_resource_id = $workspace.id; workspace_customer_id = $workspace.properties.customerId
        identity_resource_id = $identity; runtime_verified = $false
    }
}

if ($DefinitionsOnly) { return }
$action = if ($VerifyOnly -or $WhatIfPreference) { 'Preflight' }
elseif ($env:servicePrincipalId) { 'PublishDisabled' }
else { 'PublishHandler' }
$context = & {
    param($Action, $Root)
    # Local definition imports use Set/Remove-Variable; authentication itself only reads.
    $WhatIfPreference = $false
    . (Join-Path $Root 'scripts\ReviewAgent.Common.ps1')
    Get-MonitorNativeContext -Action $Action -Root $Root
} $action (Split-Path $PSScriptRoot -Parent)
try {
    Initialize-OperationsConnector -Context $context -VerifyOnly:$VerifyOnly -WhatIf:$WhatIfPreference
}
finally {
    $context.armHeaders.Clear()
    $context.sreHeaders.Clear()
}
