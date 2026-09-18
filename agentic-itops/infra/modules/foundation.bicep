targetScope = 'resourceGroup'

param name string
param location string
param tags object
param principalId string
param principalName string
param lunaCapacity int

var token = uniqueString(subscription().id, resourceGroup().id, name)
var accountName = 'ai-${name}-${token}'
var projectName = '${name}-project'
var databaseServerName = 'sql-${name}-${token}'
var searchServiceName = 'srch-${name}-${token}'
var evidenceStorageName = 'stitops${token}'
var appInsightsName = 'appi-${name}-${token}'
var foundryUserRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '53ca6127-db72-4b80-b1b0-d745d6d5456d'
)

module workspace 'br/public:avm/res/operational-insights/workspace:0.16.1' = {
  name: 'monitor-workspace'
  params: {
    name: 'law-${name}-${token}'
    location: location
    tags: tags
    enableTelemetry: false
    skuName: 'PerGB2018'
    dataRetention: 30
    dailyQuotaGb: '0.05'
    forceCmkForQuery: false
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
      disableLocalAuth: true
    }
  }
}

module insights 'br/public:avm/res/insights/component:0.8.0' = {
  name: 'monitor-insights'
  params: {
    name: appInsightsName
    location: location
    tags: tags
    enableTelemetry: false
    workspaceResourceId: workspace.outputs.resourceId
    applicationType: 'web'
    kind: 'web'
    disableLocalAuth: true
    disableIpMasking: false
    retentionInDays: 30
  }
}

// Azure creates this default rule; keep pilot notifications explicitly disabled.
resource failureAnomaly 'Microsoft.AlertsManagement/smartDetectorAlertRules@2021-04-01' = {
  name: 'Failure Anomalies - ${appInsightsName}'
  location: 'global'
  properties: {
    description: 'Failure Anomalies notifies you of an unusual rise in the rate of failed HTTP requests or dependency calls.'
    detector: {
      id: 'FailureAnomaliesDetector'
    }
    state: 'Disabled'
    severity: 'Sev3'
    frequency: 'PT1M'
    scope: [insights.outputs.resourceId]
    actionGroups: {
      groupIds: [
        resourceId('Microsoft.Insights/actionGroups', 'Application Insights Smart Detection')
      ]
    }
  }
}

module account 'br/public:avm/res/cognitive-services/account:0.19.0' = {
  name: 'foundry-account'
  params: {
    name: accountName
    location: location
    tags: tags
    enableTelemetry: false
    kind: 'AIServices'
    sku: 'S0'
    allowProjectManagement: true
    customSubDomainName: accountName
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
    restrictOutboundNetworkAccess: true
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'None'
      ipRules: []
      virtualNetworkRules: []
    }
    managedIdentities: {
      systemAssigned: true
    }
    roleAssignments: [
      {
        principalId: principalId
        principalType: 'User'
        roleDefinitionIdOrName: '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
      }
    ]
    deployments: [
      {
        name: 'gpt-5-6-luna'
        model: {
          format: 'OpenAI'
          name: 'gpt-5.6-luna'
          version: '2026-07-09'
        }
        sku: {
          name: 'DataZoneStandard'
          capacity: lunaCapacity
        }
        versionUpgradeOption: 'NoAutoUpgrade'
      }
      {
        name: 'text-embedding-3-small'
        model: {
          format: 'OpenAI'
          name: 'text-embedding-3-small'
          version: '1'
        }
        sku: {
          name: 'DataZoneStandard'
          capacity: 10
        }
        versionUpgradeOption: 'NoAutoUpgrade'
      }
    ]
    diagnosticSettings: [
      {
        name: 'foundation-metrics'
        workspaceResourceId: workspace.outputs.resourceId
        metricCategories: [{ category: 'AllMetrics' }]
      }
    ]
  }
}

resource accountReference 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: accountName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: accountReference
  name: projectName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    displayName: 'Agentic ITOps foundation'
    description: 'Synthetic, non-operational pilot. Application activation remains gated.'
  }
  dependsOn: [account]
}

resource projectOperator 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(project.id, principalId, foundryUserRoleId)
  scope: project
  properties: {
    principalId: principalId
    principalType: 'User'
    roleDefinitionId: foundryUserRoleId
  }
}

module search 'br/public:avm/res/search/search-service:0.13.0' = {
  name: 'knowledge-search'
  params: {
    name: searchServiceName
    location: location
    tags: tags
    enableTelemetry: false
    sku: 'basic'
    replicaCount: 1
    partitionCount: 1
    semanticSearch: 'free'
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
    diagnosticSettings: [
      {
        name: 'foundation-metrics'
        workspaceResourceId: workspace.outputs.resourceId
        metricCategories: [{ category: 'AllMetrics' }]
      }
    ]
  }
}

module storage 'br/public:avm/res/storage/storage-account:0.33.0' = {
  name: 'evidence-storage'
  params: {
    name: evidenceStorageName
    location: location
    tags: tags
    enableTelemetry: false
    kind: 'StorageV2'
    skuName: 'Standard_LRS'
    accessTier: 'Hot'
    publicNetworkAccess: 'Disabled'
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'None'
      ipRules: []
      virtualNetworkRules: []
    }
    blobServices: {
      containerDeleteRetentionPolicyEnabled: true
      containerDeleteRetentionPolicyDays: 7
      deleteRetentionPolicyEnabled: true
      deleteRetentionPolicyDays: 7
      isVersioningEnabled: true
      containers: [
        {
          name: 'evidence'
          publicAccess: 'None'
        }
      ]
    }
    fileServices: {}
    queueServices: {}
    tableServices: {}
  }
}

module sql 'br/public:avm/res/sql/server:0.22.1' = {
  name: 'workflow-sql'
  params: {
    name: databaseServerName
    location: location
    tags: tags
    enableTelemetry: false
    publicNetworkAccess: 'Disabled'
    minimalTlsVersion: '1.2'
    administrators: {
      administratorType: 'ActiveDirectory'
      azureADOnlyAuthentication: true
      principalType: 'User'
      login: principalName
      sid: principalId
      tenantId: subscription().tenantId
    }
    firewallRules: []
    virtualNetworkRules: []
    auditSettings: {
      state: 'Enabled'
      isAzureMonitorTargetEnabled: true
    }
    databases: [
      {
        name: 'itops'
        availabilityZone: -1
        sku: {
          name: 'Basic'
          tier: 'Basic'
          capacity: 5
        }
        maxSizeBytes: 2147483648
        collation: 'SQL_Latin1_General_CP1_CI_AS'
        zoneRedundant: false
        requestedBackupStorageRedundancy: 'Local'
        diagnosticSettings: [
          {
            name: 'foundation-diagnostics'
            workspaceResourceId: workspace.outputs.resourceId
            logCategoriesAndGroups: [{ category: 'SQLSecurityAuditEvents' }]
            metricCategories: [{ category: 'AllMetrics' }]
          }
        ]
      }
    ]
  }
}

output aiAccountName string = account.outputs.name
output aiProjectName string = project.name
output aiProjectId string = project.id
output aiProjectEndpoint string = 'https://${account.outputs.name}.services.ai.azure.com/api/projects/${project.name}'
output openaiEndpoint string = 'https://${account.outputs.name}.openai.azure.com/'
output searchName string = search.outputs.name
output storageName string = storage.outputs.name
output sqlServer string = '${sql.outputs.name}${environment().suffixes.sqlServerHostname}'
output workspaceId string = workspace.outputs.resourceId
output appInsightsId string = insights.outputs.resourceId
