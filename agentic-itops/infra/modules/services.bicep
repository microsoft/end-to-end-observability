targetScope = 'resourceGroup'
param name string
param location string
param tags object
@allowed(['infrastructure', 'bootstrap', 'preflight', 'release', 'runtime'])
param phase string
param bootstrapAuthorized bool
param reviewApplicationClientId string
param serviceApplicationClientId string
param embeddingBudgetScope string
param serviceCallerObjectIds array
param workerSettings object
param toolsSettings object
param publisherSettings object

var token = uniqueString(subscription().id, resourceGroup().id, name)
var functions = ['worker', 'tools', 'publisher']
var invocationRoles = ['Worker.Invoke', 'Tools.Invoke', 'Publisher.Invoke']
var principals = ['review', 'worker', 'tools', 'publisher', 'bootstrap']
var serviceNames = [for service in functions: 'func-${service}-${token}']
var hostNames = [for (service, i) in functions: 'sthost${i}${token}']
var webName = 'app-review-${token}'
var bootName = 'app-bootstrap-${token}'
var keyVaultName = 'kv-itops-${token}'
var zones = [
  'privatelink.database.windows.net'
  'privatelink.blob.core.windows.net'
  'privatelink.table.core.windows.net'
  'privatelink.vaultcore.azure.net'
  'privatelink.azurewebsites.net'
]
var sqlName = 'sql-${name}-${token}'
var evidenceName = 'stitops${token}'
var insightsName = 'appi-${name}-${token}'
var aiName = 'ai-${name}-${token}'
var searchName = 'srch-${name}-${token}'
var bootstrapActive = (phase == 'bootstrap' || phase == 'preflight' || phase == 'release') && bootstrapAuthorized

resource insights 'Microsoft.Insights/components@2020-02-02' existing = { name: insightsName }
resource sql 'Microsoft.Sql/servers@2023-08-01' existing = { name: sqlName }
resource evidence 'Microsoft.Storage/storageAccounts@2025-01-01' existing = { name: evidenceName }
resource evidenceContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2025-01-01' existing = {
  name: '${evidenceName}/default/evidence'
}
resource search 'Microsoft.Search/searchServices@2025-05-01' existing = { name: searchName }
resource ai 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = { name: aiName }
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = {
  parent: ai
  name: '${name}-project'
}

module identities 'br/public:avm/res/managed-identity/user-assigned-identity:0.4.1' = [for service in principals: {
  name: 'identity-${service}'
  params: {
    name: 'id-${service}-${token}'
    location: location
    tags: tags
    enableTelemetry: false
  }
}]

module network 'br/public:avm/res/network/virtual-network:0.7.2' = {
  name: 'service-network'
  params: {
    name: 'vnet-itops-${token}'
    location: location
    tags: tags
    enableTelemetry: false
    addressPrefixes: ['10.93.0.0/16']
    subnets: [
      {
        name: 'web-integration'
        addressPrefix: '10.93.0.0/26'
        delegation: 'Microsoft.Web/serverFarms'
      }
      {
        name: 'flex-integration'
        addressPrefix: '10.93.1.0/24'
        delegation: 'Microsoft.App/environments'
      }
      {
        name: 'private-endpoints'
        addressPrefix: '10.93.2.0/26'
        privateEndpointNetworkPolicies: 'Disabled'
      }
    ]
  }
}
module dns 'br/public:avm/res/network/private-dns-zone:0.7.1' = [for zone in zones: {
  name: 'dns-${uniqueString(zone)}'
  params: {
    name: zone
    location: 'global'
    tags: tags
    enableTelemetry: false
    virtualNetworkLinks: [{
      name: 'itops-vnet'
      virtualNetworkResourceId: network.outputs.resourceId
      registrationEnabled: false
    }]
  }
}]

