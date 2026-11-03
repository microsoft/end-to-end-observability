#Requires -Version 7.5
<#
.SYNOPSIS
Bootstraps and verifies the private, pinned-host SSH path of the runtime jobs for the three demo targets.

.DESCRIPTION
All names, ids and addresses come from the deployment context (Get-AsreContext); -OutputsFile supplies only the
deployed job names and the saved-plan target outputs. The Azure CLI account must be the context subscription and
tenant. -WhatIf stops before any VM or job operation. -ResumeTargets is the explicit approval to start stopped
or deallocated demo VMs; it is never implied.
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][string]$OutputsFile,
    [switch]$ResumeTargets
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'Context.Common.ps1')
$AsreContext = Get-AsreContext
$SubscriptionId = $AsreContext.subscription_id
$Arm = "https://management.azure.com/subscriptions/$SubscriptionId"
$Jobs = "$Arm/resourceGroups/$($AsreContext.resource_groups.control)/providers/Microsoft.App/jobs"
$Api = '?api-version=2025-07-01'
$OutputsFile = [IO.Path]::GetFullPath($OutputsFile)
$Outputs = Get-Content -LiteralPath $OutputsFile -Raw | ConvertFrom-Json
if (-not $Outputs.PSObject.Properties['runtime_jobs'] -or -not $Outputs.runtime_jobs.PSObject.Properties['value']) {
    throw 'The deployment outputs do not describe the runtime jobs.'
}
if (@($Outputs.runtime_jobs.value.PSObject.Properties).Count -eq 0) {
    Write-Output 'Runtime is deliberately disabled in this saved plan; SSH bootstrap was not attempted.'
    return
}
if ($Outputs.runtime_jobs.value.bootstrap -cne 'job-asre-bootstrap-cus' -or
    $Outputs.runtime_jobs.value.worker -cne 'job-asre-worker-cus') {
    throw 'The deployed runtime outputs do not match the fixed runtime job allowlist.'
}

function Invoke-AzJson {
    param([string[]]$Arguments)
    $json = & az @Arguments --subscription $SubscriptionId --only-show-errors --output json
    if ($LASTEXITCODE -ne 0) { throw "Runtime initialization failed: $($Arguments[0..2] -join ' ')." }
    return $json | ConvertFrom-Json
}

function Resume-DemoTargets {
    [CmdletBinding(SupportsShouldProcess)]
    param($TargetOutputs, [System.Collections.IDictionary]$Context, [switch]$Approved)
    if (-not $Approved) { return }
    Assert-AsreSubscription -Context $Context
    if (@($TargetOutputs.PSObject.Properties).Count -ne 3) {
        throw 'Target resumption requires exactly the three saved-plan target outputs.'
    }
    $states = @{}
    $ids = @{}
    foreach ($role in @('linux', 'patch', 'backup')) {
        $id = $Context.targets[$role].resource_id
        $target = $TargetOutputs.PSObject.Properties[$role]
        if (-not $target -or $target.Value.resource_id -cne $id) {
            throw "Target resumption rejected an unexpected $role resource."
        }
        $view = Invoke-AzJson -Arguments @('vm', 'get-instance-view', '--ids', $id)
        $power = @($view.instanceView.statuses | Where-Object code -like 'PowerState/*')
        if ($view.id -ne $id -or $power.Count -ne 1 -or
            $power[0].code -notin @('PowerState/running', 'PowerState/stopped', 'PowerState/deallocated')) {
            throw "The $role target has an unexpected or transitional power state; no resumption was started."
        }
        $states[$role] = $power[0].code
        $ids[$role] = $id
    }
    foreach ($role in @('linux', 'patch', 'backup')) {
        if ($states[$role] -ne 'PowerState/running') {
            if (-not $PSCmdlet.ShouldProcess($ids[$role], 'az vm start')) { continue }
            & az vm start --ids $ids[$role] --subscription $Context.subscription_id --only-show-errors --output none
            if ($LASTEXITCODE -ne 0) { throw "Approved resumption failed for the $role target." }
        }
        $view = Invoke-AzJson -Arguments @('vm', 'get-instance-view', '--ids', $ids[$role])
        $power = @($view.instanceView.statuses | Where-Object code -eq 'PowerState/running')
        if ($view.id -ne $ids[$role] -or $power.Count -ne 1) {
            throw "The $role target did not independently report Running after resumption."
        }
        Write-Output "Verified approved target resumption: $($Context.targets[$role].name)"
    }
}

function Invoke-ArmPost {
    param([string]$Uri, [object]$Body)
    $file = New-TemporaryFile
    try {
        $Body | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $file.FullName -Encoding utf8
        return Invoke-AzJson -Arguments @('rest', '--method', 'post', '--url', $Uri, '--body', "@$($file.FullName)")
    }
    finally { Remove-Item -LiteralPath $file.FullName -Force }
}

