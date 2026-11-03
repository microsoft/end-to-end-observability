#Requires -Version 7.5
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = Split-Path $PSScriptRoot -Parent
$driver = Join-Path $root 'scripts\Invoke-PatchIncident.ps1'
$directory = Join-Path ([IO.Path]::GetTempPath()) ('asre-patch-driver-test-' + [guid]::NewGuid())
$null = [IO.Directory]::CreateDirectory($directory)
$fixture = @{
    mode = 'normal'
    calls = [Collections.Generic.List[object]]::new()
    bindings = [Collections.Generic.List[object]]::new()
}
$passed = 0
function Assert([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $script:passed++
}
function az {
    $a = [string[]]$args
    $fixture.calls.Add([object]$a)
    $global:LASTEXITCODE = 0
    switch ($a[1]) {
        assess-patches {
            if ($fixture.mode -ceq 'assessment-error') { $global:LASTEXITCODE = 1; return '{}' }
            $patches = @()
            if ($fixture.mode -cne 'no-pending') {
                $patches = @(@{ name = 'distro-info-data'; version = '2026.10.12-0ubuntu0.22.04.1'
                    assessmentState = 'Available'; rebootBehavior = 'NeverReboots' })
            }
            return @{ status = 'Succeeded'; assessmentActivityId = '00000020-000a-4000-8000-000000000020'
                availablePatches = $patches } | ConvertTo-Json -Depth 6 -Compress
        }
        run-command {
            $path = $a[[Array]::IndexOf($a, '--scripts') + 1].Substring(1)
            $program = Get-Content -LiteralPath $path -Raw
            $match = [regex]::Match($program, 'base64\.b64decode\("([A-Za-z0-9+/=]+)"\)')
            if (-not $match.Success) { throw 'The driver omitted its complete encoded guest binding.' }
            $binding = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($match.Groups[1].Value)) |
                ConvertFrom-Json -AsHashtable -DateKind String
            $fixture.bindings.Add($binding)
            if ($fixture.mode -ceq 'guest-unknown') { $global:LASTEXITCODE = 1; return '{}' }
            $status = switch ($binding.action) { Trigger { 'active' } Reset { 'restored' } Status { 'observed' } }
            $result = @{ action = $binding.action; result = @{ status = $status } }
            if ($binding.action -cne 'Status') { $result.result.scenario_id = $binding.request.scenario_id }
            $stderr = $(if ($fixture.mode -ceq 'guest-error') { 'unit guest failure' } else { '' })
            return @{ value = @(@{ code = 'ProvisioningState/succeeded'
                message = "Enable succeeded: `n[stdout]`n$($result | ConvertTo-Json -Depth 6 -Compress)`n[stderr]`n$stderr" }) } |
                ConvertTo-Json -Depth 10 -Compress
        }
        install-patches {
            if ($fixture.mode -ceq 'installation-unknown') { $global:LASTEXITCODE = 1; return '{}' }
            if ($fixture.mode -ceq 'no-selected-package') {
                return @{ status = 'Succeeded'; failedPatchCount = 0
                    installationActivityId = '11111111-1111-4111-8111-111111111111' } | ConvertTo-Json -Compress
            }
            return @{ status = 'Failed'; failedPatchCount = 1; installationActivityId = '11111111-1111-4111-8111-111111111111' } |
                ConvertTo-Json -Compress
        }
        default { throw "Unexpected Azure operation in the isolated test: $($a -join ' ')" }
    }
}
function Reset-Test([string]$Mode = 'normal') {
    $fixture.mode = $Mode
    $fixture.calls.Clear()
    $fixture.bindings.Clear()
}
try {
    $null = & $driver -Action Trigger -EvidenceDirectory $directory
    Assert ($fixture.calls.Count -eq 3) 'Trigger must assess, arm once and submit one selected installation.'
    $install = $fixture.calls[2]
    Assert ($install[[Array]::IndexOf($install, '--package-name-masks-to-include') + 1] -ceq
        'distro-info-data=2026.10.12-0ubuntu0.22.04.1') 'The exact assessed mask must match the actual Linux extension name=version parser.'
    Assert ($install[[Array]::IndexOf($install, '--reboot-setting') + 1] -ceq 'Never') 'The driver must not reboot.'
    Assert ($install[[Array]::IndexOf($install, '--maximum-duration') + 1] -ceq 'PT1H') 'Use the provider minimum maintenance window, not a longer guest fault lifetime.'
    Assert ($install -cnotcontains '--classifications-to-include-linux') 'Do not substitute broad patch classifications.'
    Assert ($fixture.bindings[0].request.operation -ceq 'block_patch_install') 'Use only the fixed patch fault.'
    Assert ($fixture.bindings[0].request.ttl_seconds -eq 900) 'Retain the independent 900-second ceiling.'
    Assert ($fixture.bindings[0].request.byte_limit -eq 0) 'Package failures cannot grow data or memory.'
    Assert ($fixture.bindings[0].package.version -ceq '2026.10.12-0ubuntu0.22.04.1') 'Freeze the assessed vendor version.'
    foreach ($call in $fixture.calls) {
        Assert ($call[[Array]::IndexOf($call, '--subscription') + 1] -ceq
            '00000001-000a-4000-a000-000000000001') 'Every Azure operation must pin the accepted subscription.'
        Assert ($call[[Array]::IndexOf($call, '--name') + 1] -ceq 'vm-asre-patch-cus') 'Every operation must pin the patch target.'
    }
    foreach ($mode in @('no-pending', 'assessment-error', 'guest-unknown', 'guest-error', 'installation-unknown', 'no-selected-package')) {
        Reset-Test $mode
        $failed = $false
        try { $null = & $driver -Action Trigger -EvidenceDirectory $directory } catch { $failed = $true }
        Assert $failed "The $mode path must fail explicitly."
        $expected = $(if ($mode -in @('no-pending', 'assessment-error')) { 1 }
            elseif ($mode -in @('installation-unknown', 'no-selected-package')) { 3 } else { 2 })
        Assert ($fixture.calls.Count -eq $expected) "The $mode path must neither bypass prerequisites nor retry."
    }
    Reset-Test
    $null = & $driver -Action Status -EvidenceDirectory $directory
    Assert ($fixture.calls.Count -eq 1 -and $fixture.calls[0][1] -ceq 'run-command') 'Status must not assess or install.'
    Assert ($fixture.bindings[0].request.operation -ceq 'inspect') 'Status must remain read-only.'
    Reset-Test
    $id = '00000061-000a-4000-8000-000000000061'
    $null = & $driver -Action Reset -ScenarioId $id -EvidenceDirectory $directory
    Assert ($fixture.bindings[0].request.operation -ceq 'restore_patch_install' -and
        $fixture.bindings[0].request.scenario_id -ceq $id) 'Reset must retain original ownership.'
    Reset-Test
    $failed = $false
    try { $null = & $driver -Action Reset -EvidenceDirectory $directory } catch { $failed = $true }
    Assert ($failed -and $fixture.calls.Count -eq 0) 'Reset without its original ID must not authenticate.'
    $unused = Join-Path $directory 'whatif-must-not-create'
    $null = & $driver -Action Trigger -EvidenceDirectory $unused -WhatIf
    Assert ($fixture.calls.Count -eq 0 -and -not (Test-Path -LiteralPath $unused)) 'WhatIf must have no authentication or filesystem effects.'
    "Passed $passed patch driver checks (isolated Azure fixtures; not live fault qualification)."
} finally {
    [IO.Directory]::Delete($directory, $true)
}
