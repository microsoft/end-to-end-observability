$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Repo = Split-Path $PSScriptRoot -Parent
$global:FixturePlanRole = $null
$global:FixturePlanAssignments = @()
$global:FixturePlanBroadRole = $false
$original = @{ idToken = $env:idToken; servicePrincipalId = $env:servicePrincipalId; tenantId = $env:tenantId }
function global:az {
    $global:LASTEXITCODE = 0
    if (($args[0..2] -join ' ') -eq 'role definition list') {
        if (-not $global:FixturePlanRole) { return '[]' }
        if ($args[[Array]::IndexOf($args, '--name') + 1] -ne $global:FixturePlanRole.Name) {
            return '[]'
        }
        return (@{
            name = '00000041-000a-4000-8000-000000000041'
            id = '/subscriptions/00000001-000a-4000-a000-000000000001/providers/Microsoft.Authorization/roleDefinitions/00000041-000a-4000-8000-000000000041'
            roleType = 'CustomRole'
            roleName = $global:FixturePlanRole.Name; assignableScopes = $global:FixturePlanRole.AssignableScopes
            permissions = @(@{ actions = $(if ($global:FixturePlanBroadRole) { @('*') } else { $global:FixturePlanRole.Actions }); notActions = @(); dataActions = @(); notDataActions = @() })
        } | ConvertTo-Json -Depth 8)
    }
    if (($args[0..2] -join ' ') -eq 'role definition create') {
        $file = $args[[Array]::IndexOf($args, '--role-definition') + 1]
        $global:FixturePlanRole = Get-Content -LiteralPath $file.Substring(1) -Raw | ConvertFrom-Json
        return '{}'
    }
    if (($args[0..2] -join ' ') -eq 'role assignment list') {
        $scope = $args[[Array]::IndexOf($args, '--scope') + 1]
        if ($global:FixturePlanAssignments -contains $scope) { return '{"id":"fixture-assignment"}' }
        return '[]'
    }
    if (($args[0..2] -join ' ') -eq 'role assignment create') {
        if ($args[[Array]::IndexOf($args, '--role') + 1] -ne '00000041-000a-4000-8000-000000000041') {
            throw 'The assignment did not use the verified server-generated role ID.'
        }
        $global:FixturePlanAssignments += $args[[Array]::IndexOf($args, '--scope') + 1]
        return '{}'
    }
    if ($args[0] -eq 'rest') {
        if ($args[2] -eq 'post') { return '{"value":[]}' }
        return '{"properties":{"configuration":{"secrets":[]}}}'
    }
    throw 'Unexpected fixture operation; no real Azure command is permitted.'
}
try {
    $env:idToken = 'fixture-only'
    $env:tenantId = '00000002-000a-4000-9000-000000000002'
    $env:servicePrincipalId = '00000018-000a-4000-b000-000000000018'
    & (Join-Path $Repo 'scripts/Initialize-PlanJobReadAccess.ps1')
    if (($global:FixturePlanRole.Actions -join ',') -ne 'Microsoft.App/jobs/listSecrets/action' -or
        $global:FixturePlanRole.DataActions -or $global:FixturePlanAssignments.Count -ne 3 -or
        @($global:FixturePlanAssignments | Where-Object { $_ -notmatch '/jobs/job-asre-(worker|collector|bootstrap)-cus$' }).Count) {
        throw 'The fixed refresh permission fixture widened action or scope.'
    }
    & (Join-Path $Repo 'scripts/Initialize-PlanJobReadAccess.ps1')
    if ($global:FixturePlanAssignments.Count -ne 3) { throw 'An idempotent preflight duplicated assignments.' }
    $global:FixturePlanBroadRole = $true
    $rejected = $false
    try { & (Join-Path $Repo 'scripts/Initialize-PlanJobReadAccess.ps1') }
    catch {
        if ($_.Exception.Message -notmatch 'differs from its one-action contract') { throw }
        $rejected = $true
    }
    if (-not $rejected -or $global:FixturePlanAssignments.Count -ne 3) {
        throw 'The preflight admitted a widened existing role.'
    }
    $global:FixturePlanBroadRole = $false
    $env:servicePrincipalId = '00000054-000a-4000-9000-000000000054'
    & (Join-Path $Repo 'scripts/Initialize-PlanJobReadAccess.ps1') -VerifyOnly
    if ($global:FixturePlanAssignments.Count -ne 3) { throw 'Read verification mutated permission assignments.' }
    Write-Output 'Fixed Plan job-read fixtures passed; no Azure operation was executed.'
}
finally {
    foreach ($key in $original.Keys) { Set-Item -LiteralPath "Env:$key" -Value $original[$key] }
    Remove-Item Function:\az
    Remove-Variable -Name FixturePlanRole, FixturePlanAssignments, FixturePlanBroadRole -Scope Global
}
