def resolve_disks(options, parameters, subscription, group):
    root = f"/subscriptions/{subscription}/resourceGroups/{group}/providers/Microsoft.Compute/disks"
    return {
        "attachedDataDisks": [
            {
                "id": disk if disk.startswith("/") else f"{root}/{disk}",
                "lun": len(parameters.get("dataDiskSizesGb", [])) + index,
                "caching": "None",
            }
            for index, disk in enumerate(options.get("attachDataDisks", []))
        ],
    }
