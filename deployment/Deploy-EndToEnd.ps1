<#
.SYNOPSIS
    End-to-end deployment orchestrator for the SRE Agent observability solution.
    Runs Phases 0-5 in the gated order that the repo requires, driving each
    subproject's own tooling (Terraform / Python builders / PowerShell). Idempotent
    and -WhatIf-safe.

.DESCRIPTION
    Phase 0  sre-agent-pre-req     Verify foundation (providers, LAW, UAMI). Read-only gate.
    Phase 1  monitoring-baselines  Telemetry FIRST: AMA + diagnostic settings (Terraform).
    Phase 2  amba-enterprise       Alerting on top of telemetry (Terraform, ring-by-ring).
    Phase 3  workbooks             Build + validate + deploy dashboards (Python + PS/TF/Bicep).
    Phase 4  sre-agent             Create the agent (Microsoft.App/agents, mode=Review).
    Phase 5  scheduled-tasks       Deploy tasks PAUSED (enable one at a time later).

    Ordering is enforced: telemetry (P1) -> alerting (P2) -> dashboards (P3) ->
    agent (P4) -> tasks (P5). Running out of order surfaces the very findings the
    SRE Agent later reports.

    Each phase ends with a GATE. A failed gate stops the run so you never build a
    later phase on a broken earlier one.

.PARAMETER ConfigFile
    Path to the deployment config JSON. Defaults to config\deployment.parameters.json
    next to this script. See config\deployment.parameters.example.json.

.PARAMETER FromPhase
    First phase to run (0-5). Default 0.

.PARAMETER ToPhase
    Last phase to run (0-5). Default 5.

.PARAMETER WhatIf
    Dry run. Terraform stops after 'plan'; scripts pass through their own -WhatIf;
    nothing is created or activated.

.PARAMETER DeployAgent
    Opt-in for Phase 4 to actually create the agent. Without it, Phase 4 only prints
    the IaC command and the manual portal fallback (safe default).

