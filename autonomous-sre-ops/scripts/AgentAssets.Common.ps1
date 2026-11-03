# Renders the committed agent assets for one deployment context and verifies a rendered tree.
#
# The committed assets carry the synthetic fixture values of tests/fixtures/context.synthetic.json.
# scripts/AgentAssets.map.json lists which context leaves are replaced (rules), which asset roots exist,
# which files are inert (Native/ServiceNow assets that may keep sentinels) and which sentinel shapes must
# never survive in a published file. The render is a pure, offline function of (sources, map, context):
#   - one pass per file over every file under the map roots, longest-first case-insensitive alternation;
#   - an exact-case match becomes the exact target value, an all-lowercase match becomes the lower-cased
#     target value, any other casing fails;
#   - a rule whose target value is null fails when it matches in a non-inert file (inert files keep it);
#   - for a context other than the synthetic one, a non-inert file must not keep a sentinel shape that the
#     target context does not itself contain, nor any value only the synthetic context has;
#   - the synthetic context renders the identity (byte-identical output).
# Export-AsreAgentAssets writes a NEW directory and render-receipt.json last. Assert-AsreRenderedAssets
# re-renders from the sources in memory and requires byte equality; it never trusts the receipt alone.
# No network, no Azure CLI. Messages are fixed strings that never contain an asset or context value.

if (-not (Get-Command Get-AsreContextLeaf -ErrorAction SilentlyContinue)) {
    . (Join-Path $PSScriptRoot 'Context.Common.ps1')
}

function Get-AsreAssetSourceRoot {
    return (Split-Path -Parent $PSScriptRoot)
}

function Resolve-AsreAssetPath {
    param([Parameter(Mandatory)][string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path) -or $Path.IndexOf([char]0) -ge 0) { throw 'A path is empty or invalid.' }
    if (-not [System.IO.Path]::IsPathRooted($Path)) { $Path = Join-Path (Get-Location).ProviderPath $Path }
    $full = [System.IO.Path]::GetFullPath($Path)
    $root = [System.IO.Path]::GetPathRoot($full)
    if ($full.Length -gt $root.Length) {
        $full = $full.TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
    }
    return $full
}

function Test-AsreAssetPathInside {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Parent)
    $prefix = $Parent.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    return $Path.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
}

function Assert-AsreAssetRelativePath {
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Value)
    if ($Value.Length -eq 0 -or $Value.Length -gt 240 -or
        -not [regex]::IsMatch($Value, '\A[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\z') -or
        [regex]::IsMatch($Value, '(?:\A|/)\.{1,2}(?:/|\z)')) {
        throw 'An agent asset path is not a plain relative path.'
    }
    return $Value
}

function Get-AsreSha256Hex {
    param([Parameter(Mandatory)][byte[]]$Bytes)
    return [System.Convert]::ToHexString([System.Security.Cryptography.SHA256]::HashData($Bytes)).ToLowerInvariant()
}

function ConvertTo-AsreCanonicalJson {
    # Sorted-key (ordinal), minified JSON for dictionaries, strings, booleans and null only.
    param($Value)
    if ($null -eq $Value) { return 'null' }
    if ($Value -is [bool]) { if ($Value) { return 'true' } else { return 'false' } }
    if ($Value -is [string]) { return (ConvertTo-Json -InputObject $Value -Compress) }
    if ($Value -is [System.Collections.IDictionary]) {
        $keys = [string[]]@($Value.Keys | ForEach-Object { [string]$_ })
        [System.Array]::Sort($keys, [System.StringComparer]::Ordinal)
        $items = @(foreach ($key in $keys) {
                (ConvertTo-Json -InputObject $key -Compress) + ':' + (ConvertTo-AsreCanonicalJson -Value $Value[$key])
            })
        return '{' + ($items -join ',') + '}'
    }
    throw 'A value cannot be written as canonical JSON.'
}

