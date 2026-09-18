targetScope = 'subscription'

@minLength(3)
@maxLength(20)
param environmentName string
@minLength(1)
param resourceGroupName string
@allowed(['centralus'])
param location string
@allowed(['infrastructure', 'bootstrap', 'preflight', 'release', 'runtime'])
param phase string = 'infrastructure'
@description('Separate approval gate. Bootstrap code additionally requires a hash-bound authorization manifest.')
param bootstrapAuthorized bool = false
@description('Existing Entra application client IDs, not managed identity IDs.')
param reviewApplicationClientId string
param serviceApplicationClientId string
@description('One explicitly approved embedding monthly scope for worker and publisher. Empty keeps model factories fail-closed; supply before runtime activation.')
param embeddingBudgetScope string = ''
@description('Explicit authorized service caller object IDs, including the isolated bootstrap identity for acceptance if approved.')
param serviceCallerObjectIds array = []
@description('Non-secret application configuration; protected identity, network and authentication settings cannot be overridden.')
param workerSettings object = {}
param toolsSettings object = {}
param publisherSettings object = {}

resource rg 'Microsoft.Resources/resourceGroups@2025-04-01' existing = {
  name: resourceGroupName
}

module services './modules/services.bicep' = {
  name: 'itops-services'
  scope: rg
  params: {
    name: environmentName
    location: location
    tags: {
      'azd-env-name': environmentName
      workload: 'agentic-itops'
      deploymentStage: 'services'
      operationalUse: 'prohibited'
    }
    phase: phase
    bootstrapAuthorized: bootstrapAuthorized
    reviewApplicationClientId: reviewApplicationClientId
    serviceApplicationClientId: serviceApplicationClientId
    embeddingBudgetScope: embeddingBudgetScope
    serviceCallerObjectIds: serviceCallerObjectIds
    workerSettings: workerSettings
    toolsSettings: toolsSettings
    publisherSettings: publisherSettings
  }
}

output AZURE_RESOURCE_GROUP string = rg.name
output SERVICE_REVIEW_NAME string = services.outputs.reviewName
output SERVICE_WORKER_NAME string = services.outputs.functionNames[0]
output SERVICE_TOOLS_NAME string = services.outputs.functionNames[1]
output SERVICE_PUBLISHER_NAME string = services.outputs.functionNames[2]
output SERVICE_BOOTSTRAP_NAME string = services.outputs.bootstrapName
output AZURE_KEY_VAULT_NAME string = services.outputs.vaultName
output ITOPS_SERVICE_IDENTITIES array = services.outputs.identities
output ITOPS_REVIEW_URL string = 'https://${services.outputs.hostnames.review}'
output ITOPS_SERVICE_HOSTNAMES object = services.outputs.hostnames
output ITOPS_PRIVATE_HOSTNAMES array = services.outputs.privateHostnames
