def resolve_diagnostics(options, resources):
    storage = options.get("bootDiagnosticsStorage")
    if not storage:
        return {}
    if storage.startswith("https://"):
        return {"bootDiagnosticsStorageUri": storage}
    account = storage.rsplit("/", 1)[-1]
    resource = next(
        (
            resource for resource in resources
            if "/providers/microsoft.storage/storageaccounts/" in resource["id"].lower()
            and resource["id"].rsplit("/", 1)[-1].lower() == account.lower()
        ), {},
    )
    uri = resource.get("properties", {}).get("primaryEndpoints", {}).get("blob")
    return {"bootDiagnosticsStorageUri": uri or f"https://{account}.blob.core.windows.net/"}
