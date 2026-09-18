targetScope = 'resourceGroup'
param name string
param location string
param tags object
param subnetId string
param targetId string
param groupId string
param dnsZoneId string

module endpoint 'br/public:avm/res/network/private-endpoint:0.11.1' = {
  name: name
  params: {
    name: name
    location: location
    tags: tags
    enableTelemetry: false
    subnetResourceId: subnetId
    privateLinkServiceConnections: [
      {
        name: name
        properties: {
          privateLinkServiceId: targetId
          groupIds: [groupId]
        }
      }
    ]
    privateDnsZoneGroup: {
      name: 'default'
      privateDnsZoneGroupConfigs: [{ privateDnsZoneResourceId: dnsZoneId }]
    }
  }
}
