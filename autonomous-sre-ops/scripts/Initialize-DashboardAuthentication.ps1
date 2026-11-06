#Requires -Version 7.5
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$OutputsFile,
    [Parameter(Mandatory)][string]$MetadataPath,
    [Parameter(Mandatory)][string]$ViewerGroupId,
    [switch]$Execute
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'Context.Common.ps1')
$Context = Get-AsreContext
$Uuid = '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
if ($ViewerGroupId -cnotmatch $Uuid -or $ViewerGroupId.Length -ne 36) {
    throw '-ViewerGroupId must be the lowercase object id (GUID) of the Entra viewer security group.'
}
$Tenant = $Context.tenant_id
$Owner = $Context.operator_object_id
if ([string]::IsNullOrWhiteSpace($Owner)) {
    throw 'The dashboard bootstrap needs operator_object_id in the deployment context (the verified deployment owner).'
}
$Group = $ViewerGroupId
$suffix = $Context.name_suffix
$Name = "ASRE dashboard $suffix"
$AppName = "ca-asre-dash-$suffix"
$Vault = "https://kv-asre-dash-$suffix.vault.azure.net/"
$OriginPattern = '^https://' + [regex]::Escape($AppName) + '\.[a-z0-9-]+\.centralus\.azurecontainerapps\.io$'
$SecretPattern = '^' + [regex]::Escape($Vault) + 'secrets/entra-client-secret/[0-9a-f]{32}$'

function Get-Token {
    param([string]$Resource)
    # --tenant pins the token to the tenant; az rejects it together with --subscription, so this call is deliberately not subscription-scoped.
    $value = & az account get-access-token --tenant $Tenant --resource $Resource --query accessToken --output tsv --only-show-errors
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($value)) {
        throw 'Tenant-pinned operator authentication failed.'
    }
    return $value
}

function Invoke-Request {
    param([string]$Method, [string]$Uri, [string]$Token, [object]$Body)
    if (-not ($Uri.StartsWith('https://graph.microsoft.com/v1.0/', [StringComparison]::Ordinal) -or
        $Uri.StartsWith($Vault + 'secrets/entra-client-secret?', [StringComparison]::Ordinal))) {
        throw 'A bootstrap request is outside the approved directory/vault endpoints.'
    }
    $arguments = @{
        Method = $Method
        Uri = $Uri
        Headers = @{ Authorization = "Bearer $Token" }
        MaximumRedirection = 0
    }
    if ($null -ne $Body) {
        $arguments.ContentType = 'application/json'
        $arguments.Body = $Body | ConvertTo-Json -Depth 12 -Compress
    }
    try {
        return Invoke-RestMethod @arguments
    }
    catch {
        $status = if ($_.Exception.PSObject.Properties['Response'] -and $_.Exception.Response) {
            [int]$_.Exception.Response.StatusCode
        } else { 'transport' }
        throw "Dashboard authentication $Method request failed ($status). Inspect directory/vault audit logs; credential response bodies are suppressed."
    }
}

function Save-Metadata {
    param([object]$Value)
    $Value | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $MetadataPath -Encoding utf8
}

