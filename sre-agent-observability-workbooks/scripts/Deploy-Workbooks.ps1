<#
.SYNOPSIS
    Deploy the SRE Agent Observability Workbooks suite to Azure Monitor for fast
    testing / workflow review. Standalone (no Bicep/Terraform required).

.DESCRIPTION
    Deploys every NN-<slug>.workbook.json built by builder/build_workbooks.py as
    an Azure Monitor workbook scoped to a Log Analytics workspace, using `az rest`
    (ARM PUT — create-or-update, idempotent).

    Friendly display names ("SRE Agent Command Center", ...) are read from the
    builder's manifest.json. Each workbook is created with the SAME deterministic
    resource name (uuidv5 of "<workspaceResourceId>/<slug>" in the URL namespace),
    lowercased sourceId, category, and tags that the Terraform path uses — so a
    later `terraform import` lines up one-to-one and re-running either path updates
    in place instead of duplicating.

.PARAMETER WorkspaceResourceId
    Resource ID of the Log Analytics workspace the workbooks query and are scoped
    to.

.PARAMETER ResourceGroupName
    Resource group for the workbook resources. Defaults to the RG parsed from
    -WorkspaceResourceId.

.PARAMETER SubscriptionId
    Subscription to deploy into. Defaults to the sub parsed from
    -WorkspaceResourceId.

.PARAMETER WorkbooksDir
    Directory containing the built *.workbook.json files. Defaults to ..\workbooks.

.PARAMETER Location
    Azure region for the workbook resources. Default: eastus.

.PARAMETER Cleanup
    Delete the workbooks this script would deploy (same deterministic names).

.PARAMETER WhatIf
    Show what would be deployed/deleted without calling Azure.

.EXAMPLE
    ./Deploy-Workbooks.ps1 -WorkspaceResourceId "/subscriptions/xxxx/resourceGroups/rg-observability/providers/Microsoft.OperationalInsights/workspaces/law-observability"

.EXAMPLE
    ./Deploy-Workbooks.ps1 -WorkspaceResourceId "<id>" -Cleanup
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$WorkspaceResourceId,

    [string]$ResourceGroupName,

    [string]$SubscriptionId,

    [string]$WorkbooksDir,

    [string]$Location = 'eastus',

    [hashtable]$Tags = @{
        workload  = 'sre-agent-observability-workbooks'
        managedBy = 'sre-agent'
        suite     = 'SRE Agent Observability Workbooks'
    },

    [switch]$Cleanup,

    [switch]$WhatIf
)

$ErrorActionPreference = 'Stop'
$apiVersion = '2022-04-01'

$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $WorkbooksDir) {
    $WorkbooksDir = Join-Path $scriptDir '..\workbooks'
}

# --- Deterministic name: RFC 4122 UUIDv5, URL namespace. Matches Terraform's
#     uuidv5("url", "<workspaceResourceId>/<slug>"). --------------------------
function Get-UuidV5 {
    param(
        [Parameter(Mandatory)][string]$Name,
        [string]$NamespaceGuid = '6ba7b811-9dad-11d1-80b4-00c04fd430c8'
    )
    $ns = [System.Guid]::Parse($NamespaceGuid).ToByteArray()
    [Array]::Reverse($ns, 0, 4)
    [Array]::Reverse($ns, 4, 2)
    [Array]::Reverse($ns, 6, 2)

    $nameBytes = [System.Text.Encoding]::UTF8.GetBytes($Name)
    $buffer = New-Object byte[] ($ns.Length + $nameBytes.Length)
    [Array]::Copy($ns, 0, $buffer, 0, $ns.Length)
    [Array]::Copy($nameBytes, 0, $buffer, $ns.Length, $nameBytes.Length)

    $sha1 = [System.Security.Cryptography.SHA1]::Create()
    try { $hash = $sha1.ComputeHash($buffer) } finally { $sha1.Dispose() }

    $guidBytes = New-Object byte[] 16
    [Array]::Copy($hash, 0, $guidBytes, 0, 16)
    $guidBytes[6] = ($guidBytes[6] -band 0x0F) -bor 0x50
    $guidBytes[8] = ($guidBytes[8] -band 0x3F) -bor 0x80

    [Array]::Reverse($guidBytes, 0, 4)
    [Array]::Reverse($guidBytes, 4, 2)
    [Array]::Reverse($guidBytes, 6, 2)
    return ([System.Guid]::new($guidBytes)).ToString()
}

function Get-FromResourceId {
    param([string]$Id, [string]$Segment)
    $parts = $Id.Trim('/').Split('/')
    for ($i = 0; $i -lt $parts.Length - 1; $i++) {
        if ($parts[$i] -ieq $Segment) { return $parts[$i + 1] }
    }
    return $null
}

