#Requires -Version 7.5
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
$declaration = Get-Content -LiteralPath (Join-Path $root 'agent/tools/servicenow_case.json') -Raw | ConvertFrom-Json
if ($declaration.name -cne 'asre-servicenow-case' -or $declaration.source -cne 'servicenow_case.py' -or
    $declaration.callable -cne 'main') { throw 'Unexpected fixed case-tool declaration.' }
$workflow = Get-Content -LiteralPath (Join-Path $root 'agent/workflows/azure-monitor-review.json') -Raw | ConvertFrom-Json -DateKind String
if ($workflow.commissioning.version -cne '1.0' -or
    @($workflow.commissioning.PSObject.Properties.Name).Count -ne 2 -or
    -not $workflow.commissioning.minimum_not_before) { throw 'The shared commissioning policy is invalid.' }
$configurationPath = Join-Path $root 'config/native-incident-runtime.json'
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
if ($HealthOnly) {
    # The existing frozen null receiver admits only argument-free facade health.
    # Do not read or validate protected commissioning for this explicit artifact.
    $runtimeConfiguration = 'null'
} else {
    if (-not [IO.File]::Exists($configurationPath)) { throw 'The fixed protected native runtime configuration is required.' }
    $raw = [IO.File]::ReadAllText($configurationPath)
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
    $scope = $workflow.scope
    $readerNames = @($config.reader.PSObject.Properties.Name)
    if ($config.rule_id -ine $scope.rule_id -or $config.resource_id -ine $scope.resource_id -or
        $config.facade_origin -cne 'https://example-instance.service-now.com' -or $config.facade_client_id -cne '0000000000000000000000000000005a' -or
        $readerNames.Count -ne 4 -or @('sre_origin','workspace_resource_id','log_analytics_workspace_id','native_dto_version' | Where-Object { $_ -cnotin $readerNames }).Count -ne 0 -or
        $config.reader.sre_origin -cne 'https://asre-demo-cus--0a1b2c3d.4e5f6a7b.centralus.azuresre.ai' -or
        $config.reader.workspace_resource_id -ine $scope.workspace_resource_id -or
        $config.reader.log_analytics_workspace_id -cne '00000062-000a-4000-8000-000000000062' -or
        $config.reader.native_dto_version -cne 'azmonitor-incident/v1') { throw 'Trusted runtime binding differs from the fixed reviewed reader/facade.' }
    $runtimeConfiguration = $config | ConvertTo-Json -Depth 8 -Compress
}
$sources = [ordered]@{
    '_asre_case/__init__.py' = ''
    '_asre_case/agent_case.py' = [IO.File]::ReadAllText((Join-Path $root 'operations/agent_case.py'))
    '_asre_case/patch_provider.py' = [IO.File]::ReadAllText((Join-Path $root 'operations/patch_provider.py'))
    '_asre_case/servicenow.py' = [IO.File]::ReadAllText((Join-Path $root 'operations/servicenow.py'))
    '_asre_case/commissioning.json' = ($workflow.commissioning | ConvertTo-Json -Compress)
    '_asre_case/runtime-configuration.json' = $runtimeConfiguration
}
foreach ($name in @('servicenow-case', 'servicenow-case-write', 'servicenow-note-write',
                    'servicenow-feedback-ack', 'servicenow-action-audit', 'servicenow-adapter-response')) {
    $path = "operations/contracts/$name.schema.json"
    $sources["_asre_case/contracts/$name.schema.json"] = [IO.File]::ReadAllText((Join-Path $root $path))
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
                $bytes = [Text.UTF8Encoding]::new($false).GetBytes($sources[$name])
                $stream.Write($bytes, 0, $bytes.Length)
            } finally { $stream.Dispose() }
        }
    } finally { $zip.Dispose() }
    $template = [IO.File]::ReadAllText((Join-Path $root "agent/tools/$($declaration.source)"))
    $marker = '__CASE_TOOL_BUNDLE__ = ""'
    if (($template.Split($marker).Count - 1) -ne 1) { throw 'Expected one bundle marker.' }
    $source = $template.Replace($marker, ('__CASE_TOOL_BUNDLE__ = "' + [Convert]::ToBase64String($memory.ToArray()) + '"'))
} finally { $memory.Dispose() }
if ($OutputPath) {
    if (-not [IO.Path]::IsPathFullyQualified($OutputPath) -or [IO.Path]::GetExtension($OutputPath) -cne '.py') {
        throw 'OutputPath must be an absolute generated .py path.'
    }
    # Generated artifacts are caller-owned. Never replace product source.
    $full = [IO.Path]::GetFullPath($OutputPath)
    if ($full.StartsWith($root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Generated bundles must be outside the repository.'
    }
    $file = [IO.File]::Open($full, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
    try {
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes($source)
        $file.Write($bytes, 0, $bytes.Length)
    } finally { $file.Dispose() }
}
if ($PassThru) { $source }
