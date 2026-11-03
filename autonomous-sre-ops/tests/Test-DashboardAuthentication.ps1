$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Repo = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot 'TestContext.Common.ps1')
$syntheticPath = Use-AsreTestContext
$ctx = Get-AsreContext
$Directory = Join-Path ([IO.Path]::GetTempPath()) "asre-dashboard-auth-fixture-$([guid]::NewGuid().ToString('N'))"
$null = New-Item -ItemType Directory -Path $Directory
$scriptPath = Join-Path $Repo 'scripts\Initialize-DashboardAuthentication.ps1'
$outputPath = Join-Path $Directory 'outputs.json'
$metadataPath = Join-Path $Directory 'metadata.json'
$global:AuthCalls = [Collections.Generic.List[object]]::new()
$global:AuthGroup = '00000042-000a-4000-8000-000000000042'
$global:AuthGroupEnabled = $true
$global:AuthVaultFails = $false
$global:AuthSecret = 'fixture-value-not-a-real-credential'

# Every deployment value of the fixture comes from the context served by Use-AsreTestContext.
function Set-AuthFixture {
    param([System.Collections.IDictionary]$Context)
    $suffix = $Context.name_suffix
    $global:AuthTenant = $Context.tenant_id
    $global:AuthOwner = $Context.operator_object_id
    $global:AuthSuffix = $suffix
    $global:AuthAppName = "ca-asre-dash-$suffix"
    $global:AuthDisplayName = "ASRE dashboard $suffix"
    $global:AuthVault = "https://kv-asre-dash-$suffix.vault.azure.net/"
    $global:AuthOrigin = "https://ca-asre-dash-$suffix.example.centralus.azurecontainerapps.io"
    $global:AuthSecretUri = $global:AuthVault + 'secrets/entra-client-secret/' + ('a' * 32)
    $global:AuthApp = @{
        id = '11111111-1111-1111-1111-111111111111'
        appId = '22222222-2222-2222-2222-222222222222'
        displayName = $global:AuthDisplayName
        signInAudience = 'AzureADMyOrg'
        groupMembershipClaims = 'SecurityGroup'
        web = @{
            redirectUris = @($global:AuthOrigin + '/.auth/login/aad/callback')
            implicitGrantSettings = @{ enableIdTokenIssuance = $true; enableAccessTokenIssuance = $false }
        }
    }
}
Set-AuthFixture $ctx

function global:az {
    if (($args -join ' ') -notmatch ('account get-access-token --tenant ' + [regex]::Escape($global:AuthTenant) + ' --resource https://(graph.microsoft.com|vault.azure.net)')) {
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
        return @{ id = $global:AuthGroup; securityEnabled = $global:AuthGroupEnabled }
    }
    if ($Uri -eq 'https://graph.microsoft.com/v1.0/applications' -and $Method -eq 'POST') {
        if ($parsed.groupMembershipClaims -ne 'SecurityGroup' -or $parsed.signInAudience -ne 'AzureADMyOrg' -or
            $parsed.displayName -cne $global:AuthDisplayName -or
            @($parsed['owners@odata.bind']).Count -ne 1 -or
            @($parsed['owners@odata.bind'])[0] -cne ('https://graph.microsoft.com/v1.0/directoryObjects/' + $global:AuthOwner) -or
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
    if ($Uri -eq ($global:AuthVault + 'secrets/entra-client-secret?api-version=7.4')) {
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
                viewer_group_id = $global:AuthGroup
                vault_uri = $global:AuthVault
                app_name = $global:AuthAppName
                public_origin = $global:AuthOrigin
            }
        }
    } | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $outputPath -Encoding utf8
}

function Assert-Rejected {
    param([string]$Pattern, [switch]$Execute, [string]$ViewerGroupId = $global:AuthGroup)
    $rejected = $false
    try { & $scriptPath -OutputsFile $outputPath -MetadataPath $metadataPath -ViewerGroupId $ViewerGroupId -Execute:$Execute }
    catch {
        Assert-Test ($_.Exception.Message -match $Pattern) "Unexpected rejection: $($_.Exception.Message)"
        Assert-Test ($_.Exception.Message -notlike "*$global:AuthSecret*") 'A credential leaked through the failure message.'
        $rejected = $true
    }
    Assert-Test $rejected 'The unsafe bootstrap was not rejected.'
}

function Assert-SuccessAndReplay {
    # One approved bootstrap, then proof that its recorded state refuses a silent replay.
    $result = & $scriptPath -OutputsFile $outputPath -MetadataPath $metadataPath -ViewerGroupId $global:AuthGroup -Execute
    $metadataText = Get-Content -LiteralPath $metadataPath -Raw
    $metadata = $metadataText | ConvertFrom-Json
    Assert-Test ($metadata.status -eq 'completed' -and $metadata.secret_version_uri -eq $global:AuthSecretUri) 'The stored credential reference was not recorded.'
    Assert-Test ($metadata.tenant_id -ceq $global:AuthTenant -and $metadata.viewer_group_id -ceq $global:AuthGroup -and
        $metadata.callback -ceq ($global:AuthOrigin + '/.auth/login/aad/callback')) 'The metadata does not carry the context-derived tenant, viewer group and callback.'
    Assert-Test ($metadataText -notlike "*$global:AuthSecret*" -and
        $metadataText -notlike '*fixture-token*' -and ($result -join '') -notlike "*$global:AuthSecret*") 'Credential/token material reached metadata or output.'
    $writeCount = @($global:AuthCalls | Where-Object method -NE 'GET').Count
    Assert-Rejected 'credential was already issued' -Execute
    Assert-Test (@($global:AuthCalls | Where-Object method -NE 'GET').Count -eq $writeCount) 'A completed bootstrap was silently replayed.'
}

