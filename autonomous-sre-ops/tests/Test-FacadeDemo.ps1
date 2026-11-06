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
$script:contextFile = Join-Path $fixture 'context.json'
$script:checks = 0
$script:cases = 0

function New-RealisticDocument {
    # A valid, non-synthetic context (the real loader accepts it): the alternative fixture with values that match no
    # sentinel shape and a complete ServiceNow section. The facade wrapper runs the real loader in a child process.
    $document = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'fixtures\context.alt.json') -Raw | ConvertFrom-Json -AsHashtable
    $subscription = '5b7c1e84-2f3a-4d96-8c05-a1e3f7b92d60'
    $suffix = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes("${subscription}:asre-demo"))).ToLowerInvariant().Substring(0, 10)
    $document['subscription_id'] = $subscription
    $document['tenant_id'] = '9d2e6a41-7c58-4b03-b6f9-3e8a15c07d24'
    $document['operator_object_id'] = '1f6b8c32-5a47-4e09-8d13-c92b0e7a4f58'
    $document['name_suffix'] = $suffix
    $document['log_analytics']['customer_id'] = '4a9d7e13-6b20-4c85-9f41-d27e0b5a8c63'
    $document['registry'] = @{ name = "acrasre$suffix"; login_server = "acrasre$suffix.azurecr.io" }
    $document['key_vault'] = @{ name = "kv-asre-$suffix"; uri = "https://kv-asre-$suffix.vault.azure.net/" }
    $document['storage'] = @{
        account_name = "stasre$suffix"
        blob_endpoint = "https://stasre$suffix.blob.core.windows.net"
        table_endpoint = "https://stasre$suffix.table.core.windows.net"
    }
    $guids = @('7e1a4c92-3b58-4d06-a2f7-6c80d1e95b34', '2c93f0a7-8d14-4e6b-b05a-91e7c3d28f46', 'd4b61e08-9a27-4f35-8c19-0a7e52b3d6f1',
               'f08a3d65-1c94-47b2-9e60-5d2b7a1c84e9', '63e7b2d0-a815-4c93-b7f4-18d90e5a3c27', 'a95c1f48-6e03-4d7a-8b21-c4f60d9e7a15',
               '0b3d8e72-f594-4a16-9c8d-72e1a60b5f39', 'e6a07c13-4d82-4b59-a3f0-9b15c8d2e764')
    $index = 0
    foreach ($key in @('agent', 'runner', 'collector', 'pull', 'bootstrap')) {
        $document['identities'][$key] = @{ client_id = $guids[$index % 8]; principal_id = $guids[($index + 3) % 8] }
        $index++
    }
    $document['agent']['endpoint'] = 'https://asre-demo-cus--c4e81b27.7f3a95d0.centralus.azuresre.ai'
    $document['servicenow'] = @{
        origin = 'https://tenant-fixture.service-now.com'
        api_base = '/api/x_4417_asre/case_adapter/v1'
        client_id = 'c1a2b3d4e5f60718293a4b5c6d7e8f90'
        facade_client_id = 'b7a6c5d4e3f2019887766554433221ff'
        assignment_group = 'a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6'
        configuration_items = @{
            linux = 'd1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6'
            patch = 'e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7'
            backup = 'f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8'
        }
    }
    return $document
}

