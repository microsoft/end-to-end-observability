$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$out = Join-Path $PSScriptRoot "output"

Write-Host "=== 1. JSONL parse validation ===" -ForegroundColor Cyan
$allValid = $true
Get-ChildItem (Join-Path $out "*.jsonl") | ForEach-Object {
    $lines = Get-Content $_.FullName
    $bad = 0
    foreach ($l in $lines) { if ($l.Trim()) { try { $null = $l | ConvertFrom-Json } catch { $bad++ } } }
    if ($bad -gt 0) { $allValid = $false }
    "{0,-38} lines={1,-6} bad={2}" -f $_.Name, $lines.Count, $bad
}
"summary.json valid : $((Test-Path (Join-Path $out 'summary.json')) -and ((Get-Content (Join-Path $out 'summary.json') -Raw | ConvertFrom-Json) -ne $null))"
"ALL_JSONL_VALID    : $allValid"

Write-Host "`n=== 2. Workbook flow simulations ===" -ForegroundColor Cyan
$now = Get-Date
$alerts = Get-Content (Join-Path $out "amba-alerts.jsonl") | ForEach-Object { $_ | ConvertFrom-Json }
$fired24 = @($alerts | Where-Object { $_.properties.essentials.monitorCondition -eq 'Fired' -and [datetime]$_.properties.essentials.startDateTime -ge $now.AddHours(-24) })
$stale = @($alerts | Where-Object { $_.properties.essentials.monitorCondition -eq 'Fired' -and $_.properties.essentials.alertState -eq 'New' -and [datetime]$_.properties.essentials.startDateTime -lt $now.AddHours(-24) })
"Alert-health: Fired(24h)=$($fired24.Count)  Stale>24h-New=$($stale.Count)"

$apr = Get-Content (Join-Path $out "amba-action-rules.jsonl") | ForEach-Object { $_ | ConvertFrom-Json }
"Alert processing rules: total=$($apr.Count) enabled=$(@($apr | Where-Object { $_.properties.enabled }).Count)"

$ag = Get-Content (Join-Path $out "amba-action-groups.jsonl") | ForEach-Object { $_ | ConvertFrom-Json }
"Action groups (_deployed_by_amba): $(@($ag | Where-Object { $_.tags._deployed_by_amba -eq 'True' }).Count)"

$rem = Get-Content (Join-Path $out "remediation-tasks.jsonl") | ForEach-Object { $_ | ConvertFrom-Json }
$mbRem = @($rem | Where-Object { $_.properties.policyAssignmentId -like '*mon-baselines*' })
"Remediations: total=$($rem.Count) mon-baselines=$($mbRem.Count)"

$mb = Get-Content (Join-Path $out "monitoring-baselines-policy-states.jsonl") | ForEach-Object { $_ | ConvertFrom-Json }
Write-Host "MB posture by initiative + category:"
foreach ($init in @("Monitoring-Baselines-sandbox", "Monitoring-Baselines-pre-prod", "Monitoring-Baselines-prod")) {
    foreach ($grp in @("azure-monitor-agent", "diagnostic-settings", "private-endpoints")) {
        $rows = @($mb | Where-Object { $_.properties.policySetDefinitionName -eq $init -and $_.properties.policyDefinitionGroupNames[0] -eq $grp })
        if ($rows.Count -gt 0) {
            $nc = @($rows | Where-Object { $_.properties.complianceState -eq 'NonCompliant' }).Count
            $pct = [math]::Round(100.0 * ($rows.Count - $nc) / $rows.Count, 1)
            "  {0,-30} {1,-20} total={2,-5} nc={3,-4} compliant%={4}" -f $init, $grp, $rows.Count, $nc, $pct
        }
    }
}

$inv = Get-Content (Join-Path $out "amba-deployed-resources.jsonl") | ForEach-Object { $_ | ConvertFrom-Json }
Write-Host "Deployed-resource inventory by type:"
$inv | Group-Object type | Sort-Object Count -Descending | ForEach-Object { "  {0,-45} {1}" -f $_.Name, $_.Count }

Write-Host "`nFLOW_SIM_OK=$allValid" -ForegroundColor Green
