// Composed from Azure-Samples/functions-quickstart-python-http-azd v1.0.0.
targetScope = 'resourceGroup'
param name string
param location string
param tags object
param serviceName string
param identityId string
param identityClientId string
param storageName string
param subnetId string
param insightsName string
param applicationClientId string
param callerObjectIds array
param settings object
param enabled bool

resource storage 'Microsoft.Storage/storageAccounts@2025-01-01' existing = {
  name: storageName
}
resource insights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: insightsName
}
module plan 'br/public:avm/res/web/serverfarm:0.1.1' = {
  name: '${serviceName}-plan'
  params: {
    name: 'plan-${name}'
    location: location
    tags: tags
    enableTelemetry: false
    reserved: true
    sku: { name: 'FC1', tier: 'FlexConsumption' }
  }
}
module app 'br/public:avm/res/web/site:0.15.1' = {
  name: '${serviceName}-site'
  params: {
    name: name
    location: location
    tags: union(tags, { 'azd-service-name': serviceName })
    enableTelemetry: false
    kind: 'functionapp,linux'
    enabled: enabled
    httpsOnly: true
    publicNetworkAccess: 'Disabled'
    serverFarmResourceId: plan.outputs.resourceId
    managedIdentities: { userAssignedResourceIds: [identityId] }
    keyVaultAccessIdentityResourceId: identityId
    virtualNetworkSubnetId: subnetId
    basicPublishingCredentialsPolicies: [
      { name: 'scm', allow: false }
      { name: 'ftp', allow: false }
    ]
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storage.properties.primaryEndpoints.blob}deploymentpackage'
          authentication: {
            type: 'UserAssignedIdentity'
            userAssignedIdentityResourceId: identityId
          }
        }
      }
      runtime: { name: 'python', version: '3.13' }
      scaleAndConcurrency: {
        instanceMemoryMB: 2048
        maximumInstanceCount: 40
        alwaysReady: []
        triggers: { http: { perInstanceConcurrency: 1 } }
      }
    }
    siteConfig: {
      minTlsVersion: '1.2'
      scmMinTlsVersion: '1.2'
      ftpsState: 'Disabled'
    }
    appSettingsKeyValuePairs: union(settings, {
      AZURE_CLIENT_ID: identityClientId
      ENTRA_TENANT_ID: subscription().tenantId
      ENTRA_AUDIENCE: applicationClientId
      AzureWebJobsStorage__blobServiceUri: storage.properties.primaryEndpoints.blob
      AzureWebJobsStorage__tableServiceUri: storage.properties.primaryEndpoints.table
      AzureWebJobsStorage__credential: 'managedidentity'
      AzureWebJobsStorage__clientId: identityClientId
      APPLICATIONINSIGHTS_CONNECTION_STRING: insights.properties.ConnectionString
      APPLICATIONINSIGHTS_AUTHENTICATION_STRING: 'ClientId=${identityClientId};Authorization=AAD'
    })
    authSettingV2Configuration: {
      platform: { enabled: true }
      globalValidation: { requireAuthentication: true, unauthenticatedClientAction: 'Return401' }
      httpSettings: { requireHttps: true }
      identityProviders: {
        azureActiveDirectory: {
          enabled: true
          registration: {
            clientId: applicationClientId
            openIdIssuer: 'https://login.microsoftonline.com/${subscription().tenantId}/v2.0'
          }
          validation: {
            allowedAudiences: [applicationClientId]
            defaultAuthorizationPolicy: {
              allowedPrincipals: { identities: callerObjectIds }
            }
          }
        }
      }
      login: { tokenStore: { enabled: false } }
    }
  }
}
output resourceId string = app.outputs.resourceId
output defaultHostname string = app.outputs.defaultHostname
