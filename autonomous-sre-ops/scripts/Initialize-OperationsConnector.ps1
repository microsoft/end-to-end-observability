#requires -Version 7.5
<#
.SYNOPSIS
    Creates or verifies the two native read-only telemetry connectors of the existing SRE agent.
.DESCRIPTION
    Everything comes from the deployment context (Get-AsreContext): the agent, its action identity and the
    Log Analytics workspace. Requests go through the agent plane (AgentPlane.Common.ps1), which refuses any ARM
    write outside the agent resource. -VerifyOnly and -WhatIf use a read-only plane and never publish.
    Only the connectors asre-operations-logs (LogAnalytics) and monitor-client (MonitorClient) are touched;
    a connector with another type or identity is refused, never replaced.
.EXAMPLE
    ./scripts/Initialize-OperationsConnector.ps1 -VerifyOnly
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [switch]$VerifyOnly,
    [switch]$DefinitionsOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not (Get-Command Get-AsreContext -ErrorAction SilentlyContinue)) { . (Join-Path $PSScriptRoot 'Context.Common.ps1') }
if (-not (Get-Command Get-AsreAgentPlane -ErrorAction SilentlyContinue)) { . (Join-Path $PSScriptRoot 'AgentPlane.Common.ps1') }

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

function New-OperationsConnectorContext {
    # Binds the deployment context and an agent plane into the one hashtable the functions below accept.
    param(
        [Parameter(Mandatory)][System.Collections.IDictionary]$Context,
        [Parameter(Mandatory)][System.Collections.IDictionary]$Plane
    )
    $agent = Get-AsreMember -Value $Context -Path @('agent', 'resource_id')
    $identity = Get-AsreMember -Value $Context -Path @('identities', 'agent', 'resource_id')
    $workspaceId = Get-AsreMember -Value $Context -Path @('log_analytics', 'resource_id')
    $workspaceName = Get-AsreMember -Value $Context -Path @('log_analytics', 'name')
    foreach ($value in @($agent, $identity, $workspaceId, $workspaceName)) {
        if ($value -isnot [string] -or [string]::IsNullOrWhiteSpace($value)) {
            throw 'Connector setup needs the agent, action identity and workspace of the deployment context.'
        }
    }
    if ($Plane['agent'] -cne $agent) { throw 'Connector setup is restricted to the existing ASRE agent.' }
    return @{
        agent = $agent; identity = $identity; workspaceId = $workspaceId; workspaceName = $workspaceName
        readOnly = [bool]$Plane['readOnly']; plane = $Plane
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
    $agent = Get-AsreMember -Value $Context -Path @('agent')
    $plane = Get-AsreMember -Value $Context -Path @('plane')
    if ($agent -isnot [string] -or $plane -isnot [System.Collections.IDictionary] -or $plane['agent'] -cne $agent) {
        throw 'Connector setup is restricted to the existing ASRE agent.'
    }
    switch ($Resource) {
        Agent { $path = "${agent}?api-version=2026-01-01" }
        Workspace {
            $workspaceId = Get-AsreMember -Value $Context -Path @('workspaceId')
            if ($workspaceId -isnot [string] -or
                $workspaceId -cnotmatch '\A/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\.OperationalInsights/workspaces/[^/]+\z') {
                throw 'Connector setup needs the workspace of the deployment context.'
            }
            $path = "${workspaceId}?api-version=2023-09-01"
        }
        default { $path = "$agent/DataConnectors/${Resource}?api-version=2025-05-01-preview" }
    }
    $effectivePlane = $plane
    $planeMethod = $Method
    if ($Method -ceq 'ReadConfiguration') {
        if ($Resource -in @('Agent', 'Workspace')) { throw 'Configuration hydration is restricted to the two named connectors.' }
        $path = "$agent/DataConnectors/$Resource/ListSecrets?api-version=2025-05-01-preview"
        $planeMethod = 'Post'
        # ListSecrets only reads (a GET redacts the workspace descriptor) but is a POST, which a read-only plane
        # refuses. This one fixed, agent-child call uses a writable copy of the plane; nothing else does.
        $effectivePlane = @{}
        foreach ($key in $plane.Keys) { $effectivePlane[$key] = $plane[$key] }
        $effectivePlane['readOnly'] = $false
    }
    if ($Method -ceq 'Put' -and ((Get-AsreMember -Value $Context -Path @('readOnly')) -or $plane['readOnly'] -or
        $Resource -in @('Agent', 'Workspace') -or $null -eq $Properties -or $Properties['name'] -cne $Resource)) {
        throw 'Only the two named connector configurations may be published.'
    }
    $arguments = @{ Plane = $effectivePlane; Service = 'Arm'; Method = $planeMethod; Path = $path }
    if ($Method -ceq 'Put') { $arguments['Body'] = @{ properties = $Properties } }
    elseif ($Method -ceq 'ReadConfiguration') { $arguments['Body'] = @{} }
    if ($AllowNotFound -and $Method -ceq 'Get' -and $Resource -notin @('Agent', 'Workspace')) { $arguments['AllowNotFound'] = $true }
    $response = Invoke-AsreAgentPlaneRequest @arguments
    $status = $response['status']
    if ($status -eq 404 -and $arguments.ContainsKey('AllowNotFound')) { return $null }
    if ($status -notin @(200, 201, 202)) { throw "Native connector $Method $Resource returned HTTP $status." }
    if ($Method -ceq 'Put') { return }
    $data = $response['data']
    if ($data -isnot [System.Collections.IDictionary]) { throw 'The native connector response was not a JSON object.' }
    if ($Method -ceq 'Get' -and $Resource -notin @('Agent', 'Workspace')) {
        $expectedType = if ($Resource -ceq 'asre-operations-logs') { 'LogAnalytics' } else { 'MonitorClient' }
        $identity = Get-AsreMember -Value $Context -Path @('identity')
        if ((Get-AsreMember -Value $data -Path @('properties', 'name')) -cne $Resource -or
            (Get-AsreMember -Value $data -Path @('properties', 'dataConnectorType')) -cne $expectedType -or
            $identity -isnot [string] -or (Get-AsreMember -Value $data -Path @('properties', 'identity')) -cne $identity) {
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
    $identity = Get-AsreMember -Value $Context -Path @('identity')
    $workspaceId = Get-AsreMember -Value $Context -Path @('workspaceId')
    $workspaceName = Get-AsreMember -Value $Context -Path @('workspaceName')
    foreach ($value in @($identity, $workspaceId, $workspaceName)) {
        if ($value -isnot [string] -or [string]::IsNullOrWhiteSpace($value)) {
            throw 'Connector setup needs the agent, action identity and workspace of the deployment context.'
        }
    }
    $agent = Invoke-OperationsConnectorRequest -Context $Context -Resource Agent
    $workspace = Invoke-OperationsConnectorRequest -Context $Context -Resource Workspace
    $action = Get-AsreMember -Value $agent -Path @('properties', 'actionConfiguration')
    if ((Get-AsreMember -Value $action -Path @('identity')) -cne $identity -or
        (Get-AsreMember -Value $action -Path @('mode')) -cne 'Review' -or
        (Get-AsreMember -Value $action -Path @('accessLevel')) -cne 'Low' -or
        (Get-AsreMember -Value $workspace -Path @('id')) -ine $workspaceId -or
        (Get-AsreMember -Value $workspace -Path @('name')) -cne $workspaceName) {
        throw 'Connector setup requires the existing Review/Low agent, action identity and workspace.'
    }
    $definitions = @(Get-OperationsConnectorDefinitions -Workspace $workspace -Identity $identity)
    $changes = @()
    foreach ($definition in $definitions) {
        $actual = Invoke-OperationsConnectorRequest -Context $Context -Resource $definition.name -AllowNotFound
        if ($null -ne $actual) {
            $properties = Get-AsreMember -Value $actual -Path @('properties')
            if ($properties -isnot [System.Collections.IDictionary]) { throw "Connector response was not understood: $($definition.name)." }
            if ($properties.ContainsKey('provisioningState') -and
                $properties.provisioningState -in @('Failed', 'Canceled', 'Deleting')) {
                throw "Connector provisioning failed: $($definition.name)."
            }
            if ($properties['dataConnectorType'] -cne $definition.dataConnectorType -or
                $properties['identity'] -cne $identity -or
                ($definition.name -ceq 'asre-operations-logs' -and
                 (Get-AsreMember -Value $properties -Path @('extendedProperties', 'armResourceId')) -ine $workspaceId)) {
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
                $properties = Get-AsreMember -Value $actual -Path @('properties')
                if ($properties -isnot [System.Collections.IDictionary]) { throw "Connector response was not understood: $($definition.name)." }
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
$deploymentContext = Get-AsreContext
$plane = Get-AsreAgentPlane -Context $deploymentContext -ReadOnly:($VerifyOnly -or $WhatIfPreference)
try {
    $connectorContext = New-OperationsConnectorContext -Context $deploymentContext -Plane $plane
    Initialize-OperationsConnector -Context $connectorContext -VerifyOnly:$VerifyOnly -WhatIf:$WhatIfPreference
}
finally {
    Clear-AsreAgentPlane -Plane $plane
}
