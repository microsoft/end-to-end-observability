$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repo = Split-Path $PSScriptRoot -Parent
. (Join-Path $PSScriptRoot 'TestContext.Common.ps1')
$script:assertions = 0
function Assert-Helper {
    param([bool]$Condition, [string]$Message)
    $script:assertions++
    if (-not $Condition) { throw $Message }
}
$contextCommon = Join-Path $repo 'scripts\Context.Common.ps1'
$synthetic = Join-Path $PSScriptRoot 'fixtures\context.synthetic.json'

$refused = $false
try { Get-AsreContext -Path $synthetic | Out-Null }
catch { $refused = $_.Exception.Message -match 'synthetic' }
Assert-Helper $refused 'The real loader must refuse the synthetic fixture.'
Assert-Helper ($null -eq (Get-Command -Name Get-AsreContext -CommandType Alias -ErrorAction SilentlyContinue)) 'No alias may exist before Use-AsreTestContext.'

$broken = [System.IO.Path]::GetTempFileName()
try {
    $used = Use-AsreTestContext
    Assert-Helper ($used -ceq [System.IO.Path]::GetFullPath($synthetic)) 'The synthetic fixture must be the default.'
    $loaded = & { . $contextCommon; Get-AsreContext }
    Assert-Helper ($loaded.subscription_id -ceq '00000001-000a-4000-a000-000000000001') 'The alias must win over a function the script defines itself.'
    $explicit = & { . $contextCommon; Get-AsreContext -Path $synthetic }
    Assert-Helper ($explicit.name_suffix -ceq $loaded.name_suffix) 'An explicit path must still be honored.'

    Use-AsreTestContext -Name alt | Out-Null
    $alt = & { . $contextCommon; Get-AsreContext }
    Assert-Helper ($alt.subscription_id -cne $loaded.subscription_id -and $alt.name_suffix -cne $loaded.name_suffix) 'The alternative fixture must differ from the synthetic one.'

    $ambient = $env:ASRE_CONTEXT_FILE
    try {
        $env:ASRE_CONTEXT_FILE = $synthetic
        Use-AsreTestContext -Name alt | Out-Null
        $withAmbient = & { . $contextCommon; Get-AsreContext }
        Assert-Helper ($withAmbient.subscription_id -ceq $alt.subscription_id) 'A fixture chosen with Use-AsreTestContext must beat an ambient ASRE_CONTEXT_FILE.'
        $viaEnvironment = & { Clear-AsreTestContext; Set-Alias -Name Get-AsreContext -Value Get-AsreTestContext -Scope Global -Force; . $contextCommon; Get-AsreContext }
        Assert-Helper ($viaEnvironment.subscription_id -ceq $loaded.subscription_id) 'Without a chosen fixture the ambient ASRE_CONTEXT_FILE must be served.'
    }
    finally {
        $env:ASRE_CONTEXT_FILE = $ambient
    }

    [System.IO.File]::WriteAllText($broken, '{"schema":"asre-context/v1"}')
    Use-AsreTestContext -Path $broken | Out-Null
    $rejected = $false
    try { & { . $contextCommon; Get-AsreContext } | Out-Null }
    catch { $rejected = $true }
    Assert-Helper $rejected 'The real loader must still validate the document.'
}
finally {
    Clear-AsreTestContext
    Remove-Item -LiteralPath $broken -Force -ErrorAction SilentlyContinue
}

Assert-Helper ($null -eq (Get-Command -Name Get-AsreContext -CommandType Alias -ErrorAction SilentlyContinue)) 'Clear-AsreTestContext must remove the alias.'
$refusedAgain = $false
try { Get-AsreContext -Path $synthetic | Out-Null }
catch { $refusedAgain = $_.Exception.Message -match 'synthetic' }
Assert-Helper $refusedAgain 'The real loader must refuse the synthetic fixture again after cleanup.'
Write-Output "Test context helper fixtures passed: $script:assertions assertions."
