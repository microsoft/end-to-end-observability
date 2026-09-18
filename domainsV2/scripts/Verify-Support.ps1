#Requires -Version 7.0
[CmdletBinding()]
param(
    [string]$SupportDirectory = (Join-Path $PSScriptRoot '..\support')
)

$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath($SupportDirectory)
if ((Get-Item -LiteralPath $root -Force).LinkType -in @('SymbolicLink', 'Junction')) {
    throw 'Support root must be a real directory, not a link.'
}
$manifest = Get-Content -LiteralPath (Join-Path $root 'bundle-manifest.json') -Raw | ConvertFrom-Json
if ($manifest.format_version -ne 1 -or @($manifest.files).Count -eq 0) {
    throw 'Unsupported or empty support bundle manifest.'
}
$seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($entry in $manifest.files) {
    $relative = [string]$entry.path
    $parts = $relative.Split('/')
    if ($relative -notmatch '^[a-zA-Z0-9_./-]+$' -or
        [IO.Path]::IsPathRooted($relative) -or
        @($parts | Where-Object { $_ -in @('', '.', '..') }).Count -ne 0 -or
        !$seen.Add($relative) -or $entry.sha256 -notmatch '^[a-fA-F0-9]{64}$') {
        throw "Invalid or duplicate support manifest entry: $relative"
    }
    $path = $root
    foreach ($part in $parts) {
        $path = Join-Path $path $part
        $item = Get-Item -LiteralPath $path -Force
        # OneDrive placeholders are reparse points too, but are not filesystem links.
        if ($item.LinkType -in @('SymbolicLink', 'Junction')) {
            throw "Linked support content is not portable: $relative"
        }
    }
    if ($item.PSIsContainer -or $item.Length -ne $entry.bytes -or
        (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $entry.sha256) {
        throw "Support bundle integrity mismatch: $relative"
    }
}
foreach ($item in Get-ChildItem -LiteralPath $root -Recurse -Force) {
    $relative = [IO.Path]::GetRelativePath($root, $item.FullName).Replace('\', '/')
    if ($relative -match '(^|/)(__pycache__|[^/]+\.egg-info)(/|$)|\.py[co]$') { continue }
    if ($item.LinkType -in @('SymbolicLink', 'Junction')) {
        throw "Linked support content is not portable: $relative"
    }
    if (!$item.PSIsContainer -and $relative -ne 'bundle-manifest.json' -and !$seen.Contains($relative)) {
        throw "Unlisted support file; review and update the bundle manifest: $relative"
    }
}
Write-Host "Support bundle: $($seen.Count) file hashes match."
