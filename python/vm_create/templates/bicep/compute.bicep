param vmName string
param location string
param nicId string
param adminUsername string
param sshPublicKey string
param vmSize string
param osDiskSizeGb int
param storageSku string
param dataDiskSizesGb int[]
param imageReference object
param computeOptions object = {}
param existingNicIds array = []

var newDataDisks = [
  for (diskSize, index) in dataDiskSizesGb: {
    lun: index
    createOption: 'Empty'
    diskSizeGB: diskSize
    caching: 'None'
    deleteOption: 'Detach'
    managedDisk: {
      storageAccountType: storageSku
    }
  }
]
var attachedDataDisks = [
  for disk in (computeOptions.?attachedDataDisks ?? []): {
    lun: disk.lun
    createOption: 'Attach'
    caching: disk.caching
    deleteOption: 'Detach'
    managedDisk: {
      id: disk.id
    }
  }
]
var existingNetworkInterfaces = [
  for (id, index) in existingNicIds: {
    id: id
    properties: {
      primary: index == 0
      deleteOption: 'Detach'
    }
  }
]

resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' = {
  name: vmName
  location: location
  identity: computeOptions.?identity ?? null
  plan: computeOptions.?plan ?? null
  zones: computeOptions.?zones ?? null
  properties: {
    availabilitySet: empty(computeOptions.?availabilitySetId ?? '') ? null : {
      id: computeOptions.availabilitySetId
    }
    virtualMachineScaleSet: empty(computeOptions.?vmssId ?? '') ? null : {
      id: computeOptions.vmssId
    }
    proximityPlacementGroup: empty(computeOptions.?ppgId ?? '') ? null : {
      id: computeOptions.ppgId
    }
    hardwareProfile: {
      vmSize: vmSize
    }
    osProfile: {
      computerName: vmName
      adminUsername: adminUsername
      linuxConfiguration: {
        disablePasswordAuthentication: true
        ssh: {
          publicKeys: [
            {
              path: '/home/${adminUsername}/.ssh/authorized_keys'
              keyData: sshPublicKey
            }
          ]
        }
      }
    }
    storageProfile: {
      imageReference: imageReference
      osDisk: {
        createOption: 'FromImage'
        diskSizeGB: osDiskSizeGb
        caching: 'ReadWrite'
        deleteOption: 'Delete'
        managedDisk: {
          storageAccountType: storageSku
        }
      }
      dataDisks: concat(newDataDisks, attachedDataDisks)
    }
    networkProfile: {
      networkInterfaces: empty(existingNicIds) ? [
        {
          id: nicId
          properties: {
            primary: true
            deleteOption: 'Delete'
          }
        }
      ] : existingNetworkInterfaces
    }
    securityProfile: {
      securityType: 'TrustedLaunch'
      uefiSettings: {
        secureBootEnabled: true
        vTpmEnabled: true
      }
    }
    diagnosticsProfile: {
      bootDiagnostics: {
        enabled: true
        storageUri: computeOptions.?bootDiagnosticsStorageUri ?? null
      }
    }
  }
}

var roleAssignment = computeOptions.?roleAssignment ?? {}
module identityRole './role_assignment.bicep' = if (!empty(roleAssignment)) {
  name: '${vmName}-identity-role'
  params: {
    vmName: vmName
    principalId: !empty(roleAssignment) ? vm.identity!.principalId : ''
    roleDefinitionId: roleAssignment.?roleDefinitionId ?? ''
  }
}

output vmId string = vm.id
output virtualMachine object = vm
