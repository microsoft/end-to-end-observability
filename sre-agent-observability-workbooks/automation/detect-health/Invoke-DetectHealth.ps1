<#
.SYNOPSIS
    Runnable implementation of the `sre-detect-health` agent task: turn real
    platform signals into scored, evidence-backed rows in SREAgentRecommendations_CL.

.DESCRIPTION
    This is the Detect stage of the SRE Agent loop made executable. It is
    RECOMMEND-ONLY -- it queries Azure (Resource Graph, Policy, Log Analytics /
    Application Insights, fired alerts, recent changes) across the managed
    subscriptions, scores each signal with the documented scoring model
    (docs/scoring-model.md), and upserts one deterministic row per signal to the
    central SREAgentRecommendations_CL table via the Logs Ingestion API.

    Every recommendation is mapped to the workbook page that owns it
    (workbooks/manifest.json), so the suite's Top 10 panels light up with REAL
    agent findings instead of synthetic seed data. The task never mutates a
    resource; risk-bearing proposals are written with ApprovalRequired = true.

    RecommendationId is a stable hash of the signal key, so re-running upserts the
    same logical finding (the Top 10 query dedupes with arg_max(LastUpdated)).

.PARAMETER WorkspaceResourceId
    Resource ID of the Log Analytics workspace holding SREAgentRecommendations_CL.

