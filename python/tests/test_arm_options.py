import unittest
from unittest.mock import patch

from vm_create.config import DeploymentConfig
from vm_create.templates.arm_template import ArmTemplateBackend


SUBSCRIPTION = "11111111-1111-1111-1111-111111111111"
NETWORK_ROOT = (
    f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-test/providers/Microsoft.Network"
)


class ArmOptionsTests(unittest.TestCase):
    def network(self, **overrides):
        return {
            "createVnet": True,
            "createSubnet": False,
            "vnetName": "custom-vnet",
            "subnetName": "custom-subnet",
            "subnetId": f"{NETWORK_ROOT}/virtualNetworks/custom-vnet/subnets/custom-subnet",
            "vnetAddressPrefix": "10.40.0.0/16",
            "subnetAddressPrefix": "10.40.2.0/24",
            "nsgMode": "new",
            "nsgName": "custom-nsg",
            "nsgId": f"{NETWORK_ROOT}/networkSecurityGroups/custom-nsg",
            "publicIpMode": "new",
            "publicIpName": "custom-pip",
            "publicIpId": f"{NETWORK_ROOT}/publicIPAddresses/custom-pip",
            "nicName": "custom-nic",
            "nicIds": [],
            "privateIpAddress": "",
            **overrides,
        }

    def build(self, **parameters):
        config = DeploymentConfig(SUBSCRIPTION, "rg-test", "vm-test", {
            "vmName": "test-vm", "location": "eastus",
            "adminUsername": "azureuser", "sshPublicKey": "test-key", **parameters,
        })
        template = ArmTemplateBackend().build_template(config)
        resources = {
            resource["type"].removeprefix("Microsoft."): resource
            for resource in template["resources"]
        }
        return template, resources

    def test_empty_options_preserve_legacy_networking(self):
        for public_ip in (False, True):
            for subnet in ("", f"{NETWORK_ROOT}/virtualNetworks/existing/subnets/default"):
                with self.subTest(public_ip=public_ip, subnet=subnet):
                    parameters = {"publicIpEnabled": public_ip, "existingSubnetId": subnet}
                    baseline, _ = self.build(**parameters)
                    empty, _ = self.build(**parameters, networkOptions={}, computeOptions={})
                    self.assertEqual(empty, baseline)

    def test_new_network_uses_selected_names_cidrs_and_static_address(self):
        template, resources = self.build(
            networkOptions=self.network(privateIpAddress="10.40.2.10"),
        )
        self.assertEqual(set(resources), {
            "Network/virtualNetworks", "Network/networkSecurityGroups",
            "Network/publicIPAddresses", "Network/networkInterfaces",
            "Compute/virtualMachines",
        })
        vnet = resources["Network/virtualNetworks"]
        self.assertEqual(vnet["name"], "[variables('vnetName')]")
        self.assertEqual(vnet["properties"]["addressSpace"]["addressPrefixes"], [
            "[parameters('networkOptions').vnetAddressPrefix]",
        ])
        self.assertEqual(vnet["properties"]["subnets"], [{
            "name": "[parameters('networkOptions').subnetName]",
            "properties": {"addressPrefix": "[parameters('networkOptions').subnetAddressPrefix]"},
        }])
        nic = resources["Network/networkInterfaces"]
        self.assertEqual(set(nic["dependsOn"]), {
            "[resourceId('Microsoft.Network/virtualNetworks', variables('vnetName'))]",
            "[resourceId('Microsoft.Network/networkSecurityGroups', variables('nsgName'))]",
            "[resourceId('Microsoft.Network/publicIPAddresses', variables('publicIpName'))]",
        })
        ip = nic["properties"]["ipConfigurations"][0]["properties"]
        self.assertEqual(ip["subnet"], {"id": "[parameters('networkOptions').subnetId]"})
        self.assertEqual(ip["privateIPAllocationMethod"], "Static")
        self.assertEqual(ip["privateIPAddress"], "[parameters('networkOptions').privateIpAddress]")
        for name in ("vnetName", "nicName", "nsgName", "publicIpName", "nsgId", "publicIpId"):
            self.assertEqual(template["variables"][name], f"[parameters('networkOptions').{name}]")
        self.assertEqual(
            resources["Network/networkSecurityGroups"]["properties"]["securityRules"][0]
            ["properties"]["sourceAddressPrefix"],
            "[parameters('sshSourceCidr')]",
        )

    def test_selected_public_ip_enables_ssh_despite_legacy_flag(self):
        for pip_mode in ("new", "existing"):
            with self.subTest(pip_mode=pip_mode):
                _, resources = self.build(
                    publicIpEnabled=False,
                    sshSourceCidr="203.0.113.4/32",
                    networkOptions=self.network(publicIpMode=pip_mode),
                )
                rules = resources["Network/networkSecurityGroups"]["properties"]["securityRules"]
                self.assertEqual(len(rules), 1)
                self.assertEqual(rules[0]["name"], "allow-ssh")
                self.assertEqual(rules[0]["properties"]["access"], "Allow")
                self.assertEqual(rules[0]["properties"]["destinationPortRange"], "22")
                self.assertEqual(
                    rules[0]["properties"]["sourceAddressPrefix"],
                    "[parameters('sshSourceCidr')]",
                )

    def test_security_and_public_ip_modes_only_depend_on_new_resources(self):
        for nsg_mode in ("new", "existing", "none"):
            for pip_mode in ("new", "existing", "none"):
                with self.subTest(nsg=nsg_mode, pip=pip_mode):
                    template, resources = self.build(networkOptions=self.network(
                        createVnet=False, nsgMode=nsg_mode, publicIpMode=pip_mode,
                        nsgId="" if nsg_mode == "none" else f"{NETWORK_ROOT}/networkSecurityGroups/reused",
                        publicIpId="" if pip_mode == "none" else f"{NETWORK_ROOT}/publicIPAddresses/reused",
                    ))
                    self.assertNotIn("Network/virtualNetworks", resources)
                    self.assertNotIn("Network/virtualNetworks/subnets", resources)
                    self.assertEqual("Network/networkSecurityGroups" in resources, nsg_mode == "new")
                    self.assertEqual("Network/publicIPAddresses" in resources, pip_mode == "new")
                    nic = resources["Network/networkInterfaces"]
                    self.assertEqual(set(nic["dependsOn"]), {
                        f"[resourceId('Microsoft.Network/{kind}', variables('{name}'))]"
                        for mode, kind, name in (
                            (nsg_mode, "networkSecurityGroups", "nsgName"),
                            (pip_mode, "publicIPAddresses", "publicIpName"),
                        ) if mode == "new"
                    })
                    self.assertEqual("networkSecurityGroup" in nic["properties"], nsg_mode != "none")
                    if nsg_mode != "none":
                        self.assertEqual(nic["properties"]["networkSecurityGroup"], {
                            "id": "[variables('nsgId')]",
                        })
                        self.assertEqual(
                            template["variables"]["nsgId"], "[parameters('networkOptions').nsgId]",
                        )
                    ip = nic["properties"]["ipConfigurations"][0]["properties"]
                    self.assertEqual(ip["subnet"], {"id": "[parameters('networkOptions').subnetId]"})
                    self.assertEqual(ip["privateIPAllocationMethod"], "Dynamic")
                    self.assertNotIn("privateIPAddress", ip)
                    self.assertEqual("publicIPAddress" in ip, pip_mode != "none")
                    if pip_mode == "none":
                        self.assertEqual(template["outputs"]["publicIpAddress"]["value"], "")
                    else:
                        self.assertEqual(ip["publicIPAddress"], {"id": "[variables('publicIpId')]"})
                        self.assertEqual(
                            template["variables"]["publicIpId"],
                            "[parameters('networkOptions').publicIpId]",
                        )
                        self.assertIn("reference(variables('publicIpId')", template["outputs"]["publicIpAddress"]["value"])

    def test_new_subnet_does_not_redeclare_existing_vnet(self):
        _, resources = self.build(networkOptions=self.network(
            createVnet=False, createSubnet=True, nsgMode="none", publicIpMode="none",
        ))
        self.assertNotIn("Network/virtualNetworks", resources)
        subnet = resources["Network/virtualNetworks/subnets"]
        self.assertEqual(subnet["name"], (
            "[format('{0}/{1}', variables('vnetName'), parameters('networkOptions').subnetName)]"
        ))
        self.assertEqual(subnet["properties"], {
            "addressPrefix": "[parameters('networkOptions').subnetAddressPrefix]",
        })
        self.assertEqual(resources["Network/networkInterfaces"]["dependsOn"], [
            "[resourceId('Microsoft.Network/virtualNetworks/subnets', "
            "variables('vnetName'), parameters('networkOptions').subnetName)]",
        ])

    def test_existing_nics_are_attached_without_resources_or_delete(self):
        nic_ids = [f"{NETWORK_ROOT}/networkInterfaces/nic-{index}" for index in range(2)]
        for pip_mode in ("none", "existing"):
            with self.subTest(pip_mode=pip_mode), patch(
                "vm_create.templates.arm_template.api_versions.get_target_network_api",
                return_value="2099-01-01",
            ):
                template, resources = self.build(networkOptions=self.network(
                    nicIds=nic_ids, publicIpMode=pip_mode,
                    publicIpId="" if pip_mode == "none" else f"{NETWORK_ROOT}/publicIPAddresses/reused",
                ))
                self.assertEqual(set(resources), {"Compute/virtualMachines"})
                vm = resources["Compute/virtualMachines"]
                self.assertEqual(vm["dependsOn"], [])
                self.assertEqual(vm["properties"]["networkProfile"]["networkInterfaces"], [
                    {
                        "id": f"[parameters('networkOptions').nicIds[{index}]]",
                        "properties": {"primary": index == 0, "deleteOption": "Detach"},
                    } for index in range(2)
                ])
                self.assertEqual(template["variables"]["nicId"], "[parameters('networkOptions').nicIds[0]]")
                outputs = template["outputs"]
                self.assertEqual(outputs["networkInterface"]["value"], (
                    "[reference(variables('nicId'), '2099-01-01', 'Full')]"
                ))
                self.assertIn("'2099-01-01'", outputs["privateIpAddress"]["value"])
                if pip_mode == "none":
                    self.assertEqual(outputs["publicIpAddress"]["value"], "")
                else:
                    self.assertEqual(outputs["publicIpAddress"]["value"], (
                        "[reference(variables('publicIpId'), '2099-01-01').ipAddress]"
                    ))

    def test_attached_disks_preserve_new_disk_luns_and_detach(self):
        compute_root = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-test/providers/Microsoft.Compute"
        disk_root = f"{compute_root}/disks"
        _, resources = self.build(
            dataDiskSizesGb=[32, 64],
            imageReference={"id": f"{compute_root}/images/custom"},
            computeOptions={"attachedDataDisks": [
                {"id": f"{disk_root}/data-a", "lun": 4, "caching": "ReadOnly"},
                {"id": f"{disk_root}/data-b", "lun": 8, "caching": "None"},
            ]},
        )
        storage = resources["Compute/virtualMachines"]["properties"]["storageProfile"]
        self.assertEqual(storage["imageReference"], "[parameters('imageReference')]")
        disks = storage["dataDisks"]
        self.assertEqual([disk["lun"] for disk in disks[:2]], [0, 1])
        self.assertEqual([disk["createOption"] for disk in disks], ["Empty", "Empty", "Attach", "Attach"])
        for index, disk in enumerate(disks[2:]):
            self.assertEqual(disk, {
                "lun": f"[parameters('computeOptions').attachedDataDisks[{index}].lun]",
                "createOption": "Attach",
                "caching": f"[parameters('computeOptions').attachedDataDisks[{index}].caching]",
                "deleteOption": "Detach",
                "managedDisk": {"id": f"[parameters('computeOptions').attachedDataDisks[{index}].id]"},
            })

    def test_image_plan_placement_identity_and_diagnostics_are_applied_to_vm(self):
        _, resources = self.build(computeOptions={
            "plan": {"name": "linux", "publisher": "example", "product": "linux"},
            "identity": {"type": "UserAssigned", "userAssignedIdentities": {"/dummy/identity": {}}},
            "zones": ["1"], "availabilitySetId": "/dummy/availability-set",
            "vmssId": "/dummy/vmss", "ppgId": "/dummy/ppg",
            "bootDiagnosticsStorageUri": "https://dummy.blob.core.windows.net/",
        })
        vm = resources["Compute/virtualMachines"]
        for field in ("plan", "identity", "zones"):
            self.assertEqual(vm[field], f"[parameters('computeOptions').{field}]")
        for field, option in [
            ("availabilitySet", "availabilitySetId"),
            ("virtualMachineScaleSet", "vmssId"), ("proximityPlacementGroup", "ppgId"),
        ]:
            self.assertEqual(vm["properties"][field], {"id": f"[parameters('computeOptions').{option}]"})
        self.assertEqual(
            vm["properties"]["diagnosticsProfile"]["bootDiagnostics"]["storageUri"],
            "[parameters('computeOptions').bootDiagnosticsStorageUri]",
        )
        self.assertNotIn("Authorization/roleAssignments", resources)

    def test_role_assignment_depends_on_vm_and_references_runtime_principal(self):
        _, resources = self.build(computeOptions={
            "identity": {"type": "SystemAssigned"},
            "roleAssignment": {"roleDefinitionId": "/dummy/role"},
        })
        assignment = resources["Authorization/roleAssignments"]
        self.assertEqual(assignment["dependsOn"], [
            "[resourceId('Microsoft.Compute/virtualMachines', parameters('vmName'))]",
        ])
        self.assertIn("guid(", assignment["name"])
        self.assertEqual(
            assignment["properties"]["roleDefinitionId"],
            "[parameters('computeOptions').roleAssignment.roleDefinitionId]",
        )
        self.assertIn("reference(resourceId('Microsoft.Compute/virtualMachines'", assignment["properties"]["principalId"])
        self.assertTrue(assignment["properties"]["principalId"].endswith(".identity.principalId]"))


if __name__ == "__main__":
    unittest.main()