function ConvertTo-AsreContextDocument {
    # Reduces the loader's context (which carries derived resource ids) to the stored asre-context/v1 document.
    # Optional parts that are null are omitted. The result is proven by loading it again.
    [CmdletBinding()]
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Context)
    $document = @{
        schema          = [string]$Context['schema']
        subscription_id = [string]$Context['subscription_id']
        tenant_id       = [string]$Context['tenant_id']
        location        = [string]$Context['location']
        name_suffix     = [string]$Context['name_suffix']
        resource_groups = @{}
        log_analytics   = @{ name = $Context['log_analytics']['name']; customer_id = $Context['log_analytics']['customer_id'] }
        registry        = @{ name = $Context['registry']['name']; login_server = $Context['registry']['login_server'] }
        key_vault       = @{ name = $Context['key_vault']['name']; uri = $Context['key_vault']['uri'] }
        storage         = @{
            account_name   = $Context['storage']['account_name']
            blob_endpoint  = $Context['storage']['blob_endpoint']
            table_endpoint = $Context['storage']['table_endpoint']
        }
        recovery_vault  = @{ name = $Context['recovery_vault']['name']; resource_group = $Context['recovery_vault']['resource_group'] }
        identities      = @{}
        targets         = @{}
        agent           = @{ name = $Context['agent']['name'] }
    }
    foreach ($group in @('control', 'linux', 'patch', 'backup')) { $document['resource_groups'][$group] = $Context['resource_groups'][$group] }
    foreach ($key in @($Context['identities'].Keys)) {
        $document['identities'][[string]$key] = @{
            client_id    = $Context['identities'][$key]['client_id']
            principal_id = $Context['identities'][$key]['principal_id']
        }
    }
    foreach ($role in @('linux', 'patch', 'backup')) {
        $target = $Context['targets'][$role]
        $document['targets'][$role] = @{ name = $target['name']; resource_group = $target['resource_group']; address = $target['address'] }
    }
    if ($null -ne $Context['operator_object_id']) { $document['operator_object_id'] = [string]$Context['operator_object_id'] }
    if ($null -ne $Context['agent']['endpoint']) { $document['agent']['endpoint'] = [string]$Context['agent']['endpoint'] }
    if ($null -ne $Context['servicenow']) {
        $sn = $Context['servicenow']
        $document['servicenow'] = @{
            origin              = $sn['origin']
            api_base            = $sn['api_base']
            client_id           = $sn['client_id']
            facade_client_id    = $sn['facade_client_id']
            assignment_group    = $sn['assignment_group']
            configuration_items = @{
                linux  = $sn['configuration_items']['linux']
                patch  = $sn['configuration_items']['patch']
                backup = $sn['configuration_items']['backup']
            }
        }
    }
    $text = ConvertTo-AsreCanonicalJson -Value $document
    $again = Get-AsreContextLeaf -Context (ConvertTo-AsreContext -Json $text)
    $original = Get-AsreContextLeaf -Context $Context
    $same = ($again.Count -eq $original.Count)
    if ($same) {
        foreach ($entry in $original.GetEnumerator()) {
            if (-not $again.ContainsKey($entry.Key) -or $again[$entry.Key] -cne $entry.Value) { $same = $false; break }
        }
    }
    if (-not $same) { throw 'The deployment context could not be reduced to its stored document.' }
    return $text
}

function Get-AsreContextSha256 {
    # SHA-256 (lowercase hex) of the canonical sorted-key minified stored document of the context.
    [CmdletBinding()]
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Context)
    $text = ConvertTo-AsreContextDocument -Context $Context
    return (Get-AsreSha256Hex -Bytes ([System.Text.UTF8Encoding]::new($false).GetBytes($text)))
}

