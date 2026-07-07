"""Spec: 5. Operations Command Center - real-world NOC/SRE glance-first dashboard.

Leads with at-a-glance KPI scorecards and a color-coded work queue, then trend and
breakdown charts, then platform health and signal hygiene - the floor view the
on-call drives from, not a stack of raw tables.
"""

from helpers import (
    SUB_NAME_JOIN,
    arg_query,
    heatmap_grid,
    icon_grid,
    kpi_tiles,
    la_query,
    placeholder,
    section,
    status_banner,
    thresholds_grid,
)

ORDER = 5
SLUG = "operations-command-center"
NAME = "Operations Command Center"
PAGE = "Operations Command Center"
PURPOSE = (
    "The **NOC/SRE infrastructure operations** floor view: a glance-first status "
    "scorecard, the active-alert work queue, health and activity trends, and signal "
    "hygiene - so the on-call sees *are we on fire?* in seconds, then drills down."
)

# Severity color thresholds for the alert work queue (Sev0 worst -> red).
_SEV_THRESHOLDS = [
    {"operator": "==", "thresholdValue": "Sev0", "representation": "red", "text": "{0}{1}"},
    {"operator": "==", "thresholdValue": "Sev1", "representation": "orange", "text": "{0}{1}"},
    {"operator": "==", "thresholdValue": "Sev2", "representation": "yellow", "text": "{0}{1}"},
    {"operator": "Default", "thresholdValue": None, "representation": "blue", "text": "{0}{1}"},
]


