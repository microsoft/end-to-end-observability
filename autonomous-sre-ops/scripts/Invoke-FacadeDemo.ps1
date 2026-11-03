#requires -Version 7.0
<#
.SYNOPSIS
Runs the facade demo CLI without putting a live secret in arguments or files.
.DESCRIPTION
Prepare/check are offline. Live commands require an explicitly named, already
established CURRENT USER Microsoft.PowerShell.SecretStore vault and reference.
This script never installs, registers, unlocks, searches for, or falls back to
another vault. Python output is deliberately discarded, including on failure.
The deployment context (ASRE_CONTEXT_FILE, else infra/environments/context.generated.json)
is validated here and handed to the child as ASRE_CONTEXT_FILE; it must carry the
ServiceNow section, and the default client id is the one it names.
Exit codes: 0 completed, 1 command failed, 2 not ready (nothing launched).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [ValidateSet('prepare', 'check', 'readiness', 'send', 'reconcile')]
    [string]$Action,
    [string]$State,
    [string]$InputPath,
    [switch]$Synthetic,
    [string]$ClientId,
    [string]$VaultName,
    [string]$SecretName,
    [switch]$EstablishedCurrentUserStore,
    [switch]$ConfirmNewCase,
    [ValidateSet('create', 'note')][string]$Write,
    [string]$PythonExecutable
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$process = $null
$started = $false
$plainSecret = $null
$secret = $null
$exitCode = 2

