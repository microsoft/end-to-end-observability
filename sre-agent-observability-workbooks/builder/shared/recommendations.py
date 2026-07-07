"""
The reusable **Top 10 SRE Agent Recommendations** section.

This is the operating surface that makes every workbook page agent-centric: it
reads prioritized, auditable recommendations the SRE Agent has written to the
central custom log table ``SREAgentRecommendations_CL`` and renders the ten most
important for *this* page, with drilldown links to evidence, runbooks, incidents,
pull requests, and work items.

The same query shape is emitted on every page; only the ``WorkbookName`` and
``WorkbookPage`` constants differ (injected by the builder). The canonical query
is also kept standalone in ``queries/kql/top10-recommendations.kql``.
"""

from __future__ import annotations

from typing import Any

from helpers import la_query, link_grid, markdown

# Active statuses surfaced by the Top 10 (per the suite spec). Expired
# recommendations are excluded via ExpiresOn.
ACTIVE_STATUSES = ["New", "Pending approval", "Approved", "In progress", "Failed"]

# {{WorkbookName}} / {{WorkbookPage}} are build-time constants (double braces).
# {Severity} / {BusinessCriticality} / {OwnerTeam} / {RecommendationStatus} are
# workbook runtime parameters (single braces) that further narrow the list.
TOP10_QUERY = """SREAgentRecommendations_CL
| where TimeGenerated > ago(30d)
| where WorkbookName == "{{WorkbookName}}" and WorkbookPage == "{{WorkbookPage}}"
| where Status in ("New","Pending approval","Approved","In progress","Failed")
| where isnull(ExpiresOn) or ExpiresOn > now()
| where "{Severity}" == "All" or "{Severity}" == "" or Severity == "{Severity}"
| where "{BusinessCriticality}" == "All" or "{BusinessCriticality}" == "" or BusinessCriticality == "{BusinessCriticality}"
| where "{OwnerTeam}" == "*" or "{OwnerTeam}" == "" or OwnerTeam =~ "{OwnerTeam}"
| where "{RecommendationStatus}" in ("Active","All","") or Status == "{RecommendationStatus}"
| summarize arg_max(LastUpdated, *) by RecommendationId
| order by RecommendationScore desc, Severity asc, LastUpdated desc
| take 10
| project
    Rank,
    Severity,
    Recommendation = RecommendationText,
    Scope = AffectedScope,
    OwnerTeam,
    Confidence = ConfidenceScore,
    ['Agent action'] = AgentActionType,
    ['Approval required'] = ApprovalRequired,
    Status,
    ['Last updated'] = LastUpdated,
    Evidence = EvidenceLinks,
    Runbook = RelatedWorkItem,
    Incident = RelatedIncidentId,
    PR = RelatedPullRequest,
    RecommendationId"""

_EXPLAINER = (
    "## Top 10 SRE Agent Recommendations\n\n"
    "Prioritized, auditable actions the **SRE Agent** has produced for this page, "
    "ranked by `RecommendationScore`. Each row is evidence-backed and carries its "
    "agent action type, approval requirement, and lifecycle status.\n\n"
    "- **Approval required = true** -> the agent will *not* act without human sign-off "
    "(always true for SLO, alert, remediation, health-model, access, or panel-deletion changes).\n"
    "- Use the **Evidence / Incident / PR / Runbook** links to drill into the supporting data.\n"
    "- Source table: `SREAgentRecommendations_CL`. If empty, no active recommendations exist for "
    "this page, or the workspace parameter is not yet set - see *Configuration* in the README."
)


def top10_section(workbook_name: str, workbook_page: str) -> list[dict[str, Any]]:
    """Return the markdown explainer + the Top 10 recommendations grid for a page."""
    query = TOP10_QUERY.replace("{{WorkbookName}}", workbook_name).replace(
        "{{WorkbookPage}}", workbook_page
    )
    grid = link_grid("Evidence", "Incident", "PR", "Runbook")
    return [
        markdown(_EXPLAINER, name="top10-explainer"),
        la_query(
            "Top 10 SRE Agent Recommendations",
            query,
            visualization="table",
            size=0,
            name="top10-recommendations",
            grid_settings=grid,
        ),
        markdown("\n---\n", name="top10-divider"),
    ]
