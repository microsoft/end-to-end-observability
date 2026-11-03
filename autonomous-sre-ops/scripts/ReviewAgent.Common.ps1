#requires -Version 7.5
# Pure publication/lifecycle definitions. No authentication, network or settings
# changes on import. Legacy definitions retain their default native prerequisites.
$__monitorImportState = @{}
foreach ($__monitorImportName in @('OutputsFile','RuntimeProofFile','EnableIncidentPilot','Profile','DefinitionsOnly')) {
    $__monitorImportVariable = Get-Variable -Name $__monitorImportName -Scope Local -ErrorAction Ignore
    if ($null -ne $__monitorImportVariable) { $__monitorImportState[$__monitorImportName] = $__monitorImportVariable.Value }
}
. (Join-Path $PSScriptRoot 'Initialize-ReviewAgent.ps1') -DefinitionsOnly
foreach ($__monitorImportName in @('OutputsFile','RuntimeProofFile','EnableIncidentPilot','Profile','DefinitionsOnly')) {
    if ($__monitorImportState.ContainsKey($__monitorImportName)) {
        Set-Variable -Name $__monitorImportName -Scope Local -Value $__monitorImportState[$__monitorImportName]
    } else { Remove-Variable -Name $__monitorImportName -Scope Local -ErrorAction Ignore }
}
Remove-Variable __monitorImportState,__monitorImportName,__monitorImportVariable -ErrorAction Ignore

function Get-MonitorManifest {
    param([string]$Root)
    $m = Get-Content -LiteralPath (Join-Path $Root 'agent/workflows/azure-monitor-review.json') -Raw | ConvertFrom-Json -AsHashtable -DateKind String
    $p = $m.response_plan.properties
    $attempts = $(if ($m.phase -ceq 'Production') { 3 } else { 1 })
    $merge = $m.phase -ceq 'SchemaDiscovery'
    if ($m.ContainsKey('case_profiles') -and $m.case_profiles -isnot [array]) {
        throw 'Released case profiles must be an array.'
    }
    $profiles = @(if ($m.ContainsKey('case_profiles')) { $m.case_profiles } else { 'disk' })
    $profiled = $m.phase -ceq 'Production' -and $profiles.Count -gt 1
    $title = if ($profiled) { 'asre-demo-' } else { 'asre-demo-disk' }
    $priorities = if ('alert-recurrence' -cin $profiles) { 'Sev1,Sev2' } else { 'Sev1' }
    if (($profiles -join ',') -cnotin @('disk','disk,patch-installation','disk,patch-installation,alert-recurrence,capacity-growth,backup-job')) {
        throw 'Case profiles must remain one of the closed released selections.'
    }
    if ($m.version -cne '1.0' -or $m.phase -cnotin @('SchemaDiscovery','Production') -or $m.serialized_platform -cne 'AzMonitor' -or
        $m.response_plan.name -cne 'asre-azure-monitor-review' -or
        $p.incidentPlatform -cne 'AzMonitor' -or $p.isEnabled -isnot [bool] -or $p.isEnabled -or
        $p.agentMode -cne 'Review' -or $p.handlingAgent -cne 'asre-azure-monitor-review' -or
        ($p.priorities -join ',') -cne $priorities -or $p.titleContains -cne $title -or
        $p.maxAutomatedInvestigationAttempts -ne $attempts -or $p.mergeEnabled -isnot [bool] -or $p.mergeEnabled -ne $merge -or
        $p.mergeWindowHours -ne 3 -or
        $m.commissioning.version -cne '1.0' -or
        [DateTimeOffset]::Parse($m.commissioning.minimum_not_before).Offset -ne [TimeSpan]::Zero) {
        throw 'Monitor source must remain the disabled, fixed Review profile with phase-specific native attempt policy.'
    }
    $discoveryAlert = '/subscriptions/00000001-000a-4000-a000-000000000001/resourcegroups/rg-asre-control-cus/providers/microsoft.operationalinsights/workspaces/law-asre-cus/providers/Microsoft.AlertsManagement/alerts/00000047-000a-d000-0000-000000000047'
    if (($m.phase -ceq 'SchemaDiscovery' -and $p.alertId -cne $discoveryAlert) -or
        ($m.phase -ceq 'Production' -and $p.ContainsKey('alertId') -and $p.alertId)) {
        throw 'Source phase must preserve the exact discovery-only alert or fresh-alert production scope.'
    }
    return $m
}

function Get-MonitorCaseDeclaration {
    param([string]$Root)
    $d = Get-Content -LiteralPath (Join-Path $Root 'agent/tools/servicenow_case.json') -Raw | ConvertFrom-Json -AsHashtable
    if ($d.kind -cne 'tools' -or $d.type -cne 'ExtendedAgentTool' -or
        $d.properties.type -cne 'PythonFunctionTool' -or $d.callable -cne 'main' -or
        $d.buildScript -cne 'scripts/Build-CaseTool.ps1' -or $d.source -cne 'servicenow_case.py' -or
        $d.name -cnotmatch '^[a-z][a-z0-9-]+$' -or $d.properties.parameters -isnot [array] -or
        $d.properties.ContainsKey('functionCode')) {
        throw 'Consume the frozen B declaration and its builder; do not substitute tool code or parameters.'
    }
    return $d
}

function Get-MonitorHook {
    param([string]$Root)
    $m = Get-MonitorManifest -Root $Root
    if ($m.phase -ceq 'SchemaDiscovery') {
        return @'
#!/usr/bin/env python3
import json, sys
reason = "ASRE SchemaDiscovery denies ALL tools."
print(json.dumps({"decision": "block", "reason": reason}))
print(reason, file=sys.stderr)
sys.exit(2)
'@
    }
    return Get-Content -LiteralPath (Join-Path $Root 'agent/hooks/monitor-case-policy.py') -Raw
}

function Get-MonitorReviewConfiguration {
    param([string]$Root, [string]$CaseToolSource)
    $manifest = Get-MonitorManifest -Root $Root
    if ($manifest.phase -ceq 'SchemaDiscovery') {
        @{
            kind = 'agents'; name = 'asre-azure-monitor-review'; type = 'ExtendedAgent'
            properties = @{
                instructions = @'
ASRE_NATIVE_WORKFLOW_PHASE=SchemaDiscovery
This is a bounded, tool-inaccessible native-provenance discovery handler, NOT production.
Do not call ANY tool, including inherited native/custom/system tools, skill helpers,
files, shell, ServiceNow, case tools, delegation, connectors or repair. Empty tool
lists alone are not authorization: a deny-all PreToolUse hook must block every call.
Use only context already supplied by native intake; report its identifiers and
missing provenance without fabricating a source, positive decoder or case.
Only the one source-pinned historical alert is eligible. Do not create another
investigation, ticket, evidence request or action. No case reader is installed by
this profile. A Production phase requires independent review and a SECOND
protected disabled publication; neither activation nor this agent may change phase.
'@
                handoffDescription = 'SchemaDiscovery only; deny all tool execution, no case capability.'
                tools = @(); mcpTools = @(); commonTools = @(); handoffs = @()
                connectors = @(); agentsAsTools = @()
                enableSkills = $false; allowedSkills = @(); addSystemSkills = $false
                disableDocumentRetrieval = $true; enableVanillaMode = $false
                hooks = @{ PreToolUse = @(@{
                    type = 'command'; matcher = '(?s:.*)'; timeout = 15; failMode = 'block'
                    script = Get-MonitorHook -Root $Root
                    prompt = $null; command = $null; model = $null; maxRejections = $null; sources = $null
                }) }
            }
        }
        return
    }
    $d = Get-MonitorCaseDeclaration -Root $Root
    $source = $CaseToolSource
    if (-not $source) { $source = & (Join-Path $Root $d.buildScript) -PassThru }
    if ($source -isnot [string] -or [string]::IsNullOrWhiteSpace($source)) { throw 'The B builder did not return its complete Python package.' }
    $properties = $d.properties.Clone()
    $properties.functionCode = $source
    $properties.dependencies = @($d.dependencies)
    foreach ($parameter in $properties.parameters) { $parameter.value = $null }
    @{ kind = $d.kind; name = $d.name; type = $d.type; properties = $properties }
    $selectedSkills = @('asre-linux-rca', 'asre-patch-backup-recovery', 'asre-case-and-verification')
    if ($manifest.ContainsKey('case_profiles') -and 'capacity-growth' -cin $manifest.case_profiles) {
        $selectedSkills += 'asre-proactive-analysis'
    }
    foreach ($item in @(Get-ReviewConfiguration -Root $Root -Profile Common)) {
        if ($item.kind -eq 'skills' -and $item.name -cin $selectedSkills) {
            $item
            continue
        }
        if ($item.kind -ne 'tools') { continue }
        foreach ($parameter in $item.properties.parameters) { $parameter.value = $null }
        $item
    }
    $schema = Get-Content -LiteralPath (Join-Path $Root 'contracts/servicenow-case.schema.json') -Raw
    $instructions = Get-Content -LiteralPath (Join-Path $Root 'agent/instructions/azure-monitor-review.md') -Raw
    @{
        kind = 'agents'; name = 'asre-azure-monitor-review'; type = 'ExtendedAgent'
        properties = @{
            instructions = "ASRE_NATIVE_WORKFLOW_PHASE=Production`n$instructions`n`n## Embedded shared evidence contract`nThe tool derives episode_id. Omit episode_id in evidence_json input; the following schema applies after derivation.`n~~~json`n$schema`n~~~`n"
            handoffDescription = 'Native Azure Monitor five fixed infrastructure profiles and owned case findings only; no repair.'
            tools = @('QueryLogAnalyticsByResourceId', 'ReadFile', 'SearchMemory')
            mcpTools = @('asre-review-evidence', $d.name)
            commonTools = @(); handoffs = @()
            enableSkills = $true; allowedSkills = $selectedSkills
            hooks = @{ PostToolUse = @(@{
                type = 'command'; matcher = '(?s:.*)'; timeout = 15; failMode = 'allow'
                script = Get-MonitorHook -Root $Root
                prompt = $null; command = $null; model = $null; maxRejections = $null; sources = $null
            }) }
        }
    }
}

function Assert-MonitorExact {
    param($Expected, $Actual, [string]$Path = 'monitor')
    # Compare recursively INCLUDING unexpected fields/types. Resource properties
    # use a separate allowed-server-metadata projection; policies are exact.
    $e = ConvertTo-Json -InputObject $Expected -Depth 40 | ConvertFrom-Json -AsHashtable -NoEnumerate -DateKind String
    $a = ConvertTo-Json -InputObject $Actual -Depth 40 | ConvertFrom-Json -AsHashtable -NoEnumerate -DateKind String
    if ($e -is [Collections.IDictionary]) {
        if ($a -isnot [Collections.IDictionary] -or $e.Count -ne $a.Count) { throw "Strict readback changed $Path" }
        foreach ($key in $e.Keys) {
            if (@($a.Keys) -cnotcontains $key) { throw "Strict readback omitted $Path.$key" }
            Assert-MonitorExact $e[$key] $a[$key] "$Path.$key"
        }
    }
    elseif ($e -is [array]) {
        if ($a -isnot [array] -or $e.Count -ne $a.Count) { throw "Strict readback changed $Path" }
        for ($i = 0; $i -lt $e.Count; $i++) { Assert-MonitorExact $e[$i] $a[$i] "$Path[$i]" }
    }
    elseif (($null -eq $e) -ne ($null -eq $a) -or
        ($null -ne $e -and ($e.GetType() -ne $a.GetType() -or $e -cne $a))) {
        throw "Strict readback changed $Path"
    }
}

function Assert-MonitorResource {
    param([hashtable]$Expected, $Actual)
    $a = $Actual | ConvertTo-Json -Depth 40 | ConvertFrom-Json -AsHashtable -DateKind String
    if ($a.name -cne $Expected.name -or $a.type -cne $Expected.type) { throw 'Resource readback changed identity/type.' }
    foreach ($key in $a.Keys) {
        if ($key -cnotin @('name', 'type', 'tags', 'properties', 'kind')) { throw "Strict readback: unknown resource envelope field: $key" }
    }
    # Explicit deployed DTO defaults, not blanket exclusion of security fields.
    $defaults = @{}
    switch ($Expected.type) {
        ExtendedAgentTool {
            $defaults = @{ dependencies = $null; authEnabled = $false; authScopes = $null
                connector = $null; attributes = $null; toolMode = 'Auto' }
        }
        Skill {
            $defaults = @{ sourcePluginInstallation = $null; pluginName = $null
                marketplaceName = $null; workspacePath = $null }
        }
        ExtendedAgent {
            foreach ($key in @('connectors', 'allowParallelToolCalls', 'agentsAsTools', 'maxReflectionCount',
                'criticPromptPath', 'criticOnHandOff', 'customReflectionNote', 'commonPrompts',
                'disableDocumentRetrieval', 'enableHandoffPromptOverride', 'userPromptOverride',
                'handoffPromptOverride', 'instructionsOverride', 'temperature', 'llmModelName',
                'addSystemSkills', 'outputType', 'permissions')) { $defaults[$key] = $null }
            $defaults.enableVanillaMode = $false
        }
        IncidentFilter {
            foreach ($key in @('name', 'impactedService', 'state', 'category', 'subcategory', 'configurationItem',
                'callerId', 'location', 'impact', 'urgency', 'severity', 'incidentType', 'alertId', 'owningTeamId')) {
                $defaults[$key] = ''
            }
            foreach ($key in @('assignmentGroups', 'customFields', 'incidentTypes', 'titleContainsAll',
                'titleContainsAny', 'titleNotContains', 'owningTeamIds')) { $defaults[$key] = @() }
            foreach ($key in @('handlingAgents', 'icmFilterSettings', 'azMonitorFilterSettings', 'configurationWarning')) {
                $defaults[$key] = $null
            }
            $defaults.requiresServiceNowPlatformFeatures = $false
        }
    }
    foreach ($key in @('etag', 'createdAt', 'updatedAt')) {
        if ($a.properties.ContainsKey($key)) {
            if ($a.properties[$key] -isnot [string] -or [string]::IsNullOrWhiteSpace($a.properties[$key])) {
                throw "Invalid service metadata: $key"
            }
            $a.properties.Remove($key)
        }
    }
    foreach ($key in @($a.properties.Keys)) {
        if ($Expected.properties.ContainsKey($key)) { continue }
        if (-not $defaults.ContainsKey($key)) { throw "Strict readback: unknown security-sensitive resource property: $key" }
        if ($key -ceq 'permissions' -and $null -ne $a.properties[$key]) {
            Assert-MonitorExact (Get-MonitorAgentPermissions) (ConvertTo-MonitorAgentPermissions $a.properties[$key]) 'agent-scoped-permissions'
        } elseif ([string]::Equals($key,'azMonitorFilterSettings',[StringComparison]::Ordinal) -and
            [string]::Equals($Expected.type,'IncidentFilter',[StringComparison]::Ordinal) -and
            [string]::Equals($Expected.properties.incidentPlatform,'AzMonitor',[StringComparison]::Ordinal)) {
            $nested = $a.properties[$key]
            if ($nested -isnot [Collections.IDictionary] -or $nested.Count -ne 2) { throw 'Unsupported Monitor filter defaults.' }
            $names = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
            foreach ($name in $nested.Keys) { $null = $names.Add($name) }
            if (-not $names.SetEquals([string[]]@('targetResourceType','targetResource'))) { throw 'Unsupported Monitor filter default keys.' }
            foreach ($name in $names) {
                if ($nested[$name] -isnot [string] -or $nested[$name].Length -ne 0) { throw 'Unsupported Monitor filter default value.' }
            }
        } else { Assert-MonitorExact $defaults[$key] $a.properties[$key] "$($Expected.name).default.$key" }
        $a.properties.Remove($key)
    }
    Assert-MonitorExact -Expected $Expected.properties -Actual $a.properties -Path $Expected.name
}

function Get-MonitorAgentPermissions {
    param([string]$Root = (Split-Path $PSScriptRoot -Parent))
    if ((Get-MonitorManifest -Root $Root).phase -ceq 'SchemaDiscovery') { return @{ allow = @() } }
    # Official native policy syntax is exact raw runtime names; no global allow.
    return @{ allow = @('QueryLogAnalyticsByResourceId', 'ReadFile', 'SearchMemory', 'asre-review-evidence', 'asre-servicenow-case') }
}

function ConvertTo-MonitorAgentPermissions {
    param([hashtable]$Policy)
    if (-not $Policy -or -not $Policy.ContainsKey('allow') -or $Policy.allow -isnot [array]) {
        throw 'Agent policy readback omitted its explicit allow collection.'
    }
    foreach ($key in $Policy.Keys) {
        if ($key -cnotin @('allow', 'ask', 'deny') -or $Policy[$key] -isnot [array] -or
            ($key -cne 'allow' -and $Policy[$key].Count)) { throw 'Unexpected per-agent ask/deny or policy field.' }
    }
    return @{ allow = $Policy.allow }
}

function Get-MonitorScannerBinding {
    # Retained Terraform identity and coordinator-reviewed access delta. These
    # are source constants, never values supplied by a readiness caller.
    $scope = '/subscriptions/00000001-000a-4000-a000-000000000001'
    return @{
        identity_resource_id = "$scope/resourceGroups/rg-asre-control-cus/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-asre-agent-cus"
        principal_id = '00000050-000a-4000-8000-000000000050'
        scope = $scope
        role_definition_id = "$scope/providers/Microsoft.Authorization/roleDefinitions/749f88d5-cbae-40b8-bcfc-e573ddc772fa"
    }
}

function ConvertTo-MonitorCanonicalJson {
    param($Item)
    if ($Item -is [Collections.IDictionary]) {
        $keys = [string[]]@($Item.Keys)
        [Array]::Sort($keys, [StringComparer]::Ordinal)
        return '{' + ((@($keys | ForEach-Object {
            (ConvertTo-Json -InputObject $_ -Compress) + ':' + (ConvertTo-MonitorCanonicalJson $Item[$_])
        })) -join ',') + '}'
    }
    if ($Item -is [array]) {
        return '[' + ((@($Item | ForEach-Object { ConvertTo-MonitorCanonicalJson $_ })) -join ',') + ']'
    }
    return ConvertTo-Json -InputObject $Item -Depth 40 -Compress
}

function Get-MonitorObjectSha256 {
    param($Value)
    # Observer contract: UTF8/no BOM; ordinal object keys, original array order,
    # compact ConvertTo-Json scalar encoding. Never hashes a displayed table.
    $json = ConvertTo-MonitorCanonicalJson $Value
    return [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData(
        [Text.Encoding]::UTF8.GetBytes($json))).ToLowerInvariant()
}

