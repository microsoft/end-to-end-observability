"""Reusable glance-first scorecards.

Each page opens with a posture banner + KPI tiles + a distribution chart so the
dashboard answers "what is the state right now?" before the detailed sections.
``scorecard_for(slug)`` returns the section for a page (or [] when the page builds
its own, e.g. the Operations Command Center).
"""

from __future__ import annotations

from typing import Any

from helpers import (
    arg_query,
    kpi_tiles,
    kpi_tiles_thresholds,
    la_query,
    section,
    status_banner,
    with_no_data,
)

# Hero-KPI threshold coloring for "0 is good" security-hygiene counts: the big
# number turns red as the count climbs. Reused across the security hero row.
_HERO_ZERO_GOOD = [
    {"operator": ">=", "thresholdValue": "5", "representation": "red", "text": "{0}"},
    {"operator": ">=", "thresholdValue": "1", "representation": "orange", "text": "{0}"},
    {"operator": "Default", "thresholdValue": None, "representation": "green", "text": "{0}"},
]

# Any nonzero is red (for active-threat KPIs where even one is urgent).
_HERO_ANY_BAD = [
    {"operator": ">=", "thresholdValue": "1", "representation": "red", "text": "{0}"},
    {"operator": "Default", "thresholdValue": None, "representation": "green", "text": "{0}"},
]

# Elapsed-hours KPIs (MTTR, age): higher is worse.
_HERO_HOURS_BAD = [
    {"operator": ">=", "thresholdValue": "24", "representation": "red", "text": "{0} h"},
    {"operator": ">=", "thresholdValue": "8", "representation": "orange", "text": "{0} h"},
    {"operator": "Default", "thresholdValue": None, "representation": "green", "text": "{0} h"},
]

# Coverage-percent KPIs (MFA, Conditional Access): lower is worse. 1-decimal + % unit.
_HERO_PCT_COVERAGE = [
    {"operator": "<", "thresholdValue": "70", "representation": "red", "text": "{0}%"},
    {"operator": "<", "thresholdValue": "90", "representation": "orange", "text": "{0}%"},
    {"operator": "Default", "thresholdValue": None, "representation": "green", "text": "{0}%"},
]

# Active Defender for Cloud alerts (live threat) as an at-a-glance banner.
def _active_alert_banner() -> dict[str, Any]:
    return arg_query(
        "Active alert posture",
        "securityresources\n"
        "| where type =~ 'microsoft.security/alerts'\n"
        "| extend sev = tostring(properties.severity), status = tostring(properties.status)\n"
        "| where status in ('Active','InProgress')\n"
        "| summarize High = countif(sev =~ 'High'), Med = countif(sev =~ 'Medium'), Total = count()\n"
        "| extend Posture = case(High > 0, 'CRITICAL', Med > 0, 'DEGRADED', "
        "Total > 0, 'ELEVATED', 'HEALTHY')\n"
        "| project Posture, ['Active alerts'] = Total",
        visualization="tiles",
        size=4,
        extra=status_banner("Posture", "Active alerts"),
    )

# Shared ARG fragment: resolve a role assignment's role-definition GUID to RoleName.
_ROLE_JOIN = (
    "| extend roleDefinitionId = tolower(tostring(properties.roleDefinitionId))\n"
    "| join kind=leftouter (authorizationresources\n"
    "    | where type =~ 'microsoft.authorization/roledefinitions'\n"
    "    | project roleDefinitionId = tolower(id), RoleName = tostring(properties.roleName)) "
    "on roleDefinitionId\n"
)

_FIRED = (
    "alertsmanagementresources\n"
    "| where type =~ 'microsoft.alertsmanagement/alerts'\n"
    "| extend sev = tostring(properties.essentials.severity),\n"
    "         cond = tostring(properties.essentials.monitorCondition)\n"
    "| where cond == 'Fired'\n"
)


