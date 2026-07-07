<#
.SYNOPSIS
    Validates AMBA enterprise parameter files against governance rules.
.DESCRIPTION
    Checks that parameter values comply with enterprise governance:
    - Required parameters are set
    - Initiative flags are valid
    - Tag conventions are correct
    - Exclusion lists are within policy
    - BYON configurations are consistent
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
$exitCode = 0

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
        "platform_management_group",
        "identity_management_group",
        "management_management_group",
        "connectivity_management_group",
        "landing_zone_management_group",
        "alz_monitor_resource_group_name",
        "alz_monitor_resource_group_location"
    )

    foreach ($param in $requiredParams) {
        if ($content -notmatch "$param\s*=") {
            $errors += "MISSING: Required parameter '$param' not found"
        }
    }

    # Validate _deployed_by_amba tag is present in resource group tags
    if ($content -notmatch '_deployed_by_amba\s*=\s*"True"') {
        $errors += "GOVERNANCE: Tag '_deployed_by_amba = True' must be present in resource group tags (required for cleanup scripts)"
    }

    # Validate initiative flags use correct values
    $flagPattern = 'enable_amba_\w+\s*=\s*"([^"]*)"'
    $flags = [regex]::Matches($content, $flagPattern)
    foreach ($flag in $flags) {
        if ($flag.Groups[1].Value -notin @("Yes", "No")) {
            $errors += "INVALID: Initiative flag value '$($flag.Groups[1].Value)' must be 'Yes' or 'No'"
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

    # Validate disable tag values - production should be strict
    if ($fileName -eq "prod.tfvars") {
        if ($content -match 'alz_monitor_disable_tag_values\s*=\s*\[([^\]]*)\]') {
            $tagValues = $Matches[1]
            if ($tagValues -match '"(Test|Dev|Sandbox)"') {
                $warnings += "GOVERNANCE: Production disable tag values include non-production values - verify this is intentional"
            }
        }

        # Production should opt out of telemetry
        if ($content -match 'telemetry_opt_out\s*=\s*"No"') {
            $warnings += "GOVERNANCE: Production environment has telemetry enabled - verify per enterprise policy"
        }
    }

    # BYON consistency check
    $hasBYOAG = $content -match 'bring_your_own_action_group\s*=\s*\[(?!\s*\])'
    $hasBYOAPR = $content -match 'bring_your_own_alert_processing_rule\s*=\s*\[(?!\s*\])'
    if ($hasBYOAG -and -not $hasBYOAPR) {
        $warnings += "BYON: Action groups are BYO but alert processing rules are not - AMBA APR will still be created"
    }

    # Report results
    foreach ($error in $errors) {
        Write-Host "  ERROR: $error" -ForegroundColor Red
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
