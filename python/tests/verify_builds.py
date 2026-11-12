"""Check Bicep parity and Python-generated ARM JSON without Azure calls."""

import argparse
import base64
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from vm_create.config import load_config
from vm_create.factories.backend import create_backend

ROOT = Path(__file__).resolve().parents[2]


def run(command, cwd):
    result = subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    if result.returncode:
        raise RuntimeError(f"Command failed: {command[0]}\n{result.stderr}\n{result.stdout}")
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bicep-path", required=True)
    parser.add_argument("--dotnet-path", default="dotnet")
    args = parser.parse_args()
    bicep = str(Path(shutil.which(args.bicep_path) or args.bicep_path).resolve())
    dotnet = str(Path(shutil.which(args.dotnet_path) or args.dotnet_path).resolve())
    run([
        dotnet, "build", str(ROOT / "csharp" / "VmCreate"), "--nologo", "-p:NuGetAudit=false",
    ], ROOT)
    dll = ROOT / "csharp" / "VmCreate" / "bin" / "Debug" / "net10.0" / "VmCreate.dll"
    work = ROOT / ".tools"
    work.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work) as directory:
        directory = Path(directory)
        key_type = b"ssh-ed25519"
        blob = len(key_type).to_bytes(4, "big") + key_type + (32).to_bytes(4, "big") + bytes(range(32))
        (directory / "key.pub").write_text(
            "ssh-ed25519 " + base64.b64encode(blob).decode("ascii"), encoding="utf-8",
        )
        base = {
            "subscriptionId": "11111111-1111-1111-1111-111111111111",
            "resourceGroup": "rg-offline",
            "deploymentName": "vm-offline",
            "sshPublicKeyPath": "key.pub",
            "parameters": {
                "vmName": "offline-vm", "location": "eastus", "adminUsername": "azureuser",
            },
        }
        cases = {
            "new-private-network": {},
            "new-data-disks": {"dataDiskSizesGb": [32, 64], "storageSku": "StandardSSD_LRS"},
            "new-network-public-ip": {
                "publicIpEnabled": True, "sshSourceCidr": "203.0.113.4/32",
            },
            "existing-subnet-private": {
                "existingSubnetId": "/subscriptions/11111111-1111-1111-1111-111111111111"
                "/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/vnet/subnets/default",
            },
            "existing-subnet-public-ip": {
                "publicIpEnabled": True, "sshSourceCidr": "203.0.113.4/32",
                "existingSubnetId": "/subscriptions/11111111-1111-1111-1111-111111111111"
                "/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/vnet/subnets/default",
            },
        }
        for name, updates in cases.items():
            config = {**base, "parameters": {**base["parameters"], **updates}}
            path = directory / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            options = ["--config", str(path), "--bicep-path", bicep]
            python = json.loads(run([sys.executable, "-m", "vm_create", *options], ROOT / "python"))
            csharp = json.loads(run([dotnet, str(dll), *options], ROOT))
            if python != csharp:
                raise AssertionError(f"Deployment payload mismatch: {name}")
            resources = python["properties"]["template"]["resources"]
            if isinstance(resources, dict):
                resources = list(resources.values())
            if len(resources) != 2 or not all(
                resource["type"] == "Microsoft.Resources/deployments" for resource in resources
            ):
                raise AssertionError("Expected network and compute module deployments.")
            if "sshPublicKeyPath" in python["properties"]["parameters"]:
                raise AssertionError("Local key path leaked into deployment parameters.")
            print(f"PASS {name}: equivalent Python/C# payloads")
            arm = json.loads(run([
                sys.executable, "-m", "vm_create", *options, "--backend", "arm-template",
            ], ROOT / "python"))
            if arm["properties"]["parameters"] != python["properties"]["parameters"]:
                raise AssertionError(f"Backend parameter mismatch: {name}")
            arm_template = arm["properties"]["template"]
            bicep_parameters = {
                key: {field: value for field, value in definition.items() if field != "items"}
                for key, definition in python["properties"]["template"]["parameters"].items()
            }
            if arm_template["parameters"] != bicep_parameters:
                raise AssertionError(f"Backend parameter defaults or constraints differ: {name}")
            if arm_template["outputs"].keys() != python["properties"]["template"]["outputs"].keys():
                raise AssertionError(f"Backend output names differ: {name}")
            bicep_outputs = python["properties"]["template"]["outputs"]
            for output_name in ["virtualMachine", "networkInterface"]:
                for template in [arm_template, python["properties"]["template"]]:
                    if template["outputs"].get(output_name, {}).get("type") != "object":
                        raise AssertionError(f"Missing resource object output: {output_name}")
                if f".outputs.{output_name}.value" not in bicep_outputs[output_name]["value"]:
                    raise AssertionError(f"Bicep must forward the module resource output: {output_name}")
                module_outputs = [
                    resource["properties"]["template"]["outputs"][output_name]
                    for resource in resources
                    if output_name in resource["properties"]["template"]["outputs"]
                ]
                if len(module_outputs) != 1:
                    raise AssertionError(f"Expected one Bicep module producing {output_name}")
                for definition in [module_outputs[0], arm_template["outputs"][output_name]]:
                    expression = definition["value"].lower()
                    if "reference(" not in expression or "'full'" not in expression:
                        raise AssertionError(f"Resource output must use a full ARM reference: {output_name}")
            if any(resource["type"] == "Microsoft.Resources/deployments" for resource in arm_template["resources"]):
                raise AssertionError("ARM-template backend must build flat resources.")
            arm_path = directory / f"{name}.json"
            arm_path.write_text(json.dumps(arm_template), encoding="utf-8")
            decompiled = directory / f"{name}.bicep"
            run([bicep, "decompile", str(arm_path), "--outfile", str(decompiled)], ROOT)
            run([bicep, "build", str(decompiled), "--stdout"], ROOT)
            print(f"PASS {name}: Python ARM JSON decompiles and compiles; parameters match Bicep")
        path.write_text(json.dumps({
            **base, "parameters": {**base["parameters"], **cases["new-network-public-ip"]},
        }), encoding="utf-8")
        config = load_config(path)
        with patch("vm_create.operations.api_versions.get_target_network_api", return_value="2022-01-01") as select:
            template = create_backend("arm-template").build_template(config)
            for resource in template["resources"]:
                if resource["type"].startswith("Microsoft.Network/"):
                    if resource["apiVersion"] != select.return_value:
                        raise AssertionError("ARM network resource ignored API selection.")
            for name in ["privateIpAddress", "publicIpAddress", "networkInterface"]:
                if f"'{select.return_value}'" not in template["outputs"][name]["value"]:
                    raise AssertionError(f"ARM network output ignored API selection: {name}")
            print("PASS arm-template: selected network API reaches resources and references")
    print("All offline template checks passed. No Azure operations performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
