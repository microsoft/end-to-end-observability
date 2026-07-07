"""Spec: 3. Incident Triage & RCA."""

from helpers import SUB_NAME_JOIN, arg_query, la_query, markdown, placeholder, section

ORDER = 3
SLUG = "incident-triage-rca"
NAME = "Incident Triage & RCA"
PAGE = "Incident Triage & RCA"
PURPOSE = (
    "Open from an incident and see **correlated evidence for root-cause analysis**: "
    "blast radius, a unified change/alert/telemetry timeline, agent-ranked root-cause "
    "candidates with confidence and missing data, and a recommended, reversible remediation."
)


def _incident_params():
    return {
        "type": 9,
        "content": {
            "version": "KqlParameterItem/1.0",
            "parameters": [
                {
                    "id": "param-incidentid",
                    "version": "KqlParameterItem/1.0",
                    "name": "IncidentId",
                    "label": "Incident ID",
                    "type": 1,
                    "isRequired": False,
                    "value": "",
                },
                {
                    "id": "param-correlationid",
                    "version": "KqlParameterItem/1.0",
                    "name": "CorrelationId",
                    "label": "Correlation ID",
                    "type": 1,
                    "isRequired": False,
                    "value": "",
                },
            ],
        },
        "name": "incident-context",
    }


def sections():
    items = [
        markdown(
            "## Incident context\n\nSet the incident/correlation identifiers to focus the "
            "evidence below. Leave blank to see tenant-wide active incidents.",
            name="incident-context-header",
        ),
        _incident_params(),
    ]

    items += section(
        "Incident summary",
        "Severity, state, owner, affected workload, and business criticality for the "
        "selected incident. Bind your incident/ITSM source for full fidelity.",
        placeholder(
            "Incident record",
            "Bind your incident table (ServiceNow/ADO/Incidents_CL) filtered by "
            "`{IncidentId}` to show severity, state, owner, affected workload, and "
            "business criticality.",
        ),
        arg_query(
            "Currently active alerts (triage candidates)",
            "alertsmanagementresources\n"
            "| where type =~ 'microsoft.alertsmanagement/alerts'\n"
            "| extend sev = tostring(properties.essentials.severity),\n"
            "         cond = tostring(properties.essentials.monitorCondition),\n"
            "         rule = tostring(properties.essentials.alertRule),\n"
            "         target = tostring(properties.essentials.targetResource),\n"
            "         fired = todatetime(properties.essentials.startDateTime)\n"
            "| where cond == 'Fired'\n"
            "| project fired, sev, rule, target\n| order by fired desc\n| take 50",
        ),
    )

    items += section(
        "Blast radius",
        "What is impacted: apps, regions, subscriptions, dependencies, user journeys, and "
        "SLOs. Quantifies how far the incident reaches.",
        arg_query(
            "Impacted resources by region (unhealthy)",
            "healthresources\n"
            "| where type =~ 'microsoft.resourcehealth/availabilitystatuses'\n"
            "| extend state = tostring(properties.availabilityState)\n"
            "| where state in ('Unavailable','Degraded')\n"
            + SUB_NAME_JOIN
            + "| summarize Impacted = count() by location, Subscription\n"
            "| order by Impacted desc",
        ),
        la_query(
            "Impacted dependencies (failures in window)",
            "AppDependencies\n"
            "| where TimeGenerated {TimeRange}\n"
            "| where Success == false\n"
            "| summarize Failures = count() by Target, DependencyType\n"
            "| order by Failures desc\n| take 25",
        ),
    )

    items += section(
        "Timeline",
        "Unified timeline of control-plane changes (deployments, config, RBAC, policy, "
        "network) from Activity logs. Correlate change spikes with the alert onset.",
        la_query(
            "Control-plane activity timeline",
            "AzureActivity\n"
            "| where TimeGenerated {TimeRange}\n"
            "| project TimeGenerated, OperationNameValue, ActivityStatusValue, Caller, ResourceGroup, _ResourceId\n"
            "| order by TimeGenerated desc\n| take 200",
        ),
        arg_query(
            "Recent resource changes",
            "resourcechanges\n"
            "| extend changeType = tostring(properties.changeType),\n"
            "         changeTime = todatetime(properties.changeAttributes.timestamp),\n"
            "         target = tostring(properties.targetResourceId)\n"
            "| project changeTime, changeType, target\n| order by changeTime desc\n| take 50",
        ),
    )

    items += section(
        "Root cause candidates",
        "Agent-ranked hypotheses with confidence, supporting evidence, what data is "
        "missing, and the suggested validation step. Review before approving remediation.",
        la_query(
            "Agent root-cause candidates",
            "SREAgentRecommendations_CL\n"
            "| where TimeGenerated > ago(7d)\n"
            "| where Category in ('Root cause','RCA')\n"
            "| where isempty('{IncidentId}') or RelatedIncidentId == '{IncidentId}'\n"
            "| summarize arg_max(LastUpdated, *) by RecommendationId\n"
            "| project ConfidenceScore, RecommendationText, Reason, EvidenceSummary, AffectedScope, EvidenceLinks\n"
            "| order by ConfidenceScore desc",
        ),
    )

    items += section(
        "Recommended remediation",
        "The agent's proposed fix, its risk, whether rollback is available, and the "
        "approval requirement. **Remediation is always approval-gated.**",
        la_query(
            "Recommended remediation actions",
            "SREAgentRecommendations_CL\n"
            "| where TimeGenerated > ago(7d)\n"
            "| where AgentActionType in ('Auto-remediate','Open PR','Open ticket','Request approval','Escalate')\n"
            "| where isempty('{IncidentId}') or RelatedIncidentId == '{IncidentId}'\n"
            "| summarize arg_max(LastUpdated, *) by RecommendationId\n"
            "| project Severity, RecommendationText, AgentActionType, ApprovalRequired, RemediationRisk, RollbackAvailable, Status, EvidenceLinks\n"
            "| order by Severity asc",
        ),
        markdown(
            "> **Approval & rollback.** Remediation requires human approval; the agent "
            "records `RollbackAvailable` and a rollback plan before any action. Approve "
            "via the Command Center *Pending approvals* tile.",
            name="rca-approval-note",
        ),
    )

    items += section(
        "Related incidents & post-incident actions",
        "Prior incidents with the same signature and the open post-incident action items "
        "the agent is tracking to closure.",
        placeholder(
            "Related incidents",
            "Bind your incident table to list prior incidents sharing this signature/"
            "ProblemId for pattern detection.",
        ),
        la_query(
            "Post-incident action items",
            "SREAgentRecommendations_CL\n"
            "| where TimeGenerated > ago(90d)\n"
            "| where Category == 'Post-incident'\n"
            "| where isempty('{IncidentId}') or RelatedIncidentId == '{IncidentId}'\n"
            "| summarize arg_max(LastUpdated, *) by RecommendationId\n"
            "| project Status, RecommendationText, OwnerTeam, RelatedWorkItem, LastUpdated\n"
            "| order by LastUpdated desc",
        ),
    )

    return items