function Write-ContextFile {
    param([string]$Path, $Document)
    [IO.File]::WriteAllText($Path, (ConvertTo-Json -InputObject $Document -Depth 10), [Text.UTF8Encoding]::new($false))
}

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
        [int]$PythonExit = 0,
        [string]$ContextFile = $script:contextFile,
        [switch]$NoContextFile,
        [switch]$InlineContext
    )
    $script:cases++
    foreach ($path in @($log, $transport)) {
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
    }
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $pwsh
    $info.UseShellExecute = $false
    $info.WorkingDirectory = $fixture
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
    # The wrapper reads the context from the environment; the run never inherits one from the machine running this test.
    [void]$info.Environment.Remove('ASRE_CONTEXT')
    if ($InlineContext) { $info.Environment['ASRE_CONTEXT'] = '{"inline":"context"}' }
    if ($NoContextFile) { [void]$info.Environment.Remove('ASRE_CONTEXT_FILE') } else { $info.Environment['ASRE_CONTEXT_FILE'] = $ContextFile }
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
    # The deployment context the wrapper validates, read here from the same fixture the wrapper's child loads.
    $document = New-RealisticDocument
    Write-ContextFile $script:contextFile $document
    $contextClientId = $document['servicenow']['client_id']
    Assert-Fixture ($contextClientId -cmatch '^[0-9a-f]{32}$') 'The fixture context must carry a 32-hex client id.'
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
    "context_file": os.environ.get("ASRE_CONTEXT_FILE"),
    "inline_context_present": "ASRE_CONTEXT" in os.environ,
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
        Assert-Fixture ($result.Record.context_file -ceq $script:contextFile -and -not $result.Record.inline_context_present) 'Offline execution must hand the child the validated context file only.'
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
        Assert-Fixture ($result.Record.argv[-2] -ceq $contextClientId) 'Default client ID must be the context client ID.'
        Assert-Fixture ($result.Record.context_file -ceq $script:contextFile -and -not $result.Record.inline_context_present) 'Live execution must hand the child the validated context file only.'
        $expectedArguments = @($arguments[0])
        if ($arguments[0] -ne 'readiness') { $expectedArguments += @('--state', $state) }
        if ($arguments[0] -eq 'send') { $expectedArguments += '--confirm-new-case' }
        if ($arguments[0] -eq 'reconcile') { $expectedArguments += @('--write', $arguments[4]) }
        $expectedArguments += @('--client-id', $contextClientId, '--secret-stdin')
        Assert-Fixture (($result.Record.argv -join '|') -ceq ($expectedArguments -join '|')) 'Live arguments widened or changed.'
    }
    $result = Invoke-Fixture -Arguments $send -Mode 'noisy-secret'
    Assert-Fixture ($result.Code -eq 0 -and $result.Record.stdin_sha256 -ceq $expected) 'Retrieval stream redaction changed the secret.'
    $overrideId = '0123456789abcdef0123456789abcdef'
    $result = Invoke-Fixture -Arguments ($send + @('-ClientId', $overrideId))
    Assert-Fixture ($result.Code -eq 0 -and $result.Record.argv[-2] -ceq $overrideId) 'Valid explicit client ID was not relayed.'
    # The context, not a built-in value, supplies the default client id; the child reads exactly the validated file.
    $otherDocument = New-RealisticDocument
    $otherClientId = '0f1e2d3c4b5a69788796a5b4c3d2e1f0'
    $otherDocument['servicenow']['client_id'] = $otherClientId
    $otherFile = Join-Path $fixture 'other-context.json'
    Write-ContextFile $otherFile $otherDocument
    $result = Invoke-Fixture -Arguments $send -ContextFile $otherFile
    Assert-Fixture ($result.Code -eq 0 -and $result.Record.argv[-2] -ceq $otherClientId -and $result.Record.context_file -ceq $otherFile) 'The default client ID and the child context must follow the context file in the environment.'
    $result = Invoke-Fixture -Arguments ($send + @('-ClientId', $overrideId)) -ContextFile $otherFile
    Assert-Fixture ($result.Code -eq 0 -and $result.Record.argv[-2] -ceq $overrideId) 'An explicit client ID must win over the context default.'
    $result = Invoke-Fixture -Arguments $send -ContextFile 'other-context.json'
    Assert-Fixture ($result.Code -eq 0 -and $result.Record.context_file -ceq $otherFile) 'A relative context path must reach the child as an absolute path.'
    $checkArguments = @('check', '-State', $state) + $base
    foreach ($arguments in @(, $checkArguments) + @(, $send)) {
        $result = Invoke-Fixture -Arguments $arguments -InlineContext
        Assert-Fixture ($result.Code -eq 0 -and -not $result.Record.inline_context_present -and $result.Record.context_file -ceq $script:contextFile) 'An inherited inline context must be removed from the child environment.'
    }
    $result = Invoke-Fixture -Arguments $send -PythonExit 9
    Assert-Fixture ($result.Code -eq 1 -and $result.Output -ceq 'Facade command failed.') 'Child failure was not redacted.'
    $result = Invoke-Fixture -Arguments $send -Mode 'boundary-secret'
    Assert-Fixture ($result.Code -eq 0 -and $result.Record.stdin_bytes -eq 4096) '4096-byte boundary was rejected.'
    $result = Invoke-Fixture -Arguments $send -Mode 'unicode-boundary-secret'
    Assert-Fixture ($result.Code -eq 0 -and $result.Record.stdin_bytes -eq 4096) '4096-byte Unicode boundary was rejected.'

    Assert-NotReady (Invoke-Fixture -Arguments (@('readiness') + $base))
    # A context that is synthetic, incomplete, unreadable or missing is "not ready" before anything is launched or retrieved.
    $phase1Document = New-RealisticDocument
    $phase1Document.Remove('servicenow'); $phase1Document.Remove('operator_object_id'); $phase1Document['agent'].Remove('endpoint')
    $phase1File = Join-Path $fixture 'phase1-context.json'
    Write-ContextFile $phase1File $phase1Document
    $brokenFile = Join-Path $fixture 'broken-context.json'
    [IO.File]::WriteAllText($brokenFile, '{"schema":')
    $refusedFiles = @(
        (Join-Path $repo 'tests\fixtures\context.synthetic.json'), (Join-Path $repo 'tests\fixtures\context.alt.json'),
        $phase1File, $brokenFile, (Join-Path $fixture 'absent-context.json'))
    foreach ($refused in $refusedFiles) {
        foreach ($arguments in @((@('check', '-State', $state) + $base), (@('prepare', '-State', $state, '-Synthetic') + $base), $send, (@('readiness') + $live))) {
            Assert-NotReady (Invoke-Fixture -Arguments $arguments -ContextFile $refused)
        }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $repo 'infra\environments\context.generated.json'))) {
        Assert-NotReady (Invoke-Fixture -Arguments (@('check', '-State', $state) + $base) -NoContextFile)
    }
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
