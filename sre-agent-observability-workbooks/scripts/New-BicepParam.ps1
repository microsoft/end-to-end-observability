<#
.SYNOPSIS
    Assemble infra/bicep/main.bicepparam from the built workbooks/*.workbook.json
    and manifest.json. Bicep cannot read a directory of files at deploy time, so
    this bakes the serialized workbook JSON into a .bicepparam the template
    consumes via its `workbooks` array parameter.

.EXAMPLE
    ./New-BicepParam.ps1 -WorkspaceResourceId "<id>"
    az deployment group create -g <rg> --template-file ../infra/bicep/main.bicep --parameters ../infra/bicep/main.bicepparam
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$WorkspaceResourceId,

    [string]$WorkbooksDir,

    [string]$OutFile
)

$ErrorActionPreference = 'Stop'
$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $WorkbooksDir) { $WorkbooksDir = Join-Path $scriptDir '..\workbooks' }
if (-not $OutFile) { $OutFile = Join-Path $scriptDir '..\infra\bicep\main.bicepparam' }

$WorkbooksDir = (Resolve-Path $WorkbooksDir).Path
$files = Get-ChildItem -Path $WorkbooksDir -Filter '*.workbook.json' -File | Sort-Object Name

$displayNames = @{}
$manifestPath = Join-Path $WorkbooksDir 'manifest.json'
if (Test-Path $manifestPath) {
    $manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
    foreach ($w in $manifest.workbooks) { $displayNames[$w.file] = $w.displayName }
}

$items = foreach ($file in $files) {
    $slug = $file.Name -replace '^\d+-', '' -replace '\.workbook\.json$', ''
    $displayName = if ($displayNames.ContainsKey($file.Name)) { $displayNames[$file.Name] }
                   else { (Get-Culture).TextInfo.ToTitleCase(($slug -replace '-', ' ')) }
    [ordered]@{
        slug           = $slug
        displayName    = $displayName
        serializedData = (Get-Content -Path $file.FullName -Raw)
    }
}

$lines = @()
$lines += "using 'main.bicep'"
$lines += ""
$lines += "param workspaceResourceId = '$WorkspaceResourceId'"
$lines += ""
$lines += "param workbooks = " + (($items | ConvertTo-Json -Depth 32) -replace '"([^"]+)":', '$1:')
($lines -join "`n") | Set-Content -Path $OutFile -Encoding utf8
Write-Host "Wrote $OutFile ($($files.Count) workbooks)."
