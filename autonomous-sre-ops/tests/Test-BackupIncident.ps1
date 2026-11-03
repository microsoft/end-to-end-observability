#Requires -Version 7.5
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$driver = Join-Path $PSScriptRoot '..\scripts\Invoke-BackupIncident.ps1'
$directory = Join-Path ([IO.Path]::GetTempPath()) ('asre-inert-backup-' + [guid]::NewGuid())
$state = @{ calls = [Collections.Generic.List[object]]::new(); passed = 0; foreign = $false; busy = $false; ready = $true }
$jobId = '00000023-000a-4000-9000-000000000023'
$job = @{ name = $jobId
    id = '/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-backup-cus/providers/Microsoft.RecoveryServices/vaults/rsv-asre-cus/backupJobs/' + $jobId
    properties = @{ operation = 'Backup'; entityFriendlyName = 'vm-asre-backup-cus'; status = 'InProgress' } }
function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $state.passed++
}
function Assert-Rejected([scriptblock]$Run) {
    $rejected = $false
    try { & $Run | Out-Null } catch { $rejected = $true }
    Assert-True $rejected 'Expected explicit rejection.'
}
function az {
    $a = @($args)
    $state.calls.Add($a)
    $global:LASTEXITCODE = 0
    if ($a[0] -ceq 'backup' -and $a[1] -ceq 'job' -and $a[2] -ceq 'list') {
        if ($state.busy) { return '[{"properties":{"status":"InProgress"}}]' }
        return '[]'
    }
    if ($a[0] -ceq 'backup' -and $a[1] -ceq 'protection') {
        $copy = $job | ConvertTo-Json -Depth 6 | ConvertFrom-Json -AsHashtable
        if ($state.foreign) { $copy.properties.entityFriendlyName = 'foreign' }
        return $copy | ConvertTo-Json -Depth 6 -Compress
    }
    if ($a[0] -ceq 'backup' -and $a[1] -ceq 'job' -and $a[2] -ceq 'show') { return $job | ConvertTo-Json -Depth 6 -Compress }
    if ($a[0] -ceq 'vm' -and $a[1] -ceq 'get-instance-view') {
        return $(if ($state.ready) { '["Ready"]' } else { '["NotReady"]' })
    }
    if ($a[0] -cne 'vm' -or $a[1] -cne 'run-command') { throw 'Unexpected native fixture command.' }
    $scriptPath = $a[[array]::IndexOf($a, '--scripts') + 1].Substring(1)
    $shell = [IO.File]::ReadAllText($scriptPath)
    $match = [regex]::Match($shell, 'base64\.b64decode\("([A-Za-z0-9+/=]+)"\)')
    $binding = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($match.Groups[1].Value)) | ConvertFrom-Json
    if ($binding.action -ceq 'Trigger') {
        Assert-True ('--no-wait' -cin $a) 'Never await a stopped-agent acknowledgment before job submission.'
        Assert-True ($binding.absolute_deadline -le [DateTimeOffset]::UtcNow.AddSeconds(900).ToUnixTimeSeconds()) 'Original fault deadline is bounded.'
        Assert-True ($shell -match 'stop_guest_agent' -and $shell -match 'remaining=int') 'Fault must use the existing helper and remaining original lifetime.'
        return 'null'
    }
    Assert-True ('--no-wait' -cnotin $a) 'Guest read/reset requires complete native results.'
    $result = $(if ($binding.action -ceq 'Status') {
        @{ status = 'observed'; state = $null; health = @{ mode = 'normal'; status = 'ok' }
            faults_enabled = $false; owned_data_empty = $true }
    } else { @{ status = 'restored'; scenario_id = $binding.scenario_id; recovery_owner = 'watchdog' } })
    $stdout = @{ action = $binding.action; result = $result } | ConvertTo-Json -Depth 8 -Compress
    @{ value = @(@{ code = 'ProvisioningState/succeeded'; message = "Enable succeeded: `n[stdout]`n$stdout`n[stderr]`n" }) } |
        ConvertTo-Json -Depth 10 -Compress
}
try {
    & $driver -Action Trigger -WhatIf
    Assert-True ($state.calls.Count -eq 0) 'WhatIf must not authenticate or invoke Azure.'
    Assert-Rejected { & $driver -Action Status -EvidenceDirectory $directory }
    Assert-Rejected { & $driver -Action Trigger -LifetimeSeconds 901 -EvidenceDirectory $directory }
    Assert-True ($state.calls.Count -eq 0) 'Invalid inputs must stop before native access.'
    $trigger = (& $driver -Action Trigger -EvidenceDirectory $directory) -join "`n" | ConvertFrom-Json
    $id = $trigger.scenario_id
    Assert-True ($trigger.native_job_id -ceq $jobId -and $trigger.status -ceq 'submitted_not_failure_or_case_proof') 'Submission is not failure/case proof.'
    Assert-True ($state.calls.Count -eq 5 -and $state.calls[3][0] -ceq 'backup' -and
        $state.calls[3][1] -ceq 'protection' -and $state.calls[4][1] -ceq 'run-command') 'A real job must precede the asynchronous agent stop.'
    $before = $state.calls.Count
    $status = (& $driver -Action Status -ScenarioId $id -EvidenceDirectory $directory) -join "`n" | ConvertFrom-Json
    Assert-True ($state.calls.Count -eq $before + 1 -and $status.guest.status -ceq 'not_observed' -and
        -not $status.guest.recovery_verified) 'Active-window Status must not depend on paused RunCommand or claim recovery.'
    $before = $state.calls.Count
    Assert-Rejected { & $driver -Action Reset -ScenarioId $id -EvidenceDirectory $directory }
    Assert-Rejected { & $driver -Action Trigger -ScenarioId $id -EvidenceDirectory $directory }
    Assert-True ($state.calls.Count -eq $before) 'No active-window reset or repeated Trigger may reach Azure.'
    $intentPath = Join-Path $directory ('backup-' + $id + '-intent.json')
    $intent = Get-Content -LiteralPath $intentPath -Raw | ConvertFrom-Json -AsHashtable
    $intent.absolute_deadline = 0
    [IO.File]::WriteAllText($intentPath, ($intent | ConvertTo-Json -Depth 8))
    $status = (& $driver -Action Status -ScenarioId $id -EvidenceDirectory $directory) -join "`n" | ConvertFrom-Json
    Assert-True ($status.guest.status -ceq 'observed') 'After the original deadline, observe the real guest state.'
    $reset = (& $driver -Action Reset -ScenarioId $id -EvidenceDirectory $directory) -join "`n" | ConvertFrom-Json
    Assert-True ($reset.scenario_id -ceq $id -and $reset.status -ceq 'restored') 'Reset must retain the original owner.'
    $state.foreign = $true
    $before = $state.calls.Count
    Assert-Rejected { & $driver -Action Trigger -EvidenceDirectory $directory }
    Assert-True ($state.calls.Count -eq $before + 4) 'A foreign native job must not submit any guest fault.'
    $state.foreign = $false; $state.busy = $true
    $before = $state.calls.Count
    Assert-Rejected { & $driver -Action Trigger -EvidenceDirectory $directory }
    Assert-True ($state.calls.Count -eq $before + 3) 'A pending backup must not be duplicated or faulted again.'
    $state.busy = $false; $state.ready = $false
    $before = $state.calls.Count
    Assert-Rejected { & $driver -Action Reset -ScenarioId $id -EvidenceDirectory $directory }
    Assert-True ($state.calls.Count -eq $before + 1) 'Unavailable native transport must stop before RunCommand.'
    foreach ($call in $state.calls) {
        Assert-True ($call[[array]::IndexOf($call, '--subscription') + 1] -ceq '00000001-000a-4000-a000-000000000001') 'The subscription is fixed.'
    }
    "Backup incident driver: $($state.passed) assertions passed; zero Azure calls."
} finally {
    if ([IO.Directory]::Exists($directory)) { Remove-Item -LiteralPath $directory -Recurse -Force }
}
