def resolve_placement(options, subscription, group):
    root = f"/subscriptions/{subscription}/resourceGroups/{group}/providers/Microsoft.Compute"
    result = {}
    for field, kind, output in [
        ("availabilitySet", "availabilitySets", "availabilitySetId"),
        ("vmss", "virtualMachineScaleSets", "vmssId"),
        ("ppg", "proximityPlacementGroups", "ppgId"),
    ]:
        value = options.get(field)
        if value:
            result[output] = value if value.startswith("/") else f"{root}/{kind}/{value}"
    if options.get("zones"):
        result["zones"] = options["zones"]
    return result
