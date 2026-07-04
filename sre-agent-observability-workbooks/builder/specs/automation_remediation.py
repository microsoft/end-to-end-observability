"""Spec: 4. Automation & Remediation."""

from helpers import la_query, markdown, placeholder, section

ORDER = 4
SLUG = "automation-remediation"
NAME = "Automation & Remediation"
PAGE = "Automation & Remediation"
PURPOSE = (
    "Track what the SRE Agent **can** do, what it **did**, what **requires approval**, "
    "and **rollback** status - the agent's automation surface with guardrails front and center."
)


def sections():
    items = []

    items += section(
        "Automation catalog",
        "The catalog of supported automations: resource type, trigger condition, required "
        "permissions, approval requirement, risk rating, and rollback availability.",
        placeholder(
            "SREAgentAutomationCatalog_CL",
            "Bind the automation catalog table (ActionType, SupportedResourceType, "
            "TriggerCondition, RequiredPermissions, ApprovalRequired, RiskRating, "
            "RollbackAvailable, LastRun). Documented in docs/data-sources.md.",
        ),
    )

    items += section(
        "Pending approvals",
        "Recommendations whose agent action is blocked on human sign-off. Approving "
        "authorizes execution per `AgentActionType`.",
        la_query(
            "Actions pending approval",
            "SREAgentRecommendations_CL\n"
            "| where TimeGenerated > ago(30d)\n"
            "| where Status == 'Pending approval'\n"
            "| summarize arg_max(LastUpdated, *) by RecommendationId\n"
            "| project LastUpdated, Severity, RecommendationText, AgentActionType, AffectedResourceId, RemediationRisk, RollbackAvailable, OwnerTeam\n"
            "| order by LastUpdated desc",
        ),
    )

    items += section(
        "Remediation execution history",
        "Every agent-initiated action and its outcome. The audit trail for what changed, "
        "when, by whom (or which agent), and whether it can be rolled back.",
        la_query(
            "Remediation history",
            "SREAgentActions_CL\n"
            "| where TimeGenerated {TimeRange}\n"
            "| project StartedOn, ActionType, TargetResourceId, Outcome, Approver, RollbackAvailable, CompletedOn\n"
            "| order by StartedOn desc\n| take 200",
        ),
        placeholder(
            "SREAgentActions_CL",
            "Custom table for automation runs (ActionId, ActionType, TargetResourceId, "
            "Outcome [Running/Succeeded/Failed/Blocked], BlockedReason, Approver, "
            "RollbackAvailable, StartedOn, CompletedOn).",
        ),
    )

    items += section(
        "Success / failure rate",
        "Reliability of the automation itself. A rising failure rate for an action type "
        "is a signal to pause it and review.",
        la_query(
            "Outcome rate by action type",
            "SREAgentActions_CL\n"
            "| where TimeGenerated {TimeRange}\n"
            "| summarize Total = count(), Succeeded = countif(Outcome=='Succeeded'), Failed = countif(Outcome=='Failed') by ActionType\n"
            "| extend SuccessRate = round(100.0*Succeeded/Total,2)\n"
            "| order by Failed desc",
        ),
    )

    items += section(
        "Failed remediations",
        "Actions that failed - candidates for rollback verification and root-cause review.",
        la_query(
            "Failed remediations",
            "SREAgentActions_CL\n"
            "| where TimeGenerated {TimeRange}\n"
            "| where Outcome == 'Failed'\n"
            "| project StartedOn, ActionType, TargetResourceId, BlockedReason, RollbackAvailable\n"
            "| order by StartedOn desc",
        ),
    )

    items += section(
        "Blocked actions & guardrails",
        "Actions the agent declined to run, with the guardrail that stopped them. This is "
        "the system working as intended - review for false positives only.",
        la_query(
            "Blocked actions by reason",
            "SREAgentActions_CL\n"
            "| where TimeGenerated {TimeRange}\n"
            "| where Outcome == 'Blocked'\n"
            "| summarize Count = count() by BlockedReason\n"
            "| order by Count desc",
        ),
        markdown(
            "### Guardrails\n"
            "The agent blocks an action when any guardrail trips:\n\n"
            "- Blocked by policy / Blocked by RBAC / Blocked by business hours\n"
            "- Blocked for production / Blocked due to missing owner\n"
            "- Blocked due to insufficient confidence / Blocked due to missing rollback\n"
            "- Blocked because Service Health indicates a platform issue\n\n"
            "Guardrail changes are **approval-required** and versioned.",
            name="guardrails",
        ),
    )

    return items