try {
    $actionName = $Action.ToLowerInvariant()
    $live = $actionName -in @('readiness', 'send', 'reconcile')
    if ($actionName -ne 'readiness' -and [string]::IsNullOrWhiteSpace($State)) {
        throw 'State is required.'
    }
    if ($actionName -eq 'readiness' -and $PSBoundParameters.ContainsKey('State')) {
        throw 'Readiness has no state argument.'
    }
    if ($actionName -eq 'prepare') {
        if ([bool]$Synthetic -eq (-not [string]::IsNullOrWhiteSpace($InputPath))) {
            throw 'Choose exactly one input source.'
        }
    }
    elseif ($Synthetic -or $PSBoundParameters.ContainsKey('InputPath')) {
        throw 'Input sources are only valid for prepare.'
    }
    if ($actionName -eq 'send' -and -not $ConfirmNewCase) {
        throw 'Explicit new-case intent is required.'
    }
    if ($actionName -ne 'send' -and $ConfirmNewCase) {
        throw 'New-case intent is only valid for send.'
    }
    if (($actionName -eq 'reconcile') -ne $PSBoundParameters.ContainsKey('Write')) {
        throw 'Reconcile requires an explicit write kind.'
    }
    # The context is read and validated here, offline; a synthetic or incomplete one is "not ready".
    if (-not (Get-Command Get-AsreContext -ErrorAction SilentlyContinue)) { . (Join-Path $PSScriptRoot 'Context.Common.ps1') }
    $contextFile = $env:ASRE_CONTEXT_FILE
    if ([string]::IsNullOrWhiteSpace($contextFile)) {
        $contextFile = Join-Path (Split-Path $PSScriptRoot -Parent) 'infra\environments\context.generated.json'
    }
    $contextFile = [IO.Path]::GetFullPath($contextFile)
    $context = Get-AsreContext -Path $contextFile
    if ($null -eq $context['servicenow']) { throw 'The context has no ServiceNow section.' }
    if (-not $PSBoundParameters.ContainsKey('ClientId')) { $ClientId = [string]$context['servicenow']['client_id'] }
    if ($live -and ($ClientId -cnotmatch '^[0-9a-f]{32}$' -or
            -not $EstablishedCurrentUserStore -or
            [string]::IsNullOrWhiteSpace($VaultName) -or
            [string]::IsNullOrWhiteSpace($SecretName))) {
        throw 'An explicit local store and secret reference are required.'
    }
    if ($live -and ([Management.Automation.WildcardPattern]::ContainsWildcardCharacters($VaultName) -or
            [Management.Automation.WildcardPattern]::ContainsWildcardCharacters($SecretName))) {
        throw 'Wildcard store or secret searches are not admitted.'
    }

    # Resolve an application, never a function, alias, command string, or shell.
    if ($PSBoundParameters.ContainsKey('PythonExecutable')) {
        if (-not [IO.Path]::IsPathFullyQualified($PythonExecutable) -or
            -not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
            throw 'An existing absolute executable path is required.'
        }
        $applications = @(Get-Command -Name $PythonExecutable -CommandType Application -ErrorAction Stop)
        $applications = @($applications | Where-Object { $_.Source -eq $PythonExecutable })
    }
    else {
        $applications = @(Get-Command -Name python -CommandType Application -ErrorAction Stop)
    }
    if ($applications.Count -eq 0) { throw 'Python is unavailable.' }
    $python = $applications[0].Source
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Python is unavailable.' }

    $arguments = [Collections.Generic.List[string]]::new()
    foreach ($value in @('-m', 'operations.facade_demo', $actionName)) { $arguments.Add($value) }
    if ($actionName -ne 'readiness') {
        $arguments.Add('--state')
        $arguments.Add($State)
    }
    if ($actionName -eq 'prepare') {
        if ($Synthetic) { $arguments.Add('--synthetic') }
        else {
            $arguments.Add('--input')
            $arguments.Add($InputPath)
        }
    }
    if ($actionName -eq 'send') { $arguments.Add('--confirm-new-case') }
    if ($actionName -eq 'reconcile') {
        $arguments.Add('--write')
        $arguments.Add($Write.ToLowerInvariant())
    }
    if ($live) {
        # This branch is never entered for prepare/check, even for discovery.
        if (@(Get-Module -ListAvailable -Name Microsoft.PowerShell.SecretManagement).Count -eq 0) {
            throw 'SecretManagement is unavailable.'
        }
        Import-Module Microsoft.PowerShell.SecretManagement -ErrorAction Stop *> $null
        $vaults = @(Microsoft.PowerShell.SecretManagement\Get-SecretVault -Name $VaultName `
            -ErrorAction Stop -WarningAction SilentlyContinue -InformationAction SilentlyContinue `
            -Verbose:$false -Debug:$false 3>$null 4>$null 5>$null 6>$null)
        if ($vaults.Count -ne 1 -or $vaults[0].Name -cne $VaultName -or
            $vaults[0].ModuleName -cne 'Microsoft.PowerShell.SecretStore') {
            throw 'The exact supported local vault is unavailable.'
        }
        $values = @(Microsoft.PowerShell.SecretManagement\Get-Secret -Vault $VaultName -Name $SecretName `
            -ErrorAction Stop -WarningAction SilentlyContinue -InformationAction SilentlyContinue `
            -Verbose:$false -Debug:$false 3>$null 4>$null 5>$null 6>$null)
        if ($values.Count -ne 1 -or $values[0] -isnot [Security.SecureString]) {
            throw 'A SecureString is required.'
        }
        $secret = $values[0]
        if ($secret.Length -eq 0 -or $secret.Length -gt 4096) { throw 'Secret length is invalid.' }
        $pointer = [IntPtr]::Zero
        try {
            $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
            $plainSecret = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
        }
        finally {
            if ($pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
        }
        $utf8 = [Text.UTF8Encoding]::new($false, $true)
        if ($utf8.GetByteCount($plainSecret) -gt 4096) { throw 'Secret length is invalid.' }
        for ($index = 0; $index -lt $plainSecret.Length; $index++) {
            if ([char]::IsControl($plainSecret[$index])) { throw 'Control characters are not admitted.' }
        }
        $arguments.Add('--client-id')
        $arguments.Add($ClientId)
        $arguments.Add('--secret-stdin')
    }

    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $python
    $startInfo.WorkingDirectory = Split-Path $PSScriptRoot -Parent
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardInput = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.StandardInputEncoding = [Text.UTF8Encoding]::new($false, $true)
    # The child reads exactly the validated document; an inherited inline context would otherwise compete with it.
    $startInfo.Environment['ASRE_CONTEXT_FILE'] = $contextFile
    [void]$startInfo.Environment.Remove('ASRE_CONTEXT')
    foreach ($argument in $arguments) { $startInfo.ArgumentList.Add($argument) }
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    if (-not $process.Start()) { throw 'Process could not start.' }
    $started = $true
    $exitCode = 1
    # Drain both pipes concurrently into a zero-retention sink; never buffer or
    # relay Python bodies/tracebacks, even when the child fails or is noisy.
    $stdout = $process.StandardOutput.BaseStream.CopyToAsync([IO.Stream]::Null)
    $stderr = $process.StandardError.BaseStream.CopyToAsync([IO.Stream]::Null)
    if ($live) { $process.StandardInput.Write($plainSecret) }
    $process.StandardInput.Close()
    $plainSecret = $null
    if (-not $process.WaitForExit(120000)) { throw 'Process timed out.' }
    if (-not [Threading.Tasks.Task]::WaitAll([Threading.Tasks.Task[]]@($stdout, $stderr), 5000)) {
        throw 'Output did not close.'
    }
    if ($process.ExitCode -ne 0) { throw 'Command failed.' }
    $exitCode = 0
}
catch {
    # Deliberately do not emit exception messages, records, or child output.
}
finally {
    $plainSecret = $null
    $values = $null
    if ($null -ne $secret) { $secret.Dispose() }
    $secret = $null
    if ($null -ne $process) {
        try {
            if ($started -and -not $process.HasExited) { $process.Kill($true) }
        }
        catch { }
        $process.Dispose()
    }
}
switch ($exitCode) {
    0 { Write-Output 'Facade command completed.' }
    1 { Write-Output 'Facade command failed.' }
    2 { Write-Output 'Facade command not ready.' }
}
exit $exitCode