function Test-MonitorDiscoveryReadiness {
    param([hashtable]$Context)
    $root = $Context.root
    $profile = $Context.probeProfile
    $snapshot = $Context.snapshot
    $window = $Context.activationWindow
    Assert-MonitorActivationWindow $window -Discovery
    if (-not $profile -or -not $snapshot) { throw 'Discovery readiness requires ProbeProfile and original Snapshot.' }
    $connectedMode = Test-MonitorConnectedProfile $profile
    $profileFields = @('version','phase','probe_id','tenant_id','coordinator_object_id','coordinator_client_id',
        'agent_resource_id','publication_manifest_sha256','agent_definition_sha256','snapshot_sha256',
        'activation_window')
    if ($connectedMode) { $profileFields += @('bootstrap_receipt_sha256','publication_receipt_sha256') }
    if ([string]::Equals($profile.version,'3.0-adopted',[StringComparison]::Ordinal)) {
        $profileFields += @('publication_context_sha256','publication_build_sha256')
    }
    Assert-MonitorBootstrapBinding @($profileFields | Sort-Object) @($profile.Keys | Sort-Object)
    $authority = (Get-MonitorManifest -Root $root).activation_authority
    if ((-not $connectedMode -and $profile.version -cne '1.0') -or $profile.phase -cne 'SchemaDiscovery' -or
        $profile.probe_id -cnotmatch '^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$' -or
        $profile.tenant_id -cne $authority.tenant_id -or
        $profile.coordinator_object_id -cne $authority.object_id -or
        $profile.coordinator_client_id -cne $authority.client_id -or
        $profile.agent_resource_id -cne $Context.agent -or
        $profile.publication_manifest_sha256 -cne $Context.publicationManifestSha256 -or
        $profile.snapshot_sha256 -cne (Get-MonitorObjectSha256 $snapshot)) {
        throw 'Discovery profile does not bind the pinned actor, publication and original snapshot.'
    }
    Assert-MonitorExact $window $profile.activation_window 'discovery-profile-window'
    if ($connectedMode) {
        Assert-MonitorBootstrapBinding $window $profile.activation_window
        foreach ($pair in @(@('phase','SchemaDiscovery'),@('tenant_id',$authority.tenant_id),
            @('coordinator_object_id',$authority.object_id),@('coordinator_client_id',$authority.client_id),
            @('agent_resource_id',$Context.agent),@('publication_manifest_sha256',$Context.publicationManifestSha256),
            @('snapshot_sha256',(Get-MonitorObjectSha256 $snapshot)))) {
            Assert-MonitorBootstrapBinding $pair[1] $profile[$pair[0]]
        }
        Test-MonitorConnectedDiscoveryChain $Context
    }
    # All proofs are checked against fresh service reads, not a profile's flags.
    $state = Get-MonitorNativeState $Context
    $manifest = Get-MonitorManifest -Root $root
    Assert-MonitorQuiescent $state $manifest.response_plan
    Assert-MonitorExact @{ allow = @(); ask = @(); deny = @() } $state.permissions 'discovery-global-baseline'
    Assert-MonitorExact @{ allow = @() } $state.agentPermissions 'discovery-agent-policy'
    $expectedState = $snapshot | ConvertTo-Json -Depth 40 | ConvertFrom-Json -AsHashtable -DateKind String
    if ($Context.action -ceq 'Enable' -and -not $connectedMode) {
        # The reviewed Cutover may change ONLY the platform and ARM revision.
        $expectedState.platformConfiguration = @{ type = 'AzMonitor'; connectionName = 'azmonitor'; oboUser = '' }
        $expectedState.armEtag = $state.armEtag
    }
    $adoptedDiscovery = [string]::Equals($profile.version,'3.0-adopted',[StringComparison]::Ordinal) -and
        [string]::Equals($profile.phase,'SchemaDiscovery',[StringComparison]::Ordinal)
    if ($adoptedDiscovery) {
        Assert-MonitorSchemaDiscoveryAdoptedState $expectedState $state $root
    } else {
        Assert-MonitorExact $expectedState $state 'discovery-snapshot-context'
    }
    if ([string]::IsNullOrWhiteSpace($state.permissionsEtag) -or
        [string]::IsNullOrWhiteSpace($state.agentPermissionsEtag) -or
        (-not $connectedMode -and [string]::IsNullOrWhiteSpace($state.armEtag))) { throw 'Discovery readback omitted an actual configuration ETag.' }
    if ($connectedMode) {
        if (-not $adoptedDiscovery) { Assert-MonitorBootstrapBinding $expectedState $state }
        Assert-MonitorConnected $Context $state
    }
    $expectedAgent = @(Get-MonitorReviewConfiguration -Root $root)[0]
    $actualAgent = (Invoke-MonitorNativeRequest $Context Sre Get '/api/v2/extendedAgent/agents/asre-azure-monitor-review').data
    Assert-MonitorResource $expectedAgent $actualAgent
    if ($connectedMode) {
        foreach ($key in @('name','type')) { Assert-MonitorBootstrapBinding $expectedAgent[$key] $actualAgent[$key] }
        foreach ($key in $expectedAgent.properties.Keys) { Assert-MonitorBootstrapBinding $expectedAgent.properties[$key] $actualAgent.properties[$key] }
    }
    $agentHash = Get-MonitorObjectSha256 $expectedAgent
    if ($profile.agent_definition_sha256 -cne $agentHash) { throw 'Discovery selected-agent source hash changed.' }

    $scanner = Get-MonitorScannerBinding
    $arm = (Invoke-MonitorNativeRequest $Context Arm Get "$($Context.agent)?api-version=2026-01-01").data
    $identities = $arm.identity.userAssignedIdentities
    $identityKeys = @($identities.Keys | Where-Object { $_ -ieq $scanner.identity_resource_id })
    if ($arm.properties.actionConfiguration.identity -ine $scanner.identity_resource_id -or
        $identityKeys.Count -ne 1 -or
        $identities[$identityKeys[0]].principalId -cne $scanner.principal_id) {
        throw 'Actual scanner is not the exact retained action UAMI/principal.'
    }
    $assignmentPath = "$($scanner.scope)/providers/Microsoft.Authorization/roleAssignments?api-version=2022-04-01&`$filter=" +
        [uri]::EscapeDataString("principalId eq '$($scanner.principal_id)'")
    $assignments = (Invoke-MonitorNativeRequest $Context Arm Get $assignmentPath).data
    if ($assignments.value -isnot [array] -or
        ($assignments.ContainsKey('nextLink') -and $assignments.nextLink)) { throw 'Scanner role assignment enumeration is incomplete.' }
    $matching = @($assignments.value | Where-Object {
        $_.properties.principalId -ceq $scanner.principal_id -and
        $_.properties.roleDefinitionId -ieq $scanner.role_definition_id -and
        $_.properties.scope -ieq $scanner.scope
    })
    if ($matching.Count -ne 1 -or $matching[0].properties.principalType -cne 'ServicePrincipal' -or
        @('condition','conditionVersion','delegatedManagedIdentityResourceId' | Where-Object {
            $matching[0].properties.ContainsKey($_) -and $matching[0].properties[$_]
        }).Count -or $matching[0].id -inotmatch ('^' + [regex]::Escape($scanner.scope) +
            '/providers/Microsoft.Authorization/roleAssignments/[0-9a-f-]{36}$')) {
        throw 'Exact unconditional subscription Monitoring Contributor assignment is missing or ambiguous.'
    }
    $definition = (Invoke-MonitorNativeRequest $Context Arm Get "$($scanner.role_definition_id)?api-version=2022-04-01").data
    if ($definition.id -ine $scanner.role_definition_id -or
        $definition.properties.roleName -cne 'Monitoring Contributor' -or
        $definition.properties.type -cne 'BuiltInRole' -or $definition.properties.permissions -isnot [array]) {
        throw 'Actual Monitoring Contributor role definition changed.'
    }
    foreach ($action in @('Microsoft.AlertsManagement/alerts/read','Microsoft.Insights/scheduledQueryRules/read')) {
        $grants = @($definition.properties.permissions | Where-Object {
            $_.actions -is [array] -and $_.notActions -is [array] -and
            @($_.actions | Where-Object { $action -like $_ }).Count -and
            -not @($_.notActions | Where-Object { $action -like $_ }).Count
        })
        if (-not $grants.Count) { throw 'Actual Monitoring Contributor definition omits native scanner reads.' }
    }

    # Architect turn13: full source-exact readback of the unconditional native
    # exit2 hook/failMode=block is sufficient for this tool-inaccessible probe.
    # No additional live diagnostic, outbound credential or positive DTO gate.
    # Freshness here means a fresh ARM status read of the deliberately historical
    # alert, NOT Production's per-alert age/commissioning eligibility.
    $historical = Get-MonitorDiscoveryAlertIds -Root $root
    foreach ($id in $historical) {
        $alert = (Invoke-MonitorNativeRequest $Context Arm Get "$($id)?api-version=2019-03-01").data
        $essentials = $alert.properties.essentials
        if ($alert.id -ine $id -or $alert.type -ine 'microsoft.alertsmanagement/alerts' -or
            $essentials.alertRule -ine $manifest.scope.rule_id -or
            $essentials.targetResource -ine $manifest.scope.workspace_resource_id -or
            $essentials.severity -cne 'Sev1' -or $essentials.monitorService -cne 'Log Alerts V2' -or
            $essentials.signalType -cne 'Log') { throw 'Historical alert identity/rule/workspace no longer matches the protected discovery scope.' }
        $eligible = $essentials.monitorCondition -ceq 'Fired' -and $essentials.alertState -ceq 'New' -and
            $essentials.actionStatus.isSuppressed -is [bool] -and -not $essentials.actionStatus.isSuppressed
        if (($id -ieq $manifest.response_plan.properties.alertId -and -not $eligible) -or
            ($id -ine $manifest.response_plan.properties.alertId -and $eligible)) {
            throw 'Discovery requires exactly the sole source-pinned Fired/New historical alert.'
        }
    }
    Assert-MonitorActivationWindow $window -Discovery
    return @{ status = 'verified'; phase = 'SchemaDiscovery'; probe_id = $profile.probe_id
        publication_manifest_sha256 = $Context.publicationManifestSha256; agent_definition_sha256 = $agentHash
        scanner_principal_id = $scanner.principal_id; scanner_assignment_id = $matching[0].id
        scanner_role_definition_sha256 = Get-MonitorObjectSha256 $definition
        expires_at = $window.expires_at }
}

function Get-MonitorDiscoveryAlertIds {
    param([string]$Root)
    $id = (Get-MonitorManifest -Root $Root).response_plan.properties.alertId
    return @($id, $id.Replace('00000047-000a-d000-0000-000000000047','00000058-000a-f000-d000-000000000058'))
}

function Get-MonitorNativeContext {
    param([ValidateSet('Preflight','PublishDisabled','PublishHandler','BootstrapPlatform','ReconcileBootstrap','Cutover','Enable','Pause','Rollback', IgnoreCase = $false)][string]$Action = 'Preflight',
        [string]$Root = (Split-Path $PSScriptRoot -Parent), [hashtable]$AuthorityWindow)
    $ReadOnly = $Action -cin @('Preflight','ReconcileBootstrap')
    $sub = '00000001-000a-4000-a000-000000000001'
    $tenant = '00000002-000a-4000-9000-000000000002'
    $publisher = '00000018-000a-4000-b000-000000000018'
    try {
        $accountJson = & az account show --subscription $sub --output json --only-show-errors
        if ($LASTEXITCODE -ne 0) { throw 'Account lookup failed.' }
        $account = $accountJson | ConvertFrom-Json -AsHashtable -DateKind String
        if ($account.id -cne $sub -or $account.tenantId -cne $tenant) { throw 'Wrong account context.' }
        if ($Action -ceq 'PublishDisabled' -and ($env:servicePrincipalId -cne $publisher -or
            $env:tenantId -cne $tenant -or [string]::IsNullOrWhiteSpace($env:idToken) -or
            $account.user.type -cne 'servicePrincipal' -or $account.user.name -cne $publisher)) {
            throw 'Use the retained protected Apply client, never user AAD, for publication.'
        }
        $armToken = & az account get-access-token --subscription $sub --resource https://management.azure.com --query accessToken --output tsv --only-show-errors
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($armToken)) { throw 'ARM authentication failed.' }
        $sreToken = & az account get-access-token --subscription $sub --resource https://azuresre.dev --query accessToken --output tsv --only-show-errors
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($sreToken)) { throw 'SRE authentication failed.' }
    } catch { throw 'Fixed Monitor authentication failed. Mutations require the exact retained protected Apply client; details suppressed.' }
    $context = @{
        root = $Root; action = $Action; authorityAction = $Action
        authorityWindow = $AuthorityWindow
        readOnly = $ReadOnly
        agent = "/subscriptions/$sub/resourceGroups/rg-asre-control-cus/providers/Microsoft.App/agents/asre-demo-cus"
        endpoint = 'https://asre-demo-cus--0a1b2c3d.4e5f6a7b.centralus.azuresre.ai'
        armHeaders = @{ Authorization = "Bearer $armToken" }
        sreHeaders = @{ Authorization = "Bearer $sreToken" }
    }
    if ($Action -in @('PublishHandler','BootstrapPlatform','ReconcileBootstrap','Cutover','Enable','Pause','Rollback')) {
        $authority = (Get-MonitorManifest -Root $Root).activation_authority
        foreach ($token in @($armToken, $sreToken)) {
            try {
                $part = $token.Split('.')[1].Replace('-', '+').Replace('_', '/')
                $part += '=' * ((4 - $part.Length % 4) % 4)
                $claims = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($part)) | ConvertFrom-Json -AsHashtable
                $client = $(if ($claims.ContainsKey('appid')) { $claims.appid } else { $claims.azp })
                if ($claims.tid -cne $authority.tenant_id -or $claims.oid -cne $authority.object_id -or
                    $client -cne $authority.client_id -or $account.user.type -cne 'user') { throw 'Wrong actor.' }
            } catch { throw 'Control actions require only the pinned existing coordinator OID/tenant/Azure CLI client; no arbitrary administrator bypass.' }
        }
        $permissions = Invoke-MonitorNativeRequest $context Arm Get "$($context.agent)/providers/Microsoft.Authorization/permissions?api-version=2022-04-01"
        $capable = $false
        foreach ($entry in $permissions.data.value) {
            if ($entry.notActions.Count -or $entry.notDataActions.Count) { continue }
            if (@($entry.actions | Where-Object { 'Microsoft.App/agents/write' -like $_ }).Count -and
                @('Microsoft.App/agents/*/read','Microsoft.App/agents/*/write',
                    'Microsoft.App/agents/threads/approve/action' | Where-Object { $_ -cnotin $entry.dataActions }).Count -eq 0) {
                $capable = $true
            }
        }
        if (-not $capable) { throw 'Pinned coordinator effective own-agent SRE Administrator capability was not independently verified.' }
    }
    return $context
}

function Invoke-MonitorNativeRequest {
    param([hashtable]$Context, [ValidateSet('Arm', 'Sre')][string]$Service,
        [ValidateSet('Get', 'Put', 'Patch', 'Delete')][string]$Method = 'Get',
        [string]$Path, [hashtable]$Body, [string]$IfMatch, [switch]$AllowNotFound)
    if ($Method -cne 'Get' -and $Context.readOnly) { throw 'Read-only preflight cannot mutate configuration.' }
    if ($Method -cne 'Get' -and $Context.authorityAction -ceq 'PublishHandler') {
        $routePath = '/api/v2/incidentManagement/incidentFilters/asre-azure-monitor-review'
        $expandRoute = $Path -ceq $routePath -and $Context.ContainsKey('handlerExpandProfiles') -and
            $Context.handlerExpandProfiles
        if ($Service -cne 'Sre' -or ($Method -cne 'Put' -and -not ($Method -ceq 'Patch' -and $expandRoute)) -or
            (Get-MonitorManifest -Root $Context.root).phase -cne 'Production' -or
            -not $Context.ContainsKey('handlerJournal') -or
            $Context.handlerJournal -isnot [IO.FileStream] -or
            -not $Context.handlerPreimages.ContainsKey($Path) -or
            $Context.handlerSubmitted.Contains($Path)) {
            throw 'Handler publication permits one journaled update of each existing owned resource only.'
        }
        if ($Path -ceq $routePath) {
            if (-not $expandRoute -or (Get-MonitorManifest -Root $Context.root).case_profiles.Count -lt 2) {
                throw 'Route expansion requires its explicit closed multi-profile publication.'
            }
            $plan = (Get-MonitorManifest -Root $Context.root).response_plan
            $properties = $plan.properties.Clone()
            $properties.isEnabled = $true
            Assert-MonitorExact @{ name = $plan.name; type = $plan.type; properties = $properties } $Body 'enabled-expanded-source-route'
        } elseif ($Path -ceq '/api/v2/extendedAgent/asre-azure-monitor-review/permissions') {
            Assert-MonitorExact (Get-MonitorAgentPermissions -Root $Context.root) $Body 'handler-permissions'
            if (-not $IfMatch) { throw 'Handler permissions require their actual ETag.' }
        } else {
            $resource = @(Get-MonitorReviewConfiguration -Root $Context.root | Where-Object {
                $_.name -cne 'asre-review-evidence' -and
                $Path -ceq "/api/v2/extendedAgent/$($_.kind)/$($_.name)" })
            if ($resource.Count -ne 1) { throw 'Handler update escaped the fixed case tool, skills and investigator.' }
            Assert-MonitorExact @{ name = $resource[0].name; type = $resource[0].type
                properties = $resource[0].properties; tags = @() } $Body 'handler-source'
        }
        $current = Invoke-MonitorNativeRequest $Context Sre Get $Path
        Assert-MonitorExact $Context.handlerPreimages[$Path].data $current.data 'handler-owned-preimage'
        if ($IfMatch -and $IfMatch -cne $current.etag) { throw 'Handler resource revision changed.' }
        Write-MonitorBootstrapRecord $Context.handlerJournal @{
            event = 'SubmitOnce'; path = $Path; source_sha256 = Get-MonitorObjectSha256 $Body
            preimage_sha256 = Get-MonitorObjectSha256 $current.data; etag = $current.etag
        }
        $null = $Context.handlerSubmitted.Add($Path)
    }
    $reconcile = [string]::Equals($Context.authorityAction, 'ReconcileBootstrap', [StringComparison]::Ordinal)
    if ($reconcile) {
        if (-not $Context.readOnly -or $Method -cne 'Get') { throw 'Reconciliation permits GET only.' }
        Assert-MonitorActivationWindow $Context.authorityWindow -Discovery
    }
    $armPath = "$($Context.agent)?api-version=2026-01-01"
    $resourcePath = '^/api/v2/extendedAgent/(?:tools/(?:asre-review-evidence|asre-servicenow-case)|skills/(?:asre-linux-rca|asre-patch-backup-recovery|asre-case-and-verification|asre-proactive-analysis)|commonprompts/asre-operating-boundaries|agents/asre-azure-monitor-review)$'
    $filterPath = '/api/v2/incidentManagement/incidentFilters'
    $bootstrap = [string]::Equals($Context.authorityAction, 'BootstrapPlatform', [StringComparison]::Ordinal)
    $bootstrapArmPath = "$($Context.agent)?api-version=2025-05-01-preview"
    if ($bootstrap -and $Method -cne 'Get') {
        if ($Service -ceq 'Sre' -and $Method -ceq 'Delete' -and
            [string]::Equals($Path, "$filterPath/asre-servicenow-review-pilot", [StringComparison]::Ordinal)) {
            if ($null -ne $Body -or $IfMatch -or $Context.bootstrapWrite -cne 'Retire') { throw 'Bootstrap retirement boundary rejected.' }
        } elseif ($Service -ceq 'Arm' -and $Method -ceq 'Patch' -and
            [string]::Equals($Path, $bootstrapArmPath, [StringComparison]::Ordinal)) {
            Assert-MonitorBootstrapBinding @{ properties = @{ incidentManagementConfiguration = @{
                type = 'AzMonitor'; connectionName = 'azmonitor'; oboUser = '' } } } $Body
            if ($IfMatch -or $Context.bootstrapWrite -cne 'Connect') { throw 'Bootstrap connection boundary rejected.' }
        } else { throw 'Bootstrap permits only the exact retirement and connection transaction.' }
    }
    if ($Service -ceq 'Arm') {
        $permissionsPath = "$($Context.agent)/providers/Microsoft.Authorization/permissions?api-version=2022-04-01"
        $scanner = Get-MonitorScannerBinding
        $assignmentPath = "$($scanner.scope)/providers/Microsoft.Authorization/roleAssignments?api-version=2022-04-01&`$filter=" +
            [uri]::EscapeDataString("principalId eq '$($scanner.principal_id)'")
        if (-not (($Path -ceq $armPath -and $Method -in @('Get','Patch') -and -not $bootstrap) -or
            ($bootstrap -and [string]::Equals($Path, $bootstrapArmPath, [StringComparison]::Ordinal) -and $Method -cin @('Get','Patch')) -or
            ($reconcile -and [string]::Equals($Path, $bootstrapArmPath, [StringComparison]::Ordinal) -and $Method -ceq 'Get') -or
            ($Path -ceq $permissionsPath -and $Method -ceq 'Get') -or
            ($Context.ContainsKey('nativeCapture') -and $Method -ceq 'Get' -and
                $Path -ceq "$((Get-MonitorManifest -Root $Context.root).scope.rule_id)?api-version=2023-12-01") -or
            (-not $bootstrap -and $Method -ceq 'Get' -and $Path -cin @($assignmentPath, "$($scanner.role_definition_id)?api-version=2022-04-01")) -or
            (-not $bootstrap -and $Method -ceq 'Get' -and (Get-MonitorManifest -Root $Context.root).phase -ceq 'SchemaDiscovery' -and
                $Path -cin @(Get-MonitorDiscoveryAlertIds -Root $Context.root | ForEach-Object { "$($_)?api-version=2019-03-01" })))) {
            throw 'ARM request escaped the fixed agent configuration.'
        }
        $uri = 'https://management.azure.com' + $Path
        $headers = $Context.armHeaders.Clone()
    } else {
        $valid = ($Path -cmatch $resourcePath -and $Method -in @('Get', 'Put')) -or
            ($Path -ceq '/api/v2/agent/settings/global' -and $Method -ceq 'Get') -or
            ($Path -ceq "$filterPath/asre-azure-monitor-review" -and $Method -in @('Get', 'Put', 'Patch')) -or
            ($Path -ceq $filterPath -and $Method -ceq 'Get') -or
            ($bootstrap -and [string]::Equals($Path, "$filterPath/asre-servicenow-review-pilot", [StringComparison]::Ordinal) -and $Method -cin @('Get','Delete')) -or
            ($Path -ceq '/api/v2/incidentManagement/checkConnectivityDetailed' -and $Method -ceq 'Get') -or
            ($Path -ceq '/api/v2/extendedAgent/asre-azure-monitor-review/permissions' -and $Method -in @('Get', 'Put'))
        if (-not $valid) { throw 'SRE request escaped the fixed publication allowlist.' }
        $uri = $Context.endpoint + $Path
        $headers = $Context.sreHeaders.Clone()
    }
    if ($IfMatch) { $headers['If-Match'] = $IfMatch }
    $args = @{ Uri = $uri; Method = $Method; Headers = $headers; MaximumRedirection = 0
        TimeoutSec = 60; ErrorAction = 'Stop'; SkipHttpErrorCheck = $true }
    if ($null -ne $Body) { $args.Body = ConvertTo-Json -InputObject $Body -Depth 40; $args.ContentType = 'application/json' }
    if ($reconcile) {
        Assert-MonitorActivationWindow $Context.authorityWindow -Discovery
        $remaining = ([DateTimeOffset]::Parse($Context.authorityWindow.expires_at) - (Get-MonitorUtcNow)).TotalSeconds
        if ($remaining -lt 1) { throw 'Reconciliation observation deadline expired.' }
        $args.TimeoutSec = [int][Math]::Min(30, [Math]::Floor($remaining))
    }
    if ($bootstrap) {
        Assert-MonitorActivationWindow $Context.authorityWindow -Discovery
        $remaining = ([DateTimeOffset]::Parse($Context.authorityWindow.expires_at) - (Get-MonitorUtcNow)).TotalSeconds
        if ($remaining -lt 1) { throw 'Bootstrap original window has less than one second remaining.' }
        $args.TimeoutSec = [int][Math]::Min(30, [Math]::Floor($remaining))
        if ($Method -cne 'Get') {
            if (-not $Context.ContainsKey('bootstrapJournal') -or $Context.bootstrapJournal -isnot [IO.FileStream]) {
                throw 'Bootstrap mutation requires its private durable journal.'
            }
            # Consume before submitting, including transport exceptions. Never retry.
            if ($Context.bootstrapSubmitted.Contains($Method)) { throw 'Bootstrap mutation was already attempted.' }
            $null = $Context.bootstrapSubmitted.Add($Method)
            $Context.bootstrapWrite = ''
        }
    }
    # Last boundary before submission: internal ETag/state GETs may have used
    # up the original authority window. Never refresh it from caller objects.
    if ($Method -cne 'Get' -and $Context.ContainsKey('nativeCapture')) {
        # Capture never compensates with shutdown, even after expiry/ambiguity.
        Assert-MonitorActivationWindow $Context.authorityWindow -Discovery
        $capture = Get-MonitorNativeCaptureConfiguration -Root $Context.root -Window $Context.authorityWindow
        $expected = $null
        if ($Context.authorityAction -ceq 'PublishDisabled') {
            if ($Service -ceq 'Sre' -and $Path -ceq '/api/v2/extendedAgent/agents/asre-azure-monitor-review' -and $Method -ceq 'Put') {
                $expected = @{ name=$capture.agent.name; type=$capture.agent.type; properties=$capture.agent.properties; tags=@() }
            } elseif ($Service -ceq 'Sre' -and $Path -ceq "$filterPath/asre-azure-monitor-review" -and $Method -cin @('Put','Patch')) {
                $expected = @{ name=$capture.plan.name; type=$capture.plan.type; properties=$capture.plan.properties }
            }
        } elseif ($Context.authorityAction -ceq 'Enable' -and $Service -ceq 'Sre' -and
            $Path -ceq "$filterPath/asre-azure-monitor-review" -and $Method -ceq 'Patch') {
            $capture.plan.properties.isEnabled = $true
            $expected = @{ name=$capture.plan.name; type=$capture.plan.type; properties=$capture.plan.properties }
        }
        $handlerPut = $Context.authorityAction -ceq 'PublishDisabled' -and $Service -ceq 'Sre' -and
            $Path -ceq '/api/v2/extendedAgent/agents/asre-azure-monitor-review' -and $Method -ceq 'Put'
        if ($null -eq $expected -or (-not $handlerPut -and [string]::IsNullOrWhiteSpace($IfMatch))) { throw 'Capture forbids shutdown/other writes and requires an actual ETag.' }
        Assert-MonitorExact $expected $Body 'capture-source-exact-write'
        if($handlerPut){
            # This API has NO handler revision/CAS. Exclusive coordinated
            # publication is an operating condition, not atomic protection.
            if($IfMatch){throw 'Revisionless capture handler PUT cannot claim an If-Match revision.'}
            if($Context.ContainsKey('captureHandlerAttempted')){throw 'Capture handler submission already attempted; parent reconciliation required.'}
            $preimage=Invoke-MonitorNativeRequest $Context Sre Get $Path
            $desired=$false
            try {Assert-MonitorResource $capture.agent $preimage.data;$desired=$true}
            catch {Assert-MonitorResource @(Get-MonitorReviewConfiguration -Root $Context.root)[0] $preimage.data}
            if($desired){return $preimage}
        }
        $remaining = ([DateTimeOffset]::Parse($Context.authorityWindow.expires_at) - (Get-MonitorUtcNow)).TotalSeconds
        if ($remaining -lt 1) { throw 'Capture original window expired; retain UNKNOWN, never disable intake.' }
        $args.TimeoutSec = [int][Math]::Min(30,[Math]::Floor($remaining))
    } elseif ($Method -cne 'Get' -and $Context.authorityAction -cin @('Cutover','Enable')) {
        $plan = (Get-MonitorManifest -Root $Context.root).response_plan
        $planBody = @{ name = $plan.name; type = $plan.type; properties = $plan.properties }
        $ownedPlanWrite = $Service -ceq 'Sre' -and
            $Path -ceq "$filterPath/asre-azure-monitor-review" -and $Method -cin @('Put','Patch')
        $pause = $ownedPlanWrite -and $Body.properties.isEnabled -is [bool] -and -not $Body.properties.isEnabled
        if ($pause) {
            # Only the complete source-exact disabled owned plan may compensate
            # after expiry; not an arbitrary PATCH, route or false-valued field.
            Assert-MonitorExact $planBody $Body 'expired-owned-pause-only'
        } else {
            if ($Context.authorityAction -ceq 'Enable' -and $ownedPlanWrite) {
                $planBody.properties.isEnabled = $true
                Assert-MonitorExact $planBody $Body 'enable-submit-only'
            } elseif ($Context.authorityAction -ceq 'Cutover' -and $Service -ceq 'Arm' -and
                $Method -ceq 'Patch' -and $Path -ceq $armPath) {
                Assert-MonitorExact @{ properties = @{ incidentManagementConfiguration = @{
                    type = 'AzMonitor'; connectionName = 'azmonitor'; oboUser = '' } } } $Body 'cutover-submit-only'
            } else { throw 'Activation authority does not permit this mutation.' }
            Assert-MonitorActivationWindow $Context.authorityWindow -Discovery:(
                (Get-MonitorManifest -Root $Context.root).phase -ceq 'SchemaDiscovery')
            $remaining = ([DateTimeOffset]::Parse($Context.authorityWindow.expires_at) - (Get-MonitorUtcNow)).TotalSeconds
            if ($remaining -lt 1) { throw 'Original activation authority has less than one second remaining; no submission.' }
            $args.TimeoutSec = [int][Math]::Min(30, [Math]::Floor($remaining))
        }
    }
    # Fixed labels only, after the existing request allowlist has succeeded.
    $target = switch -CaseSensitive ($Path) {
        '/api/v2/extendedAgent/agents/asre-azure-monitor-review' { 'OwnedAgent' }
        '/api/v2/incidentManagement/incidentFilters/asre-azure-monitor-review' { 'OwnedIncidentFilter' }
        '/api/v2/extendedAgent/asre-azure-monitor-review/permissions' { 'AgentPermissions' }
        default { 'OtherAllowedRequest' }
    }
    if($Method -ceq 'Put' -and $Context.ContainsKey('nativeCapture') -and
        $Context.authorityAction -ceq 'PublishDisabled' -and $Service -ceq 'Sre' -and
        $Path -ceq '/api/v2/extendedAgent/agents/asre-azure-monitor-review'){
        $Context.captureHandlerAttempted=$true # consume BEFORE transport, including lost responses
    }
    try { $response = Invoke-WebRequest @args }
    catch {
        $failure = [InvalidOperationException]::new('Fixed native request transport-failure; response and authentication details suppressed. Re-read before retry.')
        $failure.Data['MonitorNativeCategory'] = 'TransportFailure'
        $failure.Data['MonitorNativeMethod'] = $Method
        $failure.Data['MonitorNativeTarget'] = $target
        throw $failure
    }
    if ($bootstrap -and $Method -cne 'Get') {
        # Private response custody precedes status/JSON validation. Opaque
        # operation references are never followed or emitted to public streams.
        $record = @{ event = 'MutationResponse'; method = $Method; service = $Service; headers = @{} }
        if ($response.StatusCode -is [int] -and $response.StatusCode -ge 100 -and $response.StatusCode -le 599) {
            $record.http_status = $response.StatusCode
        } else { $record.invalid_status = $true }
        foreach ($name in @('Azure-AsyncOperation','Operation-Location','Location','Retry-After',
            'x-ms-request-id','x-ms-correlation-request-id','x-ms-client-request-id','x-ms-routing-request-id',
            'x-ms-operation-identifier','x-ms-activity-id','request-id','client-request-id')) {
            $value = $response.Headers[$name]
            if ($null -eq $value) { continue }
            $values = @($value)
            if (@($values | Where-Object { $_ -isnot [string] }).Count) { $record.invalid_header_type = $true; continue }
            if ($value -is [string]) { $record.headers[$name] = $value } else { $record.headers[$name] = $values }
        }
        Write-MonitorBootstrapRecord $Context.bootstrapJournal $record
        if ($record.ContainsKey('invalid_header_type')) { throw [IO.InvalidDataException]::new('Unsupported private operation header type.') }
    }
    if ($response.StatusCode -isnot [int] -or $response.StatusCode -lt 100 -or $response.StatusCode -gt 599) {
        throw [InvalidOperationException]::new('Fixed native request returned an invalid HTTP status; details suppressed.')
    }
    if ($response.StatusCode -eq 404 -and $AllowNotFound) { return $null }
    if ($response.StatusCode -lt 200 -or $response.StatusCode -ge 300) {
        $failure = [InvalidOperationException]::new("Fixed native request returned HTTP $($response.StatusCode); response details suppressed. Re-read before retry.")
        $failure.Data['MonitorNativeCategory'] = 'HttpFailure'
        $failure.Data['MonitorNativeMethod'] = $Method
        $failure.Data['MonitorNativeTarget'] = $target
        $failure.Data['MonitorNativeStatus'] = $response.StatusCode
        if ($response.StatusCode -eq 400 -and $Service -ceq 'Sre' -and $Method -ceq 'Put' -and
            $Path -ceq '/api/v2/incidentManagement/incidentFilters/asre-azure-monitor-review') {
            try {
                $content = $response.Content
                if ($content -is [string] -and $content.Length -le 16384 -and
                    [Text.Encoding]::UTF8.GetByteCount($content) -le 16384) {
                    $validation = ConvertFrom-Json -InputObject $content -AsHashtable -NoEnumerate -Depth 8 -ErrorAction Stop
                    if ($validation -is [Collections.IDictionary] -and @($validation.Keys) -ccontains 'error' -and
                        $validation['error'] -is [Collections.IDictionary]) {
                        $envelope = $validation['error']
                        if (@($envelope.Keys) -ccontains 'code' -and $envelope['code'] -is [string] -and
                            [string]::Equals($envelope['code'], 'IncidentPlatformConfigurationPending', [StringComparison]::Ordinal)) {
                            $failure.Data['MonitorNativeValidation'] = 'IncidentPlatformConfigurationPending'
                        }
                    }
                }
            } catch { } # Unsupported response detail never replaces the safe HTTP diagnostic.
        }
        throw $failure
    }
    $data = $null
    if (-not [string]::IsNullOrWhiteSpace($response.Content)) {
        try { $data = $response.Content | ConvertFrom-Json -AsHashtable -DateKind String }
        catch {
            $failure = [InvalidOperationException]::new('Fixed native request parse-failure; response details suppressed.')
            $failure.Data['MonitorNativeCategory'] = 'ParseFailure'
            $failure.Data['MonitorNativeMethod'] = $Method
            $failure.Data['MonitorNativeTarget'] = $target
            throw $failure
        }
    }
    $etag = @($response.Headers['ETag']) -join ''
    if($Service -ceq 'Sre' -and $Method -ceq 'Get' -and
        $Path -ceq '/api/v2/incidentManagement/incidentFilters/asre-azure-monitor-review'){
        $revisions=@()
        $header=$response.Headers['ETag']
        if($null -ne $header -and -not ($header -is [string] -and $header.Length -eq 0)){
            $values=@($header)
            if($values.Count -ne 1){throw 'Owned filter requires one strong actual revision.'}
            $revisions+=,$values[0]
        }
        if($data -is [hashtable] -and $data.properties -is [hashtable] -and $data.properties.ContainsKey('etag')){
            $revisions+=,$data.properties.etag
        }
        if(-not $revisions.Count){throw 'Owned filter requires a strong actual revision.'}
        foreach($revision in $revisions){
            if($revision -isnot [string] -or $revision -cnotmatch '^"[\x21\x23-\x7e\x80-\xff]*"$'){
                throw 'Owned filter revision must be one strong opaque ETag.'
            }
            if($revision -cne $revisions[0]){throw 'Owned filter HTTP/body revision conflict.'}
        }
        $etag=$revisions[0]
    }
    return @{ data = $data; etag = $etag }
}

