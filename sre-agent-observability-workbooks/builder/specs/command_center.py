"""Spec: 1. SRE Agent Command Center."""

from helpers import SUB_NAME_JOIN, arg_query, la_query, placeholder, section

ORDER = 1
SLUG = "sre-agent-command-center"
NAME = "SRE Agent Command Center"
PAGE = "Command Center"
PURPOSE = (
    "Central landing page for **agent-detected tenant health**, active incidents, "
    "recommendations, confidence, evidence, and automation state. Start your day "
    "here: triage the Top 10, confirm pending approvals, and follow drilldowns into "
    "the specialized workbooks."
)


def sections():
    items = []

    items += section(
        "Tenant health summary",
        "Resource Health rollup across the selected subscriptions. **Unknown is not "
        "healthy** - treat Unknown/empty as risk and investigate telemetry coverage.",
        arg_query(
            "Resource health state (tenant)",
            "healthresources\n"
            "| where type =~ 'microsoft.resourcehealth/availabilitystatuses'\n"
            "| extend state = tostring(properties.availabilityState)\n"
            "| summarize Count = count() by state\n"
            "| order by Count desc",
            visualization="piechart",
        ),
    )

    items += section(
        "Agent health summary",
        "Is the SRE Agent itself healthy and producing recommendations? Stale output "
        "(no rows in the last few hours) means the agent or its pipeline is degraded.",
        la_query(
            "SRE Agent recommendation output (last 24h)",
            "SREAgentRecommendations_CL\n"
            "| where TimeGenerated {TimeRange}\n"
            "| summarize Recommendations = count(), LastSeen = max(TimeGenerated) by CreatedBy\n"
            "| order by Recommendations desc",
        ),
        placeholder(
            "Agent heartbeat / runtime telemetry",
            "Bind the SRE Agent's own AppRequests/AppTraces or container heartbeat to "
            "show agent uptime, error rate, and queue depth.",
        ),
    )

    items += section(
        "Active incidents",
        "Fired, non-closed Azure Monitor alerts grouped by severity. Click through to "
        "**Incident Triage & RCA** for correlated evidence and root-cause candidates.",
        arg_query(
            "Active alerts by severity",
            "alertsmanagementresources\n"
            "| where type =~ 'microsoft.alertsmanagement/alerts'\n"
            "| extend sev = tostring(properties.essentials.severity),\n"
            "         cond = tostring(properties.essentials.monitorCondition),\n"
            "         state = tostring(properties.essentials.alertState)\n"
            "| where cond == 'Fired' and state != 'Closed'\n"
            "| summarize Alerts = count() by sev\n"
            "| order by sev asc",
            visualization="barchart",
        ),
    )

    items += section(
        "Health state by management group / subscription / workload",
        "Where is the risk concentrated? Resource and unhealthy counts per subscription "
        "highlight which scope to drill into.",
        arg_query(
            "Resources by subscription",
            "resources\n"
            + SUB_NAME_JOIN
            + "| summarize Resources = count() by Subscription\n| order by Resources desc",
        ),
        arg_query(
            "Unhealthy / degraded resources by subscription",
            "healthresources\n"
            "| where type =~ 'microsoft.resourcehealth/availabilitystatuses'\n"
            "| extend state = tostring(properties.availabilityState)\n"
            "| where state in ('Unavailable','Degraded','Unknown')\n"
            + SUB_NAME_JOIN
            + "| summarize Count = count() by Subscription, state\n"
            "| order by Count desc",
        ),
    )

    items += section(
        "Critical alerts",
        "Sev0/Sev1 alerts currently firing. These should map to a Top 10 recommendation "
        "or an open incident; if not, alert hygiene needs attention.",
        arg_query(
            "Critical (Sev0/Sev1) fired alerts",
            "alertsmanagementresources\n"
            "| where type =~ 'microsoft.alertsmanagement/alerts'\n"
            "| extend sev = tostring(properties.essentials.severity),\n"
            "         cond = tostring(properties.essentials.monitorCondition),\n"
            "         name = tostring(properties.essentials.alertRule),\n"
            "         target = tostring(properties.essentials.targetResource),\n"
            "         fired = todatetime(properties.essentials.startDateTime)\n"
            "| where cond == 'Fired' and sev in ('Sev0','Sev1')\n"
            + SUB_NAME_JOIN
            + "| project fired, sev, name, target, Subscription\n"
            "| order by fired desc\n| take 50",
        ),
    )

    items += section(
        "Service Health issues",
        "Active Azure platform Service Health events (outages, advisories, planned "
        "maintenance) affecting the tenant. Platform issues should suppress auto-remediation.",
        arg_query(
            "Active Service Health events",
            "servicehealthresources\n"
            "| where type =~ 'microsoft.resourcehealth/events'\n"
            "| extend eventType = tostring(properties.EventType),\n"
            "         status = tostring(properties.Status),\n"
            "         eventTitle = tostring(properties.Title)\n"
            "| where status =~ 'Active'\n"
            + SUB_NAME_JOIN
            + "| project eventType, eventTitle, Subscription\n| take 50",
        ),
    )

    items += section(
        "Resource Health issues",
        "Individual resources reporting Unavailable/Degraded right now - the concrete "
        "targets behind the rollup above.",
        arg_query(
            "Unavailable / degraded resources",
            "healthresources\n"
            "| where type =~ 'microsoft.resourcehealth/availabilitystatuses'\n"
            "| extend state = tostring(properties.availabilityState),\n"
            "         summary = tostring(properties.summary)\n"
            "| where state in ('Unavailable','Degraded')\n"
            "| project resourceId = tostring(properties.targetResourceId), state, summary\n"
            "| take 100",
        ),
    )

    items += section(
        "Agent confidence summary",
        "Distribution of agent confidence on its active recommendations. Low average "
        "confidence in a category means more human review is warranted.",
        la_query(
            "Average confidence by category",
            "SREAgentRecommendations_CL\n"
            "| where TimeGenerated > ago(30d)\n"
            "| where Status in ('New','Pending approval','Approved','In progress','Failed')\n"
            "| summarize AvgConfidence = round(avg(ConfidenceScore), 2), Count = count() by Category\n"
            "| order by Count desc",
        ),
    )

    items += section(
        "Pending approvals",
        "Recommendations waiting on a human decision. Approving here authorizes the agent "
        "to act per its `AgentActionType`.",
        la_query(
            "Recommendations pending approval",
            "SREAgentRecommendations_CL\n"
            "| where TimeGenerated > ago(30d)\n"
            "| where Status == 'Pending approval'\n"
            "| summarize arg_max(LastUpdated, *) by RecommendationId\n"
            "| project LastUpdated, Severity, RecommendationText, AffectedScope, OwnerTeam, AgentActionType, RemediationRisk, RollbackAvailable\n"
            "| order by LastUpdated desc",
        ),
    )

    items += section(
        "Automation actions - running / completed / failed",
        "Execution state of agent-initiated automation. Failures and blocked actions feed "
        "the **Automation & Remediation** workbook.",
        la_query(
            "Automation action outcomes",
            "SREAgentActions_CL\n"
            "| where TimeGenerated {TimeRange}\n"
            "| summarize Count = count() by Outcome\n"
            "| order by Count desc",
        ),
        placeholder(
            "SREAgentActions_CL",
            "Custom table for agent automation runs (fields: ActionId, ActionType, "
            "TargetResourceId, Outcome [Running/Succeeded/Failed/Blocked], Approver, "
            "RollbackAvailable, StartedOn, CompletedOn). Documented in docs/data-sources.md.",
        ),
    )

    items += section(
        "Recent changes",
        "Resource configuration changes in the window - a leading indicator for new "
        "incidents. Correlate spikes here with the incident timeline.",
        arg_query(
            "Recent resource changes",
            "resourcechanges\n"
            "| extend changeType = tostring(properties.changeType),\n"
            "         changeTime = todatetime(properties.changeAttributes.timestamp),\n"
            "         target = tostring(properties.targetResourceId)\n"
            "| project changeTime, changeType, target\n"
            "| order by changeTime desc\n| take 50",
        ),
    )

    items += section(
        "Repeated incidents",
        "Recurring problems (same ProblemId/signature) indicate an unaddressed root cause "
        "and are strong automation candidates.",
        placeholder(
            "Repeated incident signatures",
            "Bind your incident/ITSM table (or AppExceptions ProblemId) to count recurrences "
            "per signature over the window and flag the top offenders.",
        ),
    )

    items += section(
        "Unknown health due to missing telemetry",
        "Resources with **no diagnostic settings or stale heartbeat** report Unknown health. "
        "Unknown is a risk, not a pass - these drive coverage recommendations in "
        "**Observability Quality & Cost**.",
        arg_query(
            "Resources reporting Unknown health",
            "healthresources\n"
            "| where type =~ 'microsoft.resourcehealth/availabilitystatuses'\n"
            "| where tostring(properties.availabilityState) == 'Unknown'\n"
            + SUB_NAME_JOIN
            + "| summarize Unknown = count() by Subscription\n"
            "| order by Unknown desc",
        ),
    )

    return items
