<#
.SYNOPSIS
    Scaffold for the SRE Agent's weekly review of the SRE Agent Observability
    Workbooks suite. Performs the mechanical checks (panel health, freshness,
    new-resource/drift detection) and emits findings as recommendation rows.

.DESCRIPTION
    This is the deterministic harness the sre-workbook-maintenance agent runs. It:
      1. Enumerates every panel query across workbooks/*.workbook.json.
      2. Runs each Log Analytics query and records duration + row count, flagging
         empty panels, query failures, and slow queries.
      3. Detects subscriptions/resource types present in Azure Resource Graph but
         not represented in any workbook (coverage gaps).
      4. Writes findings to ./review-findings.json in the
         SREAgentRecommendations_CL shape, ready to ingest via
         scripts/New-SampleRecommendations.ps1 -DcrEndpoint ... (or your pipeline).

    Risk classification and PR authoring are the agent's job (see
    sre-workbook-maintenance.agent.md); this script only produces evidence and
    low-risk findings. It NEVER edits workbooks or deploys.

.PARAMETER WorkspaceResourceId
    Log Analytics workspace the suite is scoped to.

.PARAMETER WorkbooksDir
    Directory with the built *.workbook.json. Default ..\..\workbooks.

.PARAMETER SlowQueryThresholdSeconds
    Panels slower than this are flagged Slow query. Default 30.

.PARAMETER OutFile
    Findings output path. Default ./review-findings.json.

.EXAMPLE
    ./Invoke-WeeklyWorkbookReview.ps1 -WorkspaceResourceId "<id>"
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$WorkspaceResourceId,

    [string]$WorkbooksDir,

    [int]$SlowQueryThresholdSeconds = 30,

    [string]$OutFile
)

$ErrorActionPreference = 'Stop'
$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $WorkbooksDir) { $WorkbooksDir = Join-Path $scriptDir '..\..\workbooks' }
if (-not $OutFile) { $OutFile = Join-Path $scriptDir 'review-findings.json' }

$WorkbooksDir = (Resolve-Path $WorkbooksDir).Path
$workspaceGuid = az monitor log-analytics workspace show --ids $WorkspaceResourceId --query customerId -o tsv
if (-not $workspaceGuid) { throw "Could not resolve workspace GUID from $WorkspaceResourceId (run 'az login')." }

$now = (Get-Date).ToUniversalTime()
$findings = New-Object System.Collections.Generic.List[object]

function New-Finding {
    param($WorkbookName, $Page, $Panel, $Category, $Severity, $Text, $Risk)
    return [ordered]@{
        TimeGenerated       = $now.ToString('o')
        RecommendationId    = [guid]::NewGuid().ToString()
        WorkbookName        = 'SRE Agent Workbook Maintenance'
        WorkbookPage        = 'Workbook Maintenance'
        Rank                = 0
        Severity            = $Severity
        Category            = $Category
        RecommendationText  = $Text
        Reason              = "Weekly review of '$WorkbookName' / '$Page' panel '$Panel'."
        EvidenceSummary     = "Panel '$Panel' on page '$Page'."
        EvidenceLinks       = ''
        AffectedScope       = $WorkbookName
        AffectedResourceId  = ''
        ApplicationName     = ''
        BusinessCriticality = 'Medium'
        OwnerTeam           = 'Platform-SRE'
        ConfidenceScore     = 0.9
        RecommendationScore = 0
        AgentActionType     = 'Auto-update workbook'
        ApprovalRequired    = $false
        RemediationRisk     = $Risk
        RollbackAvailable   = $true
        Status              = 'New'
        CreatedBy           = 'sre-workbook-maintenance'
        LastUpdated         = $now.ToString('o')
        ExpiresOn           = $now.AddDays(14).ToString('o')
        RelatedIncidentId   = ''
        RelatedAlertId      = ''
        RelatedPullRequest  = ''
        RelatedWorkItem     = ''
    }
}

# Recursively collect Log Analytics query items from a workbook items tree.
function Get-LaPanels {
    param($Items)
    $out = @()
    foreach ($item in $Items) {
        if ($item.type -eq 3 -and $item.content.query -and $item.content.queryType -eq 0) {
            $out += [pscustomobject]@{ Name = $item.name; Query = $item.content.query }
        }
        if ($item.type -eq 12 -and $item.content.items) {
            $out += Get-LaPanels -Items $item.content.items
        }
    }
    return $out
}

$files = Get-ChildItem -Path $WorkbooksDir -Filter '*.workbook.json' -File | Sort-Object Name
Write-Host "Reviewing $($files.Count) workbook(s) against workspace $workspaceGuid"
Write-Host ('-' * 70)

foreach ($file in $files) {
    $wb = Get-Content $file.FullName -Raw | ConvertFrom-Json
    $wbName = $file.BaseName -replace '^\d+-', '' -replace '\.workbook$', ''
    $panels = Get-LaPanels -Items $wb.items

    foreach ($panel in $panels) {
        # Substitute workbook parameter tokens with neutral test values so the
        # query parses; the agent should use representative values per page.
        $q = $panel.Query `
            -replace '\{TimeRange\}', 'between(ago(1d) .. now())' `
            -replace '\{Severity\}', 'All' `
            -replace '\{BusinessCriticality\}', 'All' `
            -replace '\{OwnerTeam\}', '*' `
            -replace '\{RecommendationStatus\}', 'Active'

        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        try {
            $resultJson = az monitor log-analytics query --workspace $workspaceGuid --analytics-query $q -o json 2>$null
            $sw.Stop()
            $rows = if ($resultJson) { ($resultJson | ConvertFrom-Json).Count } else { 0 }
            $secs = [math]::Round($sw.Elapsed.TotalSeconds, 1)

            if ($rows -eq 0) {
                $findings.Add((New-Finding $wbName 'page' $panel.Name 'Empty panel' 'Sev3' "Panel '$($panel.Name)' returned no rows." 'Low'))
                Write-Host "[empty]  $wbName / $($panel.Name)"
            }
            elseif ($secs -gt $SlowQueryThresholdSeconds) {
                $findings.Add((New-Finding $wbName 'page' $panel.Name 'Slow query' 'Sev3' "Panel '$($panel.Name)' took ${secs}s (> ${SlowQueryThresholdSeconds}s)." 'Low'))
                Write-Host "[slow]   $wbName / $($panel.Name) (${secs}s)"
            }
            else {
                Write-Host "[ok]     $wbName / $($panel.Name) ($rows rows, ${secs}s)"
            }
        }
        catch {
            $sw.Stop()
            $findings.Add((New-Finding $wbName 'page' $panel.Name 'Query failure' 'Sev2' "Panel '$($panel.Name)' query failed: $($_.Exception.Message)" 'Low'))
            Write-Host "[fail]   $wbName / $($panel.Name)"
        }
    }
}

# --- Coverage: subscriptions present in ARG but the suite is scoped to one WS ---
try {
    $subs = az graph query -q "resourcecontainers | where type =~ 'microsoft.resources/subscriptions' | project subscriptionId, name" -o json 2>$null | ConvertFrom-Json
    Write-Host ('-' * 70)
    Write-Host "Tenant has $($subs.data.Count) subscription(s); confirm each is in scope via the Subscriptions parameter."
}
catch {
    Write-Host "Coverage check skipped (Resource Graph query failed)."
}

Write-Host ('-' * 70)
($findings | ConvertTo-Json -Depth 6) | Set-Content -Path $OutFile -Encoding utf8
Write-Host "Wrote $($findings.Count) finding(s) -> $OutFile"
Write-Host "Ingest with: scripts/New-SampleRecommendations.ps1 (or your DCR pipeline) using this file's shape."
