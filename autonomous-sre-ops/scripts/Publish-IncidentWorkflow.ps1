<#
.SYNOPSIS
Fixed protected publication and pinned-coordinator native control.
.DESCRIPTION
Discovery Cutover and Enable require the SAME original Preflight.snapshot,
complete PublicationArtifactDirectory, finite UTC ActivationWindow
{not_before,expires_at} of at most600seconds and source-bound ProbeProfile.
ProbeProfile exact fields: version='1.0',phase='SchemaDiscovery',probe_id,
tenant_id,coordinator_object_id,coordinator_client_id,agent_resource_id,
publication_manifest_sha256,agent_definition_sha256,snapshot_sha256,
activation_window. Hash raw configuration-manifest.json bytes; hash the
single Get-MonitorReviewConfiguration resource and original snapshot using
Get-MonitorObjectSha256 after pure import of ReviewAgent.Common.ps1.
Use ConvertFrom-Json -AsHashtable -DateKind String when restoring JSON.
No live diagnostic, credentials, case reader, positive DTO, role grant or
caller ready/force/skip Boolean is part of Discovery control. Pause needs
none of these activation inputs and remains usable after expiry.
BootstrapPlatform is a separate SchemaDiscovery-only, one-shot transaction.
BootstrapProfile binds the sealed original/archive/qualification, exact protected
artifact and an original finite UTC window. Its fixed resource journal cannot
be resumed or bypassed by changing attempt IDs. It never restores a pilot.
Connected Discovery uses ProbeProfile version 2.0-connected plus the bootstrap
and publication receipts and the post-publication paused Snapshot. Cutover
verifies that connected target without another ARM PATCH; Enable retains the
independent scanner/historical-alert/hook/window gates. Ordinary SetPlatform
and Rollback retain their actual ARM ETag requirements.
#>
[CmdletBinding()]
param(
    [ValidateSet('Preflight', 'PublishDisabled', 'BootstrapPlatform', 'ReconcileBootstrap', 'Cutover', 'Enable', 'Pause', 'Rollback', 'NativeCapturePreflight', 'NativeCaptureEnable', IgnoreCase = $false)][string]$Action = 'Preflight',
    [hashtable]$Snapshot,
    [string]$PublicationArtifactDirectory,
    [hashtable]$ActivationWindow,
    [hashtable]$ProbeProfile,
    [hashtable]$BootstrapProfile,
    [hashtable]$BootstrapReceipt,
    [hashtable]$PublicationReceipt,
    [hashtable]$PublicationContext,
    [ValidateRange(1,2147483647)][int]$PublicationRunId
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
# The unchanged protected outer release script owns this existing parameter.
# Its complete artifact is still independently validated by the fixed reader.
if ($Action -ceq 'PublishDisabled' -and -not $PublicationArtifactDirectory) {
    $outerArtifact = Get-Variable -Name ArtifactDirectory -ErrorAction Ignore
    if ($null -ne $outerArtifact -and $outerArtifact.Value -is [string]) { $PublicationArtifactDirectory = $outerArtifact.Value }
}
. (Join-Path $PSScriptRoot 'ReviewAgent.Common.ps1')
$root=Split-Path $PSScriptRoot -Parent
$manifest=Get-MonitorManifest -Root $root
if(($Action -ceq 'PublishDisabled' -and $manifest.ContainsKey('native_capture')) -or
    $Action -cin @('NativeCapturePreflight','NativeCaptureEnable')){
    # Configuration validation is source-owned and runs before authentication.
    if($Action -cne 'PublishDisabled' -and $PublicationRunId -lt 1){throw 'A positive PublicationRunId is required.'}
    if($Action -cne 'PublishDisabled' -and [string]::IsNullOrWhiteSpace($PublicationArtifactDirectory)){throw 'Native capture control needs the complete protected publication artifact.'}
    $nativeAction=$(if($Action -ceq 'PublishDisabled'){'PublishDisabled'}elseif($Action -ceq 'NativeCaptureEnable'){'Enable'}else{'Preflight'})
    $result=Invoke-MonitorNativeCapture -Action $nativeAction -Root $root -CaptureWindow $ActivationWindow `
        -PublicationArtifactDirectory $PublicationArtifactDirectory -PublicationRunId $PublicationRunId
    if($Action -ceq 'PublishDisabled'){
        if($result.status -cne 'published-disabled' -or $result.runtime_accepted -isnot [bool] -or $result.runtime_accepted){throw 'Capture publication readback failed.'}
        Write-Information ('ASRE_NATIVE_CAPTURE_PUBLICATION_V1='+($result.capture_publication_receipt|ConvertTo-Json -Depth 40 -Compress)) -InformationAction Continue
        Write-Information ('ASRE_NATIVE_CAPTURE_CONTEXT_V1='+($result.capture_publication_context|ConvertTo-Json -Depth 40 -Compress)) -InformationAction Continue
    }
    $result
    return
}
# Fixed native auth/REST adapter only. No executable transport or policy
# patterns are accepted from a release artifact or command-line caller.
$result = Invoke-IncidentWorkflow -Action $Action `
    -Snapshot $Snapshot -PublicationArtifactDirectory $PublicationArtifactDirectory `
    -ActivationWindow $ActivationWindow -ProbeProfile $ProbeProfile -BootstrapProfile $BootstrapProfile `
    -BootstrapReceipt $BootstrapReceipt -PublicationReceipt $PublicationReceipt -PublicationContext $PublicationContext -Root (Split-Path $PSScriptRoot -Parent)
if ($Action -ceq 'PublishDisabled') {
    $receipt = $result.publication_receipt
    Assert-MonitorBootstrapBinding @('agent_definition_sha256','agent_resource_id','paused_snapshot_sha256',
        'phase','pre_snapshot_sha256','runtime_accepted','version') @($receipt.Keys | Sort-Object)
    Assert-MonitorBootstrapBinding 'asre-monitor-publication-receipt/v1' $receipt.version
    Assert-MonitorBootstrapBinding '/subscriptions/00000001-000a-4000-a000-000000000001/resourceGroups/rg-asre-control-cus/providers/Microsoft.App/agents/asre-demo-cus' $receipt.agent_resource_id
    if ((-not [string]::Equals($receipt.phase, 'SchemaDiscovery', [StringComparison]::Ordinal) -and
        -not [string]::Equals($receipt.phase, 'Production', [StringComparison]::Ordinal)) -or
        $receipt.runtime_accepted -isnot [bool] -or $receipt.runtime_accepted) {
        throw 'Publication receipt metadata is unsupported.'
    }
    foreach ($key in @('agent_definition_sha256','paused_snapshot_sha256','pre_snapshot_sha256')) {
        if ($receipt[$key] -isnot [string] -or $receipt[$key] -cnotmatch '^[0-9a-f]{64}\z') { throw 'Publication receipt hash is unsupported.' }
    }
    Write-Information ('ASRE_MONITOR_PUBLICATION_RECEIPT_V1=' + (ConvertTo-Json -InputObject $receipt -Depth 4 -Compress)) -InformationAction Continue
    if ($result.ContainsKey('publication_context')) {
        Write-Information ('ASRE_MONITOR_PUBLICATION_CONTEXT_V1=' + (ConvertTo-Json -InputObject $result.publication_context -Depth 40 -Compress)) -InformationAction Continue
    }
}
$result