function Get-AsreAssetMap {
    # Loads and validates the render map and the synthetic source context it names (both under SourceRoot).
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$SourceRoot)
    $mapPath = Join-Path $SourceRoot 'scripts/AgentAssets.map.json'
    if (-not [System.IO.File]::Exists($mapPath)) { throw 'The agent asset map was not found.' }
    $bytes = [System.IO.File]::ReadAllBytes($mapPath)
    if ($bytes.Length -gt 262144) { throw 'The agent asset map is too large.' }
    try {
        $map = ConvertFrom-AsreStrictJson -Json ([System.Text.UTF8Encoding]::new($false, $true).GetString($bytes))
    }
    catch { throw 'The agent asset map is not valid JSON.' }
    $expectedKeys = @('description', 'format', 'inert', 'roots', 'rules', 'sentinel_patterns', 'source_context')
    $keys = [string[]]@($map.Keys)
    [System.Array]::Sort($keys, [System.StringComparer]::Ordinal)
    if (($keys -join ',') -cne ($expectedKeys -join ',') -or $map['format'] -cne 'asre-agent-assets-map/v1') {
        throw 'The agent asset map has an unexpected shape.'
    }
    foreach ($name in @('roots', 'inert', 'sentinel_patterns', 'rules')) {
        if ($map[$name] -isnot [object[]] -or @($map[$name] | Where-Object { $_ -isnot [string] }).Count -ne 0) {
            throw 'The agent asset map has an unexpected shape.'
        }
    }
    if ($map['roots'].Count -lt 1 -or $map['rules'].Count -lt 1 -or $map['sentinel_patterns'].Count -lt 1) {
        throw 'The agent asset map has an unexpected shape.'
    }
    $roots = @($map['roots'] | ForEach-Object { Assert-AsreAssetRelativePath -Value $_ })
    $inert = @($map['inert'] | ForEach-Object { Assert-AsreAssetRelativePath -Value $_ })
    if (@($roots | Select-Object -Unique).Count -ne $roots.Count -or @($inert | Select-Object -Unique).Count -ne $inert.Count) {
        throw 'The agent asset map repeats an entry.'
    }
    foreach ($a in $roots) {
        foreach ($b in $roots) {
            if ($a -cne $b -and $b.StartsWith($a + '/', [System.StringComparison]::Ordinal)) { throw 'The agent asset map has overlapping roots.' }
        }
    }
    $sentinels = @(foreach ($pattern in $map['sentinel_patterns']) {
            try { [regex]::new($pattern, [System.Text.RegularExpressions.RegexOptions]::CultureInvariant) }
            catch { throw 'The agent asset map has an invalid sentinel pattern.' }
        })
    $sourceContextPath = Join-Path $SourceRoot (Assert-AsreAssetRelativePath -Value $map['source_context'])
    $sourceContext = Get-AsreContext -Path $sourceContextPath -AllowSynthetic
    $sourceLeaf = Get-AsreContextLeaf -Context $sourceContext
    $rules = [System.Collections.Generic.List[object]]::new()
    $seenPath = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
    foreach ($path in $map['rules']) {
        if (-not $seenPath.Add($path)) { throw 'The agent asset map repeats a rule.' }
        if (-not $sourceLeaf.ContainsKey($path) -or $sourceLeaf[$path].Length -lt 4) {
            throw "The agent asset map rule $path has no usable value in the source context."
        }
        $rules.Add(@{ Path = $path; Source = $sourceLeaf[$path] })
    }
    return @{
        Roots         = $roots
        Inert         = $inert
        Sentinels     = $sentinels
        Rules         = $rules.ToArray()
        SourceContext = $sourceContext
        SourceLeaf    = $sourceLeaf
        MapSha256     = (Get-AsreSha256Hex -Bytes $bytes)
        MapPath       = $mapPath
    }
}

