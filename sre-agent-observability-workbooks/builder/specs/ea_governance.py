"""Spec: 7. Enterprise Architecture & Governance."""

from helpers import SUB_NAME_JOIN, arg_query, markdown, placeholder, section

ORDER = 7
SLUG = "enterprise-architecture-governance"
NAME = "Enterprise Architecture & Governance"
PAGE = "Enterprise Architecture & Governance"
PURPOSE = (
    "The **EA / platform governance** view: inventory, landing-zone and policy compliance, "
    "tagging, network posture, RBAC, resilience coverage, and architecture drift - driven by "
    "Azure Resource Graph across the tenant."
)


def sections():
    items = []

    items += section(
        "Management group & subscription inventory",
        "The account topology the rest of the suite is scoped against.",
        arg_query(
            "Subscriptions",
            "resourcecontainers\n"
            "| where type =~ 'microsoft.resources/subscriptions'\n"
            "| project Subscription = name, state = tostring(properties.state)\n"
            "| order by Subscription asc",
        ),
        arg_query(
            "Management groups",
            "resourcecontainers\n"
            "| where type =~ 'microsoft.management/managementgroups'\n"
            "| project name, displayName = tostring(properties.displayName)\n"
            "| order by displayName asc",
        ),
    )

    items += section(
        "Resource inventory",
        "What exists, by type, region, and environment tag. The denominator for every "
        "coverage and compliance metric.",
        arg_query(
            "Resources by type",
            "resources\n"
            "| summarize Count = count() by type\n"
            "| order by Count desc\n| take 50",
        ),
        arg_query(
            "Resources by region & environment",
            "resources\n"
            "| extend env = tostring(tags['Environment'])\n"
            "| summarize Count = count() by location, env\n"
            "| order by Count desc",
        ),
    )

    items += section(
        "Policy & landing-zone compliance",
        "Azure Policy compliance state across the tenant. Non-compliance is the agent's "
        "governance backlog.",
        arg_query(
            "Policy compliance summary",
            "policyresources\n"
            "| where type =~ 'microsoft.policyinsights/policystates'\n"
            "| extend state = tostring(properties.complianceState)\n"
            "| summarize Count = count() by state\n"
            "| order by Count desc",
        ),
        arg_query(
            "Top non-compliant policies",
            "policyresources\n"
            "| where type =~ 'microsoft.policyinsights/policystates'\n"
            "| where tostring(properties.complianceState) == 'NonCompliant'\n"
            "| summarize NonCompliant = count() by policyDefinition = tostring(properties.policyDefinitionName)\n"
            "| order by NonCompliant desc\n| take 25",
        ),
    )

    items += section(
        "Tag compliance",
        "Resources missing governance-critical tags (Owner, Environment, CostCenter). Drives "
        "ownership and cost-allocation gaps elsewhere in the suite.",
        arg_query(
            "Missing required tags",
            "resources\n"
            "| extend owner = tostring(tags['Owner']), env = tostring(tags['Environment']), cc = tostring(tags['CostCenter'])\n"
            "| extend MissingOwner = isempty(owner), MissingEnv = isempty(env), MissingCostCenter = isempty(cc)\n"
            "| where MissingOwner or MissingEnv or MissingCostCenter\n"
            "| summarize Count = count() by type\n"
            "| order by Count desc\n| take 50",
        ),
    )

    items += section(
        "Network exposure & private endpoints",
        "Public-exposure posture and private-endpoint adoption. Overlaps with Security & "
        "Identity; here it is an architecture-standard check.",
        arg_query(
            "Public IP addresses",
            "resources\n"
            "| where type =~ 'microsoft.network/publicipaddresses'\n"
            "| extend assoc = isnotempty(tostring(properties.ipConfiguration.id))\n"
            "| summarize Total = count(), Associated = countif(assoc) by location\n"
            "| order by Total desc",
        ),
        arg_query(
            "Private endpoint adoption",
            "resources\n"
            "| where type =~ 'microsoft.network/privateendpoints'\n"
            + SUB_NAME_JOIN
            + "| summarize PrivateEndpoints = count() by location, Subscription\n"
            "| order by PrivateEndpoints desc",
        ),
        placeholder(
            "Hub-spoke mapping",
            "Bind your network topology source (or vnet peering ARG) to validate hub-spoke "
            "adherence and detect orphaned/peered-incorrectly spokes.",
        ),
    )

    items += section(
        "RBAC posture",
        "Role-assignment surface, with emphasis on broad/privileged grants. Detailed risky-"
        "assignment analysis lives in Security & Identity.",
        arg_query(
            "Role assignments by role",
            "authorizationresources\n"
            "| where type =~ 'microsoft.authorization/roleassignments'\n"
            "| extend roleId = tostring(properties.roleDefinitionId)\n"
            "| summarize Assignments = count() by roleId\n"
            "| order by Assignments desc\n| take 25",
        ),
    )

    items += section(
        "Resilience & Advisor",
        "Zone-redundancy and backup/DR coverage, plus Azure Advisor's reliability and "
        "operational-excellence recommendations.",
        arg_query(
            "Advisor recommendations by category",
            "advisorresources\n"
            "| where type =~ 'microsoft.advisor/recommendations'\n"
            "| extend category = tostring(properties.category), impact = tostring(properties.impact)\n"
            "| summarize Count = count() by category, impact\n"
            "| order by Count desc",
        ),
        placeholder(
            "Zone redundancy & backup/DR coverage",
            "Bind recoveryservicesresources for protected-item coverage and inspect SKU/"
            "zones fields per resource type for zone-redundancy posture.",
        ),
    )

    items += section(
        "Architecture drift & cost governance",
        "Resources deviating from approved regions/SKUs, and cost-governance hooks. Drift is "
        "reviewed weekly by the agent.",
        placeholder(
            "Architecture drift",
            "Compare resource regions/SKUs to the approved allow-lists (parameters) to list "
            "deviations; compare current topology to the last approved snapshot for drift.",
        ),
        placeholder(
            "Cost governance",
            "Bind Cost Management exports (or a CostByResource_CL) for spend by subscription/"
            "owner/tag and budget-vs-actual. Detailed cost analysis is in Observability "
            "Quality & Cost.",
        ),
    )

    return items