def _alert_posture_banner() -> dict[str, Any]:
    return arg_query(
        "Alert posture",
        _FIRED
        + "| summarize Crit = countif(sev in ('Sev0','Sev1')), "
        "Warn = countif(sev in ('Sev2','Sev3')), Total = count()\n"
        "| extend Posture = case(Crit > 0, 'CRITICAL', Warn > 0, 'DEGRADED', "
        "Total > 0, 'ELEVATED', 'HEALTHY')\n"
        "| project Posture, ['Active alerts'] = Total",
        visualization="tiles",
        size=4,
        extra=status_banner("Posture", "Active alerts"),
    )


def _alert_severity_tiles() -> dict[str, Any]:
    return arg_query(
        "Fired alerts by severity",
        _FIRED + "| summarize Alerts = count() by Severity = sev\n| order by Severity asc",
        visualization="tiles",
        size=3,
        extra=kpi_tiles("Severity", "Alerts"),
    )


def _alert_severity_donut() -> dict[str, Any]:
    return arg_query(
        "Alert severity mix",
        _FIRED + "| summarize Alerts = count() by Severity = sev\n| order by Severity asc",
        visualization="piechart",
        size=1,
    )


def _command_center() -> list[dict[str, Any]]:
    return section(
        "Status at a glance",
        "Tenant posture from live alerts, plus the agent's active recommendation load. "
        "Drill into the Top 10 and the sections below.",
        _alert_posture_banner(),
        _alert_severity_tiles(),
        _alert_severity_donut(),
        la_query(
            "Active agent recommendations by severity",
            "SREAgentRecommendations_CL\n"
            "| where TimeGenerated > ago(30d)\n"
            "| where Status in ('New','Pending approval','Approved','In progress','Failed')\n"
            "| summarize arg_max(LastUpdated, *) by RecommendationId\n"
            "| summarize Recommendations = count() by Severity\n"
            "| order by Severity asc",
            visualization="tiles",
            size=3,
            extra=kpi_tiles("Severity", "Recommendations"),
        ),
    )


def _tenant_health() -> list[dict[str, Any]]:
    return section(
        "Health at a glance",
        "Resource Health distribution (Unknown is not Healthy) and the ownership/tagging "
        "coverage that the health model depends on.",
        arg_query(
            "Resource health mix",
            "healthresources\n"
            "| where type =~ 'microsoft.resourcehealth/availabilitystatuses'\n"
            "| summarize Count = count() by State = tostring(properties.availabilityState)\n"
            "| order by Count desc",
            visualization="piechart",
            size=1,
            extra={
                "noDataMessage": (
                    "No Resource Health data is flowing (provider returns 0 records). "
                    "Health is Unknown, not Healthy - enable Resource Health / diagnostics."
                )
            },
        ),
        arg_query(
            "Health-model coverage gaps",
            "resources\n"
            "| extend owner = tostring(tags['Owner']), team = tostring(tags['OwnerTeam']),\n"
            "         app = tostring(tags['Application']), wl = tostring(tags['Workload'])\n"
            "| summarize ['No owner'] = countif(isempty(owner) and isempty(team)),\n"
            "            ['No app tag'] = countif(isempty(app) and isempty(wl))\n"
            "| project Gap = 'No owner', Resources = ['No owner']\n"
            "| union (resources\n"
            "    | extend app = tostring(tags['Application']), wl = tostring(tags['Workload'])\n"
            "    | summarize Resources = countif(isempty(app) and isempty(wl))\n"
            "    | extend Gap = 'No app tag' | project Gap, Resources)",
            visualization="tiles",
            size=3,
            extra=kpi_tiles("Gap", "Resources"),
        ),
    )


def _incident_rca() -> list[dict[str, Any]]:
    return section(
        "Incident posture at a glance",
        "Live incident pressure: fired alerts and the recent change load that is the "
        "leading root-cause candidate.",
        _alert_posture_banner(),
        _alert_severity_tiles(),
        arg_query(
            "Resource changes (last 24h)",
            "resourcechanges\n"
            "| extend ct = todatetime(properties.changeAttributes.timestamp)\n"
            "| where ct > ago(24h)\n"
            "| summarize Changes = count()\n"
            "| extend Window = 'Last 24h'\n"
            "| project Window, Changes",
            visualization="tiles",
            size=4,
            extra=kpi_tiles("Window", "Changes"),
        ),
    )


