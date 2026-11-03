$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Repo = Split-Path $PSScriptRoot -Parent
$Directory = Join-Path ([IO.Path]::GetTempPath()) "asre-dashboard-auth-fixture-$([guid]::NewGuid().ToString('N'))"
$null = New-Item -ItemType Directory -Path $Directory
$scriptPath = Join-Path $Repo 'scripts\Initialize-DashboardAuthentication.ps1'
$outputPath = Join-Path $Directory 'outputs.json'
$metadataPath = Join-Path $Directory 'metadata.json'
$global:AuthCalls = [Collections.Generic.List[object]]::new()
$global:AuthOwner = '00000057-000a-4000-a000-000000000057'
$global:AuthGroupEnabled = $true
$global:AuthVaultFails = $false
$global:AuthSecret = 'fixture-value-not-a-real-credential'
$global:AuthOrigin = 'https://ca-asre-dash-demo-4d79.example.centralus.azurecontainerapps.io'
$global:AuthSecretUri = 'https://kv-asre-dash-demo-4d79.vault.azure.net/secrets/entra-client-secret/' + ('a' * 32)
$global:AuthApp = @{
    id = '11111111-1111-1111-1111-111111111111'
    appId = '22222222-2222-2222-2222-222222222222'
    displayName = 'ASRE dashboard demo 4d79'
    signInAudience = 'AzureADMyOrg'
    groupMembershipClaims = 'SecurityGroup'
    web = @{
        redirectUris = @($global:AuthOrigin + '/.auth/login/aad/callback')
        implicitGrantSettings = @{ enableIdTokenIssuance = $true; enableAccessTokenIssuance = $false }
    }
}

function global:az {
    if (($args -join ' ') -notmatch 'account get-access-token --tenant 00000002-000a-4000-9000-000000000002 --resource https://(graph.microsoft.com|vault.azure.net)') {
        throw 'Unexpected fixture Azure command; real authentication is forbidden.'
    }
    $global:LASTEXITCODE = 0
    return 'fixture-token-not-real'
}

function global:Invoke-RestMethod {
    param($Method, $Uri, $Headers, $MaximumRedirection, $ContentType, $Body)
    if ($MaximumRedirection -ne 0 -or $Headers.Authorization -ne 'Bearer fixture-token-not-real') {
        throw 'The fixture requires redirect-blocked, tenant-pinned requests.'
    }
    $parsed = if ($Body) { $Body | ConvertFrom-Json -AsHashtable } else { $null }
    $global:AuthCalls.Add(@{ method = $Method; uri = $Uri; body = $parsed })
    if ($Uri -like 'https://graph.microsoft.com/v1.0/me?*') { return @{ id = $global:AuthOwner } }
    if ($Uri -like 'https://graph.microsoft.com/v1.0/groups/*') {
        if ($Method -ne 'GET') { throw 'Group changes are forbidden.' }
        return @{ id = '00000042-000a-4000-8000-000000000042'; securityEnabled = $global:AuthGroupEnabled }
    }
    if ($Uri -eq 'https://graph.microsoft.com/v1.0/applications' -and $Method -eq 'POST') {
        if ($parsed.groupMembershipClaims -ne 'SecurityGroup' -or $parsed.signInAudience -ne 'AzureADMyOrg' -or
            $parsed.web.redirectUris[0] -ne $global:AuthApp.web.redirectUris[0]) {
            throw 'Unexpected application registration configuration.'
        }
        return $global:AuthApp
    }
    if ($Uri -like 'https://graph.microsoft.com/v1.0/servicePrincipals?*') { return @{ value = @() } }
    if ($Uri -eq 'https://graph.microsoft.com/v1.0/servicePrincipals' -and $Method -eq 'POST') {
        return @{ id = '44444444-4444-4444-4444-444444444444'; appId = $global:AuthApp.appId }
    }
    if ($Uri -eq "https://graph.microsoft.com/v1.0/applications/$($global:AuthApp.id)/addPassword") {
        return @{ keyId = '33333333-3333-3333-3333-333333333333'; secretText = $global:AuthSecret }
    }
    if ($Uri -eq 'https://kv-asre-dash-demo-4d79.vault.azure.net/secrets/entra-client-secret?api-version=7.4') {
        if ($Method -eq 'PUT' -and $parsed.value -eq 'dashboard-bootstrap-pending' -and
            $parsed.attributes.enabled -eq $false) {
            return @{ id = $global:AuthSecretUri }
        }
        if ($Method -ne 'PUT' -or $parsed.value -ne $global:AuthSecret) { throw 'Unexpected credential storage request.' }
        if ($global:AuthVaultFails) { throw "Fixture storage failure containing $global:AuthSecret" }
        return @{ id = $global:AuthSecretUri; value = $global:AuthSecret }
    }
    throw "Unexpected fixture request $Method $Uri; real HTTP is forbidden."
}