module hostStorage 'br/public:avm/res/storage/storage-account:0.33.0' = [for (service, i) in functions: {
  name: 'host-storage-${service}'
  params: {
    name: hostNames[i]
    location: location
    tags: tags
    enableTelemetry: false
    skuName: 'Standard_LRS'
    kind: 'StorageV2'
    accessTier: 'Hot'
    publicNetworkAccess: 'Disabled'
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    networkAcls: { defaultAction: 'Deny', bypass: 'None', ipRules: [], virtualNetworkRules: [] }
    blobServices: { containers: [{ name: 'deploymentpackage', publicAccess: 'None' }] }
    tableServices: {}
    fileServices: {}
    queueServices: {}
    roleAssignments: [
      {
        principalId: identities[i + 1].outputs.principalId
        principalType: 'ServicePrincipal'
        roleDefinitionIdOrName: 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
      }
      {
        principalId: identities[i + 1].outputs.principalId
        principalType: 'ServicePrincipal'
        roleDefinitionIdOrName: '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'
      }
    ]
  }
}]
module vault 'br/public:avm/res/key-vault/vault:0.13.1' = {
  name: 'service-vault'
  params: {
    name: keyVaultName
    location: location
    tags: tags
    enableTelemetry: false
    sku: 'standard'
    publicNetworkAccess: 'Disabled'
    enableRbacAuthorization: true
    enablePurgeProtection: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    enableVaultForDeployment: false
    enableVaultForTemplateDeployment: false
    enableVaultForDiskEncryption: false
    networkAcls: { defaultAction: 'Deny', bypass: 'None', ipRules: [], virtualNetworkRules: [] }
    roleAssignments: concat([
      {
        principalId: identities[0].outputs.principalId
        principalType: 'ServicePrincipal'
        roleDefinitionIdOrName: '4633458b-17de-408a-b874-0445c86b69e6'
      }
    ], bootstrapAuthorized ? [{
      principalId: identities[4].outputs.principalId
      principalType: 'ServicePrincipal'
      roleDefinitionIdOrName: 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'
    }] : [])
  }
}

var hostTargets = [for (service, i) in functions: [
  { name: '${service}-blob', resourceId: resourceId('Microsoft.Storage/storageAccounts', hostNames[i]), group: 'blob', zone: 1 }
  { name: '${service}-table', resourceId: resourceId('Microsoft.Storage/storageAccounts', hostNames[i]), group: 'table', zone: 2 }
]]
var dataTargets = concat([
  { name: 'sql', resourceId: sql.id, group: 'sqlServer', zone: 0 }
  { name: 'evidence', resourceId: evidence.id, group: 'blob', zone: 1 }
  { name: 'vault', resourceId: resourceId('Microsoft.KeyVault/vaults', keyVaultName), group: 'vault', zone: 3 }
], flatten(hostTargets))
module dataEndpoints './service-private-endpoint.bicep' = [for endpoint in dataTargets: {
  name: 'pe-${endpoint.name}'
  params: {
    name: 'pe-${endpoint.name}-${token}'
    location: location
    tags: tags
    subnetId: network.outputs.subnetResourceIds[2]
    targetId: endpoint.resourceId
    groupId: endpoint.group
    dnsZoneId: dns[endpoint.zone].outputs.resourceId
  }
  dependsOn: [hostStorage, vault]
}]