def sections():
    items = []

    # --- 1. Status scorecard (KPI tiles) --------------------------------------
    items += section(
        "Operations status at a glance",
        "Live scorecard for the on-call. The posture banner is the headline; the tiles "
        "and the severity mix break it down. Check Service Health before remediating.",
        arg_query(
            "NOC posture",
            "alertsmanagementresources\n"
            "| where type =~ 'microsoft.alertsmanagement/alerts'\n"
            "| extend sev = tostring(properties.essentials.severity),\n"
            "         cond = tostring(properties.essentials.monitorCondition)\n"
            "| where cond == 'Fired'\n"
            "| summarize Crit = countif(sev in ('Sev0','Sev1')),\n"
            "            Warn = countif(sev in ('Sev2','Sev3')), Total = count()\n"
            "| extend Posture = case(Crit > 0, 'CRITICAL', Warn > 0, 'DEGRADED', "
            "Total > 0, 'ELEVATED', 'HEALTHY')\n"
            "| project Posture, ['Active alerts'] = Total",
            visualization="tiles",
            size=4,
            extra=status_banner("Posture", "Active alerts"),
        ),
        arg_query(
            "Fired alerts by severity",
            "alertsmanagementresources\n"
            "| where type =~ 'microsoft.alertsmanagement/alerts'\n"
            "| extend sev = tostring(properties.essentials.severity),\n"
            "         cond = tostring(properties.essentials.monitorCondition)\n"
            "| where cond == 'Fired'\n"
            "| summarize Alerts = count() by Severity = sev\n"
            "| order by Severity asc",
            visualization="tiles",
            size=3,
            extra=kpi_tiles("Severity", "Alerts"),
        ),
        arg_query(
            "Alert severity mix",
            "alertsmanagementresources\n"
            "| where type =~ 'microsoft.alertsmanagement/alerts'\n"
            "| extend sev = tostring(properties.essentials.severity),\n"
            "         cond = tostring(properties.essentials.monitorCondition)\n"
            "| where cond == 'Fired'\n"
            "| summarize Alerts = count() by Severity = sev\n"
            "| order by Severity asc",
            visualization="piechart",
            size=1,
        ),
        arg_query(
            "Resource & platform health",
            "healthresources\n"
            "| where type =~ 'microsoft.resourcehealth/availabilitystatuses'\n"
            "| extend state = tostring(properties.availabilityState)\n"
            "| summarize Resources = count() by State = state\n"
            "| union (servicehealthresources\n"
            "    | where type =~ 'microsoft.resourcehealth/events'\n"
            "    | where tostring(properties.Status) == 'Active'\n"
            "    | summarize Resources = count() | extend State = 'Service Health events')\n"
            "| order by State asc",
            visualization="tiles",
            size=3,
            extra=kpi_tiles("State", "Resources"),
        ),
    )

    # --- 2. The work queue (color-coded table) --------------------------------
    items += section(
        "Active alert work queue",
        "The prioritized queue the on-call works - now with *what each alert is "
        "actually about*: the incident title, the impacted service and region (for "
        "Service Health), or the resource for metric/log alerts. Severity uses "
        "traffic-light icons; the **Age (h)** heatmap makes stale alerts jump out.",
        arg_query(
            "Active alerts - what, where, how old",
            "alertsmanagementresources\n"
            "| where type =~ 'microsoft.alertsmanagement/alerts'\n"
            "| extend e = properties.essentials\n"
            "| extend cond = tostring(e.monitorCondition)\n"
            "| where cond == 'Fired'\n"
            "| extend sev = tostring(e.severity), state = tostring(e.alertState),\n"
            "         monsvc = tostring(e.monitorService), rname = tostring(e.targetResourceName),\n"
            "         rg = tostring(e.targetResourceGroup), fired = todatetime(e.startDateTime)\n"
            "| extend sh = properties.context.context.activityLog.properties\n"
            "| extend shTitle = tostring(sh['title']), shService = tostring(sh['service']),\n"
            "         shRegion = tostring(sh['region'])\n"
            "| extend ruleParts = split(tostring(e.alertRule), '/')\n"
            "| extend What = iff(isnotempty(shTitle), shTitle, "
            "tostring(ruleParts[array_length(ruleParts) - 1]))\n"
            "| extend ['Service / Resource'] = iff(isnotempty(shService), "
            "strcat(shService, iff(isnotempty(shRegion), strcat(' (', shRegion, ')'), '')), "
            "iff(isnotempty(rname), rname, rg))\n"
            "| extend ['Age (h)'] = round(datetime_diff('minute', now(), fired)/60.0, 1)\n"
            "| join kind=leftouter (resourcecontainers\n"
            "    | where type =~ 'microsoft.resources/subscriptions'\n"
            "    | project subscriptionId, Subscription = name) on subscriptionId\n"
            "| project Severity = sev, What, ['Service / Resource'], Monitor = monsvc, "
            "Subscription, ['Age (h)'], State = state\n"
            "| order by Severity asc, ['Age (h)'] desc\n| take 200",
            grid_settings={
                "formatters": (
                    icon_grid(
                        "Severity",
                        [
                            ("==", "Sev0", "critical"),
                            ("==", "Sev1", "error"),
                            ("==", "Sev2", "warning"),
                            ("==", "Sev3", "info"),
                            ("Default", None, "info"),
                        ],
                    )["formatters"]
                    + heatmap_grid("Age (h)", "red")["formatters"]
                )
            },
        ),
    )

    # --- 3. Trends ------------------------------------------------------------
    items += section(
        "Trends - is it getting better or worse?",
        "Alert inflow and control-plane activity over time. A rising alert trend or a "
        "spike in failed operations is an early warning before it becomes an incident.",
        arg_query(
            "Alerts fired over time (by severity)",
            "alertsmanagementresources\n"
            "| where type =~ 'microsoft.alertsmanagement/alerts'\n"
            "| extend sev = tostring(properties.essentials.severity),\n"
            "         fired = todatetime(properties.essentials.startDateTime)\n"
            "| where isnotempty(fired)\n"
            "| summarize Alerts = count() by bin(fired, 6h), Severity = sev\n"
            "| order by fired asc",
            visualization="timechart",
            size=0,
        ),
        la_query(
            "Control-plane operations over time (failed vs succeeded)",
            "AzureActivity\n"
            "| where TimeGenerated {TimeRange}\n"
            "| summarize Operations = count() by bin(TimeGenerated, 1h), "
            "Outcome = ActivityStatusValue\n"
            "| order by TimeGenerated asc",
            visualization="timechart",
            size=0,
        ),
    )

    # --- 4. Breakdowns --------------------------------------------------------
    items += section(
        "Where it hurts - breakdown by domain",
        "Which resource domain and which subscription is generating the pain, so you "
        "route to the right owner. Driven by the alert signal (Resource Health is shown "
        "too, with an explicit note when the provider is not reporting).",
        arg_query(
            "Fired alerts by domain (resource type)",
            "alertsmanagementresources\n"
            "| where type =~ 'microsoft.alertsmanagement/alerts'\n"
            "| extend cond = tostring(properties.essentials.monitorCondition),\n"
            "         target = tostring(properties.essentials.targetResource)\n"
            "| where cond == 'Fired'\n"
            "| extend afterProv = tostring(split(target, '/providers/')[1])\n"
            "| extend Domain = iff(isempty(afterProv), 'Subscription / Service Health', "
            "strcat(tostring(split(afterProv, '/')[0]), '/', tostring(split(afterProv, '/')[1])))\n"
            "| summarize Alerts = count() by Domain\n"
            "| order by Alerts desc\n| take 15",
            visualization="barchart",
            size=1,
        ),
        arg_query(
            "Fired alerts by subscription",
            "alertsmanagementresources\n"
            "| where type =~ 'microsoft.alertsmanagement/alerts'\n"
            "| extend cond = tostring(properties.essentials.monitorCondition)\n"
            "| where cond == 'Fired'\n"
            + SUB_NAME_JOIN
            + "| summarize Alerts = count() by Subscription\n"
            "| order by Alerts desc\n| take 15",
            visualization="barchart",
            size=1,
        ),
        arg_query(
            "Unhealthy resources by domain (Resource Health)",
            "healthresources\n"
            "| where type =~ 'microsoft.resourcehealth/availabilitystatuses'\n"
            "| extend state = tostring(properties.availabilityState)\n"
            "| where state != 'Available'\n"
            "| extend resType = tostring(split(id, '/providers/')[1])\n"
            "| extend Domain = tostring(split(resType, '/')[0])\n"
            "| summarize Unhealthy = count() by Domain\n"
            "| order by Unhealthy desc\n| take 15",
            visualization="barchart",
            size=1,
            extra={
                "noDataMessage": (
                    "No Resource Health data is flowing (the provider returns 0 records "
                    "tenant-wide). Health is Unknown, not Healthy - enable Resource Health "
                    "/ diagnostic settings. The agent raises this as a coverage finding in "
                    "the Top 10 above."
                )
            },
        ),
    )

    # --- 5. Platform health ---------------------------------------------------
    items += section(
        "Platform health (Azure Service Health)",
        "Active platform-side events. **A platform issue is not yours to fix** - "
        "annotate affected work and wait on Microsoft rather than remediating.",
        arg_query(
            "Active Service Health events",
            "servicehealthresources\n"
            "| where type =~ 'microsoft.resourcehealth/events'\n"
            "| extend eventType = tostring(properties.EventType),\n"
            "         status = tostring(properties.Status),\n"
            "         eventTitle = tostring(properties.Title)\n"
            "| where status == 'Active'\n"
            + SUB_NAME_JOIN
            + "| project Type = eventType, Event = eventTitle, Subscription\n| take 100",
        ),
    )

    # --- 6. Signal hygiene ----------------------------------------------------
    items += section(
        "Signal hygiene",
        "Noisy rules and silent agents erode trust in the signal. The agent recommends "
        "tuning the worst offenders (see the Top 10 above).",
        arg_query(
            "Noisiest alert rules",
            "alertsmanagementresources\n"
            "| where type =~ 'microsoft.alertsmanagement/alerts'\n"
            "| extend rule = tostring(properties.essentials.alertRule)\n"
            "| summarize Fires = count() by Rule = rule\n"
            "| order by Fires desc\n| take 15",
            visualization="barchart",
            size=1,
        ),
        la_query(
            "Agent heartbeat (stale > 15m)",
            "Heartbeat\n"
            "| where TimeGenerated > ago(1h)\n"
            "| summarize LastHeartbeat = max(TimeGenerated) by Computer\n"
            "| extend Status = iff(LastHeartbeat < ago(15m), 'Stale', 'Healthy')\n"
            "| where Status == 'Stale'\n"
            "| order by LastHeartbeat asc",
        ),
        placeholder(
            "Alerts without action groups / runbooks",
            "Bind alert-rule config (ARG microsoft.insights/* rules or a config export) to "
            "list rules missing an action group or a linked runbook. AMBA baseline alert "
            "coverage compares deployed rules to the AMBA recommended set.",
        ),
    )

    return items
