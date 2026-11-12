targetScope = 'resourceGroup'

@minLength(1)
@maxLength(56)
param vmName string
param location string
param adminUsername string
param sshPublicKey string
param vmSize string = 'Standard_D2s_v5'

@minValue(1)
param osDiskSizeGb int = 32

@allowed([
  'Standard_LRS'
  'StandardSSD_LRS'
  'Premium_LRS'
])
param storageSku string = 'Premium_LRS'

@maxLength(64)
param dataDiskSizesGb int[] = []
param publicIpEnabled bool = false
param sshSourceCidr string = ''
param existingSubnetId string = ''
param networkOptions object = {}
param computeOptions object = {}
param imageReference object = {
  publisher: 'Canonical'
  offer: '0001-com-ubuntu-server-jammy'
  sku: '22_04-lts-gen2'
  version: 'latest'
}

module network './network.bicep' = {
  name: '${vmName}-network'
  params: {
    vmName: vmName
    location: location
    publicIpEnabled: publicIpEnabled
    sshSourceCidr: sshSourceCidr
    existingSubnetId: existingSubnetId
    networkOptions: networkOptions
  }
}

module compute './compute.bicep' = {
  name: '${vmName}-compute'
  params: {
    vmName: vmName
    location: location
    nicId: network.outputs.nicId
    adminUsername: adminUsername
    sshPublicKey: sshPublicKey
    vmSize: vmSize
    osDiskSizeGb: osDiskSizeGb
    storageSku: storageSku
    dataDiskSizesGb: dataDiskSizesGb
    imageReference: imageReference
    computeOptions: computeOptions
    existingNicIds: networkOptions.?nicIds ?? []
  }
}

output vmId string = compute.outputs.vmId
output nicId string = network.outputs.nicId
output privateIpAddress string = network.outputs.privateIpAddress
output publicIpAddress string = network.outputs.publicIpAddress
output virtualMachine object = compute.outputs.virtualMachine
output networkInterface object = network.outputs.networkInterface