module webPlan 'br/public:avm/res/web/serverfarm:0.1.1' = {
  name: 'review-plan'
  params: {
    name: 'plan-web-${token}'
    location: location
    tags: tags
    enableTelemetry: false
    reserved: true
    sku: { name: 'B1', tier: 'Basic', capacity: 1 }
  }
}
var noBasicAuth = [{ name: 'scm', allow: false }, { name: 'ftp', allow: false }]
module review 'br/public:avm/res/web/site:0.15.1' = {
  name: 'review-site'
  params: {
    name: webName
    location: location
    tags: union(tags, { 'azd-service-name': 'review' })
    enableTelemetry: false
    kind: 'app,linux'
    enabled: phase == 'runtime'
    serverFarmResourceId: webPlan.outputs.resourceId
    managedIdentities: { userAssignedResourceIds: [identities[0].outputs.resourceId] }
    keyVaultAccessIdentityResourceId: identities[0].outputs.resourceId
    virtualNetworkSubnetId: network.outputs.subnetResourceIds[0]
    vnetRouteAllEnabled: true
    publicNetworkAccess: 'Enabled'
    httpsOnly: true
    clientAffinityEnabled: false
    basicPublishingCredentialsPolicies: noBasicAuth
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.13'
      alwaysOn: true
      minTlsVersion: '1.2'
      scmMinTlsVersion: '1.2'
      ftpsState: 'Disabled'
      appCommandLine: 'gunicorn --bind=0.0.0.0:8000 --workers=1 --threads=4 --timeout=120 "agentic_itops.service_projection:create_review_service()"'
    }
    appSettingsKeyValuePairs: {
      SCM_DO_BUILD_DURING_DEPLOYMENT: 'true'
      WEBSITES_CONTAINER_START_TIME_LIMIT: '600'
      AZURE_CLIENT_ID: identities[0].outputs.clientId
      ITOPS_SQL_SERVER: '${sqlName}.database.windows.net'
      ITOPS_SQL_DATABASE: 'itops'
      ENTRA_TENANT_ID: subscription().tenantId
      ENTRA_AUDIENCE: reviewApplicationClientId
      ITOPS_PUBLIC_ORIGIN: 'https://${webName}.azurewebsites.net'
      ITOPS_ALLOW_EASYAUTH: 'true'
      ITOPS_WORKER_ORIGIN: 'https://${serviceNames[0]}.azurewebsites.net'
      ITOPS_WORKER_TOKEN_SCOPE: 'api://${serviceApplicationClientId}/.default'
      ITOPS_PUBLISHER_ORIGIN: 'https://${serviceNames[2]}.azurewebsites.net'
      ITOPS_PUBLISHER_TOKEN_SCOPE: 'api://${serviceApplicationClientId}/.default'
      ITOPS_EVIDENCE_BLOB_ENDPOINT: 'https://${evidenceName}.blob.core.windows.net'
      ITOPS_EVIDENCE_CONTAINER: 'evidence'
      ITOPS_PROJECTION_AUDIENCE: serviceApplicationClientId
      ITOPS_PROJECTION_CALLERS_JSON: string([{
        oid: identities[1].outputs.principalId
        client_id: identities[1].outputs.clientId
        roles: ['Results.Project']
      }])
      ITOPS_REVIEW_SECRET_KEY: '@Microsoft.KeyVault(VaultName=${keyVaultName};SecretName=review-session-key)'
      ITOPS_REVIEW_SCOPES_JSON: '@Microsoft.KeyVault(VaultName=${keyVaultName};SecretName=review-scopes)'
      MICROSOFT_PROVIDER_AUTHENTICATION_SECRET: '@Microsoft.KeyVault(VaultName=${keyVaultName};SecretName=review-oidc-secret)'
      APPLICATIONINSIGHTS_CONNECTION_STRING: insights.properties.ConnectionString
      APPLICATIONINSIGHTS_AUTHENTICATION_STRING: 'ClientId=${identities[0].outputs.clientId};Authorization=AAD'
    }
    authSettingV2Configuration: {
      platform: { enabled: true }
      globalValidation: {
        requireAuthentication: true
        unauthenticatedClientAction: 'RedirectToLoginPage'
        redirectToProvider: 'azureactivedirectory'
      }
      httpSettings: { requireHttps: true }
      identityProviders: {
        azureActiveDirectory: {
          enabled: true
          registration: {
            clientId: reviewApplicationClientId
            clientSecretSettingName: 'MICROSOFT_PROVIDER_AUTHENTICATION_SECRET'
            openIdIssuer: 'https://login.microsoftonline.com/${subscription().tenantId}/v2.0'
          }
          validation: {
            defaultAuthorizationPolicy: {
              allowedApplications: [
                reviewApplicationClientId
                identities[1].outputs.clientId
              ]
            }
            allowedAudiences: [
              reviewApplicationClientId
              serviceApplicationClientId
            ]
          }
          login: { loginParameters: ['scope=openid profile email'] }
        }
      }
      login: { tokenStore: { enabled: true } }
    }
  }
}

