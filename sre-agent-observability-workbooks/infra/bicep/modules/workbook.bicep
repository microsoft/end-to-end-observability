// ---------------------------------------------------------------------------
// workbook.bicep
// Deploys a single Azure Monitor workbook from a serialized JSON definition and
// scopes it to the target Log Analytics workspace. Invoked by main.bicep once
// per built workbook in the suite.
// ---------------------------------------------------------------------------

@description('Display name shown in the Azure Monitor gallery.')
param displayName string

@description('Serialized workbook JSON (the contents of NN-<slug>.workbook.json).')
param serializedData string

@description('Resource ID of the Log Analytics workspace the workbook is bound to.')
param sourceResourceId string

@description('Azure region for the workbook resource.')
param location string = resourceGroup().location

@description('Gallery category. "workbook" places it under the shared gallery.')
param category string = 'workbook'

@description('Optional tags applied to the workbook resource.')
param tags object = {}

// Deterministic GUID so re-deploying the same workbook updates in place rather
// than creating duplicates.
var workbookName = guid(resourceGroup().id, displayName, sourceResourceId)

resource workbook 'microsoft.insights/workbooks@2022-04-01' = {
  name: workbookName
  location: location
  kind: 'shared'
  tags: tags
  properties: {
    displayName: displayName
    serializedData: serializedData
    category: category
    sourceId: sourceResourceId
    version: 'Notebook/1.0'
  }
}

output workbookId string = workbook.id
output workbookName string = workbook.name
