targetScope = 'subscription'

param location string = 'centralus'
@minLength(3)
@maxLength(26)
param prefix string = 'iot-domains-v2-e0q0j7'
param storageName string = 'iotdomainsv2e0q0j7sa'
@description('Opt in to public, identity-only storage without any private networking.')
param publicStorage bool = false
@description('Use the independently deployed, same-group <prefix>-plan without redeploying it.')
param useExistingPlan bool = false
@description('Deploy data/network resources only; application provisioning remains a separate validated stage.')
param deferApplication bool = false
@description('Empty preserves defaults. A nonempty override requires storageAccountUrl, logsEndpoint, dcrImmutableId and workspaceId from actual ARM readback.')
param resolvedRuntimeEndpoints object = {}
@description('Use an enforced subscription-identity perimeter instead of private networking or unrestricted storage.')
param securityPerimeter bool = false
@description('Explicit risk acceptance: anonymous portal and demo controls without IP restrictions.')
param unrestrictedPortal bool = false
@secure()
@description('Required when unrestrictedPortal is false; guarded deployment rejects an empty CIDR.')
param approvedIpCidr string = ''
param smokeToken string = ''

resource group 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: '${prefix}-rg'
  location: location
  tags: {
    application: 'energy-domains-v2'
    environment: 'synthetic-poc'
    'support-owner': 'poc-operator'
  }
}

module resources 'main.bicep' = {
  name: 'domains-v2-resources'
  scope: group
  params: {
    location: location
    prefix: prefix
    storageName: storageName
    publicStorage: publicStorage
    useExistingPlan: useExistingPlan
    deferApplication: deferApplication
    resolvedRuntimeEndpoints: resolvedRuntimeEndpoints
    securityPerimeter: securityPerimeter
    unrestrictedPortal: unrestrictedPortal
    approvedIpCidr: approvedIpCidr
    smokeToken: smokeToken
  }
}

output appName string = resources.outputs.appName
output appUrl string = resources.outputs.appUrl
output workspaceId string = resources.outputs.workspaceId
output workspaceResourceId string = resources.outputs.workspaceResourceId
output dcrImmutableId string = resources.outputs.dcrImmutableId
output logsEndpoint string = resources.outputs.logsEndpoint
output principalId string = resources.outputs.principalId
output perimeterName string = resources.outputs.perimeterName
output perimeterProfileId string = resources.outputs.perimeterProfileId
output perimeterResourceGroup string = securityPerimeter ? group.name : ''