module bootstrap 'br/public:avm/res/web/site:0.15.1' = {
  name: 'bootstrap-site'
  params: {
    name: bootName
    location: location
    tags: union(tags, { 'azd-service-name': 'bootstrap', temporary: 'explicit-retirement-required' })
    enableTelemetry: false
    kind: 'app,linux'
    enabled: bootstrapActive
    serverFarmResourceId: webPlan.outputs.resourceId
    managedIdentities: { userAssignedResourceIds: [identities[4].outputs.resourceId] }
    virtualNetworkSubnetId: network.outputs.subnetResourceIds[0]
    vnetRouteAllEnabled: true
    publicNetworkAccess: 'Enabled'
    httpsOnly: true
    basicPublishingCredentialsPolicies: noBasicAuth
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.13'
      alwaysOn: true
      minTlsVersion: '1.2'
      scmMinTlsVersion: '1.2'
      ftpsState: 'Disabled'
      ipSecurityRestrictionsDefaultAction: 'Deny'
      ipSecurityRestrictions: []
      scmIpSecurityRestrictionsUseMain: false
      scmIpSecurityRestrictionsDefaultAction: 'Allow'
      appCommandLine: 'python -m deployment.bootstrap'
    }
    appSettingsKeyValuePairs: {
      SCM_DO_BUILD_DURING_DEPLOYMENT: 'true'
      AZURE_CLIENT_ID: identities[4].outputs.clientId
      ITOPS_SQL_SERVER: '${sqlName}.database.windows.net'
      ITOPS_SQL_DATABASE: 'itops'
      ITOPS_KEY_VAULT_URL: 'https://${keyVaultName}.vault.azure.net'
      ITOPS_SEARCH_ENDPOINT: 'https://${searchName}.search.windows.net'
      WEBSITES_ENABLE_APP_SERVICE_STORAGE: 'true'
      ITOPS_BOOTSTRAP_AUTHORIZED: bootstrapActive ? 'true' : 'false'
    }
  }
}