function Assert-Test {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

function Write-Outputs {
    param([string]$Phase = 'bootstrap')
    @{
        dashboard = @{
            value = @{
                phase = $Phase
                viewer_group_id = '00000042-000a-4000-8000-000000000042'
                vault_uri = 'https://kv-asre-dash-demo-4d79.vault.azure.net/'
                app_name = 'ca-asre-dash-demo-4d79'
                public_origin = $global:AuthOrigin
            }
        }
    } | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $outputPath -Encoding utf8
}

function Assert-Rejected {
    param([string]$Pattern, [switch]$Execute)
    $rejected = $false
    try { & $scriptPath -OutputsFile $outputPath -MetadataPath $metadataPath -Execute:$Execute }
    catch {
        Assert-Test ($_.Exception.Message -match $Pattern) "Unexpected rejection: $($_.Exception.Message)"
        Assert-Test ($_.Exception.Message -notlike "*$global:AuthSecret*") 'A credential leaked through the failure message.'
        $rejected = $true
    }
    Assert-Test $rejected 'The unsafe bootstrap was not rejected.'
}

try {
    Write-Outputs
    Assert-Rejected 'Use -Execute'
    Assert-Test ($global:AuthCalls.Count -eq 0) 'Bootstrap performed HTTP before execution approval.'
    Write-Outputs 'public'
    Assert-Rejected 'actual approved dashboard foundation' -Execute
    Assert-Test ($global:AuthCalls.Count -eq 0) 'Bootstrap mutated a live-app context.'
    Write-Outputs
    $global:AuthOwner = '55555555-5555-5555-5555-555555555555'
    Assert-Rejected 'approved signed-in deployment owner' -Execute
    Assert-Test (@($global:AuthCalls | Where-Object method -NE 'GET').Count -eq 0) 'The wrong operator reached a write.'
    $global:AuthOwner = '00000057-000a-4000-a000-000000000057'
    $global:AuthGroupEnabled = $false
    Assert-Rejected 'enabled security group' -Execute
    Assert-Test (@($global:AuthCalls | Where-Object method -NE 'GET').Count -eq 0) 'An invalid viewer group reached a write.'
    $global:AuthGroupEnabled = $true
    $result = & $scriptPath -OutputsFile $outputPath -MetadataPath $metadataPath -Execute
    $metadataText = Get-Content -LiteralPath $metadataPath -Raw
    $metadata = $metadataText | ConvertFrom-Json
    Assert-Test ($metadata.status -eq 'completed' -and $metadata.secret_version_uri -eq $global:AuthSecretUri) 'The stored credential reference was not recorded.'
    Assert-Test ($metadataText -notlike "*$global:AuthSecret*" -and
        $metadataText -notlike '*fixture-token*' -and ($result -join '') -notlike "*$global:AuthSecret*") 'Credential/token material reached metadata or output.'
    $writeCount = @($global:AuthCalls | Where-Object method -NE 'GET').Count
    Assert-Rejected 'credential was already issued' -Execute
    Assert-Test (@($global:AuthCalls | Where-Object method -NE 'GET').Count -eq $writeCount) 'A completed bootstrap was silently replayed.'

    $metadataPath = Join-Path $Directory 'failed-storage.json'
    $global:AuthVaultFails = $true
    Assert-Rejected 'credential response bodies are suppressed' -Execute
    $partialText = Get-Content -LiteralPath $metadataPath -Raw
    $partial = $partialText | ConvertFrom-Json
    Assert-Test ($partial.status -eq 'credential_issued_vault_write_pending' -and
        $partial.credential_key_id -eq '33333333-3333-3333-3333-333333333333' -and
        $partialText -notlike "*$global:AuthSecret*") 'Partial issuance lost its nonsecret reconciliation state.'
    $writeCount = @($global:AuthCalls | Where-Object method -NE 'GET').Count
    Assert-Rejected 'credential was already issued' -Execute
    Assert-Test (@($global:AuthCalls | Where-Object method -NE 'GET').Count -eq $writeCount) 'Uncertain storage caused another credential issuance.'
    Write-Output 'Dashboard authentication fixtures passed; no actual directory or vault request was made.'
}
finally {
    Remove-Item Function:\az
    Remove-Item Function:\Invoke-RestMethod
    Remove-Item -LiteralPath $Directory -Recurse -Force
}
