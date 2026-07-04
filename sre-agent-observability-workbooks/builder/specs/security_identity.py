"""Spec: 9. Security & Identity Observability."""

from helpers import (
    ROLE_NAME_JOIN,
    SUB_NAME_JOIN,
    arg_query,
    heatmap_grid,
    la_query,
    markdown,
    pivot_params,
    placeholder,
    resource_link_grid,
    section,
    tab_group,
    with_export,
    with_no_data,
)

ORDER = 9
SLUG = "security-identity-observability"
NAME = "Security & Identity Observability"
PAGE = "Security & Identity Observability"
# Lead the page with the tabbed navigation (Overview | Threats | ... | Posture) placed
# above the shared hero scorecard and Top 10 section. build_workbooks honours this flag.
SECTIONS_FIRST = True
PURPOSE = (
    "**Security and identity observability**: live security alerts first, then identity "
    "risk, exposure, privileged access, lifecycle hygiene, change/RCA, expiring credentials, "
    "and finally Defender posture - the security lens on the same tenant the rest of the "
    "suite operates."
)

# Severity-word coloring (High/Medium/Low) reused for alert grids: High=red,
# Medium=orange, Low=yellow, else green. Mirrors the suite's severity color language.
_SEV_WORD_COLORS = [
    {"operator": "==", "thresholdValue": "High", "representation": "red", "text": "{0}"},
    {"operator": "==", "thresholdValue": "Medium", "representation": "orange", "text": "{0}"},
    {"operator": "==", "thresholdValue": "Low", "representation": "yellow", "text": "{0}"},
    {"operator": "Default", "thresholdValue": None, "representation": "green", "text": "{0}"},
]

# Entra risk levels are lowercase (high/medium/low). Same color language as severity.
_RISK_COLORS = [
    {"operator": "==", "thresholdValue": "high", "representation": "red", "text": "{0}"},
    {"operator": "==", "thresholdValue": "medium", "representation": "orange", "text": "{0}"},
    {"operator": "==", "thresholdValue": "low", "representation": "yellow", "text": "{0}"},
    {"operator": "Default", "thresholdValue": None, "representation": "green", "text": "{0}"},
]

# Stale-account priority matrix: P0 (privileged AND dormant AND standing creds) = red,
# down to P3 = green. Colored by exact-match rule.
_PRIORITY_COLORS = [
    {"operator": "==", "thresholdValue": "P0", "representation": "red", "text": "{0}"},
    {"operator": "==", "thresholdValue": "P1", "representation": "orange", "text": "{0}"},
    {"operator": "==", "thresholdValue": "P2", "representation": "yellow", "text": "{0}"},
    {"operator": "Default", "thresholdValue": None, "representation": "green", "text": "{0}"},
]

# Legacy authentication client apps (no modern-auth / MFA enforcement path).
_LEGACY_AUTH_CLIENTS = (
    "'Other clients','IMAP4','POP3','MAPI','SMTP','Exchange ActiveSync',"
    "'Exchange Web Services','Authenticated SMTP','AutoDiscover','Offline Address Book'"
)

# Days-to-expiry bands: <=30 red, <=60 orange, <=90 yellow, else green (urgent glows red -
# note this is the inverse of a heatmap, which would redden the largest values).
_EXPIRY_COLORS = [
    {"operator": "<=", "thresholdValue": "30", "representation": "red", "text": "{0}"},
    {"operator": "<=", "thresholdValue": "60", "representation": "orange", "text": "{0}"},
    {"operator": "<=", "thresholdValue": "90", "representation": "yellow", "text": "{0}"},
    {"operator": "Default", "thresholdValue": None, "representation": "green", "text": "{0}"},
]

# Well-known built-in role GUID -> name (AzureActivity can't cross-join ARG roledefinitions).
_ROLE_GUID_CASE = (
    "| extend roleGuid = tolower(extract('([0-9a-fA-F-]{36})$', 1, rdid))\n"
    "| extend RoleName = case(\n"
    "    roleGuid == '8e3af657-a8ff-443c-a75c-2fe8c4bcb635', 'Owner',\n"
    "    roleGuid == 'b24988ac-6180-42a0-ab88-20f7382dd24c', 'Contributor',\n"
    "    roleGuid == '18d7d88d-d35e-4fb5-a5c3-7773c20a72d9', 'User Access Administrator',\n"
    "    roleGuid == 'acdd72a7-3385-48ef-bd42-f606fbee5b42', 'Reader',\n"
    "    isnotempty(roleGuid), strcat('Role ', roleGuid), 'n/a')\n"
)