var settings = [workerSettings, toolsSettings, publisherSettings]
@batchSize(1)
module functionApps './service-function.bicep' = [for (service, i) in functions: {
  name: '${service}-function'
  params: {
    name: serviceNames[i]
    location: location
    tags: tags
    serviceName: service
    identityId: identities[i + 1].outputs.resourceId
    identityClientId: identities[i + 1].outputs.clientId
    storageName: hostStorage[i].outputs.name
    subnetId: network.outputs.subnetResourceIds[1]
    insightsName: insightsName
    applicationClientId: serviceApplicationClientId
    callerObjectIds: phase == 'release' || (phase == 'preflight' && service == 'worker')
      ? [identities[4].outputs.principalId]
      : (service == 'tools' ? union(serviceCallerObjectIds, [identities[1].outputs.principalId]) : serviceCallerObjectIds)
    enabled: phase == 'runtime' || phase == 'release' || (phase == 'preflight' && service == 'worker')
    settings: union(settings[i], {
      ITOPS_SQL_SERVER: '${sqlName}.database.windows.net'
      ITOPS_SQL_DATABASE: 'itops'
      AZURE_STORAGE_ACCOUNT_NAME: evidenceName
      AZURE_EVIDENCE_CONTAINER: 'evidence'
      ITOPS_EVIDENCE_BLOB_ENDPOINT: 'https://${evidenceName}.blob.core.windows.net'
      ITOPS_EVIDENCE_CONTAINER: 'evidence'
      ITOPS_PROJECT_ENDPOINT: 'https://${aiName}.services.ai.azure.com/api/projects/${name}-project'
      ITOPS_TOOLS_ORIGIN: 'https://${serviceNames[1]}.azurewebsites.net'
      ITOPS_TOOLS_AUDIENCE: serviceApplicationClientId
      ITOPS_TOOLS_TOKEN_SCOPE: 'api://${serviceApplicationClientId}/.default'
      ITOPS_REVIEW_ORIGIN: 'https://${webName}.azurewebsites.net'
      ITOPS_PROJECTION_AUDIENCE: serviceApplicationClientId
      ITOPS_PROJECTION_TOKEN_SCOPE: 'api://${serviceApplicationClientId}/.default'
      ITOPS_RUNTIME_CONFIG_PATH: '.runtime/runtime.json'
      ITOPS_BINDINGS_PATH: '.runtime/bindings.json'
      ITOPS_REGISTRATION_PLAN_PATH: '.runtime/registration-plan.json'
      ITOPS_REGISTRATION_RECEIPT_PATH: '.runtime/registration-receipt.json'
      ITOPS_ACCESS_PROBE_CONTRACT_PATH: '.runtime/access-probe-contract.json'
      ITOPS_PREFLIGHT_BOOTSTRAP_OID: identities[4].outputs.principalId
      AZURE_AI_PROJECT_ENDPOINT: 'https://${aiName}.services.ai.azure.com/api/projects/${name}-project'
      AZURE_OPENAI_ENDPOINT: 'https://${aiName}.openai.azure.com/'
      AZURE_AI_MODEL_DEPLOYMENT: 'gpt-5-6-luna'
      AZURE_AI_EMBEDDING_DEPLOYMENT: 'text-embedding-3-small'
      AZURE_SEARCH_ENDPOINT: 'https://${searchName}.search.windows.net'
      ITOPS_SEARCH_ENDPOINT: 'https://${searchName}.search.windows.net'
      ITOPS_SEARCH_INDEX: 'agentic-itops-knowledge'
      ITOPS_OPENAI_ENDPOINT: 'https://${aiName}.openai.azure.com/'
      ITOPS_EMBEDDING_DEPLOYMENT: 'text-embedding-3-small'
      ITOPS_EMBEDDING_MODEL: 'text-embedding-3-small'
      ITOPS_EMBEDDING_MODEL_VERSION: '1'
      ITOPS_EMBEDDING_DIMENSIONS: '1536'
      ITOPS_EMBEDDING_BUDGET_SCOPE: embeddingBudgetScope
      ITOPS_PUBLISHER_AUDIENCE: serviceApplicationClientId
    }, service == 'publisher' ? {
      ITOPS_PUBLISHER_ENABLED: phase == 'runtime' || phase == 'release' ? 'true' : 'false'
      ITOPS_PUBLISHER_CALLER_OIDS_JSON: string(phase == 'release'
        ? [identities[4].outputs.principalId]
        : serviceCallerObjectIds)
    } : {}, phase == 'release' || (phase == 'preflight' && service == 'worker') ? {
      ITOPS_SERVICE_CALLERS_JSON: string([{
        oid: identities[4].outputs.principalId
        client_id: identities[4].outputs.clientId
        roles: [invocationRoles[i]]
      }])
    } : {})
  }
  dependsOn: [dataEndpoints]
}]
module functionEndpoints './service-private-endpoint.bicep' = [for (service, i) in functions: {
  name: 'pe-${service}-ingress'
  params: {
    name: 'pe-${service}-ingress-${token}'
    location: location
    tags: tags
    subnetId: network.outputs.subnetResourceIds[2]
    targetId: functionApps[i].outputs.resourceId
    groupId: 'sites'
    dnsZoneId: dns[4].outputs.resourceId
  }
}]