function Start-JobAndWait {
    param([string]$Job, [string]$Mode, [string]$HostTrust, [System.Collections.IDictionary]$Context = $AsreContext)
    $resource = Invoke-AzJson -Arguments @('rest', '--method', 'get', '--url', "$Jobs/$Job$Api")
    $template = $resource.properties.template
    if (@($template.containers).Count -ne 1 -or $template.containers[0].name -ne 'operations') {
        throw 'Unexpected runtime job template; no execution was started.'
    }
    if (($template.PSObject.Properties['volumes'] -and $template.volumes) -or
        ($template.PSObject.Properties['initContainers'] -and $template.initContainers) -or
        ($template.containers[0].PSObject.Properties['volumeMounts'] -and $template.containers[0].volumeMounts)) {
        throw 'The fixed runtime does not admit volumes, mounts or initialization sidecars.'
    }
    $container = $template.containers[0]
    $variables = if ($container.PSObject.Properties['env'] -and $container.env) { @($container.env) } else { @() }
    # The start request replaces the template env, so every existing variable (notably ASRE_CONTEXT) is copied intact.
    $carried = @($variables | Where-Object { $_.name -ceq 'ASRE_CONTEXT' })
    if ($carried.Count -ne 1 -or -not $carried[0].PSObject.Properties['value'] -or [string]::IsNullOrWhiteSpace($carried[0].value)) {
        throw 'The runtime job template does not carry the deployment context; no execution was started.'
    }
    try { $carriedContext = $carried[0].value | ConvertFrom-Json -AsHashtable -DateKind String }
    catch { throw 'The runtime job deployment context is unreadable; no execution was started.' }
    if ($carriedContext -isnot [System.Collections.IDictionary] -or
        -not $carriedContext.Contains('subscription_id') -or $carriedContext['subscription_id'] -cne $Context.subscription_id) {
        throw 'The runtime job deployment context belongs to another subscription; no execution was started.'
    }
    $start = @{
        containers = @(@{
            name = $container.name
            image = $container.image
            command = @($container.command)
            args = @($Mode)
            resources = @{ cpu = $container.resources.cpu; memory = $container.resources.memory }
            env = @($variables | ForEach-Object {
                if ($_.PSObject.Properties['secretRef'] -and -not [string]::IsNullOrEmpty($_.secretRef)) {
                    @{ name = $_.name; secretRef = $_.secretRef }
                }
                else { @{ name = $_.name; value = $_.value } }
            })
        })
    }
    if ($HostTrust) {
        $start.containers[0].env += @{
            name = 'ASRE_SSH_KNOWN_HOSTS_B64'
            value = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($HostTrust))
        }
    }
    $execution = Invoke-ArmPost -Uri "$Jobs/$Job/start$Api" -Body $start
    if ($execution.name -notmatch '^[a-z0-9-]+$') { throw 'The job submission did not return a valid execution identifier.' }
    $deadline = [DateTimeOffset]::UtcNow.AddMinutes(10)
    do {
        $result = Invoke-AzJson -Arguments @('rest', '--method', 'get', '--url', "$Jobs/$Job/executions/$($execution.name)$Api")
        if ($result.properties.status -eq 'Succeeded') { return $execution.name }
        if ($result.properties.status -in @('Failed', 'Stopped', 'Degraded')) {
            throw "The real $Mode execution failed: $($execution.name). Inspect its Container Apps logs."
        }
        Start-Sleep -Seconds 10
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "The real $Mode execution did not reach Succeeded within its observation deadline."
}

