[CmdletBinding()]
param([ValidateSet('Source', 'Delivery')][string]$Phase = 'Source')

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Repo = Split-Path $PSScriptRoot -Parent
$Root = Join-Path $Repo 'infra'
$Subscription = '00000001-000a-4000-a000-000000000001'
$Checks = [Collections.Generic.List[object]]::new()

function Add-Check {
    param([string]$Name, [bool]$Passed, [string]$Detail)
    $Checks.Add([pscustomobject]@{ name = $Name; passed = $Passed; detail = $Detail })
}

foreach ($Path in @('infra/versions.tf', 'infra/backend.tf', 'infra/.terraform.lock.hcl',
        'workloads/linux-demo-service/guest_control.py', 'contracts/servicenow-case.schema.json')) {
    Add-Check $Path (Test-Path (Join-Path $Repo $Path) -PathType Leaf) 'Required preparation artifact'
}

$Format = & terraform "-chdir=$Root" fmt -check -recursive 2>&1
Add-Check 'terraform-format' ($LASTEXITCODE -eq 0) ($Format -join "`n")
$Validation = & terraform "-chdir=$Root" validate -json
if ($LASTEXITCODE -eq 0) {
    $Result = $Validation | ConvertFrom-Json
    Add-Check 'terraform-schema' $Result.valid 'Pinned-provider local schema validation; not a live Azure plan'
}
else {
    Add-Check 'terraform-schema' $false ($Validation -join "`n")
}

foreach ($Config in @(@('response-plans', 'plans.json', 'plans'), @('scheduled-tasks', 'tasks.json', 'tasks'))) {
    $Path = Join-Path (Join-Path (Join-Path $Repo 'agent') $Config[0]) $Config[1]
    $Manifest = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    $Enabled = @($Manifest.($Config[2]) | Where-Object { $_.enabled })
    Add-Check $Config[0] ($Enabled.Count -eq 0 -and -not $Manifest.deployed) 'Prepared only; all product configuration remains disabled'
}

if ($Phase -eq 'Delivery') {
    $AccountJson = & az account show --subscription $Subscription --output json --only-show-errors
    if ($LASTEXITCODE -ne 0) {
        Add-Check 'azure-context' $false 'Unable to authenticate to the confirmed subscription'
    }
    else {
        $Account = $AccountJson | ConvertFrom-Json
        Add-Check 'azure-context' ($Account.id -eq $Subscription -and $Account.tenantId -eq '00000002-000a-4000-9000-000000000002') 'Explicit target subscription/tenant'
    }
    foreach ($Namespace in @('Microsoft.App', 'Microsoft.KeyVault', 'Microsoft.Quota')) {
        $State = & az provider show --namespace $Namespace --subscription $Subscription --query registrationState --output tsv --only-show-errors
        Add-Check $Namespace ($LASTEXITCODE -eq 0 -and $State -eq 'Registered') "Observed registration state: $State"
    }
    $StateExists = & az storage blob exists --account-name stexamplestate001 --container-name tfstate `
        --name autonomous-sre-ops --auth-mode login --subscription 00000003-000a-4000-8000-000000000003 `
        --query exists --output tsv --only-show-errors 2>&1
    Add-Check 'private-state-access' ($LASTEXITCODE -eq 0) ($StateExists -join "`n")
}

$Failed = @($Checks | Where-Object { -not $_.passed })
[pscustomobject]@{
    phase         = $Phase
    checked_at    = [DateTimeOffset]::UtcNow.ToString('o')
    passed        = ($Failed.Count -eq 0)
    demo_ready    = $false
    requires_saved_plan = $true
    faults_enabled = $false
    checks        = $Checks
} | ConvertTo-Json -Depth 6
if ($Failed.Count) { exit 1 }