# Entity-centric pivot parameters (backed by pivot_params). A tile exports into one of
# these on click; downstream tiles filter on it. Empty default -> show all rows.
_PIVOT_USER = "SelectedUser"
_PIVOT_IP = "SelectedIP"
_PIVOT_RESOURCE = "SelectedResource"


def _pivot_filter(column: str, param: str) -> str:
    """A KQL line that filters ``column`` to the pivot ``param`` when one is selected, and
    passes all rows when it is empty (self-degrading)."""
    return "| where isempty('{" + param + "}') or " + column + " =~ '{" + param + "}'\n"


def sections():
    overview = [
        markdown(
            "## Overview\n\n"
            "Response-first security cockpit. Pick a tab to drill in; the **hero KPIs and "
            "'Security posture at a glance' scorecard sit just below** and turn **red** on "
            "live signal. **Click any risky user, source IP, or "
            "resource row to pivot** - the sign-in, RBAC-change, stale-account, and "
            "credential tiles refilter to that entity for one-click investigation. Clear "
            "a pivot by reloading the page.\n\n"
            "Tabs: **Threats** (active alerts) - **Identity** (risk) - **Exposure** - "
            "**Access** (privilege + credentials) - **Lifecycle** (stale accounts) - "
            "**Changes** (RCA) - **Posture** (Defender backlog).",
            name="sec-overview",
        )
    ]

    # 1. ACTIVE SECURITY ALERTS -- lead with live threat, not backlog.
    threats = section(
        "Active security alerts",
        "Live Microsoft Defender for Cloud security alerts (active attacks / suspicious "
        "activity), newest first. Click a row to **pivot** the page to that resource; "
        "click **Open** to jump to its blade.",
        with_export(
            with_no_data(
                arg_query(
                    "Active Defender alerts",
                    "securityresources\n"
                    "| where type =~ 'microsoft.security/alerts'\n"
                    "| extend sev = tostring(properties.severity),\n"
                    "         status = tostring(properties.status),\n"
                    "         alertName = tostring(properties.alertDisplayName),\n"
                    "         entity = tostring(properties.compromisedEntity),\n"
                    "         started = todatetime(properties.timeGeneratedUtc),\n"
                    "         resId = tostring(properties.resourceIdentifiers[0].azureResourceId)\n"
                    "| where status in ('Active','InProgress')\n"
                    + _pivot_filter("resId", _PIVOT_RESOURCE)
                    + SUB_NAME_JOIN
                    + "| project started, Severity = sev, Alert = alertName, Entity = entity,\n"
                    "           Subscription, ResourceId = resId\n"
                    "| order by started desc\n| take 100",
                    grid_settings={
                        "formatters": [
                            {
                                "columnMatch": "Severity",
                                "formatter": 18,
                                "formatOptions": {"thresholdsOptions": "colors", "thresholdsGrid": _SEV_WORD_COLORS},
                            },
                            {
                                "columnMatch": "ResourceId",
                                "formatter": 7,
                                "formatOptions": {"linkTarget": "Resource", "linkLabel": "Open"},
                            },
                        ]
                    },
                ),
                "No active Defender for Cloud alerts in scope - either the tenant is quiet or "
                "Defender for Cloud alerting is not enabled on these subscriptions.",
            ),
            "ResourceId",
            _PIVOT_RESOURCE,
        ),
        with_no_data(
            la_query(
                "Sentinel alerts (if connected)",
                "SecurityAlert\n"
                "| where TimeGenerated {TimeRange}\n"
                "| summarize arg_max(TimeGenerated, *) by SystemAlertId\n"
                "| project TimeGenerated, AlertSeverity, AlertName, CompromisedEntity, ProviderName\n"
                "| order by TimeGenerated desc\n| take 100",
                grid_settings={
                    "formatters": [
                        {
                            "columnMatch": "AlertSeverity",
                            "formatter": 18,
                            "formatOptions": {"thresholdsOptions": "colors", "thresholdsGrid": _SEV_WORD_COLORS},
                        }
                    ]
                },
            ),
            "No Microsoft Sentinel SecurityAlert rows - Sentinel is not connected to this "
            "workspace, or no alerts in the selected time range. (Defender alerts above are "
            "the primary source.)",
        ),
        with_no_data(
            la_query(
                "Sentinel incidents (open)",
                "SecurityIncident\n"
                "| where TimeGenerated {TimeRange}\n"
                "| summarize arg_max(TimeGenerated, *) by IncidentNumber\n"
                "| where Status != 'Closed'\n"
                "| project TimeGenerated, Severity, Title, Status, Owner = tostring(Owner)\n"
                "| order by TimeGenerated desc\n| take 100",
            ),
            "No open Sentinel incidents (or Sentinel is not connected to this workspace).",
        ),
    )

    # 2. IDENTITY RISK -- risky sign-ins, password spray, legacy auth.
    identity = section(
        "Identity risk",
        "Risky sign-ins, password-spray campaigns, and legacy-auth logons - the live "
        "identity-attack signal. Click a risky user or source IP to **pivot** the page. "
        "Requires Entra ID logs (SigninLogs / AADUserRiskEvents) and Entra ID P1/P2.",
        with_export(
            with_no_data(
                la_query(
                    "Risky sign-ins (Entra Identity Protection)",
                    "AADUserRiskEvents\n"
                    "| where TimeGenerated {TimeRange}\n"
                    "| summarize arg_max(TimeGenerated, *) by UserId, RiskEventType\n"
                    "| where RiskState in ('atRisk','confirmedCompromised')\n"
                    "| extend User = UserPrincipalName\n"
                    + _pivot_filter("User", _PIVOT_USER)
                    + _pivot_filter("IpAddress", _PIVOT_IP)
                    + "| extend ['Open in Entra'] = strcat("
                    "'https://portal.azure.com/#view/Microsoft_AAD_IAM/UserDetailsMenuBlade/~/RiskyUsers/userId/', UserId)\n"
                    "| project TimeGenerated, User, RiskLevel, RiskEventType,\n"
                    "          IpAddress, Location, ['Open in Entra']\n"
                    "| order by TimeGenerated desc\n| take 100",
                    grid_settings={
                        "formatters": [
                            {
                                "columnMatch": "RiskLevel",
                                "formatter": 18,
                                "formatOptions": {"thresholdsOptions": "colors", "thresholdsGrid": _RISK_COLORS},
                            },
                            {
                                "columnMatch": "Open in Entra",
                                "formatter": 7,
                                "formatOptions": {"linkTarget": "Url", "linkLabel": "Open in Entra"},
                            },
                        ]
                    },
                ),
                "No risky sign-ins in the window - either Identity Protection is clean, or "
                "AADUserRiskEvents is not streamed / Entra ID P2 is not licensed on this tenant.",
            ),
            "User",
            _PIVOT_USER,
        ),
        with_export(
            with_no_data(
                la_query(
                    "Password-spray campaigns (failures grouped by source IP)",
                    "SigninLogs\n"
                    "| where TimeGenerated {TimeRange}\n"
                    "| where ResultType != 0\n"
                    "| summarize Failures = count(), TargetedUsers = dcount(UserPrincipalName),\n"
                    "            FirstSeen = min(TimeGenerated), LastSeen = max(TimeGenerated)\n"
                    "    by IPAddress, Location = tostring(LocationDetails.countryOrRegion)\n"
                    "| where Failures >= 5\n"
                    + _pivot_filter("IPAddress", _PIVOT_IP)
                    + "| project IPAddress, Location, TargetedUsers, Failures, FirstSeen, LastSeen\n"
                    "| order by TargetedUsers desc, Failures desc\n| take 100",
                    grid_settings=heatmap_grid("TargetedUsers"),
                ),
                "No sign-in failure clusters in the window - one row per source IP; a high "
                "distinct-user count is a spray signal. (Or SigninLogs is not streamed here.)",
            ),
            "IPAddress",
            _PIVOT_IP,
        ),
        with_no_data(
            la_query(
                "Legacy-authentication sign-ins",
                "SigninLogs\n"
                "| where TimeGenerated {TimeRange}\n"
                "| where ClientAppUsed in (" + _LEGACY_AUTH_CLIENTS + ")\n"
                "| summarize SignIns = count(), Users = dcount(UserPrincipalName) by ClientAppUsed\n"
                "| order by SignIns desc",
                visualization="barchart",
            ),
            "No legacy-auth sign-ins in the window - modern auth is enforced (or SigninLogs "
            "is not streamed to this workspace). Legacy auth bypasses MFA; block it.",
        ),
    )

    # 3. EXPOSURE -- internet-facing attack surface.
    exposure = section(
        "Exposure",
        "Internet-facing surface: public IPs, public storage, and NSGs opening sensitive "
        "ports. Each is an attack-surface and a governance finding.",
        with_no_data(
            arg_query(
                "Public IP exposure",
                "resources\n"
                "| where type =~ 'microsoft.network/publicipaddresses'\n"
                "| extend assoc = isnotempty(tostring(properties.ipConfiguration.id))\n"
                "| where assoc\n"
                + SUB_NAME_JOIN
                + "| summarize ExposedPublicIPs = count() by location, Subscription\n"
                "| order by ExposedPublicIPs desc",
                visualization="barchart",
            ),
            "No associated public IP addresses in scope.",
        ),
        with_no_data(
            arg_query(
                "Storage accounts allowing public blob access",
                "resources\n"
                "| where type =~ 'microsoft.storage/storageaccounts'\n"
                "| extend allowBlobPublic = tobool(properties.allowBlobPublicAccess),\n"
                "         defaultAction = tostring(properties.networkAcls.defaultAction)\n"
                "| where allowBlobPublic == true or defaultAction == 'Allow'\n"
                + SUB_NAME_JOIN
                + "| project name, location, allowBlobPublic, defaultAction, Subscription, ResourceId = id\n"
                "| take 100",
                grid_settings=resource_link_grid("ResourceId", "Open"),
            ),
            "No storage accounts allow public blob access or open network default action.",
        ),
        with_no_data(
            arg_query(
                "NSGs allowing Internet inbound on sensitive ports",
                "resources\n"
                "| where type =~ 'microsoft.network/networksecuritygroups'\n"
                "| mv-expand rule = properties.securityRules\n"
                "| extend access = tostring(rule.properties.access),\n"
                "         direction = tostring(rule.properties.direction),\n"
                "         src = tostring(rule.properties.sourceAddressPrefix),\n"
                "         dport = tostring(rule.properties.destinationPortRange),\n"
                "         ruleName = tostring(rule.name)\n"
                "| where access =~ 'Allow' and direction =~ 'Inbound'\n"
                "| where src in ('*','0.0.0.0/0','Internet','Any')\n"
                "| where dport in ('22','3389','1433','3306','5432','6379','27017') or dport == '*'\n"
                "| extend PortRisk = case(dport in ('3389','22'), 3, dport == '*', 3,\n"
                "                          dport in ('1433','3306','5432','6379','27017'), 2, 1)\n"
                + SUB_NAME_JOIN
                + "| project NSG = name, Rule = ruleName, Port = dport, Source = src,\n"
                "           PortRisk, Subscription, ResourceId = id\n"
                "| order by PortRisk desc, NSG asc\n| take 100",
                grid_settings={
                    "formatters": [
                        {
                            "columnMatch": "PortRisk",
                            "formatter": 8,
                            "formatOptions": {"palette": "red"},
                        },
                        {
                            "columnMatch": "ResourceId",
                            "formatter": 7,
                            "formatOptions": {"linkTarget": "Resource", "linkLabel": "Open"},
                        },
                    ]
                },
            ),
            "No NSG rules expose sensitive ports (22/3389/1433/3306/5432/6379/27017) to the "
            "Internet in scope. Good - keep management ports private.",
        ),
    )

    # 4. PRIVILEGED ACCESS -- broad grants, guests, high-privilege SPs.
    access = section(
        "Privileged access",
        "Privileged role assignments and broad Owner grants. Over-privilege is the most "
        "common path to blast-radius escalation.",
        arg_query(
            "Privileged role assignments",
            "authorizationresources\n"
            "| where type =~ 'microsoft.authorization/roleassignments'\n"
            "| extend principalType = tostring(properties.principalType),\n"
            "         principalId = tostring(properties.principalId),\n"
            "         scope = tostring(properties.scope)\n"
            + ROLE_NAME_JOIN
            + "| where RoleName in ('Owner', 'User Access Administrator', 'Contributor')\n"
            + SUB_NAME_JOIN
            + "| project RoleName, principalType, scope, principalId, Subscription\n"
            "| order by RoleName asc\n| take 100",
        ),
        markdown(
            "> Privileged grants (**Owner / User Access Administrator / Contributor**) at "
            "subscription or management-group scope warrant review. Role names are resolved "
            "from role definitions; the daily posture task enriches `principalId` to a "
            "display name / UPN so reviewers see who holds the grant, not a GUID.",
            name="rbac-note",
        ),
        with_no_data(
            arg_query(
                "Guest / external accounts holding roles",
                "authorizationresources\n"
                "| where type =~ 'microsoft.authorization/roleassignments'\n"
                "| extend principalType = tostring(properties.principalType),\n"
                "         principalId = tostring(properties.principalId),\n"
                "         scope = tostring(properties.scope)\n"
                "| where principalType == 'User'\n"
                + ROLE_NAME_JOIN
                + SUB_NAME_JOIN
                + "| project RoleName, scope, ['PrincipalId (task resolves name/UPN)'] = principalId, Subscription\n"
                "| order by RoleName asc\n| take 100",
            ),
            "No user-principal role assignments in scope. The daily posture task flags which "
            "of these are guest (#EXT#) accounts by resolving each principal via Graph.",
        ),
        with_no_data(
            arg_query(
                "High-privilege service principals",
                "authorizationresources\n"
                "| where type =~ 'microsoft.authorization/roleassignments'\n"
                "| extend principalType = tostring(properties.principalType),\n"
                "         principalId = tostring(properties.principalId),\n"
                "         scope = tostring(properties.scope)\n"
                "| where principalType == 'ServicePrincipal'\n"
                + ROLE_NAME_JOIN
                + "| where RoleName in ('Owner', 'Contributor')\n"
                "| where scope !contains '/resourceGroups/' or scope endswith tolower(subscriptionId)\n"
                + SUB_NAME_JOIN
                + "| project RoleName, scope, ['PrincipalId (task resolves SP name)'] = principalId, Subscription\n"
                "| order by RoleName asc\n| take 100",
            ),
            "No service principals hold Owner/Contributor at subscription or management-group "
            "scope. The posture task resolves each SP's display name.",
        ),
    )

    # 5. IDENTITY LIFECYCLE -- orphaned / dangling identity artifacts.
    lifecycle = section(
        "Identity lifecycle & stale-account hygiene",
        "Stale accounts (priority matrix P0-P3), orphaned MIs, and dangling assignments - "
        "delete/disable candidates. Every proposal is recommend-only and human-gated; "
        "break-glass identities tagged `ExcludeFromLifecycle` are skipped by the task.",
        with_no_data(
            arg_query(
                "Orphaned user-assigned managed identities",
                "resources\n"
                "| where type =~ 'microsoft.managedidentity/userassignedidentities'\n"
                "| extend miId = tolower(id)\n"
                "| join kind=leftanti (resources\n"
                "    | where isnotnull(identity.userAssignedIdentities)\n"
                "    | mv-expand uami = bag_keys(identity.userAssignedIdentities)\n"
                "    | project uamiId = tolower(tostring(uami))) on $left.miId == $right.uamiId\n"
                + SUB_NAME_JOIN
                + "| project ['Managed identity'] = name, resourceGroup, Subscription, ResourceId = id\n"
                "| order by ['Managed identity'] asc\n| take 100",
                grid_settings=resource_link_grid("ResourceId", "Open"),
            ),
            "No orphaned user-assigned managed identities - every UAMI is attached to at least "
            "one resource. (System-assigned identities are excluded by design.)",
        ),
        with_no_data(
            arg_query(
                "Dangling role assignments (target resource gone)",
                "authorizationresources\n"
                "| where type =~ 'microsoft.authorization/roleassignments'\n"
                "| extend principalType = tostring(properties.principalType),\n"
                "         principalId = tostring(properties.principalId),\n"
                "         scope = tolower(tostring(properties.scope))\n"
                "| where principalType == 'ServicePrincipal'\n"
                "| where scope contains '/resourcegroups/' and scope contains '/providers/'\n"
                + ROLE_NAME_JOIN
                + "| join kind=leftanti (resources | project scope = tolower(id)) on scope\n"
                + SUB_NAME_JOIN
                + "| project RoleName, scope, ['PrincipalId (task resolves name)'] = principalId, Subscription\n"
                "| order by RoleName asc\n| take 100",
            ),
            "No dangling resource-scoped role assignments detected - every assignment's target "
            "resource still exists.",
        ),
        with_no_data(
            la_query(
                "Stale accounts - priority matrix (P0-P3)",
                "IdentityInventory_CL\n"
                "| where TimeGenerated {TimeRange}\n"
                "| summarize arg_max(TimeGenerated, *) by PrincipalId_s\n"
                "| where ExcludeFromLifecycle_b != true\n"
                "| where isnotempty(Priority_s)\n"
                + _pivot_filter("DisplayName_s", _PIVOT_USER)
                + "| extend ['Open in Entra'] = EvidenceLinks_s\n"
                "| project Priority = Priority_s, Type = PrincipalType_s, Identity = DisplayName_s,\n"
                "          ['Days since sign-in'] = DaysSinceSignIn_d, ['Cred age (d)'] = CredAgeDays_d,\n"
                "          Privileged = Privileged_b, Recommendation = Recommendation_s, ['Open in Entra']\n"
                "| order by Priority asc, ['Days since sign-in'] desc\n| take 200",
                grid_settings={
                    "formatters": [
                        {
                            "columnMatch": "Priority",
                            "formatter": 18,
                            "formatOptions": {"thresholdsOptions": "colors", "thresholdsGrid": _PRIORITY_COLORS},
                        },
                        {
                            "columnMatch": "Days since sign-in",
                            "formatter": 8,
                            "formatOptions": {"palette": "red"},
                        },
                        {
                            "columnMatch": "Open in Entra",
                            "formatter": 7,
                            "formatOptions": {"linkTarget": "Url", "linkLabel": "Open in Entra"},
                        },
                    ]
                },
            ),
            "No stale accounts scored yet. The daily posture task enriches users, service "
            "principals, and managed identities (last sign-in, privilege, credential age) into "
            "IdentityInventory_CL and scores each P0-P3. P0 = privileged AND dormant AND has "
            "standing credentials. Once the task runs, ranked candidates appear here.",
        ),
        with_no_data(
            la_query(
                "Dormant service principals (no sign-in in window)",
                "AADServicePrincipalSignInLogs\n"
                "| where TimeGenerated {TimeRange}\n"
                "| summarize LastSignIn = max(TimeGenerated) by ServicePrincipalName, ServicePrincipalId\n"
                "| extend ['Days since sign-in'] = datetime_diff('day', now(), LastSignIn) * -1\n"
                "| where ['Days since sign-in'] >= 90\n"
                "| project ServicePrincipal = ServicePrincipalName, LastSignIn, ['Days since sign-in']\n"
                "| order by ['Days since sign-in'] desc\n| take 100",
                grid_settings=heatmap_grid("Days since sign-in"),
            ),
            "No dormant service principals (or AADServicePrincipalSignInLogs is not streamed). "
            "The task cross-checks these against standing credentials and privilege.",
        ),
        with_no_data(
            la_query(
                "Dormant managed identities (no sign-in in window)",
                "AADManagedIdentitySignInLogs\n"
                "| where TimeGenerated {TimeRange}\n"
                "| summarize LastSignIn = max(TimeGenerated) by ServicePrincipalName, ServicePrincipalId\n"
                "| extend ['Days since sign-in'] = datetime_diff('day', now(), LastSignIn) * -1\n"
                "| where ['Days since sign-in'] >= 90\n"
                "| project ['Managed identity'] = ServicePrincipalName, LastSignIn, ['Days since sign-in']\n"
                "| order by ['Days since sign-in'] desc\n| take 100",
                grid_settings=heatmap_grid("Days since sign-in"),
            ),
            "No dormant managed identities with sign-in history (or "
            "AADManagedIdentitySignInLogs is not streamed). Orphaned MIs are covered above.",
        ),
    )

    # 6. CHANGE / RCA -- config/identity changes correlated to incidents.
    changes = section(
        "Change / RCA",
        "RBAC, Conditional Access, Key Vault, and app-credential/consent changes - the "
        "config-and-identity change stream. Unexpected grants or new credentials near an "
        "incident are a strong RCA signal.",
        with_no_data(
            la_query(
                "RBAC change events",
                "AzureActivity\n"
                "| where TimeGenerated {TimeRange}\n"
                "| where OperationNameValue has 'roleAssignments/write' or OperationNameValue has 'roleAssignments/delete'\n"
                "| extend rdid = tostring(parse_json(tostring(parse_json(tostring(Properties)).requestbody)).Properties.RoleDefinitionId)\n"
                + _ROLE_GUID_CASE
                + _pivot_filter("Caller", _PIVOT_USER)
                + "| project TimeGenerated, Operation = OperationNameValue, Status = ActivityStatusValue,\n"
                "          Caller, RoleName, ResourceId = _ResourceId\n"
                "| order by TimeGenerated desc\n| take 100",
                grid_settings=resource_link_grid("ResourceId", "Open"),
            ),
            "No RBAC change events in the window (or AzureActivity is not streamed to this "
            "workspace).",
        ),
        with_no_data(
            la_query(
                "Conditional Access policy changes",
                "AuditLogs\n"
                "| where TimeGenerated {TimeRange}\n"
                "| where OperationName has 'conditional access policy'\n"
                "| project TimeGenerated, OperationName,\n"
                "          InitiatedBy = tostring(InitiatedBy.user.userPrincipalName),\n"
                "          Policy = tostring(TargetResources[0].displayName), Result\n"
                "| order by TimeGenerated desc\n| take 100",
            ),
            "No Conditional Access policy changes in the window (or AuditLogs is not streamed). "
            "CA changes can weaken MFA/access controls - review any near an incident.",
        ),
        with_no_data(
            la_query(
                "Key Vault access-policy / RBAC changes",
                "AzureActivity\n"
                "| where TimeGenerated {TimeRange}\n"
                "| where tolower(_ResourceId) has '/providers/microsoft.keyvault/vaults/'\n"
                "| where OperationNameValue has 'accessPolicies' or OperationNameValue has 'vaults/write'\n"
                "    or OperationNameValue has 'roleAssignments'\n"
                "| project TimeGenerated, Operation = OperationNameValue, Status = ActivityStatusValue,\n"
                "          Caller, ResourceId = _ResourceId\n"
                "| order by TimeGenerated desc\n| take 100",
                grid_settings=resource_link_grid("ResourceId", "Open"),
            ),
            "No Key Vault access-policy or RBAC changes in the window (or AzureActivity is not "
            "streamed). A widened vault policy is a common exfiltration precursor.",
        ),
        with_no_data(
            la_query(
                "New app credentials & OAuth consent grants",
                "AuditLogs\n"
                "| where TimeGenerated {TimeRange}\n"
                "| where OperationName in ('Add service principal credentials',\n"
                "    'Update application - Certificates and secrets management',\n"
                "    'Add app role assignment to service principal',\n"
                "    'Consent to application', 'Add delegated permission grant')\n"
                "| project TimeGenerated, OperationName,\n"
                "          InitiatedBy = tostring(InitiatedBy.user.userPrincipalName),\n"
                "          Target = tostring(TargetResources[0].displayName), Result\n"
                "| order by TimeGenerated desc\n| take 100",
            ),
            "No new app credentials or OAuth consent grants in the window (or AuditLogs is not "
            "streamed). New secrets / broad consent grants are a common persistence technique.",
        ),
    )

    # 7. EXPIRING CREDENTIALS -- Key Vault + Entra app credential expiry (live).
    credentials = section(
        "Expiring credentials",
        "Key Vault secrets/certificates and Entra app-registration credentials approaching "
        "expiry (30/60/90d) - rotate before they lapse and break auth. Fed by the posture "
        "task's companion tables.",
        with_no_data(
            la_query(
                "Key Vault secrets / certificates expiring",
                "KeyVaultCredentials_CL\n"
                "| where TimeGenerated {TimeRange}\n"
                "| summarize arg_max(TimeGenerated, *) by ResourceId_s, ItemName_s\n"
                "| where DaysToExpiry_d <= 90\n"
                "| project Vault = VaultName_s, Type = ItemType_s, Item = ItemName_s,\n"
                "          ['Days to expiry'] = DaysToExpiry_d, ExpiresOn = ExpiresOn_t,\n"
                "          Subscription = Subscription_s, ResourceId = ResourceId_s\n"
                "| order by ['Days to expiry'] asc\n| take 200",
                grid_settings={
                    "formatters": [
                        {
                            "columnMatch": "Days to expiry",
                            "formatter": 18,
                            "formatOptions": {"thresholdsOptions": "colors", "thresholdsGrid": _EXPIRY_COLORS},
                        },
                        {
                            "columnMatch": "ResourceId",
                            "formatter": 7,
                            "formatOptions": {"linkTarget": "Resource", "linkLabel": "Open"},
                        },
                    ]
                },
            ),
            "No Key Vault secrets/certificates expiring within 90 days (or the posture task "
            "has not yet populated KeyVaultCredentials_CL - it reads vault expiry via the "
            "data-plane where it has access).",
        ),
        with_no_data(
            la_query(
                "Entra app-registration credentials expiring",
                "AppCredentials_CL\n"
                "| where TimeGenerated {TimeRange}\n"
                "| summarize arg_max(TimeGenerated, *) by AppId_s, KeyId_s\n"
                "| where DaysToExpiry_d <= 90\n"
                "| extend ['Open in Entra'] = EvidenceLinks_s\n"
                "| project Application = AppDisplayName_s, Type = CredType_s,\n"
                "          ['Days to expiry'] = DaysToExpiry_d, ExpiresOn = ExpiresOn_t,\n"
                "          Owner = Owner_s, ['Open in Entra']\n"
                "| order by ['Days to expiry'] asc\n| take 200",
                grid_settings={
                    "formatters": [
                        {
                            "columnMatch": "Days to expiry",
                            "formatter": 18,
                            "formatOptions": {"thresholdsOptions": "colors", "thresholdsGrid": _EXPIRY_COLORS},
                        },
                        {
                            "columnMatch": "Open in Entra",
                            "formatter": 7,
                            "formatOptions": {"linkTarget": "Url", "linkLabel": "Open in Entra"},
                        },
                    ]
                },
            ),
            "No Entra app-registration credentials expiring within 90 days (or the posture "
            "task has not yet populated AppCredentials_CL via Microsoft Graph). Expired app "
            "secrets/certs break service-to-service auth - rotate ahead of the red band.",
        ),
    )

    # 8. POSTURE & SECURE SCORE -- backlog, demoted to last.
    posture = section(
        "Posture & secure score",
        "Defender for Cloud posture, secure score, and Key Vault protection - the burn-down "
        "backlog behind the live signals above.",
        with_no_data(
            arg_query(
                "Security assessments (unhealthy)",
                "securityresources\n"
                "| where type =~ 'microsoft.security/assessments'\n"
                "| extend status = tostring(properties.status.code),\n"
                "         sev = tostring(properties.metadata.severity),\n"
                "         display = tostring(properties.displayName)\n"
                "| where status == 'Unhealthy'\n"
                "| summarize Count = count() by sev, display\n"
                "| order by Count desc\n| take 50",
                visualization="barchart",
            ),
            "No unhealthy Defender assessments in scope (or Defender for Cloud is not enabled).",
        ),
        placeholder(
            "Secure score",
            "Bind Defender secure score (securityresources microsoft.security/secureScores "
            "or the Defender API) for the score trend and per-control contribution.",
        ),
        with_no_data(
            arg_query(
                "Key Vault protection settings",
                "resources\n"
                "| where type =~ 'microsoft.keyvault/vaults'\n"
                "| extend purgeProtection = tobool(properties.enablePurgeProtection),\n"
                "         softDelete = tobool(properties.enableSoftDelete)\n"
                + SUB_NAME_JOIN
                + "| project name, location, purgeProtection, softDelete, Subscription, ResourceId = id\n"
                "| order by name asc",
                grid_settings=resource_link_grid("ResourceId", "Open"),
            ),
            "No Key Vaults in scope.",
        ),
        placeholder(
            "Key Vault diagnostic logging",
            "Use the diagnostic-settings coverage source (Observability Quality & Cost) "
            "filtered to microsoft.keyvault/vaults to confirm AuditEvent logging is on.",
        ),
    )

    return [
        pivot_params(
            [
                (_PIVOT_USER, "Selected user"),
                (_PIVOT_IP, "Selected IP"),
                (_PIVOT_RESOURCE, "Selected resource"),
            ],
            name="security-pivot-params",
        ),
        tab_group(
            "secnav",
            [
                ("Overview", overview),
                ("Threats", threats),
                ("Identity", identity),
                ("Exposure", exposure),
                ("Access", access + credentials),
                ("Lifecycle", lifecycle),
                ("Changes", changes),
                ("Posture", posture),
            ],
            name="security-tabs",
        ),
    ]
