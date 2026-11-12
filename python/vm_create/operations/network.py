def resolve_network(options, parameters, subscription, group, resources):
    root = f"/subscriptions/{subscription}/resourceGroups/{group}/providers/Microsoft.Network"
    vm_name = parameters["vmName"]
    inventory = {resource["id"].lower(): resource for resource in resources}
    for resource in resources:
        for subnet in resource.get("properties", {}).get("subnets", []):
            identity = resource["id"] + "/subnets/" + subnet["name"]
            inventory[identity.lower()] = {**subnet, "id": identity}
    resolved = {
        "createVnet": False,
        "createSubnet": False,
        "vnetName": f"{vm_name}-vnet",
        "subnetName": "default",
        "subnetId": "",
        "vnetAddressPrefix": options.get("vnetAddressPrefix", "10.20.0.0/16"),
        "subnetAddressPrefix": options.get("subnetAddressPrefix", "10.20.0.0/24"),
        "nsgMode": "none",
        "nsgName": f"{vm_name}-nsg",
        "nsgId": "",
        "publicIpMode": "none",
        "publicIpName": f"{vm_name}-pip",
        "publicIpId": "",
        "nicName": options.get("nicName", f"{vm_name}-nic"),
        "nicIds": [
            nic if nic.startswith("/") else f"{root}/networkInterfaces/{nic}"
            for nic in options.get("nics", [])
        ],
        "privateIpAddress": options.get("privateIpAddress", ""),
    }
    if resolved["nicIds"]:
        return resolved

    subnet_value = parameters.get("existingSubnetId") or options.get("subnet", "")
    if subnet_value.startswith("/"):
        resolved["subnetId"] = subnet_value
        resolved["vnetName"] = subnet_value.split("/")[-3]
        resolved["subnetName"] = subnet_value.split("/")[-1]
    else:
        vnet_value = options.get("vnet")
        if not vnet_value:
            candidates = sorted(
                resource["id"] for resource in resources
                if resource["id"].lower().startswith(root.lower() + "/virtualnetworks/")
                and "/subnets/" not in resource["id"].lower()
                and (resource.get("location") or parameters["location"]).lower() == parameters["location"].lower()
            )
            vnet_value = candidates[0] if candidates else f"{vm_name}-vnet"
        vnet_id = vnet_value if vnet_value.startswith("/") else f"{root}/virtualNetworks/{vnet_value}"
        vnet = inventory.get(vnet_id.lower(), {})
        resolved["createVnet"] = not (vnet or vnet_value.startswith("/"))
        resolved["vnetName"] = vnet_id.split("/")[-1]
        prefixes = vnet.get("properties", {}).get("addressSpace", {}).get("addressPrefixes", [])
        if prefixes and "vnetAddressPrefix" not in options:
            resolved["vnetAddressPrefix"] = prefixes[0]
        if not subnet_value:
            candidates = sorted(
                resource["id"] for resource in inventory.values()
                if resource["id"].lower().startswith(vnet_id.lower() + "/subnets/")
                and resource["id"].split("/")[-1].lower() != "gatewaysubnet"
            )
            subnet_value = candidates[0].split("/")[-1] if candidates else "default"
        resolved["subnetName"] = subnet_value
        resolved["subnetId"] = f"{vnet_id}/subnets/{subnet_value}"
        resolved["createSubnet"] = not resolved["createVnet"] and resolved["subnetId"].lower() not in inventory
        if resolved["createSubnet"] and vnet_id.split("/")[4].lower() != group.lower():
            raise ValueError("Creating a subnet in a different resource group is not supported.")
    subnet = inventory.get(resolved["subnetId"].lower(), {})
    prefix = subnet.get("properties", {}).get("addressPrefix")
    if prefix and "subnetAddressPrefix" not in options:
        resolved["subnetAddressPrefix"] = prefix

    for field, kind, default, mode_key, name_key, id_key in [
        ("nsg", "networkSecurityGroups", f"{vm_name}-nsg", "nsgMode", "nsgName", "nsgId"),
        ("publicIp", "publicIPAddresses", f"{vm_name}-pip" if parameters.get("publicIpEnabled") else "",
         "publicIpMode", "publicIpName", "publicIpId"),
    ]:
        value = options.get(field, default)
        if value:
            identity = value if value.startswith("/") else f"{root}/{kind}/{value}"
            resolved[mode_key] = "existing" if value.startswith("/") or identity.lower() in inventory else "new"
            resolved[name_key] = identity.split("/")[-1]
            resolved[id_key] = identity
    return resolved
