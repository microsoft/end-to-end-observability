<#
.SYNOPSIS
    Quick-deploy the generated Azure Monitor workbooks for fast testing /
    workflow review. Standalone (no Bicep/Terraform required).

.DESCRIPTION
    Deploys every <domain>.workbook.json produced by generate_workbooks.py as an
    Azure Monitor workbook scoped to a Log Analytics workspace, using `az rest`
    (ARM PUT — create-or-update, idempotent).

    This is a TEST helper. The production path is Terraform
    (terraform/main.tf). To keep the two consistent, each workbook here is
    created with the SAME deterministic resource name (uuidv5 of
    "<workspaceResourceId>/<domain>" in the URL namespace), the same lowercased
    sourceId, display name ("Auto-Generated <Domain> Workbook"), category, and
    tags that Terraform would use. That means a later `terraform import` lines up
    one-to-one, and re-running either path updates in place instead of
    duplicating.

.PARAMETER WorkspaceResourceId
    Resource ID of the Log Analytics workspace the workbooks query and are
    scoped to. The workbook resources are created in this workspace's
    subscription unless -ResourceGroupName points elsewhere.

.PARAMETER ResourceGroupName
    Resource group that will contain the workbook resources. Defaults to the
    resource group parsed from -WorkspaceResourceId.

.PARAMETER SubscriptionId
    Subscription to deploy into. Defaults to the subscription parsed from
    -WorkspaceResourceId.

.PARAMETER WorkbooksDir
    Directory containing the generated *.workbook.json files. Defaults to
    ..\generator\out relative to this script.

.PARAMETER Location
    Azure region for the workbook resources. Default: eastus.

.PARAMETER Tags
    Hashtable of tags applied to every workbook (a 'domain' tag is added per
    workbook automatically). Matches the Terraform default.

.PARAMETER Cleanup
    Delete the workbooks this script would deploy (same deterministic names)
    instead of creating them. Use after testing to leave no residue.

.PARAMETER WhatIf
    Show what would be deployed/deleted without calling Azure.

.EXAMPLE
    ./Deploy-Workbooks.ps1 -WorkspaceResourceId "/subscriptions/xxxx/resourceGroups/rg-sre-lab/providers/Microsoft.OperationalInsights/workspaces/law-f7gvab4i42zky"

.EXAMPLE
    ./Deploy-Workbooks.ps1 -WorkspaceResourceId "<id>" -WhatIf

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
        workload    = 'auto-generated-workbooks'
        generatedBy = 'sre-workbook-orchestrator'
    },

    [switch]$Cleanup,

    [switch]$WhatIf
)

$ErrorActionPreference = 'Stop'
$apiVersion = '2022-04-01'

if (-not $WorkbooksDir) {
    $scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
    $WorkbooksDir = Join-Path $scriptDir '..\generator\out'
}

# --- Deterministic name: RFC 4122 UUIDv5, URL namespace. Matches Terraform's
#     uuidv5("url", "<workspaceResourceId>/<domain>"). ----------------------
function Get-UuidV5 {
    param(
        [Parameter(Mandatory)][string]$Name,
        # URL namespace per RFC 4122 appendix C.
        [string]$NamespaceGuid = '6ba7b811-9dad-11d1-80b4-00c04fd430c8'
    )
    $ns = [System.Guid]::Parse($NamespaceGuid).ToByteArray()
    # .NET stores the first three fields little-endian; convert to big-endian
    # network order so the hash matches RFC / Terraform.
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
    # Version 5 (bits 12-15 of time_hi_and_version) and RFC 4122 variant.
    $guidBytes[6] = ($guidBytes[6] -band 0x0F) -bor 0x50
    $guidBytes[8] = ($guidBytes[8] -band 0x3F) -bor 0x80

    # Convert big-endian bytes back to the little-endian layout Guid expects.
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
$files = Get-ChildItem -Path $WorkbooksDir -Filter '*.workbook.json' -File
if (-not $files) {
    throw "No *.workbook.json files found in '$WorkbooksDir'. Run generator/generate_workbooks.py first."
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
    $domain = $file.Name -replace '\.workbook\.json$', ''
    $displayName = "Auto-Generated $((Get-Culture).TextInfo.ToTitleCase($domain)) Workbook"
    $name = Get-UuidV5 -Name "$WorkspaceResourceId/$domain"
    $armId = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroupName/providers/microsoft.insights/workbooks/$name"
    $url = "https://management.azure.com$($armId)?api-version=$apiVersion"

    if ($Cleanup) {
        if ($WhatIf) {
            Write-Host "[what-if] DELETE $domain -> $name"
            continue
        }
        Write-Host "Deleting $domain ($name) ..."
        az rest --method delete --url $url --only-show-errors 2>$null | Out-Null
        $results += [pscustomobject]@{ Domain = $domain; Name = $name; Action = 'deleted' }
        continue
    }

    if ($WhatIf) {
        Write-Host "[what-if] PUT $domain -> $name  ('$displayName')"
        $results += [pscustomobject]@{ Domain = $domain; Name = $name; Action = 'would-deploy' }
        continue
    }

    $serialized = Get-Content -Path $file.FullName -Raw
    $bodyTags = $Tags.Clone()
    $bodyTags['domain'] = $domain

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
        ($body | ConvertTo-Json -Depth 12 -Compress) | Set-Content -Path $tmp -Encoding utf8 -NoNewline
        Write-Host "Deploying $domain ($name) ..."
        az rest --method put --url $url --headers "Content-Type=application/json" --body "@$tmp" --only-show-errors | Out-Null
        $portal = "https://portal.azure.com/#@/resource$armId/workbook"
        $results += [pscustomobject]@{ Domain = $domain; Name = $name; Action = 'deployed'; Portal = $portal }
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
    Write-Host "Done. Open Azure Monitor > Workbooks (gallery 'Auto-Generated *') to review."
}
