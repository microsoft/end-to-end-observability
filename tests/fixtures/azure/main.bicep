// Test fixture for the discovery tool.
//
// Deploys a deterministic set of resources into a single resource group.
// Some are intentionally NOT instrumented (to trigger rules); a few are
// fully instrumented (negative controls).
//
// Cost: ~USD $1-3/day if left running. Tear down with Remove-TestFixture.ps1.

targetScope = 'resourceGroup'

@description('Short suffix appended to resource names for uniqueness.')
param nameSuffix string = uniqueString(resourceGroup().id)

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Administrator login for the test SQL server.')
param sqlAdminLogin string = 'obsadmin'

@description('Administrator password for the test SQL server.')
@secure()
param sqlAdminPassword string

@description('Object ID of the principal that should have Key Vault admin rights for tests.')
param keyVaultAdminObjectId string

var tagsBase = {
  purpose: 'obs-accelerator-test'
  managedBy: 'discovery-test-fixture'
}

// ---------- Shared sinks ----------

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'law-obs-${nameSuffix}'
  location: location
  tags: tagsBase
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

// ---------- App Service: BAD (no diag, no owner tag) -> APPSVC-001 + TAG-001

resource planBad 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: 'plan-bad-${nameSuffix}'
  location: location
  tags: tagsBase
  sku: {
    name: 'F1'
    tier: 'Free'
  }
  properties: {
    reserved: false
  }
}

resource appBad 'Microsoft.Web/sites@2023-12-01' = {
  name: 'app-bad-${nameSuffix}'
  location: location
  tags: tagsBase // intentionally no `owner`
  properties: {
    serverFarmId: planBad.id
    httpsOnly: true
  }
}

// ---------- App Service: GOOD (diag + owner tag) -> negative control

resource planGood 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: 'plan-good-${nameSuffix}'
  location: location
  tags: tagsBase
  sku: {
    name: 'F1'
    tier: 'Free'
  }
  properties: {
    reserved: false
  }
}

resource appGood 'Microsoft.Web/sites@2023-12-01' = {
  name: 'app-good-${nameSuffix}'
  location: location
  tags: union(tagsBase, {
    owner: 'team-observability'
  })
  properties: {
    serverFarmId: planGood.id
    httpsOnly: true
  }
}

resource appGoodDiag 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: appGood
  name: 'to-law'
  properties: {
    workspaceId: law.id
    logs: [
      {
        categoryGroup: 'allLogs'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}

// ---------- AKS: BAD (no diag) -> AKS-001

resource aksBad 'Microsoft.ContainerService/managedClusters@2024-05-01' = {
  name: 'aks-bad-${nameSuffix}'
  location: location
  tags: tagsBase
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    dnsPrefix: 'aksbad${nameSuffix}'
    agentPoolProfiles: [
      {
        name: 'sys'
        count: 1
        vmSize: 'Standard_B2s'
        mode: 'System'
        osType: 'Linux'
      }
    ]
  }
}

// ---------- SQL: BAD (no diag) -> SQL-001

resource sqlServer 'Microsoft.Sql/servers@2023-08-01-preview' = {
  name: 'sql-bad-${nameSuffix}'
  location: location
  tags: tagsBase
  properties: {
    administratorLogin: sqlAdminLogin
    administratorLoginPassword: sqlAdminPassword
    minimalTlsVersion: '1.2'
    publicNetworkAccess: 'Disabled'
  }
}

resource sqlDb 'Microsoft.Sql/servers/databases@2023-08-01-preview' = {
  parent: sqlServer
  name: 'db-bad'
  location: location
  tags: tagsBase
  sku: {
    name: 'Basic'
    tier: 'Basic'
    capacity: 5
  }
}

// ---------- Storage: BAD (no diag) -> STG-001

resource stgBad 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'stgbad${nameSuffix}'
  location: location
  tags: tagsBase
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

// ---------- Key Vault: BAD (no diag) -> KV-001

resource kvBad 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: 'kv-bad-${take(nameSuffix, 18)}'
  location: location
  tags: tagsBase
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    accessPolicies: []
  }
}

// ---------- Key Vault: PARTIAL (diag set, but no LAW sink) -> KV-002

resource kvPartial 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: 'kv-prt-${take(nameSuffix, 18)}'
  location: location
  tags: tagsBase
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    accessPolicies: []
  }
}

// Storage account used purely as the diag-setting sink for kvPartial,
// which lets us trigger KV-002 (diag exists, no workspaceLink).
resource stgSink 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'stgsink${nameSuffix}'
  location: location
  tags: tagsBase
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource stgSinkDiag 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: stgSink
  name: 'to-law'
  properties: {
    workspaceId: law.id
    metrics: [
      {
        category: 'Transaction'
        enabled: true
      }
    ]
  }
}

resource kvPartialDiag 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: kvPartial
  name: 'to-storage-only'
  properties: {
    storageAccountId: stgSink.id
    logs: [
      {
        categoryGroup: 'audit'
        enabled: true
      }
    ]
  }
}

// ---------- Outputs (consumed by the integration test) ----------

output expectedFindings array = [
  {
    resourceName: appBad.name
    resourceType: 'microsoft.web/sites'
    ruleIds: [
      'APPSVC-001'
      'TAG-001'
    ]
  }
  {
    resourceName: appGood.name
    resourceType: 'microsoft.web/sites'
    ruleIds: []
  }
  {
    resourceName: aksBad.name
    resourceType: 'microsoft.containerservice/managedclusters'
    ruleIds: [
      'AKS-001'
    ]
  }
  {
    resourceName: sqlDb.name
    resourceType: 'microsoft.sql/servers/databases'
    ruleIds: [
      'SQL-001'
    ]
  }
  {
    resourceName: stgBad.name
    resourceType: 'microsoft.storage/storageaccounts'
    ruleIds: [
      'STG-001'
    ]
  }
  {
    resourceName: kvBad.name
    resourceType: 'microsoft.keyvault/vaults'
    ruleIds: [
      'KV-001'
    ]
  }
  {
    resourceName: kvPartial.name
    resourceType: 'microsoft.keyvault/vaults'
    ruleIds: [
      'KV-002'
    ]
  }
]

output resourceGroupName string = resourceGroup().name
output keyVaultAdminObjectIdEcho string = keyVaultAdminObjectId
