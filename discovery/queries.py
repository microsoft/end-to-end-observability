"""KQL queries used by the discovery sweep.

Keep queries small, composable, and joinable. The goal of the primary
sweep is to return one row per in-scope resource together with the
observability signals we care about (diagnostic settings present,
linked App Insights, linked Log Analytics workspace, etc.).
"""

# Primary inventory + observability-signal sweep.
#
# Output columns:
#   id, name, type, location, subscriptionId, resourceGroup, tags,
#   hasDiagnosticSettings (bool), diagnosticSettingsCount (int),
#   diagnosticSinkWorkspaceIds (list[str])
#
# Notes:
#   * `microsoft.insights/diagnosticsettings` is an extension resource;
#     we left-join it to every resource by its `id` prefix.
#   * We intentionally project a small column set to keep payloads small.
INVENTORY_WITH_DIAGNOSTICS = r"""
resources
| project id, name, type=tolower(type), location, subscriptionId,
          resourceGroup, tags, properties
| join kind=leftouter (
    resources
    | where type =~ 'microsoft.insights/diagnosticsettings'
    | extend targetId = tolower(tostring(properties.targetResourceId))
    | extend workspaceId = tolower(tostring(properties.workspaceId))
    | summarize diagnosticSettingsCount = count(),
                diagnosticSinkWorkspaceIds = make_set(workspaceId)
              by targetId
  ) on $left.id == $right.targetId
| extend hasDiagnosticSettings = isnotnull(diagnosticSettingsCount)
| project-away targetId, properties
"""

# Catalog of monitoring resources already deployed in the estate. Used
# by the rules engine to detect "already covered" cases.
MONITORING_INVENTORY = r"""
resources
| where type in~ (
    'microsoft.insights/components',
    'microsoft.operationalinsights/workspaces',
    'microsoft.insights/actiongroups',
    'microsoft.insights/metricalerts',
    'microsoft.insights/scheduledqueryrules',
    'microsoft.insights/activitylogalerts'
  )
| project id, name, type=tolower(type), location, subscriptionId,
          resourceGroup, properties
"""
