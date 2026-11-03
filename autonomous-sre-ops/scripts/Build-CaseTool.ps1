#Requires -Version 7.5
<#
.SYNOPSIS
Builds the generated case-tool .py (a bundle in one file) for the deployment context.

.DESCRIPTION
The deployment context (Get-AsreContext: ASRE_CONTEXT_FILE, else infra/environments/context.generated.json) must
carry its ServiceNow section and the agent endpoint. The agent assets are rendered for that context into a private
temporary directory (Export-AsreAgentAssets); the declaration, the template, the commissioning policy, the runtime
configuration and the contracts are read from that render and the directory is deleted before the script returns.
Every exact binding of the runtime configuration (facade origin and client id, SRE origin, workspace customer id,
scope rule, resource and workspace ids) is checked against the context. -HealthOnly never reads or validates the
protected runtime configuration. The output is deterministic, never replaces a file and is never written inside the
repository.
#>
[CmdletBinding()]
param(
    [string]$OutputPath,
    [switch]$PassThru,
    [switch]$HealthOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $OutputPath -and -not $PassThru) { throw 'Specify OutputPath or PassThru.' }
$root = Split-Path -Parent $PSScriptRoot
if (-not (Get-Command Get-AsreContext -ErrorAction SilentlyContinue)) { . (Join-Path $PSScriptRoot 'Context.Common.ps1') }
if (-not (Get-Command Export-AsreAgentAssets -ErrorAction SilentlyContinue)) { . (Join-Path $PSScriptRoot 'AgentAssets.Common.ps1') }

