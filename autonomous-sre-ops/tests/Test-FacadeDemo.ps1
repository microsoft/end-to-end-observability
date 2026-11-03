#requires -Version 7.0
# Executes the complete production wrapper in child PowerShell processes.
# Only dummy SecureStrings and an isolated fake Python module are used.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repo = Split-Path $PSScriptRoot -Parent
$wrapper = Join-Path $repo 'scripts/Invoke-FacadeDemo.ps1'
$python = @(Get-Command python -CommandType Application -ErrorAction Stop)[0].Source
$pwsh = (Get-Process -Id $PID).Path
$fixture = Join-Path ([IO.Path]::GetTempPath()) ('facade-wrapper-' + [guid]::NewGuid().ToString('N'))
$moduleRoot = Join-Path $fixture 'modules'
$module = Join-Path $moduleRoot 'Microsoft.PowerShell.SecretManagement'
$fakePython = Join-Path $fixture 'python'
$log = Join-Path $fixture 'calls.jsonl'
$transport = Join-Path $fixture 'transport.json'
$script:checks = 0
$script:cases = 0

function Assert-Fixture {
    param([bool]$Condition, [string]$Message)
    $script:checks++
    if (-not $Condition) { throw $Message }
}

function Invoke-Fixture {
    param(
        [string[]]$Arguments,
        [string]$Mode = 'secure',
        [switch]$MissingModule,
        [int]$PythonExit = 0
    )
    $script:cases++
    foreach ($path in @($log, $transport)) {
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
    }
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $pwsh
    $info.UseShellExecute = $false
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $coreModules = Join-Path $PSHOME 'Modules'
    $shim = Join-Path $fixture 'shim.ps1'
    # pwsh startup prepends user/shared module paths; isolate after startup.
    # Static script plus ArgumentList/@args preserves paths without code interpolation.
    [IO.File]::WriteAllText($shim, '$env:PSModulePath = $env:FACADE_TEST_PSMODULEPATH; & $env:FACADE_TEST_WRAPPER @args; exit $LASTEXITCODE')
    foreach ($argument in @('-NoLogo', '-NoProfile', '-NonInteractive', '-File', $shim) + $Arguments) {
        $info.ArgumentList.Add($argument)
    }
    $info.Environment['FACADE_TEST_WRAPPER'] = $wrapper
    $info.Environment['FACADE_TEST_PSMODULEPATH'] = if ($MissingModule) { $coreModules } else {
        $moduleRoot + [IO.Path]::PathSeparator + $coreModules
    }
    $info.Environment['PYTHONPATH'] = $fakePython
    $info.Environment['FACADE_TEST_MODE'] = $Mode
    $info.Environment['FACADE_TEST_LOG'] = $log
    $info.Environment['FACADE_TEST_TRANSPORT'] = $transport
    $info.Environment['FACADE_TEST_EXIT'] = [string]$PythonExit
    $child = [Diagnostics.Process]::new()
    $child.StartInfo = $info
    try {
        [void]$child.Start()
        $out = $child.StandardOutput.ReadToEndAsync()
        $err = $child.StandardError.ReadToEndAsync()
        if (-not $child.WaitForExit(20000)) {
            $child.Kill($true)
            throw 'The isolated fixture timed out.'
        }
        $output = $out.GetAwaiter().GetResult() + $err.GetAwaiter().GetResult()
        $calls = @(if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log })
        $record = if (Test-Path -LiteralPath $transport) {
            Get-Content -LiteralPath $transport -Raw | ConvertFrom-Json
        } else { $null }
        Assert-Fixture ($output -notmatch 'fixture-only|transport-body|retrieval-body') 'A secret/body escaped into diagnostics.'
        return [pscustomobject]@{ Code = $child.ExitCode; Output = $output.Trim(); Calls = $calls; Record = $record }
    }
    finally { $child.Dispose() }
}

function Assert-NotReady {
    param($Result, [int]$Calls = 0)
    Assert-Fixture ($Result.Code -eq 2 -and $Result.Output -ceq 'Facade command not ready.') 'Expected fixed not-ready result.'
    Assert-Fixture ($null -eq $Result.Record -and $Result.Calls.Count -eq $Calls) 'Not-ready crossed a transport/retrieval boundary.'
}

