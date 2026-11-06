#Requires -Version 7.5
[CmdletBinding(SupportsShouldProcess)]
param(
    [ValidateSet('Trigger', 'Status', 'Reset')][string]$Action = 'Status',
    [ValidateSet('Disk', 'WorkloadOom', 'CapacityGrowth')][string]$Fault = 'Disk',
    [ValidateRange(120, 900)][int]$LifetimeSeconds = 900,
    [string]$ScenarioId,
    [string]$EvidenceDirectory
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($ScenarioId -and ([guid]::Parse($ScenarioId).ToString() -cne $ScenarioId)) {
    throw 'ScenarioId must be a canonical lowercase UUID.'
}
if ($Action -ceq 'Reset' -and -not $ScenarioId) { throw 'Reset requires the original ScenarioId.' }
if ($Action -ceq 'Trigger' -and -not $ScenarioId) { $ScenarioId = [guid]::NewGuid().ToString() }
if (-not $EvidenceDirectory) { $EvidenceDirectory = Join-Path ([IO.Path]::GetTempPath()) 'asre-infrastructure-evidence' }
if (-not [IO.Path]::IsPathFullyQualified($EvidenceDirectory)) { throw 'EvidenceDirectory must be absolute.' }
# The target comes from the deployment context only (reading the file is offline; no Azure call happens before the gate).
if (-not (Get-Command Get-AsreContext -ErrorAction SilentlyContinue)) { . (Join-Path $PSScriptRoot 'Context.Common.ps1') }
$context = Get-AsreContext
$subscription = $context['subscription_id']
$group = $context['targets']['linux']['resource_group']
$vm = $context['targets']['linux']['name']
if (-not $PSCmdlet.ShouldProcess("$subscription/$group/$vm", "$Action $Fault incident $ScenarioId")) { return }
Assert-AsreSubscription -Context $context
$null = [IO.Directory]::CreateDirectory($EvidenceDirectory)
$prefix = Join-Path $EvidenceDirectory ("linux-$($Action.ToLowerInvariant())-" + [guid]::NewGuid())
function Save-New([string]$Suffix, [string]$Text) {
    $file = [IO.File]::Open(($prefix + $Suffix), [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
    try {
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes($Text)
        $file.Write($bytes, 0, $bytes.Length)
        $file.Flush($true)
    } finally { $file.Dispose() }
}
$request = switch -CaseSensitive ($Action) {
    Trigger {
        @{
            operation = $(if ($Fault -ceq 'WorkloadOom') { 'start_workload_oom' } else { 'start_disk' })
            scenario_id = $ScenarioId; ttl_seconds = $LifetimeSeconds
            byte_limit = $(if ($Fault -ceq 'WorkloadOom') { 167772160 } else { 3162505216L })
            interval_seconds = $(if ($Fault -ceq 'CapacityGrowth') { 1 } else { 0.25 })
        }
    }
    Reset { @{ operation = 'recover_workload'; scenario_id = $ScenarioId } }
    Status { @{ operation = 'inspect' } }
}
$binding = @{
    action = $Action; request = $request
    helper_sha256 = (Get-FileHash (Join-Path $PSScriptRoot '..\workloads\linux-demo-service\guest_control.py') -Algorithm SHA256).Hash.ToLowerInvariant()
} | ConvertTo-Json -Depth 6 -Compress
$encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($binding))
$program = @'
import base64,hashlib,json,subprocess,time
from pathlib import Path
from urllib.request import urlopen
binding=json.loads(base64.b64decode("ASRE_BINDING"))
helper=Path("/usr/local/lib/asre-demo/guest_control.py")
if hashlib.sha256(helper.read_bytes()).hexdigest()!=binding["helper_sha256"]:
    raise RuntimeError("The installed helper differs from the operator script release.")
if binding["action"]=="Status":
    state_path=Path("/var/lib/asre-demo/state.json")
    with urlopen("http://127.0.0.1:8080/health",timeout=5) as response:
        health=json.load(response)
    policy=json.loads(Path("/etc/asre-demo/policy.json").read_bytes())
    result={"status":"observed","state":json.loads(state_path.read_bytes()) if state_path.exists() else None,
            "health":health,"faults_enabled":policy["faults_enabled"],
            "owned_data_empty":not any(Path("/srv/asre-demo/data").iterdir())}
else:
    args=["/usr/bin/python3","-B",str(helper)]
    if binding["action"]=="Trigger":
        args.append("--operator-fault")
    completed=subprocess.run(args,input=json.dumps(binding["request"]),capture_output=True,text=True,timeout=75)
    if completed.returncode:
        raise RuntimeError("Guest helper rejected the operation: "+completed.stderr[-1600:])
    result=json.loads(completed.stdout)
print(json.dumps({"observed_at":time.time(),"action":binding["action"],"result":result},sort_keys=True))
'@
$program = $program.Replace('ASRE_BINDING', $encoded)
$shell = "#!/bin/sh`nset -eu`n/usr/bin/python3 -I -B - <<'ASRE_INFRASTRUCTURE'`n$program`nASRE_INFRASTRUCTURE`n"
Save-New '.sh' $shell
Save-New '-intent.json' (@{
    action = $Action; scenario_id = $ScenarioId; target = "$subscription/$group/$vm"
    request = $request; started_at = [DateTimeOffset]::UtcNow.ToString('o')
    monitoring_changes = 0; investigation_started_by_script = $false; case_written_by_script = $false
} | ConvertTo-Json -Depth 6)
$raw = (& az vm run-command invoke --command-id RunShellScript --subscription $subscription `
    --resource-group $group --name $vm --scripts ('@' + $prefix + '.sh') --output json --only-show-errors) -join "`n"
$code = $LASTEXITCODE
Save-New '-platform.json' $raw
if ($code -ne 0) { throw "Native operation outcome is unknown. Read Status; do not blindly repeat Trigger. Evidence: $prefix" }
$platform = $raw | ConvertFrom-Json -AsHashtable -DateKind String
if ($platform.value.Count -ne 1 -or $platform.value[0].code -cne 'ProvisioningState/succeeded') {
    throw "Native command did not report success. Read Status and retain evidence: $prefix"
}
$framing = "Enable succeeded: `n[stdout]`n"
$message = $platform.value[0].message
if (-not $message.StartsWith($framing, [StringComparison]::Ordinal)) { throw "Invalid native framing: $prefix" }
$parts = $message.Substring($framing.Length).Split("`n[stderr]`n", [StringSplitOptions]::None)
if ($parts.Count -ne 2 -or $parts[1] -or -not $parts[0].Trim()) {
    throw "Guest operation failed or returned no complete result. Read Status; do not repeat Trigger. Evidence: $prefix"
}
$result = $parts[0] | ConvertFrom-Json -AsHashtable -DateKind String
if ($result.action -cne $Action) { throw "Guest action mismatch: $prefix" }
if ($Action -ceq 'Trigger' -and ($result.result.status -cne 'active' -or $result.result.scenario_id -cne $ScenarioId)) {
    throw "No matching active fault was confirmed. Read Status: $prefix"
}
if ($Action -ceq 'Reset' -and ($result.result.status -cne 'restored' -or $result.result.scenario_id -cne $ScenarioId)) {
    throw "No matching restoration was confirmed. Read Status: $prefix"
}
Save-New '-result.json' ($result | ConvertTo-Json -Depth 10)
$result | ConvertTo-Json -Depth 10
