<#
.SYNOPSIS
    Deploys the discovery test fixture to a test subscription.

.PARAMETER SubscriptionId
    Target subscription. The signed-in user must have Contributor on the RG
    (or Owner / Contributor at the subscription scope to create the RG).

.PARAMETER ResourceGroupName
    Resource group to deploy into. Created if missing.

.PARAMETER Location
    Azure region (e.g. eastus2, westus3).

.PARAMETER SqlAdminPassword
    SecureString for the test SQL server admin password.

.EXAMPLE
    $pw = Read-Host -AsSecureString "SQL admin password"
    .\Deploy-TestFixture.ps1 -SubscriptionId <id> -ResourceGroupName rg-obs-test -Location eastus2 -SqlAdminPassword $pw
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $SubscriptionId,
    [Parameter()]          [string] $ResourceGroupName = 'rg-obs-discovery-test',
    [Parameter()]          [string] $Location = 'eastus2',
    [Parameter(Mandatory)] [securestring] $SqlAdminPassword
)

$ErrorActionPreference = 'Stop'

Write-Host "==> Setting subscription context..." -ForegroundColor Cyan
az account set --subscription $SubscriptionId | Out-Null

Write-Host "==> Ensuring resource group $ResourceGroupName in $Location..." -ForegroundColor Cyan
az group create --name $ResourceGroupName --location $Location --output none

$signedInObjectId = az ad signed-in-user show --query id -o tsv
if (-not $signedInObjectId) {
    throw "Could not resolve signed-in user object id. Run 'az login' first."
}

$pwPlain = [System.Net.NetworkCredential]::new('', $SqlAdminPassword).Password

$bicep = Join-Path $PSScriptRoot 'main.bicep'
$deploymentName = "obs-fixture-{0:yyyyMMddHHmmss}" -f (Get-Date)

Write-Host "==> Deploying $bicep (deployment: $deploymentName)..." -ForegroundColor Cyan
$result = az deployment group create `
    --resource-group $ResourceGroupName `
    --name $deploymentName `
    --template-file $bicep `
    --parameters sqlAdminPassword=$pwPlain keyVaultAdminObjectId=$signedInObjectId `
    --output json | ConvertFrom-Json

if (-not $result) {
    throw "Deployment failed."
}

$expected = $result.properties.outputs.expectedFindings.value
$outFile  = Join-Path $PSScriptRoot 'expected-findings.json'
$expected | ConvertTo-Json -Depth 6 | Set-Content -Path $outFile -Encoding utf8

Write-Host ""
Write-Host "Deployment complete." -ForegroundColor Green
Write-Host "  Resource group : $ResourceGroupName"
Write-Host "  Expected file  : $outFile"
Write-Host ""
Write-Host "Next:" -ForegroundColor Yellow
Write-Host "  python -m discovery scan --subscription $SubscriptionId --out-dir .\out"
Write-Host "  pytest tests/integration -q"