try {
    [void](New-Item -ItemType Directory -Path $module, (Join-Path $fakePython 'operations') -Force)
    # Fake maintained API surface. No real module, store, or credential is read.
    $moduleText = @'
function Log-Call([string]$Value) {
    [IO.File]::AppendAllText($env:FACADE_TEST_LOG, $Value + "`n")
}
Log-Call 'import'
function Get-SecretVault {
    [CmdletBinding()]param([string]$Name)
    Log-Call ('vault:' + $Name)
    switch ($env:FACADE_TEST_MODE) {
        'missing-vault' { return }
        'duplicate-vault' {
            [pscustomobject]@{ Name = $Name; ModuleName = 'Microsoft.PowerShell.SecretStore' }
            [pscustomobject]@{ Name = 'fallback'; ModuleName = 'Microsoft.PowerShell.SecretStore' }
            return
        }
        'wrong-case' { [pscustomobject]@{ Name = $Name.ToUpperInvariant(); ModuleName = 'Microsoft.PowerShell.SecretStore' }; return }
        'azure-vault' { [pscustomobject]@{ Name = $Name; ModuleName = 'Az.KeyVault' }; return }
        'other-vault' { [pscustomobject]@{ Name = $Name; ModuleName = 'Other.Store' }; return }
    }
    [pscustomobject]@{ Name = $Name; ModuleName = 'Microsoft.PowerShell.SecretStore' }
}
function Get-Secret {
    [CmdletBinding()]param([string]$Vault, [string]$Name)
    Log-Call ('secret:' + $Vault + ':' + $Name)
    $value = 'fixture-only-sentinel-☃-; $() "'
    switch ($env:FACADE_TEST_MODE) {
        'missing-secret' { return }
        'throw-secret' { throw 'retrieval-body fixture-only-sentinel' }
        'plain-secret' { return $value }
        'empty-secret' { return [Security.SecureString]::new() }
        'long-secret' { $value = 'x' * 4097 }
        'wide-secret' { $value = '☃' * 1400 }
        'control-secret' { $value += "`n" }
        'nul-secret' { $value += [char]0 }
        'del-secret' { $value += [char]127 }
        'surrogate-secret' { $value += [char]0xD800 }
        'boundary-secret' { $value = 'x' * 4096 }
        'unicode-boundary-secret' { $value = ('☃' * 1365) + 'x' }
        'noisy-secret' {
            Write-Warning 'retrieval-body fixture-only-sentinel'
            Write-Verbose 'retrieval-body fixture-only-sentinel' -Verbose
            Write-Debug 'retrieval-body fixture-only-sentinel' -Debug
            Write-Information 'retrieval-body fixture-only-sentinel' -InformationAction Continue
        }
    }
    ConvertTo-SecureString -String $value -AsPlainText -Force
}
Export-ModuleMember -Function Get-SecretVault, Get-Secret
'@
    [IO.File]::WriteAllText((Join-Path $module 'Microsoft.PowerShell.SecretManagement.psm1'), $moduleText)
    # sitecustomize makes only this child Python resolve operations to the fake
    # package, despite the production wrapper's pinned repository cwd.
    [IO.File]::WriteAllText((Join-Path $fakePython 'sitecustomize.py'), @'
import os
import sys
import types
package = types.ModuleType("operations")
package.__path__ = [os.path.join(os.path.dirname(__file__), "operations")]
sys.modules["operations"] = package
'@)
    [IO.File]::WriteAllText((Join-Path $fakePython 'operations/facade_demo.py'), @'
import hashlib
import json
import os
import sys
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
raw = sys.stdin.buffer.read()
sentinel = 'fixture-only-sentinel-☃-; $() "'
record = {
    "argv": sys.argv[1:],
    "stdin_sha256": hashlib.sha256(raw).hexdigest(),
    "stdin_bytes": len(raw),
    "secret_in_argv": any(sentinel in arg for arg in sys.argv),
}
with open(os.environ["FACADE_TEST_TRANSPORT"], "w", encoding="utf-8") as stream:
    json.dump(record, stream)
# A buggy/untrusted child must not be able to leak bodies on either stream.
sys.stdout.write("transport-body " + sentinel + "x" * 131072)
sys.stderr.write("transport-body " + sentinel + "y" * 131072)
sys.exit(int(os.environ["FACADE_TEST_EXIT"]))
'@)
    $state = Join-Path $fixture 'state with spaces;$(no-shell).json'
    $base = @('-PythonExecutable', $python)
    $live = $base + @('-VaultName', 'fixture-vault', '-SecretName', 'fixture-ref', '-EstablishedCurrentUserStore')
    $send = @('send', '-State', $state, '-ConfirmNewCase') + $live

    foreach ($arguments in @(
        @('prepare', '-State', $state, '-Synthetic'),
        @('prepare', '-State', $state, '-InputPath', 'input with spaces;$(no-shell).json'),
        @('check', '-State', $state)
    )) {
        $result = Invoke-Fixture -Arguments ($arguments + $base) -MissingModule
        Assert-Fixture ($result.Code -eq 0 -and $result.Output -ceq 'Facade command completed.') "Offline command failed ($($result.Code)): $($result.Output)"
        Assert-Fixture ($result.Calls.Count -eq 0 -and $result.Record.stdin_bytes -eq 0) 'Offline execution touched the secret API/stdin.'
        Assert-Fixture ($result.Record.argv[0] -ceq $arguments[0] -and $result.Record.argv[2] -ceq $state) 'Offline argv/path changed.'
        $expectedArguments = @($arguments[0], '--state', $state)
        if ($arguments[0] -eq 'prepare') {
            $expectedArguments += if ($arguments[-1] -eq '-Synthetic') { @('--synthetic') } else {
                @('--input', $arguments[-1])
            }
        }
        Assert-Fixture (($result.Record.argv -join '|') -ceq ($expectedArguments -join '|')) 'Offline arguments widened or changed.'
    }
    $result = Invoke-Fixture -Arguments (@('check', '-State', $state) + $base)
    Assert-Fixture ($result.Code -eq 0 -and $result.Calls.Count -eq 0) 'Offline command discovered/imported the available fake module.'
    $result = Invoke-Fixture -Arguments @('check', '-State', $state) -MissingModule
    Assert-Fixture ($result.Code -eq 0 -and $result.Calls.Count -eq 0) 'Default application-only Python discovery failed.'
    $hasher = [Security.Cryptography.SHA256]::Create()
    try {
        $expected = [BitConverter]::ToString($hasher.ComputeHash(
            [Text.Encoding]::UTF8.GetBytes('fixture-only-sentinel-☃-; $() "'))).Replace('-', '').ToLowerInvariant()
    }
    finally { $hasher.Dispose() }
    foreach ($arguments in @(
        (@('readiness') + $live),
        $send,
        (@('reconcile', '-State', $state, '-Write', 'create') + $live),
        (@('reconcile', '-State', $state, '-Write', 'note') + $live)
    )) {
        $result = Invoke-Fixture -Arguments $arguments
        Assert-Fixture ($result.Code -eq 0 -and $result.Output -ceq 'Facade command completed.') "Live fixture failed ($($result.Code)): $($result.Output); calls=$($result.Calls -join '|'); arguments=$($arguments -join '|')"
        Assert-Fixture (($result.Calls -join '|') -ceq 'import|vault:fixture-vault|secret:fixture-vault:fixture-ref') 'Unexpected lookup or fallback.'
        Assert-Fixture ($result.Record.stdin_sha256 -ceq $expected -and -not $result.Record.secret_in_argv) 'Secret stdin or argv isolation changed.'
        Assert-Fixture ('--secret-stdin' -in $result.Record.argv -and '--client-id' -in $result.Record.argv) 'Missing live CLI arguments.'
        Assert-Fixture ($result.Record.argv[-2] -ceq '0000000000000000000000000000003a') 'Default client ID changed.'
        $expectedArguments = @($arguments[0])
        if ($arguments[0] -ne 'readiness') { $expectedArguments += @('--state', $state) }
        if ($arguments[0] -eq 'send') { $expectedArguments += '--confirm-new-case' }
        if ($arguments[0] -eq 'reconcile') { $expectedArguments += @('--write', $arguments[4]) }
        $expectedArguments += @('--client-id', '0000000000000000000000000000003a', '--secret-stdin')
        Assert-Fixture (($result.Record.argv -join '|') -ceq ($expectedArguments -join '|')) 'Live arguments widened or changed.'
    }
    $result = Invoke-Fixture -Arguments $send -Mode 'noisy-secret'
    Assert-Fixture ($result.Code -eq 0 -and $result.Record.stdin_sha256 -ceq $expected) 'Retrieval stream redaction changed the secret.'
    $overrideId = '0123456789abcdef0123456789abcdef'
    $result = Invoke-Fixture -Arguments ($send + @('-ClientId', $overrideId))
    Assert-Fixture ($result.Code -eq 0 -and $result.Record.argv[-2] -ceq $overrideId) 'Valid explicit client ID was not relayed.'
    $result = Invoke-Fixture -Arguments $send -PythonExit 9
    Assert-Fixture ($result.Code -eq 1 -and $result.Output -ceq 'Facade command failed.') 'Child failure was not redacted.'
    $result = Invoke-Fixture -Arguments $send -Mode 'boundary-secret'
    Assert-Fixture ($result.Code -eq 0 -and $result.Record.stdin_bytes -eq 4096) '4096-byte boundary was rejected.'
    $result = Invoke-Fixture -Arguments $send -Mode 'unicode-boundary-secret'
    Assert-Fixture ($result.Code -eq 0 -and $result.Record.stdin_bytes -eq 4096) '4096-byte Unicode boundary was rejected.'

    Assert-NotReady (Invoke-Fixture -Arguments (@('readiness') + $base))
    Assert-NotReady (Invoke-Fixture -Arguments (@('readiness', '-VaultName', 'fixture-vault', '-SecretName', 'fixture-ref') + $base))
    Assert-NotReady (Invoke-Fixture -Arguments (@('readiness', '-VaultName', 'fixture-vault', '-EstablishedCurrentUserStore') + $base))
    Assert-NotReady (Invoke-Fixture -Arguments (@('readiness', '-SecretName', 'fixture-ref', '-EstablishedCurrentUserStore') + $base))
    Assert-NotReady (Invoke-Fixture -Arguments (@('readiness', '-VaultName', '*', '-SecretName', 'fixture-ref', '-EstablishedCurrentUserStore') + $base))
    Assert-NotReady (Invoke-Fixture -Arguments (@('readiness', '-VaultName', 'fixture-vault', '-SecretName', 'fixture-*', '-EstablishedCurrentUserStore') + $base))
    Assert-NotReady (Invoke-Fixture -Arguments $send -MissingModule)
    foreach ($mode in @('missing-vault', 'duplicate-vault', 'wrong-case', 'azure-vault', 'other-vault')) {
        Assert-NotReady (Invoke-Fixture -Arguments $send -Mode $mode) -Calls 2
    }
    foreach ($mode in @('missing-secret', 'throw-secret', 'plain-secret', 'empty-secret',
            'long-secret', 'wide-secret', 'control-secret', 'nul-secret', 'del-secret', 'surrogate-secret')) {
        Assert-NotReady (Invoke-Fixture -Arguments $send -Mode $mode) -Calls 3
    }
    foreach ($arguments in @(
        (@('send', '-State', $state) + $live),
        (@('reconcile', '-State', $state) + $live),
        (@('readiness', '-ClientId', 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA') + $live),
        (@('readiness', '-ClientId', 'bad') + $live),
        (@('prepare', '-State', $state) + $base),
        (@('prepare', '-State', $state, '-Synthetic', '-InputPath', 'both.json') + $base),
        (@('check') + $base),
        (@('check', '-State', $state, '-Synthetic') + $base),
        (@('check', '-State', $state, '-Write', 'note') + $base),
        (@('readiness', '-State', $state) + $live),
        @('check', '-State', $state, '-PythonExecutable', ($python + ' -c bad')),
        @('check', '-State', $state, '-PythonExecutable', $wrapper),
        @('check', '-State', $state, '-PythonExecutable', $moduleText)
    )) {
        Assert-NotReady (Invoke-Fixture -Arguments $arguments)
    }
    Write-Output "Facade wrapper fixtures passed: $script:cases cases, $script:checks assertions; no real store or live transport used."
}
finally {
    if (Test-Path -LiteralPath $fixture) { Remove-Item -LiteralPath $fixture -Recurse -Force }
}