function Get-MonitorNativeState {
    param([hashtable]$Context)
    $version = $(if ($Context.authorityAction -ceq 'BootstrapPlatform') { '2025-05-01-preview' } else { '2026-01-01' })
    $arm = Invoke-MonitorNativeRequest $Context Arm Get "$($Context.agent)?api-version=$version"
    $resource = $arm.data
    if ($resource.id -cne $Context.agent -or
        $resource.properties.agentEndpoint.TrimEnd('/') -cne $Context.endpoint -or
        $resource.properties.actionConfiguration.mode -cne 'Review' -or
        $resource.properties.actionConfiguration.accessLevel -cne 'Low') {
        throw 'Actual ARM agent identity, fixed endpoint or Review/Low configuration changed.'
    }
    $Context.lastArm = $resource
    $global = Invoke-MonitorNativeRequest $Context Sre Get '/api/v2/agent/settings/global'
    $filters = Invoke-MonitorNativeRequest $Context Sre Get '/api/v2/incidentManagement/incidentFilters'
    if ($filters.data.value -isnot [array] -or $filters.data.nextLink) {
        throw 'Native filter enumeration must be a complete value collection; do not silently truncate pagination.'
    }
    $agent = Invoke-MonitorNativeRequest $Context Sre Get '/api/v2/extendedAgent/asre-azure-monitor-review/permissions' -AllowNotFound
    $allow = @{ allow = @() }
    if ($null -ne $agent) { $allow = ConvertTo-MonitorAgentPermissions $agent.data }
    # ARM configuration has references only; refuse password/token material in
    # snapshots rather than echoing credentials or inventing restoration fields.
    $platform = $resource.properties.incidentManagementConfiguration
    foreach ($key in $platform.Keys) {
        if ($key -match '(?i)password|secret|token|credential') { throw 'Platform snapshot unexpectedly contains credential fields.' }
    }
    return @{
        agentResourceId = $resource.id; mode = 'Review'; accessLevel = 'Low'
        platformConfiguration = $platform; permissions = $global.data.permissions
        permissionsEtag = $global.etag; agentPermissions = $allow
        agentPermissionsEtag = $(if ($null -ne $agent) { $agent.etag } else { '' })
        armEtag = $arm.etag; filters = $filters.data.value
    }
}

function Test-MonitorProductionReadiness {
    param([hashtable]$Context, [hashtable]$Commissioning)
    $root = $Context.root
    $configuration = Get-Content -LiteralPath (Join-Path $root 'config/native-incident-runtime.json') -Raw |
        ConvertFrom-Json -AsHashtable -DateKind String
    if ($configuration.reader.native_dto_version -cne 'azmonitor-incident/v1') {
        throw 'Production requires the observed native incident decoder.'
    }
    Assert-MonitorExact $configuration $Commissioning 'frozen-production-commissioning'
    $state = Get-MonitorNativeState $Context
    Assert-MonitorConnected $Context $state
    Assert-MonitorExact @{ allow = @(); ask = @(); deny = @() } $state.permissions 'production-global-policy'
    Assert-MonitorExact (Get-MonitorAgentPermissions) $state.agentPermissions 'production-agent-policy'
    $scanner = Get-MonitorScannerBinding
    if ($Context.lastArm.properties.actionConfiguration.identity -ine $scanner.identity_resource_id) {
        throw 'Production must retain the existing action identity.'
    }
    foreach ($resource in @(Get-MonitorReviewConfiguration -Root $root)) {
        $path = "/api/v2/extendedAgent/$($resource.kind)/$($resource.name)"
        $actual = (Invoke-MonitorNativeRequest $Context Sre Get $path).data
        if ($resource.name -ceq 'asre-review-evidence') {
            if ($actual.name -cne $resource.name -or $actual.type -cne $resource.type -or
                $actual.properties.type -cne 'PythonFunctionTool') { throw 'Retained evidence tool identity changed.' }
            Assert-MonitorExact $resource.properties.parameters $actual.properties.parameters 'retained-evidence-interface'
            continue
        }
        Assert-MonitorResource $resource $actual
    }
    # Configuration eligibility is not incident-to-case acceptance. The latter
    # still requires an automatic investigation and persisted case/journal.
    return @{
        status = 'verified'; authoritative_reader_bound = $true; commissioning_bound = $true
        scanner_permissions_verified = $true; selected_tools_no_approval = $true
        commissioning = $configuration; runtime_accepted = $false
    }
}

function Publish-MonitorHandler {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$JournalPath, [switch]$ExpandProfiles,
        [string]$Root = (Split-Path $PSScriptRoot -Parent))
    if ((Get-MonitorManifest -Root $Root).phase -cne 'Production') { throw 'Production source required.' }
    if ($ExpandProfiles) {
        $configuration = Get-Content -LiteralPath (Join-Path $Root 'config/native-incident-runtime.json') -Raw |
            ConvertFrom-Json -AsHashtable -DateKind String
        if (-not $configuration.ContainsKey('profiles') -or ($configuration.profiles -join ',') -cne
            ((Get-MonitorManifest -Root $Root).case_profiles -join ',')) {
            throw 'Route expansion requires protected commissioning for the exact released profiles.'
        }
    }
    $context = Get-MonitorNativeContext -Action PublishHandler -Root $Root
    $before = Get-MonitorNativeState $context
    Assert-MonitorConnected $context $before
    Assert-MonitorExact @{ allow = @(); ask = @(); deny = @() } $before.permissions 'unchanged-global-baseline'
    $route = @($before.filters | Where-Object name -CEQ 'asre-azure-monitor-review')
    if ($route.Count -ne 1 -or $route[0].properties.isEnabled -isnot [bool] -or
        -not $route[0].properties.isEnabled -or $route[0].properties.incidentPlatform -cne 'AzMonitor' -or
        $route[0].properties.handlingAgent -cne 'asre-azure-monitor-review') {
        throw 'Preserve the existing enabled native incident route.'
    }
    $resources = @(Get-MonitorReviewConfiguration -Root $Root | Where-Object name -CNE 'asre-review-evidence')
    $evidencePath = '/api/v2/extendedAgent/tools/asre-review-evidence'
    $retainedEvidence = (Invoke-MonitorNativeRequest $context Sre Get $evidencePath).data
    $context.handlerPreimages = @{}
    $context.handlerExpandProfiles = $ExpandProfiles.IsPresent
    $context.handlerSubmitted = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    $context.handlerJournal = [IO.File]::Open($JournalPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
    try {
        Write-MonitorBootstrapRecord $context.handlerJournal @{ event = 'Begin'; state = $before
            retained_evidence = $retainedEvidence
            content_cas = $false; operating_condition = 'exclusive coordinated publication' }
        foreach ($resource in $resources) {
            $path = "/api/v2/extendedAgent/$($resource.kind)/$($resource.name)"
            $current = Invoke-MonitorNativeRequest $context Sre Get $path
            $context.handlerPreimages[$path] = $current
            Write-MonitorBootstrapRecord $context.handlerJournal @{ event = 'Preimage'; path = $path; resource = $current.data }
            $desired = $true
            try { Assert-MonitorResource $resource $current.data } catch { $desired = $false }
            if ($resource.kind -ceq 'agents') {
                $policyPath = '/api/v2/extendedAgent/asre-azure-monitor-review/permissions'
                $policy = Invoke-MonitorNativeRequest $context Sre Get $policyPath
                $context.handlerPreimages[$policyPath] = $policy
                Write-MonitorBootstrapRecord $context.handlerJournal @{ event = 'Preimage'; path = $policyPath; resource = $policy.data }
                $expected = Get-MonitorAgentPermissions -Root $Root
                if ((Get-MonitorObjectSha256 (ConvertTo-MonitorAgentPermissions $policy.data)) -cne
                    (Get-MonitorObjectSha256 $expected)) {
                    $null = Invoke-MonitorNativeRequest $context Sre Put $policyPath -Body $expected -IfMatch $policy.etag
                    $permissionReadback = (Invoke-MonitorNativeRequest $context Sre Get $policyPath).data
                    Assert-MonitorExact $expected (ConvertTo-MonitorAgentPermissions $permissionReadback) 'handler-policy-readback'
                    $projected = $current.data | ConvertTo-Json -Depth 40 |
                        ConvertFrom-Json -AsHashtable -DateKind String
                    $projected.properties.permissions = $permissionReadback
                    $updated = Invoke-MonitorNativeRequest $context Sre Get $path
                    Assert-MonitorExact $projected $updated.data 'owned-permission-projection-only'
                    $current = $updated
                    $context.handlerPreimages[$path] = $current
                    Write-MonitorBootstrapRecord $context.handlerJournal @{
                        event = 'OwnedPermissionProjection'; path = $path; resource = $current.data
                    }
                }
            }
            if ($desired) { continue }
            $body = @{ name = $resource.name; type = $resource.type; properties = $resource.properties; tags = @() }
            $null = Invoke-MonitorNativeRequest $context Sre Put $path -Body $body -IfMatch $current.etag
            $actual = (Invoke-MonitorNativeRequest $context Sre Get $path).data
            Assert-MonitorResource $resource $actual
            Write-MonitorBootstrapRecord $context.handlerJournal @{ event = 'Readback'; path = $path; resource = $actual }
        }
        $expectedFilters = $before.filters
        if ($ExpandProfiles) {
            $plan = (Get-MonitorManifest -Root $Root).response_plan
            $properties = $plan.properties.Clone()
            $properties.isEnabled = $true
            $expanded = @{ name = $plan.name; type = $plan.type; properties = $properties }
            $routePath = '/api/v2/incidentManagement/incidentFilters/asre-azure-monitor-review'
            $current = Invoke-MonitorNativeRequest $context Sre Get $routePath
            $alreadyExpanded = $true
            try { Assert-MonitorResource $expanded $current.data } catch { $alreadyExpanded = $false }
            if (-not $alreadyExpanded) {
                # Preserve intake while expanding only the released title/severity filters.
                $old = $properties.Clone()
                $old.priorities = @('Sev1')
                $compatible = $false
                foreach ($title in @('asre-demo-disk', 'asre-demo-')) {
                    $old.titleContains = $title
                    try {
                        Assert-MonitorResource @{ name = $plan.name; type = $plan.type; properties = $old } $current.data
                        $compatible = $true
                        break
                    } catch { $compatible = $false }
                }
                if (-not $compatible) { throw 'Only the exact existing enabled Sev1 route can be expanded.' }
                $context.handlerPreimages[$routePath] = $current
                Write-MonitorBootstrapRecord $context.handlerJournal @{
                    event = 'Preimage'; path = $routePath; resource = $current.data
                }
                $null = Invoke-MonitorNativeRequest $context Sre Patch $routePath -Body $expanded -IfMatch $current.etag
                $actual = (Invoke-MonitorNativeRequest $context Sre Get $routePath).data
                Assert-MonitorResource $expanded $actual
                Write-MonitorBootstrapRecord $context.handlerJournal @{
                    event = 'Readback'; path = $routePath; resource = $actual
                }
            } else { $actual = $current.data }
            $expectedFilters = @($before.filters | ForEach-Object {
                if ($_.name -ceq $plan.name) { $actual } else { $_ }
            })
        }
        $after = Get-MonitorNativeState $context
        foreach ($key in @('platformConfiguration','permissions')) {
            Assert-MonitorExact $before[$key] $after[$key] "unchanged-$key"
        }
        Assert-MonitorExact $expectedFilters $after.filters 'source-expanded-or-unchanged-filters'
        Assert-MonitorExact $retainedEvidence (Invoke-MonitorNativeRequest $context Sre Get $evidencePath).data 'unchanged-retained-evidence'
        $commissioning = Get-Content -LiteralPath (Join-Path $Root 'config/native-incident-runtime.json') -Raw |
            ConvertFrom-Json -AsHashtable -DateKind String
        $proof = Test-MonitorProductionReadiness $context $commissioning
        Write-MonitorBootstrapRecord $context.handlerJournal @{ event = 'Verified'; proof = $proof; state = $after }
        return @{ status = 'verified'; runtime_accepted = $false; route_enabled = $true
            submitted = @($context.handlerSubmitted); journal = $JournalPath }
    } finally { $context.handlerJournal.Dispose() }
}

