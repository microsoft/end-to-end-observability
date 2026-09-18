targetScope = 'resourceGroup'

param location string = resourceGroup().location
@minLength(3)
@maxLength(26)
param prefix string

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${prefix}-plan'
  location: location
  tags: {
    application: 'energy-domains-v2'
    environment: 'synthetic-poc'
    'support-owner': 'poc-operator'
  }
  kind: 'linux'
  sku: { name: 'B2', tier: 'Basic', capacity: 1 }
  properties: { reserved: true }
}

output planId string = plan.id