def _automation() -> list[dict[str, Any]]:
    return section(
        "Automation posture at a glance",
        "Health of the automation itself over the last 30 days: outcomes and whether the "
        "guardrails are blocking or actions are failing.",
        la_query(
            "Automation posture",
            "SREAgentActions_CL\n"
            "| where TimeGenerated > ago(30d)\n"
            "| summarize Failed = countif(Outcome=='Failed'), Blocked = countif(Outcome=='Blocked'), "
            "Total = count()\n"
            "| extend Posture = case(Failed > 0, 'CRITICAL', Blocked > 0, 'DEGRADED', "
            "Total > 0, 'ELEVATED', 'HEALTHY')\n"
            "| project Posture, ['Actions (30d)'] = Total",
            visualization="tiles",
            size=4,
            extra=status_banner("Posture", "Actions (30d)"),
        ),
        la_query(
            "Action outcomes",
            "SREAgentActions_CL\n"
            "| where TimeGenerated > ago(30d)\n"
            "| summarize Actions = count() by Outcome\n"
            "| order by Actions desc",
            visualization="tiles",
            size=3,
            extra=kpi_tiles("Outcome", "Actions"),
        ),
        la_query(
            "Outcome mix",
            "SREAgentActions_CL\n"
            "| where TimeGenerated > ago(30d)\n"
            "| summarize Actions = count() by Outcome",
            visualization="piechart",
            size=1,
        ),
    )


def _app_reliability() -> list[dict[str, Any]]:
    return section(
        "Reliability at a glance",
        "Headline SLIs for the selected window: request volume, failure rate, and tail "
        "latency against the SLO.",
        la_query(
            "SLO posture",
            "AppRequests\n"
            "| where TimeGenerated {TimeRange}\n"
            "| summarize Total = count(), Failed = countif(Success == false)\n"
            "| extend FailPct = iff(Total > 0, round(100.0*Failed/Total, 2), 0.0)\n"
            "| extend Posture = case(Total == 0, 'ELEVATED', FailPct > 5, 'CRITICAL', "
            "FailPct > 1, 'DEGRADED', 'HEALTHY')\n"
            "| project Posture, Requests = Total",
            visualization="tiles",
            size=4,
            extra=status_banner("Posture", "Requests"),
        ),
        la_query(
            "Reliability KPIs",
            "AppRequests\n"
            "| where TimeGenerated {TimeRange}\n"
            "| summarize ['Requests'] = count(), ['Failures'] = countif(Success == false),\n"
            "            ['p95 ms'] = round(percentile(DurationMs, 95), 0),\n"
            "            ['p99 ms'] = round(percentile(DurationMs, 99), 0)\n"
            "| project Metric = 'Requests', Value = ['Requests']\n"
            "| union (AppRequests | where TimeGenerated {TimeRange} | summarize Value = countif(Success == false) | extend Metric = 'Failures' | project Metric, Value)\n"
            "| union (AppRequests | where TimeGenerated {TimeRange} | summarize Value = toint(round(percentile(DurationMs,95),0)) | extend Metric = 'p95 ms' | project Metric, Value)",
            visualization="tiles",
            size=3,
            extra=kpi_tiles("Metric", "Value"),
        ),
    )


