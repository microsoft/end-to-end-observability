$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Repo = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot 'TestContext.Common.ps1')
$global:AssertionCount = 0
$Roles = @('linux', 'patch', 'backup')

$null = Use-AsreTestContext -Name alt
$AltContext = Get-AsreContext
$null = Use-AsreTestContext
$AsreContext = Get-AsreContext

$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    (Join-Path $Repo 'scripts\Initialize-RuntimeSsh.ps1'), [ref]$tokens, [ref]$errors
)
if ($errors.Count) { throw 'The runtime initializer did not parse.' }
$definition = @($ast.FindAll({
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Resume-DemoTargets'
}, $true))
if ($definition.Count -ne 1) { throw 'The production target-resumption function is not unique.' }
. ([scriptblock]::Create($definition[0].Extent.Text))

$targets = [pscustomobject]@{}
foreach ($role in $Roles) {
    $targets | Add-Member -NotePropertyName $role -NotePropertyValue ([pscustomobject]@{ resource_id = $AsreContext.targets[$role].resource_id })
}

function Assert-Fixture {
    param([bool]$Condition, [string]$Message)
    $global:AssertionCount++
    if (-not $Condition) { throw "ASSERTION FAILED: $Message" }
}

function Assert-Fails {
    param([scriptblock]$Block, [string]$Pattern, [string]$Message)
    $global:AssertionCount++
    $failure = $null
    try { & $Block } catch { $failure = $_.Exception.Message }
    if ($null -eq $failure) { throw "ASSERTION FAILED: $Message (the operation succeeded)." }
    if ($failure -notmatch $Pattern) { throw "ASSERTION FAILED: $Message (unexpected failure: $failure)" }
}

function Reset-Fixture {
    $script:Starts = @()
    $script:Reads = 0
    $script:AzCalls = @()
    $script:StartFailure = $false
    $script:StartNoEffect = $false
    $script:AccountFails = $false
    $script:AccountState = 'Enabled'
    $script:AccountSubscription = $AsreContext.subscription_id
    $script:AccountTenant = $AsreContext.tenant_id
    $script:ExpectedSubscription = $AsreContext.subscription_id
    $script:ExtraStatus = $false
    $script:NoPowerStatus = $false
    $script:ViewIdOverride = $null
    $script:States = @{ linux = 'PowerState/deallocated'; patch = 'PowerState/stopped'; backup = 'PowerState/running' }
}

function Get-TargetRole {
    param([string]$Id)
    $role = @($Roles | Where-Object { $Id -ceq $AsreContext.targets[$_].resource_id })
    if ($role.Count -ne 1) { throw 'A fixture read or start escaped the fixed target scope.' }
    return $role[0]
}

function Invoke-AzJson {
    param([string[]]$Arguments)
    if (($Arguments[0..1] -join ' ') -cne 'vm get-instance-view') { throw 'Unexpected fixture read.' }
    $id = $Arguments[[Array]::IndexOf($Arguments, '--ids') + 1]
    $role = Get-TargetRole -Id $id
    $script:Reads++
    $statuses = @()
    if (-not $script:NoPowerStatus) { $statuses += [pscustomobject]@{ code = $script:States[$role] } }
    if ($script:ExtraStatus) { $statuses += [pscustomobject]@{ code = 'PowerState/running' } }
    $viewId = if ($null -ne $script:ViewIdOverride) { $script:ViewIdOverride } else { $id }
    return [pscustomobject]@{ id = $viewId; instanceView = [pscustomobject]@{ statuses = $statuses } }
}

function global:az {
    $arguments = @($args)
    $script:AzCalls += ($arguments -join ' ')
    $subscriptionIndex = [Array]::IndexOf($arguments, '--subscription')
    if ($subscriptionIndex -lt 0 -or $arguments[$subscriptionIndex + 1] -cne $script:ExpectedSubscription) {
        throw 'The fixture requires the explicit deployment subscription on every az call.'
    }
    if (($arguments[0..1] -join ' ') -ceq 'account show') {
        if ($script:AccountFails) { $global:LASTEXITCODE = 1; return }
        $global:LASTEXITCODE = 0
        return (@{ id = $script:AccountSubscription; tenantId = $script:AccountTenant; state = $script:AccountState } | ConvertTo-Json -Compress)
    }
    if (($arguments[0..1] -join ' ') -cne 'vm start') { throw 'The fixture admitted an unexpected Azure mutation.' }
    $role = Get-TargetRole -Id $arguments[[Array]::IndexOf($arguments, '--ids') + 1]
    $script:Starts += $arguments[[Array]::IndexOf($arguments, '--ids') + 1]
    $global:LASTEXITCODE = if ($script:StartFailure) { 1 } else { 0 }
    if (-not $script:StartFailure -and -not $script:StartNoEffect) { $script:States[$role] = 'PowerState/running' }
}

