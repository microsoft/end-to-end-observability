#Requires -Version 7.5
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'TestContext.Common.ps1')
$driver = Join-Path $PSScriptRoot '..\scripts\Invoke-LinuxIncident.ps1'
$workRoot = Join-Path ([IO.Path]::GetTempPath()) ('asre-inert-linux-test-' + [guid]::NewGuid())
$state = @{
    calls = [Collections.Generic.List[object]]::new(); log = [Collections.Generic.List[string]]::new()
    accounts = [Collections.Generic.List[object]]::new(); bindings = [Collections.Generic.List[object]]::new()
    failure = $false; passed = 0; context = $null; accountSubscription = $null
}
function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $state.passed++
}
function az {
    $arguments = @($args)
    $global:LASTEXITCODE = 0
    if ($arguments[0] -ceq 'account' -and $arguments[1] -ceq 'show') {
        $state.log.Add('account show')
        $state.accounts.Add($arguments)
        $subscription = $(if ($state.accountSubscription) { $state.accountSubscription } else { $state.context['subscription_id'] })
        return (@{ id = $subscription; tenantId = $state.context['tenant_id']; state = 'Enabled' } | ConvertTo-Json -Compress)
    }
    $state.log.Add("$($arguments[0]) $($arguments[1])")
    $state.calls.Add($arguments)
    if ($arguments[0] -cne 'vm' -or $arguments[1] -cne 'run-command' -or $arguments[2] -cne 'invoke') {
        throw 'Only the fixed VM operation is permitted by this inert test.'
    }
    $scriptArgument = $arguments[[array]::IndexOf($arguments, '--scripts') + 1]
    $shell = [IO.File]::ReadAllText($scriptArgument.Substring(1))
    $match = [regex]::Match($shell, 'base64\.b64decode\("([A-Za-z0-9+/=]+)"\)')
    if (-not $match.Success) { throw 'Missing sealed input.' }
    $binding = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($match.Groups[1].Value)) |
        ConvertFrom-Json -AsHashtable
    $state.bindings.Add($binding)
    $result = switch -CaseSensitive ($binding.action) {
        Trigger { @{ status = 'active'; scenario_id = $binding.request.scenario_id; expires_at = 1800000900 } }
        Reset { @{ status = 'restored'; scenario_id = $binding.request.scenario_id; recovery_owner = 'operator' } }
        Status { @{ status = 'observed'; faults_enabled = $false; owned_data_empty = $true } }
    }
    $stdout = @{ action = $binding.action; observed_at = 1800000000; result = $result } | ConvertTo-Json -Depth 8 -Compress
    $stderr = $(if ($state.failure) { 'Unit-fixture guest failure' } else { '' })
    $global:LASTEXITCODE = 0
    @{ value = @(@{ code = 'ProvisioningState/succeeded'; level = 'Info'
        message = "Enable succeeded: `n[stdout]`n$stdout`n[stderr]`n$stderr" }) } |
        ConvertTo-Json -Depth 10 -Compress
}
function Reset-Log {
    $state.calls.Clear(); $state.log.Clear(); $state.accounts.Clear()
}
function Test-LinuxDriver([string]$Label, [string]$ContextName) {
    $null = Use-AsreTestContext -Name $ContextName
    try {
        $context = Get-AsreContext
        $state.context = $context
        $state.accountSubscription = $null
        $state.failure = $false
        Reset-Log
        $subscription = $context['subscription_id']
        $target = $context['targets']['linux']
        $directory = Join-Path $workRoot "$Label-$([guid]::NewGuid())"
        Assert-True ((Get-Command az).CommandType -eq 'Function') 'Inert native command interception is required.'
        & $driver -Action Trigger -WhatIf
        Assert-True ($state.calls.Count -eq 0 -and $state.accounts.Count -eq 0 -and $state.log.Count -eq 0) "[$Label] WhatIf must not authenticate or invoke Azure."
        $trigger = (& $driver -Action Trigger -EvidenceDirectory $directory) -join "`n" | ConvertFrom-Json
        Assert-True ($trigger.result.status -ceq 'active') "[$Label] Trigger must require a matching active guest result."
        Assert-True ($state.log.Count -eq 2 -and $state.log[0] -ceq 'account show' -and $state.log[1] -ceq 'vm run-command') "[$Label] The subscription is verified once, before the first Azure operation."
        Assert-True ($state.accounts[0][[array]::IndexOf($state.accounts[0], '--subscription') + 1] -ceq $subscription) "[$Label] The account check must name the context subscription."
        $id = $trigger.result.scenario_id
        Assert-True ([guid]::Parse($id).ToString() -ceq $id) "[$Label] Trigger must generate one canonical episode ID."
        $reset = (& $driver -Action Reset -ScenarioId $id -EvidenceDirectory $directory) -join "`n" | ConvertFrom-Json
        Assert-True ($reset.result.scenario_id -ceq $id -and $reset.result.recovery_owner -ceq 'operator') "[$Label] Reset retains original ownership."
        $status = (& $driver -Action Status -EvidenceDirectory $directory) -join "`n" | ConvertFrom-Json
        Assert-True ($status.result.faults_enabled -eq $false) "[$Label] Status must expose disabled runner policy."
        $growth = (& $driver -Action Trigger -Fault CapacityGrowth -EvidenceDirectory $directory) -join "`n" | ConvertFrom-Json
        $request = $state.bindings[$state.bindings.Count - 1].request
        Assert-True ($growth.result.status -ceq 'active' -and $request.operation -ceq 'start_disk') "[$Label] Growth must reuse the existing disk operation."
        Assert-True ($request.interval_seconds -eq 1 -and $request.ttl_seconds -eq 900) "[$Label] Growth is slower, never longer."
        Assert-True ($request.byte_limit -eq 3162505216L) "[$Label] Growth retains the accepted disposable-volume byte cap."
        $count = $state.calls.Count
        $accounts = $state.accounts.Count
        $rejected = $false
        try { & $driver -Action Reset -EvidenceDirectory $directory | Out-Null } catch { $rejected = $true }
        Assert-True ($rejected -and $state.calls.Count -eq $count -and $state.accounts.Count -eq $accounts) "[$Label] Reset without original ID must stop before native access."
        $rejected = $false
        try { & $driver -Action Trigger -LifetimeSeconds 901 -EvidenceDirectory $directory | Out-Null } catch { $rejected = $true }
        Assert-True ($rejected -and $state.calls.Count -eq $count -and $state.accounts.Count -eq $accounts) "[$Label] Fault lifetime remains capped at 900 seconds."
        $state.failure = $true
        $rejected = $false
        try { & $driver -Action Trigger -EvidenceDirectory $directory | Out-Null } catch { $rejected = $true }
        Assert-True ($rejected -and $state.calls.Count -eq $count + 1) "[$Label] Guest failure must be explicit without a retry."
        $state.failure = $false
        Assert-True (@(Get-ChildItem -LiteralPath $directory -Filter '*-platform.json').Count -eq 5) "[$Label] Preserve every native outcome, including failure."
        Assert-True ($state.calls.Count -eq 5 -and $state.accounts.Count -eq 5) "[$Label] Five operations reached Azure, each after one account check."
        foreach ($call in $state.calls) {
            Assert-True ($call[[array]::IndexOf($call, '--subscription') + 1] -ceq $subscription) "[$Label] The subscription is the context subscription on every call."
            Assert-True ($call[[array]::IndexOf($call, '--name') + 1] -ceq $target['name']) "[$Label] The target is the context Linux VM."
            Assert-True ($call[[array]::IndexOf($call, '--resource-group') + 1] -ceq $target['resource_group']) "[$Label] The resource group is the context Linux group."
        }
        foreach ($account in $state.accounts) {
            Assert-True ($account[[array]::IndexOf($account, '--subscription') + 1] -ceq $subscription) "[$Label] Every account check names the context subscription."
        }
        # a signed-in account that is not the context subscription stops the run before the first operation
        Reset-Log
        $state.accountSubscription = '11111111-2222-4333-8444-555555555555'
        $before = @(Get-ChildItem -LiteralPath $directory -File).Count
        $rejected = $false
        try { & $driver -Action Status -EvidenceDirectory $directory | Out-Null } catch { $rejected = $_.Exception.Message -match 'not the enabled deployment subscription' }
        Assert-True ($rejected -and $state.calls.Count -eq 0 -and $state.log.Count -eq 1) "[$Label] A different signed-in subscription must be refused before any operation."
        $state.accountSubscription = $null
        Assert-True ($before -eq @(Get-ChildItem -LiteralPath $directory -File).Count) "[$Label] A refused run must not add evidence."
        return $subscription
    }
    finally { Clear-AsreTestContext }
}
$previousContextFile = $env:ASRE_CONTEXT_FILE
try {
    $syntheticSubscription = @(Test-LinuxDriver 'synthetic' 'synthetic')[-1]
    $altSubscription = @(Test-LinuxDriver 'alt' 'alt')[-1]
    Assert-True ($syntheticSubscription -cne $altSubscription) 'The alternative context must differ from the synthetic one.'
    # without the test alias the real loader refuses a context made of placeholder values, WhatIf included
    $env:ASRE_CONTEXT_FILE = Join-Path $PSScriptRoot 'fixtures\context.synthetic.json'
    Reset-Log
    $rejected = $false
    try { & $driver -Action Trigger -WhatIf | Out-Null } catch { $rejected = $true }
    Assert-True ($rejected -and $state.log.Count -eq 0) 'A placeholder context must be refused before any Azure call.'
    $env:ASRE_CONTEXT_FILE = Join-Path $workRoot 'missing-context.json'
    $rejected = $false
    try { & $driver -Action Status | Out-Null } catch { $rejected = $true }
    Assert-True ($rejected -and $state.log.Count -eq 0) 'A missing context must be refused before any Azure call.'
    "Linux incident driver: $($state.passed) assertions passed; zero Azure calls."
} finally {
    $env:ASRE_CONTEXT_FILE = $previousContextFile
    Clear-AsreTestContext
    if ([IO.Directory]::Exists($workRoot)) { Remove-Item -LiteralPath $workRoot -Recurse -Force }
}
