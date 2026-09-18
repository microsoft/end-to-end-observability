#Requires -Version 7.0
[CmdletBinding()]
param([Parameter(Mandatory)][string]$OutputDirectory)

$ErrorActionPreference = 'Stop'
$kit = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$output = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $output) {
    throw 'Choose a new, nonexistent output directory for the source handoff.'
}
$trees = @('energy_domains_v2', 'support', 'contracts', 'infra', 'scripts', 'tests', 'docs')
$files = @('.gitignore', '.gitattributes', 'README.md', 'pyproject.toml',
    'requirements.txt', 'wsgi.py', 'environment.example.json')
foreach ($name in $files) {
    if (!(Test-Path -LiteralPath (Join-Path $kit $name) -PathType Leaf)) {
        throw "Required kit file is missing: $name"
    }
}
foreach ($tree in $trees) {
    $source = Join-Path $kit $tree
    if ($output -eq $source -or $output.StartsWith($source + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Output must not be inside a source tree.'
    }
}
& (Join-Path $PSScriptRoot 'Verify-Support.ps1')

function Copy-Source([string]$Source, [string]$Destination) {
    if ((Get-Item -LiteralPath $Source -Force).LinkType -in @('SymbolicLink', 'Junction')) {
        throw "Linked source tree is not portable: $Source"
    }
    [IO.Directory]::CreateDirectory($Destination) | Out-Null
    foreach ($item in Get-ChildItem -LiteralPath $Source -Force) {
        if ($item.LinkType -in @('SymbolicLink', 'Junction')) {
            throw "Linked source content is not portable: $($item.FullName)"
        }
        if ($item.Name -in @('.git', '.venv', '__pycache__', '.local', '.azure', 'node_modules', 'build', 'dist') -or
            $item.Name -like '*.egg-info' -or $item.Name -like '.env*' -or
            $item.Name -like '*.local.json' -or $item.Name -eq 'local.settings.json' -or
            $item.Name -match '\.py[co]$|\.tfstate|\.tfplan$|\.zip$') { continue }
        if ($item.PSIsContainer) {
            Copy-Source $item.FullName (Join-Path $Destination $item.Name)
        } else {
            if ((Split-Path -Leaf $Source) -eq 'infra' -and $item.Extension -eq '.json') { continue }
            Copy-Item -LiteralPath $item.FullName -Destination (Join-Path $Destination $item.Name)
        }
    }
}
foreach ($tree in $trees) {
    Copy-Source (Join-Path $kit $tree) (Join-Path $output $tree)
}
foreach ($name in $files) {
    Copy-Item -LiteralPath (Join-Path $kit $name) -Destination (Join-Path $output $name)
}
& (Join-Path $output 'scripts\Verify-Support.ps1')
Write-Host "Portable source handoff: $output"
