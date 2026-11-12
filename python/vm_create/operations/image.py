def resolve_image(options, parameters, resources):
    image = options.get("image")
    reference = parameters.get("imageReference")
    result = {}
    if image:
        if image.lower() == "ubuntu2204":
            image = "Canonical:0001-com-ubuntu-server-jammy:22_04-lts-gen2:latest"
        if image.startswith("/"):
            reference = {"id": image}
        else:
            publisher, offer, sku, version = image.split(":")
            reference = {
                "publisher": publisher, "offer": offer, "sku": sku, "version": version,
            }
    if not reference:
        return result
    identity = reference.get("id") or ":".join(
        reference[key] for key in ("publisher", "offer", "sku", "version")
    )
    resource = next(
        (resource for resource in resources if resource["id"].lower() == identity.lower()), {},
    )
    properties = resource.get("properties", {})
    reference = dict(reference)
    if reference.get("version") == "latest" and resource.get("name"):
        reference["version"] = resource["name"]
    if identity.lower().endswith("/versions/latest") and resource.get("name"):
        reference["id"] = identity.rsplit("/", 1)[0] + "/" + resource["name"]
    result["imageReference"] = reference
    plan = resource.get("plan") or properties.get("purchasePlan")
    if plan:
        result["plan"] = plan
    disk = properties.get("storageProfile", {}).get("osDisk", {})
    minimum_size = properties.get("osDiskImage", {}).get("sizeInGb") or disk.get("diskSizeGB")
    if minimum_size and "osDiskSizeGb" not in parameters:
        result["osDiskSizeGb"] = max(32, minimum_size)
    return result
