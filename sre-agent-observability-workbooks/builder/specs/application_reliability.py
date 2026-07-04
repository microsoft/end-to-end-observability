"""Spec: 6. Application Reliability."""

from helpers import la_query, markdown, placeholder, section

ORDER = 6
SLUG = "application-reliability"
NAME = "Application Reliability"
PAGE = "Application Reliability"
PURPOSE = (
    "The **application team view**: SLOs and error budget, user-journey health, request/"
    "failure/latency, exceptions, dependency reliability, and release correlation - sourced "
    "from Application Insights workspace-based tables."
)


def sections():
    items = []

    items += section(
        "Application health & SLO",
        "Headline reliability for the selected application: success rate, error budget "
        "remaining, and burn rate against your SLO target.",
        la_query(
            "Request success rate (SLI)",
            "AppRequests\n"
            "| where TimeGenerated {TimeRange}\n"
            "| summarize Total = count(), Failed = countif(Success == false)\n"
            "| extend SuccessRatePct = round(100.0*(Total-Failed)/Total, 3),\n"
            "         ErrorBudgetTarget = 99.9\n"
            "| extend BudgetRemainingPct = round((SuccessRatePct - ErrorBudgetTarget) / (100 - ErrorBudgetTarget) * 100, 1)",
        ),
        placeholder(
            "Error budget & burn rate",
            "Bind your SLO definition (target, window) to compute remaining error budget "
            "and multi-window burn rate. Sample logic in docs/scoring-model.md.",
        ),
    )

    items += section(
        "Availability tests",
        "Outside-in synthetic availability and pass rate by test. Catches user-facing "
        "outages telemetry alone can miss.",
        la_query(
            "Availability test results",
            "AppAvailabilityResults\n"
            "| where TimeGenerated {TimeRange}\n"
            "| summarize Runs = count(), SuccessRate = round(100.0*countif(Success==true)/count(),2), AvgDurationMs = round(avg(DurationMs),0) by Name\n"
            "| order by SuccessRate asc",
        ),
    )

    items += section(
        "Request volume & failure rate",
        "Throughput and failure trend over time. Pre-aggregated by hour to stay within the "
        "chart point budget on wide time ranges.",
        la_query(
            "Requests and failures over time",
            "AppRequests\n"
            "| where TimeGenerated {TimeRange}\n"
            "| summarize Requests = count(), Failures = countif(Success == false) by bin(TimeGenerated, 1h)\n"
            "| order by TimeGenerated asc",
        ),
    )

    items += section(
        "Latency (p50/p95/p99)",
        "Latency percentiles over time. p95/p99 reveal tail pain that averages hide.",
        la_query(
            "Request duration percentiles",
            "AppRequests\n"
            "| where TimeGenerated {TimeRange}\n"
            "| summarize p50 = percentile(DurationMs,50), p95 = percentile(DurationMs,95), p99 = percentile(DurationMs,99) by bin(TimeGenerated, 1h)\n"
            "| order by TimeGenerated asc",
        ),
    )

    items += section(
        "Exceptions",
        "Top exception types driving failures. Feeds RCA and is a primary remediation target.",
        la_query(
            "Top exceptions",
            "AppExceptions\n"
            "| where TimeGenerated {TimeRange}\n"
            "| summarize Count = count() by ExceptionType, ProblemId\n"
            "| order by Count desc\n| take 20",
        ),
    )

    items += section(
        "Dependencies",
        "Downstream dependency failure rate and latency. Many app incidents are really a "
        "dependency's incident.",
        la_query(
            "Dependency reliability",
            "AppDependencies\n"
            "| where TimeGenerated {TimeRange}\n"
            "| summarize Calls = count(), Failures = countif(Success==false), p95Ms = round(percentile(DurationMs,95),0) by Target, DependencyType\n"
            "| extend FailureRatePct = round(100.0*Failures/Calls,2)\n"
            "| order by Failures desc\n| take 25",
        ),
        placeholder(
            "Distributed traces / correlation",
            "Use OperationId / OperationParentId across AppRequests, AppDependencies, and "
            "AppExceptions to reconstruct an end-to-end trace for a failed request.",
        ),
    )

    items += section(
        "User journeys",
        "Health of the key multi-step user journeys (e.g. sign-in -> search -> checkout). "
        "Map journey steps to operation names.",
        la_query(
            "Top operations (journey steps)",
            "AppRequests\n"
            "| where TimeGenerated {TimeRange}\n"
            "| summarize Requests = count(), FailureRatePct = round(100.0*countif(Success==false)/count(),2), p95Ms = round(percentile(DurationMs,95),0) by OperationName\n"
            "| order by Requests desc\n| take 25",
        ),
        placeholder(
            "User-journey map",
            "Bind a journey definition (UserJourneys_CL) mapping ordered operation names to "
            "named journeys for step-level health rollup.",
        ),
    )

    items += section(
        "Release correlation & coverage gaps",
        "Deployments/feature-flag changes lined up against reliability, plus telemetry and "
        "synthetic-test coverage gaps for this app.",
        la_query(
            "Deployment-time activity (App Service/Functions)",
            "AzureActivity\n"
            "| where TimeGenerated {TimeRange}\n"
            "| where OperationNameValue has_any ('publish','deploy','slotsswap','restart')\n"
            "| project TimeGenerated, OperationNameValue, ActivityStatusValue, Caller, _ResourceId\n"
            "| order by TimeGenerated desc\n| take 50",
        ),
        placeholder(
            "Feature flags & config changes",
            "Bind App Configuration / feature-flag change feed to overlay flag flips on the "
            "reliability timeline.",
        ),
    )

    return items
