Set-StrictMode -Version Latest

function Invoke-DeploymentAz {
    param([Parameter(Mandatory)][string[]]$Arguments)
    $result = & az @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Azure CLI failed (exit $LASTEXITCODE). No credential, network or permission fallback will be attempted."
    }
    $result
}

function Assert-DeploymentContext {
    param(
        [Parameter(Mandatory)][guid]$SubscriptionId,
        [Parameter(Mandatory)][guid]$TenantId
    )
    if ($SubscriptionId -eq [guid]::Empty -or $TenantId -eq [guid]::Empty) {
        throw 'SubscriptionId and TenantId must be nonempty GUIDs.'
    }
    $account = Invoke-DeploymentAz -Arguments @(
        'account', 'show', '--subscription', "$SubscriptionId",
        '--query', '{id:id,tenantId:tenantId,state:state}', '--output', 'json', '--only-show-errors'
    ) | ConvertFrom-Json
    if ($account.id -ne "$SubscriptionId" -or $account.tenantId -ne "$TenantId" -or $account.state -ne 'Enabled') {
        throw 'Azure account does not match the explicitly approved enabled subscription and tenant.'
    }
}

function Get-DeploymentPackage {
    param(
        [Parameter(Mandatory)][string]$PackagePath,
        [string]$ExpectedSHA256
    )
    $package = Get-Item -LiteralPath $PackagePath -ErrorAction Stop
    if ($package.PSIsContainer -or $package.Extension -ine '.zip' -or $package.Length -eq 0) {
        throw 'PackagePath must reference an existing, nonempty ZIP file.'
    }
    if ($ExpectedSHA256 -and (Get-FileHash -LiteralPath $package.FullName -Algorithm SHA256).Hash -ne $ExpectedSHA256) {
        throw 'Package hash differs from the approved artifact.'
    }
    $package.FullName
}
