#Requires -Version 7.5
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'TestContext.Common.ps1')
$driver = Join-Path $PSScriptRoot '..\scripts\Invoke-BackupPolicyProbe.ps1'
$directory = Join-Path ([IO.Path]::GetTempPath()) ('asre-inert-policy-' + [guid]::NewGuid())
$jobId = '00000035-000a-4000-a000-000000000035'
$policyGroup = 'AzureBackupRG_centralus_1'
$state = @{ passed = 0; events = [Collections.Generic.List[string]]::new(); azCalls = [Collections.Generic.List[object]]::new()
    accounts = [Collections.Generic.List[object]]::new(); log = [Collections.Generic.List[string]]::new()
    fake = @{}; registered = ''; taskArgument = ''; jobId = $jobId; job = ''; context = $null; contextPath = $null; accountSubscription = $null }
# The fakes run inside the driver's child scopes, which define their own $state, $context and $jobId; they read the shared data through this alias.
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
function Reset-Calls { $state.azCalls.Clear(); $state.accounts.Clear(); $state.log.Clear() }
function Set-ProbeContext([string]$Path) {
    # The fake signed-in account and the fake native job follow the document the probe is about to read.
    $c = Get-AsreContext -Path $Path
    $state.context = $c
    $state.contextPath = [IO.Path]::GetFullPath($Path)
    $state.job = "/subscriptions/$($c['subscription_id'])/resourceGroups/$($c['recovery_vault']['resource_group'])/providers/Microsoft.RecoveryServices/vaults/$($c['recovery_vault']['name'])/backupJobs/$jobId"
    $state.accountSubscription = $null
}
function Get-Count([string]$Name) { @($state.events | Where-Object { $_ -ceq $Name }).Count }
function Get-Index([string]$Name, [int]$After = -1) {
    for ($i = $After + 1; $i -lt $state.events.Count; $i++) { if ($state.events[$i] -ceq $Name) { return $i } }
    -1
}
function Test-Mutation { @($state.events | Where-Object { $_ -cin 'PUT', 'DELETE', 'BACKUP', 'Register' }).Count -gt 0 }
function Invoke-Probe([hashtable]$Parameters) {
    $p = @{} + $Parameters
    if (-not $p.ContainsKey('ContextPath')) { $p['ContextPath'] = $state.contextPath }
    try { $text = (& $driver @p 6>$null) -join "`n" } catch { throw "Driver stopped ($($p['Action'])): $($_.Exception.Message)" }
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
    if ($call[0] -ceq 'account' -and $call[1] -ceq 'show') {
        $fixture.log.Add('account show')
        $fixture.accounts.Add($call)
        $signedIn = $(if ($fixture.accountSubscription) { $fixture.accountSubscription } else { $fixture.context['subscription_id'] })
        return (@{ id = $signedIn; tenantId = $fixture.context['tenant_id']; state = 'Enabled' } | ConvertTo-Json -Compress)
    }
    $fixture.log.Add("$($call[0]) $($call[1])")
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
function Get-Arg($Call, [string]$Name) { $Call[[array]::IndexOf($Call, $Name) + 1] }
function Assert-AzCalls([string]$Label, $Context) {
    # Every az call of the run names the document's subscription and the document's targets, never a fixed one.
    $subscription = $Context['subscription_id']
    $group = $Context['targets']['backup']['resource_group']; $vm = $Context['targets']['backup']['name']
    $vault = $Context['recovery_vault']['name']; $vaultGroup = $Context['recovery_vault']['resource_group']
    Assert-True ($state.azCalls.Count -gt 0) "[$Label] The suite must have reached Azure."
    foreach ($call in $state.azCalls) {
        $text = $call -join ' '
        Assert-True ((Get-Arg $call '--subscription') -ceq $subscription) "[$Label] The subscription is the context subscription on every az call: $($call[0]) $($call[1])."
        switch -Regex ($text) {
            '^vm ' { Assert-True ((Get-Arg $call '--resource-group') -ceq $group -and (Get-Arg $call '--name') -ceq $vm) "[$Label] VM calls name the context backup target." }
            '^backup ' {
                Assert-True ((Get-Arg $call '--resource-group') -ceq $vaultGroup -and (Get-Arg $call '--vault-name') -ceq $vault) "[$Label] Vault calls name the context vault and its group."
                if ($text -match '^backup protection') {
                    Assert-True ((Get-Arg $call '--container-name') -ceq "IaasVMContainer;iaasvmcontainerv2;$group;$vm" -and
                        (Get-Arg $call '--item-name') -ceq "VM;iaasvmcontainerv2;$group;$vm") "[$Label] The protected item is the context backup target."
                }
            }
            '^(resource list|deployment group validate)' { Assert-True ((Get-Arg $call '--resource-group') -ceq $policyGroup) "[$Label] The policy scope is the Azure Backup restore-point group." }
            '^rest --method (get|put|delete) ' {
                Assert-True ((Get-Arg $call '--url').StartsWith("https://management.azure.com/subscriptions/$subscription/resourceGroups/$policyGroup/providers/Microsoft.Authorization/policyAssignments/asre-demo-bkp-deny-probe?", [StringComparison]::Ordinal)) "[$Label] The assignment URL carries the context subscription."
            }
        }
    }
}
function Test-ProbeSuite([string]$Label, [string]$ContextName) {
    $fullPath = Use-AsreTestContext -Name $ContextName
    try {
        Set-ProbeContext $fullPath
        $context = $state.context
        $subscription = $context['subscription_id']
        $group = $context['targets']['backup']['resource_group']; $vm = $context['targets']['backup']['name']
        $dir = Join-Path $directory $Label
        Reset-Calls
        Start-Case
        $id = [guid]::NewGuid().ToString()
        & $driver -ContextPath $fullPath -Action Trigger -WhatIf -EvidenceDirectory $dir
        & $driver -ContextPath $fullPath -Action Cleanup -WhatIf -ScenarioId $id -EvidenceDirectory $dir
        Assert-True ($state.events.Count -eq 0 -and $state.azCalls.Count -eq 0 -and $state.log.Count -eq 0 -and -not [IO.Directory]::Exists($dir)) "[$Label] WhatIf must not call Azure, touch a task or write evidence."

        $upper = 'AAAAAAAA-1111-4111-8111-000000000001'
        $invalid = @(
            @{ Parameters = @{ Action = 'Cleanup'; EvidenceDirectory = $dir }; Pattern = 'requires the original ScenarioId' }
            @{ Parameters = @{ Action = 'Cleanup'; ScenarioId = $id }; Pattern = 'requires the original ScenarioId' }
            @{ Parameters = @{ Action = 'Cleanup'; ScenarioId = 'not-a-uuid'; EvidenceDirectory = $dir }; Pattern = 'Guid' }
            @{ Parameters = @{ Action = 'Cleanup'; ScenarioId = $upper; EvidenceDirectory = $dir }; Pattern = 'canonical lowercase UUID' }
            @{ Parameters = @{ Action = 'Preflight'; EvidenceDirectory = 'relative\evidence' }; Pattern = 'must be absolute' }
            @{ Parameters = @{ Action = 'Preflight'; EvidenceDirectory = 'C:\' }; Pattern = 'drive root' }
            @{ Parameters = @{ Action = 'Trigger'; WatchdogMinutes = 140; EvidenceDirectory = $dir }; Pattern = 'at least 30 minutes' }
        )
        foreach ($case in $invalid) { Assert-Rejected { Invoke-Probe $case.Parameters } $case.Pattern }
        Assert-True ($state.events.Count -eq 0 -and $state.azCalls.Count -eq 0) "[$Label] Invalid inputs must stop before any Azure or task call."

        Start-Case
        $pre = Invoke-Probe @{ EvidenceDirectory = $dir }
        Assert-True ($pre.status -ceq 'preflight_passed' -and -not (Test-Mutation) -and (Get-Count 'Unregister') -eq 0) "[$Label] Preflight (the default action) must be read-only."
        Assert-True ($state.accounts.Count -eq 1 -and $state.log[0] -ceq 'account show' -and $state.log[1] -ceq 'vm get-instance-view') "[$Label] Preflight (no gate) verifies the subscription once, before its first Azure call."

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
            Assert-Rejected { Invoke-Probe @{ Action = 'Trigger'; EvidenceDirectory = $dir } } $gate.Pattern
            Assert-True (-not (Test-Mutation)) "[$Label] A refused preflight gate must not change Azure or register the watchdog."
        }

        Start-Case
        $run = Invoke-Probe @{ Action = 'Trigger'; ScenarioId = $id; EvidenceDirectory = $dir }
        Assert-True ($run.outcome -ceq 'terminal_Failed' -and $run.error_codes -ceq '400211' -and $run.assignment_absent -and $run.watchdog_unregistered) "[$Label] Trigger must end with the native job Failed and a verified cleanup."
        $register = Get-Index 'Register'; $put = Get-Index 'PUT'; $denied = Get-Index 'CANARY:denied'; $backup = Get-Index 'BACKUP'
        Assert-True ($register -ge 0 -and $register -lt $put -and $put -lt $denied -and $denied -lt $backup) "[$Label] Order: watchdog, then assignment, then an observed deny, then backup-now."
        Assert-True ((Get-Count 'BACKUP') -eq 1) "[$Label] backup-now is submitted exactly once."
        $delete = Get-Index 'DELETE'
        $absent = Get-Index 'GET:absent' $delete; $allowed = Get-Index 'CANARY:allowed' $delete; $unregister = Get-Index 'Unregister'
        Assert-True ($delete -gt (Get-Index 'JOB:Failed') -and $delete -lt $absent -and $absent -lt $unregister -and $allowed -lt $unregister) "[$Label] Order: terminal job, delete, verified absent, canary allowed, then unregister."
        Assert-True ($state.registered -ceq 'asre-bkp-policy-probe-watchdog' -and $state.taskArgument.EndsWith("-Action Cleanup -ScenarioId $id -EvidenceDirectory `"$dir`"")) "[$Label] The watchdog must run Cleanup for this episode."
        Assert-True ($state.taskArgument.Contains("-File `"") -and $state.taskArgument.Contains("-ContextPath `"$fullPath`" -Action Cleanup ")) "[$Label] The watchdog is handed the resolved context file before the action."
        $intent = Get-Content -LiteralPath (Join-Path $dir "backup-policy-$id-intent.json") -Raw | ConvertFrom-Json -AsHashtable
        Assert-True ($intent.target -ceq "$subscription/$group/$vm" -and
            $intent.deny_scope -ceq "/subscriptions/$subscription/resourceGroups/$policyGroup") "[$Label] The recorded intent names the context target and the policy scope."
        $argQuery = Get-Content -LiteralPath (Join-Path $dir "backup-policy-$id-arg-query.json") -Raw | ConvertFrom-Json -AsHashtable
        Assert-True (@($argQuery.subscriptions).Count -eq 1 -and $argQuery.subscriptions[0] -ceq $subscription) "[$Label] The Resource Graph query is scoped to the context subscription."
        Assert-AzCalls $Label $context
        $before = $state.events.Count
        Assert-Rejected { Invoke-Probe @{ Action = 'Trigger'; ScenarioId = $id; EvidenceDirectory = $dir } } 'already has an intent'
        Assert-True ($state.events.Count -eq $before) "[$Label] A repeated Trigger must not reach Azure or the task scheduler."

        Start-Case; $state.fake.failBackup = $true
        Assert-Rejected { Invoke-Probe @{ Action = 'Trigger'; EvidenceDirectory = $dir } } 'backup-now outcome unknown'
        Assert-True ((Get-Count 'BACKUP') -eq 1 -and (Get-Count 'DELETE') -eq 1 -and -not $state.fake.assignment -and (Get-Count 'Unregister') -eq 1) "[$Label] A failed backup-now is never retried, and the assignment is still removed."
        Start-Case; $state.fake.failPut = $true
        Assert-Rejected { Invoke-Probe @{ Action = 'Trigger'; EvidenceDirectory = $dir } } 'simulated PUT failure'
        Assert-True ((Get-Count 'BACKUP') -eq 0 -and (Get-Count 'DELETE') -ge 1 -and (Get-Count 'Unregister') -eq 1) "[$Label] A failed PUT must still attempt the delete and never submit a backup."
        Start-Case; $state.fake.failDelete = $true
        Assert-Rejected { Invoke-Probe @{ Action = 'Trigger'; EvidenceDirectory = $dir } } 'CLEANUP NOT VERIFIED'
        Assert-True ((Get-Count 'DELETE') -eq 5 -and (Get-Count 'Unregister') -eq 0 -and $state.fake.task -and $state.fake.assignment) "[$Label] An unverified cleanup must leave the watchdog registered."

        Start-Case; $state.fake.task = $true
        $r = Invoke-Probe @{ Action = 'Cleanup'; ScenarioId = $id; EvidenceDirectory = $dir }
        Assert-True ((Get-Count 'DELETE') -eq 0 -and (Get-Count 'Unregister') -eq 1 -and $r.assignment_absent -and $r.watchdog_unregistered) "[$Label] Cleanup with nothing to remove must only unregister the watchdog."
        Start-Case; $state.fake.task = $true; $state.fake.assignment = $true
        $r = Invoke-Probe @{ Action = 'Cleanup'; ScenarioId = $id; EvidenceDirectory = $dir }
        Assert-True ((Get-Count 'DELETE') -eq 1 -and (Get-Index 'GET:present') -lt (Get-Index 'DELETE') -and (Get-Index 'DELETE') -lt (Get-Index 'Unregister') -and $r.assignment_absent) "[$Label] Cleanup deletes a present assignment, verifies, then unregisters."
        Start-Case; $state.fake.task = $true; $state.fake.assignment = $true; $state.fake.flaky = 3
        $r = Invoke-Probe @{ Action = 'Cleanup'; ScenarioId = $id; EvidenceDirectory = $dir }
        Assert-True ((Get-Count 'GET:inconclusive') -eq 3 -and (Get-Count 'DELETE') -eq 1 -and $r.assignment_absent -and $r.watchdog_unregistered) "[$Label] An inconclusive lookup must still attempt the delete."
        Start-Case; $state.fake.task = $true; $state.fake.assignment = $true; $state.fake.flaky = [int]::MaxValue; $state.fake.failDelete = $true
        Assert-Rejected { Invoke-Probe @{ Action = 'Cleanup'; ScenarioId = $id; EvidenceDirectory = $dir } } 'could not be confirmed absent'
        Assert-True ((Get-Count 'DELETE') -eq 5 -and (Get-Count 'Unregister') -eq 0 -and $state.fake.task) "[$Label] Cleanup that cannot verify removal must keep the watchdog."

        # A signed-in account that is not the context subscription stops every action before its first operation.
        foreach ($action in @('Preflight', 'Trigger', 'Cleanup')) {
            Start-Case; Reset-Calls
            $state.accountSubscription = '11111111-2222-4333-8444-555555555555'
            $other = Join-Path $directory "$Label-wrong-$action"
            $parameters = @{ Action = $action; EvidenceDirectory = $other }
            if ($action -ceq 'Cleanup') { $parameters['ScenarioId'] = $id }
            Assert-Rejected { Invoke-Probe $parameters } 'not the enabled deployment subscription'
            Assert-True ($state.azCalls.Count -eq 1 -and $state.events.Count -eq 0 -and -not (Test-Path -LiteralPath $other)) "[$Label] A different signed-in subscription stops $action before any operation or evidence."
            $state.accountSubscription = $null
        }
        return $subscription
    } finally { Clear-AsreTestContext }
}
$previousContextFile = $env:ASRE_CONTEXT_FILE
$syntheticFile = Join-Path $PSScriptRoot 'fixtures\context.synthetic.json'
$altFile = Join-Path $PSScriptRoot 'fixtures\context.alt.json'
try {
    foreach ($name in @('az', 'Start-Sleep') + @($taskFakes.Keys)) {
        $command = Get-Command $name
        Assert-True ($command.CommandType -ceq 'Function' -and -not $command.Module) "$name must be the local fake, not the real command."
    }
    $syntheticSubscription = @(Test-ProbeSuite 'synthetic' 'synthetic')[-1]
    $altSubscription = @(Test-ProbeSuite 'alt' 'alt')[-1]
    Assert-True ($syntheticSubscription -cne $altSubscription) 'The alternative context must differ from the synthetic one.'

    # Path resolution: -ContextPath, else ASRE_CONTEXT_FILE, else the generated file of the infrastructure folder.
    $null = Use-AsreTestContext -Name synthetic
    try {
        $dir = Join-Path $directory 'resolution'
        $env:ASRE_CONTEXT_FILE = $altFile
        Set-ProbeContext $altFile; Start-Case; Reset-Calls
        $pre = Invoke-Probe @{ EvidenceDirectory = $dir; ContextPath = '' }
        Assert-True ($pre.status -ceq 'preflight_passed' -and $state.azCalls[0][[array]::IndexOf($state.azCalls[0], '--subscription') + 1] -ceq $state.context['subscription_id']) 'Without -ContextPath the probe reads ASRE_CONTEXT_FILE.'
        Set-ProbeContext $syntheticFile; Start-Case; Reset-Calls
        $pre = Invoke-Probe @{ EvidenceDirectory = $dir; ContextPath = $syntheticFile }
        Assert-True ($pre.status -ceq 'preflight_passed' -and $state.azCalls[0][[array]::IndexOf($state.azCalls[0], '--subscription') + 1] -ceq $state.context['subscription_id']) 'An explicit -ContextPath beats ASRE_CONTEXT_FILE.'
        Start-Case
        $id = [guid]::NewGuid().ToString()
        $env:ASRE_CONTEXT_FILE = $altFile
        Set-ProbeContext $altFile
        $null = Invoke-Probe @{ Action = 'Trigger'; ScenarioId = $id; EvidenceDirectory = $dir; ContextPath = '' }
        Assert-True ($state.taskArgument.Contains("-ContextPath `"$([IO.Path]::GetFullPath($altFile))`" -Action Cleanup ")) 'The watchdog receives the context file resolved from the environment as an absolute path.'
        $env:ASRE_CONTEXT_FILE = $null
        $generated = Join-Path (Split-Path $PSScriptRoot -Parent) 'infra\environments\context.generated.json'
        if (-not [IO.File]::Exists($generated)) {
            Reset-Calls
            Assert-Rejected { Invoke-Probe @{ EvidenceDirectory = $dir; ContextPath = '' } } 'context\.generated\.json'
            Assert-True ($state.azCalls.Count -eq 0) 'Without a file the probe falls back to the generated context and stops before any Azure call.'
        }
        $env:ASRE_CONTEXT_FILE = Join-Path $directory 'missing-context.json'
        Reset-Calls
        Assert-Rejected { Invoke-Probe @{ EvidenceDirectory = $dir; ContextPath = '' } } 'was not found'
        Assert-True ($state.azCalls.Count -eq 0) 'A missing context file stops before any Azure call.'
    } finally { Clear-AsreTestContext }

    # Without the test alias the real loader refuses a context made of placeholder values, and no action reaches Azure.
    $env:ASRE_CONTEXT_FILE = $null
    Reset-Calls; Start-Case
    foreach ($action in @('Preflight', 'Trigger')) {
        Assert-Rejected { & $driver -ContextPath $syntheticFile -Action $action -EvidenceDirectory $directory -WhatIf } 'synthetic test values'
    }
    Assert-Rejected { & $driver -ContextPath $syntheticFile -Action Cleanup -ScenarioId ([guid]::NewGuid().ToString()) -EvidenceDirectory $directory } 'synthetic test values'
    Assert-True ($state.azCalls.Count -eq 0 -and $state.events.Count -eq 0) 'A placeholder context must be refused before any Azure or task call.'
    "Backup policy probe: $($state.passed) assertions passed; zero Azure calls and zero scheduled tasks."
} finally {
    $env:ASRE_CONTEXT_FILE = $previousContextFile
    Clear-AsreTestContext
    if ([IO.Directory]::Exists($directory)) { Remove-Item -LiteralPath $directory -Recurse -Force }
}
