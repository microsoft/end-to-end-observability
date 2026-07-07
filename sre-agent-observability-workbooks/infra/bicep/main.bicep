// ---------------------------------------------------------------------------
// main.bicep
// SRE Agent Observability Workbooks — CLI deployment entry point.
//
// Deploys one Azure Monitor workbook per built suite definition to the target
// Log Analytics workspace:
//
//   az deployment group create \
//     --resource-group <rg> \
//     --template-file infra/bicep/main.bicep \
//     --parameters infra/bicep/main.bicepparam
//
// Workbook definitions are passed as an array of { slug, displayName,
// serializedData } objects. Use scripts/New-BicepParam.ps1 (or the README
// snippet) to assemble main.bicepparam from workbooks/*.workbook.json, since
// Bicep cannot read a directory of files at deploy time.
// ---------------------------------------------------------------------------

targetScope = 'resourceGroup'

@description('Resource ID of the Log Analytics workspace the workbooks query.')
param workspaceResourceId string

@description('Azure region for the workbook resources.')
param location string = resourceGroup().location

@description('Tags applied to every deployed workbook.')
param tags object = {
  workload: 'sre-agent-observability-workbooks'
  managedBy: 'sre-agent'
  suite: 'SRE Agent Observability Workbooks'
}

@description('''
Array of workbook definitions to deploy. Each item:
  {
    slug: 'sre-agent-command-center'
    displayName: 'SRE Agent Command Center'
    serializedData: '<contents of 01-sre-agent-command-center.workbook.json>'
  }
''')
param workbooks array

module workbookDeployments 'modules/workbook.bicep' = [for wb in workbooks: {
  name: 'wb-${wb.slug}'
  params: {
    displayName: wb.displayName
    serializedData: wb.serializedData
    sourceResourceId: workspaceResourceId
    location: location
    tags: union(tags, {
      slug: wb.slug
    })
  }
}]

output deployedWorkbookIds array = [for (wb, i) in workbooks: workbookDeployments[i].outputs.workbookId]