def _ea_governance() -> list[dict[str, Any]]:
    return section(
        "Governance at a glance",
        "Tenant policy-compliance posture and the compliant vs non-compliant split.",
        arg_query(
            "Compliance posture",
            "policyresources\n"
            "| where type =~ 'microsoft.policyinsights/policystates'\n"
            "| extend cs = tostring(properties.complianceState)\n"
            "| summarize C = countif(cs == 'Compliant'), Total = count()\n"
            "| extend Pct = iff(Total > 0, round(100.0*C/Total, 1), 100.0)\n"
            "| extend Posture = case(Pct < 60, 'CRITICAL', Pct < 80, 'DEGRADED', "
            "Pct < 95, 'ELEVATED', 'HEALTHY')\n"
            "| project Posture, ['Compliance %'] = Pct",
            visualization="tiles",
            size=4,
            extra=status_banner("Posture", "Compliance %"),
        ),
        arg_query(
            "Policy compliance mix",
            "policyresources\n"
            "| where type =~ 'microsoft.policyinsights/policystates'\n"
            "| summarize Count = count() by State = tostring(properties.complianceState)\n"
            "| order by Count desc",
            visualization="piechart",
            size=1,
        ),
        arg_query(
            "Compliance counts",
            "policyresources\n"
            "| where type =~ 'microsoft.policyinsights/policystates'\n"
            "| summarize States = count() by State = tostring(properties.complianceState)\n"
            "| order by States desc",
            visualization="tiles",
            size=3,
            extra=kpi_tiles("State", "States"),
        ),
    )


def _quality_cost() -> list[dict[str, Any]]:
    return section(
        "Observability quality & cost at a glance",
        "Ingestion volume, billable split, and telemetry coverage of the monitoring estate.",
        la_query(
            "Billable ingestion (selected window)",
            "Usage\n"
            "| where TimeGenerated {TimeRange}\n"
            "| summarize ['Billable GB'] = round(sum(Quantity)/1024, 1)\n"
            "| extend Metric = 'Billable GB'\n"
            "| project Metric, Value = ['Billable GB']",
            visualization="tiles",
            size=4,
            extra=kpi_tiles("Metric", "Value"),
        ),
        la_query(
            "Billable vs non-billable",
            "Usage\n"
            "| where TimeGenerated {TimeRange}\n"
            "| summarize GB = round(sum(Quantity)/1024, 1) by Billable = tostring(IsBillable)",
            visualization="piechart",
            size=1,
        ),
        la_query(
            "Top tables by ingestion",
            "Usage\n"
            "| where TimeGenerated {TimeRange}\n"
            "| where IsBillable == true\n"
            "| summarize GB = round(sum(Quantity)/1024, 2) by DataType\n"
            "| order by GB desc\n| take 10",
            visualization="barchart",
            size=1,
        ),
    )


