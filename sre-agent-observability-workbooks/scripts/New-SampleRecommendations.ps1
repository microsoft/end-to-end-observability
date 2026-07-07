<#
.SYNOPSIS
    Generate sample SREAgentRecommendations_CL rows so the "Top 10 SRE Agent
    Recommendations" panels light up during testing, and (optionally) ingest them
    via the Azure Monitor Logs Ingestion API.

.DESCRIPTION
    The Top 10 section on every workbook page reads from the custom table
    SREAgentRecommendations_CL. Until the SRE Agent is writing real
    recommendations, use this to seed representative rows.

    Two modes:
      1. File mode (default): writes sample rows to a JSON file you can inspect or
         hand to your own ingestion pipeline.
      2. Ingest mode: if -DcrEndpoint, -DcrImmutableId and -StreamName are supplied,
         POSTs the rows to the Logs Ingestion API (requires the DCR/DCE and custom
         table to exist — see docs/data-sources.md). Auth uses the current az login
         token for https://monitor.azure.com.

.PARAMETER Count
    Number of sample rows to generate. Default 25.

.PARAMETER OutFile
    Where to write the sample JSON. Default ./sample-recommendations.json.

.PARAMETER DcrEndpoint
    Data Collection Endpoint logs-ingestion URI, e.g.
    https://<dce-name>.<region>.ingest.monitor.azure.com

.PARAMETER DcrImmutableId
    Immutable ID of the Data Collection Rule (e.g. dcr-xxxxxxxx...).

.PARAMETER StreamName
    DCR stream name targeting the custom table, e.g.
    Custom-SREAgentRecommendations_CL.

.EXAMPLE
    ./New-SampleRecommendations.ps1 -Count 30

