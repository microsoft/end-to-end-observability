#Requires -Version 7.5
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$driver = Join-Path $PSScriptRoot '..\scripts\Invoke-BackupPolicyProbe.ps1'
$directory = Join-Path ([IO.Path]::GetTempPath()) ('asre-inert-policy-' + [guid]::NewGuid())
$subscription = '00000001-000a-4000-a000-000000000001'
$jobId = '00000035-000a-4000-a000-000000000035'
$jobPrefix = "/subscriptions/$subscription/resourceGroups/rg-asre-backup-cus/providers/Microsoft.RecoveryServices/vaults/rsv-asre-cus/backupJobs/"
$state = @{ passed = 0; events = [Collections.Generic.List[string]]::new(); azCalls = [Collections.Generic.List[object]]::new()
    fake = @{}; registered = ''; taskArgument = ''; jobId = $jobId; job = $jobPrefix + $jobId }
# The fakes run inside the driver's child scopes, which define their own $state and $jobId; they read the shared data through this alias.
$fixture = $state
function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $state.passed++
}
function Assert-Rejected([scriptblock]$Run, [string]$Pattern) {
    $message = $null
    try { & $Run | Out-Null } catch { $message = $_.Exception.Message }
    Assert-True ($null -ne $message) 'Expected explicit rejection.'
    Assert-True ($message -match $Pattern) "Rejected for the wrong reason: $message"
}
function Add-Call([string]$Name) { $fixture.events.Add($Name) }
function Set-FakeTask([bool]$Present, [string]$Name = '') { $fixture.fake.task = $Present; if ($Name) { $fixture.registered = $Name } }
function Set-FakeArgument([string]$Text) { $fixture.taskArgument = $Text }
function Get-FakeTask([string]$Name) { Add-Call GetTask; if ($fixture.fake.task) { [pscustomobject]@{ TaskName = $Name } } }
function New-AzError([string]$Text) {
    $global:LASTEXITCODE = 1
    [Management.Automation.ErrorRecord]::new([Exception]::new($Text), 'az', 'NotSpecified', $null)
}
function Start-Case {
    $state.events.Clear()
    $state.fake = @{ power = 'PowerState/running'; agent = 'Ready'; assignment = $false; task = $false; busy = $false; rpc = 1; denied = $false
        flaky = 0; shown = 0; failPut = $false; failDelete = $false; failBackup = $false }
}
function Get-Count([string]$Name) { @($state.events | Where-Object { $_ -ceq $Name }).Count }
function Get-Index([string]$Name, [int]$After = -1) {
    for ($i = $After + 1; $i -lt $state.events.Count; $i++) { if ($state.events[$i] -ceq $Name) { return $i } }
    -1
}
function Test-Mutation { @($state.events | Where-Object { $_ -cin 'PUT', 'DELETE', 'BACKUP', 'Register' }).Count -gt 0 }
function Invoke-Probe([hashtable]$Parameters) {
    try { $text = (& $driver @Parameters 6>$null) -join "`n" } catch { throw "Driver stopped ($($Parameters['Action'])): $($_.Exception.Message)" }
    if ($text) { $text | ConvertFrom-Json }
}
function Start-Sleep { }
function New-FakeCim([string]$Class) { [Microsoft.Management.Infrastructure.CimInstance]::new($Class, 'Root/Microsoft/Windows/TaskScheduler') }
function az {
    $call = @($args)
    $fixture.azCalls.Add($call)
    $global:LASTEXITCODE = 0
    $f = $fixture.fake
    $job = $fixture.job
    switch -Regex ($call -join ' ') {
        '^vm get-instance-view' {
            return @{ instanceView = @{ statuses = @(@{ code = $f.power }); vmAgent = @{ statuses = @(@{ displayStatus = $f.agent }) } } } | ConvertTo-Json -Depth 6 -Compress
        }
        '^rest --method get ' {
            if ($f.flaky -gt 0) { $f.flaky--; Add-Call 'GET:inconclusive'; return New-AzError 'ServiceUnavailable' }
            if ($f.assignment) { Add-Call 'GET:present'; return '{}' }
            Add-Call 'GET:absent'; return New-AzError '(PolicyAssignmentNotFound) The policy assignment could not be found.'
        }
        '^rest --method put ' {
            Add-Call 'PUT'
            if ($f.failPut) { return New-AzError 'simulated PUT failure' }
            $f.assignment = $true; return
        }
        '^rest --method delete ' {
            Add-Call 'DELETE'
            if ($f.failDelete) { return New-AzError 'simulated DELETE failure' }
            $f.assignment = $false; return
        }
        '^rest --method post ' { return '{"data":[{"status":"Failed","endTime":"2026-01-01T00:00:03Z"}]}' }
        '^deployment group validate' {
            if ($f.assignment -or $f.denied) { Add-Call 'CANARY:denied'; return New-AzError 'RequestDisallowedByPolicy' }
            Add-Call 'CANARY:allowed'; return
        }
        '^backup job list' { return $(if ($f.busy) { '[{"properties":{"status":"InProgress"}}]' } else { '[{"properties":{"status":"Failed"}}]' }) }
        '^resource list' {
            return @(for ($i = 0; $i -lt $f.rpc; $i++) { @{ type = 'Microsoft.Compute/restorePointCollections' } }) | ConvertTo-Json -AsArray -Compress
        }
        '^backup item list' { return '[{"name":"item","properties":{"protectionState":"Protected"}}]' }
        '^backup protection backup-now' {
            Add-Call 'BACKUP'
            if ($f.failBackup) { return New-AzError 'simulated backup-now failure' }
            return @{ id = $job; name = $fixture.jobId; properties = @{ operation = 'Backup'; status = 'InProgress' } } | ConvertTo-Json -Depth 6 -Compress
        }
        '^backup job show' {
            $f.shown++
            $status = $(if ($f.shown -lt 2) { 'InProgress' } else { 'Failed' })
            Add-Call "JOB:$status"
            return @{ id = $job; name = $fixture.jobId; properties = @{ operation = 'Backup'; status = $status; startTime = '2026-01-01T00:00:00Z'
                endTime = '2026-01-01T00:00:03Z'; errorDetails = @(@{ errorCode = 400211 }) } } | ConvertTo-Json -Depth 6 -Compress
        }
        default { throw "Unexpected az call: $($call -join ' ')" }
    }
}
# The scheduled-task fakes keep the REAL parameter binding (names, types, parameter sets, ShouldProcess); only the body is replaced.
$taskFakes = [ordered]@{
    'Get-ScheduledTask' = 'Get-FakeTask $TaskName'
    'Register-ScheduledTask' = 'Add-Call Register; Set-FakeTask $true $TaskName'
    'Unregister-ScheduledTask' = 'Add-Call Unregister; Set-FakeTask $false'
    'New-ScheduledTaskAction' = 'Set-FakeArgument $Argument; New-FakeCim MSFT_TaskAction'
    'New-ScheduledTaskTrigger' = 'New-FakeCim MSFT_TaskTrigger'
    'New-ScheduledTaskSettingsSet' = 'New-FakeCim MSFT_TaskSettings'
}
foreach ($name in $taskFakes.Keys) {
    $real = Get-Command $name -ErrorAction SilentlyContinue
    if (-not $real) { throw "$name is required to build a faithful fake (Windows ScheduledTasks module)." }
    $meta = [Management.Automation.CommandMetadata]::new($real)
    $header = [Management.Automation.ProxyCommand]::GetCmdletBindingAttribute($meta) + "`nparam(" + [Management.Automation.ProxyCommand]::GetParamBlock($meta) + ')'
    . ([scriptblock]::Create("function $name { $header`nprocess { " + $taskFakes[$name] + ' } }'))
}
try {
    foreach ($name in @('az', 'Start-Sleep') + @($taskFakes.Keys)) {
        $command = Get-Command $name
        Assert-True ($command.CommandType -ceq 'Function' -and -not $command.Module) "$name must be the local fake, not the real command."
    }
    Start-Case
    $id = [guid]::NewGuid().ToString()
    & $driver -Action Trigger -WhatIf -EvidenceDirectory $directory
    & $driver -Action Cleanup -WhatIf -ScenarioId $id -EvidenceDirectory $directory
    Assert-True ($state.events.Count -eq 0 -and -not [IO.Directory]::Exists($directory)) 'WhatIf must not call Azure, touch a task or write evidence.'

    $upper = 'AAAAAAAA-1111-4111-8111-000000000001'
    $invalid = @(
        @{ Parameters = @{ Action = 'Cleanup'; EvidenceDirectory = $directory }; Pattern = 'requires the original ScenarioId' }
        @{ Parameters = @{ Action = 'Cleanup'; ScenarioId = $id }; Pattern = 'requires the original ScenarioId' }
        @{ Parameters = @{ Action = 'Cleanup'; ScenarioId = 'not-a-uuid'; EvidenceDirectory = $directory }; Pattern = 'Guid' }
        @{ Parameters = @{ Action = 'Cleanup'; ScenarioId = $upper; EvidenceDirectory = $directory }; Pattern = 'canonical lowercase UUID' }
        @{ Parameters = @{ Action = 'Preflight'; EvidenceDirectory = 'relative\evidence' }; Pattern = 'must be absolute' }
        @{ Parameters = @{ Action = 'Preflight'; EvidenceDirectory = 'C:\' }; Pattern = 'drive root' }
        @{ Parameters = @{ Action = 'Trigger'; WatchdogMinutes = 140; EvidenceDirectory = $directory }; Pattern = 'at least 30 minutes' }
    )
    foreach ($case in $invalid) { Assert-Rejected { Invoke-Probe $case.Parameters } $case.Pattern }
    Assert-True ($state.events.Count -eq 0) 'Invalid inputs must stop before any Azure or task call.'

    Start-Case
    $pre = Invoke-Probe @{ EvidenceDirectory = $directory }
    Assert-True ($pre.status -ceq 'preflight_passed' -and -not (Test-Mutation) -and (Get-Count 'Unregister') -eq 0) 'Preflight (the default action) must be read-only.'

    $gates = @(
        @{ Set = { $state.fake.power = 'PowerState/deallocated' }; Pattern = 'VM is not running' }
        @{ Set = { $state.fake.agent = 'Not Ready' }; Pattern = 'VM is not running' }
        @{ Set = { $state.fake.assignment = $true }; Pattern = 'assignment already exists' }
        @{ Set = { $state.fake.task = $true }; Pattern = 'watchdog task already exists' }
        @{ Set = { $state.fake.busy = $true }; Pattern = 'not terminal' }
        @{ Set = { $state.fake.rpc = 0 }; Pattern = 'exactly the one expected' }
        @{ Set = { $state.fake.rpc = 2 }; Pattern = 'exactly the one expected' }
        @{ Set = { $state.fake.denied = $true }; Pattern = 'Canary baseline' }
    )
    foreach ($gate in $gates) {
        Start-Case; & $gate.Set
        Assert-Rejected { Invoke-Probe @{ Action = 'Trigger'; EvidenceDirectory = $directory } } $gate.Pattern
        Assert-True (-not (Test-Mutation)) 'A refused preflight gate must not change Azure or register the watchdog.'
    }

    Start-Case
    $run = Invoke-Probe @{ Action = 'Trigger'; ScenarioId = $id; EvidenceDirectory = $directory }
    Assert-True ($run.outcome -ceq 'terminal_Failed' -and $run.error_codes -ceq '400211' -and $run.assignment_absent -and $run.watchdog_unregistered) 'Trigger must end with the native job Failed and a verified cleanup.'
    $register = Get-Index 'Register'; $put = Get-Index 'PUT'; $denied = Get-Index 'CANARY:denied'; $backup = Get-Index 'BACKUP'
    Assert-True ($register -ge 0 -and $register -lt $put -and $put -lt $denied -and $denied -lt $backup) 'Order: watchdog, then assignment, then an observed deny, then backup-now.'
    Assert-True ((Get-Count 'BACKUP') -eq 1) 'backup-now is submitted exactly once.'
    $delete = Get-Index 'DELETE'
    $absent = Get-Index 'GET:absent' $delete; $allowed = Get-Index 'CANARY:allowed' $delete; $unregister = Get-Index 'Unregister'
    Assert-True ($delete -gt (Get-Index 'JOB:Failed') -and $delete -lt $absent -and $absent -lt $unregister -and $allowed -lt $unregister) 'Order: terminal job, delete, verified absent, canary allowed, then unregister.'
    Assert-True ($state.registered -ceq 'asre-bkp-policy-probe-watchdog' -and $state.taskArgument.EndsWith("-Action Cleanup -ScenarioId $id -EvidenceDirectory `"$directory`"")) 'The watchdog must run Cleanup for this episode.'
    $before = $state.events.Count
    Assert-Rejected { Invoke-Probe @{ Action = 'Trigger'; ScenarioId = $id; EvidenceDirectory = $directory } } 'already has an intent'
    Assert-True ($state.events.Count -eq $before) 'A repeated Trigger must not reach Azure or the task scheduler.'

    Start-Case; $state.fake.failBackup = $true
    Assert-Rejected { Invoke-Probe @{ Action = 'Trigger'; EvidenceDirectory = $directory } } 'backup-now outcome unknown'
    Assert-True ((Get-Count 'BACKUP') -eq 1 -and (Get-Count 'DELETE') -eq 1 -and -not $state.fake.assignment -and (Get-Count 'Unregister') -eq 1) 'A failed backup-now is never retried, and the assignment is still removed.'
    Start-Case; $state.fake.failPut = $true
    Assert-Rejected { Invoke-Probe @{ Action = 'Trigger'; EvidenceDirectory = $directory } } 'simulated PUT failure'
    Assert-True ((Get-Count 'BACKUP') -eq 0 -and (Get-Count 'DELETE') -ge 1 -and (Get-Count 'Unregister') -eq 1) 'A failed PUT must still attempt the delete and never submit a backup.'
    Start-Case; $state.fake.failDelete = $true
    Assert-Rejected { Invoke-Probe @{ Action = 'Trigger'; EvidenceDirectory = $directory } } 'CLEANUP NOT VERIFIED'
    Assert-True ((Get-Count 'DELETE') -eq 5 -and (Get-Count 'Unregister') -eq 0 -and $state.fake.task -and $state.fake.assignment) 'An unverified cleanup must leave the watchdog registered.'

    Start-Case; $state.fake.task = $true
    $r = Invoke-Probe @{ Action = 'Cleanup'; ScenarioId = $id; EvidenceDirectory = $directory }
    Assert-True ((Get-Count 'DELETE') -eq 0 -and (Get-Count 'Unregister') -eq 1 -and $r.assignment_absent -and $r.watchdog_unregistered) 'Cleanup with nothing to remove must only unregister the watchdog.'
    Start-Case; $state.fake.task = $true; $state.fake.assignment = $true
    $r = Invoke-Probe @{ Action = 'Cleanup'; ScenarioId = $id; EvidenceDirectory = $directory }
    Assert-True ((Get-Count 'DELETE') -eq 1 -and (Get-Index 'GET:present') -lt (Get-Index 'DELETE') -and (Get-Index 'DELETE') -lt (Get-Index 'Unregister') -and $r.assignment_absent) 'Cleanup deletes a present assignment, verifies, then unregisters.'
    Start-Case; $state.fake.task = $true; $state.fake.assignment = $true; $state.fake.flaky = 3
    $r = Invoke-Probe @{ Action = 'Cleanup'; ScenarioId = $id; EvidenceDirectory = $directory }
    Assert-True ((Get-Count 'GET:inconclusive') -eq 3 -and (Get-Count 'DELETE') -eq 1 -and $r.assignment_absent -and $r.watchdog_unregistered) 'An inconclusive lookup must still attempt the delete.'
    Start-Case; $state.fake.task = $true; $state.fake.assignment = $true; $state.fake.flaky = [int]::MaxValue; $state.fake.failDelete = $true
    Assert-Rejected { Invoke-Probe @{ Action = 'Cleanup'; ScenarioId = $id; EvidenceDirectory = $directory } } 'could not be confirmed absent'
    Assert-True ((Get-Count 'DELETE') -eq 5 -and (Get-Count 'Unregister') -eq 0 -and $state.fake.task) 'Cleanup that cannot verify removal must keep the watchdog.'

    foreach ($call in $state.azCalls) {
        Assert-True ($call[[array]::IndexOf($call, '--subscription') + 1] -ceq $subscription) 'The subscription is pinned on every az call.'
    }
    "Backup policy probe: $($state.passed) assertions passed; zero Azure calls and zero scheduled tasks."
} finally {
    if ([IO.Directory]::Exists($directory)) { Remove-Item -LiteralPath $directory -Recurse -Force }
}
