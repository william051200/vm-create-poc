import base64
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from vm_create.arm import ArmDeployer, ArmTransport, Response
from vm_create.cli import main
from vm_create.config import DeploymentConfig, load_config
from vm_create.factories.backend import BackendType, create_backend
from vm_create.templates.arm_template import ArmTemplateBackend
from vm_create.templates.bicep import BicepBackend

ROOT = Path(__file__).resolve().parents[2]
SUBSCRIPTION = "11111111-1111-1111-1111-111111111111"
KEY_TYPE = b"ssh-ed25519"
PUBLIC_KEY = "ssh-ed25519 " + base64.b64encode(
    len(KEY_TYPE).to_bytes(4, "big") + KEY_TYPE + (32).to_bytes(4, "big") + bytes(range(32))
).decode("ascii")


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="vm-create-config-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "key.pub").write_text(PUBLIC_KEY, encoding="utf-8")
        self.path = self.root / "config.json"
        self.data = {
            "subscriptionId": SUBSCRIPTION,
            "resourceGroup": "rg-test",
            "deploymentName": "vm-test",
            "sshPublicKeyPath": "key.pub",
            "parameters": {
                "vmName": "test-vm", "location": "eastus", "adminUsername": "azureuser",
            },
        }

    def load(self):
        self.path.write_text(json.dumps(self.data), encoding="utf-8")
        return load_config(self.path)

    def test_relative_key_and_typed_parameters(self):
        self.data["parameters"].update(dataDiskSizesGb=[32, 64], publicIpEnabled=False)
        config = self.load()
        payload = config.payload({"resources": []})
        parameters = payload["properties"]["parameters"]
        self.assertEqual(parameters["sshPublicKey"]["value"], PUBLIC_KEY)
        self.assertEqual(parameters["dataDiskSizesGb"]["value"], [32, 64])
        self.assertIs(parameters["publicIpEnabled"]["value"], False)
        self.assertNotIn("sshPublicKeyPath", parameters)

    def test_invalid_configuration_rejected_before_compilation(self):
        cases = [
            {"publicIpEnabled": True},
            {"publicIpEnabled": True, "sshSourceCidr": "0.0.0.0/0"},
            {"sshSourceCidr": "10.0.0.1/24"},
            {"dataDiskSizesGb": [True]},
            {"dataDiskSizesGb": [0]},
            {"dataDiskSizesGb": None},
            {"vmName": "invalid;name"},
            {"vmName": "a" * 57},
            {"adminUsername": "root"},
            {"unknown": "value"},
            {"imageReference": {"id": "not-supported"}},
            {"existingSubnetId": "/subscriptions/other/resourceGroups/rg"},
        ]
        original = dict(self.data["parameters"])
        for update in cases:
            with self.subTest(update=update):
                self.data["parameters"] = {**original, **update}
                with self.assertRaises(ValueError):
                    self.load()

    def test_public_ip_and_existing_subnet(self):
        self.data["parameters"].update(
            publicIpEnabled=True, sshSourceCidr="203.0.113.4/32",
            existingSubnetId=f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg"
            "/providers/Microsoft.Network/virtualNetworks/vnet/subnets/default",
        )
        self.assertTrue(self.load().parameters["publicIpEnabled"])

    def test_private_key_rejected(self):
        (self.root / "key.pub").write_text("-----BEGIN OPENSSH PRIVATE KEY-----", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "public key"):
            self.load()

    def test_default_operation_build_does_not_authenticate(self):
        config = self.load()
        for options in [[], ["--backend", "bicep"]]:
            with self.subTest(options=options), \
                    patch("vm_create.templates.bicep.compile_template", return_value={"resources": []}) as compile_, \
                    patch("vm_create.templates.base.AzureCliToken") as token, \
                    redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main([
                    "--config", str(self.path), "--bicep-path", "custom-bicep", *options,
                ]), 0)
                self.assertEqual(json.loads(output.getvalue()), config.payload({"resources": []}))
                compile_.assert_called_once_with("custom-bicep")
                token.assert_not_called()

    def test_cli_executes_selected_backend(self):
        config = self.load()
        result = {"operation": "validate", "state": "Succeeded", "outputs": {}}
        for backend in BackendType:
            with self.subTest(backend=backend), patch("vm_create.cli.create_backend") as factory, \
                    redirect_stdout(io.StringIO()) as output:
                factory.return_value.execute.return_value = result
                self.assertEqual(main([
                    "--config", str(self.path), "--backend", backend.value,
                    "--operation", "validate", "--timeout-seconds", "42",
                    "--bicep-path", "custom-bicep",
                ]), 0)
                factory.assert_called_once_with(backend.value, bicep_path="custom-bicep")
                factory.return_value.execute.assert_called_once_with(config, "validate", 42)
                self.assertEqual(json.loads(output.getvalue()), result)

    def test_arm_template_build_does_not_compile_or_authenticate(self):
        config = self.load()
        with patch("vm_create.templates.bicep.run_process") as compiler, \
                patch("vm_create.templates.base.AzureCliToken") as token, \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main([
                "--config", str(self.path), "--backend", "arm-template",
                "--bicep-path", "nonexistent-bicep",
            ]), 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["properties"]["mode"], "Incremental")
            self.assertEqual(
                payload["properties"]["parameters"],
                config.payload({})["properties"]["parameters"],
            )
            self.assertEqual(
                {resource["type"] for resource in payload["properties"]["template"]["resources"]},
                {
                    "Microsoft.Network/virtualNetworks", "Microsoft.Network/networkSecurityGroups",
                    "Microsoft.Network/networkInterfaces", "Microsoft.Compute/virtualMachines",
                },
            )
            compiler.assert_not_called()
            token.assert_not_called()

    def test_cli_rejects_unsupported_backend_before_execution(self):
        with patch("vm_create.cli.create_backend") as factory, \
                redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            main(["--config", str(self.path), "--backend", "api"])
        self.assertEqual(error.exception.code, 2)
        factory.assert_not_called()

    def resource(self, resource_type, name, **properties):
        types = resource_type.split("/")
        names = name.split("/")
        suffix = "/".join(part for pair in zip(types[1:], names) for part in pair)
        return {
            "id": f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-test/providers/{types[0]}/{suffix}",
            "location": "eastus",
            "properties": properties,
        }

    def test_offline_network_creates_custom_resources_without_lookups_to_azure(self):
        self.data["network"] = {
            "vnet": "custom-vnet", "subnet": "workload",
            "vnetAddressPrefix": "10.30.0.0/16", "subnetAddressPrefix": "10.30.1.0/24",
            "nicName": "custom-nic", "privateIpAddress": "10.30.1.8",
            "nsg": "", "publicIp": "",
        }
        with patch("vm_create.templates.base.AzureCliToken") as token, \
                patch("vm_create.arm.ArmTransport.__call__") as request:
            config = self.load()
            payload = create_backend("arm-template").execute(config, "build")
        options = payload["properties"]["parameters"]["networkOptions"]["value"]
        self.assertTrue(config.simulated)
        self.assertTrue(options["createVnet"])
        self.assertFalse(options["createSubnet"])
        self.assertEqual(options["vnetName"], "custom-vnet")
        self.assertEqual(options["subnetName"], "workload")
        self.assertEqual(options["privateIpAddress"], "10.30.1.8")
        self.assertEqual(options["nsgMode"], "none")
        self.assertEqual(options["publicIpMode"], "none")
        token.assert_not_called()
        request.assert_not_called()

    def test_offline_discovery_reuses_vnet_and_nested_subnet(self):
        vnet = self.resource(
            "Microsoft.Network/virtualNetworks", "shared",
            addressSpace={"addressPrefixes": ["10.40.0.0/16"]},
            subnets=[{"name": "apps", "properties": {"addressPrefix": "10.40.2.0/24"}}],
        )
        self.data.update(network={}, mockResources=[vnet])
        options = self.load().parameters["networkOptions"]
        self.assertFalse(options["createVnet"])
        self.assertFalse(options["createSubnet"])
        self.assertEqual(options["subnetId"], vnet["id"] + "/subnets/apps")
        self.assertEqual(options["subnetAddressPrefix"], "10.40.2.0/24")
        del self.data["network"]
        self.assertEqual(self.load().parameters["networkOptions"], options)
        del vnet["location"]
        self.assertEqual(self.load().parameters["networkOptions"], options)

    def test_offline_subnet_discovery_skips_gateway_subnet(self):
        vnet = self.resource(
            "Microsoft.Network/virtualNetworks", "shared",
            addressSpace={"addressPrefixes": ["10.40.0.0/16"]},
            subnets=[
                {"name": "GatewaySubnet", "properties": {"addressPrefix": "10.40.0.0/24"}},
                {"name": "workload", "properties": {"addressPrefix": "10.40.1.0/24"}},
            ],
        )
        self.data.update(network={}, mockResources=[vnet])
        self.assertEqual(self.load().parameters["networkOptions"]["subnetName"], "workload")

    def test_offline_existing_vnet_only_creates_missing_subnet(self):
        vnet = self.resource(
            "Microsoft.Network/virtualNetworks", "shared",
            addressSpace={"addressPrefixes": ["10.40.0.0/16"]},
        )
        self.data.update(
            network={"vnet": "shared", "subnet": "new", "subnetAddressPrefix": "10.40.3.0/24"},
            mockResources=[vnet],
        )
        options = self.load().parameters["networkOptions"]
        self.assertFalse(options["createVnet"])
        self.assertTrue(options["createSubnet"])
        self.assertEqual(options["subnetId"], vnet["id"] + "/subnets/new")

    def test_offline_network_passes_through_dummy_prefixes_and_addresses(self):
        vnet = self.resource(
            "Microsoft.Network/virtualNetworks", "shared",
            addressSpace={"addressPrefixes": ["10.40.0.0/16"]},
            subnets=[{"name": "apps", "properties": {"addressPrefix": "10.40.1.0/24"}}],
        )
        self.data.update(
            mockResources=[vnet],
            network={
                "vnet": "shared", "subnet": "apps",
                "subnetAddressPrefix": "dummy-prefix", "privateIpAddress": "dummy-address",
            },
        )
        options = self.load().parameters["networkOptions"]
        self.assertFalse(options["createVnet"])
        self.assertFalse(options["createSubnet"])
        self.assertEqual(options["subnetAddressPrefix"], "dummy-prefix")
        self.assertEqual(options["privateIpAddress"], "dummy-address")

    def test_offline_reuses_nsg_public_ip_and_nics(self):
        nsg = self.resource("Microsoft.Network/networkSecurityGroups", "shared-nsg")
        pip = self.resource("Microsoft.Network/publicIPAddresses", "shared-pip")
        nics = [self.resource("Microsoft.Network/networkInterfaces", name) for name in ["nic1", "nic2"]]
        self.data["mockResources"] = [nsg, pip, *nics]
        self.data["network"] = {"nsg": nsg["id"], "publicIp": "shared-pip"}
        options = self.load().parameters["networkOptions"]
        self.assertEqual(options["nsgMode"], "existing")
        self.assertEqual(options["publicIpMode"], "existing")
        self.assertEqual(options["nsgId"], nsg["id"])
        self.data["network"] = {"nics": ["nic1", nics[1]["id"]]}
        options = self.load().parameters["networkOptions"]
        self.assertEqual(options["nicIds"], [nic["id"] for nic in nics])
        self.assertFalse(options["createVnet"])
        self.assertEqual(options["nsgMode"], "none")
        del self.data["mockResources"]
        self.assertEqual(self.load().parameters["networkOptions"], options)
        self.data["network"] = {"nsg": nsg["id"], "publicIp": pip["id"]}
        options = self.load().parameters["networkOptions"]
        self.assertEqual(options["nsgMode"], "existing")
        self.assertEqual(options["publicIpMode"], "existing")

    def test_offline_image_and_disk_resolution(self):
        image = self.resource("Microsoft.Compute/images", "linux", osType="Linux")
        disk = self.resource("Microsoft.Compute/disks", "data", diskState="Unattached")
        self.data.update(
            compute={"image": image["id"], "attachDataDisks": ["data"]},
            mockResources=[image, disk],
        )
        self.data["parameters"]["dataDiskSizesGb"] = [32, 64]
        parameters = self.load().parameters
        self.assertEqual(parameters["imageReference"], {"id": image["id"]})
        self.assertEqual(parameters["computeOptions"]["attachedDataDisks"], [
            {"id": disk["id"], "lun": 2, "caching": "None"},
        ])
        self.data["compute"] = {"image": "Canonical:offer:sku:latest"}
        parameters = self.load().parameters
        self.assertEqual(parameters["imageReference"], {
            "publisher": "Canonical", "offer": "offer", "sku": "sku", "version": "latest",
        })
        self.data["compute"] = {"image": "Ubuntu2204"}
        self.assertEqual(self.load().parameters["imageReference"]["publisher"], "Canonical")

    def test_offline_compute_accepts_dummy_ids_without_inventory(self):
        image = self.resource("Microsoft.Compute/images", "image")
        disk = self.resource("Microsoft.Compute/disks", "data")
        self.data["compute"] = {"image": image["id"], "attachDataDisks": ["data"]}
        parameters = self.load().parameters
        self.assertEqual(parameters["imageReference"], {"id": image["id"]})
        self.assertEqual(parameters["computeOptions"]["attachedDataDisks"], [
            {"id": disk["id"], "lun": 0, "caching": "None"},
        ])

    def test_offline_location_defaults_from_resource_group_only_when_omitted(self):
        self.data["mockResources"] = [{
            "id": f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-test",
            "location": "westus",
        }]
        self.assertEqual(self.load().parameters["location"], "eastus")
        del self.data["parameters"]["location"]
        self.assertEqual(self.load().parameters["location"], "westus")
        self.data["mockResources"] = []
        with self.assertRaisesRegex(ValueError, "Missing template parameter: location"):
            self.load()

    def test_offline_image_metadata_resolves_latest_plan_and_default_disk_size(self):
        urn = "Canonical:0001-com-ubuntu-server-jammy:22_04-lts-gen2:latest"
        plan = {"name": "linux-plan", "publisher": "example", "product": "linux"}
        self.data.update(
            compute={"image": "Ubuntu2204"},
            mockResources=[{
                "id": urn, "name": "22.04.20260101", "plan": plan,
                "properties": {"osDiskImage": {"sizeInGb": 64}},
            }],
        )
        parameters = self.load().parameters
        self.assertEqual(parameters["imageReference"]["version"], "22.04.20260101")
        self.assertEqual(parameters["computeOptions"]["plan"], plan)
        self.assertEqual(parameters["osDiskSizeGb"], 64)
        self.data["parameters"]["osDiskSizeGb"] = 128
        self.assertEqual(self.load().parameters["osDiskSizeGb"], 128)

    def test_offline_gallery_version_metadata_and_parameter_image(self):
        image = self.resource("Microsoft.Compute/galleries/images/versions", "gallery/linux/latest")
        image.update(name="1.2.3", properties={"storageProfile": {"osDisk": {"diskSizeGB": 64}}})
        self.data.update(compute={}, mockResources=[image])
        self.data["parameters"]["imageReference"] = {"id": image["id"]}
        parameters = self.load().parameters
        self.assertEqual(parameters["imageReference"], {
            "id": image["id"].removesuffix("latest") + "1.2.3",
        })
        self.assertEqual(parameters["osDiskSizeGb"], 64)

    def test_offline_placement_and_boot_diagnostics_storage(self):
        storage = self.resource("Microsoft.Storage/storageAccounts", "diagnostics")
        storage["properties"] = {"primaryEndpoints": {"blob": "https://dummy.blob.core.windows.net/"}}
        vmss = self.resource("Microsoft.Compute/virtualMachineScaleSets", "flex")
        self.data.update(
            compute={
                "availabilitySet": "shared", "vmss": vmss["id"], "ppg": "nearby",
                "zones": ["1"], "bootDiagnosticsStorage": "diagnostics",
            },
            mockResources=[storage],
        )
        options = self.load().parameters["computeOptions"]
        root = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-test/providers/Microsoft.Compute"
        self.assertEqual(options["availabilitySetId"], root + "/availabilitySets/shared")
        self.assertEqual(options["vmssId"], vmss["id"])
        self.assertEqual(options["ppgId"], root + "/proximityPlacementGroups/nearby")
        self.assertEqual(options["zones"], ["1"])
        self.assertEqual(options["bootDiagnosticsStorageUri"], "https://dummy.blob.core.windows.net/")
        self.data["compute"] = {"bootDiagnosticsStorage": "https://direct.blob.core.windows.net/"}
        self.assertEqual(
            self.load().parameters["computeOptions"]["bootDiagnosticsStorageUri"],
            "https://direct.blob.core.windows.net/",
        )

    def test_offline_identity_and_role_resolution(self):
        role_id = f"/subscriptions/{SUBSCRIPTION}/providers/Microsoft.Authorization/roleDefinitions/{SUBSCRIPTION}"
        self.data["mockResources"] = [{"id": role_id, "properties": {"roleName": "Reader"}}]
        for role in ["Reader", SUBSCRIPTION, role_id]:
            with self.subTest(role=role):
                self.data["compute"] = {"assignIdentity": ["[system]", "application"], "role": role}
                options = self.load().parameters["computeOptions"]
                self.assertEqual(options["identity"], {
                    "type": "SystemAssigned,UserAssigned",
                    "userAssignedIdentities": {
                        f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-test"
                        "/providers/Microsoft.ManagedIdentity/userAssignedIdentities/application": {},
                    },
                })
                self.assertEqual(options["roleAssignment"], {"roleDefinitionId": role_id})
        self.data["compute"] = {"assignIdentity": []}
        self.assertEqual(self.load().parameters["computeOptions"]["identity"], {"type": "SystemAssigned"})

    def test_offline_role_assignment_requires_system_identity_and_unambiguous_role(self):
        cases = [
            {"role": "Reader"},
            {"assignIdentity": ["application"], "role": "Reader"},
            {"assignIdentity": [], "role": "Missing"},
            {"assignIdentity": [], "scope": "/subscriptions/other", "role": SUBSCRIPTION},
        ]
        for compute in cases:
            with self.subTest(compute=compute), self.assertRaises(ValueError):
                self.data["compute"] = compute
                self.load()
        role = {
            "id": f"/subscriptions/{SUBSCRIPTION}/providers/Microsoft.Authorization/roleDefinitions/{SUBSCRIPTION}",
            "properties": {"roleName": "Reader"},
        }
        self.data.update(compute={"assignIdentity": [], "role": "Reader"}, mockResources=[role, role])
        with self.assertRaisesRegex(ValueError, "exactly one"):
            self.load()

    def test_offline_requires_basic_json_shapes(self):
        cases = [
            {"network": []},
            {"compute": []},
            {"mockResources": {}},
            {"mockResources": [{}]},
        ]
        for update in cases:
            with self.subTest(update=update):
                original = self.data
                self.data = {**original, **update}
                try:
                    with self.assertRaises(ValueError):
                        self.load()
                finally:
                    self.data = original

    def test_simulation_blocks_validate_and_deploy_before_compilation_or_authentication(self):
        original = self.data
        for fields in [{"network": {}}, {"compute": {"assignIdentity": []}}, {"mockResources": []}]:
            self.data = {**original, **fields}
            self.load()
            for backend in BackendType:
                for operation in ["validate", "deploy"]:
                    with self.subTest(fields=fields, backend=backend, operation=operation), \
                            patch("vm_create.templates.base.AzureCliToken") as token, \
                            patch("vm_create.templates.bicep.run_process") as compiler, \
                            redirect_stderr(io.StringIO()) as errors:
                        self.assertEqual(main([
                            "--config", str(self.path), "--backend", backend.value,
                            "--operation", operation,
                        ]), 1)
                        self.assertIn("build only", errors.getvalue())
                        compiler.assert_not_called()
                        token.assert_not_called()