function Read-ConsoleMarker {
    param([string]$Prefix, [DateTimeOffset]$After)
    $workspace = "https://management.azure.com$($AsreContext.log_analytics.resource_id)"
    $deadline = [DateTimeOffset]::UtcNow.AddMinutes(6)
    do {
        $query = "ContainerAppConsoleLogs_CL | where TimeGenerated >= datetime($($After.ToString('o'))) | where Log_s startswith '$Prefix' | distinct Log_s"
        $result = Invoke-ArmPost -Uri "$workspace/api/query?api-version=2020-08-01" -Body @{query = $query; timespan = 'PT30M'}
        if (-not $result.PSObject.Properties['Tables']) { throw 'The runtime log query did not return its ARM Tables envelope.' }
        $values = @()
        $statusSeen = $false
        foreach ($table in $result.Tables) {
            $names = @($table.Columns | ForEach-Object ColumnName)
            if ($names -contains 'StatusCode') {
                $code = [Array]::IndexOf($names, 'StatusCode')
                $severity = [Array]::IndexOf($names, 'Severity')
                if ($severity -lt 0) { throw 'The runtime log query omitted its completion severity.' }
                foreach ($row in $table.Rows) {
                    if ($row[$code] -ne 0 -or $row[$severity] -lt 4) { throw 'Azure rejected or only partially completed the runtime log query.' }
                    $statusSeen = $true
                }
            }
            if ($names.Count -eq 1 -and $names[0] -eq 'Log_s') {
                $values += @($table.Rows | ForEach-Object { $_[0] })
            }
        }
        if (-not $statusSeen) { throw 'The runtime log query omitted its authoritative completion status.' }
        if ($values.Count -eq 1) { return $values[0] }
        if ($values.Count -gt 1) { throw 'Conflicting bootstrap log markers were observed; refusing ambiguous configuration.' }
        Start-Sleep -Seconds 15
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw 'The completed runtime execution has no ingested verification marker within the observation deadline.'
}

Assert-AsreSubscription -Context $AsreContext
Resume-DemoTargets -TargetOutputs $Outputs.targets.value -Context $AsreContext -Approved:$ResumeTargets
if (-not $PSCmdlet.ShouldProcess($AsreContext.resource_groups.control,
        'run the runtime SSH bootstrap: retrieve host keys, start the bootstrap and verification jobs, install the restricted public key on three targets')) {
    return
}
$known = @()
foreach ($role in @('linux', 'patch', 'backup')) {
    $target = $AsreContext.targets[$role]
    $result = Invoke-AzJson -Arguments @(
        'vm', 'run-command', 'invoke', '--resource-group', $target.resource_group,
        '--name', $target.name, '--command-id', 'RunShellScript',
        '--scripts', ('@' + (Join-Path $PSScriptRoot 'Get-RuntimeHostTrust.sh')),
        '--parameters', $target.address
    )
    $text = ($result.value | ForEach-Object message) -join "`n"
    $matches = [regex]::Matches($text, "(?m)^ASRE_SSH_HOST_KEY ($([regex]::Escape($target.address)) ssh-ed25519 [A-Za-z0-9+/=]+)\r?$")
    if ($matches.Count -ne 1) { throw "Authenticated host-key retrieval failed for $role; no SSH trust was installed." }
    $known += $matches[0].Groups[1].Value
}
$started = [DateTimeOffset]::UtcNow
$bootstrap = Start-JobAndWait -Job $Outputs.runtime_jobs.value.bootstrap -Mode bootstrap -HostTrust ($known -join "`n")
$marker = Read-ConsoleMarker -Prefix 'ASRE_RUNTIME_SSH_PUBLIC_KEY ' -After $started
$public = $marker.Substring('ASRE_RUNTIME_SSH_PUBLIC_KEY '.Length)
if ($public -notmatch '^ssh-ed25519 [A-Za-z0-9+/=]+$') { throw 'The bootstrap did not return the expected public-key contract.' }
foreach ($role in @('linux', 'patch', 'backup')) {
    $target = $AsreContext.targets[$role]
    $result = Invoke-AzJson -Arguments @(
        'vm', 'run-command', 'invoke', '--resource-group', $target.resource_group,
        '--name', $target.name, '--command-id', 'RunShellScript',
        '--scripts', ('@' + (Join-Path $PSScriptRoot 'Install-RuntimePublicKey.sh')),
        '--parameters', "public_key=$($public.Substring('ssh-ed25519 '.Length))"
    )
    if ((($result.value | ForEach-Object message) -join "`n") -notmatch '(?m)^ASRE_RUNTIME_SSH_INSTALLED\r?$') {
        throw "The restricted runtime public key was not verified on $role."
    }
}
$started = [DateTimeOffset]::UtcNow
$verification = Start-JobAndWait -Job $Outputs.runtime_jobs.value.worker -Mode verify -HostTrust ''
$marker = Read-ConsoleMarker -Prefix 'ASRE_RUNTIME_VERIFIED ' -After $started
$proof = $marker.Substring('ASRE_RUNTIME_VERIFIED '.Length) | ConvertFrom-Json
if ($proof.status -ne 'verified' -or $proof.targets -ne 3 -or $proof.mode -ne 'Review') {
    throw 'The private worker did not establish its actual independent SSH and evidence baseline.'
}
@{
    status = 'verified'
    bootstrap_execution = $bootstrap
    verification_execution = $verification
    evidence_uri = $proof.evidence_uri
    targets = 3
    mode = 'Review'
    faults_enabled = $false
    resumption_requested = [bool]$ResumeTargets
    checked_at = [DateTimeOffset]::UtcNow.ToString('o')
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path (Split-Path $OutputsFile -Parent) 'runtime-ssh-verification.json') -Encoding utf8
Write-Output "Verified private, pinned-host SSH and real evidence for three targets: $($proof.evidence_uri)"
