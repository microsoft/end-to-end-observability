"""Spec: 10. SRE Agent Workbook Maintenance."""

from helpers import la_query, markdown, placeholder, section

ORDER = 10
SLUG = "sre-agent-workbook-maintenance"
NAME = "SRE Agent Workbook Maintenance"
PAGE = "Workbook Maintenance"
PURPOSE = (
    "The **weekly scheduled review** of the suite itself: data freshness, broken/empty/slow "
    "panels, new resources not yet represented, coverage gaps, and proposed dashboard "
    "improvements - with versioning, approvals, and rollback. The agent keeps the "
    "observability surfaces healthy the same way it keeps the tenant healthy."
)


def sections():
    items = []

    items.append(
        markdown(
            "## Review cycle\n\n"
            "The SRE Agent runs a weekly review (see `automation/weekly-review`). It writes "
            "findings to `SREAgentRecommendations_CL` with `WorkbookName = "
            "'SRE Agent Workbook Maintenance'`, **auto-applies only low-risk metadata "
            "changes**, opens **PRs for medium/high-risk** definition changes, and requires "
            "**mandatory approval** for SLO, alert, remediation, health-model, access, or "
            "panel-deletion changes. Every change is versioned, validated, and reversible.",
            name="review-cycle",
        )
    )

    items += section(
        "Review status",
        "When the last review ran, when the next is due, and which workbooks it covered.",
        la_query(
            "Last & next review",
            "SREAgentRecommendations_CL\n"
            "| where TimeGenerated > ago(30d)\n"
            "| where WorkbookName == 'SRE Agent Workbook Maintenance'\n"
            "| summarize LastReview = max(LastUpdated)\n"
            "| extend NextReviewDue = LastReview + 7d",
        ),
        placeholder(
            "Workbooks reviewed",
            "Bind the review run log (WorkbookReviewRuns_CL) for the list of workbooks "
            "covered, panels inspected, and duration of the last run.",
        ),
    )

    items += section(
        "Panel quality findings",
        "Broken, empty, slow, or failing panels detected by the review. The work queue for "
        "keeping the suite trustworthy.",
        la_query(
            "Open panel-quality recommendations",
            "SREAgentRecommendations_CL\n"
            "| where TimeGenerated > ago(30d)\n"
            "| where WorkbookName == 'SRE Agent Workbook Maintenance'\n"
            "| where Category in ('Broken panel','Empty panel','Slow query','Query failure','Stale data source')\n"
            "| summarize arg_max(LastUpdated, *) by RecommendationId\n"
            "| project Severity, Category, RecommendationText, AffectedScope, Status, RelatedPullRequest, LastUpdated\n"
            "| order by Severity asc, LastUpdated desc",
        ),
        placeholder(
            "Per-panel QA detail",
            "Bind WorkbookQA_CL (WorkbookName, PageName, PanelName, QueryDurationMs, "
            "RowCount, LinkOk, Error) emitted by the weekly review for drilldown.",
        ),
    )

    items += section(
        "Coverage & drift",
        "New subscriptions/resources not yet represented and health-model drift since the "
        "last review - the suite must keep up with the tenant.",
        placeholder(
            "New resources not represented",
            "The review compares the current resource graph to the last modeled snapshot "
            "(HealthModelSnapshot_CL) and lists new subscriptions/resource types not yet "
            "shown in any workbook.",
        ),
        placeholder(
            "Telemetry coverage gaps & health-model drift",
            "Surfaces the coverage gaps from Observability Quality & Cost and the drift "
            "from Tenant Health Model that the agent flagged this cycle.",
        ),
    )

    items += section(
        "Proposed & applied changes",
        "What the agent wants to change, what it already auto-applied (low-risk), and what "
        "is waiting on approval.",
        la_query(
            "Proposed workbook changes",
            "SREAgentRecommendations_CL\n"
            "| where TimeGenerated > ago(30d)\n"
            "| where WorkbookName == 'SRE Agent Workbook Maintenance'\n"
            "| where AgentActionType in ('Auto-update workbook','Open PR','Request approval')\n"
            "| summarize arg_max(LastUpdated, *) by RecommendationId\n"
            "| project Status, AgentActionType, ApprovalRequired, RecommendationText, RelatedPullRequest, RemediationRisk, RollbackAvailable, LastUpdated\n"
            "| order by LastUpdated desc",
        ),
    )

    items += section(
        "Versions, rollback & audit",
        "Workbook version history, available rollback points, and the full audit trail of "
        "agent changes. Nothing the agent does is unauditable or irreversible.",
        la_query(
            "Change audit trail",
            "SREAgentActions_CL\n"
            "| where TimeGenerated {TimeRange}\n"
            "| where ActionType has 'workbook'\n"
            "| project StartedOn, ActionType, TargetResourceId, Outcome, Approver, RollbackAvailable, CompletedOn\n"
            "| order by StartedOn desc\n| take 100",
        ),
        placeholder(
            "Workbook versions & usage",
            "Bind the version registry (WorkbookVersions_CL: WorkbookName, Version, "
            "CommitSha, AppliedOn, RollbackToVersion) and, if available, workbook usage "
            "telemetry for adoption-driven prioritization.",
        ),
    )

    return items