resource telemetryRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (service, i) in take(principals, 4): {
  name: guid(insights.id, 'id-${principals[i]}-${token}', 'metrics-publisher')
  scope: insights
  properties: {
    principalId: identities[i].outputs.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '3913510d-42f4-4e42-8a64-420c390055eb')
  }
}]
resource foundryWorker 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(project.id, 'id-worker-${token}', 'foundry-user')
  scope: project
  properties: {
    principalId: identities[1].outputs.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
  }
}
resource inferenceRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for i in [1, 3]: {
  name: guid(ai.id, 'id-${principals[i]}-${token}', 'openai-user')
  scope: ai
  properties: {
    principalId: identities[i].outputs.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
  }
}]
resource searchReadRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for i in [1]: {
  name: guid(search.id, 'id-${principals[i]}-${token}', 'search-reader')
  scope: search
  properties: {
    principalId: identities[i].outputs.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '1407120a-92aa-4202-b7e9-c0e197c71c8f')
  }
}]
resource searchPublisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, 'id-publisher-${token}', 'search-writer')
  scope: search
  properties: {
    principalId: identities[3].outputs.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8ebe5a00-799e-43f5-93ac-243d3dce84a7')
  }
}
resource searchBootstrap 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (bootstrapAuthorized) {
  name: guid(search.id, 'id-bootstrap-${token}', 'search-schema')
  scope: search
  properties: {
    principalId: identities[4].outputs.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7ca78c08-252a-4471-8644-bb5ff32d4ba0')
  }
}
resource evidenceWriterDefinition 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(resourceGroup().id, 'itops-evidence-read-write-no-delete')
  properties: {
    roleName: 'ITOps evidence read-write ${token}'
    description: 'Evidence read/write only. Conditional create enforced by application; no delete permission.'
    type: 'CustomRole'
    assignableScopes: [resourceGroup().id]
    permissions: [{
      actions: []
      notActions: []
      dataActions: [
        'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read'
        'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/write'
      ]
      notDataActions: []
    }]
  }
}
resource evidenceWorker 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(evidenceContainer.id, 'id-worker-${token}', evidenceWriterDefinition.id)
  scope: evidenceContainer
  properties: {
    principalId: identities[1].outputs.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: evidenceWriterDefinition.id
  }
}
resource evidenceReaders 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for i in [0, 2]: {
  name: guid(evidenceContainer.id, 'id-${principals[i]}-${token}', 'blob-reader')
  scope: evidenceContainer
  properties: {
    principalId: identities[i].outputs.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1')
  }
}]
resource publishDefinition 'Microsoft.Authorization/roleDefinitions@2022-04-01' = if (bootstrapAuthorized) {
  name: guid(resourceGroup().id, 'itops-bootstrap-publish')
  properties: {
    roleName: 'ITOps bootstrap publish ${token}'
    description: 'Entra SCM code deployment only; no site settings, identities, keys, or role assignments.'
    type: 'CustomRole'
    assignableScopes: [resourceGroup().id]
    permissions: [{
      actions: ['Microsoft.Web/sites/read', 'Microsoft.Web/sites/publish/Action']
      notActions: []
      dataActions: []
      notDataActions: []
    }]
  }
}
module publishRoles './service-publish-role.bicep' = [for (service, i) in functions: if (bootstrapAuthorized) {
  name: 'bootstrap-publish-${service}'
  params: {
    name: serviceNames[i]
    principalId: identities[4].outputs.principalId
    roleDefinitionId: publishDefinition!.id
  }
  dependsOn: [functionApps]
}]

output reviewName string = webName
output bootstrapName string = bootName
output functionNames array = serviceNames
output hostnames object = {
  review: review.outputs.defaultHostname
  bootstrap: bootstrap.outputs.defaultHostname
  worker: functionApps[0].outputs.defaultHostname
  tools: functionApps[1].outputs.defaultHostname
  publisher: functionApps[2].outputs.defaultHostname
}
output vaultName string = keyVaultName
output identities array = [for (service, i) in principals: {
  service: service
  name: identities[i].outputs.name
  resourceId: identities[i].outputs.resourceId
  clientId: identities[i].outputs.clientId
  principalId: identities[i].outputs.principalId
}]
var hostPrivateNames = [for host in hostNames: ['${host}.blob.core.windows.net', '${host}.table.core.windows.net']]
var functionPrivateNames = [for site in serviceNames: ['${site}.azurewebsites.net', '${site}.scm.azurewebsites.net']]
output privateHostnames array = concat([
  '${sqlName}.database.windows.net'
  '${evidenceName}.blob.core.windows.net'
  '${keyVaultName}.vault.azure.net'
], flatten(hostPrivateNames), flatten(functionPrivateNames))
