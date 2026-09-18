targetScope = 'subscription'

@allowed(['centralus'])
param location string = 'centralus'
@minLength(3)
@maxLength(26)
param prefix string

resource group 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: '${prefix}-rg'
  location: location
  tags: {
    application: 'energy-domains-v2'
    environment: 'synthetic-poc'
    'support-owner': 'poc-operator'
  }
}

output resourceGroupName string = group.name
output resourceGroupId string = group.id
