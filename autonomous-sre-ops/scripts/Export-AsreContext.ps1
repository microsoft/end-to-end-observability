#Requires -Version 7.5
<#
.SYNOPSIS
Saves the Terraform output 'context' as the git-ignored deployment context file used by every workstation script.

.DESCRIPTION
Reads context.value from a deployment-outputs.json (-OutputsFile) or, without it, runs
'terraform -chdir=infra output -json context'. The document is validated with the context loader, refused when it carries
synthetic test values or another subscription/tenant, and written atomically (temp file + move, UTF-8 without BOM).
An invalid document never replaces an existing file. Nothing sensitive is printed.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateScript({ [regex]::IsMatch($_, '\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\z') },
        ErrorMessage = 'SubscriptionId must be a lowercase GUID.')][string]$SubscriptionId,
    [Parameter(Mandatory)]
    [ValidateScript({ [regex]::IsMatch($_, '\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\z') },
        ErrorMessage = 'TenantId must be a lowercase GUID.')][string]$TenantId,
    [string]$OutputPath,
    [string]$OutputsFile
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'Context.Common.ps1')

$Repo = Split-Path $PSScriptRoot -Parent
if ([string]::IsNullOrWhiteSpace($OutputPath)) { $OutputPath = Join-Path $Repo 'infra\environments\context.generated.json' }
$OutputPath = [IO.Path]::GetFullPath($OutputPath)
if (-not (Test-Path -LiteralPath (Split-Path $OutputPath -Parent) -PathType Container)) {
    throw 'The OutputPath directory does not exist.'
}
$MaximumBytes = 65536

if (-not [string]::IsNullOrWhiteSpace($OutputsFile)) {
    $OutputsFile = [IO.Path]::GetFullPath($OutputsFile)
    if (-not (Test-Path -LiteralPath $OutputsFile -PathType Leaf)) { throw 'The deployment outputs file was not found.' }
    $Source = [IO.File]::ReadAllText($OutputsFile, [Text.UTF8Encoding]::new($false, $true))
    try {
        $Document = [Text.Json.JsonDocument]::Parse($Source)
        try {
            $ContextElement = [Text.Json.JsonElement]::new()
            $ValueElement = [Text.Json.JsonElement]::new()
            if ($Document.RootElement.ValueKind -ne 'Object' -or
                -not $Document.RootElement.TryGetProperty('context', [ref]$ContextElement) -or
                $ContextElement.ValueKind -ne 'Object' -or
                -not $ContextElement.TryGetProperty('value', [ref]$ValueElement) -or
                $ValueElement.ValueKind -ne 'Object') {
                throw 'shape'
            }
            $Raw = $ValueElement.GetRawText()
        }
        finally { $Document.Dispose() }
    }
    catch { throw 'The deployment outputs file has no context.value document.' }
}
else {
    $env:ARM_USE_CLI = 'true'
    $env:ARM_SUBSCRIPTION_ID = $SubscriptionId
    $env:ARM_TENANT_ID = $TenantId
    foreach ($Name in @('ARM_CLIENT_ID', 'ARM_CLIENT_SECRET', 'ARM_CLIENT_CERTIFICATE_PATH', 'ARM_CLIENT_CERTIFICATE', 'ARM_USE_OIDC',
            'ARM_OIDC_TOKEN', 'ARM_OIDC_TOKEN_FILE_PATH', 'ARM_USE_MSI', 'ARM_ACCESS_KEY', 'ARM_SAS_TOKEN', 'TF_WORKSPACE')) {
        # Remove-Item, not SetEnvironmentVariable($null): the latter leaves an empty-string variable in the process.
        Remove-Item -LiteralPath "Env:\$Name" -Force -ErrorAction SilentlyContinue
    }
    Assert-AsreSubscription -Context @{ subscription_id = $SubscriptionId; tenant_id = $TenantId }
    $Output = & terraform "-chdir=$(Join-Path $Repo 'infra')" output -json context
    if ($LASTEXITCODE -ne 0) { throw 'Terraform could not read the context output; apply first and run init in infra.' }
    $Raw = (@($Output) -join "`n")
}
if ([Text.Encoding]::UTF8.GetByteCount($Raw) -gt $MaximumBytes) { throw 'The deployment context is too large.' }
$Context = ConvertTo-AsreContext -Json $Raw
if (Test-AsreSyntheticContext -Context $Context) {
    throw 'The deployment context carries synthetic test values; export the Terraform output of the real deployment.'
}
if ($Context.subscription_id -cne $SubscriptionId -or $Context.tenant_id -cne $TenantId) {
    throw 'The deployment context does not belong to the requested subscription and tenant.'
}

$Temporary = "$OutputPath.tmp-$([guid]::NewGuid().ToString('N'))"
try {
    [IO.File]::WriteAllText($Temporary, ($Raw.TrimEnd() + "`n"), [Text.UTF8Encoding]::new($false))
    [IO.File]::Move($Temporary, $OutputPath, $true)
}
finally { if (Test-Path -LiteralPath $Temporary) { Remove-Item -LiteralPath $Temporary -Force } }

$Endpoint = if ($null -ne $Context.agent.endpoint) { 'present' } else { 'not yet deployed' }
Write-Output "Deployment context saved: $OutputPath"
Write-Output "Control group: $($Context.resource_groups.control); registry: $($Context.registry.name); key vault: $($Context.key_vault.name)"
Write-Output "Agent: $($Context.agent.name) (endpoint $Endpoint); ServiceNow: $(if ($null -ne $Context.servicenow) { 'configured' } else { 'not configured' })"
