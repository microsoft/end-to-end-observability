[CmdletBinding()]
param([switch]$VerifyOnly)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Subscription = '00000001-000a-4000-a000-000000000001'
$Group = "/subscriptions/$Subscription/resourceGroups/rg-asre-control-cus"
$Principal = '00000043-000a-4000-a000-000000000043'
$Client = if ($VerifyOnly) { '00000054-000a-4000-9000-000000000054' } else { '00000018-000a-4000-b000-000000000018' }
$Action = 'Microsoft.App/jobs/listSecrets/action'
$Role = @{
    Name = 'ASRE Terraform job refresh'
    IsCustom = $true
    Description = 'Allow the existing readonly Plan identity to refresh only the three secret-free demo jobs.'
    Actions = @($Action)
    NotActions = @()
    DataActions = @()
    NotDataActions = @()
    AssignableScopes = @($Group)
}
if ([string]::IsNullOrWhiteSpace($env:idToken) -or $env:servicePrincipalId -ne $Client -or
    $env:tenantId -ne '00000002-000a-4000-9000-000000000002') {
    throw 'Use only the protected approved OIDC service connection for this fixed delivery precondition.'
}

function Invoke-AzJson {
    param([string[]]$Arguments, [switch]$AllowAuthorizationPropagation)
    $deadline = [DateTimeOffset]::UtcNow.AddMinutes(6)
    do {
        $json = & az @Arguments --subscription $Subscription --only-show-errors --output json 2>&1
        if ($LASTEXITCODE -eq 0) { return ($json -join "`n") | ConvertFrom-Json }
        if (-not $AllowAuthorizationPropagation -or ($json -join "`n") -notmatch 'AuthorizationFailed' -or
            [DateTimeOffset]::UtcNow -ge $deadline) {
            throw "The fixed job-refresh permission operation failed: $($Arguments[0..2] -join ' ')."
        }
        Write-Information 'Waiting for the explicitly scoped readonly role to propagate.' -InformationAction Continue
        Start-Sleep -Seconds 15
    } while ($true)
}

if (-not $VerifyOnly) {
    $existing = @(Invoke-AzJson -Arguments @('role', 'definition', 'list', '--name', $Role.Name))
    if ($existing.Count -eq 0) {
        $file = New-TemporaryFile
        try {
            $Role | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $file.FullName -Encoding utf8
            $null = Invoke-AzJson -Arguments @('role', 'definition', 'create', '--role-definition', "@$($file.FullName)")
        }
        finally { Remove-Item -LiteralPath $file.FullName -Force }
        $existing = @(Invoke-AzJson -Arguments @('role', 'definition', 'list', '--name', $Role.Name))
    }
    if ($existing.Count -ne 1 -or $existing[0].roleName -ne $Role.Name -or
        $existing[0].roleType -ne 'CustomRole' -or
        $existing[0].name -notmatch '^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$' -or
        $existing[0].id -ne "/subscriptions/$Subscription/providers/Microsoft.Authorization/roleDefinitions/$($existing[0].name)" -or
        ($existing[0].assignableScopes -join ',') -ne $Group -or
        @($existing[0].permissions).Count -ne 1 -or
        ($existing[0].permissions[0].actions -join ',') -ne $Action -or
        $existing[0].permissions[0].notActions -or $existing[0].permissions[0].dataActions -or
        $existing[0].permissions[0].notDataActions) {
        throw 'The existing fixed role differs from its one-action contract; no assignment was made.'
    }
    $RoleId = $existing[0].name
}
foreach ($name in @('worker', 'collector', 'bootstrap')) {
    $scope = "$Group/providers/Microsoft.App/jobs/job-asre-$name-cus"
    if ($VerifyOnly) {
        $result = Invoke-AzJson -AllowAuthorizationPropagation -Arguments @(
            'rest', '--method', 'post', '--url', "https://management.azure.com$scope/listSecrets?api-version=2025-07-01"
        )
        if (-not $result.PSObject.Properties['value'] -or @($result.value).Count -ne 0) {
            throw 'The fixed demo job unexpectedly contains application secrets; do not publish their values or continue.'
        }
        Write-Output "Confirmed readonly provider refresh of secret-free job: $name"
        continue
    }
    $job = Invoke-AzJson -Arguments @(
        'rest', '--method', 'get', '--url', "https://management.azure.com${scope}?api-version=2025-07-01"
    )
    $config = $job.properties.configuration
    if ($config.PSObject.Properties['secrets'] -and $config.secrets) {
        throw 'A fixed demo job unexpectedly contains application secrets; no refresh permission was assigned to it.'
    }
    $assigned = @(Invoke-AzJson -Arguments @(
        'role', 'assignment', 'list', '--scope', $scope, '--assignee-object-id', $Principal, '--role', $RoleId
    ))
    if ($assigned.Count -eq 0) {
        $null = Invoke-AzJson -Arguments @(
            'role', 'assignment', 'create', '--scope', $scope, '--assignee-object-id', $Principal,
            '--assignee-principal-type', 'ServicePrincipal', '--role', $RoleId
        )
    }
    Write-Output "Established the single readonly refresh action at the exact job scope: $name"
}