try {
    # ---- the default is a no-op: no read, no start, not even an account lookup -------------------------------------
    Reset-Fixture
    Resume-DemoTargets -TargetOutputs $targets -Context $AsreContext
    Assert-Fixture ($script:Reads -eq 0 -and $script:Starts.Count -eq 0 -and $script:AzCalls.Count -eq 0) 'Default execution attempted target resumption.'

    # ---- the Azure CLI account must be the enabled deployment subscription and tenant ------------------------------
    $script:AccountFails = $true
    Assert-Fails { Resume-DemoTargets -TargetOutputs $targets -Context $AsreContext -Approved } 'Azure CLI cannot read the deployment subscription' 'A missing Azure CLI login reached target operations.'
    $script:AccountFails = $false
    $script:AccountSubscription = $AltContext.subscription_id
    Assert-Fails { Resume-DemoTargets -TargetOutputs $targets -Context $AsreContext -Approved } 'not the enabled deployment subscription and tenant' 'An account on another subscription reached target operations.'
    $script:AccountSubscription = $AsreContext.subscription_id
    $script:AccountTenant = $AltContext.tenant_id
    Assert-Fails { Resume-DemoTargets -TargetOutputs $targets -Context $AsreContext -Approved } 'not the enabled deployment subscription and tenant' 'An account on another tenant reached target operations.'
    $script:AccountTenant = $AsreContext.tenant_id
    $script:AccountState = 'Disabled'
    Assert-Fails { Resume-DemoTargets -TargetOutputs $targets -Context $AsreContext -Approved } 'not the enabled deployment subscription and tenant' 'A disabled subscription reached target operations.'
    $script:AccountState = 'Enabled'
    Assert-Fixture ($script:Reads -eq 0 -and $script:Starts.Count -eq 0) 'An unverified identity reached target operations.'

    # ---- approved resumption starts only the stopped targets and verifies every one independently -----------------
    Reset-Fixture
    $messages = @(Resume-DemoTargets -TargetOutputs $targets -Context $AsreContext -Approved)
    Assert-Fixture ($script:Starts.Count -eq 2 -and $script:Reads -eq 6) 'Stopped targets were not started and all targets independently rechecked.'
    Assert-Fixture (($script:Starts -join ',') -ceq (($AsreContext.targets.linux.resource_id, $AsreContext.targets.patch.resource_id) -join ',')) 'The wrong targets were started.'
    Assert-Fixture ($messages.Count -eq 3 -and $messages[0] -ceq "Verified approved target resumption: $($AsreContext.targets.linux.name)" -and
        $messages[2] -ceq "Verified approved target resumption: $($AsreContext.targets.backup.name)") 'Verification was not reported for the three context targets in order.'
    Assert-Fixture (@($script:AzCalls | Where-Object { $_ -notmatch '^(account show|vm start) ' }).Count -eq 0) 'An unexpected Azure command was issued.'
    $null = Resume-DemoTargets -TargetOutputs $targets -Context $AsreContext -Approved
    Assert-Fixture ($script:Starts.Count -eq 2) 'Replaying resumption restarted an already-running target.'

    # ---- -WhatIf reads and decides, but never starts ---------------------------------------------------------------
    Reset-Fixture
    $messages = @(Resume-DemoTargets -TargetOutputs $targets -Context $AsreContext -Approved -WhatIf)
    Assert-Fixture ($script:Starts.Count -eq 0 -and $script:Reads -eq 4 -and $messages.Count -eq 1 -and $messages[0] -ceq "Verified approved target resumption: $($AsreContext.targets.backup.name)") 'WhatIf started a VM, or reported a target that was not running as verified.'
    Assert-Fixture ($script:States.linux -ceq 'PowerState/deallocated') 'WhatIf changed a power state.'

    # ---- preflight is all-or-nothing --------------------------------------------------------------------------------
    Reset-Fixture
    $script:States.backup = 'PowerState/deallocating'
    Assert-Fails { Resume-DemoTargets -TargetOutputs $targets -Context $AsreContext -Approved } 'unexpected or transitional power state' 'A transitional state was accepted.'
    Assert-Fixture ($script:Starts.Count -eq 0) 'A failed preflight partially resumed the target set.'
    foreach ($bad in 'PowerState/starting', 'PowerState/stopping', 'PowerState/unknown', 'ProvisioningState/succeeded') {
        Reset-Fixture
        $script:States.patch = $bad
        Assert-Fails { Resume-DemoTargets -TargetOutputs $targets -Context $AsreContext -Approved } 'unexpected or transitional power state' "The power state $bad was accepted."
        Assert-Fixture ($script:Starts.Count -eq 0) "The power state $bad partially resumed the target set."
    }
    Reset-Fixture
    $script:ExtraStatus = $true
    Assert-Fails { Resume-DemoTargets -TargetOutputs $targets -Context $AsreContext -Approved } 'unexpected or transitional power state' 'An ambiguous power state was accepted.'
    Reset-Fixture
    $script:NoPowerStatus = $true
    Assert-Fails { Resume-DemoTargets -TargetOutputs $targets -Context $AsreContext -Approved } 'unexpected or transitional power state' 'A missing power state was accepted.'
    Reset-Fixture
    $script:ViewIdOverride = '/subscriptions/unrelated'
    Assert-Fails { Resume-DemoTargets -TargetOutputs $targets -Context $AsreContext -Approved } 'unexpected or transitional power state' 'A view of a different resource was accepted.'
    Assert-Fixture ($script:Starts.Count -eq 0) 'A refused view still started a VM.'

    # ---- the saved-plan outputs must be exactly the context targets --------------------------------------------------
    Reset-Fixture
    $bad = $targets | ConvertTo-Json -Depth 4 | ConvertFrom-Json
    $bad.linux.resource_id = '/subscriptions/unrelated'
    Assert-Fails { Resume-DemoTargets -TargetOutputs $bad -Context $AsreContext -Approved } 'unexpected linux resource' 'Unexpected saved-plan target outputs were admitted.'
    $bad = $targets | ConvertTo-Json -Depth 4 | ConvertFrom-Json
    $bad.linux.resource_id = $targets.linux.resource_id.ToUpperInvariant()
    Assert-Fails { Resume-DemoTargets -TargetOutputs $bad -Context $AsreContext -Approved } 'unexpected linux resource' 'A differently cased resource id was admitted.'
    $bad = $targets | ConvertTo-Json -Depth 4 | ConvertFrom-Json
    $swap = $bad.linux.resource_id
    $bad.linux.resource_id = $bad.patch.resource_id
    $bad.patch.resource_id = $swap
    Assert-Fails { Resume-DemoTargets -TargetOutputs $bad -Context $AsreContext -Approved } 'unexpected linux resource' 'Swapped target roles were admitted.'
    $two = $targets | ConvertTo-Json -Depth 4 | ConvertFrom-Json
    $two.PSObject.Properties.Remove('backup')
    Assert-Fails { Resume-DemoTargets -TargetOutputs $two -Context $AsreContext -Approved } 'exactly the three saved-plan target outputs' 'Two targets were admitted.'
    $four = $targets | ConvertTo-Json -Depth 4 | ConvertFrom-Json
    $four | Add-Member -NotePropertyName extra -NotePropertyValue ([pscustomobject]@{ resource_id = $targets.linux.resource_id })
    Assert-Fails { Resume-DemoTargets -TargetOutputs $four -Context $AsreContext -Approved } 'exactly the three saved-plan target outputs' 'A fourth target was admitted.'
    Assert-Fixture ($script:Starts.Count -eq 0 -and $script:Reads -eq 0) 'Unexpected saved-plan outputs reached a VM operation.'

    # The context, not the outputs, defines the allowed targets: another deployment's context rejects these outputs.
    Reset-Fixture
    $script:AccountSubscription = $AltContext.subscription_id
    $script:AccountTenant = $AltContext.tenant_id
    $script:ExpectedSubscription = $AltContext.subscription_id
    Assert-Fails { Resume-DemoTargets -TargetOutputs $targets -Context $AltContext -Approved } 'unexpected linux resource' 'Outputs of another deployment were admitted.'
    Assert-Fixture ($script:Starts.Count -eq 0) 'Outputs of another deployment started a VM.'

    # ---- failures are never reported as verified ---------------------------------------------------------------------
    Reset-Fixture
    $script:StartFailure = $true
    Assert-Fails { Resume-DemoTargets -TargetOutputs $targets -Context $AsreContext -Approved } 'resumption failed' 'An unsuccessful Azure start was reported as verified.'
    Reset-Fixture
    $script:StartNoEffect = $true
    Assert-Fails { Resume-DemoTargets -TargetOutputs $targets -Context $AsreContext -Approved } 'did not independently report Running' 'A start that did not reach Running was reported as verified.'

    Write-Output "Bounded target-resumption fixtures passed ($($global:AssertionCount) assertions); no Azure operation was executed."
}
finally {
    Clear-AsreTestContext
    Remove-Item -LiteralPath 'Function:\az' -Force -ErrorAction SilentlyContinue
}
