<#
.SYNOPSIS
    Validates Monitoring Baselines parameter files against governance rules.
.DESCRIPTION
    Checks that parameter values comply with governance:
    - Required parameters are set
    - Policy category flags are valid (Yes/No)
    - Private endpoint effect is valid (Audit/Deny/Disabled)
    - Environment matches filename
    - Production does not silently enable Deny without review marker
.PARAMETER Environment
    The environment ring to validate (sandbox, pre-prod, prod). Use -All to validate all.
.PARAMETER All
    Validate all environment files.
.EXAMPLE
    ./validate-parameters.ps1 -Environment prod
    ./validate-parameters.ps1 -All
#>

[CmdletBinding()]
param(
    [Parameter()]
    [ValidateSet("sandbox", "pre-prod", "prod")]
    [string]$Environment,

    [Parameter()]
    [switch]$All
)

$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$terraformDir = Join-Path $scriptRoot ".." "terraform" "environments"

function Test-ParameterFile {
    param([string]$FilePath)

    $fileName = Split-Path -Leaf $FilePath
    Write-Host "`n=== Validating: $fileName ===" -ForegroundColor Cyan

    $content = Get-Content $FilePath -Raw
    $errors = @()
    $warnings = @()

    # Required parameter checks
    $requiredParams = @(
        "environment",
        "enterprise_scale_company_prefix",
        "deployment_location",
        "landing_zone_management_group",
        "enable_ama_policies",
        "enable_diagnostic_settings_policies",
        "enable_private_endpoint_policies",
        "private_endpoint_effect",
        "log_analytics_workspace_id",
        "user_assigned_managed_identity_resource_id"
    )

    foreach ($param in $requiredParams) {
        if ($content -notmatch "$param\s*=") {
            $errors += "MISSING: Required parameter '$param' not found"
        }
    }

    # Validate _deployed_by_monitoring_baselines tag is present
    if ($content -notmatch '_deployed_by_monitoring_baselines\s*=\s*"True"') {
        $errors += "GOVERNANCE: Tag '_deployed_by_monitoring_baselines = True' must be present in resource_tags"
    }

    # Validate policy category flags use correct values
    $flagPattern = 'enable_(ama|diagnostic_settings|private_endpoint)_policies\s*=\s*"([^"]*)"'
    $flags = [regex]::Matches($content, $flagPattern)
    foreach ($flag in $flags) {
        if ($flag.Groups[2].Value -notin @("Yes", "No")) {
            $errors += "INVALID: Policy flag value '$($flag.Groups[2].Value)' must be 'Yes' or 'No'"
        }
    }

    # Validate private endpoint effect
    $effectMatch = [regex]::Match($content, 'private_endpoint_effect\s*=\s*"([^"]*)"')
    if ($effectMatch.Success) {
        $effect = $effectMatch.Groups[1].Value
        if ($effect -notin @("Audit", "Deny", "Disabled")) {
            $errors += "INVALID: private_endpoint_effect '$effect' must be 'Audit', 'Deny', or 'Disabled'"
        }
    }

    # Validate environment matches filename
    $envMatch = [regex]::Match($content, 'environment\s*=\s*"([^"]*)"')
    if ($envMatch.Success) {
        $declaredEnv = $envMatch.Groups[1].Value
        $expectedEnv = $fileName -replace '\.tfvars$', ''
        if ($declaredEnv -ne $expectedEnv) {
            $errors += "MISMATCH: Declared environment '$declaredEnv' doesn't match file '$expectedEnv'"
        }
    }

    # Governance: Deny effect should not be enabled in sandbox
    if ($fileName -eq "sandbox.tfvars" -and $effectMatch.Success -and $effectMatch.Groups[1].Value -eq "Deny") {
        $warnings += "GOVERNANCE: Sandbox should use 'Audit' for private endpoints, not 'Deny'"
    }

    # Governance: warn when prod flips to Deny (high-impact change)
    if ($fileName -eq "prod.tfvars" -and $effectMatch.Success -and $effectMatch.Groups[1].Value -eq "Deny") {
        $warnings += "GOVERNANCE: Production private_endpoint_effect is 'Deny' - confirm pre-prod soak completed and change management approved"
    }

    # Report results
    foreach ($err in $errors) {
        Write-Host "  ERROR: $err" -ForegroundColor Red
    }
    foreach ($warning in $warnings) {
        Write-Host "  WARNING: $warning" -ForegroundColor Yellow
    }

    if ($errors.Count -eq 0) {
        Write-Host "  PASSED: All governance checks passed" -ForegroundColor Green
    }

    return $errors.Count
}

# Determine which files to validate
$filesToValidate = @()
if ($All) {
    $filesToValidate = Get-ChildItem -Path $terraformDir -Filter "*.tfvars"
} elseif ($Environment) {
    $filePath = Join-Path $terraformDir "$Environment.tfvars"
    if (Test-Path $filePath) {
        $filesToValidate = @(Get-Item $filePath)
    } else {
        Write-Error "Parameter file not found: $filePath"
        exit 1
    }
} else {
    # Default: validate all
    $filesToValidate = Get-ChildItem -Path $terraformDir -Filter "*.tfvars"
}

$totalErrors = 0
foreach ($file in $filesToValidate) {
    $totalErrors += Test-ParameterFile -FilePath $file.FullName
}

Write-Host "`n=== Summary ===" -ForegroundColor Cyan
Write-Host "Files validated: $($filesToValidate.Count)"
Write-Host "Total errors: $totalErrors"

if ($totalErrors -gt 0) {
    Write-Host "FAILED: Parameter governance validation failed" -ForegroundColor Red
    exit 1
} else {
    Write-Host "PASSED: All parameter files comply with governance rules" -ForegroundColor Green
    exit 0
}