class BackendTests(unittest.TestCase):
    def test_factory_defaults_to_bicep_and_accepts_explicit_selection(self):
        for selection in [None, BackendType.BICEP, "bicep"]:
            with self.subTest(selection=selection):
                options = {} if selection is None else {"backend": selection}
                backend = create_backend(**options, bicep_path="custom-bicep")
                self.assertIsInstance(backend, BicepBackend)
                with patch("vm_create.templates.bicep.compile_template", return_value={"resources": []}) as compile_:
                    config = DeploymentConfig(SUBSCRIPTION, "rg-test", "vm-test", {})
                    self.assertEqual(backend.execute(config, "build"), config.payload({"resources": []}))
                    compile_.assert_called_once_with("custom-bicep")

    def test_factory_rejects_unsupported_backend(self):
        with self.assertRaises(ValueError):
            create_backend("api")

    def test_factory_selects_arm_template_from_enum_or_string(self):
        for selection in [BackendType.ARM_TEMPLATE, "arm-template"]:
            with self.subTest(selection=selection):
                self.assertIsInstance(create_backend(selection), ArmTemplateBackend)

    def test_backends_share_validation_and_deployment_execution(self):
        config = DeploymentConfig(SUBSCRIPTION, "rg-test", "vm-test", {})
        deployment_outputs = {
            "vmId": "vm-id",
            "nicId": "nic-id",
            "privateIpAddress": "10.20.0.4",
            "publicIpAddress": "",
            "virtualMachine": {
                "id": "vm-id", "name": "test-vm", "location": "eastus",
                "properties": {
                    "hardwareProfile": {"vmSize": "Standard_D2s_v5"},
                    "storageProfile": {"dataDisks": [{"lun": 0, "diskSizeGB": 32}]},
                },
            },
            "networkInterface": {
                "id": "nic-id",
                "properties": {"ipConfigurations": [{
                    "properties": {"privateIPAddress": "10.20.0.4", "publicIPAddress": None},
                }]},
            },
        }
        for backend_type in BackendType:
            for operation, outputs in [
                ("validate", None),
                ("deploy", {
                    name: {"type": "Object" if isinstance(value, dict) else "String", "value": value}
                    for name, value in deployment_outputs.items()
                }),
            ]:
                backend = create_backend(backend_type)
                with self.subTest(backend=backend_type, operation=operation), \
                        patch.object(backend, "build_template", return_value={"resources": []}), \
                        patch("vm_create.templates.base.AzureCliToken") as token, \
                        patch("vm_create.templates.base.ArmTransport") as transport, \
                        patch("vm_create.templates.base.ArmDeployer") as deployer:
                    deployer.return_value.execute.return_value = {"properties": {"outputs": outputs}}
                    result = backend.execute(config, operation, 42)
                    token.assert_called_once_with(SUBSCRIPTION)
                    transport.assert_called_once_with(token.return_value)
                    deployer.assert_called_once_with(transport.return_value)
                    deployer.return_value.execute.assert_called_once_with(
                        config, config.payload({"resources": []}), operation, 42,
                    )
                    self.assertEqual(result, {
                        "operation": operation, "state": "Succeeded",
                        "outputs": {} if outputs is None else deployment_outputs,
                    })