function Invoke-MonitorNativeOperation {
    param([hashtable]$Context, [string]$Operation, [hashtable]$Request)
        switch ($Operation) {
            ReadState { return Get-MonitorNativeState $context }
            PublishResource {
                $r = $Request.resource
                $path = "/api/v2/extendedAgent/$($r.kind)/$($r.name)"
                if ($r.kind -ceq 'incidentFilters') { $path = "/api/v2/incidentManagement/incidentFilters/$($r.name)" }
                $current = Invoke-MonitorNativeRequest $context Sre Get $path -AllowNotFound
                $method = 'Put'
                if ($r.kind -ceq 'incidentFilters' -and $null -ne $current) { $method = 'Patch' }
                $body = @{ name = $r.name; type = $r.type; properties = $r.properties }
                if ($r.kind -cne 'incidentFilters') { $body.tags = @() }
                $null = Invoke-MonitorNativeRequest $context Sre $method $path -Body $body -IfMatch $(if ($current) { $current.etag } else { '' })
                return $true
            }
            ReadResource {
                $path = "/api/v2/extendedAgent/$($Request.kind)/$($Request.name)"
                if ($Request.kind -ceq 'incidentFilters') { $path = "/api/v2/incidentManagement/incidentFilters/$($Request.name)" }
                return (Invoke-MonitorNativeRequest $context Sre Get $path).data
            }
            SetAgentPermissions {
                if (@($Request.permissions.allow).Count) {
                    Assert-MonitorExact (Get-MonitorAgentPermissions) $Request.permissions 'fixed-agent-policy'
                } else { Assert-MonitorExact @{ allow = @() } $Request.permissions 'empty-agent-policy' }
                $current = Invoke-MonitorNativeRequest $context Sre Get '/api/v2/extendedAgent/asre-azure-monitor-review/permissions'
                if ([string]::IsNullOrWhiteSpace($current.etag)) { throw 'Per-agent permissions PUT requires the actual current ETag.' }
                $null = Invoke-MonitorNativeRequest $context Sre Put '/api/v2/extendedAgent/asre-azure-monitor-review/permissions' `
                    -Body $Request.permissions -IfMatch $current.etag
                return $true
            }
            SetPlatform {
                $state = Get-MonitorNativeState $context
                if ([string]::IsNullOrWhiteSpace($state.armEtag)) { throw 'ARM mutation requires the current ETag.' }
                $null = Invoke-MonitorNativeRequest $context Arm Patch "$($context.agent)?api-version=2026-01-01" `
                    -Body @{ properties = @{ incidentManagementConfiguration = $Request.configuration } } -IfMatch $state.armEtag
                return $true
            }
            VerifyRuntime {
                if ((Get-MonitorManifest -Root (Split-Path $PSScriptRoot -Parent)).phase -ceq 'SchemaDiscovery') {
                    return Test-MonitorDiscoveryReadiness -Context $Context
                }
                return Test-MonitorProductionReadiness -Context $Context -Commissioning $Request.commissioning
            }
            default { throw 'Default adapter does not expose global policy writes, cases, credentials, shell or fault operations.' }
        }
}

function Invoke-MonitorTransport {
    param([scriptblock]$Transport, [string]$Operation, [hashtable]$Request = @{})
    if (-not $Transport) { throw 'No verified runtime transport adapter installed. No external action was attempted.' }
    $knownOperation = $Operation -cin @('ReadState','PublishResource','ReadResource','SetAgentPermissions','SetPlatform','VerifyRuntime')
    $safeOperation = $(if ($knownOperation) { $Operation } else { 'UnknownOperation' })
    try { $result = & $Transport $Operation $Request }
    catch {
        $message = "Monitor adapter failed during $safeOperation; outcome may be unknown. Re-read before retry; response details suppressed."
        $meta = $_.Exception.Data
        $category = $meta['MonitorNativeCategory']
        $method = $meta['MonitorNativeMethod']
        $target = $meta['MonitorNativeTarget']
        $valid = $knownOperation -and $category -is [string] -and
            $category -cin @('HttpFailure','TransportFailure','ParseFailure') -and
            $method -is [string] -and $method -cin @('Get','Put','Patch') -and
            $target -is [string] -and $target -cin @('OwnedAgent','OwnedIncidentFilter','AgentPermissions','OtherAllowedRequest')
        if ($category -ceq 'HttpFailure') {
            $status = $meta['MonitorNativeStatus']
            $extended = $meta.Count -eq 5 -and $Operation -ceq 'PublishResource' -and
                $status -is [int] -and $status -eq 400 -and $method -ceq 'Put' -and $target -ceq 'OwnedIncidentFilter' -and
                $meta['MonitorNativeValidation'] -is [string] -and
                [string]::Equals($meta['MonitorNativeValidation'], 'IncidentPlatformConfigurationPending', [StringComparison]::Ordinal)
            $valid = $valid -and ($meta.Count -eq 4 -or $extended) -and $status -is [int] -and
                $status -ge 100 -and $status -le 599 -and ($status -lt 200 -or $status -ge 300)
            $detail = $(if ($valid) { "HTTP $status" } else { '' })
            if ($valid -and $extended) { $detail += '; IncidentPlatformConfigurationPending' }
        } else {
            $valid = $valid -and $meta.Count -eq 3
            $detail = $(if ($category -ceq 'TransportFailure') { 'transport-failure' } else { 'parse-failure' })
        }
        if ($valid) {
            $message = "Monitor adapter failed during $safeOperation ($detail; $method; $target); outcome may be unknown. Re-read before retry; response details suppressed."
        }
        # Never retain the original exception/response as inner or target data.
        throw [InvalidOperationException]::new($message)
    }
    if ($null -eq $result) { throw "Monitor adapter omitted $safeOperation readback." }
    return $result
}

function Get-MonitorState {
    param([scriptblock]$Transport)
    # Adapter contract, NOT a guessed native DTO: normalize ARM Review/Low,
    # platform configuration, effective global permissions+ETag, native filters.
    $state = Invoke-MonitorTransport $Transport 'ReadState'
    $s = $state | ConvertTo-Json -Depth 40 | ConvertFrom-Json -AsHashtable -DateKind String
    foreach ($key in @('agentResourceId', 'mode', 'accessLevel', 'platformConfiguration', 'permissions', 'permissionsEtag', 'agentPermissions', 'filters')) {
        if (-not $s.ContainsKey($key)) { throw "Runtime schema gap: state omitted $key." }
    }
    $agent = '/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.App/agents/asre-demo-cus'
    if ($s.agentResourceId -cne $agent -or $s.mode -cne 'Review' -or $s.accessLevel -cne 'Low' -or $s.filters -isnot [array] -or
        [string]::IsNullOrWhiteSpace($s.platformConfiguration.type)) { throw 'Runtime is not the exact Review/Low native state.' }
    foreach ($key in @('allow', 'ask', 'deny')) {
        if (-not $s.permissions.ContainsKey($key) -or $s.permissions[$key] -isnot [array]) { throw 'Effective permissions schema gap.' }
    }
    foreach ($f in $s.filters) {
        if ($f.type -cne 'IncidentFilter' -or [string]::IsNullOrWhiteSpace($f.name) -or
            $f.properties.isEnabled -isnot [bool]) { throw 'Native filters require real named resource envelopes and typed isEnabled.' }
    }
    if (@($s.filters | Group-Object name | Where-Object Count -gt 1).Count) { throw 'Duplicate native response plans are ambiguous.' }
    return $s
}

function Get-MonitorPermissions {
    param([hashtable]$Binding, [string]$Root)
    if (-not $Binding) { return Get-MonitorAgentPermissions }
    # Optional injected bindings are for offline fixtures only. The public
    # entry point exposes neither bindings nor executable transport input.
    $d = Get-MonitorCaseDeclaration -Root $Root
    $selected = @('QueryLogAnalyticsByResourceId', 'ReadFile', 'SearchMemory', 'asre-review-evidence', $d.name)
    if (-not $Binding -or -not $Binding.ContainsKey('toolPatterns')) { throw 'Runtime schema gap: exact effective tool-policy patterns are not verified.' }
    Assert-MonitorExact ($selected | Sort-Object) (@($Binding.toolPatterns.Keys) | Sort-Object) 'tool-pattern-bindings'
    $patterns = @($selected | ForEach-Object { $Binding.toolPatterns[$_] })
    if (@($patterns | Where-Object { $_ -isnot [string] -or [string]::IsNullOrWhiteSpace($_) -or $_ -match '[*?]' }).Count -or
        @($patterns | Sort-Object -Unique).Count -ne $patterns.Count) { throw 'Policy patterns must be exact unique selected tools, not wildcards.' }
    return @{ allow = $patterns }
}

function Assert-MonitorCommissioning {
    param([hashtable]$Commissioning, [DateTimeOffset]$Now = [DateTimeOffset]::UtcNow,
        [string]$Root = (Split-Path $PSScriptRoot -Parent))
    $manifest = Get-MonitorManifest -Root $Root
    if (-not $Commissioning -or -not $Commissioning.ContainsKey('version') -or
        $Commissioning.version -cne $manifest.commissioning.version -or -not $Commissioning.ContainsKey('not_before') -or
        -not $Commissioning.ContainsKey('historical_alert_ids') -or
        $Commissioning.historical_alert_ids -isnot [array]) { throw 'An explicit commissioning boundary and historical exclusions are required.' }
    foreach ($key in @('not_before','expires_at' | Where-Object { $Commissioning.ContainsKey($_) })) {
        if ($Commissioning[$key] -isnot [string] -or
            $Commissioning[$key] -cnotmatch '^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,7})?(?:Z|\+00:00)$') {
            throw 'Commissioning timestamps must be explicit UTC instants.'
        }
    }
    $start = [DateTimeOffset]::Parse($Commissioning.not_before)
    if ($start.Offset -ne [TimeSpan]::Zero -or
        $start -lt [DateTimeOffset]::Parse($manifest.commissioning.minimum_not_before) -or
        $Now -lt $start) { throw 'Commissioning must respect the reviewed UTC minimum and historical exclusions.' }
    if ($Commissioning.ContainsKey('expires_at')) {
        $end = [DateTimeOffset]::Parse($Commissioning.expires_at)
        if ($end -le $start -or $Now -ge $end) { throw 'Commissioning optional expiry has passed or is invalid.' }
    }
}

function Get-MonitorUtcNow { return [DateTimeOffset]::UtcNow }

function Assert-MonitorActivationWindow {
    param([hashtable]$Window, [switch]$Discovery)
    if (-not $Window -or $Window.Keys.Count -ne 2 -or
        -not $Window.ContainsKey('not_before') -or -not $Window.ContainsKey('expires_at')) {
        throw 'An explicit finite activation/test authority window is required; it is not the ongoing tool lifetime.'
    }
    foreach ($key in @('not_before','expires_at')) {
        if ($Window[$key] -isnot [string] -or $Window[$key] -cnotmatch '(?:Z|\+00:00)$') {
            throw 'Activation authority window requires explicit UTC timestamps.'
        }
    }
    $now = Get-MonitorUtcNow
    $start = [DateTimeOffset]::Parse($Window.not_before)
    $end = [DateTimeOffset]::Parse($Window.expires_at)
    if ($end -le $start -or $now -lt $start -or $now -ge $end) { throw 'Activation/test authority window is not currently valid.' }
    if ($Discovery -and ($end - $start).TotalSeconds -gt 600) { throw 'SchemaDiscovery authority window must be exclusive and at most 600 seconds.' }
}

function Read-MonitorPublicationArtifact {
    param([string]$Directory, [string]$Root)
    if ([string]::IsNullOrWhiteSpace($Directory)) { throw 'Activation requires the complete protected publication artifact, not a newly generated package.' }
    $base = [IO.Path]::GetFullPath($Directory).TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    if ((Get-Item -LiteralPath $base).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Protected artifact root is a reparse point.' }
    $path = Join-Path $base 'configuration-manifest.json'
    $receipt = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json -AsHashtable -DateKind String
    if ($receipt.artifact_kind -cne 'asre-configuration/v1' -or $receipt.profile -cne 'asre-configuration' -or
        $receipt.publication_action -cne 'PublishDisabled' -or $receipt.runtime_accepted -isnot [bool] -or $receipt.runtime_accepted -or
        $receipt.agent_resource_id -cne '/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.App/agents/asre-demo-cus' -or
        $receipt.agent_endpoint -cne 'https://asre-demo-cus--0a1b2c3d.4e5f6a7b.centralus.azuresre.ai' -or
        $receipt.tenant_id -cne '00000002-000a-4000-9000-000000000002' -or
        $receipt.subscription_id -cne '00000001-000a-4000-a000-000000000001' -or
        $receipt.source_commit -cnotmatch '^[0-9a-f]{40}$' -or $receipt.template_commit -cnotmatch '^[0-9a-f]{40}$' -or
        $receipt.source_branch -cne 'refs/heads/master' -or $receipt.validation_run_id -cnotmatch '^[1-9][0-9]*$' -or
        $receipt.files -isnot [Collections.IDictionary]) { throw 'Protected publication artifact context changed.' }
    # Read ONLY literal allowlists from the trusted local release source. Do not
    # invoke D Verify (which builds), execute artifact code, or duplicate its seal.
    $tokens = $null; $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $Root 'scripts/Invoke-IncidentRelease.ps1'), [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw 'Trusted release source did not parse.' }
    $required = @('generated/case-tool.py')
    foreach ($name in @('SourceFiles','TemplateFiles')) {
        $assignments = @($ast.FindAll({
            param($node)
            $node -is [Management.Automation.Language.AssignmentStatementAst] -and
                $node.Left.Extent.Text -ceq ('$' + $name)
        }, $true))
        if ($assignments.Count -ne 1) { throw 'Trusted release allowlist is not unique.' }
        if ($assignments[0].Right -isnot [Management.Automation.Language.CommandExpressionAst]) {
            throw 'Trusted release allowlist is not a literal expression.'
        }
        $values = $assignments[0].Right.Expression.SafeGetValue()
        $prefix = $(if ($name -ceq 'SourceFiles') { 'source/' } else { 'templates/' })
        $required += @($values | ForEach-Object { $prefix + $_ })
    }
    Assert-MonitorExact @($required | Sort-Object) @($receipt.files.Keys | Sort-Object) 'complete-protected-file-closure'
    foreach ($name in $receipt.files.Keys) {
        if ($name -cnotmatch '^(?:source|templates|generated)/[A-Za-z0-9_./-]+$' -or $name.Split('/') -contains '..' -or
            $receipt.files[$name] -cnotmatch '^[0-9a-f]{64}$') { throw 'Unsafe protected artifact path/hash.' }
        $file = Join-Path $base $name
        $part = Get-Item -LiteralPath $file
        while ($part.FullName -ne $base) {
            if ($part.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Protected artifact contains a reparse point.' }
            $part = $(if ($part -is [IO.DirectoryInfo]) { $part.Parent } else { $part.Directory })
            if ($null -eq $part) { throw 'Protected artifact path escaped its root.' }
        }
        if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -cne $receipt.files[$name]) {
            throw "Protected artifact hash mismatch: $name"
        }
        if ($name.StartsWith('source/', [StringComparison]::Ordinal)) {
            $current = Join-Path $Root $name.Substring(7)
            if ((Get-FileHash -LiteralPath $current -Algorithm SHA256).Hash.ToLowerInvariant() -cne $receipt.files[$name]) {
                throw "Activation source differs from the complete protected publication: $name"
            }
        }
    }
    $config = Get-Content -LiteralPath (Join-Path $base 'source/config/native-incident-runtime.json') -Raw | ConvertFrom-Json -AsHashtable -DateKind String
    Assert-MonitorCommissioning -Commissioning $config -Root $Root
    return @{ source = [IO.File]::ReadAllText((Join-Path $base 'generated/case-tool.py')); configuration = $config
        source_commit = $receipt.source_commit; template_commit = $receipt.template_commit
        release_context = $receipt
        manifest_sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() }
}

function Test-MonitorConnectedProfile {
    param([hashtable]$Profile)
    return $null -ne $Profile -and (
        [string]::Equals($Profile.version,'2.0-connected',[StringComparison]::Ordinal) -or
        [string]::Equals($Profile.version,'3.0-adopted',[StringComparison]::Ordinal))
}

