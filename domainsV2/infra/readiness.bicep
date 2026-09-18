// Deploy this file independently in Incremental mode; it never redeclares app settings.
// https://learn.microsoft.com/azure/azure-monitor/logs/granular-rbac-log-analytics#role-selection
// https://learn.microsoft.com/azure/azure-monitor/logs/granular-rbac-use-case#build-restrictive-condition
targetScope = 'resourceGroup'

param workspaceName string = 'iot-domains-v2-e0q0j7-law'
param appName string = 'iot-domains-v2-e0q0j7-app'

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: workspaceName
}

resource app 'Microsoft.Web/sites@2023-12-01' existing = {
  name: appName
}

resource readinessRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(workspace.id, 'energy-observations-readiness-role-v1')
  properties: {
    roleName: '${workspaceName}-observation-readiness'
    description: 'V2 query execution; assign only with an EnergyObservations_CL table condition.'
    type: 'CustomRole'
    assignableScopes: [resourceGroup().id]
    permissions: [
      {
        actions: [
          'Microsoft.OperationalInsights/workspaces/query/read'
        ]
        notActions: []
        dataActions: [
          'Microsoft.OperationalInsights/workspaces/tables/data/read'
        ]
        notDataActions: []
      }
    ]
  }
}

resource observationReadiness 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(workspace.id, app.id, readinessRole.id)
  scope: workspace
  properties: {
    principalId: app.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: readinessRole.id
    description: 'Allow this V2 app identity to query observation data only, never injection audit.'
    conditionVersion: '2.0'
    condition: '''
      (
        !(ActionMatches{'Microsoft.OperationalInsights/workspaces/tables/data/read'})
        OR
        @Resource[Microsoft.OperationalInsights/workspaces/tables:name] StringEquals 'EnergyObservations_CL'
      )
      '''
  }
}

output readinessRoleId string = readinessRole.id
output roleAssignmentId string = observationReadiness.id
output workspaceResourceId string = workspace.id
output workspaceId string = workspace.properties.customerId
output principalId string = app.identity.principalId