class NetworkApiTests(unittest.TestCase):
    def test_arm_uses_selected_network_api_for_resources_and_references(self):
        config = DeploymentConfig(SUBSCRIPTION, "rg-test", "vm-test", {"publicIpEnabled": True})
        backend = ArmTemplateBackend()
        original = backend.build_template(config)
        with patch("vm_create.operations.api_versions.get_target_network_api", return_value="2099-01-01") as select:
            template = backend.build_template(config)
        select.assert_called_once_with()
        for resource, previous in zip(template["resources"], original["resources"]):
            if resource["type"].startswith("Microsoft.Network/"):
                self.assertEqual(resource["apiVersion"], select.return_value)
            else:
                self.assertEqual(resource, previous)
        for name in ["privateIpAddress", "publicIpAddress", "networkInterface"]:
            self.assertIn(f"'{select.return_value}'", template["outputs"][name]["value"])
        self.assertEqual(template["outputs"]["virtualMachine"], original["outputs"]["virtualMachine"])

    def test_bicep_compiles_shared_sources_directly_without_api_selection(self):
        template_dir = ROOT / "python" / "vm_create" / "templates" / "bicep"
        originals = {
            source.name: source.read_text(encoding="utf-8")
            for source in template_dir.glob("*.bicep")
        }
        config = DeploymentConfig(SUBSCRIPTION, "rg-test", "vm-test", {})
        for fail in [False, True]:
            with self.subTest(compiler_failure=fail), \
                    patch("vm_create.operations.api_versions.get_target_network_api",
                          side_effect=AssertionError("Bicep must use its authored API versions")) as select:

                def compile_(executable, arguments):
                    self.assertEqual(executable, "custom-bicep")
                    self.assertEqual(arguments[0], "build")
                    self.assertEqual(arguments[2:], ["--stdout"])
                    entry = Path(arguments[1])
                    self.assertEqual(entry, template_dir / "main.bicep")
                    for name in originals:
                        self.assertEqual((entry.parent / name).read_text(encoding="utf-8"), originals[name])
                    if fail:
                        raise ValueError("compiler failed")
                    return '{"resources": []}'

                with patch("vm_create.templates.bicep.run_process", side_effect=compile_) as compiler:
                    if fail:
                        with self.assertRaisesRegex(ValueError, "compiler failed"):
                            BicepBackend("custom-bicep").build_template(config)
                    else:
                        self.assertEqual(BicepBackend("custom-bicep").build_template(config), {"resources": []})
                select.assert_not_called()
                compiler.assert_called_once()
                for name, text in originals.items():
                    self.assertEqual((template_dir / name).read_text(encoding="utf-8"), text)


