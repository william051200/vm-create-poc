from uuid import UUID


def resolve_identity(options, subscription, group, resources):
    if "assignIdentity" not in options:
        if options.get("role") or options.get("scope"):
            raise ValueError("Role assignment requires a system-assigned identity.")
        return {}
    identities = options["assignIdentity"] or ["[system]"]
    system = "[system]" in identities
    root = f"/subscriptions/{subscription}/resourceGroups/{group}/providers/Microsoft.ManagedIdentity"
    users = {
        value if value.startswith("/") else f"{root}/userAssignedIdentities/{value}": {}
        for value in identities if value != "[system]"
    }
    identity = {"type": ",".join(
        name for enabled, name in [(system, "SystemAssigned"), (bool(users), "UserAssigned")]
        if enabled
    )}
    if users:
        identity["userAssignedIdentities"] = users
    result = {"identity": identity}
    role = options.get("role")
    scope = f"/subscriptions/{subscription}/resourceGroups/{group}"
    if options.get("scope") and not role:
        raise ValueError("Role assignment scope requires a role.")
    if role:
        if not system:
            raise ValueError("Role assignment requires a system-assigned identity.")
        if options.get("scope", scope).lower() != scope.lower():
            raise ValueError("Offline role assignments support the configured resource group only.")
        if role.startswith("/"):
            role_id = role
        else:
            try:
                role_id = f"/subscriptions/{subscription}/providers/Microsoft.Authorization/roleDefinitions/{UUID(role)}"
            except ValueError:
                matches = [
                    resource["id"] for resource in resources
                    if "/providers/microsoft.authorization/roledefinitions/" in resource["id"].lower()
                    and resource.get("properties", {}).get("roleName", "").lower() == role.lower()
                ]
                if len(matches) != 1:
                    raise ValueError("Role name must match exactly one dummy role definition.")
                role_id = matches[0]
        result["roleAssignment"] = {"roleDefinitionId": role_id}
    return result
