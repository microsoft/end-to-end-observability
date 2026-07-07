"""Spec: 2. Tenant Health Model."""

from helpers import SUB_NAME_JOIN, arg_query, la_query, markdown, placeholder, section

ORDER = 2
SLUG = "tenant-health-model"
NAME = "Tenant Health Model"
PAGE = "Tenant Health Model"
PURPOSE = (
    "The health hierarchy and the **SRE Agent's interpretation of health**. "
    "**Unknown is not healthy** - missing telemetry, stale data, missing owner, or "
    "missing diagnostic settings resolve to *Unknown/Risk* and must be closed out."
)


def sections():
    items = []

    items.append(
        markdown(
            "## Health hierarchy\n\n"
            "`Tenant -> Management Group -> Subscription -> Workload/Application -> "
            "User Journey -> Component -> Dependency -> Infrastructure Resource -> "
            "Network Path -> Data Store`\n\n"
            "**States:** Healthy / Degraded / Unhealthy / **Unknown** (= risk). "
            "The agent rolls health up this hierarchy; any Unknown at a lower level "
            "propagates risk upward.",
            name="hierarchy",
        )
    )

    items += section(
        "Health rollup by scope",
        "Resource Health distribution by subscription and by region. Large Unknown "
        "slices indicate telemetry gaps, not health.",
        arg_query(
            "Health state by subscription",
            "healthresources\n"
            "| where type =~ 'microsoft.resourcehealth/availabilitystatuses'\n"
            "| extend state = tostring(properties.availabilityState)\n"
            + SUB_NAME_JOIN
            + "| summarize Count = count() by Subscription, state\n"
            "| order by Count desc",
        ),
        arg_query(
            "Health state by region",
            "healthresources\n"
            "| where type =~ 'microsoft.resourcehealth/availabilitystatuses'\n"
            "| extend state = tostring(properties.availabilityState)\n"
            "| summarize Count = count() by location, state\n"
            "| order by Count desc",
        ),
    )

    items += section(
        "Workloads without owner",
        "Resources missing an `Owner`/`OwnerTeam` tag cannot be routed or paged - the "
        "agent treats them as Unknown and recommends ownership assignment.",
        arg_query(
            "Resources missing owner tag",
            "resources\n"
            "| extend owner = tostring(tags['Owner']), team = tostring(tags['OwnerTeam'])\n"
            "| where isempty(owner) and isempty(team)\n"
            + SUB_NAME_JOIN
            + "| summarize ResourcesWithoutOwner = count() by type, Subscription\n"
            "| order by ResourcesWithoutOwner desc\n| take 100",
        ),
    )

    items += section(
        "Workloads without SLO",
        "Workloads with no defined SLO/error budget have no objective health bar. "
        "Bind your SLO registry to list them.",
        placeholder(
            "SLO registry",
            "Bind the SLO definition source (SLODefinitions_CL or a config repo) and "
            "anti-join against discovered workloads to list those lacking an SLO.",
        ),
    )

    items += section(
        "Resources without application mapping",
        "Resources with no `Application`/`Workload` tag cannot be rolled into a service "
        "view and weaken the health model.",
        arg_query(
            "Resources missing application/workload tag",
            "resources\n"
            "| extend app = tostring(tags['Application']), workload = tostring(tags['Workload'])\n"
            "| where isempty(app) and isempty(workload)\n"
            + SUB_NAME_JOIN
            + "| summarize Count = count() by Subscription, type\n"
            "| order by Count desc\n| take 100",
        ),
    )

    items += section(
        "Resources without telemetry",
        "No diagnostic settings or stale/absent heartbeat -> Unknown health. These drive "
        "the diagnostic-coverage recommendations in **Observability Quality & Cost**.",
        la_query(
            "Stale or missing heartbeat (last reported)",
            "Heartbeat\n"
            "| where TimeGenerated > ago(7d)\n"
            "| summarize LastHeartbeat = max(TimeGenerated) by Computer, _ResourceId\n"
            "| extend Status = iff(LastHeartbeat < ago(15m), 'Stale', 'Healthy')\n"
            "| where Status == 'Stale'\n"
            "| project Computer, _ResourceId, LastHeartbeat\n"
            "| order by LastHeartbeat asc",
        ),
        placeholder(
            "Diagnostic-settings coverage",
            "ARG cannot list diagnostic settings directly; use the documented "
            "Azure Policy 'Deploy diagnostic settings' compliance state, or an export, "
            "to enumerate resources without diagnostics. See docs/data-sources.md.",
        ),
    )

    items += section(
        "Missing dependency mapping",
        "Components without mapped dependencies hide failure paths. Application "
        "dependencies seen in telemetry vs the expected map reveal gaps.",
        la_query(
            "Observed application dependencies",
            "AppDependencies\n"
            "| where TimeGenerated {TimeRange}\n"
            "| summarize Calls = count() by Target, DependencyType\n"
            "| order by Calls desc\n| take 50",
        ),
    )

    items += section(
        "Synthetic test coverage",
        "Availability (synthetic) test coverage and pass rate. Workloads without a "
        "synthetic test have no outside-in health signal.",
        la_query(
            "Availability test coverage",
            "AppAvailabilityResults\n"
            "| where TimeGenerated {TimeRange}\n"
            "| summarize Runs = count(), SuccessRate = round(100.0*countif(Success==true)/count(),2) by Name\n"
            "| order by SuccessRate asc",
        ),
    )

    items += section(
        "Health model drift",
        "New/changed resources not yet represented in the health model, or scopes whose "
        "health signal disappeared. Reviewed weekly by the agent (see Workbook Maintenance).",
        placeholder(
            "Health model drift",
            "Compare the current resource graph snapshot to the last modeled snapshot "
            "(HealthModelSnapshot_CL) to surface additions, removals, and signal loss.",
        ),
    )

    return items