try {
    Write-Outputs
    Assert-Rejected 'Use -Execute'
    Assert-Test ($global:AuthCalls.Count -eq 0) 'Bootstrap performed HTTP before execution approval.'
    Write-Outputs 'public'
    Assert-Rejected 'actual approved dashboard foundation' -Execute
    Assert-Test ($global:AuthCalls.Count -eq 0) 'Bootstrap mutated a live-app context.'

    # A viewer group that differs from the foundation outputs is refused before any request.
    Write-Outputs
    Assert-Rejected 'actual approved dashboard foundation' -Execute -ViewerGroupId '00000043-000a-4000-8000-000000000043'
    Assert-Test ($global:AuthCalls.Count -eq 0) 'A viewer group that differs from the foundation outputs reached a request.'
    Assert-Test (-not (Test-Path -LiteralPath $metadataPath)) 'A mismatching viewer group created bootstrap metadata.'

    # An uppercase, trailing-newline or otherwise malformed viewer group is refused before any request.
    foreach ($malformed in @($global:AuthGroup.ToUpperInvariant(), ($global:AuthGroup + "`n"), 'not-a-group', '00000042-000a-4000-8000-00000000004')) {
        Assert-Rejected 'lowercase object id' -Execute -ViewerGroupId $malformed
    }
    Assert-Test ($global:AuthCalls.Count -eq 0) 'A malformed viewer group reached a request.'

    # A deployment context without the verified operator is refused before any request.
    $noOwnerPath = Join-Path $Directory 'context-no-owner.json'
    foreach ($variant in 'null', 'absent') {
        $document = Get-Content -LiteralPath $syntheticPath -Raw | ConvertFrom-Json -AsHashtable
        if ($variant -eq 'null') { $document['operator_object_id'] = $null } else { $document.Remove('operator_object_id') }
        $document | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $noOwnerPath -Encoding utf8
        $null = Use-AsreTestContext -Path $noOwnerPath
        try { Assert-Rejected 'needs operator_object_id' -Execute }
        finally { $null = Use-AsreTestContext -Path $syntheticPath }
        Assert-Test ($global:AuthCalls.Count -eq 0) "A context with operator_object_id $variant reached a request."
    }

    $global:AuthOwner = '55555555-5555-5555-5555-555555555555'
    Assert-Rejected 'approved signed-in deployment owner' -Execute
    Assert-Test (@($global:AuthCalls | Where-Object method -NE 'GET').Count -eq 0) 'The wrong operator reached a write.'
    $global:AuthOwner = $ctx.operator_object_id
    $global:AuthGroupEnabled = $false
    Assert-Rejected 'enabled security group' -Execute
    Assert-Test (@($global:AuthCalls | Where-Object method -NE 'GET').Count -eq 0) 'An invalid viewer group reached a write.'
    $global:AuthGroupEnabled = $true
    Assert-SuccessAndReplay

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

    # The same success and replay flow under a different deployment context proves that the helper
    # fixes no suffix, tenant, owner or viewer group of its own.
    $null = Use-AsreTestContext -Name alt
    $altCtx = Get-AsreContext
    Assert-Test ($altCtx.name_suffix -cne $ctx.name_suffix -and $altCtx.tenant_id -cne $ctx.tenant_id -and
        $altCtx.operator_object_id -cne $ctx.operator_object_id) 'The alt fixture must differ from the synthetic fixture.'
    Set-AuthFixture $altCtx
    $global:AuthGroup = '00000044-000a-4000-8000-000000000044'
    $global:AuthVaultFails = $false
    $global:AuthGroupEnabled = $true
    $global:AuthCalls.Clear()
    $metadataPath = Join-Path $Directory 'alt-metadata.json'
    Write-Outputs
    Assert-SuccessAndReplay
    $createdApp = @($global:AuthCalls | Where-Object { $_.method -eq 'POST' -and $_.uri -eq 'https://graph.microsoft.com/v1.0/applications' })
    Assert-Test ($createdApp.Count -eq 1 -and $createdApp[0].body.displayName -ceq ('ASRE dashboard ' + $altCtx.name_suffix)) 'The alt registration was not named from the alt context.'

    # Foundation outputs built for the other deployment are refused before any request.
    $callCount = $global:AuthCalls.Count
    Set-AuthFixture $ctx
    Write-Outputs
    Set-AuthFixture $altCtx
    $metadataPath = Join-Path $Directory 'alt-foreign-outputs.json'
    Assert-Rejected 'actual approved dashboard foundation' -Execute
    Assert-Test ($global:AuthCalls.Count -eq $callCount) 'Foundation outputs of another deployment reached a request.'
    Write-Output 'Dashboard authentication fixtures passed; no actual directory or vault request was made.'
}
finally {
    Remove-Item Function:\az
    Remove-Item Function:\Invoke-RestMethod
    Clear-AsreTestContext
    Remove-Item -LiteralPath $Directory -Recurse -Force
}