class ArmTemplateTests(unittest.TestCase):
    def build(self, **parameters):
        config = DeploymentConfig(SUBSCRIPTION, "rg-test", "vm-test", {
            "vmName": "test-vm", "location": "eastus",
            "adminUsername": "azureuser", "sshPublicKey": PUBLIC_KEY, **parameters,
        })
        payload = create_backend(BackendType.ARM_TEMPLATE).execute(config, "build")
        self.template = payload["properties"]["template"]
        self.resources = {
            resource["type"]: resource for resource in self.template["resources"]
        }
        return payload

    def test_outputs_include_full_resource_objects_and_preserve_summary_fields(self):
        self.build()
        outputs = self.template["outputs"]
        self.assertTrue({
            "vmId", "nicId", "privateIpAddress", "publicIpAddress",
        }.issubset(outputs))
        for name, resource in [
            ("virtualMachine", "Microsoft.Compute/virtualMachines"),
            ("networkInterface", "variables('nicId')"),
        ]:
            with self.subTest(output=name):
                self.assertEqual(outputs[name]["type"], "object")
                self.assertIn("reference(", outputs[name]["value"])
                self.assertIn(resource, outputs[name]["value"])
                self.assertIn("'Full'", outputs[name]["value"])

    def test_network_resources_and_dependencies_follow_configuration(self):
        for existing_subnet in ["", f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg"
                                "/providers/Microsoft.Network/virtualNetworks/vnet/subnets/default"]:
            for public_ip in [False, True]:
                with self.subTest(existing_subnet=bool(existing_subnet), public_ip=public_ip):
                    self.build(
                        existingSubnetId=existing_subnet, publicIpEnabled=public_ip,
                        sshSourceCidr="203.0.113.4/32" if public_ip else "",
                    )
                    self.assertEqual(
                        "Microsoft.Network/virtualNetworks" in self.resources, not bool(existing_subnet),
                    )
                    self.assertEqual("Microsoft.Network/publicIPAddresses" in self.resources, public_ip)
                    nic = self.resources["Microsoft.Network/networkInterfaces"]
                    self.assertIn(
                        "[resourceId('Microsoft.Network/networkSecurityGroups', variables('nsgName'))]",
                        nic["dependsOn"],
                    )
                    self.assertEqual(
                        "[resourceId('Microsoft.Network/virtualNetworks', variables('vnetName'))]"
                        in nic["dependsOn"], not bool(existing_subnet),
                    )
                    self.assertEqual(
                        "[resourceId('Microsoft.Network/publicIPAddresses', variables('publicIpName'))]"
                        in nic["dependsOn"], public_ip,
                    )
                    ip_config = nic["properties"]["ipConfigurations"][0]["properties"]
                    if existing_subnet:
                        self.assertEqual(ip_config["subnet"]["id"], "[parameters('existingSubnetId')]")
                    else:
                        self.assertIn("virtualNetworks/subnets", ip_config["subnet"]["id"])
                    nsg = self.resources["Microsoft.Network/networkSecurityGroups"]
                    rules = nsg["properties"]["securityRules"]
                    if public_ip:
                        self.assertEqual(ip_config["publicIPAddress"], {"id": "[variables('publicIpId')]"})
                        self.assertEqual(rules[0]["properties"]["sourceAddressPrefix"], "[parameters('sshSourceCidr')]")
                        self.assertEqual(rules[0]["properties"]["destinationPortRange"], "22")
                        self.assertIn("reference(", self.template["outputs"]["publicIpAddress"]["value"])
                    else:
                        self.assertIsNone(ip_config["publicIPAddress"])
                        self.assertEqual(rules, [])
                        self.assertEqual(self.template["outputs"]["publicIpAddress"]["value"], "")
                    vm = self.resources["Microsoft.Compute/virtualMachines"]
                    self.assertEqual(vm["dependsOn"], [
                        "[resourceId('Microsoft.Network/networkInterfaces', variables('nicName'))]",
                    ])
                    self.assertEqual(vm["properties"]["networkProfile"]["networkInterfaces"][0], {
                        "id": "[variables('nicId')]",
                        "properties": {"primary": True, "deleteOption": "Delete"},
                    })

    def test_vm_preserves_custom_parameters_disks_and_security(self):
        image = {"publisher": "example", "offer": "linux", "sku": "gen2", "version": "1.0"}
        payload = self.build(
            dataDiskSizesGb=[32, 64], osDiskSizeGb=128,
            storageSku="StandardSSD_LRS", vmSize="Standard_D4s_v5", imageReference=image,
        )
        parameters = payload["properties"]["parameters"]
        self.assertEqual(parameters["dataDiskSizesGb"]["value"], [32, 64])
        self.assertEqual(parameters["imageReference"]["value"], image)
        properties = self.resources["Microsoft.Compute/virtualMachines"]["properties"]
        self.assertEqual(properties["hardwareProfile"]["vmSize"], "[parameters('vmSize')]")
        storage = properties["storageProfile"]
        self.assertEqual(storage["imageReference"], "[parameters('imageReference')]")
        self.assertEqual(storage["osDisk"]["diskSizeGB"], "[parameters('osDiskSizeGb')]")
        self.assertEqual(storage["osDisk"]["deleteOption"], "Delete")
        self.assertEqual([disk["lun"] for disk in storage["dataDisks"]], [0, 1])
        for index, disk in enumerate(storage["dataDisks"]):
            self.assertEqual(disk["diskSizeGB"], f"[parameters('dataDiskSizesGb')[{index}]]")
            self.assertEqual(disk["deleteOption"], "Detach")
            self.assertEqual(disk["managedDisk"], {"storageAccountType": "[parameters('storageSku')]"})
        linux = properties["osProfile"]["linuxConfiguration"]
        self.assertTrue(linux["disablePasswordAuthentication"])
        self.assertEqual(linux["ssh"]["publicKeys"][0]["keyData"], "[parameters('sshPublicKey')]")
        self.assertEqual(properties["securityProfile"], {
            "securityType": "TrustedLaunch",
            "uefiSettings": {"secureBootEnabled": True, "vTpmEnabled": True},
        })
        self.assertTrue(properties["diagnosticsProfile"]["bootDiagnostics"]["enabled"])

    def test_builds_do_not_share_mutable_template_state(self):
        self.build(publicIpEnabled=True, dataDiskSizesGb=[32])
        self.template["parameters"]["imageReference"]["defaultValue"]["publisher"] = "changed"
        self.build()
        self.assertEqual(
            self.resources["Microsoft.Compute/virtualMachines"]["properties"]["storageProfile"]["dataDisks"], [],
        )
        self.assertNotIn("Microsoft.Network/publicIPAddresses", self.resources)
        self.assertEqual(
            self.template["parameters"]["imageReference"]["defaultValue"]["publisher"], "Canonical",
        )


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.config = DeploymentConfig(SUBSCRIPTION, "rg-test", "vm-test", {})

    def execute(self, responses, operation="deploy", **kwargs):
        self.calls = []
        iterator = iter(responses)

        def transport(method, url, payload=None):
            self.calls.append((method, url, payload))
            return next(iterator)

        return ArmDeployer(transport, sleep=lambda _: None, **kwargs).execute(
            self.config, {"properties": {}}, operation,
        )

    def test_deploy_polls_async_operation_then_fetches_outputs(self):
        url = "https://management.azure.com/operations/test"
        result = self.execute([
            Response(202, {"azure-asyncoperation": url, "retry-after": "0"}, {}),
            Response(200, {"retry-after": "0"}, {"status": "Running"}),
            Response(200, {}, {"status": "Succeeded"}),
            Response(200, {}, {"properties": {
                "provisioningState": "Succeeded", "outputs": {"vmId": {"value": "vm-id"}},
            }}),
        ])
        self.assertEqual([call[0] for call in self.calls], ["PUT", "GET", "GET", "GET"])
        self.assertEqual(self.calls[1][1], url)
        self.assertEqual(result["properties"]["outputs"]["vmId"]["value"], "vm-id")

    def test_deploy_without_poll_header_polls_deployment(self):
        result = self.execute([
            Response(201, {"retry-after": "0"}, {"properties": {"provisioningState": "Accepted"}}),
            Response(200, {}, {"properties": {"provisioningState": "Succeeded"}}),
        ])
        self.assertEqual(self.calls[0][1], self.calls[1][1])
        self.assertEqual(result["properties"]["provisioningState"], "Succeeded")

    def test_validation_can_be_synchronous_or_asynchronous(self):
        self.execute([Response(200, {}, {"properties": {}})], "validate")
        self.assertEqual(self.calls[0][0], "POST")
        self.assertIn("/validate?", self.calls[0][1])
        result = self.execute([
            Response(202, {"location": "https://management.azure.com/operations/check", "retry-after": "0"}, {}),
            Response(200, {}, {"status": "Succeeded"}),
        ], "validate")
        self.assertEqual(result["status"], "Succeeded")
        with self.assertRaisesRegex(ValueError, "failed"):
            self.execute([
                Response(200, {"azure-asyncoperation": "https://management.azure.com/operations/check", "retry-after": "0"}, {}),
                Response(200, {}, {"status": "Failed"}),
            ], "validate")

    def test_failed_or_canceled_operation_is_not_success(self):
        for state in ["Failed", "Canceled"]:
            with self.subTest(state=state), self.assertRaisesRegex(ValueError, "failed"):
                self.execute([Response(200, {}, {"status": state})])
        with self.assertRaisesRegex(ValueError, "InvalidTemplate"):
            self.execute([Response(200, {}, {"error": {"code": "InvalidTemplate"}})], "validate")

    def test_timeout_does_not_report_success(self):
        with self.assertRaises(TimeoutError):
            self.execute([Response(202, {}, {})], clock=iter([0, 1801]).__next__)

    def test_token_not_sent_to_foreign_polling_host(self):
        token = unittest.mock.Mock(return_value="test-token")
        with self.assertRaisesRegex(ValueError, "outside"):
            ArmTransport(token)("GET", "https://example.com/operations/test")
        token.assert_not_called()


if __name__ == "__main__":
    unittest.main()
