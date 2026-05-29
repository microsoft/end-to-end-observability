# Maintenance Windows Runbook

Procedures for suppressing AMBA alert notifications during planned maintenance.

## Overview

AMBA deploys a Suppression Alert Processing Rule (APR) that can be activated during maintenance windows to prevent alert noise while planned work is executed.

## Activate Suppression (Start Maintenance Window)

### Option 1: Azure Portal
1. Navigate to Monitor → Alert Processing Rules
2. Find the AMBA suppression rule (name includes `apr-AMBA-` and subscription name)
3. Set Status to **Enabled**
4. Optionally set a schedule with start/end time

### Option 2: Azure CLI
```powershell
# Enable suppression APR
az monitor alert-processing-rule update \
  --name "apr-AMBA-<subscription-name>-Suppression" \
  --resource-group "rg-amba-monitoring-001" \
  --enabled true

# Or with a scheduled window (ISO 8601 format):
az monitor alert-processing-rule update \
  --name "apr-AMBA-<subscription-name>-Suppression" \
  --resource-group "rg-amba-monitoring-001" \
  --enabled true \
  --schedule-start-date-time "2026-06-01T02:00:00Z" \
  --schedule-end-date-time "2026-06-01T06:00:00Z"
```

### Option 3: Using Disable Tag (per resource)
For targeted suppression on specific resources:
```powershell
# Add disable tag to specific resource
az tag update --resource-id "/subscriptions/<sub>/resourceGroups/<rg>/providers/<provider>/<resource>" \
  --operation merge \
  --tags MonitorDisable=true
```

## Deactivate Suppression (End Maintenance Window)

### Azure CLI
```powershell
# Disable suppression APR
az monitor alert-processing-rule update \
  --name "apr-AMBA-<subscription-name>-Suppression" \
  --resource-group "rg-amba-monitoring-001" \
  --enabled false
```

### Remove per-resource disable tag
```powershell
az tag update --resource-id "/subscriptions/<sub>/resourceGroups/<rg>/providers/<provider>/<resource>" \
  --operation delete \
  --tags MonitorDisable
```

## Scheduled Maintenance Windows

For recurring maintenance windows, configure the APR schedule:

```powershell
# Every Sunday 02:00-06:00 UTC
az monitor alert-processing-rule update \
  --name "apr-AMBA-<subscription-name>-Suppression" \
  --resource-group "rg-amba-monitoring-001" \
  --enabled true \
  --schedule-recurrence-type Weekly \
  --schedule-recurrence-value Sunday \
  --schedule-start-time "02:00:00" \
  --schedule-end-time "06:00:00" \
  --schedule-time-zone "UTC"
```

## Emergency Suppression

For unplanned incidents where alerts must be silenced:

```powershell
# Immediate 4-hour suppression
$endTime = (Get-Date).AddHours(4).ToString("yyyy-MM-ddTHH:mm:ssZ")
az monitor alert-processing-rule update \
  --name "apr-AMBA-<subscription-name>-Suppression" \
  --resource-group "rg-amba-monitoring-001" \
  --enabled true \
  --schedule-end-date-time $endTime
```

**Important**: Always set an end time for emergency suppression to prevent indefinite silence.

## Verification

After activating or deactivating suppression:

```powershell
# Check APR status
az monitor alert-processing-rule show \
  --name "apr-AMBA-<subscription-name>-Suppression" \
  --resource-group "rg-amba-monitoring-001" \
  --query "{name:name, enabled:properties.enabled, schedule:properties.schedule}"
```

## Governance

- All maintenance windows must be logged in change management
- Maximum suppression duration: 8 hours (unless approved exception)
- Emergency suppression requires post-incident review
- Per-resource disable tags must be removed within 24 hours
- Quarterly audit: check for stale disable tags on resources
