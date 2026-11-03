#Requires -Version 7.5
[CmdletBinding(SupportsShouldProcess)]
param(
    [ValidateSet('Trigger', 'Status', 'Reset')][string]$Action = 'Status',
    [ValidateRange(180, 900)][int]$LifetimeSeconds = 900,
    [string]$ScenarioId,
    [string]$EvidenceDirectory
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$subscription = '00000001-000a-4000-a000-000000000001'
$group = 'rg-asre-backup-cus'
$vm = 'vm-asre-backup-cus'
$vault = 'rsv-asre-cus'
if ($ScenarioId -and [guid]::Parse($ScenarioId).ToString() -cne $ScenarioId) { throw 'ScenarioId must be a canonical lowercase UUID.' }
if ($Action -cne 'Trigger' -and -not $ScenarioId) { throw 'Status/Reset require the original ScenarioId and evidence directory.' }
if (-not $ScenarioId) { $ScenarioId = [guid]::NewGuid().ToString() }
if (-not $EvidenceDirectory) { $EvidenceDirectory = Join-Path ([IO.Path]::GetTempPath()) 'asre-infrastructure-evidence' }
if (-not [IO.Path]::IsPathFullyQualified($EvidenceDirectory)) { throw 'EvidenceDirectory must be absolute.' }
if (-not $PSCmdlet.ShouldProcess("$subscription/$group/$vm", "$Action bounded backup incident $ScenarioId")) { return }
$null = [IO.Directory]::CreateDirectory($EvidenceDirectory)
$prefix = Join-Path $EvidenceDirectory ('backup-' + $ScenarioId)
function Save-New([string]$Path, [string]$Text) {
    $stream = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
    try {
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes($Text)
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    } finally { $stream.Dispose() }
}
function Invoke-Guest([string]$GuestAction, [long]$Deadline = 0, [long]$AdmissionCutoff = 0) {
    if ($GuestAction -cne 'Trigger') {
        $ready = (& az vm get-instance-view --subscription $subscription --resource-group $group --name $vm `
            --query 'vmAgent.statuses[].displayStatus' --output json --only-show-errors) -join "`n"
        if ($LASTEXITCODE -or 'Ready' -cnotin @($ready | ConvertFrom-Json)) {
            throw 'Native guest agent is not Ready; no RunCommand submitted. Use the existing independent SSH inspection and original watchdog recovery.'
        }
    }
    $binding = @{
        action = $GuestAction; scenario_id = $ScenarioId; absolute_deadline = $Deadline; admission_cutoff = $AdmissionCutoff
        helper_sha256 = (Get-FileHash (Join-Path $PSScriptRoot '..\workloads\linux-demo-service\guest_control.py') -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($binding | ConvertTo-Json -Compress)))
    $program = @'
import base64,hashlib,json,subprocess,time
from pathlib import Path
from urllib.request import urlopen
binding=json.loads(base64.b64decode("ASRE_BINDING"))
helper=Path("/usr/local/lib/asre-demo/guest_control.py")
if hashlib.sha256(helper.read_bytes()).hexdigest()!=binding["helper_sha256"]:
    raise RuntimeError("The installed backup helper differs from this operator release.")
policy=json.loads(Path("/etc/asre-demo/policy.json").read_bytes())
if policy["role"]!="backup" or policy["owner"]!="asre-demo" or policy["faults_enabled"] is not False:
    raise RuntimeError("The fixed backup role and disabled runner fault policy are required.")
state_path=Path("/var/lib/asre-demo/state.json")
state=json.loads(state_path.read_bytes()) if state_path.exists() else None
if binding["action"]=="Status":
    with urlopen("http://127.0.0.1:8080/health",timeout=5) as response:
        health=json.load(response)
    result={"status":"observed","state":state,
            "health":health,"faults_enabled":False,"owned_data_empty":not any(Path("/srv/asre-demo/data").iterdir())}
elif binding["action"]=="Reset" and state and state["status"]=="restored":
    if state["scenario_id"]!=binding["scenario_id"]:
        raise RuntimeError("The original recovery owner does not match.")
    result=state
else:
    args=["/usr/bin/python3","-B",str(helper)]
    if binding["action"]=="Trigger":
        if time.time()>=binding["admission_cutoff"]:
            raise RuntimeError("The original fault admission expired; no fault was started.")
        remaining=int(binding["absolute_deadline"]-time.time())-5
        if not 120<=remaining<=900:
            raise RuntimeError("Insufficient original cleanup lifetime; no fault was started.")
        request={"operation":"stop_guest_agent","scenario_id":binding["scenario_id"],
                 "ttl_seconds":remaining,"byte_limit":0,"interval_seconds":2}
        args.append("--operator-fault")
    else:
        request={"operation":"restore_guest_agent","scenario_id":binding["scenario_id"]}
    completed=subprocess.run(args,input=json.dumps(request),capture_output=True,text=True,timeout=75)
    if completed.returncode:
        raise RuntimeError("Backup helper rejected the operation: "+completed.stderr[-1600:])
    result=json.loads(completed.stdout)
print(json.dumps({"observed_at":time.time(),"action":binding["action"],"result":result},sort_keys=True))
'@
    $program = $program.Replace('ASRE_BINDING', $encoded)
    $commandPrefix = $prefix + '-' + $GuestAction.ToLowerInvariant() + '-' + [guid]::NewGuid()
    Save-New ($commandPrefix + '.sh') "#!/bin/sh`nset -eu`n/usr/bin/python3 -I -B - <<'ASRE_BACKUP'`n$program`nASRE_BACKUP`n"
    $arguments = @('vm','run-command','invoke','--subscription',$subscription,'--resource-group',$group,
        '--name',$vm,'--command-id','RunShellScript','--scripts',('@' + $commandPrefix + '.sh'),'--output','json','--only-show-errors')
    if ($GuestAction -ceq 'Trigger') { $arguments += '--no-wait' }
    $raw = (& az @arguments) -join "`n"
    $code = $LASTEXITCODE
    Save-New ($commandPrefix + '-platform.json') $raw
    if ($code) { throw "Guest operation outcome is unknown; do not resubmit a fault. Evidence: $commandPrefix" }
    if ($GuestAction -ceq 'Trigger') { return @{ status = 'submitted_activation_unverified'; evidence = $commandPrefix } }
    $platform = $raw | ConvertFrom-Json -AsHashtable -DateKind String
    if ($platform.value.Count -ne 1 -or $platform.value[0].code -cne 'ProvisioningState/succeeded') {
        throw "Guest operation did not report success: $commandPrefix"
    }
    $framing = "Enable succeeded: `n[stdout]`n"
    $message = $platform.value[0].message
    if (-not $message.StartsWith($framing,[StringComparison]::Ordinal)) { throw "Invalid native guest framing: $commandPrefix" }
    $parts = $message.Substring($framing.Length).Split("`n[stderr]`n",[StringSplitOptions]::None)
    if ($parts.Count -ne 2 -or $parts[1] -or -not $parts[0].Trim()) { throw "Guest error or incomplete result: $commandPrefix" }
    $result = $parts[0] | ConvertFrom-Json -AsHashtable -DateKind String
    if ($result.action -cne $GuestAction) { throw 'Guest action mismatch.' }
    if ($GuestAction -ceq 'Reset' -and ($result.result.status -cne 'restored' -or $result.result.scenario_id -cne $ScenarioId)) {
        throw 'The original backup episode was not confirmed restored.'
    }
    return $result.result
}
if ($Action -ceq 'Trigger') {
    if ([IO.File]::Exists($prefix + '-intent.json')) { throw 'This episode already has an intent; do not repeat Trigger.' }
    $guest = Invoke-Guest 'Status'
    if ($guest.status -cne 'observed' -or $guest.health.mode -cne 'normal' -or $guest.health.status -cne 'ok' -or
        $guest.faults_enabled -or -not $guest.owned_data_empty -or ($guest.state -and $guest.state.status -cne 'restored')) {
        throw 'A source-exact healthy, empty backup guest with no active episode is required.'
    }
    $jobs = (& az backup job list --subscription $subscription --resource-group $group --vault-name $vault --output json --only-show-errors) -join "`n"
    if ($LASTEXITCODE) { throw 'Existing native backup job enumeration failed; no new job/fault submitted.' }
    if (@(($jobs | ConvertFrom-Json) | Where-Object {
        $_.properties.status -notin @('Completed','Failed','Cancelled','CompletedWithWarnings')
    }).Count) { throw 'An existing backup operation is not terminal; do not submit a new job/fault.' }
    $begin = [DateTimeOffset]::UtcNow
    $intent = @{ scenario_id = $ScenarioId; started_at = $begin.ToString('o')
        absolute_deadline = $begin.AddSeconds($LifetimeSeconds).ToUnixTimeSeconds()
        admission_cutoff = $begin.AddSeconds(120).ToUnixTimeSeconds(); target = "$subscription/$group/$vm"
        monitoring_changes = 0; investigations_started = 0; case_or_note_writes = 0; protection_or_policy_changes = 0 }
    Save-New ($prefix + '-intent.json') ($intent | ConvertTo-Json -Depth 6)
    $raw = (& az backup protection backup-now --subscription $subscription --resource-group $group --vault-name $vault `
        --backup-management-type AzureIaasVM --container-name 'IaasVMContainer;iaasvmcontainerv2;rg-asre-backup-cus;vm-asre-backup-cus' `
        --item-name 'VM;iaasvmcontainerv2;rg-asre-backup-cus;vm-asre-backup-cus' --output json --only-show-errors) -join "`n"
    $code = $LASTEXITCODE
    Save-New ($prefix + '-native-job.json') $raw
    if ($code) { throw "Backup submission outcome is unknown; do not repeat. Evidence: $prefix" }
    $job = $raw | ConvertFrom-Json -AsHashtable -DateKind String
    $expected = "/subscriptions/$subscription/resourceGroups/$group/providers/Microsoft.RecoveryServices/vaults/$vault/backupJobs/"
    if (-not $job.id -or -not $job.id.StartsWith($expected,[StringComparison]::OrdinalIgnoreCase) -or
        $job.properties.operation -cne 'Backup' -or $job.properties.entityFriendlyName -cne $vm -or
        $job.properties.status -cne 'InProgress' -or [guid]::Parse($job.name).ToString() -cne $job.name -or
        $job.id -ine ($expected + $job.name)) {
        throw "No exact in-progress Backup job confirmed; no fault submitted. Evidence: $prefix"
    }
    $fault = Invoke-Guest 'Trigger' $intent.absolute_deadline $intent.admission_cutoff
    @{ status = 'submitted_not_failure_or_case_proof'; scenario_id = $ScenarioId; native_job_id = $job.name
        absolute_deadline = $intent.absolute_deadline; fault = $fault; evidence = $prefix } | ConvertTo-Json -Depth 8
    return
}
$intent = Get-Content -LiteralPath ($prefix + '-intent.json') -Raw | ConvertFrom-Json -AsHashtable -DateKind String
if ($intent.scenario_id -cne $ScenarioId -or $intent.target -cne "$subscription/$group/$vm") { throw 'Original intent scope mismatch.' }
if ($Action -ceq 'Reset') {
    if ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() -le $intent.absolute_deadline + 5) {
        throw 'Guest-agent pause has no RunCommand reset transport. The original independent watchdog is the recovery path; no reset or renewal was submitted.'
    }
    Invoke-Guest 'Reset' | ConvertTo-Json -Depth 12
    return
}
$job = Get-Content -LiteralPath ($prefix + '-native-job.json') -Raw | ConvertFrom-Json -AsHashtable -DateKind String
if ([guid]::Parse($job.name).ToString() -cne $job.name) { throw 'Original native job identity is invalid.' }
$raw = (& az backup job show --subscription $subscription --resource-group $group --vault-name $vault --name $job.name --output json --only-show-errors) -join "`n"
if ($LASTEXITCODE) { throw 'Native backup job status could not be read.' }
$native = $raw | ConvertFrom-Json -AsHashtable -DateKind String
if ($native.id -ine $job.id) { throw 'Native backup status returned another job.' }
$guest = @{ status = 'not_observed'; recovery_verified = $false
    message = 'While the guest agent may be paused, use the existing pinned-SSH Review inspection. This native job read does not prove guest recovery.' }
if ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() -gt $intent.absolute_deadline + 5) { $guest = Invoke-Guest 'Status' }
@{ status = 'observed'; scenario_id = $ScenarioId; native_job = $native; guest = $guest
    failure_or_case_accepted = $false } | ConvertTo-Json -Depth 25
