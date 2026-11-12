from ..operations import api_versions
from .base import TemplateBackend

COMPUTE_API_VERSION = "2024-07-01"


class ArmTemplateBackend(TemplateBackend):
    def build_template(self, config):
        network_api_version = api_versions.get_target_network_api()
        parameters = config.parameters
        network = parameters.get("networkOptions") or {}
        compute = parameters.get("computeOptions") or {}
        existing_nics = network.get("nicIds", [])
        create_vnet = network.get("createVnet", not parameters.get("existingSubnetId"))
        create_subnet = network.get("createSubnet", False)
        nsg_mode = network.get("nsgMode", "new")
        public_ip_mode = network.get(
            "publicIpMode", "new" if parameters.get("publicIpEnabled", False) else "none",
        )
        public_ip_enabled = public_ip_mode != "none"
        variables = {
            "vnetName": "[format('{0}-vnet', parameters('vmName'))]",
            "nsgName": "[format('{0}-nsg', parameters('vmName'))]",
            "publicIpName": "[format('{0}-pip', parameters('vmName'))]",
            "nicName": "[format('{0}-nic', parameters('vmName'))]",
            "nsgId": "[resourceId('Microsoft.Network/networkSecurityGroups', variables('nsgName'))]",
            "publicIpId": "[resourceId('Microsoft.Network/publicIPAddresses', variables('publicIpName'))]",
            "nicId": "[resourceId('Microsoft.Network/networkInterfaces', variables('nicName'))]",
        }
        if network:
            variables.update({
                name: f"[parameters('networkOptions').{name}]"
                for name in (
                    "vnetName", "nsgName", "publicIpName", "nicName",
                    "nsgId", "publicIpId",
                )
            })
        if existing_nics:
            variables["nicId"] = "[parameters('networkOptions').nicIds[0]]"
        resources = []
        nic_dependencies = []

        if create_vnet and not existing_nics:
            resources.append({
                "type": "Microsoft.Network/virtualNetworks",
                "apiVersion": network_api_version,
                "name": "[variables('vnetName')]",
                "location": "[parameters('location')]",
                "properties": {
                    "addressSpace": {"addressPrefixes": [
                        "[parameters('networkOptions').vnetAddressPrefix]"
                        if network else "10.20.0.0/16",
                    ]},
                    "subnets": [{
                        "name": "[parameters('networkOptions').subnetName]" if network else "default",
                        "properties": {
                            "addressPrefix": "[parameters('networkOptions').subnetAddressPrefix]"
                            if network else "10.20.0.0/24",
                        },
                    }],
                },
            })
            nic_dependencies.append("[resourceId('Microsoft.Network/virtualNetworks', variables('vnetName'))]")

        if create_subnet and not existing_nics:
            resources.append({
                "type": "Microsoft.Network/virtualNetworks/subnets",
                "apiVersion": network_api_version,
                "name": "[format('{0}/{1}', variables('vnetName'), "
                "parameters('networkOptions').subnetName)]",
                "properties": {
                    "addressPrefix": "[parameters('networkOptions').subnetAddressPrefix]",
                },
            })
            nic_dependencies.append(
                "[resourceId('Microsoft.Network/virtualNetworks/subnets', "
                "variables('vnetName'), parameters('networkOptions').subnetName)]",
            )

        if nsg_mode == "new" and not existing_nics:
            resources.append({
                "type": "Microsoft.Network/networkSecurityGroups",
                "apiVersion": network_api_version,
                "name": "[variables('nsgName')]",
                "location": "[parameters('location')]",
                "properties": {
                    "securityRules": [{
                        "name": "allow-ssh",
                        "properties": {
                            "priority": 1000,
                            "direction": "Inbound",
                            "access": "Allow",
                            "protocol": "Tcp",
                            "sourcePortRange": "*",
                            "destinationPortRange": "22",
                            "sourceAddressPrefix": "[parameters('sshSourceCidr')]",
                            "destinationAddressPrefix": "*",
                        },
                    }] if public_ip_enabled else [],
                },
            })
            nic_dependencies.append(
                "[resourceId('Microsoft.Network/networkSecurityGroups', variables('nsgName'))]",
            )

        if public_ip_mode == "new" and not existing_nics:
            resources.append({
                "type": "Microsoft.Network/publicIPAddresses",
                "apiVersion": network_api_version,
                "name": "[variables('publicIpName')]",
                "location": "[parameters('location')]",
                "sku": {"name": "Standard"},
                "properties": {"publicIPAllocationMethod": "Static"},
            })
            nic_dependencies.append("[resourceId('Microsoft.Network/publicIPAddresses', variables('publicIpName'))]")

        if not existing_nics:
            ip_properties = {
                "privateIPAllocationMethod": "Static" if network.get("privateIpAddress") else "Dynamic",
                "subnet": {
                    "id": "[parameters('networkOptions').subnetId]" if network else (
                        "[resourceId('Microsoft.Network/virtualNetworks/subnets', "
                        "variables('vnetName'), 'default')]" if create_vnet
                        else "[parameters('existingSubnetId')]"
                    ),
                },
            }
            if public_ip_enabled:
                ip_properties["publicIPAddress"] = {"id": "[variables('publicIpId')]"}
            elif not network:
                ip_properties["publicIPAddress"] = None
            if network.get("privateIpAddress"):
                ip_properties["privateIPAddress"] = "[parameters('networkOptions').privateIpAddress]"
            nic_properties = {
                "ipConfigurations": [{"name": "ipconfig1", "properties": ip_properties}],
            }
            if nsg_mode != "none":
                nic_properties["networkSecurityGroup"] = {"id": "[variables('nsgId')]"}
            resources.append({
                "type": "Microsoft.Network/networkInterfaces",
                "apiVersion": network_api_version,
                "name": "[variables('nicName')]",
                "location": "[parameters('location')]",
                "dependsOn": nic_dependencies,
                "properties": nic_properties,
            })

        resources.append({
            "type": "Microsoft.Compute/virtualMachines",
            "apiVersion": COMPUTE_API_VERSION,
            "name": "[parameters('vmName')]",
            "location": "[parameters('location')]",
            "dependsOn": [] if existing_nics else [
                "[resourceId('Microsoft.Network/networkInterfaces', variables('nicName'))]",
            ],
            "properties": {
                "hardwareProfile": {"vmSize": "[parameters('vmSize')]"},
                "osProfile": {
                    "computerName": "[parameters('vmName')]",
                    "adminUsername": "[parameters('adminUsername')]",
                    "linuxConfiguration": {
                        "disablePasswordAuthentication": True,
                        "ssh": {
                            "publicKeys": [{
                                "path": "[format('/home/{0}/.ssh/authorized_keys', "
                                "parameters('adminUsername'))]",
                                "keyData": "[parameters('sshPublicKey')]",
                            }],
                        },
                    },
                },
                "storageProfile": {
                    "imageReference": "[parameters('imageReference')]",
                    "osDisk": {
                        "createOption": "FromImage",
                        "diskSizeGB": "[parameters('osDiskSizeGb')]",
                        "caching": "ReadWrite",
                        "deleteOption": "Delete",
                        "managedDisk": {"storageAccountType": "[parameters('storageSku')]"},
                    },
                    "dataDisks": [{
                        "lun": index,
                        "createOption": "Empty",
                        "diskSizeGB": f"[parameters('dataDiskSizesGb')[{index}]]",
                        "caching": "None",
                        "deleteOption": "Detach",
                        "managedDisk": {"storageAccountType": "[parameters('storageSku')]"},
                    } for index in range(len(parameters.get("dataDiskSizesGb", [])))] + [{
                        "lun": f"[parameters('computeOptions').attachedDataDisks[{index}].lun]",
                        "createOption": "Attach",
                        "caching": f"[parameters('computeOptions').attachedDataDisks[{index}].caching]",
                        "deleteOption": "Detach",
                        "managedDisk": {
                            "id": f"[parameters('computeOptions').attachedDataDisks[{index}].id]",
                        },
                    } for index in range(len(compute.get("attachedDataDisks", [])))],
                },
                "networkProfile": {
                    "networkInterfaces": [{
                        "id": f"[parameters('networkOptions').nicIds[{index}]]",
                        "properties": {"primary": index == 0, "deleteOption": "Detach"},
                    } for index in range(len(existing_nics))] if existing_nics else [{
                        "id": "[variables('nicId')]",
                        "properties": {"primary": True, "deleteOption": "Delete"},
                    }],
                },
                "securityProfile": {
                    "securityType": "TrustedLaunch",
                    "uefiSettings": {"secureBootEnabled": True, "vTpmEnabled": True},
                },
                "diagnosticsProfile": {"bootDiagnostics": {"enabled": True}},
            },
        })

        vm = resources[-1]
        for field in ("identity", "plan", "zones"):
            if field in compute:
                vm[field] = f"[parameters('computeOptions').{field}]"
        for option, field in [
            ("availabilitySetId", "availabilitySet"),
            ("vmssId", "virtualMachineScaleSet"),
            ("ppgId", "proximityPlacementGroup"),
        ]:
            if compute.get(option):
                vm["properties"][field] = {"id": f"[parameters('computeOptions').{option}]"}
        if compute.get("bootDiagnosticsStorageUri"):
            vm["properties"]["diagnosticsProfile"]["bootDiagnostics"]["storageUri"] = (
                "[parameters('computeOptions').bootDiagnosticsStorageUri]"
            )
        if compute.get("roleAssignment"):
            resources.append({
                "type": "Microsoft.Authorization/roleAssignments",
                "apiVersion": "2022-04-01",
                "name": "[guid(resourceGroup().id, parameters('vmName'), "
                "parameters('computeOptions').roleAssignment.roleDefinitionId)]",
                "dependsOn": [
                    "[resourceId('Microsoft.Compute/virtualMachines', parameters('vmName'))]",
                ],
                "properties": {
                    "roleDefinitionId": "[parameters('computeOptions').roleAssignment.roleDefinitionId]",
                    "principalId": "[reference(resourceId('Microsoft.Compute/virtualMachines', "
                    f"parameters('vmName')), '{COMPUTE_API_VERSION}', 'Full').identity.principalId]",
                    "principalType": "ServicePrincipal",
                },
            })

        return {
            "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
            "contentVersion": "1.0.0.0",
            "parameters": {
                "vmName": {"type": "string", "minLength": 1, "maxLength": 56},
                "location": {"type": "string"},
                "adminUsername": {"type": "string"},
                "sshPublicKey": {"type": "string"},
                "vmSize": {"type": "string", "defaultValue": "Standard_D2s_v5"},
                "osDiskSizeGb": {"type": "int", "defaultValue": 32, "minValue": 1},
                "storageSku": {
                    "type": "string", "defaultValue": "Premium_LRS",
                    "allowedValues": ["Standard_LRS", "StandardSSD_LRS", "Premium_LRS"],
                },
                "dataDiskSizesGb": {"type": "array", "defaultValue": [], "maxLength": 64},
                "publicIpEnabled": {"type": "bool", "defaultValue": False},
                "sshSourceCidr": {"type": "string", "defaultValue": ""},
                "existingSubnetId": {"type": "string", "defaultValue": ""},
                "networkOptions": {"type": "object", "defaultValue": {}},
                "computeOptions": {"type": "object", "defaultValue": {}},
                "imageReference": {
                    "type": "object",
                    "defaultValue": {
                        "publisher": "Canonical",
                        "offer": "0001-com-ubuntu-server-jammy",
                        "sku": "22_04-lts-gen2",
                        "version": "latest",
                    },
                },
            },
            "variables": variables,
            "resources": resources,
            "outputs": {
                "vmId": {
                    "type": "string",
                    "value": "[resourceId('Microsoft.Compute/virtualMachines', parameters('vmName'))]",
                },
                "nicId": {"type": "string", "value": "[variables('nicId')]"},
                "privateIpAddress": {
                    "type": "string",
                    "value": f"[reference(variables('nicId'), '{network_api_version}')"
                    ".ipConfigurations[0].properties.privateIPAddress]",
                },
                "publicIpAddress": {
                    "type": "string",
                    "value": f"[reference(variables('publicIpId'), '{network_api_version}').ipAddress]"
                    if public_ip_enabled else "",
                },
                "virtualMachine": {
                    "type": "object",
                    "value": "[reference(resourceId('Microsoft.Compute/virtualMachines', "
                    f"parameters('vmName')), '{COMPUTE_API_VERSION}', 'Full')]",
                },
                "networkInterface": {
                    "type": "object",
                    "value": f"[reference(variables('nicId'), '{network_api_version}', 'Full')]",
                },
            },
        }
