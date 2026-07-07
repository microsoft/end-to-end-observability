// ---------------------------------------------------------------------------
// main.bicep
// Auto-Generated Workbooks — CLI deployment entry point (Story #33).
//
// Deploys one Azure Monitor workbook per generated domain definition to the
// target Log Analytics workspace. The SRE Workbook Orchestrator agent invokes
// this during the Deploy stage:
//
//   az deployment group create \
//     --resource-group <rg> \
//     --template-file bicep/main.bicep \
//     --parameters bicep/main.bicepparam
//
// Workbook definitions are passed as an array of { domain, displayName,
// serializedData } objects, typically assembled from generator/out/*.json.
// ---------------------------------------------------------------------------

targetScope = 'resourceGroup'

@description('Resource ID of the Log Analytics workspace the workbooks query.')
param workspaceResourceId string

@description('Azure region for the workbook resources.')
param location string = resourceGroup().location

@description('Tags applied to every deployed workbook.')
param tags object = {
  workload: 'auto-generated-workbooks'
  generatedBy: 'sre-workbook-orchestrator'
}

@description('''
Array of workbook definitions to deploy. Each item:
  {
    domain: 'infrastructure'
    displayName: 'Auto-Generated Infrastructure Workbook'
    serializedData: '<contents of infrastructure.workbook.json>'
  }
''')
param workbooks array

module workbookDeployments 'modules/workbook.bicep' = [for wb in workbooks: {
  name: 'wb-${wb.domain}'
  params: {
    displayName: wb.displayName
    serializedData: wb.serializedData
    sourceResourceId: workspaceResourceId
    location: location
    tags: union(tags, {
      domain: wb.domain
    })
  }
}]

output deployedWorkbookIds array = [for (wb, i) in workbooks: workbookDeployments[i].outputs.workbookId]