function Get-MonitorPublicationOrigin {
    # Portable, reviewed custody of the exact immutable reconciliation92 bytes.
    # No Windows journal is transported to the Linux publisher; no operation
    # headers or credentials occur in this receipt. CI198 qualification:
    # 000000000000000000000000000000000000000000000000000000000000004a.
    $encoded = 'REDACTED-ENCODED-LAB-PAYLOAD'
    $bytes = [Convert]::FromBase64String($encoded)
    Assert-MonitorBootstrapBinding '000000000000000000000000000000000000000000000000000000000000013a' (
        [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant())
    $b = [Text.Encoding]::UTF8.GetString($bytes) | ConvertFrom-Json -AsHashtable -DateKind String
    foreach ($pair in @(@('source_commit','000000000000000000000000000000000000008a'),
        @('template_commit','000000000000000000000000000000000000006a'),
        @('publication_manifest_sha256','000000000000000000000000000000000000000000000000000000000000016a'),
        @('runtime_accepted',$false),@('status','connected-reconciled'))) {
        Assert-MonitorBootstrapBinding $pair[1] $b[$pair[0]]
    }
    Assert-MonitorBootstrapBinding '000000000000000000000000000000000000000000000000000000000000010a' $b.origin.journal_sha256
    Assert-MonitorBootstrapBinding $b.connected_snapshot_sha256 (Get-MonitorObjectSha256 $b.connected_snapshot)
    if ($b.connected_snapshot.filters.Count) { throw 'Historical migration snapshot must remain zero-plan.' }
    return $b
}

function Assert-MonitorAdoptedState {
    param([hashtable]$RootState, [hashtable]$State, [string]$Root)
    $plan = (Get-MonitorManifest -Root $Root).response_plan
    if ($State.filters -isnot [array] -or $State.filters.Count -gt 1) { throw 'Adoption permits zero or one owned disabled route.' }
    foreach ($filter in $State.filters) {
        Assert-MonitorResource $plan $filter
        if ($filter.properties.isEnabled -isnot [bool] -or $filter.properties.isEnabled) { throw 'Adoption requires a Boolean disabled route.' }
    }
    if ($State.agentPermissionsEtag -isnot [string] -or [string]::IsNullOrWhiteSpace($State.agentPermissionsEtag)) { throw 'Adoption requires the actual agent policy ETag.' }
    Assert-MonitorPlatform $State.platformConfiguration
    $expected = $RootState | ConvertTo-Json -Depth 40 | ConvertFrom-Json -AsHashtable -DateKind String
    $expected.filters = $State.filters
    $expected.agentPermissionsEtag = $State.agentPermissionsEtag
    Assert-MonitorBootstrapBinding $expected $State
}

function Assert-MonitorSchemaDiscoveryAdoptedState {
    param([hashtable]$RootState, [hashtable]$State, [string]$Root)
    # Revision drift is compatible only after proving both exact empty policies.
    # This is not transaction stability, writer attribution or evidence repinning.
    foreach ($candidate in @($RootState, $State)) {
        $policy = $candidate['permissions']
        if ($policy -isnot [Collections.IDictionary] -or $policy.get_Count() -ne 3) {
            throw 'SchemaDiscovery adoption requires the exact supported empty global policy.'
        }
        foreach ($key in $policy.get_Keys()) {
            if ($key -isnot [string] -or $key -cnotin @('allow','ask','deny') -or
                $policy[$key] -isnot [array] -or $policy[$key].Count -ne 0) {
                throw 'SchemaDiscovery adoption requires the exact supported empty global policy.'
            }
        }
        if ($candidate['permissionsEtag'] -isnot [string] -or
            [string]::IsNullOrWhiteSpace($candidate['permissionsEtag'])) {
            throw 'SchemaDiscovery adoption requires a typed nonblank global policy revision.'
        }
    }
    Assert-MonitorBootstrapBinding $RootState.permissions $State.permissions
    $expected = $RootState | ConvertTo-Json -Depth 40 | ConvertFrom-Json -AsHashtable -DateKind String
    $expected.permissionsEtag = $State.permissionsEtag
    Assert-MonitorAdoptedState $expected $State $Root
}

function Assert-MonitorRetainedIdentity {
    param([hashtable]$Context)
    $scanner = Get-MonitorScannerBinding
    if ($Context -isnot [Collections.IDictionary] -or $Context['lastArm'] -isnot [Collections.IDictionary]) {
        throw 'Retained identity envelope is malformed.'
    }
    $arm = $Context.lastArm
    if ($arm['identity'] -isnot [Collections.IDictionary] -or $arm['properties'] -isnot [Collections.IDictionary] -or
        $arm['properties']['actionConfiguration'] -isnot [Collections.IDictionary]) {
        throw 'Retained identity envelope is malformed.'
    }
    $identities = $arm['identity']['userAssignedIdentities']
    $actionIdentity = $arm['properties']['actionConfiguration']['identity']
    if ($identities -isnot [Collections.IDictionary] -or $actionIdentity -isnot [string]) {
        throw 'Retained identity fields have invalid types.'
    }
    # These fixed ARM IDs are ASCII. Reject aliases before case folding,
    # including a confusable alongside a valid key rather than discarding it.
    if ($actionIdentity.Length -eq 0 -or [regex]::IsMatch($actionIdentity, '[^\x21-\x7e]')) {
        throw 'Retained action identity is not an ASCII resource ID.'
    }
    $keys = [Collections.Generic.List[string]]::new()
    foreach ($key in $identities.get_Keys()) {
        if ($key -isnot [string] -or $key.Length -eq 0 -or [regex]::IsMatch($key, '[^\x21-\x7e]')) {
            throw 'Retained identity map key is not an ASCII resource ID.'
        }
        if ([StringComparer]::OrdinalIgnoreCase.Equals($key, $scanner.identity_resource_id)) { $keys.Add($key) }
    }
    if ($keys.Count -ne 1) { throw 'Retained action identity is missing or ambiguous.' }
    if (-not [StringComparer]::OrdinalIgnoreCase.Equals($actionIdentity, $scanner.identity_resource_id)) {
        throw 'Retained action identity does not match.'
    }
    $matched = $identities[$keys[0]]
    if ($matched -isnot [Collections.IDictionary] -or $matched['principalId'] -isnot [string]) {
        throw 'Retained identity principal has an invalid type.'
    }
    if (-not [StringComparer]::Ordinal.Equals($scanner.principal_id, $matched['principalId'])) {
        throw 'Bootstrap exact binding changed.'
    }
}

function Get-MonitorProtectedPublicationRun {
    param([hashtable]$Artifact)
    $run = @{}
    foreach ($pair in @(@('collection_uri','SYSTEM_COLLECTIONURI'),@('project_id','SYSTEM_TEAMPROJECTID'),
        @('definition_id','SYSTEM_DEFINITIONID'),@('repository_id','BUILD_REPOSITORY_ID'))) {
        $value = [Environment]::GetEnvironmentVariable($pair[1])
        Assert-MonitorBootstrapBinding $Artifact.release_context[$pair[0]] $value
        $run[$pair[0]] = $value
    }
    foreach ($pair in @(@('BUILD_SOURCEVERSION',$Artifact.source_commit),@('TEMPLATE_SOURCE_COMMIT',$Artifact.template_commit),
        @('BUILD_SOURCEBRANCH','refs/heads/master'),@('BUILD_REASON','Manual'),@('BUILD_REPOSITORY_PROVIDER','TfsGit'))) {
        Assert-MonitorBootstrapBinding $pair[1] ([Environment]::GetEnvironmentVariable($pair[0]))
    }
    if ($env:BUILD_BUILDID -cnotmatch '^[1-9][0-9]*\z' -or
        [string]::Equals($env:BUILD_BUILDID,$Artifact.release_context.validation_run_id,[StringComparison]::Ordinal)) {
        throw 'Publication needs its separate protected run, not validation authority.'
    }
    $run.validation_run_id = $Artifact.release_context.validation_run_id
    $run.publication_run_id = $env:BUILD_BUILDID
    return $run
}

function Test-MonitorAdoptedPublicationChain {
    param([hashtable]$Context)
    $b = Get-MonitorPublicationOrigin
    $p = $Context.publicationReceipt
    $c = $Context.publicationContext
    $artifact = $Context.qualifyingArtifact
    if ([string]::Equals($artifact.manifest_sha256,$b.publication_manifest_sha256,[StringComparison]::Ordinal)) {
        throw 'Historical qualification is not current release authority.'
    }
    Assert-MonitorBootstrapBinding $b $Context.bootstrapReceipt
    Assert-MonitorBootstrapBinding (Get-MonitorObjectSha256 $b) $Context.probeProfile.bootstrap_receipt_sha256
    Assert-MonitorBootstrapBinding (Get-MonitorObjectSha256 $p) $Context.probeProfile.publication_receipt_sha256
    Assert-MonitorBootstrapBinding (Get-MonitorObjectSha256 $c) $Context.probeProfile.publication_context_sha256
    Assert-MonitorBootstrapBinding @('version','phase','agent_resource_id','origin_receipt_sha256','publication_manifest_sha256',
        'source_commit','template_commit','agent_definition_sha256','adopted_pre_snapshot_sha256','adopted_pre_snapshot',
        'paused_snapshot_sha256','publication_receipt_sha256','protected_run','runtime_accepted' | Sort-Object) @($c.Keys | Sort-Object)
    foreach ($pair in @(@('version','asre-monitor-publication-context/v1'),@('phase','SchemaDiscovery'),
        @('agent_resource_id',$Context.agent),@('runtime_accepted',$false),
        @('origin_receipt_sha256','000000000000000000000000000000000000000000000000000000000000013a'),
        @('publication_manifest_sha256',$artifact.manifest_sha256),@('source_commit',$artifact.source_commit),
        @('template_commit',$artifact.template_commit),@('agent_definition_sha256',$Context.probeProfile.agent_definition_sha256),
        @('publication_receipt_sha256',(Get-MonitorObjectSha256 $p)),
        @('adopted_pre_snapshot_sha256',(Get-MonitorObjectSha256 $c.adopted_pre_snapshot)),
        @('paused_snapshot_sha256',(Get-MonitorObjectSha256 $Context.snapshot)))) {
        Assert-MonitorBootstrapBinding $pair[1] $c[$pair[0]]
    }
    Assert-MonitorBootstrapBinding @('version','agent_resource_id','phase','pre_snapshot_sha256','paused_snapshot_sha256',
        'agent_definition_sha256','runtime_accepted' | Sort-Object) @($p.Keys | Sort-Object)
    foreach ($pair in @(@('version','asre-monitor-publication-receipt/v1'),@('agent_resource_id',$Context.agent),
        @('phase','SchemaDiscovery'),@('runtime_accepted',$false),
        @('pre_snapshot_sha256',$c.adopted_pre_snapshot_sha256),@('paused_snapshot_sha256',$c.paused_snapshot_sha256),
        @('agent_definition_sha256',$c.agent_definition_sha256))) { Assert-MonitorBootstrapBinding $pair[1] $p[$pair[0]] }
    Assert-MonitorSchemaDiscoveryAdoptedState $b.connected_snapshot $c.adopted_pre_snapshot $Context.root
    Assert-MonitorAdoptedState $c.adopted_pre_snapshot $Context.snapshot $Context.root
    if ($Context.snapshot.filters.Count -ne 1) { throw 'Publication must finish with exactly the owned disabled route.' }
    $run = $c.protected_run
    Assert-MonitorBootstrapBinding @('collection_uri','project_id','definition_id','repository_id','validation_run_id','publication_run_id' | Sort-Object) @($run.Keys | Sort-Object)
    foreach ($key in @('collection_uri','project_id','definition_id','repository_id','validation_run_id')) {
        Assert-MonitorBootstrapBinding $artifact.release_context[$key] $run[$key]
    }
    if ($run.publication_run_id -isnot [string] -or $run.publication_run_id -cnotmatch '^[1-9][0-9]*\z' -or
        [string]::Equals($run.publication_run_id,$run.validation_run_id,[StringComparison]::Ordinal)) { throw 'Invalid protected publication run.' }
    # Independently captured actual run response is required, not just hashes
    # or a caller supplied succeeded/ready flag. The probe pins its raw bytes.
    $path = Join-Path (Get-MonitorReconciliationPins).directory "auto-native-publication$($run.publication_run_id)-build-completed.json"
    $bytes = [IO.File]::ReadAllBytes($path)
    Assert-MonitorBootstrapBinding $Context.probeProfile.publication_build_sha256 (
        [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant())
    try { $build = [Text.Encoding]::UTF8.GetString($bytes) | ConvertFrom-Json -AsHashtable -DateKind String }
    catch { throw 'Protected publication run evidence did not parse.' }
    foreach ($pair in @(@('status','completed'),@('result','succeeded'),@('sourceVersion',$artifact.source_commit),
        @('sourceBranch','refs/heads/master'),@('reason','manual'),@('id',[long]::Parse($run.publication_run_id)))) {
        Assert-MonitorBootstrapBinding $pair[1] $build[$pair[0]]
    }
    Assert-MonitorBootstrapBinding ([long]::Parse($run.definition_id)) $build.definition.id
    Assert-MonitorBootstrapBinding $run.project_id $build.definition.project.id
    Assert-MonitorBootstrapBinding $run.repository_id $build.repository.id
    Assert-MonitorBootstrapBinding 'TfsGit' $build.repository.type
}

# Bootstrap is a one-shot migration, not the ordinary SetPlatform operation.
# Its local resource journal is never selected by attempt id or caller path.
function Get-MonitorBootstrapJournalDirectory {
    return Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'ASRE/native-bootstrap/asre-demo-cus'
}

function Assert-MonitorBootstrapBinding {
    param($Expected, $Actual)
    if (-not [string]::Equals((Get-MonitorObjectSha256 $Expected), (Get-MonitorObjectSha256 $Actual),
        [StringComparison]::Ordinal)) { throw 'Bootstrap exact binding changed.' }
}

function Assert-MonitorPlatform {
    param($Configuration)
    if ($Configuration -isnot [hashtable]) { throw 'Unsupported Monitor platform DTO.' }
    $expected = @{ type = 'AzMonitor'; connectionName = 'azmonitor'; oboUser = '' }
    if ($Configuration.Count -eq 6) {
        # Exact observed RP merge, bound to the frozen original preimage.
        $expected.connectionUrl = 'https://example-instance.service-now.com/'
        $expected.connectionKey = ''
        $expected.apiConnectionName = 'asre-demo-cus-servicenow-buq5o4th4p'
    }
    foreach ($key in $Configuration.Keys) {
        if ($Configuration[$key] -isnot [string]) { throw 'Unsupported Monitor platform field type.' }
    }
    Assert-MonitorBootstrapBinding $expected $Configuration
}

function Get-MonitorReconciliationPins {
    return @{
        directory = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.copilot/session-state/00000052-000a-4000-9000-000000000052/files'
        journal_sha256 = '000000000000000000000000000000000000000000000000000000000000010a'
        profile_sha256 = '000000000000000000000000000000000000000000000000000000000000018a'
        attempt_id = '00000040-000a-4000-a000-000000000040'
        source_commit = '000000000000000000000000000000000000002a'
        template_commit = '000000000000000000000000000000000000006a'
        manifest_sha256 = '000000000000000000000000000000000000000000000000000000000000005a'
        files = @{
            'auto-native-monitor-bootstrap78-profile.json' = '000000000000000000000000000000000000000000000000000000000000014a'
            'auto-native-monitor-bootstrap81-launch.json' = '000000000000000000000000000000000000000000000000000000000000007a'
            'auto-native-monitor-bootstrap81-process-result.json' = '000000000000000000000000000000000000000000000000000000000000008a'
            'auto-native-validation-197.build.json' = '000000000000000000000000000000000000000000000000000000000000012a'
            'auto-native-validation-197-artifact-qualification.json' = '000000000000000000000000000000000000000000000000000000000000003a'
        }
    }
}

function Read-MonitorReconciliationOrigin {
    $pins = Get-MonitorReconciliationPins
    $evidence = @{}
    try {
        foreach ($name in $pins.files.Keys) {
            $bytes = [IO.File]::ReadAllBytes((Join-Path $pins.directory $name))
            Assert-MonitorBootstrapBinding $pins.files[$name] (
                [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant())
            $evidence[$name] = [Text.Encoding]::UTF8.GetString($bytes) | ConvertFrom-Json -AsHashtable -DateKind String
        }
        $bytes = [IO.File]::ReadAllBytes((Join-Path (Get-MonitorBootstrapJournalDirectory) 'attempt.jsonl'))
        Assert-MonitorBootstrapBinding $pins.journal_sha256 (
            [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant())
        $records = @([Text.Encoding]::UTF8.GetString($bytes).Split("`n", [StringSplitOptions]::RemoveEmptyEntries) |
            ForEach-Object { ConvertFrom-Json -InputObject $_ -AsHashtable -DateKind String })
        Assert-MonitorBootstrapBinding @('Attempt','BeforeDelete','MutationResponse','BeforePatch','MutationResponse','Stopped') @($records.event)
        $profile = $evidence['auto-native-monitor-bootstrap78-profile.json']
        Assert-MonitorBootstrapBinding $pins.profile_sha256 (Get-MonitorObjectSha256 $profile)
        Assert-MonitorBootstrapBinding $profile $records[0].profile
        Assert-MonitorBootstrapBinding $pins.profile_sha256 $records[0].profile_sha256
        foreach ($pair in @(@('attempt_id',$pins.attempt_id),@('source_commit',$pins.source_commit),
            @('template_commit',$pins.template_commit),@('publication_manifest_sha256',$pins.manifest_sha256))) {
            Assert-MonitorBootstrapBinding $pair[1] $profile[$pair[0]]
        }
        foreach ($i in @(1,3)) { Assert-MonitorBootstrapBinding $pins.attempt_id $records[$i].attempt_id }
        foreach ($pair in @(@(2,'Delete','Sre'),@(4,'Patch','Arm'))) {
            $record = $records[$pair[0]]
            Assert-MonitorBootstrapBinding $pair[1] $record.method
            Assert-MonitorBootstrapBinding $pair[2] $record.service
            Assert-MonitorBootstrapBinding 202 $record.http_status
        }
        Assert-MonitorBootstrapBinding 'configured-but-unready' $records[5].status
        Assert-MonitorBootstrapBinding @{ category = 'SuppressedLocalFailure' } $records[5].failure
        $launch = $evidence['auto-native-monitor-bootstrap81-launch.json']
        $process = $evidence['auto-native-monitor-bootstrap81-process-result.json']
        foreach ($key in @('child_start_attempt_consumed','prior79_consumed_not_reused','prior_bootstrap_absent')) {
            Assert-MonitorBootstrapBinding $true $launch[$key]
        }
        Assert-MonitorBootstrapBinding 27 $launch.trusted_source_files_verified
        foreach ($pair in @(@('exit_code',1),@('deadline_terminated',$false),@('authority_consumed',$true),
            @('replay_allowed',$false),@('attempt_exists',$true),@('receipt_exists',$false),@('runtime_accepted',$false))) {
            Assert-MonitorBootstrapBinding $pair[1] $process[$pair[0]]
        }
        $start = [DateTimeOffset]::Parse($launch.at)
        $finish = [DateTimeOffset]::Parse($process.completed_at)
        if ($finish -lt $start -or $start -lt [DateTimeOffset]::Parse($profile.activation_window.not_before) -or
            $finish -gt [DateTimeOffset]::Parse($profile.activation_window.expires_at)) { throw 'Historical operation window mismatch.' }
        $build = $evidence['auto-native-validation-197.build.json']
        Assert-MonitorBootstrapBinding 197 $build.id
        Assert-MonitorBootstrapBinding 'completed' $build.status
        Assert-MonitorBootstrapBinding 'succeeded' $build.result
        Assert-MonitorBootstrapBinding $pins.source_commit $build.sourceVersion
        if ([DateTimeOffset]::Parse($build.finishTime) -gt $start) { throw 'Historical validation did not precede operation.' }
        $oldDirectory = Join-Path $pins.directory 'auto-native-validation-197-artifact'
        Assert-MonitorBootstrapBinding $pins.manifest_sha256 (
            Get-FileHash -LiteralPath (Join-Path $oldDirectory 'configuration-manifest.json') -Algorithm SHA256).Hash.ToLowerInvariant()
        # The fixed manifest authenticates historical source; never execute it.
        $old = Read-MonitorPublicationArtifact -Directory $oldDirectory -Root (Join-Path $oldDirectory 'source')
        Assert-MonitorBootstrapBinding $pins.source_commit $old.source_commit
        Assert-MonitorBootstrapBinding $pins.template_commit $old.template_commit
        return @{
            profile = $profile
            binding = @{
                attempt_id = $pins.attempt_id; profile_raw_sha256 = $pins.files['auto-native-monitor-bootstrap78-profile.json']
                profile_sha256 = $pins.profile_sha256; journal_sha256 = $pins.journal_sha256
                source_commit = $pins.source_commit; template_commit = $pins.template_commit
                publication_manifest_sha256 = $pins.manifest_sha256
                launch_sha256 = $pins.files['auto-native-monitor-bootstrap81-launch.json']
                process_result_sha256 = $pins.files['auto-native-monitor-bootstrap81-process-result.json']
                validation_build_sha256 = $pins.files['auto-native-validation-197.build.json']
                qualification_sha256 = $pins.files['auto-native-validation-197-artifact-qualification.json']
                terminal_status = 'configured-but-unready'
            }
        }
    } catch { throw 'Frozen consumed bootstrap origin evidence is missing, altered or invalid; no reconciliation.' }
}

function Assert-MonitorReconciliationReceipt {
    param([hashtable]$Context, [hashtable]$Receipt, [hashtable]$Artifact)
    Assert-MonitorBootstrapBinding @('version','status','phase','agent_resource_id','origin','publication_manifest_sha256',
        'source_commit','template_commit','agent_definition_sha256','original_snapshot_sha256',
        'connected_snapshot_sha256','connected_snapshot','activation_window','runtime_accepted' | Sort-Object) @($Receipt.Keys | Sort-Object)
    foreach ($pair in @(@('version','asre-monitor-bootstrap-reconciliation/v1'),@('status','connected-reconciled'),
        @('phase','SchemaDiscovery'),@('agent_resource_id',$Context.agent),@('runtime_accepted',$false),
        @('publication_manifest_sha256',$Artifact.manifest_sha256),@('source_commit',$Artifact.source_commit),
        @('template_commit',$Artifact.template_commit),
        @('agent_definition_sha256',(Get-MonitorObjectSha256 @(Get-MonitorReviewConfiguration -Root $Context.root)[0])))) {
        Assert-MonitorBootstrapBinding $pair[1] $Receipt[$pair[0]]
    }
    $origin = Read-MonitorReconciliationOrigin
    Assert-MonitorBootstrapBinding $origin.binding $Receipt.origin
    Assert-MonitorBootstrapBinding (Get-MonitorObjectSha256 $origin.profile.original_snapshot) $Receipt.original_snapshot_sha256
    Assert-MonitorBootstrapBinding (Get-MonitorObjectSha256 $Receipt.connected_snapshot) $Receipt.connected_snapshot_sha256
    Assert-MonitorBootstrapBinding @('expires_at','not_before') @($Receipt.activation_window.Keys | Sort-Object)
    foreach ($key in @('not_before','expires_at')) {
        if ($Receipt.activation_window[$key] -isnot [string] -or
            $Receipt.activation_window[$key] -cnotmatch '^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,7})?(?:Z|\+00:00)\z') {
            throw 'Reconciliation observation window changed schema.'
        }
    }
    $span = ([DateTimeOffset]::Parse($Receipt.activation_window.expires_at) -
        [DateTimeOffset]::Parse($Receipt.activation_window.not_before)).TotalSeconds
    if ($span -le 0 -or $span -gt 600) { throw 'Reconciliation observation window is not finite.' }
    Assert-MonitorPlatform $Receipt.connected_snapshot.platformConfiguration
    if ($Receipt.connected_snapshot.filters.Count -ne 0 -or
        [string]::Equals($Receipt.publication_manifest_sha256,$origin.binding.publication_manifest_sha256,[StringComparison]::Ordinal)) {
        throw 'Reconciliation must bind zero plans and its separate current qualification.'
    }
    $saved = Get-Content -LiteralPath (Join-Path (Get-MonitorBootstrapJournalDirectory) 'bootstrap-reconciliation.json') -Raw |
        ConvertFrom-Json -AsHashtable -DateKind String
    Assert-MonitorBootstrapBinding $saved $Receipt
}

function Invoke-MonitorBootstrapReconciliation {
    param([hashtable]$Context, [hashtable]$Artifact)
    if (-not $Context.readOnly -or -not [string]::Equals($Context.authorityAction,'ReconcileBootstrap',[StringComparison]::Ordinal) -or
        -not [string]::Equals((Get-MonitorManifest -Root $Context.root).phase,'SchemaDiscovery',[StringComparison]::Ordinal)) {
        throw 'Reconciliation requires its explicit read-only Discovery context.'
    }
    $directory = Get-MonitorBootstrapJournalDirectory
    $part = Get-Item -LiteralPath $directory
    while ($null -ne $part) {
        if ($part.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reconciliation journal cannot traverse a reparse point.' }
        $part = $part.Parent
    }
    $lock = [IO.File]::Open((Join-Path $directory 'resource.lock'),[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
    try {
        if ((Test-Path -LiteralPath (Join-Path $directory 'receipt.json')) -or
            (Test-Path -LiteralPath (Join-Path $directory 'bootstrap-reconciliation.json'))) { throw 'Reconciliation cannot overwrite or fabricate an existing receipt.' }
        $origin = Read-MonitorReconciliationOrigin
        if ([string]::Equals($Artifact.manifest_sha256,$origin.binding.publication_manifest_sha256,[StringComparison]::Ordinal)) {
            throw 'Historical artifact is not current reconciliation qualification.'
        }
        $arm = (Invoke-MonitorNativeRequest $Context Arm Get "$($Context.agent)?api-version=2025-05-01-preview").data
        Assert-MonitorBootstrapBinding 'Succeeded' $arm.properties.provisioningState
        $scanner = Get-MonitorScannerBinding
        Assert-MonitorBootstrapBinding $scanner.identity_resource_id $arm.properties.actionConfiguration.identity
        $identityKeys = @($arm.identity.userAssignedIdentities.Keys | Where-Object { $_ -ieq $scanner.identity_resource_id })
        if ($identityKeys.Count -ne 1) { throw 'Retained identity association changed.' }
        Assert-MonitorBootstrapBinding $scanner.principal_id $arm.identity.userAssignedIdentities[$identityKeys[0]].principalId
        $state = Get-MonitorNativeState $Context
        Assert-MonitorConnected $Context $state
        $expected = $origin.profile.original_snapshot | ConvertTo-Json -Depth 40 | ConvertFrom-Json -AsHashtable -DateKind String
        $expected.filters = @(); $expected.platformConfiguration = $state.platformConfiguration
        Assert-MonitorBootstrapBinding $expected $state
        Assert-MonitorBootstrapSafe $Context $state
        Assert-MonitorBootstrapBinding $arm.properties.incidentManagementConfiguration $state.platformConfiguration
        Assert-MonitorActivationWindow $Context.authorityWindow -Discovery
        # Revalidate immutable bytes immediately before the only local creation.
        Assert-MonitorBootstrapBinding $origin.binding (Read-MonitorReconciliationOrigin).binding
        $receipt = @{
            version = 'asre-monitor-bootstrap-reconciliation/v1'; status = 'connected-reconciled'; phase = 'SchemaDiscovery'
            agent_resource_id = $Context.agent; origin = $origin.binding
            publication_manifest_sha256 = $Artifact.manifest_sha256; source_commit = $Artifact.source_commit; template_commit = $Artifact.template_commit
            agent_definition_sha256 = Get-MonitorObjectSha256 @(Get-MonitorReviewConfiguration -Root $Context.root)[0]
            original_snapshot_sha256 = Get-MonitorObjectSha256 $origin.profile.original_snapshot
            connected_snapshot_sha256 = Get-MonitorObjectSha256 $state; connected_snapshot = $state
            activation_window = $Context.authorityWindow; runtime_accepted = $false
        }
        $file = [IO.FileStream]::new((Join-Path $directory 'bootstrap-reconciliation.json'),[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
        try { Write-MonitorBootstrapRecord $file $receipt } finally { $file.Dispose() }
        return $receipt
    } finally { $lock.Dispose() }
}

function Assert-MonitorConnected {
    param([hashtable]$Context, [hashtable]$State, [switch]$AllowPending)
    if (-not $AllowPending) {
        Assert-MonitorPlatform $State.platformConfiguration
    }
    $c = (Invoke-MonitorNativeRequest $Context Sre Get '/api/v2/incidentManagement/checkConnectivityDetailed').data
    if ($c -isnot [hashtable] -or -not $c.ContainsKey('success') -or -not $c.ContainsKey('incidentType') -or
        $c.success -isnot [bool] -or $c.incidentType -isnot [string]) {
        throw [IO.InvalidDataException]::new('Unsupported connectivity DTO types; no readiness.')
    }
    $fields = [Collections.Generic.HashSet[string]]::new([string[]]@(
        'success','incidentType','errorMessage','observedConfigurationRevision','observedApiConnectionName','observedConnectionStatus'), [StringComparer]::Ordinal)
    foreach ($key in $c.Keys) {
        if (-not $fields.Contains($key)) {
            throw [IO.InvalidDataException]::new('Unsupported connectivity DTO field; no readiness.')
        }
        if ($key -cne 'success' -and $null -ne $c[$key] -and $c[$key] -isnot [string]) {
            throw [IO.InvalidDataException]::new('Unsupported connectivity DTO type.')
        }
    }
    $target = [string]::Equals($c.incidentType, 'AzMonitor', [StringComparison]::Ordinal)
    $previous = $AllowPending -and [string]::Equals($c.incidentType, 'ServiceNow', [StringComparison]::Ordinal)
    if (-not $target -and -not $previous) { throw [IO.InvalidDataException]::new('Unexpected connectivity platform; no readiness.') }
    if ($target -and $c.success) { if ($AllowPending) { return 'ready' }; return }
    if ($AllowPending -and -not $c.success) { return 'pending' }
    if ($previous) {
        Assert-MonitorBootstrapBinding $Context.bootstrapOriginalConnectivity $c
        return 'pending'
    }
    throw 'Connected AzMonitor readiness is unavailable.'
}

function Assert-MonitorBootstrapSafe {
    param([hashtable]$Context, [hashtable]$State)
    Assert-MonitorBootstrapBinding @{ allow = @(); ask = @(); deny = @() } $State.permissions
    Assert-MonitorBootstrapBinding @{ allow = @() } $State.agentPermissions
    if ([string]::IsNullOrWhiteSpace($State.permissionsEtag) -or [string]::IsNullOrWhiteSpace($State.agentPermissionsEtag)) {
        throw 'Bootstrap requires actual global and agent permission ETags.'
    }
    $handler = (Invoke-MonitorNativeRequest $Context Sre Get '/api/v2/extendedAgent/agents/asre-azure-monitor-review').data
    $expected = @(Get-MonitorReviewConfiguration -Root $Context.root)[0]
    Assert-MonitorResource $expected $handler
    foreach ($key in @('name','type')) { Assert-MonitorBootstrapBinding $expected[$key] $handler[$key] }
    foreach ($key in $expected.properties.Keys) { Assert-MonitorBootstrapBinding $expected.properties[$key] $handler.properties[$key] }
}

function Write-MonitorBootstrapRecord {
    param([IO.FileStream]$Stream, [hashtable]$Record)
    $bytes = [Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject $Record -Depth 40 -Compress) + "`n")
    $Stream.Write($bytes, 0, $bytes.Length)
    $Stream.Flush($true)
}

function Test-MonitorConnectedDiscoveryChain {
        param([hashtable]$Context)
        if ([string]::Equals($Context.probeProfile.version,'3.0-adopted',[StringComparison]::Ordinal)) {
            Test-MonitorAdoptedPublicationChain $Context
            return
        }
        $b = $Context.bootstrapReceipt
        $p = $Context.publicationReceipt
        $reconciled = [string]::Equals($b.version,'asre-monitor-bootstrap-reconciliation/v1',[StringComparison]::Ordinal)
        if ($reconciled) {
            Assert-MonitorReconciliationReceipt $Context $b $Context.qualifyingArtifact
        } else {
        if ([string]::Equals($b.attempt_id,(Get-MonitorReconciliationPins).attempt_id,[StringComparison]::Ordinal)) {
            throw 'The failed consumed attempt cannot acquire a fabricated legacy success receipt.'
        }
        $saved = Get-Content -LiteralPath (Join-Path (Get-MonitorBootstrapJournalDirectory) 'receipt.json') -Raw |
            ConvertFrom-Json -AsHashtable -DateKind String
        Assert-MonitorBootstrapBinding $saved $b
        Assert-MonitorBootstrapBinding @('version','status','phase','attempt_id','profile_sha256','agent_resource_id',
            'publication_manifest_sha256','source_commit','template_commit','original_snapshot_sha256',
            'connected_snapshot_sha256','connected_snapshot','activation_window','journal_sha256','runtime_accepted' | Sort-Object) @($b.Keys | Sort-Object)
        Assert-MonitorBootstrapBinding 'asre-monitor-bootstrap-receipt/v1' $b.version
        Assert-MonitorBootstrapBinding 'bootstrap-connected' $b.status
        Assert-MonitorBootstrapBinding $b.journal_sha256 (
            Get-FileHash -LiteralPath (Join-Path (Get-MonitorBootstrapJournalDirectory) 'attempt.jsonl') -Algorithm SHA256).Hash.ToLowerInvariant()
        }
        Assert-MonitorBootstrapBinding (Get-MonitorObjectSha256 $b) $Context.probeProfile.bootstrap_receipt_sha256
        Assert-MonitorBootstrapBinding (Get-MonitorObjectSha256 $p) $Context.probeProfile.publication_receipt_sha256
        Assert-MonitorBootstrapBinding @('version','agent_resource_id','phase','pre_snapshot_sha256',
            'paused_snapshot_sha256','agent_definition_sha256','runtime_accepted' | Sort-Object) @($p.Keys | Sort-Object)
        Assert-MonitorBootstrapBinding 'asre-monitor-publication-receipt/v1' $p.version
        foreach ($receipt in @($b,$p)) {
            Assert-MonitorBootstrapBinding 'SchemaDiscovery' $receipt.phase
            Assert-MonitorBootstrapBinding $Context.agent $receipt.agent_resource_id
            Assert-MonitorBootstrapBinding $false $receipt.runtime_accepted
        }
        Assert-MonitorBootstrapBinding $Context.publicationManifestSha256 $b.publication_manifest_sha256
        Assert-MonitorBootstrapBinding $b.connected_snapshot_sha256 (Get-MonitorObjectSha256 $b.connected_snapshot)
        Assert-MonitorBootstrapBinding $b.connected_snapshot_sha256 $p.pre_snapshot_sha256
        Assert-MonitorBootstrapBinding (Get-MonitorObjectSha256 $Context.snapshot) $p.paused_snapshot_sha256
        Assert-MonitorBootstrapBinding $Context.probeProfile.agent_definition_sha256 $p.agent_definition_sha256
        if ($b.connected_snapshot.filters.Count -ne 0) { throw 'Bootstrap chain did not start with zero plans.' }
        Assert-MonitorPlatform $b.connected_snapshot.platformConfiguration
        $expected = $b.connected_snapshot | ConvertTo-Json -Depth 40 | ConvertFrom-Json -AsHashtable -DateKind String
        $expected.filters = $Context.snapshot.filters
        $expected.agentPermissionsEtag = $Context.snapshot.agentPermissionsEtag
        Assert-MonitorBootstrapBinding $expected $Context.snapshot
        if ($Context.snapshot.filters.Count -ne 1) { throw 'Connected Discovery permits only the owned disabled target.' }
        Assert-MonitorQuiescent $Context.snapshot (Get-MonitorManifest -Root $Context.root).response_plan
    }
function Invoke-MonitorBootstrap {
    param([hashtable]$Context, [hashtable]$Profile, [hashtable]$Artifact)
    if (-not [string]::Equals((Get-MonitorManifest -Root $Context.root).phase, 'SchemaDiscovery', [StringComparison]::Ordinal)) {
        throw 'Bootstrap is SchemaDiscovery-only.'
    }
    $fields = @('version','phase','attempt_id','tenant_id','coordinator_object_id','coordinator_client_id',
        'agent_resource_id','source_commit','template_commit','publication_manifest_sha256','original_snapshot',
        'original_connectivity','archive_path','archive_sha256','writable_path','writable_sha256',
        'qualification_path','qualification_sha256','journal_directory','activation_window',
        'acknowledge_non_atomic_risk','max_delete','max_patch')
    Assert-MonitorBootstrapBinding @($fields | Sort-Object) @($Profile.Keys | Sort-Object)
    $authority = (Get-MonitorManifest -Root $Context.root).activation_authority
    foreach ($pair in @(@('version','asre-monitor-bootstrap/v1'),@('phase','SchemaDiscovery'),
        @('tenant_id',$authority.tenant_id),@('coordinator_object_id',$authority.object_id),
        @('coordinator_client_id',$authority.client_id),@('agent_resource_id',$Context.agent),
        @('source_commit',$Artifact.source_commit),@('template_commit',$Artifact.template_commit),
        @('publication_manifest_sha256',$Artifact.manifest_sha256),
        @('journal_directory',(Get-MonitorBootstrapJournalDirectory)),
        @('archive_sha256','000000000000000000000000000000000000000000000000000000000000006a'),
        @('writable_sha256','000000000000000000000000000000000000000000000000000000000000017a'),
        @('qualification_sha256','000000000000000000000000000000000000000000000000000000000000001a'))) {
        Assert-MonitorBootstrapBinding $pair[1] $Profile[$pair[0]]
    }
    if ($Profile.attempt_id -isnot [string] -or $Profile.attempt_id -cnotmatch '^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\z' -or
        $Profile.acknowledge_non_atomic_risk -isnot [bool] -or -not $Profile.acknowledge_non_atomic_risk) {
        throw 'Bootstrap requires a finite acknowledged attempt.'
    }
    foreach ($key in @('max_delete','max_patch')) {
        if ($Profile[$key] -isnot [int] -and $Profile[$key] -isnot [long]) { throw 'Bootstrap mutation limits must be typed integers.' }
    }
    Assert-MonitorBootstrapBinding 1 $Profile.max_delete
    Assert-MonitorBootstrapBinding 1 $Profile.max_patch
    foreach ($key in @('not_before','expires_at')) {
        if ($Profile.activation_window[$key] -isnot [string] -or
            $Profile.activation_window[$key] -cnotmatch '^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,7})?(?:Z|\+00:00)\z') {
            throw 'Bootstrap window requires exact UTC timestamp strings.'
        }
    }
    Assert-MonitorActivationWindow $Profile.activation_window -Discovery
    Assert-MonitorBootstrapBinding $Context.authorityWindow $Profile.activation_window
    $sealed = @{}
    foreach ($kind in @('archive','writable','qualification')) {
        $path = $Profile["${kind}_path"]
        if ($path -isnot [string] -or (Get-Item -LiteralPath $path).Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'Bootstrap archive path is unsupported.'
        }
        $bytes = [IO.File]::ReadAllBytes($path)
        $digest = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant()
        Assert-MonitorBootstrapBinding $Profile["${kind}_sha256"] $digest
        $sealed[$kind] = [Text.Encoding]::UTF8.GetString($bytes) | ConvertFrom-Json -AsHashtable -DateKind String
    }
    $archive = $sealed.archive
    $pilot = $archive.body | ConvertFrom-Json -AsHashtable -DateKind String
    if ($pilot.properties.isEnabled -isnot [bool] -or $pilot.properties.isEnabled -or
        -not [string]::Equals($pilot.name, 'asre-servicenow-review-pilot', [StringComparison]::Ordinal)) { throw 'Approved pilot is not the exact disabled owned plan.' }
    $directory = Get-MonitorBootstrapJournalDirectory
    $null = [IO.Directory]::CreateDirectory($directory)
    $part = Get-Item -LiteralPath $directory
    while ($null -ne $part) {
        if ($part.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Bootstrap journal cannot traverse a reparse point.' }
        $part = $part.Parent
    }
    $lock = [IO.File]::Open((Join-Path $directory 'resource.lock'), [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    $journal = $null
    try {
        # CreateNew rejects every prior attempt, even another id or a completed one.
        $journal = [IO.FileStream]::new((Join-Path $directory 'attempt.jsonl'), [IO.FileMode]::CreateNew,
            [IO.FileAccess]::Write, [IO.FileShare]::Read, 4096, [IO.FileOptions]::WriteThrough)
        $Context.bootstrapJournal = $journal
        $Context.bootstrapOriginalConnectivity = $Profile.original_connectivity
        Write-MonitorBootstrapRecord $journal @{ event = 'Attempt'; profile = $Profile; profile_sha256 = Get-MonitorObjectSha256 $Profile }
        $state = Get-MonitorNativeState $Context
        Assert-MonitorBootstrapBinding $Profile.original_snapshot $state
        Assert-MonitorBootstrapBinding @($pilot) $state.filters
        Assert-MonitorBootstrapSafe $Context $state
        $connectivity = (Invoke-MonitorNativeRequest $Context Sre Get '/api/v2/incidentManagement/checkConnectivityDetailed').data
        Assert-MonitorBootstrapBinding $Profile.original_connectivity $connectivity
        if ($connectivity.success -isnot [bool] -or -not $connectivity.success -or
            -not [string]::Equals($connectivity.incidentType, 'ServiceNow', [StringComparison]::Ordinal) -or
            [string]::IsNullOrWhiteSpace($connectivity.observedConfigurationRevision) -or
            [string]::IsNullOrWhiteSpace($state.platformConfiguration.apiConnectionName) -or
            -not [string]::Equals($connectivity.observedApiConnectionName, $state.platformConfiguration.apiConnectionName, [StringComparison]::Ordinal) -or
            -not [string]::Equals($state.platformConfiguration.oboUser,
                ('sreagent-config-revision:' + $connectivity.observedConfigurationRevision), [StringComparison]::Ordinal)) {
            throw 'Original ServiceNow connection is not independently ready.'
        }
        $direct = Invoke-MonitorNativeRequest $Context Sre Get '/api/v2/incidentManagement/incidentFilters/asre-servicenow-review-pilot'
        Assert-MonitorBootstrapBinding $pilot $direct.data
        # Re-read complete enumeration immediately before the one retirement.
        Assert-MonitorBootstrapBinding $Profile.original_snapshot (Get-MonitorNativeState $Context)
        Write-MonitorBootstrapRecord $journal @{ event = 'BeforeDelete'; attempt_id = $Profile.attempt_id }
        $Context.bootstrapWrite = 'Retire'
        $null = Invoke-MonitorNativeRequest $Context Sre Delete '/api/v2/incidentManagement/incidentFilters/asre-servicenow-review-pilot'
        $gone = Invoke-MonitorNativeRequest $Context Sre Get '/api/v2/incidentManagement/incidentFilters/asre-servicenow-review-pilot' -AllowNotFound
        if ($null -ne $gone) { throw 'Retired pilot is still present.' }
        $state = Get-MonitorNativeState $Context
        $retired = $Profile.original_snapshot | ConvertTo-Json -Depth 40 | ConvertFrom-Json -AsHashtable -DateKind String
        $retired.filters = @()
        Assert-MonitorBootstrapBinding $retired $state
        Assert-MonitorBootstrapSafe $Context $state
        Assert-MonitorBootstrapBinding $Profile.original_connectivity (
            Invoke-MonitorNativeRequest $Context Sre Get '/api/v2/incidentManagement/checkConnectivityDetailed').data
        # Final preimage/quiescence read after all other HTTP reads.
        Assert-MonitorBootstrapBinding $retired (Get-MonitorNativeState $Context)
        Write-MonitorBootstrapRecord $journal @{ event = 'BeforePatch'; attempt_id = $Profile.attempt_id }
        $Context.bootstrapWrite = 'Connect'
        $null = Invoke-MonitorNativeRequest $Context Arm Patch "$($Context.agent)?api-version=2025-05-01-preview" -Body @{
            properties = @{ incidentManagementConfiguration = @{ type = 'AzMonitor'; connectionName = 'azmonitor'; oboUser = '' } } }
        while ($true) {
            Assert-MonitorActivationWindow $Context.authorityWindow -Discovery
            $arm = (Invoke-MonitorNativeRequest $Context Arm Get "$($Context.agent)?api-version=2025-05-01-preview").data
            $state = Get-MonitorNativeState $Context
            $connected = $retired.Clone()
            $connected.platformConfiguration = @{ type = 'AzMonitor'; connectionName = 'azmonitor'; oboUser = '' }
            if ([string]::Equals((Get-MonitorObjectSha256 $state.platformConfiguration),
                (Get-MonitorObjectSha256 $retired.platformConfiguration), [StringComparison]::Ordinal)) {
                Assert-MonitorBootstrapBinding $retired $state
            } else {
                Assert-MonitorPlatform $state.platformConfiguration
                $connected.platformConfiguration = $state.platformConfiguration
                Assert-MonitorBootstrapBinding $connected $state
            }
            Assert-MonitorBootstrapSafe $Context $state
            $provisioned = [string]::Equals($arm.properties.provisioningState, 'Succeeded', [StringComparison]::Ordinal)
            if (-not $provisioned -and -not [string]::Equals($arm.properties.provisioningState, 'InProgress', [StringComparison]::Ordinal)) {
                throw [IO.InvalidDataException]::new('Bootstrap provisioning failed or changed schema.')
            }
            $readiness = Assert-MonitorConnected $Context $state -AllowPending
            if ($provisioned -and $readiness -ceq 'ready') {
                Assert-MonitorBootstrapBinding $connected $state
                break
            }
            $remaining = ([DateTimeOffset]::Parse($Context.authorityWindow.expires_at) - (Get-MonitorUtcNow)).TotalSeconds
            if ($remaining -le 1) { throw 'Bootstrap readiness deadline expired.' }
            Start-Sleep -Milliseconds ([int][Math]::Min(1000, [Math]::Floor($remaining * 1000)))
        }
        Write-MonitorBootstrapRecord $journal @{ event = 'Connected'; snapshot_sha256 = Get-MonitorObjectSha256 $state }
        $journal.Dispose(); $journal = $null
        $receipt = @{ version = 'asre-monitor-bootstrap-receipt/v1'; status = 'bootstrap-connected'; phase = 'SchemaDiscovery'
            attempt_id = $Profile.attempt_id; profile_sha256 = Get-MonitorObjectSha256 $Profile
            agent_resource_id = $Context.agent; publication_manifest_sha256 = $Artifact.manifest_sha256
            source_commit = $Artifact.source_commit; template_commit = $Artifact.template_commit
            original_snapshot_sha256 = Get-MonitorObjectSha256 $Profile.original_snapshot
            connected_snapshot_sha256 = Get-MonitorObjectSha256 $state
            connected_snapshot = $state; activation_window = $Context.authorityWindow
            journal_sha256 = (Get-FileHash -LiteralPath (Join-Path $directory 'attempt.jsonl') -Algorithm SHA256).Hash.ToLowerInvariant()
            runtime_accepted = $false }
        $file = [IO.FileStream]::new((Join-Path $directory 'receipt.json'), [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
        try { Write-MonitorBootstrapRecord $file $receipt } finally { $file.Dispose() }
        return $receipt
    } catch {
        if ($null -eq $journal) { throw 'Bootstrap resource attempt is already recorded or unavailable; no resume or new attempt is permitted.' }
        $failureEvidence = @{ category = 'SuppressedLocalFailure' }
        if ($_.Exception -is [IO.InvalidDataException]) { $failureEvidence.category = 'SchemaFailure' }
        $meta = $_.Exception.Data
        if ($meta['MonitorNativeCategory'] -cin @('HttpFailure','TransportFailure','ParseFailure') -and
            $meta['MonitorNativeMethod'] -cin @('Get','Delete','Patch')) {
            $failureEvidence = @{ category = $meta['MonitorNativeCategory']; method = $meta['MonitorNativeMethod'] }
            if ($meta['MonitorNativeStatus'] -is [int] -and $meta['MonitorNativeStatus'] -ge 100 -and $meta['MonitorNativeStatus'] -le 599) {
                $failureEvidence.http_status = $meta['MonitorNativeStatus']
            }
        }
        # Reconciliation never authorizes another mutation. Expired reads may
        # themselves be unavailable: report unknown, never infer completion.
        $status = 'unknown'
        try {
            $actual = Get-MonitorNativeState $Context
            if ($actual.filters.Count -eq 0) {
                $status = $(if ([string]::Equals($actual.platformConfiguration.type, 'AzMonitor', [StringComparison]::Ordinal)) {
                    'configured-but-unready' } else { 'retired-only' })
            }
        } catch { }
        Write-MonitorBootstrapRecord $journal @{ event = 'Stopped'; status = $status; failure = $failureEvidence }
        return @{ status = $status; runtime_accepted = $false; mutation_retry_permitted = $false }
    } finally {
        if ($null -ne $journal) { $journal.Dispose() }
        $Context.bootstrapJournal = $null
        $lock.Dispose()
    }
}

function Set-MonitorResource {
    param([scriptblock]$Transport, [hashtable]$Resource)
    $null = Invoke-MonitorTransport $Transport 'PublishResource' @{ resource = $Resource }
    $actual = Invoke-MonitorTransport $Transport 'ReadResource' @{ kind = $Resource.kind; name = $Resource.name }
    Assert-MonitorResource $Resource $actual
}

function Assert-MonitorPlan {
    param([hashtable]$State, [hashtable]$Plan)
    $owned = @($State.filters | Where-Object name -CEQ $Plan.name)
    if ($owned.Count -ne 1) { throw 'Native incident engine omitted the unique owned Monitor plan.' }
    Assert-MonitorResource $Plan $owned[0]
}

function Assert-MonitorQuiescent {
    param([hashtable]$State, [hashtable]$Plan)
    Assert-MonitorPlan $State $Plan
    if (@($State.filters | Where-Object { $_.properties.isEnabled }).Count) {
        throw 'Concurrent/overlapping native route became active; keep owned Monitor plan paused.'
    }
}

function Get-MonitorNativeCaptureConfiguration {
    param([string]$Root = (Split-Path $PSScriptRoot -Parent), [hashtable]$Window)
    Assert-MonitorActivationWindow $Window -Discovery
    $m = Get-MonitorManifest -Root $Root
    if ($m.phase -cne 'SchemaDiscovery') { throw 'Native capture is SchemaDiscovery-only.' }
    Assert-MonitorExact @{version='asre-native-capture/v1';rule_api_version='2023-12-01';maximum_window_seconds=600} $m.native_capture 'capture-declaration'
    $agent = @(Get-MonitorReviewConfiguration -Root $Root)[0] | ConvertTo-Json -Depth 40 | ConvertFrom-Json -AsHashtable -DateKind String
    $agent.properties.instructions = $agent.properties.instructions.Replace(
        'Only the one source-pinned historical alert is eligible.',
        'Candidate routing is NOT eligibility or qualification. Only authoritative records may establish exact rule, VM, window and native linkage.')
    $agent.properties.instructions += "`nASRE_NATIVE_CAPTURE_SCOPE=$($m.scope | ConvertTo-Json -Compress)`nCandidate intake is continuous, not fault or qualification authority. Unknown/manual/foreign/historical/unlinked DTOs remain UNKNOWN. Never authorize another fault."
    $plan = $m.response_plan | ConvertTo-Json -Depth 20 | ConvertFrom-Json -AsHashtable -DateKind String
    $plan.properties.alertId = '' # PATCH must clear the old historical selector.
    $plan.properties.azMonitorFilterSettings = @{
        targetResourceType='Microsoft.OperationalInsights/workspaces';targetResource=$m.scope.workspace_resource_id
    }
    return @{version='asre-native-capture/v1';agent=$agent;plan=$plan;window=$Window.Clone();scope=$m.scope}
}

function Assert-MonitorNativeCaptureRule {
    param([hashtable]$Rule, [hashtable]$Scope)
    if ($Rule.id -ine $Scope.rule_id -or $Rule.properties.enabled -isnot [bool] -or -not $Rule.properties.enabled) { throw 'Capture requires the exact enabled rule.' }
    $p = $Rule.properties
    Assert-MonitorExact @($Scope.workspace_resource_id) @($p.scopes) 'capture-rule-workspace'
    foreach ($pair in @(@('evaluationFrequency','PT1M'),@('windowSize','PT10M'),@('severity',1),@('autoMitigate',$true))) {
        Assert-MonitorExact $pair[1] $p[$pair[0]] "capture-rule.$($pair[0])"
    }
    if ($p.criteria.allOf -isnot [array] -or $p.criteria.allOf.Count -ne 1) { throw 'Capture requires one exact dedicated-volume criterion.' }
    $criterion = $p.criteria.allOf[0]
    foreach ($key in $criterion.Keys) {
        if ($key -cin @('query','timeAggregation','operator','threshold','failingPeriods')) { continue }
        if ($key -cin @('metricMeasureColumn','resourceIdColumn','metricName','metricNamespace')) {
            if ($null -ne $criterion[$key] -and $criterion[$key] -cne '') { throw 'Unexpected capture criterion selector.' }
        } elseif ($key -ceq 'dimensions') { Assert-MonitorExact @() $criterion[$key] 'capture-no-extra-dimensions' }
        else { throw 'Unsupported capture criterion field.' }
    }
    $query = @"
Perf
| where _ResourceId =~ "$($Scope.resource_id)"
| where ObjectName == "Logical Disk" and CounterName == "% Used Space"
| where InstanceName == "/srv/asre-demo"
| summarize Value = max(CounterValue) by _ResourceId
| where Value >= 80
"@
    # Only ARM resource-ID case is irrelevant to the =~ operand.
    $actual = $criterion.query.Replace($Scope.resource_id,$Scope.resource_id.ToLowerInvariant(),[StringComparison]::OrdinalIgnoreCase)
    $expected = $query.Replace($Scope.resource_id,$Scope.resource_id.ToLowerInvariant(),[StringComparison]::OrdinalIgnoreCase)
    if (($actual -replace '\s+',' ').Trim() -cne ($expected -replace '\s+',' ').Trim()) { throw 'Capture query must bind the exact VM/dedicated volume/80 percent threshold.' }
    foreach ($pair in @(@('timeAggregation','Count'),@('operator','GreaterThan'))) {
        Assert-MonitorExact $pair[1] $criterion[$pair[0]] "capture-criterion.$($pair[0])"
    }
    if (($criterion.threshold -isnot [int] -and $criterion.threshold -isnot [long] -and
        $criterion.threshold -isnot [double]) -or $criterion.threshold -ne 0) {
        throw 'Capture criterion threshold must be numeric zero.'
    }
    Assert-MonitorExact @{numberOfEvaluationPeriods=1;minFailingPeriodsToAlert=1} $criterion.failingPeriods 'capture-failing-periods'
}

function Test-MonitorNativeCaptureReadiness {
    param([hashtable]$Context, [hashtable]$Window, [bool]$Enabled=$true)
    $capture = Get-MonitorNativeCaptureConfiguration -Root $Context.root -Window $Window
    $state = Get-MonitorNativeState $Context
    Assert-MonitorConnected $Context $state
    Assert-MonitorExact @{allow=@();ask=@();deny=@()} $state.permissions 'capture-global-policy'
    Assert-MonitorExact @{allow=@()} $state.agentPermissions 'capture-agent-policy'
    if ([string]::IsNullOrWhiteSpace($state.permissionsEtag) -or [string]::IsNullOrWhiteSpace($state.agentPermissionsEtag)) { throw 'Capture requires actual policy revisions.' }
    $revisions=@{global=$state.permissionsEtag;agent=$state.agentPermissionsEtag}
    if($Context.ContainsKey('capturePolicyRevisions')){Assert-MonitorExact $Context.capturePolicyRevisions $revisions 'capture-unchanged-policy-revisions'}
    else{$Context.capturePolicyRevisions=$revisions}
    $capture.plan.properties.isEnabled = $Enabled
    Assert-MonitorPlan $state $capture.plan
    if (@($state.filters | Where-Object { $_.name -cne $capture.plan.name -and $_.properties.isEnabled }).Count) { throw 'Capture rejects overlapping intake.' }
    $agent = Invoke-MonitorNativeRequest $Context Sre Get '/api/v2/extendedAgent/agents/asre-azure-monitor-review'
    Assert-MonitorResource $capture.agent $agent.data
    # The rule API exposes no revision; capture only reads its exact definition.
    $rule = Invoke-MonitorNativeRequest $Context Arm Get "$($capture.scope.rule_id)?api-version=2023-12-01"
    Assert-MonitorNativeCaptureRule $rule.data $capture.scope
    Assert-MonitorActivationWindow $Window -Discovery
    return @{version='asre-native-capture-readiness/v1';status='verified';rule_id=$capture.scope.rule_id
        workspace_id=$capture.scope.workspace_resource_id;target_id=$capture.scope.resource_id
        window=$Window;handler_sha256=(Get-MonitorObjectSha256 $capture.agent);filter_sha256=(Get-MonitorObjectSha256 $capture.plan)}
}

function Get-MonitorCaptureReleaseBinding {
    return @{collection_uri='https://dev.azure.com/example-org/';project_id='00000016-000a-4000-8000-000000000016'
        definition_id='8';repository_id='00000039-000a-4000-a000-000000000039'
        template_repository_id='00000037-000a-4000-b000-000000000037';template_ref='refs/heads/main'
        template_commit='000000000000000000000000000000000000006a';definition_revision=13}
}

function Assert-MonitorCaptureArtifact {
    param([hashtable]$Artifact)
    $b=Get-MonitorCaptureReleaseBinding
    foreach($key in @('collection_uri','project_id','definition_id','repository_id')) {
        Assert-MonitorBootstrapBinding $b[$key] $Artifact.release_context[$key]
    }
    Assert-MonitorBootstrapBinding $b.template_commit $Artifact.template_commit
    Assert-MonitorBootstrapBinding 'alz-landingzone/alz-mgmt-templates' $Artifact.release_context.template_repository
    Assert-MonitorBootstrapBinding 'refs/heads/master' $Artifact.release_context.source_branch
    $created=[DateTimeOffset]::Parse($Artifact.release_context.created_at)
    $now=Get-MonitorUtcNow
    if($created -gt $now -or $now-$created -gt [TimeSpan]::FromHours(4)){throw 'Capture artifact is not fresh.'}
}

function New-MonitorCapturePublicationReceipt {
    param([hashtable]$Artifact,[hashtable]$Capture,[string]$RunId)
    return @{version='asre-native-capture-publication/v1';phase='SchemaDiscovery';runtime_accepted=$false
        publication_run_id=$RunId;validation_run_id=$Artifact.release_context.validation_run_id
        source_commit=$Artifact.source_commit;template_commit=$Artifact.template_commit
        publication_manifest_sha256=$Artifact.manifest_sha256
        release_context_sha256=(Get-MonitorObjectSha256 $Artifact.release_context)
        agent_definition_sha256=(Get-MonitorObjectSha256 $Capture.agent)
        filter_definition_sha256=(Get-MonitorObjectSha256 $Capture.plan)}
}

function Get-MonitorCaptureAdoHeaders {
    param([string]$Root,[hashtable]$Window)
    Assert-MonitorActivationWindow $Window -Discovery
    $a=(Get-MonitorManifest -Root $Root).activation_authority
    try {
        $token=& az account get-access-token --subscription 00000001-000a-4000-a000-000000000001 --resource 499b84ac-1321-427f-aa17-267ca6975798 --query accessToken --output tsv --only-show-errors
        if($LASTEXITCODE -ne 0 -or $token -isnot [string]){throw 'token'}
        $part=$token.Split('.')[1].Replace('-','+').Replace('_','/')
        $part+='='*((4-$part.Length%4)%4)
        $c=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($part))|ConvertFrom-Json -AsHashtable
        if($c.tid -cne $a.tenant_id -or $c.oid -cne $a.object_id -or
            $c.aud -cne '499b84ac-1321-427f-aa17-267ca6975798' -or
            $c.exp -isnot [long] -and $c.exp -isnot [int]){throw 'identity'}
        $clients=@('appid','azp'|Where-Object {$c.ContainsKey($_)})
        if(-not $clients.Count -or @($clients|Where-Object {$c[$_] -cne $a.client_id}).Count -or
            $c.exp -le [DateTimeOffset]::Parse($Window.expires_at).ToUnixTimeSeconds() -or
            ($c.ContainsKey('nbf') -and ($c.nbf -isnot [long] -and $c.nbf -isnot [int] -or
                $c.nbf -gt (Get-MonitorUtcNow).ToUnixTimeSeconds()))){throw 'time/client'}
        return @{Authorization="Bearer $token"}
    } catch {throw 'Fixed ADO coordinator authentication failed; details suppressed.'}
}

function Invoke-MonitorCaptureAdoRead {
    param([string]$Path,[hashtable]$Headers,[hashtable]$Window,[switch]$Text)
    if($Path -cnotmatch '^/(?:build/definitions/8|build/builds/[1-9][0-9]*(?:/timeline|/logs/[1-9][0-9]*)?|pipelines/8/runs/[1-9][0-9]*)\?api-version=7\.1$'){
        throw 'ADO read escaped fixed source endpoints.'
    }
    Assert-MonitorActivationWindow $Window -Discovery
    $seconds=([DateTimeOffset]::Parse($Window.expires_at)-(Get-MonitorUtcNow)).TotalSeconds
    if($seconds -lt 1){throw 'ADO control window expired.'}
    $uri='https://dev.azure.com/example-org/00000016-000a-4000-8000-000000000016/_apis'+$Path
    $r=Invoke-WebRequest -Uri $uri -Method Get -Headers $Headers -MaximumRedirection 0 `
        -TimeoutSec ([int][Math]::Min(30,[Math]::Floor($seconds))) -SkipHttpErrorCheck -ErrorAction Stop
    if($r.StatusCode -ne 200 -or $r.Headers['x-ms-continuationtoken'] -or
        $r.Content -isnot [string] -or [Text.Encoding]::UTF8.GetByteCount($r.Content) -gt 2097152){throw 'ADO response incomplete or outside bounded read.'}
    Assert-MonitorActivationWindow $Window -Discovery
    if($Text){return $r.Content}
    return ($r.Content|ConvertFrom-Json -AsHashtable -DateKind String)
}

function Get-MonitorCapturePublicationProof {
    param([hashtable]$Artifact,[hashtable]$Capture,[int]$PublicationRunId,[string]$Root,[hashtable]$Window)
    if($PublicationRunId -lt 1){throw 'Capture needs a positive PublicationRunId.'}
    Assert-MonitorCaptureArtifact $Artifact
    $b=Get-MonitorCaptureReleaseBinding
    $head=& git -C $Root rev-parse HEAD
    if($LASTEXITCODE -ne 0 -or $head -cne $Artifact.source_commit){throw 'Reviewed immutable Root commit differs from artifact.'}
    $h=Get-MonitorCaptureAdoHeaders $Root $Window
    $definition=Invoke-MonitorCaptureAdoRead '/build/definitions/8?api-version=7.1' $h $Window
    if($definition.id -ne 8 -or $definition.revision -ne 13 -or $definition.queueStatus -cne 'enabled' -or
        $definition.project.id -cne $b.project_id -or $definition.repository.id -cne $b.repository_id){throw 'Fixed protected definition changed.'}
    $validation=$Artifact.release_context.validation_run_id
    if($validation -cnotmatch '^[1-9][0-9]*$' -or "$PublicationRunId" -ceq $validation){throw 'Validation/publication runs must be distinct.'}
    $builds=@{}
    foreach($id in @($validation,"$PublicationRunId")){
        $build=Invoke-MonitorCaptureAdoRead "/build/builds/$id`?api-version=7.1" $h $Window
        $run=Invoke-MonitorCaptureAdoRead "/pipelines/8/runs/$id`?api-version=7.1" $h $Window
        if("$($build.id)" -cne $id -or "$($run.id)" -cne $id -or $build.status -cne 'completed' -or
            $build.result -cne 'succeeded' -or $build.reason -cne 'manual' -or $build.sourceBranch -cne 'refs/heads/master' -or
            $build.project.id -cne $b.project_id -or $build.definition.id -ne 8 -or
            $build.repository.id -cne $b.repository_id -or $build.repository.type -cne 'TfsGit' -or
            $run.pipeline.id -ne 8 -or $run.pipeline.revision -ne 13 -or $run.state -cne 'completed' -or $run.result -cne 'succeeded'){
            throw 'Actual ADO run/build identity or completion differs.'
        }
        $self=$run.resources.repositories.self;$template=$run.resources.repositories.templates
        if($self.repository.id -cne $b.repository_id -or $self.repository.type -cne 'azureReposGit' -or
            $self.refName -cne 'refs/heads/master' -or $self.version -cne $Artifact.source_commit -or
            $template.repository.id -cne $b.template_repository_id -or $template.repository.type -cne 'azureReposGit' -or
            $template.refName -cne $b.template_ref -or $template.version -cne $b.template_commit -or
            ($build.ContainsKey('sourceVersion') -and $build.sourceVersion -and $build.sourceVersion -cne $Artifact.source_commit)){
            throw 'Actual resolved source/template resources differ.'
        }
        $builds[$id]=$build
    }
    $v=$builds[$validation];$p=$builds["$PublicationRunId"];$now=Get-MonitorUtcNow
    $created=[DateTimeOffset]::Parse($Artifact.release_context.created_at)
    if([DateTimeOffset]::Parse($v.startTime) -gt $created -or [DateTimeOffset]::Parse($v.finishTime) -lt $created -or
        [DateTimeOffset]::Parse($p.startTime) -lt [DateTimeOffset]::Parse($v.finishTime) -or
        [DateTimeOffset]::Parse($p.finishTime) -gt $now -or $now-[DateTimeOffset]::Parse($p.finishTime) -gt [TimeSpan]::FromHours(4)){
        throw 'Publication/validation chronology is stale or inconsistent.'
    }
    $timeline=Invoke-MonitorCaptureAdoRead "/build/builds/$PublicationRunId/timeline?api-version=7.1" $h $Window
    if($timeline.records -isnot [array] -or -not $timeline.records.Count){throw 'ADO timeline incomplete.'}
    $stage=@($timeline.records|Where-Object {$_.type -ceq 'Stage' -and $_.name -ceq 'Protected disabled publication and strict readback only'})
    if($stage.Count -ne 1 -or $stage[0].parentId){throw 'Exact publication stage missing.'}
    if($stage[0].ContainsKey('identifier') -and $stage[0].identifier -and
        $stage[0].identifier -cne 'ConfigurationPublishDisabled'){throw 'Publication stage identifier differs.'}
    $phase=@($timeline.records|Where-Object {$_.type -ceq 'Phase' -and $_.name -ceq 'ConfigurationPublishDisabled' -and $_.parentId -ceq $stage[0].id})
    if($phase.Count -gt 1){throw 'Exact publication phase is ambiguous.'}
    $jobParent=$(if($phase.Count){$phase[0].id}else{$stage[0].id})
    $job=@($timeline.records|Where-Object {$_.type -ceq 'Job' -and $_.name -ceq 'ConfigurationPublishDisabled' -and $_.parentId -ceq $jobParent})
    if($job.Count -ne 1){throw 'Exact publication job missing.'}
    $required=@($stage[0],$job[0])+@($phase);$publisher=$null
    foreach($name in @('Guard successful exact-source configuration-only provenance before cloud',
        'Verify all artifact bytes and context before cloud login',
        'Delegate only disabled publication and strict readback to the frozen publisher')){
        $step=@($timeline.records|Where-Object {$_.type -ceq 'Task' -and $_.name -ceq $name -and $_.parentId -ceq $job[0].id})
        if($step.Count -ne 1){throw 'Exact protected publisher step missing.'}
        $required+=@($step[0]);$publisher=$step[0]
    }
    if(@($required|Where-Object {$_.state -cne 'completed' -or $_.result -cne 'succeeded'}).Count -or
        @($timeline.records|Where-Object {$_.type -ceq 'Task' -and $_.name -match '(?i)terraform' -and $_.result -cne 'skipped'}).Count){
        throw 'Publication was skipped/failed or Terraform executed.'
    }
    if($publisher.log.id -isnot [int] -and $publisher.log.id -isnot [long] -or $publisher.log.id -lt 1){throw 'Publisher task log missing.'}
    $text=Invoke-MonitorCaptureAdoRead "/build/builds/$PublicationRunId/logs/$($publisher.log.id)?api-version=7.1" $h $Window -Text
    $receipts=@();$contexts=@()
    foreach($line in ($text -split "`n")){
        if($line -cmatch '(?:^|\s)ASRE_NATIVE_CAPTURE_PUBLICATION_V1=(\{.*\})\s*$'){$receipts+=@($Matches[1]|ConvertFrom-Json -AsHashtable -DateKind String)}
        if($line -cmatch '(?:^|\s)ASRE_NATIVE_CAPTURE_CONTEXT_V1=(\{.*\})\s*$'){$contexts+=@($Matches[1]|ConvertFrom-Json -AsHashtable -DateKind String)}
    }
    if($receipts.Count -ne 1 -or $contexts.Count -ne 1){throw 'Exact source-bound capture receipt/context missing or duplicated.'}
    $expected=New-MonitorCapturePublicationReceipt $Artifact $Capture "$PublicationRunId"
    Assert-MonitorBootstrapBinding $expected $receipts[0]
    Assert-MonitorBootstrapBinding @{version='asre-native-capture-context/v1';receipt_sha256=(Get-MonitorObjectSha256 $expected);release_context=$Artifact.release_context} $contexts[0]
    return $expected
}

function Invoke-MonitorNativeCapture {
    [CmdletBinding()]
    param([ValidateSet('Preflight','PublishDisabled','Enable')][string]$Action='Preflight',
        [string]$Root=(Split-Path $PSScriptRoot -Parent),[hashtable]$CaptureWindow,[string]$PublicationArtifactDirectory,
        [int]$PublicationRunId)
    # Capture immutable original window before auth. This surface never calls
    # legacy Enable/Pause/Rollback/Bootstrap or changes platform/global policy.
    if($null -eq $CaptureWindow){
        $now=Get-MonitorUtcNow
        $CaptureWindow=@{not_before=$now.ToString('o');expires_at=$now.AddMinutes(9).ToString('o')}
    }
    $window = $CaptureWindow.Clone()
    $capture = Get-MonitorNativeCaptureConfiguration -Root $Root -Window $window
    $context = Get-MonitorNativeContext -Root $Root -Action $Action -AuthorityWindow $window
    $context.nativeCapture = $true
    if ($Action -ceq 'Preflight' -and -not $PublicationArtifactDirectory) { return Test-MonitorNativeCaptureReadiness $context $window }
    $artifact = Read-MonitorPublicationArtifact -Directory $PublicationArtifactDirectory -Root $Root
    Assert-MonitorCaptureArtifact $artifact
    if ($Action -ceq 'PublishDisabled') {
        $run=Get-MonitorProtectedPublicationRun $artifact
        $state = Get-MonitorNativeState $context
        Assert-MonitorConnected $context $state
        if (@($state.filters | Where-Object { $_.properties.isEnabled }).Count) { throw 'Capture publication cannot pause live intake: use separately approved atomic staging.' }
        Assert-MonitorExact @{allow=@();ask=@();deny=@()} $state.permissions 'capture-preserved-global-policy'
        Assert-MonitorExact @{allow=@()} $state.agentPermissions 'capture-preserved-agent-policy'
        if([string]::IsNullOrWhiteSpace($state.permissionsEtag) -or [string]::IsNullOrWhiteSpace($state.agentPermissionsEtag)){throw 'Capture publication needs actual policy revisions.'}
        foreach ($resource in @($capture.agent,$capture.plan)) {
            $path = $(if($resource.kind -ceq 'agents'){'/api/v2/extendedAgent/agents/asre-azure-monitor-review'}else{'/api/v2/incidentManagement/incidentFilters/asre-azure-monitor-review'})
            $current = Invoke-MonitorNativeRequest $context Sre Get $path
            if($resource.kind -ceq 'incidentFilters' -and
                ($current.data.properties.isEnabled -isnot [bool] -or $current.data.properties.isEnabled)){
                throw 'Capture publication refuses to disable newly active intake.'
            }
            if($resource.kind -ceq 'agents'){
                $desired=$false
                try {Assert-MonitorResource $resource $current.data;$desired=$true}
                catch {Assert-MonitorResource @(Get-MonitorReviewConfiguration -Root $Root)[0] $current.data}
                if($desired){continue}
                $handlerBody=@{name=$resource.name;type=$resource.type;properties=$resource.properties;tags=@()}
                try{
                    $null=Invoke-MonitorNativeRequest $context Sre Put $path -Body $handlerBody
                    Assert-MonitorResource $resource (Invoke-MonitorNativeRequest $context Sre Get $path).data
                }catch{throw 'Capture handler publication UNKNOWN; parent reconciliation required; no retry or compensation.'}
                continue
            }
            $body = @{name=$resource.name;type=$resource.type;properties=$resource.properties}
            $method='Patch'
            if($resource.kind -ceq 'agents'){$method='Put';$body.tags=@()}
            $null = Invoke-MonitorNativeRequest $context Sre $method $path -Body $body -IfMatch $current.etag
            Assert-MonitorResource $resource (Invoke-MonitorNativeRequest $context Sre Get $path).data
        }
        $after=Get-MonitorNativeState $context
        Assert-MonitorQuiescent $after $capture.plan
        foreach($key in @('permissions','agentPermissions','permissionsEtag','agentPermissionsEtag')){
            Assert-MonitorBootstrapBinding $state[$key] $after[$key]
        }
        $receipt=New-MonitorCapturePublicationReceipt $artifact $capture $run.publication_run_id
        $receiptContext=@{version='asre-native-capture-context/v1';receipt_sha256=(Get-MonitorObjectSha256 $receipt);release_context=$artifact.release_context}
        return @{status='published-disabled';phase='SchemaDiscovery';runtime_accepted=$false;
            capture_publication_receipt=$receipt;capture_publication_context=$receiptContext}
    }
    $null=Get-MonitorCapturePublicationProof $artifact $capture $PublicationRunId $Root $window
    $null=Test-MonitorNativeCaptureReadiness $context $window -Enabled $false
    if($Action -ceq 'Preflight'){return @{status='candidate-intake-preflight';runtime_accepted=$false}}
    # No disabling compensation: after a lost response retain UNKNOWN and read.
    try {
        $current=Invoke-MonitorNativeRequest $context Sre Get '/api/v2/incidentManagement/incidentFilters/asre-azure-monitor-review'
        Assert-MonitorResource $capture.plan $current.data
        $capture.plan.properties.isEnabled=$true
        $body=@{name=$capture.plan.name;type=$capture.plan.type;properties=$capture.plan.properties}
        $null=Invoke-MonitorNativeRequest $context Sre Patch '/api/v2/incidentManagement/incidentFilters/asre-azure-monitor-review' -Body $body -IfMatch $current.etag
        $null=Test-MonitorNativeCaptureReadiness $context $window
        return @{status='capture-enabled';runtime_accepted=$false}
    } catch {
        try {$null=Test-MonitorNativeCaptureReadiness $context $window}catch{}
        return @{status='UNKNOWN';stop_further_injection=$true;runtime_accepted=$false;reconcile_required=$true}
    }
}

function Invoke-IncidentWorkflow {
    [CmdletBinding()]
    param(
        [ValidateSet('Preflight', 'PublishDisabled', 'BootstrapPlatform', 'ReconcileBootstrap', 'Cutover', 'Enable', 'Pause', 'Rollback', IgnoreCase = $false)][string]$Action = 'Preflight',
        [string]$Root = (Split-Path $PSScriptRoot -Parent),
        [scriptblock]$Transport, [hashtable]$Binding, [hashtable]$Snapshot,
        [hashtable]$Commissioning, [hashtable]$ActivationWindow, [hashtable]$ProbeProfile,
        [hashtable]$BootstrapProfile, [hashtable]$BootstrapReceipt, [hashtable]$PublicationReceipt, [hashtable]$PublicationContext,
        [string]$PublicationArtifactDirectory, [string]$ProtectedCaseSource
    )
    $manifest = Get-MonitorManifest -Root $Root
    $plan = $manifest.response_plan
    $nativeContext = $null
    if ($ProbeProfile -and $ProbeProfile.ContainsKey('version') -and
        (Test-MonitorConnectedProfile $ProbeProfile) -and
        -not [string]::Equals($manifest.phase, 'SchemaDiscovery', [StringComparison]::Ordinal)) {
        throw 'Connected-target receipts are SchemaDiscovery-only.'
    }
    if ($Action -cin @('BootstrapPlatform','ReconcileBootstrap') -and $Transport) { throw 'Bootstrap has only the fixed native adapter.' }
    if (-not $Transport) {
        # Copy scalar UTC values BEFORE authentication/artifact/internal reads.
        # Neither caller window mutation nor a later action may renew authority.
        $originalWindow = $(if ($ActivationWindow) { $ActivationWindow.Clone() } else { $null })
        $capturedBootstrap = $(if ($BootstrapProfile) { $BootstrapProfile | ConvertTo-Json -Depth 40 | ConvertFrom-Json -AsHashtable -DateKind String } else { $null })
        $capturedProbe = $(if ($ProbeProfile) { $ProbeProfile | ConvertTo-Json -Depth 40 | ConvertFrom-Json -AsHashtable -DateKind String } else { $null })
        $capturedBootstrapReceipt = $(if ($BootstrapReceipt) { $BootstrapReceipt | ConvertTo-Json -Depth 40 | ConvertFrom-Json -AsHashtable -DateKind String } else { $null })
        $capturedPublicationReceipt = $(if ($PublicationReceipt) { $PublicationReceipt | ConvertTo-Json -Depth 40 | ConvertFrom-Json -AsHashtable -DateKind String } else { $null })
        $capturedPublicationContext = $(if ($PublicationContext) { $PublicationContext | ConvertTo-Json -Depth 40 | ConvertFrom-Json -AsHashtable -DateKind String } else { $null })
        if ($Action -ceq 'BootstrapPlatform') {
            if (-not $capturedBootstrap) { throw 'Bootstrap requires the explicit versioned sealed profile.' }
            $originalWindow = $capturedBootstrap.activation_window.Clone()
            Assert-MonitorActivationWindow $originalWindow -Discovery
        }
        if ($Action -ceq 'ReconcileBootstrap') {
            Assert-MonitorActivationWindow $originalWindow -Discovery
            $null = Read-MonitorReconciliationOrigin
        }
        $nativeContext = Get-MonitorNativeContext -Action $Action -Root $Root -AuthorityWindow $originalWindow
        $nativeContext.authorityWindow = $originalWindow
        if ($Action -in @('BootstrapPlatform','ReconcileBootstrap','Cutover','Enable') -or
            ($Action -ceq 'PublishDisabled' -and ($PublicationArtifactDirectory -or
                (Test-Path -LiteralPath (Join-Path (Get-MonitorBootstrapJournalDirectory) 'bootstrap-reconciliation.json'))))) {
            $artifact = Read-MonitorPublicationArtifact -Directory $PublicationArtifactDirectory -Root $Root
            $ProtectedCaseSource = $artifact.source
            $Commissioning = $artifact.configuration
            $nativeContext.publicationManifestSha256 = $artifact.manifest_sha256
            $nativeContext.qualifyingArtifact = $artifact
            $nativeContext.probeProfile = $capturedProbe
            $nativeContext.bootstrapReceipt = $capturedBootstrapReceipt
            $nativeContext.publicationReceipt = $capturedPublicationReceipt
            $nativeContext.publicationContext = $capturedPublicationContext
            $nativeContext.snapshot = $Snapshot
            $nativeContext.activationWindow = $originalWindow
        }
        if ($Action -ceq 'BootstrapPlatform') {
            $nativeContext.bootstrapWrite = ''
            $nativeContext.bootstrapSubmitted = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
            return Invoke-MonitorBootstrap $nativeContext $capturedBootstrap $artifact
        }
        if ($Action -ceq 'ReconcileBootstrap') {
            return Invoke-MonitorBootstrapReconciliation $nativeContext $artifact
        }
        $Transport = {
            param($Operation, $Request)
            Invoke-MonitorNativeOperation -Context $nativeContext -Operation $Operation -Request $Request
        }
        if ($Action -in @('Cutover','Enable')) {
            Assert-MonitorActivationWindow $ActivationWindow -Discovery:($manifest.phase -ceq 'SchemaDiscovery')
            if (-not $ProtectedCaseSource) { throw 'Activation must consume the protected published bundle, never regenerate it.' }
        }
    }
    $state = Get-MonitorState $Transport
    $owned = @($state.filters | Where-Object name -CEQ $plan.name)
    $othersActive = @($state.filters | Where-Object { $_.name -cne $plan.name -and $_.properties.isEnabled })
    if ($Action -eq 'Preflight') {
        return @{ status = 'preflight'; snapshot = $state; active_other_plans = @($othersActive | ForEach-Object { $_.name }); runtime_accepted = $false }
    }
    if ($Action -eq 'PublishDisabled') {
        $adopted = $false
        if ($null -ne $nativeContext) {
            # Protected Apply publication is still separate. It must never
            # create a Monitor filter while ServiceNow is selected.
            Assert-MonitorConnected $nativeContext $state
            $reconciliationPath = Join-Path (Get-MonitorBootstrapJournalDirectory) 'bootstrap-reconciliation.json'
            $reconciliation = $null
            if (Test-Path -LiteralPath $reconciliationPath) {
                $reconciliation = Get-Content -LiteralPath $reconciliationPath -Raw | ConvertFrom-Json -AsHashtable -DateKind String
            }
            $historical = Get-MonitorPublicationOrigin
            if ($PublicationArtifactDirectory -and [string]::Equals($manifest.phase,'SchemaDiscovery',[StringComparison]::Ordinal) -and
                ($null -eq $reconciliation -or [string]::Equals((Get-MonitorObjectSha256 $reconciliation),
                    (Get-MonitorObjectSha256 $historical),[StringComparison]::Ordinal))) {
                $adopted = $true
                if ([string]::Equals($artifact.manifest_sha256,$historical.publication_manifest_sha256,[StringComparison]::Ordinal)) {
                    throw 'Historical artifact is not current publication authority.'
                }
                $publicationRun = Get-MonitorProtectedPublicationRun $artifact
                Assert-MonitorSchemaDiscoveryAdoptedState $historical.connected_snapshot $state $Root
                Assert-MonitorRetainedIdentity $nativeContext
                Assert-MonitorBootstrapSafe $nativeContext $state
            } elseif ($null -ne $reconciliation) {
                Assert-MonitorReconciliationReceipt $nativeContext $reconciliation $artifact
                Assert-MonitorBootstrapBinding $reconciliation.connected_snapshot $state
            }
            foreach ($filter in $state.filters) {
                if (-not [string]::Equals($filter.name, $plan.name, [StringComparison]::Ordinal)) {
                    throw 'Publication requires zero plans or only its exact disabled target.'
                }
                Assert-MonitorResource $plan $filter
            }
        }
        if ($owned.Count -and $owned[0].properties.isEnabled) { throw 'Pause the existing Monitor plan explicitly before publication.' }
        Assert-MonitorExact @{ allow = @(); ask = @(); deny = @() } $state.permissions 'preserved-empty-global-policy'
        if ([string]::IsNullOrWhiteSpace($state.permissionsEtag)) { throw 'Global policy readback omitted its actual ETag.' }
        foreach ($item in @(Get-MonitorReviewConfiguration -Root $Root)) { Set-MonitorResource $Transport $item }
        Set-MonitorResource $Transport $plan
        $null = Invoke-MonitorTransport $Transport 'SetAgentPermissions' @{ permissions = (Get-MonitorPermissions -Binding $Binding -Root $Root) }
        $current = Get-MonitorState $Transport
        Assert-MonitorPlan $current $plan
        Assert-MonitorExact $state.permissions $current.permissions 'unchanged-global-policy'
        Assert-MonitorExact $state.permissionsEtag $current.permissionsEtag 'unchanged-global-policy-etag'
        Assert-MonitorExact (Get-MonitorPermissions -Binding $Binding -Root $Root) $current.agentPermissions 'agent-scoped-allow'
        $result = @{ status = 'published-disabled'; phase = $manifest.phase; runtime_accepted = $false
            publication_receipt = @{ version = 'asre-monitor-publication-receipt/v1'; agent_resource_id = $state.agentResourceId
                phase = $manifest.phase; pre_snapshot_sha256 = Get-MonitorObjectSha256 $state
                paused_snapshot_sha256 = Get-MonitorObjectSha256 $current
                agent_definition_sha256 = Get-MonitorObjectSha256 @(Get-MonitorReviewConfiguration -Root $Root)[0]
                runtime_accepted = $false } }
        if ($adopted) {
            Assert-MonitorAdoptedState $state $current $Root
            $result.publication_context = @{
                version = 'asre-monitor-publication-context/v1'; phase = 'SchemaDiscovery'; agent_resource_id = $state.agentResourceId
                origin_receipt_sha256 = '000000000000000000000000000000000000000000000000000000000000013a'
                publication_manifest_sha256 = $artifact.manifest_sha256; source_commit = $artifact.source_commit
                template_commit = $artifact.template_commit; agent_definition_sha256 = $result.publication_receipt.agent_definition_sha256
                adopted_pre_snapshot_sha256 = Get-MonitorObjectSha256 $state; adopted_pre_snapshot = $state
                paused_snapshot_sha256 = Get-MonitorObjectSha256 $current
                publication_receipt_sha256 = Get-MonitorObjectSha256 $result.publication_receipt
                protected_run = $publicationRun; runtime_accepted = $false
            }
        }
        return $result
    }
    if ($Action -in @('Pause', 'Rollback')) {
        # First quiesce only the owned route. Never restore an enabled snapshot.
        Set-MonitorResource $Transport $plan
        Assert-MonitorPlan (Get-MonitorState $Transport) $plan
        if ($Action -eq 'Pause') { return @{ status = 'paused'; runtime_accepted = $false } }
        if (-not $Snapshot) { throw 'Rollback requires the pre-cutover snapshot; owned plan is paused.' }
        if ($Snapshot.agentResourceId -cne $state.agentResourceId) { throw 'Rollback snapshot belongs to another agent; owned plan is paused.' }
        if ($Snapshot.filters | Where-Object { $_.properties.isEnabled }) { throw 'Rollback will not reactivate historical or quickstart routes.' }
        Assert-MonitorQuiescent (Get-MonitorState $Transport) $plan
        $null = Invoke-MonitorTransport $Transport 'SetPlatform' @{ configuration = $Snapshot.platformConfiguration }
        $current = Get-MonitorState $Transport
        Assert-MonitorExact $Snapshot.platformConfiguration $current.platformConfiguration 'rollback-platform'
        Assert-MonitorExact $Snapshot.permissions $current.permissions 'unchanged-rollback-global-policy'
        $null = Invoke-MonitorTransport $Transport 'SetAgentPermissions' @{ permissions = $Snapshot.agentPermissions }
        $current = Get-MonitorState $Transport
        Assert-MonitorExact $Snapshot.permissions $current.permissions 'rollback-permissions'
        Assert-MonitorExact $Snapshot.agentPermissions $current.agentPermissions 'rollback-agent-permissions'
        Assert-MonitorPlan $current $plan
        return @{ status = 'rolled-back-paused'; runtime_accepted = $false }
    }
    if ($othersActive.Count) { throw 'Quiesce overlapping/quickstart/legacy native plans before cutover or activation.' }
    Assert-MonitorPlan $state $plan
    foreach ($item in @(Get-MonitorReviewConfiguration -Root $Root -CaseToolSource $ProtectedCaseSource)) {
        $actual = Invoke-MonitorTransport $Transport 'ReadResource' @{ kind = $item.kind; name = $item.name }
        Assert-MonitorResource $item $actual
    }
    $permissions = Get-MonitorPermissions -Binding $Binding -Root $Root
    $target = @{ type = 'AzMonitor'; connectionName = 'azmonitor'; oboUser = '' }
    if ($Action -eq 'Cutover') {
        if (-not $Snapshot) { throw 'Cutover requires an independently retained preflight snapshot.' }
        Assert-MonitorExact $Snapshot $state 'pre-cutover-snapshot'
        if (@($state.permissions.allow).Count -or @($state.permissions.ask).Count -or @($state.permissions.deny).Count) {
            throw 'Cutover requires explicit review of existing global policies; only the observed empty baseline is supported.'
        }
        if ([string]::IsNullOrWhiteSpace($state.permissionsEtag)) { throw 'Runtime schema gap: permissions ETag is required; no unconditional update.' }
        $ready = Invoke-MonitorTransport $Transport 'VerifyRuntime' @{ commissioning = $Commissioning }
        if ($ready.status -cne 'verified') { throw 'Actual runtime/scanner readiness probes have not passed; cutover remains disabled.' }
        Assert-MonitorActivationWindow $ActivationWindow -Discovery:($manifest.phase -ceq 'SchemaDiscovery')
        if (-not ($null -ne $nativeContext -and $capturedProbe -and
            (Test-MonitorConnectedProfile $capturedProbe))) {
            $null = Invoke-MonitorTransport $Transport 'SetPlatform' @{ configuration = $target }
        }
        $current = Get-MonitorState $Transport
        Assert-MonitorPlatform $current.platformConfiguration
        if ($null -ne $nativeContext -and $capturedProbe -and
            (Test-MonitorConnectedProfile $capturedProbe)) {
            Assert-MonitorBootstrapBinding $state.platformConfiguration $current.platformConfiguration
        }
        Assert-MonitorQuiescent $current $plan
        if ([string]::IsNullOrWhiteSpace($current.permissionsEtag)) { throw 'Runtime schema gap: permissions ETag is required; no unconditional update.' }
        Assert-MonitorExact $state.permissions $current.permissions 'cutover-unchanged-global-permissions'
        Assert-MonitorExact $permissions $current.agentPermissions 'cutover-agent-permissions'
        Assert-MonitorQuiescent $current $plan
        $result = @{ status = 'cutover-disabled'; rollback_snapshot = $Snapshot; runtime_accepted = $false }
        if ($null -ne $nativeContext -and $capturedProbe -and
            (Test-MonitorConnectedProfile $capturedProbe)) {
            $result.original_migration_snapshot_sha256 = $capturedBootstrapReceipt.original_snapshot_sha256
            $result.paused_snapshot_sha256 = Get-MonitorObjectSha256 $Snapshot
            $result.bootstrap_receipt_sha256 = Get-MonitorObjectSha256 $capturedBootstrapReceipt
        }
        return $result
    }
    if ($Action -cne 'Enable') { throw 'Unsupported incident action; no activation.' }
    if ($manifest.phase -ceq 'Production') { Assert-MonitorCommissioning -Commissioning $Commissioning -Root $Root }
    Assert-MonitorPlatform $state.platformConfiguration
    Assert-MonitorExact @{ allow = @(); ask = @(); deny = @() } $state.permissions 'enable-global-permissions'
    Assert-MonitorExact $permissions $state.agentPermissions 'enable-effective-agent-permissions'
    # A trusted adapter must independently verify the actual tool execution,
    # authoritative reader, commissioning installation and scanner permissions.
    # A model-supplied Boolean is not accepted as proof.
    $ready = Invoke-MonitorTransport $Transport 'VerifyRuntime' @{ commissioning = $Commissioning }
    if ($ready.status -cne 'verified') { throw 'Runtime activation prerequisites are not independently verified.' }
    $proofKeys = @('authoritative_reader_bound', 'commissioning_bound', 'scanner_permissions_verified', 'selected_tools_no_approval')
    if ($manifest.phase -ceq 'SchemaDiscovery') {
        if (-not $Snapshot) { throw 'SchemaDiscovery activation requires the original independently retained snapshot.' }
        # Structured receipt computed by the fixed default verifier, not caller
        # supplied generic ready/effective/scanner Booleans.
        Assert-MonitorExact $ProbeProfile.probe_id $ready.probe_id 'verified-discovery-probe'
        Assert-MonitorExact $ProbeProfile.publication_manifest_sha256 $ready.publication_manifest_sha256 'verified-discovery-publication'
        Assert-MonitorExact $ProbeProfile.agent_definition_sha256 $ready.agent_definition_sha256 'verified-discovery-agent'
        $proofKeys = @()
    }
    foreach ($key in $proofKeys) {
        if ($ready[$key] -isnot [bool] -or -not $ready[$key]) { throw 'Runtime activation prerequisites require independently verified Boolean proof.' }
    }
    if ($manifest.phase -ceq 'Production') {
        Assert-MonitorExact $Commissioning $ready.commissioning 'installed-immutable-commissioning'
        Assert-MonitorCommissioning -Commissioning $Commissioning -Root $Root
    }
    $current = Get-MonitorState $Transport
    Assert-MonitorQuiescent $current $plan
    Assert-MonitorPlatform $current.platformConfiguration
    Assert-MonitorBootstrapBinding $state.platformConfiguration $current.platformConfiguration
    Assert-MonitorExact $state.permissions $current.permissions 'pre-enable-global-permissions'
    Assert-MonitorExact $permissions $current.agentPermissions 'pre-enable-effective-agent-permissions'
    if ($manifest.phase -ceq 'Production') { Assert-MonitorCommissioning -Commissioning $Commissioning -Root $Root }
    Assert-MonitorActivationWindow $ActivationWindow -Discovery:($manifest.phase -ceq 'SchemaDiscovery')
    $plan.properties.isEnabled = $true
    try {
        Set-MonitorResource $Transport $plan
        Assert-MonitorPlan (Get-MonitorState $Transport) $plan
        Assert-MonitorActivationWindow $ActivationWindow -Discovery:($manifest.phase -ceq 'SchemaDiscovery')
    }
    catch {
        $plan.properties.isEnabled = $false
        Set-MonitorResource $Transport $plan
        Assert-MonitorPlan (Get-MonitorState $Transport) $plan
        throw 'Activation failed or was uncertain; the owned Monitor route was independently paused.'
    }
    return @{ status = 'enabled'; runtime_accepted = $false }
}