if ($OutputPath) {
    if (-not [IO.Path]::IsPathFullyQualified($OutputPath) -or [IO.Path]::GetExtension($OutputPath) -cne '.py') {
        throw 'OutputPath must be an absolute generated .py path.'
    }
    # Generated artifacts are caller-owned. Never replace product source.
    $full = [IO.Path]::GetFullPath($OutputPath)
    if ($full.StartsWith($root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Generated bundles must be outside the repository.'
    }
}

$context = Get-AsreContext
$servicenow = $context['servicenow']
if ($null -eq $servicenow -or [string]::IsNullOrWhiteSpace([string]$context['agent']['endpoint'])) {
    throw 'The case-tool bundle needs a deployment context with its ServiceNow section and agent endpoint.'
}

$configurationRelative = 'config/native-incident-runtime.json'
if (-not $HealthOnly -and -not [IO.File]::Exists((Join-Path $root $configurationRelative))) {
    throw 'The fixed protected native runtime configuration is required.'
}

function Assert-UniqueJsonFields([Text.Json.JsonElement]$Item) {
    if ($Item.ValueKind -eq [Text.Json.JsonValueKind]::Object) {
        $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
        foreach ($property in $Item.EnumerateObject()) {
            if (-not $seen.Add($property.Name)) { throw 'Duplicate runtime configuration fields are forbidden.' }
            Assert-UniqueJsonFields $property.Value
        }
    } elseif ($Item.ValueKind -eq [Text.Json.JsonValueKind]::Array) {
        foreach ($child in $Item.EnumerateArray()) { Assert-UniqueJsonFields $child }
    }
}

function Copy-StagedFile([string]$From, [string]$To) {
    if (([IO.File]::GetAttributes($From) -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'A linked agent asset is not allowed.' }
    [void][IO.Directory]::CreateDirectory((Split-Path -Parent $To))
    [IO.File]::WriteAllBytes($To, [IO.File]::ReadAllBytes($From))
}

function New-HealthOnlySource([string]$Source, [string]$Target) {
    # Health-only packaging must not depend on the protected runtime configuration (present, absent or malformed),
    # yet the asset render needs every root of its map. Render from a private copy of the roots in which that one
    # file is a neutral stub; every other byte is copied unchanged.
    $mapRelative = 'scripts/AgentAssets.map.json'
    try {
        $map = [IO.File]::ReadAllText((Join-Path $Source $mapRelative)) | ConvertFrom-Json -AsHashtable
        if ($map -isnot [System.Collections.IDictionary] -or $map['roots'] -isnot [object[]]) { throw 'shape' }
        $roots = @($map['roots'] | ForEach-Object { Assert-AsreAssetRelativePath -Value ([string]$_) })
        $sourceContext = Assert-AsreAssetRelativePath -Value ([string]$map['source_context'])
    } catch { throw 'The agent asset map is not usable.' }
    Copy-StagedFile (Join-Path $Source $mapRelative) (Join-Path $Target $mapRelative)
    Copy-StagedFile (Join-Path $Source $sourceContext) (Join-Path $Target $sourceContext)
    foreach ($relative in $roots) {
        $from = Join-Path $Source $relative
        $to = Join-Path $Target $relative
        if ($relative -ceq $configurationRelative) {
            [void][IO.Directory]::CreateDirectory((Split-Path -Parent $to))
            [IO.File]::WriteAllText($to, 'null', [Text.UTF8Encoding]::new($false))
        } elseif ([IO.File]::Exists($from)) {
            Copy-StagedFile $from $to
        } elseif ([IO.Directory]::Exists($from)) {
            $options = [IO.EnumerationOptions]::new()
            $options.RecurseSubdirectories = $true
            $options.AttributesToSkip = [IO.FileAttributes]0
            foreach ($entry in [IO.Directory]::EnumerateFileSystemEntries($from, '*', $options)) {
                $attributes = [IO.File]::GetAttributes($entry)
                if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'A linked agent asset is not allowed.' }
                $inner = [IO.Path]::GetRelativePath($from, $entry).Replace('\', '/')
                if ([regex]::IsMatch($inner, '(?:\A|/)__pycache__(?:/|\z)') -or ($attributes -band [IO.FileAttributes]::Directory) -ne 0) { continue }
                Copy-StagedFile $entry (Join-Path $to $inner)
            }
        } else { throw 'An agent asset root is missing.' }
    }
    return $Target
}

# The private render lives in a temporary directory that does not exist yet and is always removed.
$work = Join-Path ([IO.Path]::GetTempPath()) ('asre-case-tool-' + [guid]::NewGuid().ToString('N'))
try {
    $assetSource = $root
    if ($HealthOnly) { $assetSource = New-HealthOnlySource -Source $root -Target (Join-Path $work 'source') }
    $rendered = (Export-AsreAgentAssets -Context $context -SourceRoot $assetSource -OutputRoot (Join-Path $work 'rendered')).Root
    function Read-Rendered([string]$Relative) { [IO.File]::ReadAllText((Join-Path $rendered $Relative)) }

    $declaration = Read-Rendered 'agent/tools/servicenow_case.json' | ConvertFrom-Json
    if ($declaration.name -cne 'asre-servicenow-case' -or $declaration.source -cne 'servicenow_case.py' -or
        $declaration.callable -cne 'main') { throw 'Unexpected fixed case-tool declaration.' }
    $workflow = Read-Rendered 'agent/workflows/azure-monitor-review.json' | ConvertFrom-Json -DateKind String
    if ($workflow.commissioning.version -cne '1.0' -or
        @($workflow.commissioning.PSObject.Properties.Name).Count -ne 2 -or
        -not $workflow.commissioning.minimum_not_before) { throw 'The shared commissioning policy is invalid.' }
    if ($HealthOnly) {
        # The existing frozen null receiver admits only argument-free facade health.
        # Do not read or validate protected commissioning for this explicit artifact.
        $runtimeConfiguration = 'null'
    } else {
        $raw = Read-Rendered $configurationRelative
        if ($raw.Length -gt 192000) { throw 'Runtime configuration exceeds its bound.' }
        # Reject duplicate fields rather than silently reserialize the last value.
        $document = [Text.Json.JsonDocument]::Parse($raw)
        try {
            Assert-UniqueJsonFields $document.RootElement
        } finally { $document.Dispose() }
        $config = $raw | ConvertFrom-Json -DateKind String
        $names = @($config.PSObject.Properties.Name)
        $required = @('version','not_before','historical_alert_ids','rule_id','resource_id','facade_origin','facade_client_id','reader')
        if (@($required | Where-Object { $_ -cnotin $names }).Count -ne 0 -or
            @($names | Where-Object { $_ -cnotin ($required + @('expires_at','finite_commissioning','profiles')) }).Count -ne 0 -or
            $config.version -cne '1.0' -or $config.not_before -isnot [string] -or
            $config.historical_alert_ids -isnot [array] -or $config.historical_alert_ids.Count -gt 1000 -or
            @($config.historical_alert_ids | Where-Object { $_ -isnot [string] -or $_.Length -gt 2048 }).Count -ne 0) {
            throw 'Unexpected trusted runtime configuration shape.'
        }
        if ('profiles' -cin $names -and $config.profiles -isnot [array]) {
            throw 'Protected runtime profiles must be an array.'
        }
        $profiles = @(if ('profiles' -cin $names) { $config.profiles } else { 'disk' })
        if ($profiles.Count -lt 1 -or $profiles.Count -gt 5 -or
            @($profiles | Where-Object { $_ -isnot [string] -or $_ -cnotin @('disk','patch-installation','alert-recurrence','capacity-growth','backup-job') }).Count -or
            @($profiles | Select-Object -Unique).Count -ne $profiles.Count -or 'disk' -cnotin $profiles) {
            throw 'Protected runtime profiles must select only the five closed released bindings.'
        }
        $hasExpiry = 'expires_at' -cin $names
        $dates = @($config.not_before, $workflow.commissioning.minimum_not_before)
        if ($hasExpiry) { $dates += $config.expires_at }
        foreach ($date in $dates) {
            if ($date -isnot [string]) { throw 'Runtime timestamps must be strings.' }
            if ($date -cnotmatch '(?:Z|[+-]\d{2}:\d{2})$') { throw 'Runtime timestamps must include their exact timezone.' }
        }
        $culture = [Globalization.CultureInfo]::InvariantCulture
        $lower = [DateTimeOffset]::Parse($config.not_before, $culture)
        $upper = if ($hasExpiry) { [DateTimeOffset]::Parse($config.expires_at, $culture) } else { $null }
        $minimum = [DateTimeOffset]::Parse($workflow.commissioning.minimum_not_before, $culture)
        if ($lower -lt $minimum -or ($hasExpiry -and $upper -le $lower)) {
            throw 'Runtime commissioning violates its versioned minimum or optional reviewed expiry.'
        }
        if ('finite_commissioning' -cin $names) {
            $finite = $config.finite_commissioning
            if ($null -eq $finite -or $finite -isnot [pscustomobject] -or
                @($finite.PSObject.Properties.Name).Count -ne 1 -or
                @($finite.PSObject.Properties.Name)[0] -cne 'version' -or
                $finite.version -isnot [string] -or $finite.version -cne '1.0' -or
                -not $hasExpiry -or ($upper - $lower).TotalSeconds -le 0 -or ($upper - $lower).TotalSeconds -gt 600 -or
                $config.not_before -cnotmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)\z' -or
                $config.expires_at -cnotmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)\z') {
                throw 'Finite commissioning requires its closed version and an explicit UTC window of at most 600 seconds.'
            }
        }
        # Exact bindings: each one must equal the value of the deployment context (never weaker than a fixed literal).
        $scope = $workflow.scope
        $readerNames = @($config.reader.PSObject.Properties.Name)
        $ruleIdPrefix = [string]$context['resource_group_ids']['control'] + '/providers/Microsoft.Insights/scheduledQueryRules/'
        if ($config.rule_id -ine $scope.rule_id -or $config.resource_id -ine $scope.resource_id -or
            ([string]$scope.rule_id).Length -le $ruleIdPrefix.Length -or
            -not ([string]$scope.rule_id).StartsWith($ruleIdPrefix, [StringComparison]::OrdinalIgnoreCase) -or
            $scope.resource_id -ine $context['targets']['linux']['resource_id'] -or
            $scope.workspace_resource_id -ine $context['log_analytics']['resource_id'] -or
            $config.facade_origin -cne $servicenow['origin'] -or $config.facade_client_id -cne $servicenow['facade_client_id'] -or
            $readerNames.Count -ne 4 -or @('sre_origin','workspace_resource_id','log_analytics_workspace_id','native_dto_version' | Where-Object { $_ -cnotin $readerNames }).Count -ne 0 -or
            $config.reader.sre_origin -cne $context['agent']['endpoint'] -or
            $config.reader.workspace_resource_id -ine $scope.workspace_resource_id -or
            $config.reader.log_analytics_workspace_id -cne $context['log_analytics']['customer_id'] -or
            $config.reader.native_dto_version -cne 'azmonitor-incident/v1') { throw 'Trusted runtime binding differs from the deployment context.' }
        $runtimeConfiguration = $config | ConvertTo-Json -Depth 8 -Compress
    }
    $sources = [ordered]@{
        '_asre_case/__init__.py' = ''
        '_asre_case/agent_case.py' = [IO.File]::ReadAllText((Join-Path $root 'operations/agent_case.py'))
        '_asre_case/patch_provider.py' = [IO.File]::ReadAllText((Join-Path $root 'operations/patch_provider.py'))
        '_asre_case/servicenow.py' = [IO.File]::ReadAllText((Join-Path $root 'operations/servicenow.py'))
        '_asre_case/context.py' = [IO.File]::ReadAllBytes((Join-Path $root 'operations/context.py'))
        '_asre_case/context.json' = ConvertTo-AsreContextDocument -Context $context
        '_asre_case/commissioning.json' = ($workflow.commissioning | ConvertTo-Json -Compress)
        '_asre_case/runtime-configuration.json' = $runtimeConfiguration
    }
    foreach ($name in @('servicenow-case', 'servicenow-case-write', 'servicenow-note-write',
                        'servicenow-feedback-ack', 'servicenow-action-audit', 'servicenow-adapter-response')) {
        $sources["_asre_case/contracts/$name.schema.json"] = Read-Rendered "operations/contracts/$name.schema.json"
    }
    $memory = [IO.MemoryStream]::new()
    try {
        $zip = [IO.Compression.ZipArchive]::new($memory, [IO.Compression.ZipArchiveMode]::Create, $true)
        try {
            foreach ($name in $sources.Keys) {
                $entry = $zip.CreateEntry($name, [IO.Compression.CompressionLevel]::Optimal)
                $entry.LastWriteTime = [DateTimeOffset]::new(1980, 1, 1, 0, 0, 0, [TimeSpan]::Zero)
                $stream = $entry.Open()
                try {
                    if ($sources[$name] -is [byte[]]) { $bytes = $sources[$name] }
                    else { $bytes = [Text.UTF8Encoding]::new($false).GetBytes($sources[$name]) }
                    $stream.Write($bytes, 0, $bytes.Length)
                } finally { $stream.Dispose() }
            }
        } finally { $zip.Dispose() }
        $template = Read-Rendered "agent/tools/$($declaration.source)"
        $marker = '__CASE_TOOL_BUNDLE__ = ""'
        if (($template.Split($marker).Count - 1) -ne 1) { throw 'Expected one bundle marker.' }
        $source = $template.Replace($marker, ('__CASE_TOOL_BUNDLE__ = "' + [Convert]::ToBase64String($memory.ToArray()) + '"'))
    } finally { $memory.Dispose() }
} finally {
    try { if ([IO.Directory]::Exists($work)) { [IO.Directory]::Delete($work, $true) } } catch { $null = $_ }
}
if ($OutputPath) {
    $file = [IO.File]::Open($full, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
    try {
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes($source)
        $file.Write($bytes, 0, $bytes.Length)
    } finally { $file.Dispose() }
}
if ($PassThru) { $source }
