// ---------------------------------------------------------------------------
// sre-agent.bicep - Provision an Azure SRE Agent as IaC.
//
// Microsoft.App/agents became an ARM/Bicep/Terraform resource (api 2026-01-01),
// replacing the earlier portal-only path. VERIFY the api version + region support
// in your tenant before relying on this (see the pre-req checklist); if the type
// is not yet enumerable, fall back to sre.azure.com -> Create agent.
//
// Start in Review mode with Reader-level permissions and the smallest managed
// resource-group blast radius, per the repo's Review-first convention.
// ---------------------------------------------------------------------------

@description('Name of the SRE Agent resource.')
param agentName string

@description('Azure region. Must be an SRE Agent supported region (e.g. eastus2, swedencentral, australiaeast, uksouth).')
param location string

@description('Operating mode for the agent.')
@allowed([
  'ReadOnly'
  'Review'
  'Autonomous'
])
param mode string = 'Review'

@description('Optional resource group IDs the agent is scoped to manage (smallest blast radius first).')
param managedResourceGroupIds array = []

resource sreAgent 'Microsoft.App/agents@2026-01-01' = {
  name: agentName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    mode: mode
    managedResourceGroups: managedResourceGroupIds
  }
}

output agentResourceId string = sreAgent.id
output agentPrincipalId string = sreAgent.identity.principalId
