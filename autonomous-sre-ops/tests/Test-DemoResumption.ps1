$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repo = Split-Path $PSScriptRoot -Parent
$Subscription = '00000001-000a-4000-a000-000000000001'
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    (Join-Path $repo 'scripts\Initialize-RuntimeSsh.ps1'), [ref]$tokens, [ref]$errors
)
if ($errors.Count) { throw 'The runtime initializer did not parse.' }
$definition = @($ast.FindAll({
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Resume-DemoTargets'
}, $true))
if ($definition.Count -ne 1) { throw 'The production target-resumption function is not unique.' }
. ([scriptblock]::Create($definition[0].Extent.Text))
$script:Starts = @()
$script:Reads = 0
$script:StartFailure = $false
$script:States = @{ linux = 'PowerState/deallocated'; patch = 'PowerState/stopped'; backup = 'PowerState/running' }
$targets = [pscustomobject]@{}
foreach ($role in @('linux', 'patch', 'backup')) {
    $targets | Add-Member -NotePropertyName $role -NotePropertyValue ([pscustomobject]@{
        resource_id = "/subscriptions/$Subscription/resourceGroups/rg-asre-$role-cus/providers/Microsoft.Compute/virtualMachines/vm-asre-$role-cus"
    })
}
function Invoke-AzJson {
    param([string[]]$Arguments)
    if (($Arguments[0..1] -join ' ') -ne 'vm get-instance-view') { throw 'Unexpected fixture read.' }
    $id = $Arguments[[Array]::IndexOf($Arguments, '--ids') + 1]
    $role = @('linux', 'patch', 'backup') | Where-Object { $id -eq $targets.$_.resource_id }
    if (@($role).Count -ne 1) { throw 'A read escaped the fixed target scope.' }
    $script:Reads++
    return [pscustomobject]@{
        id = $id
        instanceView = [pscustomobject]@{ statuses = @([pscustomobject]@{ code = $script:States[$role] }) }
    }
}
function az {
    $arguments = @($args)
    if (($arguments[0..1] -join ' ') -ne 'vm start') { throw 'The fixture admitted an unexpected Azure mutation.' }
    $id = $arguments[[Array]::IndexOf($arguments, '--ids') + 1]
    $role = @('linux', 'patch', 'backup') | Where-Object { $id -eq $targets.$_.resource_id }
    if (@($role).Count -ne 1) { throw 'VM start escaped the approved three-target allowlist.' }
    $script:Starts += $id
    $global:LASTEXITCODE = if ($script:StartFailure) { 1 } else { 0 }
    if (-not $script:StartFailure) { $script:States[$role] = 'PowerState/running' }
}
function Assert-Fixture {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}
$previous = @{}
foreach ($name in @('servicePrincipalId', 'tenantId', 'idToken')) { $previous[$name] = [Environment]::GetEnvironmentVariable($name) }
try {
    Resume-DemoTargets -TargetOutputs $targets
    Assert-Fixture ($script:Reads -eq 0 -and $script:Starts.Count -eq 0) 'Default execution attempted target resumption.'
    $env:servicePrincipalId = 'fixture-unapproved'
    $env:tenantId = '00000002-000a-4000-9000-000000000002'
    $env:idToken = 'fixture-only-not-a-token'
    $rejected = $false
    try { Resume-DemoTargets -TargetOutputs $targets -Approved }
    catch { $rejected = $_.Exception.Message -match 'protected Apply identity' }
    Assert-Fixture ($rejected -and $script:Reads -eq 0) 'An unapproved identity reached Azure operations.'
    $env:servicePrincipalId = '00000018-000a-4000-b000-000000000018'
    Resume-DemoTargets -TargetOutputs $targets -Approved
    Assert-Fixture ($script:Starts.Count -eq 2 -and $script:Reads -eq 6) 'Stopped targets were not started and all targets independently rechecked.'
    Resume-DemoTargets -TargetOutputs $targets -Approved
    Assert-Fixture ($script:Starts.Count -eq 2) 'Replaying resumption restarted an already-running target.'
    $script:States.linux = 'PowerState/deallocated'
    $script:States.backup = 'PowerState/deallocating'
    $rejected = $false
    try { Resume-DemoTargets -TargetOutputs $targets -Approved }
    catch { $rejected = $_.Exception.Message -match 'unexpected or transitional power state' }
    Assert-Fixture ($rejected -and $script:Starts.Count -eq 2) 'A failed preflight partially resumed the target set.'
    $script:States.backup = 'PowerState/running'
    $bad = $targets | ConvertTo-Json -Depth 4 | ConvertFrom-Json
    $bad.linux.resource_id = '/subscriptions/unrelated'
    $rejected = $false
    try { Resume-DemoTargets -TargetOutputs $bad -Approved }
    catch { $rejected = $_.Exception.Message -match 'unexpected linux resource' }
    Assert-Fixture ($rejected -and $script:Starts.Count -eq 2) 'Unexpected saved-plan target outputs were admitted.'
    $script:StartFailure = $true
    $rejected = $false
    try { Resume-DemoTargets -TargetOutputs $targets -Approved }
    catch { $rejected = $_.Exception.Message -match 'resumption failed' }
    Assert-Fixture $rejected 'An unsuccessful Azure start was reported as verified.'
}
finally {
    foreach ($name in $previous.Keys) { [Environment]::SetEnvironmentVariable($name, $previous[$name]) }
}
Write-Output 'Bounded target-resumption fixtures passed; no Azure operation was executed.'