function Get-AsreAssetSourceFile {
    # Every file under the map roots as an ordinal-sorted dictionary of relative path -> full path.
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$SourceRoot, [Parameter(Mandatory)][string[]]$Root)
    $textExtensions = @('.json', '.md', '.py', '.txt', '.yaml', '.yml', '.kql')
    $files = [System.Collections.Generic.SortedDictionary[string, string]]::new([System.StringComparer]::Ordinal)
    $add = {
        param([string]$Relative, [string]$Full)
        $null = Assert-AsreAssetRelativePath -Value $Relative
        if ([System.IO.Path]::GetExtension($Relative) -cnotin $textExtensions) { throw "The agent asset $Relative is not a supported text file." }
        if ($files.ContainsKey($Relative)) { throw 'The agent asset roots overlap.' }
        $files[$Relative] = $Full
    }
    foreach ($rootRelative in $Root) {
        $path = Join-Path $SourceRoot $rootRelative
        if ([System.IO.File]::Exists($path)) {
            if (([System.IO.File]::GetAttributes($path) -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'A linked agent asset is not allowed.' }
            & $add $rootRelative $path
        }
        elseif ([System.IO.Directory]::Exists($path)) {
            if (([System.IO.File]::GetAttributes($path) -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'A linked agent asset is not allowed.' }
            $options = [System.IO.EnumerationOptions]::new()
            $options.RecurseSubdirectories = $true
            $options.AttributesToSkip = [System.IO.FileAttributes]0
            foreach ($entry in [System.IO.Directory]::EnumerateFileSystemEntries($path, '*', $options)) {
                $attributes = [System.IO.File]::GetAttributes($entry)
                if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'A linked agent asset is not allowed.' }
                $relative = $rootRelative + '/' + [System.IO.Path]::GetRelativePath($path, $entry).Replace('\', '/')
                # Interpreter caches are never assets; every other file must be an allowed text file.
                if ([regex]::IsMatch($relative, '(?:\A|/)__pycache__(?:/|\z)')) { continue }
                if (($attributes -band [System.IO.FileAttributes]::Directory) -ne 0) { continue }
                & $add $relative $entry
            }
        }
        else { throw "The agent asset root $rootRelative is missing." }
    }
    if ($files.Count -lt 1 -or $files.Count -gt 4096) { throw 'The agent asset file count is out of bounds.' }
    return , $files
}

function New-AsreAssetRender {
    # Renders every asset in memory and applies every guard. Returns the bytes and hashes; writes nothing.
    [CmdletBinding()]
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Context, [Parameter(Mandatory)][string]$SourceRoot)
    $assets = Get-AsreAssetMap -SourceRoot $SourceRoot
    $targetLeaf = Get-AsreContextLeaf -Context $Context
    $sourceSha = Get-AsreContextSha256 -Context $assets.SourceContext
    $contextSha = Get-AsreContextSha256 -Context $Context
    $identity = ($contextSha -ceq $sourceSha)
    $inert = [System.Collections.Generic.HashSet[string]]::new([string[]]$assets.Inert, [System.StringComparer]::Ordinal)

    $rules = foreach ($rule in $assets.Rules) {
        $target = $null
        if ($targetLeaf.ContainsKey($rule.Path)) { $target = $targetLeaf[$rule.Path] }
        @{ Path = $rule.Path; Source = $rule.Source; Target = $target }
    }
    $byLower = @{}
    foreach ($rule in $rules) {
        $lower = $rule.Source.ToLowerInvariant()
        if ($byLower.ContainsKey($lower)) {
            if ($byLower[$lower].Target -cne $rule.Target) { throw 'The agent asset map has two rules with one source value and different targets.' }
            continue
        }
        $byLower[$lower] = $rule
    }
    $ordered = @($byLower.Values | Sort-Object -Property @{ Expression = { $_.Source.Length }; Descending = $true }, @{ Expression = { $_.Source.ToLowerInvariant() } })
    $pattern = (@($ordered | ForEach-Object { [regex]::Escape($_.Source) })) -join '|'
    $ruleRegex = [regex]::new($pattern, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase -bor [System.Text.RegularExpressions.RegexOptions]::CultureInvariant)

    # Values only the synthetic context has (a synthetic leaf whose target value differs or is absent).
    $needles = [System.Collections.Generic.List[object]]::new()
    foreach ($entry in $assets.SourceLeaf.GetEnumerator()) {
        if (-not $targetLeaf.ContainsKey($entry.Key) -or $targetLeaf[$entry.Key] -cne $entry.Value) {
            $needles.Add(@{ Path = $entry.Key; Value = $entry.Value.ToLowerInvariant() })
        }
    }
    $haystack = ($targetLeaf.Values | ForEach-Object { $_.ToLowerInvariant() }) -join "`n"

    $sourceFiles = Get-AsreAssetSourceFile -SourceRoot $SourceRoot -Root $assets.Roots
    $strict = [System.Text.UTF8Encoding]::new($false, $true)
    $plain = [System.Text.UTF8Encoding]::new($false)
    $files = [System.Collections.Generic.SortedDictionary[string, byte[]]]::new([System.StringComparer]::Ordinal)
    $sources = [System.Collections.Generic.SortedDictionary[string, string]]::new([System.StringComparer]::Ordinal)
    foreach ($entry in $sourceFiles.GetEnumerator()) {
        $relative = $entry.Key
        $bytes = [System.IO.File]::ReadAllBytes($entry.Value)
        if ($bytes.Length -gt 1048576) { throw "The agent asset $relative is too large." }
        if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) { throw "The agent asset $relative must be UTF-8 without a byte order mark." }
        try { $text = $strict.GetString($bytes) } catch { throw "The agent asset $relative is not valid UTF-8." }
        if ($text.IndexOf([char]0) -ge 0) { throw "The agent asset $relative is not a text file." }
        $sources[$relative] = Get-AsreSha256Hex -Bytes $bytes
        $isInert = $inert.Contains($relative)

        $builder = [System.Text.StringBuilder]::new($text.Length + 256)
        $position = 0
        foreach ($match in $ruleRegex.Matches($text)) {
            $rule = $byLower[$match.Value.ToLowerInvariant()]
            if ($null -eq $rule) { throw "The agent asset $relative matched a value that is not one rule." }
            if ($match.Index -gt $position) { $null = $builder.Append($text, $position, $match.Index - $position) }
            $position = $match.Index + $match.Length
            if ($null -eq $rule.Target) {
                if (-not $isInert) { throw "The agent asset $relative uses $($rule.Path), which the deployment context does not provide." }
                $null = $builder.Append($match.Value)
            }
            elseif ($match.Value -ceq $rule.Source) { $null = $builder.Append($rule.Target) }
            elseif ($match.Value -ceq $rule.Source.ToLowerInvariant()) { $null = $builder.Append($rule.Target.ToLowerInvariant()) }
            else { throw "The agent asset $relative writes $($rule.Path) in an unsupported letter case." }
        }
        if ($position -lt $text.Length) { $null = $builder.Append($text, $position, $text.Length - $position) }
        $rendered = $builder.ToString()

        if (-not $identity -and -not $isInert) {
            $lowerText = $rendered.ToLowerInvariant()
            foreach ($sentinel in $assets.Sentinels) {
                foreach ($hit in $sentinel.Matches($rendered)) {
                    if (-not $haystack.Contains($hit.Value.ToLowerInvariant())) { throw "The agent asset $relative still carries a sentinel value after rendering." }
                }
            }
            foreach ($needle in $needles) {
                if ($lowerText.Contains($needle.Value)) { throw "The agent asset $relative still carries the synthetic value of $($needle.Path) after rendering." }
            }
        }
        $files[$relative] = $plain.GetBytes($rendered)
    }
    $inertSorted = [string[]]@($assets.Inert)
    [System.Array]::Sort($inertSorted, [System.StringComparer]::Ordinal)
    return @{
        Files      = $files
        Sources    = $sources
        MapSha256  = $assets.MapSha256
        ContextSha = $contextSha
        Inert      = $inertSorted
        Roots      = $assets.Roots
        MapPath    = $assets.MapPath
        Identity   = $identity
    }
}

