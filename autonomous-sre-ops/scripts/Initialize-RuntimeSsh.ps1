[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$OutputsFile,
    [switch]$ResumeTargets
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Subscription = '00000001-000a-4000-a000-000000000001'
$Group = 'rg-asre-control-cus'
$Arm = "https://management.azure.com/subscriptions/$Subscription"
$Jobs = "$Arm/resourceGroups/$Group/providers/Microsoft.App/jobs"
$Api = '?api-version=2025-07-01'
$Outputs = Get-Content -LiteralPath $OutputsFile -Raw | ConvertFrom-Json
Write-Output '##vso[task.setvariable variable=RuntimeVerified]false'
if (@($Outputs.runtime_jobs.value.PSObject.Properties).Count -eq 0) {
    Write-Output 'Runtime is deliberately disabled in this saved plan; SSH bootstrap was not attempted.'
    return
}
if ($Outputs.runtime_jobs.value.bootstrap -ne 'job-asre-bootstrap-cus' -or
    $Outputs.runtime_jobs.value.worker -ne 'job-asre-worker-cus') {
    throw 'The deployed runtime outputs do not match the fixed lab job allowlist.'
}

function Invoke-AzJson {
    param([string[]]$Arguments)
    $json = & az @Arguments --subscription $Subscription --only-show-errors --output json
    if ($LASTEXITCODE -ne 0) { throw "Runtime initialization failed: $($Arguments[0..2] -join ' ')." }
    return $json | ConvertFrom-Json
}

function Resume-DemoTargets {
    param($TargetOutputs, [switch]$Approved)
    if (-not $Approved) { return }
    if ($env:servicePrincipalId -ne '00000018-000a-4000-b000-000000000018' -or
        $env:tenantId -ne '00000002-000a-4000-9000-000000000002' -or
        [string]::IsNullOrWhiteSpace($env:idToken)) {
        throw 'Target resumption requires the approved protected Apply identity.'
    }
    if (@($TargetOutputs.PSObject.Properties).Count -ne 3) {
        throw 'Target resumption requires exactly the three saved-plan target outputs.'
    }
    $states = @{}
    $ids = @{}
    foreach ($role in @('linux', 'patch', 'backup')) {
        $id = "/subscriptions/$Subscription/resourceGroups/rg-asre-$role-cus/providers/Microsoft.Compute/virtualMachines/vm-asre-$role-cus"
        $target = $TargetOutputs.PSObject.Properties[$role]
        if (-not $target -or $target.Value.resource_id -ne $id) {
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
            & az vm start --ids $ids[$role] --subscription $Subscription --only-show-errors --output none
            if ($LASTEXITCODE -ne 0) { throw "Approved resumption failed for the $role target." }
        }
        $view = Invoke-AzJson -Arguments @('vm', 'get-instance-view', '--ids', $ids[$role])
        $power = @($view.instanceView.statuses | Where-Object code -eq 'PowerState/running')
        if ($view.id -ne $ids[$role] -or $power.Count -ne 1) {
            throw "The $role target did not independently report Running after resumption."
        }
        Write-Output "Verified approved target resumption: vm-asre-$role-cus"
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
    param([string]$Job, [string]$Mode, [string]$HostTrust)
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
    $start = @{
        containers = @(@{
            name = $container.name
            image = $container.image
            command = @($container.command)
            args = @($Mode)
            resources = @{ cpu = $container.resources.cpu; memory = $container.resources.memory }
            env = @($container.env | ForEach-Object { @{ name = $_.name; value = $_.value } })
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
    $workspace = "$Arm/resourceGroups/$Group/providers/Microsoft.OperationalInsights/workspaces/law-asre-cus"
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

$targets = @{
    linux = '10.42.0.10'
    patch = '10.42.0.11'
    backup = '10.42.0.12'
}
Resume-DemoTargets -TargetOutputs $Outputs.targets.value -Approved:$ResumeTargets
$known = @()
foreach ($role in @('linux', 'patch', 'backup')) {
    $result = Invoke-AzJson -Arguments @(
        'vm', 'run-command', 'invoke', '--resource-group', "rg-asre-$role-cus",
        '--name', "vm-asre-$role-cus", '--command-id', 'RunShellScript',
        '--scripts', ('@' + (Join-Path $PSScriptRoot 'Get-RuntimeHostTrust.sh')),
        '--parameters', $targets[$role]
    )
    $text = ($result.value | ForEach-Object message) -join "`n"
    $matches = [regex]::Matches($text, "(?m)^ASRE_SSH_HOST_KEY ($([regex]::Escape($targets[$role])) ssh-ed25519 [A-Za-z0-9+/=]+)\r?$")
    if ($matches.Count -ne 1) { throw "Authenticated host-key retrieval failed for $role; no SSH trust was installed." }
    $known += $matches[0].Groups[1].Value
}
$started = [DateTimeOffset]::UtcNow
$bootstrap = Start-JobAndWait -Job $Outputs.runtime_jobs.value.bootstrap -Mode bootstrap -HostTrust ($known -join "`n")
$marker = Read-ConsoleMarker -Prefix 'ASRE_RUNTIME_SSH_PUBLIC_KEY ' -After $started
$public = $marker.Substring('ASRE_RUNTIME_SSH_PUBLIC_KEY '.Length)
if ($public -notmatch '^ssh-ed25519 [A-Za-z0-9+/=]+$') { throw 'The bootstrap did not return the expected public-key contract.' }
foreach ($role in @('linux', 'patch', 'backup')) {
    $result = Invoke-AzJson -Arguments @(
        'vm', 'run-command', 'invoke', '--resource-group', "rg-asre-$role-cus",
        '--name', "vm-asre-$role-cus", '--command-id', 'RunShellScript',
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
Write-Output '##vso[task.setvariable variable=RuntimeVerified]true'
Write-Output "Verified private, pinned-host SSH and real evidence for three targets: $($proof.evidence_uri)"
