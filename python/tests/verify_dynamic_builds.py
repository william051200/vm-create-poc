"""Compile offline VM resource-choice scenarios through both Python backends."""

import argparse
import base64
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from vm_create.config import load_config
from vm_create.factories.backend import create_backend


SUBSCRIPTION = "11111111-1111-1111-1111-111111111111"
NETWORK = "Microsoft.Network/"
COMPUTE = "Microsoft.Compute/"


def resource(resource_type, name, **properties):
    parts = resource_type.split("/")
    suffix = "/".join(part for pair in zip(parts[1:], name.split("/")) for part in pair)
    return {
        "id": f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-offline/providers/{parts[0]}/{suffix}",
        "location": "eastus",
        "properties": properties,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bicep-path", required=True)
    args = parser.parse_args()
    bicep = str(Path(shutil.which(args.bicep_path) or args.bicep_path).resolve())
    vnet = resource(
        NETWORK + "virtualNetworks", "shared",
        addressSpace={"addressPrefixes": ["10.40.0.0/16"]},
        subnets=[{"name": "apps", "properties": {"addressPrefix": "10.40.0.0/24"}}],
    )
    nsg = resource(NETWORK + "networkSecurityGroups", "shared-nsg")
    pip = resource(NETWORK + "publicIPAddresses", "shared-pip")
    nics = [resource(NETWORK + "networkInterfaces", name) for name in ["nic1", "nic2"]]
    disk = resource(COMPUTE + "disks", "data", diskState="Unattached")
    image = resource(COMPUTE + "images", "linux", osType="Linux")
    group = {"id": f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg-offline", "location": "westus"}
    image_metadata = {
        "id": "Example:linux:server:latest", "name": "1.2.3",
        "plan": {"name": "server", "publisher": "Example", "product": "linux"},
        "properties": {"osDiskImage": {"sizeInGb": 64}},
    }
    gallery = resource(COMPUTE + "galleries/images/versions", "gallery/linux/latest")
    gallery["name"] = "1.2.3"
    diagnostics = resource(
        "Microsoft.Storage/storageAccounts", "diagnostics",
        primaryEndpoints={"blob": "https://dummy.blob.core.windows.net/"},
    )
    role = {
        "id": f"/subscriptions/{SUBSCRIPTION}/providers/Microsoft.Authorization/roleDefinitions/{SUBSCRIPTION}",
        "properties": {"roleName": "Reader"},
    }
    cases = [
        ("legacy-private-network", {}, {
            NETWORK + "virtualNetworks", NETWORK + "networkSecurityGroups", NETWORK + "networkInterfaces",
        }),
        ("legacy-public-network", {"parameters": {"publicIpEnabled": True}}, {
            NETWORK + "virtualNetworks", NETWORK + "networkSecurityGroups",
            NETWORK + "publicIPAddresses", NETWORK + "networkInterfaces",
        }),
        ("custom-public-network", {"network": {"publicIp": "chosen-pip", "nsg": "chosen-nsg"}}, {
            NETWORK + "virtualNetworks", NETWORK + "networkSecurityGroups",
            NETWORK + "publicIPAddresses", NETWORK + "networkInterfaces",
        }),
        ("custom-new-network", {
            "network": {
                "vnet": "custom", "subnet": "apps", "nsg": "", "publicIp": "",
                "vnetAddressPrefix": "10.30.0.0/16", "subnetAddressPrefix": "10.30.1.0/24",
                "privateIpAddress": "10.30.1.8",
            },
        }, {NETWORK + "virtualNetworks", NETWORK + "networkInterfaces"}),
        ("discovered-subnet", {"network": {}, "mockResources": [vnet]}, {
            NETWORK + "networkSecurityGroups", NETWORK + "networkInterfaces",
        }),
        ("new-subnet-existing-vnet", {
            "network": {"vnet": "shared", "subnet": "new", "subnetAddressPrefix": "10.40.1.0/24"},
            "mockResources": [vnet],
        }, {NETWORK + "virtualNetworks/subnets", NETWORK + "networkSecurityGroups",
            NETWORK + "networkInterfaces"}),
        ("existing-nsg-public-ip", {
            "network": {"nsg": nsg["id"], "publicIp": pip["id"]}, "mockResources": [nsg, pip],
        }, {NETWORK + "virtualNetworks", NETWORK + "networkInterfaces"}),
        ("existing-nics", {
            "network": {"nics": ["nic1", nics[1]["id"]]}, "mockResources": nics,
        }, set()),
        ("existing-image-data-disk", {
            "compute": {"image": image["id"], "attachDataDisks": ["data"]},
            "mockResources": [image, disk],
        }, {NETWORK + "virtualNetworks", NETWORK + "networkSecurityGroups", NETWORK + "networkInterfaces"}),
        ("marketplace-urn", {"compute": {"image": "Canonical:offer:sku:latest"}}, {
            NETWORK + "virtualNetworks", NETWORK + "networkSecurityGroups", NETWORK + "networkInterfaces",
        }),
        ("image-alias", {"compute": {"image": "Ubuntu2204"}}, {
            NETWORK + "virtualNetworks", NETWORK + "networkSecurityGroups", NETWORK + "networkInterfaces",
        }),
        ("resource-group-location", {
            "parameters": {"location": None}, "mockResources": [group],
        }, {NETWORK + "virtualNetworks", NETWORK + "networkSecurityGroups", NETWORK + "networkInterfaces"}),
        ("image-version-plan-size", {
            "compute": {"image": image_metadata["id"]}, "mockResources": [image_metadata],
        }, {NETWORK + "virtualNetworks", NETWORK + "networkSecurityGroups", NETWORK + "networkInterfaces"}),
        ("gallery-latest-version", {
            "compute": {"image": gallery["id"]}, "mockResources": [gallery],
        }, {NETWORK + "virtualNetworks", NETWORK + "networkSecurityGroups", NETWORK + "networkInterfaces"}),
        ("existing-availability-set-ppg", {
            "compute": {"availabilitySet": "shared", "ppg": "nearby"},
        }, {NETWORK + "virtualNetworks", NETWORK + "networkSecurityGroups", NETWORK + "networkInterfaces"}),
        ("existing-vmss-zone", {
            "compute": {"vmss": "flex", "zones": ["1"]},
        }, {NETWORK + "virtualNetworks", NETWORK + "networkSecurityGroups", NETWORK + "networkInterfaces"}),
        ("boot-diagnostics-storage", {
            "compute": {"bootDiagnosticsStorage": "diagnostics"}, "mockResources": [diagnostics],
        }, {NETWORK + "virtualNetworks", NETWORK + "networkSecurityGroups", NETWORK + "networkInterfaces"}),
        ("system-identity-role", {
            "compute": {"assignIdentity": ["[system]"], "role": "Reader"}, "mockResources": [role],
        }, {NETWORK + "virtualNetworks", NETWORK + "networkSecurityGroups", NETWORK + "networkInterfaces",
            "Microsoft.Authorization/roleAssignments"}),
        ("user-assigned-identity", {
            "compute": {"assignIdentity": ["application"]},
        }, {NETWORK + "virtualNetworks", NETWORK + "networkSecurityGroups", NETWORK + "networkInterfaces"}),
    ]
    with tempfile.TemporaryDirectory(prefix="vm-create-proof-") as work:
        work = Path(work)
        key_type = b"ssh-ed25519"
        blob = len(key_type).to_bytes(4, "big") + key_type + (32).to_bytes(4, "big") + bytes(range(32))
        (work / "key.pub").write_text("ssh-ed25519 " + base64.b64encode(blob).decode("ascii"), encoding="utf-8")
        for name, updates, expected in cases:
            data = {
                "subscriptionId": SUBSCRIPTION, "resourceGroup": "rg-offline",
                "deploymentName": "offline", "sshPublicKeyPath": "key.pub",
                "parameters": {
                    "vmName": "offline-vm", "location": "eastus", "adminUsername": "azureuser",
                    "dataDiskSizesGb": [32], "sshSourceCidr": "203.0.113.4/32",
                    **updates.get("parameters", {}),
                },
                **{key: value for key, value in updates.items() if key != "parameters"},
            }
            data["parameters"] = {key: value for key, value in data["parameters"].items() if value is not None}
            path = work / "config.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with patch("vm_create.templates.base.AzureCliToken", side_effect=AssertionError("Unexpected authentication")), \
                    patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Unexpected HTTP request")):
                config = load_config(path)
                compiled = create_backend("bicep", bicep_path=bicep).execute(config, "build")["properties"]
                arm = create_backend("arm-template").execute(config, "build")["properties"]
            if compiled["parameters"] != arm["parameters"]:
                raise AssertionError(f"Resolved parameters differ: {name}")
            declarations = {
                key: {field: value for field, value in definition.items() if field != "items"}
                for key, definition in compiled["template"]["parameters"].items()
            }
            if declarations != arm["template"]["parameters"]:
                raise AssertionError(f"Template parameter declarations differ: {name}")
            actual = {item["type"] for item in arm["template"]["resources"]}
            if actual != expected | {COMPUTE + "virtualMachines"}:
                raise AssertionError(f"Unexpected resources for {name}: {actual}")
            if compiled["template"]["outputs"].keys() != arm["template"]["outputs"].keys():
                raise AssertionError(f"Backend outputs differ: {name}")
            modules = compiled["template"]["resources"]
            if isinstance(modules, dict):
                modules = modules.values()
            compute_template = next(
                module["properties"]["template"] for module in modules
                if "virtualMachine" in module["properties"]["template"]["outputs"]
            )
            compute_resources = compute_template["resources"]
            if isinstance(compute_resources, dict):
                compute_resources = list(compute_resources.values())
            compiled_vm = next(
                item for item in compute_resources if item["type"] == COMPUTE + "virtualMachines"
            )
            options = config.parameters.get("computeOptions", {})
            for field in ("identity", "plan", "zones"):
                if field in options and f"'{field}'" not in compiled_vm[field]:
                    raise AssertionError(f"Bicep does not forward VM {field}: {name}")
            for option, field in [
                ("availabilitySetId", "availabilitySet"),
                ("vmssId", "virtualMachineScaleSet"), ("ppgId", "proximityPlacementGroup"),
            ]:
                if option in options and option not in json.dumps(compiled_vm["properties"][field]):
                    raise AssertionError(f"Bicep does not forward VM {field}: {name}")
            if "bootDiagnosticsStorageUri" in options:
                storage_uri = compiled_vm["properties"]["diagnosticsProfile"]["bootDiagnostics"]["storageUri"]
                if "bootDiagnosticsStorageUri" not in storage_uri:
                    raise AssertionError(f"Bicep does not forward boot diagnostics storage: {name}")
            if "roleAssignment" in options:
                role_module = next(
                    item for item in compute_resources if item["type"] == "Microsoft.Resources/deployments"
                )
                principal = role_module["properties"]["parameters"]["principalId"]
                if isinstance(principal, dict):
                    principal = principal["value"]
                if not role_module.get("condition") or "reference(" not in principal or ".identity.principalId" not in principal:
                    raise AssertionError(f"Bicep role assignment must use the VM runtime identity: {name}")
            source = work / f"{name}.json"
            source.write_text(json.dumps(arm["template"]), encoding="utf-8")
            decompiled = work / f"{name}.bicep"
            for arguments in [
                ["decompile", str(source), "--outfile", str(decompiled)],
                ["build", str(decompiled), "--stdout"],
            ]:
                result = subprocess.run(
                    [bicep, *arguments], capture_output=True, text=True, encoding="utf-8", timeout=120,
                )
                if result.returncode:
                    raise AssertionError(f"{name}: {result.stderr}\n{result.stdout}")
            print(f"PASS {name}: Bicep compiled; ARM resource choices and round-trip compilation passed")
    print("All dynamic build checks passed. No authentication or Azure API calls.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
