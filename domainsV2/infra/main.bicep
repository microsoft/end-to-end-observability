targetScope = 'resourceGroup'

param location string = resourceGroup().location
@minLength(3)
@maxLength(26)
param prefix string = 'iot-domains-v2-e0q0j7'
param storageName string = 'iotdomainsv2e0q0j7sa'
@description('Opt in to public, identity-only storage without any private networking.')
param publicStorage bool = false
@description('Use the independently deployed, same-group <prefix>-plan without redeploying it.')
param useExistingPlan bool = false
@description('Deploy data/network resources only; defer the application and its identity assignments to a separately validated stage.')
param deferApplication bool = false
@description('Empty retains live resource references. Otherwise all four endpoint fields must come from guarded existing-resource ARM readback.')
param resolvedRuntimeEndpoints object = {}
@description('Use an enforced subscription-identity perimeter; underlying storage stays closed.')
param securityPerimeter bool = false
@description('Explicit risk acceptance: anonymous portal and demo controls without IP restrictions.')
param unrestrictedPortal bool = false
@secure()
@description('Required when unrestrictedPortal is false; an empty restricted IP rule is invalid.')
param approvedIpCidr string = ''
param smokeToken string = ''

var privateNetworking = !publicStorage && !securityPerimeter
var tags = {
  application: 'energy-domains-v2'
  environment: 'synthetic-poc'
  'support-owner': 'poc-operator'
}
var observationsColumns = [
  { name: 'TimeGenerated', type: 'datetime' }
  { name: 'ObservedAt', type: 'datetime' }
  { name: 'SimulatedAt', type: 'datetime' }
  { name: 'SchemaVersion', type: 'string' }
  { name: 'EventId', type: 'string' }
  { name: 'RunId', type: 'string' }
  { name: 'Domain', type: 'string' }
  { name: 'Surface', type: 'string' }
  { name: 'Portal', type: 'string' }
  { name: 'RecordType', type: 'string' }
  { name: 'EntityId', type: 'string' }
  { name: 'LocationId', type: 'string' }
  { name: 'Payload', type: 'dynamic' }
  { name: 'Context', type: 'dynamic' }
  { name: 'IsSynthetic', type: 'boolean' }
  { name: 'OperationalUse', type: 'string' }
]
var auditColumns = [
  { name: 'TimeGenerated', type: 'datetime' }
  { name: 'SchemaVersion', type: 'string' }
  { name: 'EventId', type: 'string' }
  { name: 'RunId', type: 'string' }
  { name: 'Domain', type: 'string' }
  { name: 'Surface', type: 'string' }
  { name: 'Action', type: 'string' }
  { name: 'Scenario', type: 'string' }
  { name: 'Outcome', type: 'string' }
  { name: 'IsSynthetic', type: 'boolean' }
  { name: 'OperationalUse', type: 'string' }
]

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-law'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: { dailyQuotaGb: 5 }
    features: { enableLogAccessUsingOnlyResourcePermissions: true }
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

resource observationsTable 'Microsoft.OperationalInsights/workspaces/tables@2022-10-01' = {
  parent: workspace
  name: 'EnergyObservations_CL'
  properties: {
    plan: 'Analytics'
    retentionInDays: 30
    totalRetentionInDays: 30
    schema: { name: 'EnergyObservations_CL', columns: observationsColumns }
  }
}

resource auditTable 'Microsoft.OperationalInsights/workspaces/tables@2022-10-01' = {
  parent: workspace
  name: 'EnergyInjectionAudit_CL'
  properties: {
    plan: 'Analytics'
    retentionInDays: 30
    totalRetentionInDays: 30
    schema: { name: 'EnergyInjectionAudit_CL', columns: auditColumns }
  }
}

