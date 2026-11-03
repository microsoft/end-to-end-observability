#Requires -Version 7.5
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'TestContext.Common.ps1')
$driver = Join-Path $PSScriptRoot '..\scripts\Invoke-BackupIncident.ps1'
$directory = Join-Path ([IO.Path]::GetTempPath()) ('asre-inert-backup-' + [guid]::NewGuid())
$state = @{
    calls = [Collections.Generic.List[object]]::new(); accounts = [Collections.Generic.List[object]]::new()
    log = [Collections.Generic.List[string]]::new()
    passed = 0; foreign = $false; busy = $false; ready = $true; context = $null; job = $null; accountSubscription = $null
}
$jobId = '00000023-000a-4000-9000-000000000023'
function New-Job($Context) {
    $vaultGroup = $Context['recovery_vault']['resource_group']
    @{ name = $jobId
        id = "/subscriptions/$($Context['subscription_id'])/resourceGroups/$vaultGroup/providers/Microsoft.RecoveryServices/vaults/$($Context['recovery_vault']['name'])/backupJobs/" + $jobId
        properties = @{ operation = 'Backup'; entityFriendlyName = $Context['targets']['backup']['name']; status = 'InProgress' } }
}
function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $state.passed++
}
function Assert-Rejected([scriptblock]$Run, [string]$Message = 'Expected explicit rejection.') {
    $rejected = $false
    try { & $Run | Out-Null } catch { $rejected = $true }
    Assert-True $rejected $Message
}
function az {
    $a = @($args)
    $global:LASTEXITCODE = 0
    if ($a[0] -ceq 'account' -and $a[1] -ceq 'show') {
        $state.log.Add('account show')
        $state.accounts.Add($a)
        $subscription = $(if ($state.accountSubscription) { $state.accountSubscription } else { $state.context['subscription_id'] })
        return (@{ id = $subscription; tenantId = $state.context['tenant_id']; state = 'Enabled' } | ConvertTo-Json -Compress)
    }
    $state.log.Add("$($a[0]) $($a[1])")
    $state.calls.Add($a)
    if ($a[0] -ceq 'backup' -and $a[1] -ceq 'job' -and $a[2] -ceq 'list') {
        if ($state.busy) { return '[{"properties":{"status":"InProgress"}}]' }
        return '[]'
    }
    if ($a[0] -ceq 'backup' -and $a[1] -ceq 'protection') {
        $copy = $state.job | ConvertTo-Json -Depth 6 | ConvertFrom-Json -AsHashtable
        if ($state.foreign) { $copy.properties.entityFriendlyName = 'foreign' }
        return $copy | ConvertTo-Json -Depth 6 -Compress
    }
    if ($a[0] -ceq 'backup' -and $a[1] -ceq 'job' -and $a[2] -ceq 'show') { return $state.job | ConvertTo-Json -Depth 6 -Compress }
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
function Reset-Log {
    $state.calls.Clear(); $state.accounts.Clear(); $state.log.Clear()
}
function Test-BackupDriver([string]$Label, [string]$ContextName) {
    $null = Use-AsreTestContext -Name $ContextName
    try {
        $context = Get-AsreContext
        $state.context = $context
        $state.job = New-Job $context
        $state.foreign = $false; $state.busy = $false; $state.ready = $true; $state.accountSubscription = $null
        Reset-Log
        $subscription = $context['subscription_id']
        $target = $context['targets']['backup']
        $group = $target['resource_group']; $vm = $target['name']
        $vault = $context['recovery_vault']['name']; $vaultGroup = $context['recovery_vault']['resource_group']
        $dir = Join-Path $directory $Label
        $unused = Join-Path $directory "$Label-whatif-must-not-create"
        & $driver -Action Trigger -EvidenceDirectory $unused -WhatIf
        Assert-True ($state.calls.Count -eq 0 -and $state.accounts.Count -eq 0 -and $state.log.Count -eq 0 -and
            -not (Test-Path -LiteralPath $unused)) "[$Label] WhatIf must not authenticate, invoke Azure or write evidence."
        Assert-Rejected { & $driver -Action Status -EvidenceDirectory $dir } "[$Label] Status without the original ID is rejected."
        Assert-Rejected { & $driver -Action Trigger -LifetimeSeconds 901 -EvidenceDirectory $dir } "[$Label] The lifetime cap holds."
        Assert-Rejected { & $driver -Action Trigger -LifetimeSeconds 179 -EvidenceDirectory $dir } "[$Label] The lifetime floor holds."
        Assert-Rejected { & $driver -Action Trigger -ScenarioId 'NOT-A-UUID' -EvidenceDirectory $dir } "[$Label] The episode ID must be a UUID."
        Assert-Rejected { & $driver -Action Trigger -EvidenceDirectory 'relative\path' } "[$Label] The evidence directory must be absolute."
        Assert-True ($state.calls.Count -eq 0 -and $state.accounts.Count -eq 0) "[$Label] Invalid inputs must stop before native access."
        $trigger = (& $driver -Action Trigger -EvidenceDirectory $dir) -join "`n" | ConvertFrom-Json
        $id = $trigger.scenario_id
        Assert-True ($trigger.native_job_id -ceq $jobId -and $trigger.status -ceq 'submitted_not_failure_or_case_proof') "[$Label] Submission is not failure/case proof."
        Assert-True ($state.calls.Count -eq 5 -and $state.calls[3][0] -ceq 'backup' -and
            $state.calls[3][1] -ceq 'protection' -and $state.calls[4][1] -ceq 'run-command') "[$Label] A real job must precede the asynchronous agent stop."
        Assert-True ($state.accounts.Count -eq 1 -and ($state.log -join '|') -ceq
            'account show|vm get-instance-view|vm run-command|backup job|backup protection|vm run-command') "[$Label] The subscription is verified once, before the first Azure operation, in a fixed order."
        Assert-True ($state.accounts[0][[array]::IndexOf($state.accounts[0], '--subscription') + 1] -ceq $subscription) "[$Label] The account check names the context subscription."
        foreach ($call in $state.calls) {
            Assert-True ($call[[array]::IndexOf($call, '--subscription') + 1] -ceq $subscription) "[$Label] Every call carries the context subscription."
            if ($call[0] -ceq 'backup') {
                Assert-True ($call[[array]::IndexOf($call, '--resource-group') + 1] -ceq $vaultGroup -and
                    $call[[array]::IndexOf($call, '--vault-name') + 1] -ceq $vault) "[$Label] Vault operations name the context vault and its group."
            } else {
                Assert-True ($call[[array]::IndexOf($call, '--resource-group') + 1] -ceq $group -and
                    $call[[array]::IndexOf($call, '--name') + 1] -ceq $vm) "[$Label] VM operations name the context backup target."
            }
        }
        $protect = $state.calls[3]
        Assert-True ($protect[[array]::IndexOf($protect, '--container-name') + 1] -ceq "IaasVMContainer;iaasvmcontainerv2;$group;$vm" -and
            $protect[[array]::IndexOf($protect, '--item-name') + 1] -ceq "VM;iaasvmcontainerv2;$group;$vm") "[$Label] The protected item is the context backup target."
        $intentPath = Join-Path $dir ('backup-' + $id + '-intent.json')
        $intent = Get-Content -LiteralPath $intentPath -Raw | ConvertFrom-Json -AsHashtable
        Assert-True ($intent.target -ceq "$subscription/$group/$vm") "[$Label] The recorded intent names the context target."
        Reset-Log
        $status = (& $driver -Action Status -ScenarioId $id -EvidenceDirectory $dir) -join "`n" | ConvertFrom-Json
        Assert-True ($state.calls.Count -eq 1 -and $state.accounts.Count -eq 1 -and $status.guest.status -ceq 'not_observed' -and
            -not $status.guest.recovery_verified) "[$Label] Active-window Status must not depend on paused RunCommand or claim recovery."
        $jobShow = $state.calls[0]
        Assert-True ($jobShow[[array]::IndexOf($jobShow, '--resource-group') + 1] -ceq $vaultGroup -and
            $jobShow[[array]::IndexOf($jobShow, '--vault-name') + 1] -ceq $vault) "[$Label] Native job status is read from the context vault."
        Reset-Log
        Assert-Rejected { & $driver -Action Reset -ScenarioId $id -EvidenceDirectory $dir } "[$Label] No active-window reset."
        Assert-Rejected { & $driver -Action Trigger -ScenarioId $id -EvidenceDirectory $dir } "[$Label] No repeated Trigger."
        Assert-True ($state.calls.Count -eq 0) "[$Label] No active-window reset or repeated Trigger may reach Azure."
        $intent.absolute_deadline = 0
        [IO.File]::WriteAllText($intentPath, ($intent | ConvertTo-Json -Depth 8))
        $status = (& $driver -Action Status -ScenarioId $id -EvidenceDirectory $dir) -join "`n" | ConvertFrom-Json
        Assert-True ($status.guest.status -ceq 'observed') "[$Label] After the original deadline, observe the real guest state."
        $reset = (& $driver -Action Reset -ScenarioId $id -EvidenceDirectory $dir) -join "`n" | ConvertFrom-Json
        Assert-True ($reset.scenario_id -ceq $id -and $reset.status -ceq 'restored') "[$Label] Reset must retain the original owner."
        $state.foreign = $true
        Reset-Log
        Assert-Rejected { & $driver -Action Trigger -EvidenceDirectory $dir } "[$Label] A foreign native job is rejected."
        Assert-True ($state.calls.Count -eq 4 -and $state.accounts.Count -eq 1) "[$Label] A foreign native job must not submit any guest fault."
        $state.foreign = $false; $state.busy = $true
        Reset-Log
        Assert-Rejected { & $driver -Action Trigger -EvidenceDirectory $dir } "[$Label] A pending backup is rejected."
        Assert-True ($state.calls.Count -eq 3 -and $state.accounts.Count -eq 1) "[$Label] A pending backup must not be duplicated or faulted again."
        $state.busy = $false; $state.ready = $false
        Reset-Log
        Assert-Rejected { & $driver -Action Reset -ScenarioId $id -EvidenceDirectory $dir } "[$Label] An unavailable agent is rejected."
        Assert-True ($state.calls.Count -eq 1 -and $state.accounts.Count -eq 1) "[$Label] Unavailable native transport must stop before RunCommand."
        $state.ready = $true
        # a signed-in account that is not the context subscription stops the run before the first operation
        $state.accountSubscription = '11111111-2222-4333-8444-555555555555'
        Reset-Log
        $other = Join-Path $directory "$Label-wrong-subscription"
        $refused = $false
        try { & $driver -Action Trigger -EvidenceDirectory $other | Out-Null } catch { $refused = $_.Exception.Message -match 'not the enabled deployment subscription' }
        Assert-True ($refused -and $state.calls.Count -eq 0 -and $state.log.Count -eq 1 -and
            -not (Test-Path -LiteralPath $other)) "[$Label] A different signed-in subscription must be refused before any operation or evidence."
        $state.accountSubscription = $null
        return $subscription
    } finally { Clear-AsreTestContext }
}
$previousContextFile = $env:ASRE_CONTEXT_FILE
try {
    $syntheticSubscription = @(Test-BackupDriver 'synthetic' 'synthetic')[-1]
    $altSubscription = @(Test-BackupDriver 'alt' 'alt')[-1]
    Assert-True ($syntheticSubscription -cne $altSubscription) 'The alternative context must differ from the synthetic one.'
    # without the test alias the real loader refuses a context made of placeholder values, WhatIf included
    $env:ASRE_CONTEXT_FILE = Join-Path $PSScriptRoot 'fixtures\context.synthetic.json'
    Reset-Log
    Assert-Rejected { & $driver -Action Trigger -EvidenceDirectory $directory -WhatIf } 'A placeholder context must be refused.'
    Assert-True ($state.log.Count -eq 0) 'A placeholder context must be refused before any Azure call.'
    $env:ASRE_CONTEXT_FILE = Join-Path $directory 'missing-context.json'
    Assert-Rejected { & $driver -Action Trigger -EvidenceDirectory $directory } 'A missing context must be refused.'
    Assert-True ($state.log.Count -eq 0) 'A missing context must be refused before any Azure call.'
    "Backup incident driver: $($state.passed) assertions passed; zero Azure calls."
} finally {
    $env:ASRE_CONTEXT_FILE = $previousContextFile
    Clear-AsreTestContext
    if ([IO.Directory]::Exists($directory)) { Remove-Item -LiteralPath $directory -Recurse -Force }
}