.PARAMETER DcrEndpoint
    Data Collection Endpoint logs-ingestion URI
    (https://<dce>.<region>.ingest.monitor.azure.com).

.PARAMETER DcrImmutableId
    Immutable ID of the Data Collection Rule (dcr-xxxxxxxx...).

.PARAMETER StreamName
    DCR stream targeting the custom table. Default Custom-SREAgentRecommendations_CL.

.PARAMETER Subscriptions
    Subscription IDs to analyze. Default: the workspace subscription.

.PARAMETER WhatIf
    Build and print the rows without ingesting.

.EXAMPLE
    ./Invoke-DetectHealth.ps1 `
        -WorkspaceResourceId "/subscriptions/<sub>/resourceGroups/rg-sre-lab/providers/Microsoft.OperationalInsights/workspaces/law-..." `
        -DcrEndpoint "https://dce-sre-lab-xxxx.eastus2-1.ingest.monitor.azure.com" `
        -DcrImmutableId "dcr-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$WorkspaceResourceId,
    [string]$DcrEndpoint,
    [string]$DcrImmutableId,
    [string]$StreamName = 'Custom-SREAgentRecommendations_CL',
    [string[]]$Subscriptions,
    [switch]$WhatIf
)

$ErrorActionPreference = 'Stop'

# Workbook display names / pages (single-quoted: must match workbooks/manifest.json).
$WB = @{
    CommandCenter = 'SRE Agent Command Center'
    TenantHealth  = 'Tenant Health Model'
    IncidentRca   = 'Incident Triage & RCA'
    Automation    = 'Automation & Remediation'
    Operations    = 'Operations Command Center'
    AppReliab     = 'Application Reliability'
    Governance    = 'Enterprise Architecture & Governance'
    QualityCost   = 'Observability Quality & Cost'
    Security      = 'Security & Identity Observability'
    Maintenance   = 'SRE Agent Workbook Maintenance'
}
$PG = @{
    CommandCenter = 'Command Center'
    TenantHealth  = 'Tenant Health Model'
    IncidentRca   = 'Incident Triage & RCA'
    Automation    = 'Automation & Remediation'
    Operations    = 'Operations Command Center'
    AppReliab     = 'Application Reliability'
    Governance    = 'Enterprise Architecture & Governance'
    QualityCost   = 'Observability Quality & Cost'
    Security      = 'Security & Identity Observability'
    Maintenance   = 'Workbook Maintenance'
}

function Get-FromResourceId {
    param([string]$Id, [string]$Segment)
    $parts = $Id.Trim('/').Split('/')
    for ($i = 0; $i -lt $parts.Length - 1; $i++) {
        if ($parts[$i] -ieq $Segment) { return $parts[$i + 1] }
    }
    return $null
}

$subId = Get-FromResourceId -Id $WorkspaceResourceId -Segment 'subscriptions'
$wsName = Get-FromResourceId -Id $WorkspaceResourceId -Segment 'workspaces'
$wsRg = Get-FromResourceId -Id $WorkspaceResourceId -Segment 'resourceGroups'
if (-not $Subscriptions) { $Subscriptions = @($subId) }
$subFilter = ($Subscriptions | ForEach-Object { "'$_'" }) -join ','
$wsGuid = az monitor log-analytics workspace show -g $wsRg -n $wsName --subscription $subId --query customerId -o tsv
$evidenceLink = "https://portal.azure.com/#@/resource$WorkspaceResourceId/logs"

# --- Stable RecommendationId from a signal key (MD5 -> GUID) -------------------
function Get-StableGuid {
    param([string]$Key)
    $md5 = [System.Security.Cryptography.MD5]::Create()
    try { $bytes = $md5.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Key)) }
    finally { $md5.Dispose() }
    return ([System.Guid]::new($bytes)).ToString()
}

# --- Scoring model (docs/scoring-model.md) ------------------------------------
$sevBand  = @{ 'Sev0' = 25; 'Sev1' = 20; 'Sev2' = 12; 'Sev3' = 6; 'Sev4' = 2 }
$critBand = @{ 'Mission critical' = 25; 'High' = 18; 'Medium' = 10; 'Low' = 4 }
$riskBand = @{ 'High' = 20; 'Medium' = 10; 'Low' = 3 }

function Get-Score {
    param($Severity, $Criticality, [double]$UserImpact, [double]$Confidence,
          [double]$BlastRadius, [double]$ComplianceRisk, [double]$AutomationReadiness, $RemediationRisk)
    $score = $critBand[$Criticality] + $sevBand[$Severity] + $UserImpact +
             ([math]::Min(15, $Confidence * 15)) + 10 + $BlastRadius +
             $ComplianceRisk + $AutomationReadiness - $riskBand[$RemediationRisk]
    return [math]::Round([math]::Max(0, $score), 2)
}

$now = (Get-Date).ToUniversalTime()
$signals = New-Object System.Collections.Generic.List[object]

function Add-Signal {
    param(
        [string]$Key, [string]$WorkbookName, [string]$WorkbookPage, [string]$Severity,
        [string]$Category, [string]$Text, [string]$Reason, [string]$Evidence,
        [string]$Scope, [string]$ResourceId = '', [string]$App = '',
        [string]$Criticality = 'Medium', [string]$OwnerTeam = 'Platform-SRE',
        [double]$Confidence = 0.8, [double]$UserImpact = 0, [double]$BlastRadius = 5,
        [double]$ComplianceRisk = 0, [double]$AutomationReadiness = 0,
        [string]$ActionType = 'Recommend', [bool]$ApprovalRequired = $false,
        [string]$RemediationRisk = 'Low', [bool]$RollbackAvailable = $true,
        [string]$Status = 'New', [string]$WorkItem = '',
        [string]$IncidentId = '', [string]$AlertId = ''
    )
    $score = Get-Score -Severity $Severity -Criticality $Criticality -UserImpact $UserImpact `
        -Confidence $Confidence -BlastRadius $BlastRadius -ComplianceRisk $ComplianceRisk `
        -AutomationReadiness $AutomationReadiness -RemediationRisk $RemediationRisk
    $signals.Add([ordered]@{
        TimeGenerated       = $now.ToString('o')
        RecommendationId    = Get-StableGuid -Key "detect-health::$Key"
        WorkbookName        = $WorkbookName
        WorkbookPage        = $WorkbookPage
        Rank                = 0
        Severity            = $Severity
        Category            = $Category
        RecommendationText  = $Text
        Reason              = $Reason
        EvidenceSummary     = $Evidence
        EvidenceLinks       = $evidenceLink
        AffectedScope       = $Scope
        AffectedResourceId  = $ResourceId
        ApplicationName     = $App
        BusinessCriticality = $Criticality
        OwnerTeam           = $OwnerTeam
        ConfidenceScore     = [math]::Round($Confidence, 2)
        RecommendationScore = $score
        AgentActionType     = $ActionType
        ApprovalRequired    = $ApprovalRequired
        RemediationRisk     = $RemediationRisk
        RollbackAvailable   = $RollbackAvailable
        Status              = $Status
        CreatedBy           = 'sre-agent/detect-health'
        LastUpdated         = $now.ToString('o')
        ExpiresOn           = $now.AddDays(14).ToString('o')
        RelatedIncidentId   = $IncidentId
        RelatedAlertId      = $AlertId
        RelatedPullRequest  = ''
        RelatedWorkItem     = $WorkItem
    }) | Out-Null
}

# Query helpers are resilient by design: a failed or empty query returns @() so a
# single bad signal source never aborts the whole detection pass (the agent skill
# treats missing data as a signal, not a crash).
function Invoke-Arg {
    param([string]$Query)
    try {
        $json = az graph query -q $Query --query "data" -o json 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $json) { return @() }
        return ($json | ConvertFrom-Json)
    } catch { return @() }
}
function Invoke-La {
    param([string]$Kql)
    try {
        $json = az monitor log-analytics query -w $wsGuid --analytics-query $Kql -o json 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $json) { return @() }
        return ($json | ConvertFrom-Json)
    } catch { return @() }
}

Write-Host "Detect-Health: analyzing $($Subscriptions.Count) subscription(s) -> $wsName" -ForegroundColor Cyan

# === Signal 1: Policy non-compliance by definition ============================
$qNc = "policyresources | where subscriptionId in ($subFilter) and type == 'microsoft.policyinsights/policystates' | extend cs=tostring(properties.complianceState), pdn=tostring(properties.policyDefinitionName), rt=tostring(properties.resourceType) | where cs == 'NonCompliant' | summarize n=count() by pdn, rt | order by n desc"
$nc = Invoke-Arg $qNc
$ncByDef = $nc | Group-Object pdn | ForEach-Object {
    [pscustomobject]@{ pdn = $_.Name; n = (($_.Group) | Measure-Object -Property n -Sum).Sum; rt = ($_.Group[0].rt) }
} | Sort-Object n -Descending

$tagSig = $ncByDef | Where-Object { $_.pdn -like '*owner-tag*' -or $_.pdn -like '*support-owner*' } | Select-Object -First 1
if ($tagSig) {
    Add-Signal -Key 'policy-owner-tag' -WorkbookName $WB.Governance -WorkbookPage $PG.Governance `
        -Severity 'Sev2' -Category 'Governance' `
        -Text ("Apply the required support-owner tag to {0} untagged resources" -f $tagSig.n) `
        -Reason ("Azure Policy '{0}' reports {1} non-compliant resources with no support-owner tag, breaking ownership routing for incidents." -f $tagSig.pdn, $tagSig.n) `
        -Evidence ("{0} resources non-compliant with {1}" -f $tagSig.n, $tagSig.pdn) `
        -Scope "/subscriptions/$subId" -Criticality 'High' -OwnerTeam 'Platform-SRE' `
        -Confidence 0.95 -BlastRadius 12 -ComplianceRisk 12 -AutomationReadiness 8 `
        -ActionType 'Notify owner' -RemediationRisk 'Low' -WorkItem 'AB#21'
}
$roleSig = $ncByDef | Where-Object { $_.rt -like '*roleDefinitions*' } | Select-Object -First 1
if ($roleSig) {
    Add-Signal -Key 'policy-roledefs' -WorkbookName $WB.Security -WorkbookPage $PG.Security `
        -Severity 'Sev1' -Category 'Security' `
        -Text ("Review {0} custom role definitions flagged by policy" -f $roleSig.n) `
        -Reason ("Policy reports {0} non-compliant Microsoft.Authorization/roleDefinitions: potential over-broad custom roles or missing constraints." -f $roleSig.n) `
        -Evidence ("{0} non-compliant role definitions in subscription {1}" -f $roleSig.n, $subId) `
        -Scope "/subscriptions/$subId" -Criticality 'High' -OwnerTeam 'App-Identity' `
        -Confidence 0.85 -BlastRadius 10 -ComplianceRisk 14 -AutomationReadiness 3 `
        -ActionType 'Request approval' -ApprovalRequired $true -RemediationRisk 'Medium' -RollbackAvailable $true
}
$diagSig = $ncByDef | Where-Object { $_.pdn -like '*diag*' } | Select-Object -First 1
if ($diagSig) {
    Add-Signal -Key 'policy-diagnostics' -WorkbookName $WB.QualityCost -WorkbookPage $PG.QualityCost `
        -Severity 'Sev2' -Category 'Telemetry coverage' `
        -Text ("Enable diagnostic settings to Log Analytics on {0} resource(s)" -f $diagSig.n) `
        -Reason ("Policy '{0}' shows {1} resources without diagnostic settings routed to the workspace; telemetry gaps blind the agent." -f $diagSig.pdn, $diagSig.n) `
        -Evidence ("{0} resources missing diagnostic settings ({1})" -f $diagSig.n, $diagSig.pdn) `
        -Scope "/subscriptions/$subId" -Criticality 'Medium' -OwnerTeam 'Platform-SRE' `
        -Confidence 0.9 -BlastRadius 6 -ComplianceRisk 8 -AutomationReadiness 9 `
        -ActionType 'Auto-remediate' -ApprovalRequired $true -RemediationRisk 'Low'
}
$acrSum = ($nc | Where-Object { $_.rt -like '*ContainerRegistry*' } | Measure-Object -Property n -Sum).Sum
if ($acrSum -gt 0) {
    Add-Signal -Key 'policy-acr' -WorkbookName $WB.Security -WorkbookPage $PG.Security `
        -Severity 'Sev2' -Category 'Security' `
        -Text ("Harden {0} container registry policy finding(s) (image scanning / network)" -f $acrSum) `
        -Reason 'ACR resources are non-compliant; aligns with the ACR image-vulnerability monitoring sub-agent (Story #56).' `
        -Evidence ("{0} non-compliant Microsoft.ContainerRegistry findings" -f $acrSum) `
        -Scope "/subscriptions/$subId" -Criticality 'High' -OwnerTeam 'Platform-SRE' `
        -Confidence 0.8 -BlastRadius 7 -ComplianceRisk 11 -AutomationReadiness 5 `
        -ActionType 'Open ticket' -RemediationRisk 'Low' -WorkItem 'AB#56'
}

# === Signal 2: Overall compliance posture (Command Center + Tenant Health) ====
$qAll = "policyresources | where subscriptionId in ($subFilter) and type == 'microsoft.policyinsights/policystates' | extend cs=tostring(properties.complianceState) | summarize n=count() by cs"
$allStates = Invoke-Arg $qAll
$compliant = ($allStates | Where-Object { $_.cs -eq 'Compliant' } | Measure-Object -Property n -Sum).Sum
$noncomp = ($allStates | Where-Object { $_.cs -eq 'NonCompliant' } | Measure-Object -Property n -Sum).Sum
$totalStates = $compliant + $noncomp
if ($totalStates -gt 0) {
    $pct = [math]::Round(100.0 * $compliant / $totalStates, 1)
    $ncPct = [math]::Round(100.0 - $pct, 1)
    $sev = if ($pct -lt 60) { 'Sev1' } elseif ($pct -lt 80) { 'Sev2' } else { 'Sev3' }
    Add-Signal -Key 'compliance-posture' -WorkbookName $WB.CommandCenter -WorkbookPage $PG.CommandCenter `
        -Severity $sev -Category 'Governance' `
        -Text ("Tenant policy compliance is {0} percent; close the top non-compliant initiatives" -f $pct) `
        -Reason ("Across managed scope: {0} compliant vs {1} non-compliant policy states ({2} percent)." -f $compliant, $noncomp, $pct) `
        -Evidence ("{0} non-compliant / {1} evaluated policy states ({2} percent compliant)" -f $noncomp, $totalStates, $pct) `
        -Scope "/subscriptions/$subId" -Criticality 'High' -OwnerTeam 'Platform-SRE' `
        -Confidence 0.97 -BlastRadius 13 -ComplianceRisk 13 -AutomationReadiness 6 `
        -ActionType 'Recommend' -RemediationRisk 'Low'
    Add-Signal -Key 'tenant-health-compliance' -WorkbookName $WB.TenantHealth -WorkbookPage $PG.TenantHealth `
        -Severity $sev -Category 'Governance' `
        -Text ("Governance health degraded: {0} percent of policy evaluations non-compliant" -f $ncPct) `
        -Reason 'The tenant health model rolls policy compliance into the governance pillar; current non-compliance lowers the overall score.' `
        -Evidence ("{0} percent compliant across {1} policy states" -f $pct, $totalStates) `
        -Scope "/subscriptions/$subId" -Criticality 'High' -OwnerTeam 'Platform-SRE' `
        -Confidence 0.95 -BlastRadius 12 -ComplianceRisk 12 -AutomationReadiness 5 `
        -ActionType 'Inform' -RemediationRisk 'Low'
}

# === Signal 3: Application reliability (Grubify latency via App Insights) ======
$lat = Invoke-La 'AppRequests | where TimeGenerated > ago(24h) | summarize total=count(), failed=countif(Success==false), p95=round(percentile(DurationMs,95),0), p99=round(percentile(DurationMs,99),0)'
if ($lat -and $lat[0].total -gt 0) {
    $p95 = [int]$lat[0].p95; $p99 = [int]$lat[0].p99; $failed = [int]$lat[0].failed; $total = [int]$lat[0].total
    $failPct = if ($total -gt 0) { [math]::Round(100.0 * $failed / $total, 2) } else { 0 }
    $sev = if ($failPct -gt 1 -or $p95 -gt 3000) { 'Sev1' } elseif ($p95 -gt 800) { 'Sev3' } else { 'Sev4' }
    Add-Signal -Key 'app-grubify-latency' -WorkbookName $WB.AppReliab -WorkbookPage $PG.AppReliab `
        -Severity $sev -Category 'Reliability' `
        -Text ("Investigate Grubify API p95 latency ({0} ms) against the latency SLO" -f $p95) `
        -Reason ("Over 24h: {0} requests, {1} percent failed, p95={2} ms, p99={3} ms. Elevated tail latency erodes the error budget." -f $total, $failPct, $p95, $p99) `
        -Evidence ("{0} req/24h, p95={1}ms, p99={2}ms, fail={3} percent" -f $total, $p95, $p99, $failPct) `
        -Scope "/subscriptions/$subId/resourceGroups/$wsRg" -App 'grubify-api' `
        -Criticality 'High' -OwnerTeam 'App-Payments' `
        -Confidence 0.8 -UserImpact 8 -BlastRadius 5 -ComplianceRisk 0 -AutomationReadiness 2 `
        -ActionType 'Investigate' -RemediationRisk 'Low'
}

# === Signal 4: Fired alerts (Operations Command Center) =======================
$qAlerts = "alertsmanagementresources | where subscriptionId in ($subFilter) | extend mc=tostring(properties.essentials.monitorCondition) | where mc == 'Fired' | summarize n=count()"
$alerts = Invoke-Arg $qAlerts
$firedTotal = if ($alerts -and $alerts.Count -gt 0) { [int]$alerts[0].n } else { 0 }
if ($firedTotal -gt 0) {
    Add-Signal -Key 'ops-fired-alerts' -WorkbookName $WB.Operations -WorkbookPage $PG.Operations `
        -Severity 'Sev2' -Category 'Reliability' `
        -Text ("Triage {0} fired alert(s) awaiting acknowledgement" -f $firedTotal) `
        -Reason 'Active fired alerts across managed subscriptions require NOC/SRE triage.' `
        -Evidence ("{0} alerts in Fired state" -f $firedTotal) `
        -Scope "/subscriptions/$subId" -Criticality 'High' -OwnerTeam 'Platform-SRE' `
        -Confidence 0.9 -BlastRadius 8 -AutomationReadiness 4 -ActionType 'Open ticket' -RemediationRisk 'Low'
}
else {
    Add-Signal -Key 'ops-alert-coverage' -WorkbookName $WB.Operations -WorkbookPage $PG.Operations `
        -Severity 'Sev3' -Category 'Telemetry coverage' `
        -Text 'No fired alerts detected; verify alert-rule coverage is not a blind spot' `
        -Reason 'Zero fired alerts can mean healthy OR missing alert rules. Confirm AMBA alert coverage across critical resource types (Unknown is not Healthy).' `
        -Evidence ("0 alerts in Fired state across {0} subscription(s)" -f $Subscriptions.Count) `
        -Scope "/subscriptions/$subId" -Criticality 'Medium' -OwnerTeam 'Platform-SRE' `
        -Confidence 0.6 -BlastRadius 4 -ComplianceRisk 4 -AutomationReadiness 3 -ActionType 'Inform' -RemediationRisk 'Low'
}

# === Signal 5: Incident RCA correlation (recent changes) ======================
$qChg = "resourcechanges | where subscriptionId in ($subFilter) | extend ct=todatetime(properties.changeAttributes.timestamp) | where ct > ago(24h) | summarize n=count()"
$changes = Invoke-Arg $qChg
$chgN = if ($changes -and $changes.Count -gt 0) { [int]$changes[0].n } else { 0 }
Add-Signal -Key 'rca-change-correlation' -WorkbookName $WB.IncidentRca -WorkbookPage $PG.IncidentRca `
    -Severity 'Sev3' -Category 'Root cause' `
    -Text ("Correlate {0} resource change(s) in the last 24h with active signals" -f $chgN) `
    -Reason 'Recent configuration/deployment changes are the leading root-cause candidate; keep the change feed correlated with alerts and health.' `
    -Evidence ("{0} resource changes in last 24h (change to cause correlation)" -f $chgN) `
    -Scope "/subscriptions/$subId" -Criticality 'Medium' -OwnerTeam 'Platform-SRE' `
    -Confidence 0.7 -BlastRadius 5 -AutomationReadiness 2 -ActionType 'Investigate' -RemediationRisk 'Low'

# === Signal 6: Automation backlog (Automation & Remediation) ==================
$act = Invoke-La 'SREAgentActions_CL | where TimeGenerated > ago(7d) | summarize n=count() by Outcome'
if ($act -and $act.Count -gt 0) {
    $blocked = [int](($act | Where-Object { $_.Outcome -eq 'Blocked' } | Measure-Object -Property n -Sum).Sum)
    $failedA = [int](($act | Where-Object { $_.Outcome -eq 'Failed' } | Measure-Object -Property n -Sum).Sum)
    $totalA = [int](($act | Measure-Object -Property n -Sum).Sum)
    if ($blocked + $failedA -gt 0) {
        Add-Signal -Key 'automation-backlog' -WorkbookName $WB.Automation -WorkbookPage $PG.Automation `
            -Severity 'Sev3' -Category 'Post-incident' `
            -Text ("Clear automation backlog: {0} blocked, {1} failed action(s)" -f $blocked, $failedA) `
            -Reason 'Blocked/failed automation runs in the last 7d need owner attention or guardrail review before re-run.' `
            -Evidence ("{0} blocked + {1} failed of {2} actions (7d)" -f $blocked, $failedA, $totalA) `
            -Scope "/subscriptions/$subId" -Criticality 'Medium' -OwnerTeam 'Platform-SRE' `
            -Confidence 0.85 -BlastRadius 5 -AutomationReadiness 7 -ActionType 'Escalate' -RemediationRisk 'Low'
    }
}

# === Signal 7: Workbook maintenance (self-review) =============================
Add-Signal -Key 'workbook-self-review' -WorkbookName $WB.Maintenance -WorkbookPage $PG.Maintenance `
    -Severity 'Sev4' -Category 'Telemetry coverage' `
    -Text 'Run weekly workbook self-review (freshness, broken queries, placeholder tables)' `
    -Reason 'Detect-Health refreshed recommendations now; the weekly maintenance task should confirm every page renders and flag placeholder-only panels.' `
    -Evidence ("Detect-Health run at {0}; {1} signals emitted this pass" -f $now.ToString('u'), ($signals.Count + 1)) `
    -Scope $WorkspaceResourceId -Criticality 'Low' -OwnerTeam 'Platform-SRE' `
    -Confidence 0.9 -BlastRadius 2 -AutomationReadiness 8 -ActionType 'Auto-update workbook' -RemediationRisk 'Low'

# --- Emit / ingest ------------------------------------------------------------
$rows = $signals.ToArray()
$json = ConvertTo-Json -InputObject @($rows) -Depth 6

$pageCount = ($rows | ForEach-Object { $_.WorkbookPage } | Select-Object -Unique).Count
Write-Host ("Built {0} real recommendation(s) across {1} page(s):" -f $rows.Count, $pageCount) -ForegroundColor Green
$rows | ForEach-Object { "  [{0,-5}] {1,-38} score={2,-6} {3}" -f $_.Severity, $_.WorkbookPage, $_.RecommendationScore, $_.RecommendationText } | Write-Host

if ($WhatIf) { Write-Host 'WhatIf: not ingesting.' -ForegroundColor Yellow; return }

if ($DcrEndpoint -and $DcrImmutableId) {
    $token = az account get-access-token --resource 'https://monitor.azure.com' --query accessToken -o tsv
    $uri = "$($DcrEndpoint.TrimEnd('/'))/dataCollectionRules/$DcrImmutableId/streams/$StreamName`?api-version=2023-01-01"
    Invoke-RestMethod -Method Post -Uri $uri -Headers @{ Authorization = "Bearer $token"; 'Content-Type' = 'application/json' } -Body $json | Out-Null
    Write-Host ("Ingested {0} recommendation(s) to {1}. Allow a few minutes for indexing." -f $rows.Count, $StreamName) -ForegroundColor Green
}
else {
    Write-Host 'No DCR target supplied; pass -DcrEndpoint and -DcrImmutableId to ingest.' -ForegroundColor Yellow
}