resource dcr 'Microsoft.Insights/dataCollectionRules@2024-03-11' = {
  name: '${prefix}-dcr'
  location: location
  tags: tags
  kind: 'Direct'
  properties: {
    streamDeclarations: {
      'Custom-EnergyObservations_CL': { columns: observationsColumns }
      'Custom-EnergyInjectionAudit_CL': { columns: auditColumns }
    }
    destinations: {
      logAnalytics: [{ name: 'v2Workspace', workspaceResourceId: workspace.id }]
    }
    dataFlows: [
      {
        streams: ['Custom-EnergyObservations_CL']
        destinations: ['v2Workspace']
        transformKql: 'source'
        outputStream: 'Custom-EnergyObservations_CL'
      }
      {
        streams: ['Custom-EnergyInjectionAudit_CL']
        destinations: ['v2Workspace']
        transformKql: 'source'
        outputStream: 'Custom-EnergyInjectionAudit_CL'
      }
    ]
  }
  dependsOn: [observationsTable, auditTable]
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    // Enforced NSP rules override Disabled; removing the association fails closed.
    publicNetworkAccess: securityPerimeter ? 'Disabled' : (publicStorage ? 'Enabled' : 'Disabled')
    networkAcls: { defaultAction: securityPerimeter ? 'Deny' : (publicStorage ? 'Allow' : 'Deny'), bypass: 'None' }
  }
}
resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    deleteRetentionPolicy: { enabled: true, days: 7 }
    containerDeleteRetentionPolicy: { enabled: true, days: 7 }
  }
}
resource stateContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'v2-state'
  properties: { publicAccess: 'None' }
}

resource perimeter 'Microsoft.Network/networkSecurityPerimeters@2025-01-01' = if (securityPerimeter) {
  name: '${prefix}-nsp'
  location: location
  tags: tags
  properties: {}
}
resource perimeterProfile 'Microsoft.Network/networkSecurityPerimeters/profiles@2025-01-01' = if (securityPerimeter) {
  parent: perimeter
  name: 'subscription-identities'
  properties: {}
}
resource subscriptionInbound 'Microsoft.Network/networkSecurityPerimeters/profiles/accessRules@2025-01-01' = if (securityPerimeter) {
  parent: perimeterProfile
  name: 'target-subscription'
  properties: {
    direction: 'Inbound'
    subscriptions: [{ id: subscription().id }]
  }
}
module storagePerimeter 'perimeter-association.bicep' = if (securityPerimeter) {
  name: '${prefix}-storage-perimeter'
  params: {
    perimeterName: perimeter.name
    associationName: '${prefix}-storage'
    privateLinkResourceId: storage.id
  }
  dependsOn: [subscriptionInbound]
}