# --- Resolve target scope ----------------------------------------------------
if (-not $SubscriptionId) {
    $SubscriptionId = Get-FromResourceId -Id $WorkspaceResourceId -Segment 'subscriptions'
}
if (-not $ResourceGroupName) {
    $ResourceGroupName = Get-FromResourceId -Id $WorkspaceResourceId -Segment 'resourceGroups'
}
if (-not $SubscriptionId -or -not $ResourceGroupName) {
    throw "Could not parse subscription/resource group from -WorkspaceResourceId. Pass -SubscriptionId / -ResourceGroupName explicitly."
}

$WorkbooksDir = (Resolve-Path $WorkbooksDir).Path
$files = Get-ChildItem -Path $WorkbooksDir -Filter '*.workbook.json' -File | Sort-Object Name
if (-not $files) {
    throw "No *.workbook.json files found in '$WorkbooksDir'. Run 'python builder/build_workbooks.py' first."
}

# --- Display-name lookup from manifest.json ----------------------------------
$displayNames = @{}
$manifestPath = Join-Path $WorkbooksDir 'manifest.json'
if (Test-Path $manifestPath) {
    $manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
    foreach ($w in $manifest.workbooks) { $displayNames[$w.file] = $w.displayName }
}

Write-Host "Subscription : $SubscriptionId"
Write-Host "ResourceGroup: $ResourceGroupName"
Write-Host "Workspace    : $WorkspaceResourceId"
Write-Host "Workbooks    : $($files.Count) file(s) in $WorkbooksDir"
Write-Host "Mode         : $(if ($Cleanup) { 'CLEANUP (delete)' } elseif ($WhatIf) { 'WHAT-IF' } else { 'DEPLOY' })"
Write-Host ('-' * 70)

if (-not $WhatIf) {
    az account set --subscription $SubscriptionId | Out-Null
}

$sourceId = $WorkspaceResourceId.ToLower()
$results = @()

foreach ($file in $files) {
    $slug = $file.Name -replace '^\d+-', '' -replace '\.workbook\.json$', ''
    $displayName = if ($displayNames.ContainsKey($file.Name)) {
        $displayNames[$file.Name]
    } else {
        (Get-Culture).TextInfo.ToTitleCase(($slug -replace '-', ' '))
    }
    $name = Get-UuidV5 -Name "$WorkspaceResourceId/$slug"
    $armId = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroupName/providers/microsoft.insights/workbooks/$name"
    $url = "https://management.azure.com$($armId)?api-version=$apiVersion"

    if ($Cleanup) {
        if ($WhatIf) {
            Write-Host "[what-if] DELETE $slug -> $name"
            continue
        }
        Write-Host "Deleting $slug ($name) ..."
        az rest --method delete --url $url --only-show-errors 2>$null | Out-Null
        $results += [pscustomobject]@{ Slug = $slug; Name = $name; Action = 'deleted' }
        continue
    }

    if ($WhatIf) {
        Write-Host "[what-if] PUT $slug -> $name  ('$displayName')"
        $results += [pscustomobject]@{ Slug = $slug; Name = $name; Action = 'would-deploy' }
        continue
    }

    $serialized = Get-Content -Path $file.FullName -Raw

    # Stamp the target workspace into the artifact so Log Analytics tiles bind on
    # open (the builder ships a generic, tenant-agnostic artifact):
    #   1. fallbackResourceIds placeholder -> the real workspace resource ID
    #   2. the required LogAnalyticsWorkspaceId picker gets a default value so the
    #      {LogAnalyticsWorkspaceId} cross-component bindings resolve immediately.
    $placeholderWs = '/subscriptions/<sub-id>/resourceGroups/<rg>/providers/Microsoft.OperationalInsights/workspaces/<law>'
    $serialized = $serialized.Replace($placeholderWs, $WorkspaceResourceId)
    $serialized = $serialized -replace '("id":\s*"param-loganalyticsworkspaceid",)', ('$1' + "`n            `"value`": `"$WorkspaceResourceId`",")

    $bodyTags = $Tags.Clone()
    $bodyTags['slug'] = $slug

    $body = [ordered]@{
        location   = $Location
        kind       = 'shared'
        tags       = $bodyTags
        properties = [ordered]@{
            displayName    = $displayName
            serializedData = $serialized
            category       = 'workbook'
            sourceId       = $sourceId
            version        = 'Notebook/1.0'
        }
    }

    $tmp = New-TemporaryFile
    try {
        ($body | ConvertTo-Json -Depth 16 -Compress) | Set-Content -Path $tmp -Encoding utf8 -NoNewline
        Write-Host "Deploying $slug ($name) ..."
        az rest --method put --url $url --headers "Content-Type=application/json" --body "@$tmp" --only-show-errors | Out-Null
        $portal = "https://portal.azure.com/#@/resource$armId/workbook"
        $results += [pscustomobject]@{ Slug = $slug; Name = $name; Action = 'deployed'; Portal = $portal }
    }
    finally {
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    }
}

Write-Host ('-' * 70)
if ($results) {
    $results | Format-Table -AutoSize | Out-String | Write-Host
}
if (-not $Cleanup -and -not $WhatIf) {
    Write-Host "Done. Open Azure Monitor > Workbooks (gallery; suite tag 'SRE Agent Observability Workbooks') to review."
}
