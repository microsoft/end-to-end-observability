<#
.SYNOPSIS
    Deploy the SRE Agent Operating Pack (scheduled tasks / skills) to an Azure SRE
    Agent via its data-plane API. Reads each task's *.agent.md prompt, creates the
    scheduled task, and leaves it PAUSED (safe by default).

.DESCRIPTION
    The SRE Agent's "Agent Extensions" ARM surface
    (Microsoft.App/agents/scheduledtasks) is restricted to internal Microsoft
    tenants, but the agent's data-plane endpoint
    (https://<agent>.<region>.azuresre.ai/api/v1/scheduledtasks) accepts the same
    create/pause operations for any tenant when the caller holds the **SRE Agent
    Administrator** (or Author) RBAC role on the agent resource.

    This script:
      1. Resolves the agent's data-plane endpoint (agentEndpoint) from ARM.
      2. For each task in the catalog, reads the markdown BODY of its *.agent.md
         (frontmatter stripped), sanitizes non-ASCII (the API's JSON parser rejects
         raw em-dashes/smart quotes), and POSTs a CreateScheduledTask request.
      3. Pauses every task it creates (create defaults to Active) so nothing runs
         until a human reviews and enables it.

    Re-runnable: existing tasks (by name) are skipped.

.PARAMETER AgentResourceId
    ARM resource ID of the Microsoft.App/agents resource.

.PARAMETER Tenant
    Entra tenant ID that owns the agent (for token acquisition).

.PARAMETER Activate
    Also enable (resume) each task after creating it. Default OFF -- tasks are left
    Paused. Use only after reviewing the prompts.

.PARAMETER WhatIf
    List what would be deployed without calling the agent.

.EXAMPLE
    ./Deploy-AgentTasks.ps1 `
        -AgentResourceId "/subscriptions/<sub>/resourceGroups/rg-sre-lab/providers/Microsoft.App/agents/sre-agent-xxxx" `
        -Tenant "<tenant-guid>"
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$AgentResourceId,
    [Parameter(Mandatory = $true)][string]$Tenant,
    [switch]$Activate,
    [switch]$WhatIf
)

$ErrorActionPreference = 'Stop'
$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }

# Task catalog: name -> prompt file (relative to this dir), cron, agent mode, blurb.
$catalog = @(
    @{ name = 'sre-detect-health';               file = 'detect-health\sre-detect-health.agent.md';                         cron = '*/15 * * * *'; mode = 'autonomous'; desc = 'Detect stage: platform signals -> scored recommendations. Feeds workbooks #1/#2/#5.' }
    @{ name = 'sre-coverage-onboarding';          file = 'coverage-onboarding\sre-coverage-onboarding.agent.md';             cron = '0 6 * * *';    mode = 'autonomous'; desc = 'Coverage/onboarding: ensure everything is monitored; propose baseline. Feeds #7/#8.' }
    @{ name = 'sre-incident-triage-rca';          file = 'incident-triage-rca\sre-incident-triage-rca.agent.md';             cron = '*/30 * * * *'; mode = 'autonomous'; desc = 'Triage & RCA: correlate evidence, blast radius, root-cause candidates. Feeds #3.' }
    @{ name = 'sre-remediation-act';              file = 'remediation-act\sre-remediation-act.agent.md';                     cron = '*/15 * * * *'; mode = 'review';     desc = 'Gated Act: execute APPROVED remediations under OBO with rollback. Feeds #4.' }
    @{ name = 'sre-verify-validate';              file = 'verify-validate\sre-verify-validate.agent.md';                     cron = '*/30 * * * *'; mode = 'autonomous'; desc = 'Verify: confirm a fix worked or request rollback. Feeds #6.' }
    @{ name = 'sre-app-reliability-slo';          file = 'app-reliability-slo\sre-app-reliability-slo.agent.md';             cron = '0 * * * *';    mode = 'autonomous'; desc = 'App reliability/SLO: failure rate, latency, error-budget burn. Feeds #6.' }
    @{ name = 'sre-ea-governance-drift';          file = 'ea-governance-drift\sre-ea-governance-drift.agent.md';             cron = '30 6 * * *';   mode = 'autonomous'; desc = 'Governance/drift: compliance, tagging, exposure, drift, DR. Feeds #7.' }
    @{ name = 'sre-observability-quality-cost';   file = 'observability-quality-cost\sre-observability-quality-cost.agent.md'; cron = '0 7 * * *';  mode = 'autonomous'; desc = 'Quality & cost: telemetry completeness, alert hygiene, LA cost. Feeds #8.' }
    @{ name = 'sre-security-identity-posture';    file = 'security-identity-posture\sre-security-identity-posture.agent.md'; cron = '0 5 * * *';    mode = 'review';     desc = 'Security/identity posture: Defender, exposure, KV, RBAC, ACR. Feeds #9.' }
    @{ name = 'sre-workbook-maintenance';         file = 'weekly-review\sre-workbook-maintenance.agent.md';                  cron = '0 8 * * 1';    mode = 'review';     desc = 'Weekly workbook self-maintenance: freshness, panels, drift, alert quality. Feeds #10.' }
)