resource vnet 'Microsoft.Network/virtualNetworks@2023-11-01' = if (privateNetworking) {
  name: '${prefix}-vnet'
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: ['10.74.0.0/16'] }
    subnets: [
      {
        name: 'app-integration'
        properties: {
          addressPrefix: '10.74.1.0/26'
          delegations: [{ name: 'webapps', properties: { serviceName: 'Microsoft.Web/serverFarms' } }]
        }
      }
      {
        name: 'private-endpoints'
        properties: { addressPrefix: '10.74.2.0/27', privateEndpointNetworkPolicies: 'Disabled' }
      }
    ]
  }
}
resource privateDns 'Microsoft.Network/privateDnsZones@2020-06-01' = if (privateNetworking) {
  name: 'privatelink.blob.core.windows.net'
  location: 'global'
  tags: tags
}
resource dnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = if (privateNetworking) {
  parent: privateDns
  name: 'v2-only'
  location: 'global'
  properties: { registrationEnabled: false, virtualNetwork: { id: vnet.id } }
}
resource endpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = if (privateNetworking) {
  name: '${prefix}-blob-pe'
  location: location
  tags: tags
  properties: {
    subnet: { id: '${vnet.id}/subnets/private-endpoints' }
    privateLinkServiceConnections: [{
      name: 'v2-blob'
      properties: { privateLinkServiceId: storage.id, groupIds: ['blob'] }
    }]
  }
}
resource zoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = if (privateNetworking) {
  parent: endpoint
  name: 'default'
  properties: { privateDnsZoneConfigs: [{ name: 'blob', properties: { privateDnsZoneId: privateDns.id } }] }
}

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = if (!useExistingPlan && !deferApplication) {
  name: '${prefix}-plan'
  location: location
  tags: tags
  kind: 'linux'
  sku: { name: 'B2', tier: 'Basic', capacity: 1 }
  properties: { reserved: true }
}
resource app 'Microsoft.Web/sites@2023-12-01' = if (!deferApplication) {
  name: '${prefix}-app'
  location: location
  tags: tags
  kind: 'app,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: useExistingPlan ? resourceId('Microsoft.Web/serverfarms', '${prefix}-plan') : plan.id
    httpsOnly: true
    publicNetworkAccess: 'Enabled'
    ...(privateNetworking ? {
      virtualNetworkSubnetId: '${vnet.id}/subnets/app-integration'
    } : {})
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.11'
      alwaysOn: true
      appCommandLine: 'gunicorn --bind=0.0.0.0:8000 --workers=1 --threads=8 --timeout=180 app:app'
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      scmMinTlsVersion: '1.2'
      healthCheckPath: '/api/health/ready'
      ipSecurityRestrictionsDefaultAction: unrestrictedPortal ? 'Allow' : 'Deny'
      ipSecurityRestrictions: unrestrictedPortal ? [] : [{
        name: 'approved-review-network'
        action: 'Allow'
        priority: 100
        ipAddress: approvedIpCidr
      }]
      scmIpSecurityRestrictionsUseMain: false
      appSettings: [
        { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT', value: 'true' }
        { name: 'V2_MODE', value: 'azure' }
        { name: 'V2_PUBLIC_ORIGIN', value: 'https://${prefix}-app.azurewebsites.net' }
        { name: 'V2_STORAGE_ACCOUNT_URL', value: !empty(resolvedRuntimeEndpoints) ? resolvedRuntimeEndpoints.storageAccountUrl : storage.properties.primaryEndpoints.blob }
        { name: 'V2_STORAGE_CONTAINER', value: stateContainer.name }
        { name: 'V2_LOGS_ENDPOINT', value: !empty(resolvedRuntimeEndpoints) ? resolvedRuntimeEndpoints.logsEndpoint : dcr.properties.endpoints.logsIngestion }
        { name: 'V2_DCR_IMMUTABLE_ID', value: !empty(resolvedRuntimeEndpoints) ? resolvedRuntimeEndpoints.dcrImmutableId : dcr.properties.immutableId }
        { name: 'V2_LOG_ANALYTICS_WORKSPACE_ID', value: !empty(resolvedRuntimeEndpoints) ? resolvedRuntimeEndpoints.workspaceId : workspace.properties.customerId }
        { name: 'V2_DEPLOY_SMOKE_TOKEN', value: smokeToken }
      ]
    }
  }
  dependsOn: [zoneGroup, dnsLink, storagePerimeter]
}
resource scmPolicy 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2023-12-01' = if (!deferApplication) {
  parent: app
  name: 'scm'
  properties: { allow: false }
}
resource ftpPolicy 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2023-12-01' = if (!deferApplication) {
  parent: app
  name: 'ftp'
  properties: { allow: false }
}
resource storageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!deferApplication) {
  name: guid(storage.id, app.id, 'v2-blob-writer')
  scope: storage
  properties: {
    principalId: deferApplication ? '' : app.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
  }
}
resource dcrRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!deferApplication) {
  name: guid(dcr.id, app.id, 'v2-ingestion')
  scope: dcr
  properties: {
    principalId: deferApplication ? '' : app.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '3913510d-42f4-4e42-8a64-420c390055eb')
  }
}

output appName string = '${prefix}-app'
output appUrl string = deferApplication ? '' : 'https://${app.properties.defaultHostName}'
output workspaceId string = workspace.properties.customerId
output workspaceResourceId string = workspace.id
output dcrImmutableId string = dcr.properties.immutableId
output logsEndpoint string = dcr.properties.endpoints.logsIngestion
output principalId string = deferApplication ? '' : app.identity.principalId
output perimeterName string = securityPerimeter ? perimeter.name : ''
output perimeterProfileId string = securityPerimeter ? perimeterProfile.id : ''
