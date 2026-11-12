def resolve_resource_group(parameters, subscription, group, resources):
    if "location" in parameters:
        return {}
    identity = f"/subscriptions/{subscription}/resourceGroups/{group}"
    resource = next(
        (resource for resource in resources if resource["id"].lower() == identity.lower()), {},
    )
    return {"location": resource["location"]} if resource.get("location") else {}
