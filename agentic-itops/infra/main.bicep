targetScope = 'subscription'

@minLength(3)
@maxLength(20)
param environmentName string

@minLength(1)
param resourceGroupName string

@allowed(['centralus'])
param location string

@minLength(36)
@maxLength(36)
param principalId string

@minLength(1)
param principalName string

@description('Luna quota allocation in thousands of tokens/minute. Validate regional quota first; monetary and request ledgers remain independent.')
@minValue(1)
param lunaCapacity int = 300

var tags = {
  'azd-env-name': environmentName
  workload: 'agentic-itops'
  deploymentStage: 'foundation'
  operationalUse: 'prohibited'
}

resource rg 'Microsoft.Resources/resourceGroups@2025-04-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

module foundation './modules/foundation.bicep' = {
  name: 'itops-foundation'
  scope: rg
  params: {
    name: environmentName
    location: location
    tags: tags
    principalId: principalId
    principalName: principalName
    lunaCapacity: lunaCapacity
  }
}

output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_AI_ACCOUNT_NAME string = foundation.outputs.aiAccountName
output AZURE_AI_PROJECT_NAME string = foundation.outputs.aiProjectName
output AZURE_AI_PROJECT_ID string = foundation.outputs.aiProjectId
output AZURE_AI_PROJECT_ENDPOINT string = foundation.outputs.aiProjectEndpoint
output AZURE_OPENAI_ENDPOINT string = foundation.outputs.openaiEndpoint
output AZURE_AI_MODEL_DEPLOYMENT string = 'gpt-5-6-luna'
output AZURE_AI_MODEL_CAPACITY int = lunaCapacity
output AZURE_AI_EMBEDDING_DEPLOYMENT string = 'text-embedding-3-small'
output AZURE_SEARCH_NAME string = foundation.outputs.searchName
output AZURE_STORAGE_ACCOUNT_NAME string = foundation.outputs.storageName
output AZURE_EVIDENCE_CONTAINER string = 'evidence'
output ITOPS_SQL_SERVER string = foundation.outputs.sqlServer
output ITOPS_SQL_DATABASE string = 'itops'
output AZURE_LOG_ANALYTICS_WORKSPACE_ID string = foundation.outputs.workspaceId
output APPLICATIONINSIGHTS_RESOURCE_ID string = foundation.outputs.appInsightsId