.EXAMPLE
    ./New-SampleRecommendations.ps1 -DcrEndpoint "https://dce-sre.eastus.ingest.monitor.azure.com" `
        -DcrImmutableId "dcr-abc123" -StreamName "Custom-SREAgentRecommendations_CL"
#>
[CmdletBinding()]
param(
    [int]$Count = 25,
    [string]$OutFile,
    [string]$DcrEndpoint,
    [string]$DcrImmutableId,
    [string]$StreamName
)

$ErrorActionPreference = 'Stop'
$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $OutFile) { $OutFile = Join-Path $scriptDir 'sample-recommendations.json' }

# WorkbookName / WorkbookPage pairs must match what the workbooks filter on
# (see workbooks/manifest.json -> displayName + page).
$pages = @(
    @{ Name = 'SRE Agent Command Center';                 Page = 'Command Center' },
    @{ Name = 'Tenant Health Model';                      Page = 'Tenant Health Model' },
    @{ Name = 'Incident Triage & RCA';                    Page = 'Incident Triage & RCA' },
    @{ Name = 'Automation & Remediation';                 Page = 'Automation & Remediation' },
    @{ Name = 'Operations Command Center';                Page = 'Operations Command Center' },
    @{ Name = 'Application Reliability';                   Page = 'Application Reliability' },
    @{ Name = 'Enterprise Architecture & Governance';     Page = 'Enterprise Architecture & Governance' },
    @{ Name = 'Observability Quality & Cost';             Page = 'Observability Quality & Cost' },
    @{ Name = 'Security & Identity Observability';        Page = 'Security & Identity Observability' },
    @{ Name = 'SRE Agent Workbook Maintenance';           Page = 'Workbook Maintenance' }
)

$severities      = @('Sev0', 'Sev1', 'Sev2', 'Sev3', 'Sev4')
$categories      = @('Reliability', 'Telemetry coverage', 'Security', 'Cost', 'Governance', 'Root cause', 'Post-incident')
$statuses        = @('New', 'Pending approval', 'Approved', 'In progress', 'Failed')
$actionTypes     = @('Inform', 'Recommend', 'Open ticket', 'Notify owner', 'Open PR', 'Auto-update workbook', 'Auto-remediate', 'Request approval', 'Suppress', 'Escalate')
$criticalities   = @('Mission critical', 'High', 'Medium', 'Low')
$teams           = @('Platform-SRE', 'App-Payments', 'App-Identity', 'Network-Core', 'Data-Platform')
$risks           = @('Low', 'Medium', 'High')
$texts           = @(
    'Enable diagnostic settings on resources missing telemetry',
    'Add an action group to alert rules with no routing',
    'Right-size Log Analytics commitment tier to reduce ingestion cost',
    'Assign Owner tag to unowned production workloads',
    'Enable purge protection on Key Vaults storing production secrets',
    'Add an availability test for the checkout user journey',
    'Investigate dependency failure spike on payments-db',
    'Restrict public blob access on exposed storage accounts',
    'Define an SLO for the orders API (no error budget today)',
    'Review broad Owner role assignments at subscription scope'
)

$now = (Get-Date).ToUniversalTime()
$rows = for ($i = 0; $i -lt $Count; $i++) {
    $p = $pages[$i % $pages.Count]
    $sev = $severities[(Get-Random -Maximum $severities.Count)]
    $score = [math]::Round((Get-Random -Minimum 20 -Maximum 100) + (Get-Random -Minimum 0.0 -Maximum 1.0), 2)
    [ordered]@{
        TimeGenerated       = $now.AddMinutes(-1 * (Get-Random -Maximum 4320)).ToString('o')
        RecommendationId    = [guid]::NewGuid().ToString()
        WorkbookName        = $p.Name
        WorkbookPage        = $p.Page
        Rank                = ($i % 10) + 1
        Severity            = $sev
        Category            = $categories[(Get-Random -Maximum $categories.Count)]
        RecommendationText  = $texts[(Get-Random -Maximum $texts.Count)]
        Reason              = 'Detected by SRE Agent during scheduled analysis.'
        EvidenceSummary     = 'See linked query results and resource graph snapshot.'
        EvidenceLinks       = 'https://portal.azure.com/'
        AffectedScope       = "/subscriptions/$([guid]::NewGuid())"
        AffectedResourceId  = "/subscriptions/$([guid]::NewGuid())/resourceGroups/rg-demo/providers/Microsoft.Compute/virtualMachines/vm-$i"
        ApplicationName     = "app-$((Get-Random -Maximum 20))"
        BusinessCriticality = $criticalities[(Get-Random -Maximum $criticalities.Count)]
        OwnerTeam           = $teams[(Get-Random -Maximum $teams.Count)]
        ConfidenceScore     = [math]::Round((Get-Random), 2)
        RecommendationScore = $score
        AgentActionType     = $actionTypes[(Get-Random -Maximum $actionTypes.Count)]
        ApprovalRequired    = [bool](Get-Random -Maximum 2)
        RemediationRisk     = $risks[(Get-Random -Maximum $risks.Count)]
        RollbackAvailable   = [bool](Get-Random -Maximum 2)
        Status              = $statuses[(Get-Random -Maximum $statuses.Count)]
        CreatedBy           = 'sre-agent'
        LastUpdated         = $now.AddMinutes(-1 * (Get-Random -Maximum 120)).ToString('o')
        ExpiresOn           = $now.AddDays((Get-Random -Minimum 1 -Maximum 30)).ToString('o')
        RelatedIncidentId   = "INC$((Get-Random -Minimum 100000 -Maximum 999999))"
        RelatedAlertId      = [guid]::NewGuid().ToString()
        RelatedPullRequest  = "https://dev.azure.com/org/proj/_git/repo/pullrequest/$((Get-Random -Maximum 9999))"
        RelatedWorkItem     = "$((Get-Random -Minimum 1000 -Maximum 9999))"
    }
}

$json = $rows | ConvertTo-Json -Depth 6
$json | Set-Content -Path $OutFile -Encoding utf8
Write-Host "Wrote $Count sample rows -> $OutFile"

if ($DcrEndpoint -and $DcrImmutableId -and $StreamName) {
    Write-Host "Ingesting via Logs Ingestion API ..."
    $token = (az account get-access-token --resource "https://monitor.azure.com" --query accessToken -o tsv)
    if (-not $token) { throw "Could not obtain az access token for https://monitor.azure.com (run 'az login')." }
    $uri = "$($DcrEndpoint.TrimEnd('/'))/dataCollectionRules/$DcrImmutableId/streams/$($StreamName)?api-version=2023-01-01"
    $headers = @{ Authorization = "Bearer $token"; 'Content-Type' = 'application/json' }
    Invoke-RestMethod -Method Post -Uri $uri -Headers $headers -Body $json | Out-Null
    Write-Host "Ingested $Count rows to $StreamName. Allow a few minutes for indexing."
}
else {
    Write-Host "File mode only. To ingest, pass -DcrEndpoint, -DcrImmutableId and -StreamName (see docs/data-sources.md)."
}
