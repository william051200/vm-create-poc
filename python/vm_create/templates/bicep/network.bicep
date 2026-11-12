param vmName string
param location string
param publicIpEnabled bool
param sshSourceCidr string
param existingSubnetId string
param networkOptions object = {}

var existingNicIds = networkOptions.?nicIds ?? []
var useExistingNic = !empty(existingNicIds)
var createVnet = !useExistingNic && (networkOptions.?createVnet ?? empty(existingSubnetId))
var createSubnet = !useExistingNic && (networkOptions.?createSubnet ?? false)
var vnetName = networkOptions.?vnetName ?? '${vmName}-vnet'
var subnetName = networkOptions.?subnetName ?? 'default'
var subnetId = networkOptions.?subnetId ?? (createVnet ? resourceId('Microsoft.Network/virtualNetworks/subnets', vnetName, subnetName) : existingSubnetId)
var nsgMode = networkOptions.?nsgMode ?? 'new'
var nsgName = networkOptions.?nsgName ?? '${vmName}-nsg'
var nsgId = networkOptions.?nsgId ?? resourceId('Microsoft.Network/networkSecurityGroups', nsgName)
var publicIpMode = networkOptions.?publicIpMode ?? (publicIpEnabled ? 'new' : 'none')
var publicIpName = networkOptions.?publicIpName ?? '${vmName}-pip'
var publicIpId = networkOptions.?publicIpId ?? resourceId('Microsoft.Network/publicIPAddresses', publicIpName)
var nicName = networkOptions.?nicName ?? '${vmName}-nic'
var requestedPrivateIpAddress = networkOptions.?privateIpAddress ?? ''
var firstNicId = useExistingNic ? existingNicIds[0] : resourceId('Microsoft.Network/networkInterfaces', nicName)
var hasPublicIp = publicIpMode != 'none' && !empty(publicIpId)

resource existingNic 'Microsoft.Network/networkInterfaces@2023-11-01' existing = if (useExistingNic) {
  name: last(split(firstNicId, '/'))
  scope: resourceGroup(split(firstNicId, '/')[4])
}

resource existingPublicIp 'Microsoft.Network/publicIPAddresses@2023-11-01' existing = if (hasPublicIp && publicIpMode == 'existing') {
  name: last(split(publicIpId, '/'))
  scope: resourceGroup(hasPublicIp ? split(publicIpId, '/')[4] : resourceGroup().name)
}

resource vnet 'Microsoft.Network/virtualNetworks@2023-11-01' = if (createVnet) {
  name: vnetName
  location: location
  properties: {
    addressSpace: {
      addressPrefixes: [
        networkOptions.?vnetAddressPrefix ?? '10.20.0.0/16'
      ]
    }
    subnets: [
      {
        name: subnetName
        properties: {
          addressPrefix: networkOptions.?subnetAddressPrefix ?? '10.20.0.0/24'
        }
      }
    ]
  }
}

resource subnetVnet 'Microsoft.Network/virtualNetworks@2023-11-01' existing = {
  name: vnetName
}

resource subnet 'Microsoft.Network/virtualNetworks/subnets@2023-11-01' = if (createSubnet) {
  parent: subnetVnet
  name: subnetName
  properties: {
    addressPrefix: networkOptions.?subnetAddressPrefix ?? '10.20.0.0/24'
  }
}

resource nsg 'Microsoft.Network/networkSecurityGroups@2023-11-01' = if (!useExistingNic && nsgMode == 'new') {
  name: nsgName
  location: location
  properties: {
    securityRules: publicIpMode != 'none'
      ? [
          {
            name: 'allow-ssh'
            properties: {
              priority: 1000
              direction: 'Inbound'
              access: 'Allow'
              protocol: 'Tcp'
              sourcePortRange: '*'
              destinationPortRange: '22'
              sourceAddressPrefix: sshSourceCidr
              destinationAddressPrefix: '*'
            }
          }
        ]
      : []
  }
}

resource publicIp 'Microsoft.Network/publicIPAddresses@2023-11-01' = if (!useExistingNic && publicIpMode == 'new') {
  name: publicIpName
  location: location
  sku: {
    name: 'Standard'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
  }
}

resource nic 'Microsoft.Network/networkInterfaces@2023-11-01' = if (!useExistingNic) {
  name: nicName
  location: location
  properties: {
    networkSecurityGroup: nsgMode == 'none' ? null : {
      id: nsgId
    }
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          privateIPAllocationMethod: empty(requestedPrivateIpAddress) ? 'Dynamic' : 'Static'
          privateIPAddress: empty(requestedPrivateIpAddress) ? null : requestedPrivateIpAddress
          subnet: {
            id: subnetId
          }
          publicIPAddress: hasPublicIp
            ? {
                id: publicIpId
              }
            : null
        }
      }
    ]
  }
  dependsOn: [
    vnet
    subnet
    nsg
    publicIp
  ]
}

output nicId string = firstNicId
output privateIpAddress string = useExistingNic
  ? existingNic!.properties.ipConfigurations[0].properties.privateIPAddress
  : nic!.properties.ipConfigurations[0].properties.privateIPAddress
output publicIpAddress string = !hasPublicIp ? '' : (publicIpMode == 'existing'
  ? existingPublicIp!.properties.ipAddress
  : (!useExistingNic ? publicIp!.properties.ipAddress : ''))
output networkInterface object = useExistingNic ? existingNic! : nic!