def _security() -> list[dict[str, Any]]:
    hero = [
        arg_query(
            "Active critical alerts",
            "securityresources\n"
            "| where type =~ 'microsoft.security/alerts'\n"
            "| extend sev = tostring(properties.severity),\n"
            "         status = tostring(properties.status)\n"
            "| where status in ('Active','InProgress') and sev =~ 'High'\n"
            "| summarize Value = count()\n"
            "| extend Metric = 'Active critical alerts'\n"
            "| project Metric, Value",
            visualization="tiles",
            size=4,
            extra=kpi_tiles_thresholds("Metric", "Value", _HERO_ANY_BAD),
        ),
        arg_query(
            "Open sensitive ports",
            "resources\n"
            "| where type =~ 'microsoft.network/networksecuritygroups'\n"
            "| mv-expand rule = properties.securityRules\n"
            "| extend access = tostring(rule.properties.access),\n"
            "         direction = tostring(rule.properties.direction),\n"
            "         src = tostring(rule.properties.sourceAddressPrefix),\n"
            "         dport = tostring(rule.properties.destinationPortRange)\n"
            "| where access =~ 'Allow' and direction =~ 'Inbound'\n"
            "| where src in ('*','0.0.0.0/0','Internet','Any')\n"
            "| where dport in ('22','3389','1433','3306','5432','6379','27017') or dport == '*'\n"
            "| summarize Value = count()\n"
            "| extend Metric = 'Open sensitive ports'\n"
            "| project Metric, Value",
            visualization="tiles",
            size=4,
            extra=kpi_tiles_thresholds("Metric", "Value", _HERO_ZERO_GOOD),
        ),
        arg_query(
            "Orphaned managed identities",
            "resources\n"
            "| where type =~ 'microsoft.managedidentity/userassignedidentities'\n"
            "| extend miId = tolower(id)\n"
            "| join kind=leftanti (resources\n"
            "    | where isnotnull(identity.userAssignedIdentities)\n"
            "    | mv-expand uami = bag_keys(identity.userAssignedIdentities)\n"
            "    | project uamiId = tolower(tostring(uami))) on $left.miId == $right.uamiId\n"
            "| summarize Value = count()\n"
            "| extend Metric = 'Orphaned managed identities'\n"
            "| project Metric, Value",
            visualization="tiles",
            size=4,
            extra=kpi_tiles_thresholds("Metric", "Value", _HERO_ZERO_GOOD),
        ),
        arg_query(
            "Privileged SPs & users",
            "authorizationresources\n"
            "| where type =~ 'microsoft.authorization/roleassignments'\n"
            "| extend principalType = tostring(properties.principalType)\n"
            + _ROLE_JOIN
            + "| where RoleName in ('Owner','User Access Administrator','Contributor')\n"
            "| where principalType in ('ServicePrincipal','User')\n"
            "| summarize Value = count()\n"
            "| extend Metric = 'Privileged SPs & users'\n"
            "| project Metric, Value",
            visualization="tiles",
            size=4,
            extra=kpi_tiles_thresholds("Metric", "Value", _HERO_ZERO_GOOD),
        ),
    ]

    response = [
        arg_query(
            "MTTR (alert fired to resolved)",
            "alertsmanagementresources\n"
            "| where type =~ 'microsoft.alertsmanagement/alerts'\n"
            "| extend fired = todatetime(properties.essentials.startDateTime),\n"
            "         resolved = todatetime(properties.essentials.monitorConditionResolvedDateTime),\n"
            "         cond = tostring(properties.essentials.monitorCondition)\n"
            "| where cond == 'Resolved' and isnotempty(resolved)\n"
            "| summarize Value = round(avg(datetime_diff('hour', resolved, fired)), 1)\n"
            "| extend Metric = 'MTTR (fired->resolved)'\n"
            "| project Metric, Value",
            visualization="tiles",
            size=4,
            extra=kpi_tiles_thresholds("Metric", "Value", _HERO_HOURS_BAD),
        ),
        arg_query(
            "Active alerts open over 4h",
            "alertsmanagementresources\n"
            "| where type =~ 'microsoft.alertsmanagement/alerts'\n"
            "| extend cond = tostring(properties.essentials.monitorCondition),\n"
            "         fired = todatetime(properties.essentials.startDateTime)\n"
            "| where cond == 'Fired' and fired < ago(4h)\n"
            "| summarize Value = count()\n"
            "| extend Metric = 'Alerts open > 4h'\n"
            "| project Metric, Value",
            visualization="tiles",
            size=4,
            extra=kpi_tiles_thresholds("Metric", "Value", _HERO_ZERO_GOOD),
        ),
        with_no_data(
            la_query(
                "Privileged MFA coverage %",
                "IdentityInventory_CL\n"
                "| where TimeGenerated {TimeRange}\n"
                "| summarize arg_max(TimeGenerated, *) by PrincipalId_s\n"
                "| where Privileged_b == true\n"
                "| summarize Total = count(), Mfa = countif(MfaRegistered_b == true)\n"
                "| extend Value = iff(Total == 0, 100.0, round(100.0 * Mfa / Total, 1))\n"
                "| extend Metric = 'Priv MFA coverage'\n"
                "| project Metric, Value",
                visualization="tiles",
                size=4,
                extra=kpi_tiles_thresholds("Metric", "Value", _HERO_PCT_COVERAGE),
            ),
            "Privileged MFA coverage appears once the posture task populates "
            "IdentityInventory_CL (privileged flag + MFA-registration).",
        ),
        with_no_data(
            la_query(
                "Conditional Access coverage %",
                "SigninLogs\n"
                "| where TimeGenerated {TimeRange}\n"
                "| where ResultType == 0\n"
                "| summarize Total = count(), Evaluated = countif(ConditionalAccessStatus in ('success','failure'))\n"
                "| extend Value = iff(Total == 0, 0.0, round(100.0 * Evaluated / Total, 1))\n"
                "| extend Metric = 'CA coverage'\n"
                "| project Metric, Value",
                visualization="tiles",
                size=4,
                extra=kpi_tiles_thresholds("Metric", "Value", _HERO_PCT_COVERAGE),
            ),
            "Conditional Access coverage needs SigninLogs streamed to this workspace.",
        ),
    ]

    return section(
        "Security posture at a glance",
        "Active threat first: the **Active alert** banner and hero KPIs turn **red** on live "
        "signal. The response-health row (MTTR, aging alerts, MFA & Conditional-Access "
        "coverage) tracks how fast and how well we respond; then the Defender backlog by "
        "severity - worst first.",
        _active_alert_banner(),
        arg_query(
            "Security posture",
            "securityresources\n"
            "| where type =~ 'microsoft.security/assessments'\n"
            "| extend status = tostring(properties.status.code), sev = tostring(properties.metadata.severity)\n"
            "| where status == 'Unhealthy'\n"
            "| summarize High = countif(sev == 'High'), Med = countif(sev == 'Medium'), Total = count()\n"
            "| extend Posture = case(High > 0, 'CRITICAL', Med > 0, 'DEGRADED', Total > 0, 'ELEVATED', 'HEALTHY')\n"
            "| project Posture, ['Unhealthy findings'] = Total",
            visualization="tiles",
            size=4,
            extra=status_banner("Posture", "Unhealthy findings"),
        ),
        *hero,
        *response,
        with_no_data(
            arg_query(
                "Unhealthy findings by severity",
                "securityresources\n"
                "| where type =~ 'microsoft.security/assessments'\n"
                "| extend status = tostring(properties.status.code), sev = tostring(properties.metadata.severity)\n"
                "| where status == 'Unhealthy'\n"
                "| summarize Findings = count() by Severity = sev\n"
                "| order by Severity asc",
                visualization="barchart",
                size=1,
            ),
            "No unhealthy Defender assessments in scope - either the posture is clean or "
            "Defender for Cloud is not enabled on these subscriptions.",
        ),
    )


