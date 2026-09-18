targetScope = 'resourceGroup'

@minLength(1)
param perimeterName string
@minLength(1)
param associationName string
@minLength(1)
param privateLinkResourceId string

resource perimeter 'Microsoft.Network/networkSecurityPerimeters@2025-01-01' existing = {
  name: perimeterName
}
resource profile 'Microsoft.Network/networkSecurityPerimeters/profiles@2025-01-01' existing = {
  parent: perimeter
  name: 'subscription-identities'
}
resource association 'Microsoft.Network/networkSecurityPerimeters/resourceAssociations@2025-01-01' = {
  parent: perimeter
  name: associationName
  properties: {
    accessMode: 'Enforced'
    privateLinkResource: { id: privateLinkResourceId }
    profile: { id: profile.id }
  }
}

output associationId string = association.id
output profileId string = profile.id