function Get-AsreAssetSourceState {
    # source_commit / source_dirty for the receipt. Unknown (not a repository, no git) is reported as dirty.
    param([Parameter(Mandatory)][string]$SourceRoot, [Parameter(Mandatory)][string[]]$Path)
    $commit = 'unknown'
    $dirty = $true
    $git = Get-Command -Name git -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $git) {
        $head = @(& $git.Source --no-optional-locks -C $SourceRoot rev-parse HEAD 2>$null)
        if ($LASTEXITCODE -eq 0 -and $head.Count -eq 1 -and [regex]::IsMatch([string]$head[0], '\A[0-9a-f]{40}\z')) {
            $commit = [string]$head[0]
            $status = @(& $git.Source --no-optional-locks -C $SourceRoot status --porcelain --untracked-files=normal -- @Path 2>$null)
            if ($LASTEXITCODE -eq 0) { $dirty = ($status.Count -gt 0) }
        }
    }
    return @{ Commit = $commit; Dirty = $dirty }
}

function Export-AsreAgentAssets {
    # Renders the assets for the context into a NEW directory and writes render-receipt.json last.
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][System.Collections.IDictionary]$Context,
        [string]$OutputRoot,
        [string]$SourceRoot
    )
    if ([string]::IsNullOrWhiteSpace($SourceRoot)) { $SourceRoot = Get-AsreAssetSourceRoot }
    $source = Resolve-AsreAssetPath -Path $SourceRoot
    $render = New-AsreAssetRender -Context $Context -SourceRoot $source
    if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
        $stamp = [System.DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ', [System.Globalization.CultureInfo]::InvariantCulture)
        $OutputRoot = Join-Path $source ('out/agent-assets/' + $stamp + '-' + $render.ContextSha.Substring(0, 8))
    }
    $output = Resolve-AsreAssetPath -Path $OutputRoot
    $allowed = Resolve-AsreAssetPath -Path (Join-Path $source 'out')
    if ($output -ieq $source -or ((Test-AsreAssetPathInside -Path $output -Parent $source) -and -not (Test-AsreAssetPathInside -Path $output -Parent $allowed))) {
        throw 'Rendered assets must not be written into the tracked source tree.'
    }
    if (Test-AsreAssetPathInside -Path $source -Parent $output) { throw 'Rendered assets must not be written around the source tree.' }
    if ([System.IO.Directory]::Exists($output) -or [System.IO.File]::Exists($output)) { throw 'The output directory already exists.' }

    $state = Get-AsreAssetSourceState -SourceRoot $source -Path @($render.Roots + @('scripts/AgentAssets.map.json'))
    $filesOrdered = [System.Collections.Generic.SortedDictionary[string, string]]::new([System.StringComparer]::Ordinal)
    foreach ($entry in $render.Files.GetEnumerator()) { $filesOrdered[$entry.Key] = Get-AsreSha256Hex -Bytes $entry.Value }
    $receipt = [ordered]@{
        schema        = 'asre-agent-assets-receipt/v1'
        rendered_at   = [System.DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ', [System.Globalization.CultureInfo]::InvariantCulture)
        source_commit = $state.Commit
        source_dirty  = [bool]$state.Dirty
        map_sha256    = $render.MapSha256
        context_sha256 = $render.ContextSha
        inert         = @($render.Inert)
        sources       = $render.Sources
        files         = $filesOrdered
    }
    $receiptText = (ConvertTo-Json -InputObject $receipt -Depth 4).Replace("`r`n", "`n") + "`n"
    $receiptBytes = [System.Text.UTF8Encoding]::new($false).GetBytes($receiptText)

    $created = $false
    try {
        [void][System.IO.Directory]::CreateDirectory($output)
        $created = $true
        foreach ($entry in $render.Files.GetEnumerator()) {
            $target = Join-Path $output $entry.Key
            [void][System.IO.Directory]::CreateDirectory((Split-Path -Parent $target))
            $stream = [System.IO.File]::Open($target, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write)
            try { $stream.Write($entry.Value, 0, $entry.Value.Length) } finally { $stream.Dispose() }
        }
        $stream = [System.IO.File]::Open((Join-Path $output 'render-receipt.json'), [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write)
        try { $stream.Write($receiptBytes, 0, $receiptBytes.Length) } finally { $stream.Dispose() }
    }
    catch {
        if ($created) { try { [System.IO.Directory]::Delete($output, $true) } catch { $null = $_ } }
        throw 'The rendered assets could not be written.'
    }
    return [pscustomobject]@{
        Root          = $output
        FileCount     = $render.Files.Count
        ReceiptSha256 = (Get-AsreSha256Hex -Bytes $receiptBytes)
    }
}

function Assert-AsreRenderedAssets {
    # Re-renders in memory and requires the tree to be exactly that render plus a consistent receipt.
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][System.Collections.IDictionary]$Context,
        [Parameter(Mandatory)][string]$Root,
        [string]$SourceRoot
    )
    if ([string]::IsNullOrWhiteSpace($SourceRoot)) { $SourceRoot = Get-AsreAssetSourceRoot }
    $source = Resolve-AsreAssetPath -Path $SourceRoot
    $rootFull = Resolve-AsreAssetPath -Path $Root
    if (-not [System.IO.Directory]::Exists($rootFull)) { throw 'The rendered asset root does not exist.' }
    if (([System.IO.File]::GetAttributes($rootFull) -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'A linked rendered asset root is not allowed.' }
    $render = New-AsreAssetRender -Context $Context -SourceRoot $source

    $actual = [System.Collections.Generic.SortedDictionary[string, string]]::new([System.StringComparer]::Ordinal)
    $options = [System.IO.EnumerationOptions]::new()
    $options.RecurseSubdirectories = $true
    $options.AttributesToSkip = [System.IO.FileAttributes]0
    foreach ($entry in [System.IO.Directory]::EnumerateFileSystemEntries($rootFull, '*', $options)) {
        $attributes = [System.IO.File]::GetAttributes($entry)
        if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'A linked rendered asset is not allowed.' }
        if (($attributes -band [System.IO.FileAttributes]::Directory) -ne 0) { continue }
        if ($actual.Count -ge 4200) { throw 'The rendered asset tree has too many files.' }
        $actual[[System.IO.Path]::GetRelativePath($rootFull, $entry).Replace('\', '/')] = $entry
    }
    foreach ($name in $render.Files.Keys) {
        if (-not $actual.ContainsKey($name)) { throw "The rendered asset tree is missing $name." }
    }
    foreach ($name in $actual.Keys) {
        if ($name -cne 'render-receipt.json' -and -not $render.Files.ContainsKey($name)) { throw "The rendered asset tree has an unexpected file $name." }
    }
    if (-not $actual.ContainsKey('render-receipt.json')) { throw 'The rendered asset tree has no render receipt.' }
    $hashes = [System.Collections.Generic.SortedDictionary[string, string]]::new([System.StringComparer]::Ordinal)
    foreach ($entry in $render.Files.GetEnumerator()) {
        $bytes = [System.IO.File]::ReadAllBytes($actual[$entry.Key])
        $hash = Get-AsreSha256Hex -Bytes $bytes
        if ($bytes.Length -ne $entry.Value.Length -or $hash -cne (Get-AsreSha256Hex -Bytes $entry.Value)) {
            throw "The rendered asset $($entry.Key) differs from a fresh render."
        }
        $hashes[$entry.Key] = $hash
    }

    $receiptBytes = [System.IO.File]::ReadAllBytes($actual['render-receipt.json'])
    if ($receiptBytes.Length -gt 1048576) { throw 'The render receipt is too large.' }
    try { $receipt = ConvertFrom-AsreStrictJson -Json ([System.Text.UTF8Encoding]::new($false, $true).GetString($receiptBytes)) }
    catch { throw 'The render receipt is not valid JSON.' }
    $expectedKeys = @('context_sha256', 'files', 'inert', 'map_sha256', 'rendered_at', 'schema', 'source_commit', 'source_dirty', 'sources')
    $keys = [string[]]@($receipt.Keys)
    [System.Array]::Sort($keys, [System.StringComparer]::Ordinal)
    if (($keys -join ',') -cne ($expectedKeys -join ',') -or $receipt['schema'] -cne 'asre-agent-assets-receipt/v1') { throw 'The render receipt has an unexpected shape.' }
    if ($receipt['rendered_at'] -isnot [string] -or -not [regex]::IsMatch($receipt['rendered_at'], '\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\z') -or
        $receipt['source_commit'] -isnot [string] -or -not [regex]::IsMatch($receipt['source_commit'], '\A(?:[0-9a-f]{40}|unknown)\z') -or
        $receipt['source_dirty'] -isnot [bool]) { throw 'The render receipt has an unexpected shape.' }
    if ($receipt['map_sha256'] -cne $render.MapSha256) { throw 'The render receipt was made with a different asset map.' }
    if ($receipt['context_sha256'] -cne $render.ContextSha) { throw 'The render receipt was made for a different deployment context.' }
    if ($receipt['inert'] -isnot [object[]] -or ($receipt['inert'] -join "`n") -cne ($render.Inert -join "`n")) { throw 'The render receipt lists different inert assets.' }
    foreach ($pair in @(@('sources', $render.Sources), @('files', $hashes))) {
        $recorded = $receipt[$pair[0]]
        $expected = $pair[1]
        if ($recorded -isnot [System.Collections.IDictionary] -or $recorded.Count -ne $expected.Count) { throw "The render receipt $($pair[0]) do not match." }
        foreach ($entry in $expected.GetEnumerator()) {
            if (-not $recorded.ContainsKey($entry.Key) -or $recorded[$entry.Key] -cne $entry.Value) { throw "The render receipt $($pair[0]) do not match." }
        }
    }
    return [pscustomobject]@{
        Root          = $rootFull
        FileCount     = $render.Files.Count
        ReceiptSha256 = (Get-AsreSha256Hex -Bytes $receiptBytes)
    }
}