def _maintenance() -> list[dict[str, Any]]:
    return section(
        "Workbook health at a glance",
        "Open maintenance findings for the suite and their severity mix.",
        la_query(
            "Open maintenance findings by severity",
            "SREAgentRecommendations_CL\n"
            "| where TimeGenerated > ago(30d)\n"
            "| where WorkbookName == 'SRE Agent Workbook Maintenance'\n"
            "| where Status in ('New','Pending approval','Approved','In progress','Failed')\n"
            "| summarize arg_max(LastUpdated, *) by RecommendationId\n"
            "| summarize Findings = count() by Severity\n"
            "| order by Severity asc",
            visualization="tiles",
            size=3,
            extra=kpi_tiles("Severity", "Findings"),
        ),
        la_query(
            "Finding category mix",
            "SREAgentRecommendations_CL\n"
            "| where TimeGenerated > ago(30d)\n"
            "| where WorkbookName == 'SRE Agent Workbook Maintenance'\n"
            "| where Status in ('New','Pending approval','Approved','In progress','Failed')\n"
            "| summarize arg_max(LastUpdated, *) by RecommendationId\n"
            "| summarize Findings = count() by Category",
            visualization="piechart",
            size=1,
        ),
    )


_DISPATCH = {
    "sre-agent-command-center": _command_center,
    "tenant-health-model": _tenant_health,
    "incident-triage-rca": _incident_rca,
    "automation-remediation": _automation,
    "application-reliability": _app_reliability,
    "enterprise-architecture-governance": _ea_governance,
    "observability-quality-cost": _quality_cost,
    "security-identity-observability": _security,
    "sre-agent-workbook-maintenance": _maintenance,
    # operations-command-center builds its own glance-first scorecard in its spec.
}


def scorecard_for(slug: str) -> list[dict[str, Any]]:
    builder = _DISPATCH.get(slug)
    return builder() if builder else []