.PARAMETER ActivateTasks
    Opt-in for Phase 5 to ENABLE tasks after creating them. Default OFF - tasks are
    left PAUSED for human review (the repo's Review-first convention).

.EXAMPLE
    # Full dry run for the sandbox ring
    ./Deploy-EndToEnd.ps1 -WhatIf

.EXAMPLE
    # Deploy telemetry + alerting + dashboards only
    ./Deploy-EndToEnd.ps1 -FromPhase 1 -ToPhase 3

.EXAMPLE
    # Re-run just the workbooks phase
    ./Deploy-EndToEnd.ps1 -FromPhase 3 -ToPhase 3
#>
[CmdletBinding()]
param(
    [string]$ConfigFile = "$PSScriptRoot\config\deployment.parameters.json",
    [ValidateRange(0, 5)][int]$FromPhase = 0,
    [ValidateRange(0, 5)][int]$ToPhase = 5,
    [switch]$WhatIf,
    [switch]$DeployAgent,
    [switch]$ActivateTasks
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
function Write-Phase {
    param([int]$Number, [string]$Name)
    Write-Host ''
    Write-Host ('=' * 78) -ForegroundColor Cyan
    Write-Host ("PHASE $Number - $Name") -ForegroundColor Cyan
    Write-Host ('=' * 78) -ForegroundColor Cyan
}

function Write-Step { param([string]$Message) Write-Host "  -> $Message" -ForegroundColor Gray }

function Assert-Gate {
    param([string]$Name, [bool]$Passed, [string]$Detail)
    if ($Passed) {
        Write-Host "  [GATE PASS] $Name" -ForegroundColor Green
    }
    else {
        Write-Host "  [GATE FAIL] $Name - $Detail" -ForegroundColor Red
        throw "Gate failed at '$Name': $Detail. Fix before continuing to the next phase."
    }
}

function Invoke-Native {
    # Runs an external command (name + args) and throws on non-zero exit.
    param([string]$File, [string[]]$Arguments)
    Write-Step ("$File " + ($Arguments -join ' '))
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed (exit $LASTEXITCODE): $File $($Arguments -join ' ')" }
}

function Get-BackendArgs {
    param([string]$StateKey)
    $b = $cfg.terraformBackend
    $out = @()
    if ($null -ne $b) {
        foreach ($p in $b.PSObject.Properties) {
            if ($p.Name -eq '$comment' -or [string]::IsNullOrWhiteSpace([string]$p.Value)) { continue }
            $out += "-backend-config=$($p.Name)=$($p.Value)"
        }
        if ($out.Count -gt 0) { $out += "-backend-config=key=$StateKey" }
    }
    return $out
}

function Invoke-Terraform {
    # init -> plan (-> apply unless -WhatIf), scoped to a subproject's terraform dir.
    param([string]$Dir, [string[]]$PlanArgs, [string]$StateKey)
    $backend = Get-BackendArgs -StateKey $StateKey
    $overrideFile = $null
    if ($backend.Count -eq 0) {
        # No remote backend configured (local test / dry run). The subproject declares
        # an azurerm backend, so drop in a temporary LOCAL backend override to make
        # init/plan work without cloud state. Removed in finally.
        $overrideFile = Join-Path $Dir 'zz_deployment_local_backend_override.tf'
        Set-Content -Path $overrideFile -Value @('terraform {', '  backend "local" {}', '}')
        Write-Step "No terraformBackend configured -> temporary LOCAL backend override (test mode)"
    }
    try {
        Invoke-Native 'terraform' (@("-chdir=$Dir", 'init', '-input=false', '-reconfigure') + $backend)
        $planFile = Join-Path $env:TEMP "$StateKey.tfplan"
        Invoke-Native 'terraform' (@("-chdir=$Dir", 'plan', '-input=false', "-out=$planFile") + $PlanArgs)
        if ($WhatIf) {
            Write-Step "WhatIf: skipping 'terraform apply' (plan saved to $planFile)"
        }
        else {
            Invoke-Native 'terraform' @("-chdir=$Dir", 'apply', '-input=false', '-auto-approve', $planFile)
        }
    }
    finally {
        if ($overrideFile -and (Test-Path $overrideFile)) { Remove-Item $overrideFile -Force }
    }
}

function Test-ResourceExists {
    param([string]$ResourceId)
    if ([string]::IsNullOrWhiteSpace($ResourceId) -or $ResourceId -like '*<*>*') { return $false }
    $null = az resource show --ids $ResourceId 2>$null
    return ($LASTEXITCODE -eq 0)
}

# ---------------------------------------------------------------------------
# Load + validate config
# ---------------------------------------------------------------------------
if (-not (Test-Path $ConfigFile)) {
    throw "Config file not found: $ConfigFile. Copy config\deployment.parameters.example.json to deployment.parameters.json and fill it in."
}
$cfg = Get-Content -Raw $ConfigFile | ConvertFrom-Json
Write-Host "Config      : $ConfigFile"
Write-Host "Ring        : $($cfg.ring)"
Write-Host "Phases      : $FromPhase -> $ToPhase  (WhatIf=$WhatIf)"

# az login check
$null = az account show 2>$null
if ($LASTEXITCODE -ne 0) { throw "Not logged in to Azure CLI. Run 'az login' first." }

# ===========================================================================
# PHASE 0 - sre-agent-pre-req (verify foundation; read-only)
# ===========================================================================
function Invoke-Phase0 {
    Write-Phase 0 'sre-agent-pre-req (verify foundation)'
    Write-Step "Manual checklist: sre-agent-pre-req\sre-agent-integration-checklist.md"

    Write-Step "Checking required resource providers are Registered"
    $missing = @()
    foreach ($ns in $cfg.requiredProviders) {
        $state = az provider show --namespace $ns --query registrationState -o tsv 2>$null
        Write-Host ("     {0,-32} {1}" -f $ns, $state)
        if ($state -ne 'Registered') {
            $missing += $ns
            if (-not $WhatIf) {
                Write-Step "Registering $ns"
                az provider register --namespace $ns | Out-Null
            }
        }
    }
    Assert-Gate 'Resource providers registered' ($missing.Count -eq 0 -or $WhatIf) `
        "Not registered: $($missing -join ', '). Registration triggered; re-run Phase 0 once state is Registered."

    Write-Step "Verifying central Log Analytics workspace exists"
    Assert-Gate 'Central Log Analytics workspace resolvable' (Test-ResourceExists $cfg.logAnalyticsWorkspaceResourceId) `
        "logAnalyticsWorkspaceResourceId not found: $($cfg.logAnalyticsWorkspaceResourceId)"

    Write-Step "Verifying shared remediation UAMI exists"
    Assert-Gate 'Remediation UAMI resolvable' (Test-ResourceExists $cfg.remediationUamiResourceId) `
        "remediationUamiResourceId not found: $($cfg.remediationUamiResourceId)"

    Write-Host "  NOTE: MG hierarchy, RBAC, and egress allow-list are manual pre-req steps -" -ForegroundColor Yellow
    Write-Host "        confirm them against the checklist and checklists\manual-gates.md." -ForegroundColor Yellow
}

# ===========================================================================
# PHASE 1 - monitoring-baselines (telemetry FIRST)
# ===========================================================================
function Invoke-Phase1 {
    Write-Phase 1 'monitoring-baselines (AMA + diagnostic settings)'
    # Uses the vendored, fixed copy under deployment\ (E2E findings F1/F2/F5) so the shared
    # monitoring-baselines subproject is never modified. See monitoring-baselines\README.md.
    $proj = Join-Path $PSScriptRoot 'monitoring-baselines'
    Push-Location $proj
    try {
        Write-Step "Validating parameters"
        & .\scripts\validate-parameters.ps1
        if ($LASTEXITCODE -ne 0) { throw "monitoring-baselines validate-parameters failed." }

        Invoke-Terraform -Dir (Join-Path $proj 'terraform') -StateKey "monitoring-baselines-$($cfg.ring)" -PlanArgs @(
            "-var-file=environments/$($cfg.ring).tfvars",
            "-var=management_subscription_id=$($cfg.managementSubscriptionId)",
            "-var=user_assigned_managed_identity_resource_id=$($cfg.remediationUamiResourceId)",
            "-var=log_analytics_workspace_id=$($cfg.logAnalyticsWorkspaceResourceId)"
        )
    }
    finally { Pop-Location }
    Write-Host "  NOTE: remediation waves (AMA -> diag settings -> PE review) run after apply -" -ForegroundColor Yellow
    Write-Host "        see monitoring-baselines\docs\onboarding.md." -ForegroundColor Yellow
    Assert-Gate 'monitoring-baselines applied' $true ''
}

# ===========================================================================
# PHASE 2 - amba-enterprise (alerting, ring-by-ring)
# ===========================================================================
function Invoke-Phase2 {
    Write-Phase 2 'amba-enterprise (alert rules + action groups)'
    $proj = Join-Path $repoRoot 'amba-enterprise'
    Push-Location $proj
    try {
        Write-Step "Validating naming + parameters for ring '$($cfg.ring)'"
        & .\scripts\validate-naming.ps1
        if ($LASTEXITCODE -ne 0) { throw "amba-enterprise validate-naming failed." }
        & .\scripts\validate-parameters.ps1 -Environment $cfg.ring
        if ($LASTEXITCODE -ne 0) { throw "amba-enterprise validate-parameters failed." }

        Invoke-Terraform -Dir (Join-Path $proj 'terraform') -StateKey "amba-$($cfg.ring)" -PlanArgs @(
            "-var-file=environments/$($cfg.ring).tfvars",
            "-var=management_subscription_id=$($cfg.managementSubscriptionId)"
        )
    }
    finally { Pop-Location }
    Write-Host "  NOTE: VM/VMSS/HybridVM log alerts require AMA (Phase 1) present first." -ForegroundColor Yellow
    Assert-Gate 'amba-enterprise applied' $true ''
}

# ===========================================================================
# PHASE 3 - workbooks (dashboards before tasks write _CL rows)
# ===========================================================================
function Invoke-Phase3 {
    Write-Phase 3 'workbooks (build + validate + deploy)'
    $proj = Join-Path $repoRoot 'sre-agent-observability-workbooks'
    Push-Location $proj
    try {
        Write-Step "Building spec-driven workbook suite"
        Invoke-Native 'python' @('builder/build_workbooks.py')
        Write-Step "Validating workbooks"
        Invoke-Native 'python' @('tests/validate_workbooks.py')

        Write-Step "Deploying workbooks to Log Analytics workspace"
        $deployArgs = @{ WorkspaceResourceId = $cfg.logAnalyticsWorkspaceResourceId }
        if ($cfg.workbookResourceGroup -and $cfg.workbookResourceGroup -notlike '*<*>*') {
            $deployArgs['ResourceGroupName'] = $cfg.workbookResourceGroup
        }
        if ($WhatIf) { $deployArgs['WhatIf'] = $true }
        & .\scripts\Deploy-Workbooks.ps1 @deployArgs
        if ($LASTEXITCODE -ne 0) { throw "Deploy-Workbooks.ps1 failed." }
    }
    finally { Pop-Location }

    # Optional: discovery-linked auto-generated workbooks
    if ($cfg.autoGeneratedWorkbooks.enabled -and $cfg.autoGeneratedWorkbooks.discoveryInventoryPath) {
        Write-Step "Generating discovery-linked (auto-generated) workbooks"
        $agProj = Join-Path $repoRoot 'auto-generated-workbooks'
        Push-Location $agProj
        try {
            Invoke-Native 'python' @(
                './generator/generate_workbooks.py',
                '--inventory', $cfg.autoGeneratedWorkbooks.discoveryInventoryPath,
                '--workspace-resource-id', $cfg.logAnalyticsWorkspaceResourceId,
                '--templates-dir', './templates', '--output-dir', './generator/out')
        }
        finally { Pop-Location }
    }
    Assert-Gate 'workbooks deployed' $true ''
}

# ===========================================================================
# PHASE 4 - sre-agent (create the agent; Review mode)
# ===========================================================================
function Invoke-Phase4 {
    Write-Phase 4 'sre-agent (create agent, mode=Review)'
    $agentRg = $cfg.sreAgent.resourceGroup
    $agentName = $cfg.sreAgent.name
    $template = Join-Path $PSScriptRoot 'templates\sre-agent.bicep'

    if ($cfg.sreAgent.existingResourceId -and $cfg.sreAgent.existingResourceId -notlike '*<*>*') {
        Write-Step "Using existing agent: $($cfg.sreAgent.existingResourceId)"
    }
    elseif ($DeployAgent -and -not $WhatIf) {
        Write-Step "Deploying agent via Bicep (Microsoft.App/agents)"
        Invoke-Native 'az' @('deployment', 'group', 'create',
            '--resource-group', $agentRg,
            '--template-file', $template,
            '--parameters', "agentName=$agentName", "location=$($cfg.location)", "mode=$($cfg.sreAgent.mode)")
    }
    else {
        Write-Host "  Phase 4 create is opt-in. To provision the agent as IaC, run with -DeployAgent:" -ForegroundColor Yellow
        Write-Host "     az deployment group create -g $agentRg --template-file `"$template`" \" -ForegroundColor Yellow
        Write-Host "       --parameters agentName=$agentName location=$($cfg.location) mode=$($cfg.sreAgent.mode)" -ForegroundColor Yellow
        Write-Host "  Portal fallback: sre.azure.com -> Create agent (Reader/Review to start)." -ForegroundColor Yellow
    }
    Write-Host "  NOTE: verify Microsoft.App/agents@2026-01-01 is available in your tenant/region;" -ForegroundColor Yellow
    Write-Host "        if not, use the sre.azure.com portal path per the pre-req checklist." -ForegroundColor Yellow
    Assert-Gate 'sre-agent phase handled' $true ''
}

# ===========================================================================
# PHASE 5 - scheduled-tasks (deploy PAUSED)
# ===========================================================================
function Invoke-Phase5 {
    Write-Phase 5 'scheduled-tasks (deploy PAUSED, enable one at a time)'
    $agentId = $cfg.sreAgent.existingResourceId
    if ((-not $agentId) -or ($agentId -like '*<*>*')) {
        if ($cfg.sreAgent.resourceGroup -and $cfg.sreAgent.name -and $cfg.sreAgent.name -notlike '*<*>*') {
            $agentId = "/subscriptions/$($cfg.subscriptionId)/resourceGroups/$($cfg.sreAgent.resourceGroup)/providers/Microsoft.App/agents/$($cfg.sreAgent.name)"
        }
    }
    if ((-not $agentId) -or ($agentId -like '*<*>*')) {
        Assert-Gate 'agent resource id available' $false "Set sreAgent.existingResourceId (or resourceGroup+name) in config before Phase 5."
    }

    $proj = Join-Path $repoRoot 'sre-agent-observability-workbooks\automation'
    Push-Location $proj
    try {
        $taskArgs = @{ AgentResourceId = $agentId; Tenant = $cfg.tenantId }
        if ($WhatIf) { $taskArgs['WhatIf'] = $true }
        if ($ActivateTasks) { $taskArgs['Activate'] = $true }
        & .\Deploy-AgentTasks.ps1 @taskArgs
        if ($LASTEXITCODE -ne 0) { throw "Deploy-AgentTasks.ps1 failed." }
    }
    finally { Pop-Location }
    if (-not $ActivateTasks) {
        Write-Host "  Tasks deployed PAUSED. Enable one at a time after reviewing each prompt." -ForegroundColor Yellow
    }
    Assert-Gate 'scheduled-tasks deployed' $true ''
}

# ---------------------------------------------------------------------------
# Run selected phases in order
# ---------------------------------------------------------------------------
$phases = [ordered]@{
    0 = { Invoke-Phase0 }
    1 = { Invoke-Phase1 }
    2 = { Invoke-Phase2 }
    3 = { Invoke-Phase3 }
    4 = { Invoke-Phase4 }
    5 = { Invoke-Phase5 }
}

foreach ($n in $phases.Keys) {
    if ($n -ge $FromPhase -and $n -le $ToPhase) { & $phases[$n] }
}

Write-Host ''
Write-Host ('=' * 78) -ForegroundColor Green
Write-Host "DONE. Phases $FromPhase-$ToPhase completed (WhatIf=$WhatIf)." -ForegroundColor Green
Write-Host "Record pass/fail in deployment\checklists\e2e-test-results.md (Story #65)." -ForegroundColor Green
Write-Host ('=' * 78) -ForegroundColor Green
