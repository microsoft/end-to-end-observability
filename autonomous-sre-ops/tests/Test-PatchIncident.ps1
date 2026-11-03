#Requires -Version 7.5
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'TestContext.Common.ps1')
$root = Split-Path $PSScriptRoot -Parent
$driver = Join-Path $root 'scripts\Invoke-PatchIncident.ps1'
$directory = Join-Path ([IO.Path]::GetTempPath()) ('asre-patch-driver-test-' + [guid]::NewGuid())
$null = [IO.Directory]::CreateDirectory($directory)
$fixture = @{
    mode = 'normal'
    calls = [Collections.Generic.List[object]]::new()
    accounts = [Collections.Generic.List[object]]::new()
    log = [Collections.Generic.List[string]]::new()
    bindings = [Collections.Generic.List[object]]::new()
    context = $null
    accountSubscription = $null
    passed = 0
}
function Assert([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
    $fixture.passed++
}
function az {
    $a = [string[]]$args
    $global:LASTEXITCODE = 0
    if ($a[0] -ceq 'account' -and $a[1] -ceq 'show') {
        $fixture.log.Add('account show')
        $fixture.accounts.Add([object]$a)
        $subscription = $(if ($fixture.accountSubscription) { $fixture.accountSubscription } else { $fixture.context['subscription_id'] })
        return (@{ id = $subscription; tenantId = $fixture.context['tenant_id']; state = 'Enabled' } | ConvertTo-Json -Compress)
    }
    $fixture.log.Add("$($a[0]) $($a[1])")
    $fixture.calls.Add([object]$a)
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
    $fixture.accounts.Clear()
    $fixture.log.Clear()
    $fixture.bindings.Clear()
}
function Test-PatchDriver([string]$Label, [string]$ContextName) {
    $null = Use-AsreTestContext -Name $ContextName
    try {
        $context = Get-AsreContext
        $fixture.context = $context
        $fixture.accountSubscription = $null
        Reset-Test
        $subscription = $context['subscription_id']
        $target = $context['targets']['patch']
        $null = & $driver -Action Trigger -EvidenceDirectory $directory
        Assert ($fixture.calls.Count -eq 3) "[$Label] Trigger must assess, arm once and submit one selected installation."
        Assert ($fixture.accounts.Count -eq 1) "[$Label] The subscription is verified exactly once per run."
        Assert ($fixture.log[0] -ceq 'account show' -and $fixture.log[1] -ceq 'vm assess-patches') "[$Label] The subscription is verified before the first Azure operation."
        $install = $fixture.calls[2]
        Assert ($install[[Array]::IndexOf($install, '--package-name-masks-to-include') + 1] -ceq
            'distro-info-data=2026.10.12-0ubuntu0.22.04.1') "[$Label] The exact assessed mask must match the actual Linux extension name=version parser."
        Assert ($install[[Array]::IndexOf($install, '--reboot-setting') + 1] -ceq 'Never') "[$Label] The driver must not reboot."
        Assert ($install[[Array]::IndexOf($install, '--maximum-duration') + 1] -ceq 'PT1H') "[$Label] Use the provider minimum maintenance window, not a longer guest fault lifetime."
        Assert ($install -cnotcontains '--classifications-to-include-linux') "[$Label] Do not substitute broad patch classifications."
        Assert ($fixture.bindings[0].request.operation -ceq 'block_patch_install') "[$Label] Use only the fixed patch fault."
        Assert ($fixture.bindings[0].request.ttl_seconds -eq 900) "[$Label] Retain the independent 900-second ceiling."
        Assert ($fixture.bindings[0].request.byte_limit -eq 0) "[$Label] Package failures cannot grow data or memory."
        Assert ($fixture.bindings[0].package.version -ceq '2026.10.12-0ubuntu0.22.04.1') "[$Label] Freeze the assessed vendor version."
        foreach ($call in $fixture.calls) {
            Assert ($call[[Array]::IndexOf($call, '--subscription') + 1] -ceq $subscription) "[$Label] Every Azure operation must pin the context subscription."
            Assert ($call[[Array]::IndexOf($call, '--name') + 1] -ceq $target['name']) "[$Label] Every operation must pin the context patch target."
            Assert ($call[[Array]::IndexOf($call, '--resource-group') + 1] -ceq $target['resource_group']) "[$Label] Every operation must pin the context patch resource group."
        }
        foreach ($account in $fixture.accounts) {
            Assert ($account[[Array]::IndexOf($account, '--subscription') + 1] -ceq $subscription) "[$Label] Every account check names the context subscription."
        }
        foreach ($mode in @('no-pending', 'assessment-error', 'guest-unknown', 'guest-error', 'installation-unknown', 'no-selected-package')) {
            Reset-Test $mode
            $failed = $false
            try { $null = & $driver -Action Trigger -EvidenceDirectory $directory } catch { $failed = $true }
            Assert $failed "[$Label] The $mode path must fail explicitly."
            $expected = $(if ($mode -in @('no-pending', 'assessment-error')) { 1 }
                elseif ($mode -in @('installation-unknown', 'no-selected-package')) { 3 } else { 2 })
            Assert ($fixture.calls.Count -eq $expected) "[$Label] The $mode path must neither bypass prerequisites nor retry."
            Assert ($fixture.accounts.Count -eq 1 -and $fixture.log[0] -ceq 'account show') "[$Label] The $mode path verifies the subscription once, first."
        }
        Reset-Test
        $null = & $driver -Action Status -EvidenceDirectory $directory
        Assert ($fixture.calls.Count -eq 1 -and $fixture.calls[0][1] -ceq 'run-command') "[$Label] Status must not assess or install."
        Assert ($fixture.accounts.Count -eq 1 -and $fixture.log[0] -ceq 'account show') "[$Label] Status verifies the subscription first."
        Assert ($fixture.bindings[0].request.operation -ceq 'inspect') "[$Label] Status must remain read-only."
        Reset-Test
        $id = '00000061-000a-4000-8000-000000000061'
        $null = & $driver -Action Reset -ScenarioId $id -EvidenceDirectory $directory
        Assert ($fixture.bindings[0].request.operation -ceq 'restore_patch_install' -and
            $fixture.bindings[0].request.scenario_id -ceq $id) "[$Label] Reset must retain original ownership."
        Reset-Test
        $failed = $false
        try { $null = & $driver -Action Reset -EvidenceDirectory $directory } catch { $failed = $true }
        Assert ($failed -and $fixture.calls.Count -eq 0 -and $fixture.accounts.Count -eq 0) "[$Label] Reset without its original ID must not authenticate."
        $unused = Join-Path $directory 'whatif-must-not-create'
        $null = & $driver -Action Trigger -EvidenceDirectory $unused -WhatIf
        Assert ($fixture.calls.Count -eq 0 -and $fixture.accounts.Count -eq 0 -and $fixture.log.Count -eq 0 -and
            -not (Test-Path -LiteralPath $unused)) "[$Label] WhatIf must have no authentication or filesystem effects."
        # a signed-in account that is not the context subscription stops the run before the first operation
        Reset-Test
        $fixture.accountSubscription = '11111111-2222-4333-8444-555555555555'
        $files = @(Get-ChildItem -LiteralPath $directory -File).Count
        $refused = $false
        try { $null = & $driver -Action Trigger -EvidenceDirectory $directory } catch { $refused = $_.Exception.Message -match 'not the enabled deployment subscription' }
        Assert ($refused -and $fixture.calls.Count -eq 0 -and $fixture.log.Count -eq 1) "[$Label] A different signed-in subscription must be refused before any operation."
        Assert ($files -eq @(Get-ChildItem -LiteralPath $directory -File).Count) "[$Label] A refused run must not add evidence."
        $fixture.accountSubscription = $null
        return $subscription
    } finally { Clear-AsreTestContext }
}
$previousContextFile = $env:ASRE_CONTEXT_FILE
try {
    $syntheticSubscription = @(Test-PatchDriver 'synthetic' 'synthetic')[-1]
    $altSubscription = @(Test-PatchDriver 'alt' 'alt')[-1]
    Assert ($syntheticSubscription -cne $altSubscription) 'The alternative context must differ from the synthetic one.'
    # without the test alias the real loader refuses a context made of placeholder values, WhatIf included
    $env:ASRE_CONTEXT_FILE = Join-Path $PSScriptRoot 'fixtures\context.synthetic.json'
    Reset-Test
    $refused = $false
    try { $null = & $driver -Action Trigger -EvidenceDirectory $directory -WhatIf } catch { $refused = $true }
    Assert ($refused -and $fixture.log.Count -eq 0) 'A placeholder context must be refused before any Azure call.'
    $env:ASRE_CONTEXT_FILE = Join-Path $directory 'missing-context.json'
    $refused = $false
    try { $null = & $driver -Action Status -EvidenceDirectory $directory } catch { $refused = $true }
    Assert ($refused -and $fixture.log.Count -eq 0) 'A missing context must be refused before any Azure call.'
    "Passed $($fixture.passed) patch driver checks (isolated Azure fixtures; not live fault qualification)."
} finally {
    $env:ASRE_CONTEXT_FILE = $previousContextFile
    Clear-AsreTestContext
    [IO.Directory]::Delete($directory, $true)
}
