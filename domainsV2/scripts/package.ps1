#Requires -Version 7.0
[CmdletBinding()]
param([Parameter(Mandatory)][string]$OutputDirectory)

$ErrorActionPreference = 'Stop'
$kit = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$output = [IO.Path]::GetFullPath($OutputDirectory)
$stage = Join-Path $output 'stage'
$zipPath = Join-Path $output 'domains-v2.zip'
if (Test-Path -LiteralPath $stage) { throw 'Stage already exists; use a fresh output directory.' }
if (Test-Path -LiteralPath $zipPath) { throw 'Candidate ZIP already exists.' }
foreach ($source in @('energy_domains_v2', 'support')) {
    $tree = Join-Path $kit $source
    if ($output -eq $tree -or $output.StartsWith($tree + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Output must not be inside a packaged source tree.'
    }
}
& (Join-Path $PSScriptRoot 'Verify-Support.ps1')
[IO.Directory]::CreateDirectory($stage) | Out-Null

function Copy-Tree([string]$Source, [string]$Destination) {
    if (!(Test-Path -LiteralPath $Source -PathType Container)) { throw "Required tree missing: $Source" }
    if ((Get-Item -LiteralPath $Source -Force).LinkType -in @('SymbolicLink', 'Junction')) { throw "Linked source tree: $Source" }
    foreach ($file in Get-ChildItem -LiteralPath $Source -Recurse -Force) {
        $relative = [IO.Path]::GetRelativePath($Source, $file.FullName)
        if ($relative -match '(^|[\\/])(__pycache__|[^\\/]+\.egg-info|node_modules|\.local|\.git|\.azure|\.venv)([\\/]|$)|\.py[co]$') { continue }
        if ($file.LinkType -in @('SymbolicLink', 'Junction')) { throw "Linked source content is not portable: $relative" }
        if ($file.PSIsContainer) { continue }
        if ($file.Name -like '.env*' -or $file.Name -match '\.tfstate') { throw 'Sensitive file in source tree.' }
        $target = Join-Path $Destination $relative
        [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target)) | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $target
    }
}
foreach ($name in @('pyproject.toml','README.md','requirements.txt')) {
    Copy-Item -LiteralPath (Join-Path $kit $name) -Destination (Join-Path $stage $name)
}
Copy-Item -LiteralPath (Join-Path $kit 'wsgi.py') -Destination (Join-Path $stage 'app.py')
Copy-Tree (Join-Path $kit 'energy_domains_v2') (Join-Path $stage 'energy_domains_v2')
[IO.Directory]::CreateDirectory((Join-Path $stage 'scripts')) | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'transition_replay_state.py') -Destination (Join-Path $stage 'scripts\transition_replay_state.py')
$support = Join-Path $kit 'support'
$manifest = Get-Content -LiteralPath (Join-Path $support 'bundle-manifest.json') -Raw | ConvertFrom-Json
foreach ($relative in @('bundle-manifest.json') + @($manifest.files.path)) {
    $native = $relative.Replace('/', [IO.Path]::DirectorySeparatorChar)
    $target = Join-Path $stage (Join-Path 'support' $native)
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target)) | Out-Null
    Copy-Item -LiteralPath (Join-Path $support $native) -Destination $target
}
foreach ($relative in @(
    'support\domains\power-grid\sample_data\inventory-austin\part_stock_txn.jsonl',
    'support\domains\upstream\generated\review\normal_production\part_stock_txn.jsonl',
    'support\domains\midstream\generated\review\part_stock_txn.jsonl',
    'support\domains\downstream\generated\review\part_stock_txn.jsonl'
)) {
    if (!(Test-Path -LiteralPath (Join-Path $stage $relative))) { throw "Missing inventory ledger: $relative" }
}
if (Test-Path -LiteralPath (Join-Path $stage 'support\dashboard\app.py')) { throw 'V1 gateway must not be packaged.' }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::CreateFromDirectory($stage, $zipPath)
$zip = [IO.Compression.ZipFile]::OpenRead($zipPath)
try {
    foreach ($entry in $zip.Entries) {
        if ($entry.FullName.Contains('\')) { throw 'Linux ZIP must use POSIX separators.' }
    }
    [pscustomobject]@{entries=$zip.Entries.Count;bytes=(Get-Item $zipPath).Length;sha256=(Get-FileHash $zipPath).Hash} | ConvertTo-Json
} finally { $zip.Dispose() }