if (-not $Execute) {
    throw 'This performs directory and credential writes. Use -Execute only after deployment approval and the reviewed foundation apply.'
}
$outputs = Get-Content -LiteralPath $OutputsFile -Raw | ConvertFrom-Json
$dashboard = $outputs.dashboard.value
if ($dashboard.phase -ne 'bootstrap' -or $dashboard.viewer_group_id -ne $Group -or
    $dashboard.vault_uri -cne $Vault -or $dashboard.app_name -cne $AppName -or
    $dashboard.public_origin -cnotmatch $OriginPattern) {
    throw 'Use the actual approved dashboard foundation outputs, not a live-app or unrelated deployment.'
}
$MetadataPath = [IO.Path]::GetFullPath($MetadataPath)
if (-not (Test-Path -LiteralPath (Split-Path $MetadataPath -Parent) -PathType Container)) {
    throw 'The nonsecret metadata directory must already exist.'
}
$callback = $dashboard.public_origin + '/.auth/login/aad/callback'
$graphToken = $null
$vaultToken = $null
$credential = $null
try {
    $graphToken = Get-Token 'https://graph.microsoft.com'
    $me = Invoke-Request GET 'https://graph.microsoft.com/v1.0/me?$select=id' $graphToken
    if ($me.id -ne $Owner) { throw 'Only the approved signed-in deployment owner can bootstrap this registration.' }
    $groupRecord = Invoke-Request GET "https://graph.microsoft.com/v1.0/groups/${Group}?`$select=id,securityEnabled" $graphToken
    if ($groupRecord.id -ne $Group -or $groupRecord.securityEnabled -ne $true) {
        throw 'The approved viewer object is not an enabled security group in this tenant.'
    }
    if (Test-Path -LiteralPath $MetadataPath) {
        $metadata = Get-Content -LiteralPath $MetadataPath -Raw | ConvertFrom-Json -AsHashtable
        if ($metadata.tenant_id -ne $Tenant -or $metadata.viewer_group_id -ne $Group -or
            $metadata.callback -cne $callback -or $metadata.application_object_id -cnotmatch $Uuid) {
            throw 'The existing bootstrap metadata does not match this dashboard.'
        }
        if ($metadata.credential_key_id) {
            throw 'A credential was already issued. Reuse completed metadata, or resolve the recorded partial issuance before retrying; no credential is silently replaced.'
        }
        $app = Invoke-Request GET "https://graph.microsoft.com/v1.0/applications/$($metadata.application_object_id)" $graphToken
        $owners = Invoke-Request GET "https://graph.microsoft.com/v1.0/applications/$($app.id)/owners?`$select=id" $graphToken
        if ($Owner -notin @($owners.value.id)) { throw 'The existing registration is not owned by the approved operator.' }
    }
    else {
        $app = Invoke-Request POST 'https://graph.microsoft.com/v1.0/applications' $graphToken @{
            displayName = $Name
            signInAudience = 'AzureADMyOrg'
            groupMembershipClaims = 'SecurityGroup'
            'owners@odata.bind' = @("https://graph.microsoft.com/v1.0/directoryObjects/$Owner")
            web = @{
                redirectUris = @($callback)
                implicitGrantSettings = @{
                    enableIdTokenIssuance = $true
                    enableAccessTokenIssuance = $false
                }
            }
        }
        $metadata = @{
            tenant_id = $Tenant
            viewer_group_id = $Group
            callback = $callback
            application_object_id = $app.id
            client_id = $app.appId
            credential_key_id = ''
            secret_version_uri = ''
            status = 'registration_created'
        }
        Save-Metadata $metadata
    }
    if ($app.id -cnotmatch $Uuid -or $app.appId -cnotmatch $Uuid -or
        $app.displayName -cne $Name -or $app.signInAudience -cne 'AzureADMyOrg' -or
        $app.groupMembershipClaims -cne 'SecurityGroup' -or
        @($app.web.redirectUris).Count -ne 1 -or $app.web.redirectUris[0] -cne $callback -or
        $app.web.implicitGrantSettings.enableIdTokenIssuance -ne $true -or
        $app.web.implicitGrantSettings.enableAccessTokenIssuance -ne $false -or
        $metadata.client_id -ne $app.appId) {
        throw 'The owned Entra registration does not match the exact single-tenant group/callback configuration.'
    }
    $principals = Invoke-Request GET "https://graph.microsoft.com/v1.0/servicePrincipals?`$filter=appId%20eq%20'$($app.appId)'&`$select=id,appId" $graphToken
    if (@($principals.value).Count -gt 1) { throw 'The registration has ambiguous enterprise application records.' }
    if (@($principals.value).Count -eq 0) {
        $null = Invoke-Request POST 'https://graph.microsoft.com/v1.0/servicePrincipals' $graphToken @{ appId = $app.appId }
    }
    # Test newly propagated set-only access before issuing a real credential.
    $vaultToken = Get-Token 'https://vault.azure.net'
    $null = Invoke-Request PUT ($Vault + 'secrets/entra-client-secret?api-version=7.4') $vaultToken @{
        value = 'dashboard-bootstrap-pending'
        contentType = 'Disabled bootstrap access check; not a credential'
        attributes = @{ enabled = $false }
    }
    $expiry = [DateTimeOffset]::UtcNow.AddDays(90)
    $credential = Invoke-Request POST "https://graph.microsoft.com/v1.0/applications/$($app.id)/addPassword" $graphToken @{
        passwordCredential = @{ displayName = 'dashboard-native-sign-in'; endDateTime = $expiry.ToString('o') }
    }
    if ($credential.keyId -cnotmatch $Uuid -or [string]::IsNullOrWhiteSpace($credential.secretText)) {
        throw 'Entra did not return a valid issued credential; no vault write was attempted.'
    }
    $metadata.credential_key_id = $credential.keyId
    $metadata.expires_utc = $expiry.ToString('o')
    $metadata.status = 'credential_issued_vault_write_pending'
    Save-Metadata $metadata
    $stored = Invoke-Request PUT ($Vault + 'secrets/entra-client-secret?api-version=7.4') $vaultToken @{
        value = $credential.secretText
        contentType = 'Entra native dashboard sign-in'
        attributes = @{ enabled = $true; exp = $expiry.ToUnixTimeSeconds() }
    }
    if ($stored.id -cnotmatch $SecretPattern) {
        throw 'The vault did not return the expected exact versioned secret URI; inspect recorded issuance before retrying.'
    }
    $metadata.secret_version_uri = $stored.id
    $metadata.status = 'completed'
    Save-Metadata $metadata
    Write-Output "Dashboard authentication initialized; only nonsecret IDs and the versioned reference were saved to $MetadataPath."
}
finally {
    $graphToken = $null
    $vaultToken = $null
    $credential = $null
    $stored = $null
}
