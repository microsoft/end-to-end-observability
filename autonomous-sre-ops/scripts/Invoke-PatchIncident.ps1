#Requires -Version 7.5
[CmdletBinding(SupportsShouldProcess)]
param(
    [ValidateSet('Trigger', 'Status', 'Reset')][string]$Action = 'Status',
    [ValidateRange(120, 900)][int]$LifetimeSeconds = 900,
    [string]$ScenarioId,
    [string]$EvidenceDirectory
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$subscription = '00000001-000a-4000-a000-000000000001'
$group = 'rg-asre-patch-cus'
$vm = 'vm-asre-patch-cus'
if ($ScenarioId -and ([guid]::Parse($ScenarioId).ToString() -cne $ScenarioId)) {
    throw 'ScenarioId must be a canonical lowercase UUID.'
}
if ($Action -ceq 'Reset' -and -not $ScenarioId) { throw 'Reset requires the original ScenarioId.' }
if ($Action -ceq 'Trigger' -and -not $ScenarioId) { $ScenarioId = [guid]::NewGuid().ToString() }
if (-not $PSCmdlet.ShouldProcess("$subscription/$group/$vm", "$Action bounded patch incident $ScenarioId")) { return }
if (-not $EvidenceDirectory) { $EvidenceDirectory = Join-Path ([IO.Path]::GetTempPath()) 'asre-infrastructure-evidence' }
if (-not [IO.Path]::IsPathFullyQualified($EvidenceDirectory)) { throw 'EvidenceDirectory must be absolute.' }
$null = [IO.Directory]::CreateDirectory($EvidenceDirectory)
$prefix = Join-Path $EvidenceDirectory ('patch-' + $Action.ToLowerInvariant() + '-' + [guid]::NewGuid())
function Save-New([string]$Suffix, [string]$Text) {
    $file = [IO.File]::Open(($prefix + $Suffix), [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
    try {
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes($Text)
        $file.Write($bytes, 0, $bytes.Length)
        $file.Flush($true)
    } finally { $file.Dispose() }
}
$package = $null
if ($Action -ceq 'Trigger') {
    $raw = (& az vm assess-patches --subscription $subscription --resource-group $group --name $vm `
        --output json --only-show-errors) -join "`n"
    $code = $LASTEXITCODE
    Save-New '-assessment.json' $raw
    if ($code) { throw "No successful native assessment; no fault started. Evidence: $prefix" }
    $assessment = $raw | ConvertFrom-Json -AsHashtable -DateKind String
    if ($assessment.status -cne 'Succeeded' -or -not $assessment.assessmentActivityId) {
        throw "Native assessment is not successful; no fault started. Evidence: $prefix"
    }
    foreach ($name in @('distro-info-data', 'tzdata', 'ca-certificates')) {
        $available = @($assessment.availablePatches | Where-Object {
            $_.name -ceq $name -and $_.assessmentState -ceq 'Available' -and $_.rebootBehavior -ceq 'NeverReboots'
        })
        if ($available.Count -eq 1 -and $available[0].version -cmatch '^[A-Za-z0-9.+:~\-]+$') {
            $package = @{ name = $name; version = $available[0].version }
            break
        }
    }
    if (-not $package) { throw 'No assessed, supported non-kernel upgrade is available; no fault started.' }
}
$request = switch -CaseSensitive ($Action) {
    Trigger { @{ operation = 'block_patch_install'; scenario_id = $ScenarioId; ttl_seconds = $LifetimeSeconds; byte_limit = 0; interval_seconds = 2 } }
    Reset { @{ operation = 'restore_patch_install'; scenario_id = $ScenarioId } }
    Status { @{ operation = 'inspect' } }
}
$binding = @{
    action = $Action; request = $request; package = $package
    helper_sha256 = (Get-FileHash (Join-Path $PSScriptRoot '..\workloads\linux-demo-service\guest_control.py') -Algorithm SHA256).Hash.ToLowerInvariant()
    patch_sha256 = (Get-FileHash (Join-Path $PSScriptRoot '..\workloads\linux-demo-service\patch_failure.py') -Algorithm SHA256).Hash.ToLowerInvariant()
}
$encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($binding | ConvertTo-Json -Depth 6 -Compress)))
$program = @'
import base64,hashlib,json,subprocess,time
from pathlib import Path
binding=json.loads(base64.b64decode("ASRE_BINDING"))
directory=Path("/usr/local/lib/asre-demo")
for name,key in (("guest_control.py","helper_sha256"),("patch_failure.py","patch_sha256")):
    if hashlib.sha256((directory/name).read_bytes()).hexdigest()!=binding[key]:
        raise RuntimeError("The deployed patch helper does not match this operator script.")
if binding["action"]=="Trigger":
    import apt
    package=apt.Cache()[binding["package"]["name"]]
    candidate=package.candidate
    if (not package.is_upgradable or not candidate or candidate.version!=binding["package"]["version"]
            or not candidate.origins or any(not origin.trusted or origin.origin!="Ubuntu" for origin in candidate.origins)):
        raise RuntimeError("The assessed upgrade is not the exact trusted pending Ubuntu candidate.")
if binding["action"]=="Status":
    state_path=Path("/var/lib/asre-demo/state.json")
    policy=json.loads(Path("/etc/asre-demo/policy.json").read_bytes())
    result={"status":"observed","state":json.loads(state_path.read_bytes()) if state_path.exists() else None,
            "faults_enabled":policy["faults_enabled"],
            "package_failure_files":[path.name for path in Path("/etc/apt/apt.conf.d").glob("99-asre-demo-install-failure-*")]}
else:
    args=["/usr/bin/python3","-B",str(directory/"guest_control.py")]
    if binding["action"]=="Trigger":
        args.append("--operator-fault")
    completed=subprocess.run(args,input=json.dumps(binding["request"]),capture_output=True,text=True,timeout=75)
    if completed.returncode:
        raise RuntimeError("Guest operation rejected: "+completed.stderr[-1600:])
    result=json.loads(completed.stdout)
print(json.dumps({"action":binding["action"],"observed_at":time.time(),"result":result},sort_keys=True))
'@
$program = $program.Replace('ASRE_BINDING', $encoded)
Save-New '.sh' ("#!/bin/sh`nset -eu`n/usr/bin/python3 -I -B - <<'ASRE_PATCH_INFRASTRUCTURE'`n$program`nASRE_PATCH_INFRASTRUCTURE`n")
Save-New '-intent.json' (@{ action = $Action; scenario_id = $ScenarioId; request = $request; package = $package
    started_at = [DateTimeOffset]::UtcNow.ToString('o'); investigation_started_by_script = $false; case_written_by_script = $false } | ConvertTo-Json -Depth 6)
$raw = (& az vm run-command invoke --subscription $subscription --resource-group $group --name $vm `
    --command-id RunShellScript --scripts ('@' + $prefix + '.sh') --output json --only-show-errors) -join "`n"
$code = $LASTEXITCODE
Save-New '-guest-platform.json' $raw
if ($code) { throw "Guest outcome unknown. Read Status; do not repeat Trigger. Evidence: $prefix" }
$platform = $raw | ConvertFrom-Json -AsHashtable -DateKind String
$framing = "Enable succeeded: `n[stdout]`n"
if ($platform.value.Count -ne 1 -or $platform.value[0].code -cne 'ProvisioningState/succeeded' -or
    -not $platform.value[0].message.StartsWith($framing, [StringComparison]::Ordinal)) {
    throw "No complete guest acknowledgment. Read Status. Evidence: $prefix"
}
$parts = $platform.value[0].message.Substring($framing.Length).Split("`n[stderr]`n", [StringSplitOptions]::None)
if ($parts.Count -ne 2 -or $parts[1] -or -not $parts[0].Trim()) { throw "Guest operation failed. Evidence: $prefix" }
$result = $parts[0] | ConvertFrom-Json -AsHashtable -DateKind String
if ($result.action -cne $Action -or ($Action -cne 'Status' -and $result.result.scenario_id -cne $ScenarioId) -or
    ($Action -ceq 'Trigger' -and $result.result.status -cne 'active') -or
    ($Action -ceq 'Reset' -and $result.result.status -cne 'restored')) { throw "Guest result mismatch. Evidence: $prefix" }
Save-New '-guest-result.json' ($result | ConvertTo-Json -Depth 10)
$result | ConvertTo-Json -Depth 10
if ($Action -ceq 'Trigger') {
    $mask = $package.name + '=' + $package.version
    $raw = (& az vm install-patches --subscription $subscription --resource-group $group --name $vm `
        --maximum-duration PT1H --reboot-setting Never --package-name-masks-to-include $mask `
        --output json --only-show-errors) -join "`n"
    $code = $LASTEXITCODE
    Save-New '-installation.json' $raw
    if ($code) { throw "Installation outcome requires reconciliation; do not repeat Trigger. Evidence: $prefix" }
    $installation = $raw | ConvertFrom-Json -AsHashtable -DateKind String
    if (-not $installation.installationActivityId) { throw "No native installation identity returned. Evidence: $prefix" }
    if ($installation.status -cnotin @('Failed', 'CompletedWithWarnings') -or
        -not $installation.ContainsKey('failedPatchCount') -or $installation.failedPatchCount -lt 1) {
        throw "Native installation did not prove a failed package. Preserve its operation ID and inspect cleanup; do not repeat Trigger. Evidence: $prefix"
    }
    @{ status = $installation.status; installation = $installation; scenario_id = $ScenarioId
        evidence_prefix = $prefix; use_case_complete = $false } | ConvertTo-Json -Depth 15
}
