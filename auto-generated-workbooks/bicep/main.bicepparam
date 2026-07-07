using './main.bicep'

// ---------------------------------------------------------------------------
// main.bicepparam — example parameters for the Auto-Generated Workbooks deploy.
//
// The SRE Workbook Orchestrator agent populates `workbooks[].serializedData`
// from generator/out/<domain>.workbook.json using loadTextContent(), so the
// rendered definitions are embedded at compile time. Replace the workspace ID
// and the loaded paths below for your environment.
// ---------------------------------------------------------------------------

param workspaceResourceId = '/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-observability/providers/Microsoft.OperationalInsights/workspaces/law-observability'

param location = 'eastus'

param workbooks = [
  {
    domain: 'infrastructure'
    displayName: 'Auto-Generated Infrastructure Workbook'
    serializedData: loadTextContent('../generator/out/infrastructure.workbook.json')
  }
  {
    domain: 'application'
    displayName: 'Auto-Generated Application Workbook'
    serializedData: loadTextContent('../generator/out/application.workbook.json')
  }
  {
    domain: 'storage'
    displayName: 'Auto-Generated Storage Workbook'
    serializedData: loadTextContent('../generator/out/storage.workbook.json')
  }
]