function Get-PromptBody {
    param([string]$Path)
    $raw = Get-Content $Path -Raw
    if ($raw -match '(?s)^\s*---.*?---\s*(.*)$') { $body = $Matches[1].Trim() } else { $body = $raw.Trim() }
    # The data-plane JSON parser rejects raw non-ASCII (em dashes, smart quotes).
    $body = $body -replace [char]0x2014, ' - ' -replace [char]0x2013, '-' `
                  -replace [char]0x2018, "'" -replace [char]0x2019, "'" `
                  -replace [char]0x201C, '"' -replace [char]0x201D, '"'
    -join ($body.ToCharArray() | ForEach-Object { if ([int]$_ -le 127) { $_ } else { '-' } })
}

# Resolve the agent data-plane endpoint from ARM.
$apiVersion = '2025-05-01-preview'
$armTok = az account get-access-token --tenant $Tenant --resource 'https://management.azure.com' --query accessToken -o tsv
$agent = Invoke-RestMethod -Uri "https://management.azure.com$AgentResourceId`?api-version=$apiVersion" -Headers @{ Authorization = "Bearer $armTok" }
$endpoint = $agent.properties.agentEndpoint
if (-not $endpoint) { throw "Could not resolve agentEndpoint from $AgentResourceId" }
Write-Host "Agent endpoint : $endpoint"
Write-Host "Tasks to deploy: $($catalog.Count) (left PAUSED unless -Activate)"
Write-Host ('-' * 70)

$dpTok = az account get-access-token --tenant $Tenant --resource 'https://azuresre.ai' --query accessToken -o tsv
$dh = @{ Authorization = "Bearer $dpTok"; 'Content-Type' = 'application/json' }
$existing = (Invoke-RestMethod -Uri "$endpoint/api/v1/scheduledtasks" -Headers $dh).name

foreach ($t in $catalog) {
    if ($existing -contains $t.name) { Write-Host "SKIP (exists): $($t.name)"; continue }
    $promptPath = Join-Path $scriptDir $t.file
    if (-not (Test-Path $promptPath)) { Write-Host "MISSING prompt: $($t.file)"; continue }
    if ($WhatIf) { Write-Host "[what-if] create $($t.name)  [$($t.cron), $($t.mode)]"; continue }

    $payload = @{
        name           = $t.name
        description    = $t.desc
        cronExpression = $t.cron
        agentPrompt    = (Get-PromptBody $promptPath)
        agentMode      = $t.mode
        status         = 'Paused'
    } | ConvertTo-Json -Depth 4

    Invoke-RestMethod -Method Post -Uri "$endpoint/api/v1/scheduledtasks" -Headers $dh -Body $payload | Out-Null
    Start-Sleep -Seconds 2   # eventual consistency before the task is queryable
    $id = (Invoke-RestMethod -Uri "$endpoint/api/v1/scheduledtasks" -Headers $dh | Where-Object { $_.name -eq $t.name }).id
    if ($id -and -not $Activate) {
        Invoke-RestMethod -Method Post -Uri "$endpoint/api/v1/scheduledtasks/$id/pause" -Headers $dh | Out-Null
        Write-Host "CREATED + PAUSED: $($t.name)  [$($t.cron), $($t.mode)]"
    } else {
        Write-Host "CREATED (ACTIVE): $($t.name)  [$($t.cron), $($t.mode)]"
    }
}

Write-Host ('-' * 70)
$final = Invoke-RestMethod -Uri "$endpoint/api/v1/scheduledtasks" -Headers $dh
$final | Where-Object { $_.name -like 'sre-*' } | Sort-Object name |
    ForEach-Object { "{0,-32} {1,-8} {2,-14} {3}" -f $_.name, $_.status, $_.cronExpression, $_.agentMode } | Write-Host
Write-Host "Done. Review each task in sre.azure.com, then enable one at a time."
