#Requires -Version 7.5
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$driver = Join-Path $PSScriptRoot '..\scripts\Invoke-LinuxIncident.ps1'
$directory = Join-Path ([IO.Path]::GetTempPath()) ('asre-inert-linux-test-' + [guid]::NewGuid())
$state = @{ calls = [Collections.Generic.List[object]]::new(); bindings = [Collections.Generic.List[object]]::new(); failure = $false; passed = 0 }
function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $state.passed++
}
function az {
    $arguments = @($args)
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
try {
    Assert-True ((Get-Command az).CommandType -eq 'Function') 'Inert native command interception is required.'
    & $driver -Action Trigger -WhatIf
    Assert-True ($state.calls.Count -eq 0) 'WhatIf must not authenticate or invoke Azure.'
    $trigger = (& $driver -Action Trigger -EvidenceDirectory $directory) -join "`n" | ConvertFrom-Json
    Assert-True ($trigger.result.status -ceq 'active') 'Trigger must require a matching active guest result.'
    $id = $trigger.result.scenario_id
    Assert-True ([guid]::Parse($id).ToString() -ceq $id) 'Trigger must generate one canonical episode ID.'
    $reset = (& $driver -Action Reset -ScenarioId $id -EvidenceDirectory $directory) -join "`n" | ConvertFrom-Json
    Assert-True ($reset.result.scenario_id -ceq $id -and $reset.result.recovery_owner -ceq 'operator') 'Reset retains original ownership.'
    $status = (& $driver -Action Status -EvidenceDirectory $directory) -join "`n" | ConvertFrom-Json
    Assert-True ($status.result.faults_enabled -eq $false) 'Status must expose disabled runner policy.'
    $growth = (& $driver -Action Trigger -Fault CapacityGrowth -EvidenceDirectory $directory) -join "`n" | ConvertFrom-Json
    $request = $state.bindings[$state.bindings.Count - 1].request
    Assert-True ($growth.result.status -ceq 'active' -and $request.operation -ceq 'start_disk') 'Growth must reuse the existing disk operation.'
    Assert-True ($request.interval_seconds -eq 1 -and $request.ttl_seconds -eq 900) 'Growth is slower, never longer.'
    Assert-True ($request.byte_limit -eq 3162505216L) 'Growth retains the accepted disposable-volume byte cap.'
    $count = $state.calls.Count
    $rejected = $false
    try { & $driver -Action Reset -EvidenceDirectory $directory | Out-Null } catch { $rejected = $true }
    Assert-True ($rejected -and $state.calls.Count -eq $count) 'Reset without original ID must stop before native access.'
    $rejected = $false
    try { & $driver -Action Trigger -LifetimeSeconds 901 -EvidenceDirectory $directory | Out-Null } catch { $rejected = $true }
    Assert-True ($rejected -and $state.calls.Count -eq $count) 'Fault lifetime remains capped at 900 seconds.'
    $state.failure = $true
    $rejected = $false
    try { & $driver -Action Trigger -EvidenceDirectory $directory | Out-Null } catch { $rejected = $true }
    Assert-True ($rejected -and $state.calls.Count -eq $count + 1) 'Guest failure must be explicit without a retry.'
    Assert-True (@(Get-ChildItem -LiteralPath $directory -Filter '*-platform.json').Count -eq 5) 'Preserve every native outcome, including failure.'
    foreach ($call in $state.calls) {
        Assert-True ($call[[array]::IndexOf($call, '--subscription') + 1] -ceq '00000001-000a-4000-a000-000000000001') 'The subscription is fixed.'
        Assert-True ($call[[array]::IndexOf($call, '--name') + 1] -ceq 'vm-asre-linux-cus') 'The target is fixed.'
    }
    "Linux incident driver: $($state.passed) assertions passed; zero Azure calls."
} finally {
    if ([IO.Directory]::Exists($directory)) { Remove-Item -LiteralPath $directory -Recurse -Force }
}
