targetScope = 'resourceGroup'
param name string
param principalId string
param roleDefinitionId string
resource site 'Microsoft.Web/sites@2024-04-01' existing = { name: name }
resource assignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(site.id, principalId, roleDefinitionId)
  scope: site
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: roleDefinitionId
  }
}
