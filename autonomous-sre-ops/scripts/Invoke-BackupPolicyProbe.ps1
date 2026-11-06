#Requires -Version 7.5
[CmdletBinding(SupportsShouldProcess)]
param(
    [ValidateSet('Preflight', 'Trigger', 'Cleanup')][string]$Action = 'Preflight',
    [ValidateRange(5, 120)][int]$MaxFaultMinutes = 120,
    [ValidateRange(35, 150)][int]$WatchdogMinutes = 150,
    [string]$ScenarioId,
    [string]$EvidenceDirectory,
    [string]$ContextPath
)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
Set-StrictMode -Version Latest
# Conventions of the demo environment (not deployment identifiers): the policy scope is the resource group Azure Backup
# creates for restore points, the assignment and task names are fixed, and the canary needs only some Azure region.
$backupGroup = 'AzureBackupRG_centralus_1'
$assignmentName = 'asre-demo-bkp-deny-probe'
$taskName = 'asre-bkp-policy-probe-watchdog'
# Azure built-in policy definition "Not allowed resource types": the same in every tenant, not a deployment identifier.
$builtinId = '/providers/Microsoft.Authorization/policyDefinitions/6c112d4e-5bc7-47ae-a041-ea2d9dccd749'
$deniedTypes = @(
    'Microsoft.Compute/restorePointCollections'
    'Microsoft.Compute/restorePointCollections/restorePoints'
    'Microsoft.Compute/restorePointCollections/restorePoints/diskRestorePoints'
    'Microsoft.Compute/snapshots'
    'Microsoft.Network/applicationSecurityGroups' # canary type, only used to detect enforcement
)
$enforceWaitMinutes = 30 # never submit a backup if enforcement is not observed within this
$observeMinutes = 15     # post-cleanup wait for the canary (and the ARG row after a Failed job)
$terminal = @('Completed', 'Failed', 'Cancelled', 'CompletedWithWarnings')
# az needs a file for the template, so the canary is written under the evidence directory on first use.
$canaryTemplate = @'
{
  "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
  "contentVersion": "1.0.0.0",
  "resources": [
    {
      "type": "Microsoft.Network/applicationSecurityGroups",
      "apiVersion": "2023-09-01",
      "name": "asre-probe-canary-asg",
      "location": "centralus",
      "properties": {}
    }
  ]
}
'@
if ($ScenarioId -and [guid]::Parse($ScenarioId).ToString() -cne $ScenarioId) { throw 'ScenarioId must be a canonical lowercase UUID.' }
if ($Action -ceq 'Cleanup' -and (-not $ScenarioId -or -not $EvidenceDirectory)) { throw 'Cleanup requires the original ScenarioId and EvidenceDirectory.' }
if (-not $ScenarioId) { $ScenarioId = [guid]::NewGuid().ToString() }
if (-not $EvidenceDirectory) { $EvidenceDirectory = Join-Path ([IO.Path]::GetTempPath()) 'asre-infrastructure-evidence' }
if (-not [IO.Path]::IsPathFullyQualified($EvidenceDirectory)) { throw 'EvidenceDirectory must be absolute.' }
# The watchdog passes this path inside a quoted argument, so a trailing separator or a drive root would break it.
$EvidenceDirectory = [IO.Path]::GetFullPath($EvidenceDirectory).TrimEnd('\', '/')
if ($EvidenceDirectory.Length -le 2) { throw 'EvidenceDirectory must not be a drive root.' }
if ($Action -ceq 'Trigger' -and $WatchdogMinutes -lt $MaxFaultMinutes + 30) { throw 'WatchdogMinutes must be at least 30 minutes after MaxFaultMinutes.' }
# The target and the vault come from the deployment context only (reading the file is offline; no Azure call happens before
# the gate). The resolved absolute path is handed to the watchdog, whose fresh session must read the same document.
if (-not (Get-Command Get-AsreContext -ErrorAction SilentlyContinue)) { . (Join-Path $PSScriptRoot 'Context.Common.ps1') }
$contextFile = $ContextPath
if ([string]::IsNullOrWhiteSpace($contextFile)) { $contextFile = $env:ASRE_CONTEXT_FILE }
if ([string]::IsNullOrWhiteSpace($contextFile)) { $contextFile = Join-Path (Split-Path $PSScriptRoot -Parent) 'infra\environments\context.generated.json' }
$contextFile = [IO.Path]::GetFullPath($contextFile)
if ($contextFile.Contains('"')) { throw 'ContextPath must not contain a quotation mark.' }
$context = Get-AsreContext -Path $contextFile
$subscription = $context['subscription_id']
$group = $context['targets']['backup']['resource_group']
$vm = $context['targets']['backup']['name']
$vault = $context['recovery_vault']['name']
$vaultGroup = $context['recovery_vault']['resource_group']
$scope = "/subscriptions/$subscription/resourceGroups/$backupGroup"
$assignmentUrl = "https://management.azure.com$scope/providers/Microsoft.Authorization/policyAssignments/${assignmentName}?api-version=2023-04-01"
$containerName = "IaasVMContainer;iaasvmcontainerv2;$group;$vm"
$itemName = "VM;iaasvmcontainerv2;$group;$vm"
$jobPrefix = "/subscriptions/$subscription/resourceGroups/$vaultGroup/providers/Microsoft.RecoveryServices/vaults/$vault/backupJobs/"
if ($Action -cne 'Preflight' -and -not $PSCmdlet.ShouldProcess("$subscription/$backupGroup", "$Action bounded policy-deny backup probe $ScenarioId")) { return }
Assert-AsreSubscription -Context $context
$null = [IO.Directory]::CreateDirectory($EvidenceDirectory)
$prefix = Join-Path $EvidenceDirectory ('backup-policy-' + $ScenarioId)
$invocation = $prefix + '-' + $Action.ToLowerInvariant() + '-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
$script:LogWriter = $null
$script:PollWriter = $null
$script:CanaryPath = $null

function Save-New([string]$Path, [string]$Text) {
    $stream = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
    try {
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes($Text)
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    } finally { $stream.Dispose() }
}
function Save-Json([string]$Path, $Value) { Save-New $Path (($Value | ConvertTo-Json -Depth 30) + "`n") }
function Open-New([string]$Path) {
    $stream = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
    $writer = [IO.StreamWriter]::new($stream, [Text.UTF8Encoding]::new($false))
    $writer.NewLine = "`n"
    $writer.AutoFlush = $true
    $writer
}
function Write-Log([string]$Message) {
    $now = [DateTimeOffset]::UtcNow
    Write-Host ('{0:HH:mm:ss}Z {1}' -f $now, $Message)
    if ($script:LogWriter) { $script:LogWriter.WriteLine($now.ToString('o') + ' ' + $Message) }
}
function Get-Iso($Value) { if ($null -ne $Value) { $Value.ToString('o') } }
function Get-Path($Value, [string[]]$Path) {
    foreach ($key in $Path) {
        if ($Value -isnot [Collections.IDictionary] -or -not $Value.Contains($key)) { return $null }
        $Value = $Value[$key]
    }
    $Value
}
function Invoke-Az([string[]]$AzArgs, [switch]$AllowFailure) {
    $savedPreference = $ErrorActionPreference
    $savedUtf8 = $env:PYTHONUTF8
    $ErrorActionPreference = 'Continue'
    $env:PYTHONUTF8 = '1'
    try { $out = & az @AzArgs --only-show-errors 2>&1; $code = $LASTEXITCODE } finally { $ErrorActionPreference = $savedPreference; $env:PYTHONUTF8 = $savedUtf8 }
    $kept = [Collections.Generic.List[string]]::new()
    $afterWarning = $false
    foreach ($item in $out) {
        $line = [string]$item
        if ($item -is [Management.Automation.ErrorRecord]) {
            if ($line -match 'SyntaxWarning:') { $afterWarning = $true; continue }
            if ($afterWarning -and $line -match '^\s') { $afterWarning = $false; continue }
        }
        $afterWarning = $false
        $kept.Add($line)
    }
    $text = $kept -join "`n"
    if ($code -ne 0 -and -not $AllowFailure) { throw ('az failed ({0}): {1}{2}{3}' -f $code, ($AzArgs -join ' '), "`n", $text) }
    [pscustomobject]@{ Code = $code; Text = $text }
}
function Read-Json($Result) { $Result.Text | ConvertFrom-Json -AsHashtable -DateKind String }
function Test-Canary {
    if (-not $script:CanaryPath) {
        $script:CanaryPath = $invocation + '-canary.json'
        Save-New $script:CanaryPath $canaryTemplate
    }
    # --name: az would otherwise derive the deployment name from the long evidence file name, and ARM caps it at 64 characters.
    $r = Invoke-Az @('deployment', 'group', 'validate', '--subscription', $subscription, '--resource-group', $backupGroup, '--name', 'canary', '--template-file', $script:CanaryPath, '--output', 'none') -AllowFailure
    if ($r.Code -eq 0) { return 'allowed' }
    if ($r.Text -match 'RequestDisallowedByPolicy|disallowed by policy') { return 'denied' }
    return 'error: ' + $r.Text
}
function Get-AssignmentState {
    $r = Invoke-Az @('rest', '--method', 'get', '--subscription', $subscription, '--url', $assignmentUrl, '--output', 'none') -AllowFailure
    if ($r.Code -eq 0) { return 'present' }
    if ($r.Text -match 'NotFound|could not be found') { return 'absent' }
    return 'unknown: ' + $r.Text
}
function Get-BackupJob([string]$Name) {
    $r = Invoke-Az @('backup', 'job', 'show', '--subscription', $subscription, '--resource-group', $vaultGroup, '--vault-name', $vault, '--name', $Name, '--output', 'json') -AllowFailure
    if ($r.Code -eq 0) { Read-Json $r }
}
function Get-ArgJob([string]$QueryFile) {
    $r = Invoke-Az @('rest', '--method', 'post', '--subscription', $subscription, '--url', 'https://management.azure.com/providers/Microsoft.ResourceGraph/resources?api-version=2021-03-01', '--body', ('@' + $QueryFile), '--output', 'json') -AllowFailure
    if ($r.Code -eq 0) { @(Get-Path (Read-Json $r) 'data') | Select-Object -First 1 }
}
function Get-ItemState {
    $r = Invoke-Az @('backup', 'item', 'list', '--subscription', $subscription, '--resource-group', $vaultGroup, '--vault-name', $vault, '--backup-management-type', 'AzureIaasVM', '--workload-type', 'VM', '--output', 'json') -AllowFailure
    if ($r.Code -ne 0) { return $r.Text }
    @(Read-Json $r) | ForEach-Object { [ordered]@{ name = (Get-Path $_ 'name'); protectionState = (Get-Path $_ 'properties', 'protectionState'); healthStatus = (Get-Path $_ 'properties', 'healthStatus'); lastBackupStatus = (Get-Path $_ 'properties', 'lastBackupStatus'); lastBackupTime = (Get-Path $_ 'properties', 'lastBackupTime') } }
}
function Remove-Assignment([switch]$Unconditional) {
    # The probe deletes unconditionally once its PUT was attempted (the PUT can land even if az failed). Cleanup deletes only after a GET shows the assignment.
    if (-not $Unconditional) {
        $state = Get-AssignmentState
        for ($i = 1; $i -lt 3 -and $state -cnotin @('present', 'absent'); $i++) { Start-Sleep -Seconds 15; $state = Get-AssignmentState }
        if ($state -ceq 'absent') { Write-Log 'probe policy assignment is not present'; return $true }
        if ($state -cne 'present') { Write-Log "assignment lookup inconclusive: $state" }
    }
    Write-Log 'cleanup: deleting the probe policy assignment'
    for ($i = 1; $i -le 5; $i++) {
        $d = Invoke-Az @('rest', '--method', 'delete', '--subscription', $subscription, '--url', $assignmentUrl, '--output', 'none') -AllowFailure
        if ($d.Code -eq 0) { break }
        Write-Log "delete attempt $i failed: $($d.Text)"
        Start-Sleep -Seconds 15
    }
    $gone = (Get-AssignmentState) -ceq 'absent'
    Write-Log "assignment absent: $gone"
    $gone
}
function Remove-Watchdog {
    if (-not (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)) { Write-Log 'watchdog task is not registered'; return $true }
    try { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop; Write-Log 'watchdog task unregistered'; return $true } catch { Write-Log "watchdog unregister failed: $_"; return $false }
}
function Wait-Observation([bool]$WantArg, [string]$JobName) {
    $argSeen = $null
    $canary = 'pending'
    $queryFile = $null
    if ($WantArg) {
        $queryFile = $prefix + '-arg-query.json'
        $query = "RecoveryServicesResources | where type =~ 'microsoft.recoveryservices/vaults/backupjobs' | where id endswith '/$JobName' | project id, status = tostring(properties.status), endTime = tostring(properties.endTime)"
        Save-Json $queryFile ([ordered]@{ subscriptions = @($subscription); query = $query; options = @{ resultFormat = 'objectArray' } })
    }
    $limit = [DateTimeOffset]::UtcNow.AddMinutes($observeMinutes)
    while ($true) {
        if ($WantArg -and -not $argSeen) {
            $row = Get-ArgJob $queryFile
            if ($row -and (Get-Path $row 'status') -eq 'Failed') { $argSeen = [DateTimeOffset]::UtcNow; Write-Log ('ARG shows the Failed job (endTime {0})' -f (Get-Path $row 'endTime')) }
        }
        if ($canary -cne 'allowed') { $canary = Test-Canary }
        if (($argSeen -or -not $WantArg) -and $canary -ceq 'allowed') { break }
        if ([DateTimeOffset]::UtcNow -ge $limit) { break }
        Start-Sleep -Seconds 30
    }
    [ordered]@{ canary = $canary; arg_first_seen_failed_at = (Get-Iso $argSeen) }
}
function Invoke-Preflight {
    Write-Log 'preflight: VM, assignment, jobs, backup RG, canary baseline'
    $iv = Read-Json (Invoke-Az @('vm', 'get-instance-view', '--subscription', $subscription, '--resource-group', $group, '--name', $vm, '--output', 'json'))
    $powerStatus = @(Get-Path $iv 'instanceView', 'statuses') | Where-Object { (Get-Path $_ 'code') -like 'PowerState/*' } | Select-Object -First 1
    $power = Get-Path $powerStatus 'code'
    $agent = (@(Get-Path $iv 'instanceView', 'vmAgent', 'statuses') | Where-Object { $_ } | ForEach-Object { Get-Path $_ 'displayStatus' }) -join ','
    if ($power -ne 'PowerState/running' -or $agent -ne 'Ready') { throw "VM is not running with a Ready agent: $power / $agent" }

    $state = Get-AssignmentState
    if ($state -ceq 'present') { throw 'The probe policy assignment already exists; run -Action Cleanup before starting.' }
    if ($state -cne 'absent') { throw "Unexpected assignment lookup result: $state" }
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) { throw 'The watchdog task already exists; run -Action Cleanup for the previous episode before starting.' }

    $jobs = @(Read-Json (Invoke-Az @('backup', 'job', 'list', '--subscription', $subscription, '--resource-group', $vaultGroup, '--vault-name', $vault, '--output', 'json')))
    if (@($jobs | Where-Object { (Get-Path $_ 'properties', 'status') -notin $terminal }).Count) { throw 'A vault backup job is not terminal; not starting.' }

    $rgRes = @(Read-Json (Invoke-Az @('resource', 'list', '--subscription', $subscription, '--resource-group', $backupGroup, '--output', 'json')))
    if ($rgRes.Count -ne 1 -or (Get-Path $rgRes[0] 'type') -ne 'Microsoft.Compute/restorePointCollections') { throw 'The backup RG does not hold exactly the one expected restore point collection.' }

    $baseline = Test-Canary
    if ($baseline -ne 'allowed') { throw "Canary baseline is not 'allowed': $baseline" }
    $itemBefore = Get-ItemState
    Write-Log ('baseline canary={0}; vm={1}/{2}; item={3}' -f $baseline, $power, $agent, ($itemBefore | ConvertTo-Json -Compress -Depth 5))
    [ordered]@{ vm = "$power/$agent"; canary = $baseline; item_before = $itemBefore }
}
function Invoke-Trigger {
    if ([IO.File]::Exists($prefix + '-intent.json')) { throw 'This episode already has an intent; the probe already ran. Do not repeat backup-now.' }
    $pre = Invoke-Preflight

    $armed = $false; $assignmentSent = $false; $gone = $true
    $watchdogAt = $null; $assignedAt = $null; $enforcedAt = $null; $submittedAt = $null
    $outcome = 'not_started'; $errorText = $null; $jobName = $null; $final = $null
    try {
        # The watchdog is armed before any fault exists.
        $watchdogAt = (Get-Date).AddMinutes($WatchdogMinutes)
        $taskArguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -ContextPath "{3}" -Action Cleanup -ScenarioId {1} -EvidenceDirectory "{2}"' -f $PSCommandPath, $ScenarioId, $EvidenceDirectory, $contextFile
        $taskAction = New-ScheduledTaskAction -Execute (Join-Path $PSHOME 'pwsh.exe') -Argument $taskArguments
        $taskTrigger = New-ScheduledTaskTrigger -Once -At $watchdogAt
        $taskSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
        $null = Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger $taskTrigger -Settings $taskSettings -Description 'Backstop: removes the temporary ASRE backup-failure probe policy assignment.'
        if (-not (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)) { throw 'Watchdog task was not registered; nothing was changed in Azure.' }
        $armed = $true
        Write-Log ('watchdog task armed for {0:o}' -f $watchdogAt)

        Save-Json ($prefix + '-intent.json') ([ordered]@{ scenario_id = $ScenarioId; started_at = [DateTimeOffset]::UtcNow.ToString('o'); target = "$subscription/$group/$vm"; deny_scope = $scope; assignment = $assignmentName
            denied_types = $deniedTypes; max_fault_minutes = $MaxFaultMinutes; watchdog_at_local = $watchdogAt.ToString('o'); vm = $pre.vm; item_before = $pre.item_before })

        $deleteBy = [DateTimeOffset]::UtcNow.AddMinutes($WatchdogMinutes).ToString('o')
        Save-Json ($prefix + '-assignment.json') ([ordered]@{ properties = [ordered]@{
            displayName = 'ASRE demo backup-failure probe (temporary deny)'
            description = 'Temporary, bounded Deny used to induce one real Azure Backup snapshot failure for the ASRE demo. Safe to delete.'
            policyDefinitionId = $builtinId
            enforcementMode = 'Default'
            parameters = [ordered]@{ effect = @{ value = 'Deny' }; listOfResourceTypesNotAllowed = @{ value = $deniedTypes } }
            metadata = [ordered]@{ purpose = 'asre-demo-backup-failure-probe'; deleteBy = $deleteBy; owner = 'asre-demo' }
            nonComplianceMessages = @(@{ message = 'Creating or changing snapshot and restore point resources in this resource group is blocked by the current organizational policy.' })
        } })
        $assignmentSent = $true
        $assignedAt = [DateTimeOffset]::UtcNow
        $null = Invoke-Az @('rest', '--method', 'put', '--subscription', $subscription, '--url', $assignmentUrl, '--body', ('@' + $prefix + '-assignment.json'), '--output', 'none')
        Write-Log 'policy assignment created; waiting for ARM to enforce it (canary validate)'

        $limit = $assignedAt.AddMinutes($enforceWaitMinutes)
        $canaryErrors = 0
        while ([DateTimeOffset]::UtcNow -lt $limit) {
            $c = Test-Canary
            if ($c -eq 'denied') { $enforcedAt = [DateTimeOffset]::UtcNow; break }
            if ($c -like 'error*') {
                $canaryErrors++
                Write-Log "canary validate error ($canaryErrors/3): $c"
                if ($canaryErrors -ge 3) { throw $c }
            } else { $canaryErrors = 0 }
            Start-Sleep -Seconds 30
        }

        if (-not $enforcedAt) {
            $outcome = 'enforcement_not_observed'
            Write-Log 'policy enforcement was not observed in time; no backup submitted'
        } else {
            Write-Log ('policy enforced after {0:n0}s; submitting ONE on-demand backup' -f ($enforcedAt - $assignedAt).TotalSeconds)
            $submittedAt = [DateTimeOffset]::UtcNow
            $submit = Invoke-Az @('backup', 'protection', 'backup-now', '--subscription', $subscription, '--resource-group', $vaultGroup, '--vault-name', $vault, '--backup-management-type', 'AzureIaasVM', '--container-name', $containerName, '--item-name', $itemName, '--output', 'json') -AllowFailure
            Save-New ($prefix + '-native-job.json') ($submit.Text + "`n")
            if ($submit.Code -ne 0) { throw 'backup-now outcome unknown; not repeating. See native-job.json and list vault jobs.' }
            $job = Read-Json $submit
            $jobId = Get-Path $job 'id'
            # With the Deny already enforced the job can be terminal (Failed) by the time backup-now returns.
            if (-not $jobId -or -not $jobId.StartsWith($jobPrefix, [StringComparison]::OrdinalIgnoreCase) -or (Get-Path $job 'properties', 'operation') -ne 'Backup' -or (Get-Path $job 'properties', 'status') -notin (@('InProgress') + $terminal)) {
                throw 'Unexpected backup-now response; see native-job.json.'
            }
            $jobName = Get-Path $job 'name'
            Write-Log "job $jobName submitted"

            $outcome = 'deadline'
            $script:PollWriter = Open-New ($prefix + '-poll.jsonl')
            $last = ''; $beat = Get-Date
            $deadline = $assignedAt.AddMinutes($MaxFaultMinutes)
            while ($true) {
                $j = Get-BackupJob $jobName
                if ($j) {
                    $final = $j
                    $status = Get-Path $j 'properties', 'status'
                    $tasks = @(@(Get-Path $j 'properties', 'extendedInfo', 'tasksList') | Where-Object { $_ })
                    $taskText = ($tasks | ForEach-Object { '{0}={1}' -f (Get-Path $_ 'taskId'), (Get-Path $_ 'status') }) -join '; '
                    $line = '{0} | {1}' -f $status, $taskText
                    if ($line -ne $last -or ((Get-Date) - $beat).TotalMinutes -ge 5) { Write-Log $line; $last = $line; $beat = Get-Date }
                    $script:PollWriter.WriteLine(([ordered]@{ t = [DateTimeOffset]::UtcNow.ToString('o'); status = $status; tasks = $taskText } | ConvertTo-Json -Compress))
                    if ($status -notin @('InProgress', 'Cancelling')) { $outcome = 'terminal_' + $status; break }
                    $snap = $tasks | Where-Object { (Get-Path $_ 'taskId') -eq 'Take Snapshot' } | Select-Object -First 1
                    if ($snap -and (Get-Path $snap 'status') -eq 'Completed') { $outcome = 'snapshot_completed_policy_ineffective'; break }
                } else { Write-Log 'job read failed; retrying' }
                if ([DateTimeOffset]::UtcNow -ge $deadline) { break }
                Start-Sleep -Seconds 30
            }
        }
    } catch {
        $outcome = 'error'
        $errorText = $_.Exception.Message
        Write-Log "ERROR: $errorText"
    } finally {
        if ($assignmentSent) { $gone = Remove-Assignment -Unconditional }
    }

    $cleanupEnd = [DateTimeOffset]::UtcNow
    if ($jobName) { $again = Get-BackupJob $jobName; if ($again) { $final = $again } }
    if ($final) { Save-Json ($prefix + '-job-final.json') $final }
    $wantArg = ($outcome -ceq 'terminal_Failed')
    $observed = [ordered]@{ canary = 'skipped'; arg_first_seen_failed_at = $null }
    if ($gone -and ($wantArg -or $assignmentSent)) { $observed = Wait-Observation $wantArg $jobName }
    $unregistered = $false
    if (-not $armed) {
        $unregistered = $true
    } elseif ($gone -and $observed.canary -cin @('allowed', 'skipped')) {
        $unregistered = Remove-Watchdog
    } else {
        Write-Log 'CLEANUP NOT VERIFIED: the watchdog task stays armed; run -Action Cleanup with the same ScenarioId and EvidenceDirectory.'
    }
    $itemAfter = Get-ItemState
    $errorCodes = (@(Get-Path $final 'properties', 'errorDetails') | Where-Object { $_ } | ForEach-Object { Get-Path $_ 'errorCode' }) -join ','
    $summary = [ordered]@{
        scenario_id = $ScenarioId; outcome = $outcome; error = $errorText; job = $jobName; error_codes = $errorCodes
        assigned_at = (Get-Iso $assignedAt); enforced_at = (Get-Iso $enforcedAt); submitted_at = (Get-Iso $submittedAt)
        job_start = (Get-Path $final 'properties', 'startTime'); job_end = (Get-Path $final 'properties', 'endTime'); job_status = (Get-Path $final 'properties', 'status')
        assignment_absent = $gone; post_clean_canary = $observed.canary; arg_first_seen_failed_at = $observed.arg_first_seen_failed_at
        watchdog_unregistered = $unregistered; cleanup_finished_at = $cleanupEnd.ToString('o'); item_after = $itemAfter
    }
    Save-Json ($prefix + '-summary.json') $summary
    $summary | ConvertTo-Json -Depth 6
    if (-not $gone) { throw 'CLEANUP NOT VERIFIED: the probe policy assignment may still exist; the watchdog stays armed. Run -Action Cleanup with the same ScenarioId and EvidenceDirectory.' }
    if ($outcome -ceq 'error') { throw $errorText }
}
function Invoke-Cleanup {
    $gone = Remove-Assignment
    $observed = [ordered]@{ canary = 'skipped'; arg_first_seen_failed_at = $null }
    if ($gone) { $observed = Wait-Observation $false '' }
    $unregistered = $false
    if ($gone -and $observed.canary -ceq 'allowed') {
        $unregistered = Remove-Watchdog
    } else {
        Write-Log 'CLEANUP NOT VERIFIED: the watchdog task, if any, is left in place; run -Action Cleanup again.'
    }
    $result = [ordered]@{ scenario_id = $ScenarioId; assignment_absent = $gone; canary = $observed.canary; watchdog_unregistered = $unregistered; finished_at = [DateTimeOffset]::UtcNow.ToString('o') }
    Save-Json ($invocation + '-result.json') $result
    $result | ConvertTo-Json -Depth 4
    if (-not $gone) { throw 'The probe policy assignment could not be confirmed absent.' }
    if ($observed.canary -cne 'allowed') { throw "The canary is not allowed again yet: $($observed.canary)" }
}

$script:LogWriter = Open-New ($invocation + '.log')
try {
    switch ($Action) {
        'Preflight' {
            $pre = Invoke-Preflight
            Write-Log 'Preflight: nothing was changed.'
            [ordered]@{ status = 'preflight_passed'; scenario_id = $ScenarioId; vm = $pre.vm; canary = $pre.canary; item_before = $pre.item_before } | ConvertTo-Json -Depth 6
        }
        'Trigger' { Invoke-Trigger }
        'Cleanup' { Invoke-Cleanup }
    }
} finally {
    if ($script:PollWriter) { $script:PollWriter.Dispose() }
    $script:LogWriter.Dispose()
}
