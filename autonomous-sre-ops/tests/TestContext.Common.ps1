# Test-only helper: runs a script in the same process under a committed synthetic context.
#
# Dot-source it from a test, call Use-AsreTestContext before running the script and
# Clear-AsreTestContext in a finally block. A script that dot-sources Context.Common.ps1 defines its own
# Get-AsreContext, which a test cannot shadow with a function; command resolution prefers an alias over a
# function, so this helper installs a global alias instead. The alias target calls the real loader, so the
# document is still parsed and validated; only the refusal of synthetic values is lifted.
# A fixture chosen with Use-AsreTestContext beats an ambient ASRE_CONTEXT_FILE (pytest sets one for every
# test process); an explicit -Path beats both.
# A script started in a separate process is not covered. Deployment scripts must never use this helper.
Set-StrictMode -Version Latest

$global:AsreTestRepo = Split-Path $PSScriptRoot -Parent
. (Join-Path $global:AsreTestRepo 'scripts\Context.Common.ps1')
$global:AsreTestRealLoader = ${function:Get-AsreContext}
$global:AsreTestContextPath = $null

function global:Get-AsreTestContext {
    [CmdletBinding()]
    param([string]$Path, [switch]$AllowSynthetic)
    if ([string]::IsNullOrWhiteSpace($Path)) { $Path = $global:AsreTestContextPath }
    if ([string]::IsNullOrWhiteSpace($Path)) { $Path = $env:ASRE_CONTEXT_FILE }
    & $global:AsreTestRealLoader -Path $Path -AllowSynthetic
}

function Use-AsreTestContext {
    # Returns the full path of the fixture now served by Get-AsreContext.
    [CmdletBinding()]
    param([ValidateSet('synthetic', 'alt')][string]$Name = 'synthetic', [string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { $Path = Join-Path $global:AsreTestRepo "tests\fixtures\context.$Name.json" }
    $full = [System.IO.Path]::GetFullPath($Path)
    if (-not [System.IO.File]::Exists($full)) { throw "The test context was not found: $full" }
    $global:AsreTestContextPath = $full
    Set-Alias -Name Get-AsreContext -Value Get-AsreTestContext -Scope Global -Force
    return $full
}

function Clear-AsreTestContext {
    Remove-Item -LiteralPath 'Alias:\Get-AsreContext' -Force -ErrorAction SilentlyContinue
    $global:AsreTestContextPath = $null
}
