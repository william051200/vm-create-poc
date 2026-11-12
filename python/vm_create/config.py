import base64
import ipaddress
import json
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from .operations.compute import resolve_compute
from .operations.network import resolve_network
from .operations.resource_group import resolve_resource_group


@dataclass(frozen=True)
class DeploymentConfig:
    subscription_id: str
    resource_group: str
    deployment_name: str
    parameters: dict
    simulated: bool = False

    def payload(self, template):
        return {
            "properties": {
                "mode": "Incremental",
                "template": template,
                "parameters": {
                    name: {"value": value} for name, value in self.parameters.items()
                },
            }
        }


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def load_config(path):
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    _require(isinstance(data, dict), "Configuration must be a JSON object.")
    allowed = {
        "subscriptionId", "resourceGroup", "deploymentName",
        "sshPublicKeyPath", "parameters", "network", "compute", "mockResources",
    }
    _require(not (data.keys() - allowed), "Unknown configuration field.")
    for name in {"subscriptionId", "resourceGroup", "deploymentName", "sshPublicKeyPath", "parameters"}:
        _require(name in data, f"Missing configuration field: {name}")
    try:
        _require(
            isinstance(data["subscriptionId"], str)
            and re.fullmatch(
                r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", data["subscriptionId"],
            ),
            "subscriptionId must be a UUID.",
        )
        subscription = str(UUID(data["subscriptionId"]))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("subscriptionId must be a UUID.") from error
    for name, limit in [("resourceGroup", 90), ("deploymentName", 64)]:
        value = data[name]
        _require(
            isinstance(value, str)
            and re.fullmatch(r"[A-Za-z0-9_.()-]{1," + str(limit) + "}", value)
            and not value.endswith("."),
            f"{name} has an invalid name.",
        )
    parameters = data["parameters"]
    _require(isinstance(parameters, dict), "parameters must be an object.")
    simulated = bool({"network", "compute", "mockResources"} & data.keys())
    resources = data.get("mockResources", [])
    if simulated:
        _require(
            isinstance(resources, list) and all(
                isinstance(resource, dict) and isinstance(resource.get("id"), str)
                for resource in resources
            ),
            "mockResources must be an array of objects with resource IDs.",
        )
        for name in {"network", "compute"} & data.keys():
            _require(isinstance(data[name], dict), f"{name} must be an object.")
        parameters = {**parameters, **resolve_resource_group(
            parameters, subscription, data["resourceGroup"], resources,
        )}
    string_fields = {
        "vmName", "location", "adminUsername", "vmSize", "storageSku",
        "sshSourceCidr", "existingSubnetId",
    }
    allowed_parameters = string_fields | {
        "osDiskSizeGb", "dataDiskSizesGb", "publicIpEnabled", "imageReference",
    }
    _require(
        not (parameters.keys() - allowed_parameters),
        "Unknown template parameter; supply the SSH key through sshPublicKeyPath.",
    )
    for name in ["vmName", "location", "adminUsername"]:
        _require(name in parameters, f"Missing template parameter: {name}")
    for name in string_fields & parameters.keys():
        _require(isinstance(parameters[name], str), f"{name} must be a string.")
    _require(
        re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,55}", parameters["vmName"])
        and not parameters["vmName"].endswith("-"),
        "vmName must start with a letter, contain letters/digits/hyphens, "
        "not end with a hyphen, and be at most 56 characters.",
    )
    _require(bool(parameters["location"].strip()), "location cannot be empty.")
    _require(
        re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", parameters["adminUsername"])
        and parameters["adminUsername"] not in {"root", "admin", "administrator"},
        "adminUsername must be a non-reserved Linux username, at most 32 characters.",
    )
    if "vmSize" in parameters:
        _require(bool(parameters["vmSize"].strip()), "vmSize cannot be empty.")
    if "storageSku" in parameters:
        _require(
            parameters["storageSku"] in {"Standard_LRS", "StandardSSD_LRS", "Premium_LRS"},
            "Unsupported storageSku.",
        )
    if "osDiskSizeGb" in parameters:
        _require(
            type(parameters["osDiskSizeGb"]) is int
            and 0 < parameters["osDiskSizeGb"] <= 2147483647,
            "osDiskSizeGb must be a positive integer.",
        )
    disks = parameters.get("dataDiskSizesGb", [])
    _require(
        isinstance(disks, list) and len(disks) <= 64
        and all(type(size) is int and 0 < size <= 2147483647 for size in disks),
        "dataDiskSizesGb must contain at most 64 positive integers.",
    )
    public_ip = parameters.get("publicIpEnabled", False)
    _require(type(public_ip) is bool, "publicIpEnabled must be a boolean.")
    cidr = parameters.get("sshSourceCidr", "")
    if cidr:
        try:
            network = ipaddress.ip_network(cidr, strict=True)
        except ValueError as error:
            raise ValueError("sshSourceCidr must be a canonical IPv4 CIDR.") from error
        _require(
            network.version == 4 and network.prefixlen > 0 and str(network) == cidr,
            "sshSourceCidr must be IPv4 and cannot allow the entire Internet.",
        )
    _require(not public_ip or bool(cidr), "Public IP requires sshSourceCidr.")
    subnet = parameters.get("existingSubnetId", "")
    if subnet:
        pattern = (
            rf"/subscriptions/{re.escape(subscription)}/resourceGroups/[^/]+"
            r"/providers/Microsoft.Network/virtualNetworks/[^/]+/subnets/[^/]+"
        )
        _require(
            re.fullmatch(pattern, subnet, re.IGNORECASE),
            "existingSubnetId must be a subnet ID in the configured subscription.",
        )
    if "imageReference" in parameters:
        image = parameters["imageReference"]
        _require(
            isinstance(image, dict)
            and image.keys() in ({"publisher", "offer", "sku", "version"}, {"id"})
            and all(isinstance(value, str) and value.strip() for value in image.values()),
            "imageReference requires publisher, offer, sku, and version, or an image resource id.",
        )
        if "id" in image:
            _require(
                re.fullmatch(
                    rf"/subscriptions/{re.escape(subscription)}/resourceGroups/[^/]+"
                    r"/providers/Microsoft.Compute/(?:images/[^/]+|galleries/[^/]+/images/[^/]+/versions/[^/]+)",
                    image["id"], re.IGNORECASE,
                ),
                "imageReference.id must reference a managed image or gallery image version.",
            )
    if simulated:
        if "network" in data or "mockResources" in data:
            parameters["networkOptions"] = resolve_network(
                data.get("network", {}), parameters, subscription, data["resourceGroup"], resources,
            )
            network = parameters["networkOptions"]
            _require(
                network["publicIpMode"] == "none" or network["nsgMode"] != "new" or bool(cidr),
                "Public IP with a new NSG requires sshSourceCidr.",
            )
        if "compute" in data:
            parameters.update(resolve_compute(
                data["compute"], parameters, subscription, data["resourceGroup"], resources,
            ))
    key_path = data["sshPublicKeyPath"]
    _require(isinstance(key_path, str) and key_path.strip(), "sshPublicKeyPath is required.")
    key = (path.parent / key_path).read_text(encoding="utf-8-sig").strip()
    parts = key.split()
    _require(
        "\n" not in key and "\r" not in key and len(parts) >= 2
        and parts[0] in {"ssh-rsa", "ssh-ed25519"},
        "Supply a single-line RSA or Ed25519 SSH public key, never a private key.",
    )
    try:
        decoded = base64.b64decode(parts[1], validate=True)
    except ValueError as error:
        raise ValueError("SSH public key is not valid base64.") from error
    key_type = parts[0].encode("ascii")
    _require(
        len(decoded) > 4 + len(key_type)
        and int.from_bytes(decoded[:4], "big") == len(key_type)
        and decoded[4:4 + len(key_type)] == key_type,
        "SSH public key has an invalid key type header.",
    )
    parameters = {**parameters, "sshPublicKey": key}
    return DeploymentConfig(
        subscription, data["resourceGroup"], data["deploymentName"], parameters, simulated,
    )
