"""Spec: 8. Observability Quality & Cost."""

from helpers import SUB_NAME_JOIN, arg_query, la_query, markdown, placeholder, section

ORDER = 8
SLUG = "observability-quality-cost"
NAME = "Observability Quality & Cost"
PAGE = "Observability Quality & Cost"
PURPOSE = (
    "Monitor **telemetry completeness, workbook quality, alert hygiene, and Log Analytics "
    "cost**. Gaps here become Unknown health everywhere else; this page keeps the "
    "observability platform itself observable."
)


def sections():
    items = []

    items += section(
        "Diagnostic-settings coverage",
        "Resources missing diagnostics or sending to the wrong workspace produce Unknown "
        "health. This is the agent's top telemetry-coverage backlog.",
        placeholder(
            "Resources missing diagnostic settings",
            "ARG cannot read diagnostic settings directly. Use the Azure Policy "
            "'Deploy/Audit diagnostic settings' compliance state (policyresources) or a "
            "scheduled export to list resources without diagnostics, by type/subscription.",
        ),
        la_query(
            "Resources actively sending logs (sanity check)",
            "union *\n"
            "| where TimeGenerated {TimeRange}\n"
            "| where isnotempty(_ResourceId)\n"
            "| summarize Records = count() by _ResourceId\n"
            "| summarize ResourcesEmitting = dcount(_ResourceId)",
        ),
    )

    items += section(
        "Workspace inventory & ingestion",
        "Which workspaces exist and how much they ingest. Spikes are cost and noise; drops "
        "are coverage loss.",
        arg_query(
            "Log Analytics workspaces",
            "resources\n"
            "| where type =~ 'microsoft.operationalinsights/workspaces'\n"
            "| extend sku = tostring(properties.sku.name), retention = toint(properties.retentionInDays)\n"
            + SUB_NAME_JOIN
            + "| project name, location, sku, retention, Subscription\n"
            "| order by name asc",
        ),
        la_query(
            "Ingestion volume by table",
            "Usage\n"
            "| where TimeGenerated {TimeRange}\n"
            "| where IsBillable == true\n"
            "| summarize BillableGB = round(sum(Quantity)/1024, 3) by DataType\n"
            "| order by BillableGB desc\n| take 25",
        ),
    )

    items += section(
        "Billable vs non-billable & trend",
        "Cost driver breakdown and daily ingestion trend. Use with commitment-tier sizing.",
        la_query(
            "Billable vs non-billable",
            "Usage\n"
            "| where TimeGenerated {TimeRange}\n"
            "| summarize GB = round(sum(Quantity)/1024, 2) by IsBillable\n",
        ),
        la_query(
            "Daily ingestion trend",
            "Usage\n"
            "| where TimeGenerated {TimeRange}\n"
            "| where IsBillable == true\n"
            "| summarize BillableGB = round(sum(Quantity)/1024, 3) by bin(TimeGenerated, 1d)\n"
            "| order by TimeGenerated asc",
        ),
        placeholder(
            "Commitment tier & daily cap",
            "Compare average daily billable GB to commitment-tier breakpoints (100/200/"
            "300/400/500 GB) for right-sizing, and surface daily-cap hits from the "
            "workspace _LogOperation events.",
        ),
    )

    items += section(
        "Collection coverage",
        "Data Collection Rule coverage and agent (AMA) heartbeat failures. Missing DCRs and "
        "dead agents are silent coverage holes.",
        la_query(
            "AMA heartbeat failures (stale machines)",
            "Heartbeat\n"
            "| where TimeGenerated > ago(1h)\n"
            "| summarize LastHeartbeat = max(TimeGenerated) by Computer, Category\n"
            "| where LastHeartbeat < ago(15m)\n"
            "| order by LastHeartbeat asc",
        ),
        placeholder(
            "DCR / Container Insights / Prometheus coverage",
            "Bind ARG (microsoft.insights/datacollectionrules) for DCR inventory and "
            "compare to enrolled machines; check ContainerLog/InsightsMetrics for "
            "Container Insights and the managed-Prometheus scrape targets for coverage.",
        ),
    )

    items += section(
        "App Insights coverage",
        "Applications actively reporting vs the expected set. A silent app is Unknown, not "
        "healthy.",
        la_query(
            "Applications reporting telemetry",
            "AppRequests\n"
            "| where TimeGenerated {TimeRange}\n"
            "| summarize Requests = count(), LastSeen = max(TimeGenerated) by AppRoleName\n"
            "| order by Requests desc",
        ),
    )

    items += section(
        "Workbook quality",
        "Slow queries, empty panels, and broken links across the suite. Keeps the surfaces "
        "the agent and humans rely on trustworthy (driven by Workbook Maintenance).",
        placeholder(
            "Slow / empty / broken panels",
            "Bind workbook QA results (WorkbookQA_CL) produced by the weekly review: "
            "per-panel query duration, row counts (0 = empty), and link reachability. "
            "See automation/weekly-review.",
        ),
    )

    items += section(
        "Alert hygiene",
        "Duplicate/noisy alert rules and rules missing routing. Poor hygiene trains people "
        "to ignore alerts.",
        arg_query(
            "Noisiest alert rules",
            "alertsmanagementresources\n"
            "| where type =~ 'microsoft.alertsmanagement/alerts'\n"
            "| extend rule = tostring(properties.essentials.alertRule)\n"
            "| summarize Fires = count() by rule\n"
            "| order by Fires desc\n| take 25",
        ),
        placeholder(
            "Alerts without action groups",
            "Bind alert-rule configuration to list enabled rules with no action group "
            "(unroutable) or duplicated scope/condition (noise candidates).",
        ),
    )

    return items
