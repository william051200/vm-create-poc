# Offline resource choices

Add top-level `network`, `compute`, and/or `mockResources` fields to a Python configuration to simulate resource resolution. Their presence makes the configuration **build-only**: `validate` and `deploy` fail before compilation or authentication.

Neither lookup resolution nor `build` calls Azure. C# does not accept these extra configuration fields. Configurations without them retain the normal build/validate/deploy flow.

## Dummy inventory

`mockResources` is an optional array of plain objects with `id`, optional `location`, and `properties`. Image entries may use Marketplace URNs as dummy IDs. Full resource IDs can be treated as existing without inventory entries.

Merge the following fragment into the root example configuration, retaining its required fields and supplying your public key path. This is not a standalone configuration. IDs, regions, and resource groups must match your configuration.

```json
{
  "network": {
    "vnet": "shared-vnet",
    "subnet": "apps",
    "nsg": "",
    "publicIp": "",
    "privateIpAddress": "10.40.1.8"
  },
  "compute": {
    "image": "Canonical:0001-com-ubuntu-server-jammy:22_04-lts-gen2:latest",
    "attachDataDisks": ["existing-data"]
  },
  "mockResources": [
    {
      "id": "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-vm-create/providers/Microsoft.Network/virtualNetworks/shared-vnet",
      "location": "eastus",
      "properties": {
        "addressSpace": {"addressPrefixes": ["10.40.0.0/16"]},
        "subnets": [
          {"name": "apps", "properties": {"addressPrefix": "10.40.1.0/24"}}
        ]
      }
    }
  ]
}
```

This reuses a VNet/subnet, disables NSG/public IP attachment, selects a static private IP, resolves a Marketplace URN, and attaches a dummy disk without disk metadata.

## Network choices

| Choice | Behavior |
| --- | --- |
| `network.vnet`, `network.subnet` | Name or existing full ID. If omitted, discover the first matching VNet/subnet in deterministic ID order, excluding `GatewaySubnet`; otherwise create defaults. A missing named subnet in an existing VNet creates only a child subnet. |
| `network.vnetAddressPrefix`, `network.subnetAddressPrefix` | Defaults `10.20.0.0/16` and `10.20.0.0/24`; existing prefixes come from inventory unless overridden. No CIDR or overlap validation. |
| `network.nsg` | Omitted: resolve `<vmName>-nsg`; name: reuse or create; ID: reuse; empty string: attach no NSG to the new NIC. |
| `network.publicIp` | Omitted: follow `publicIpEnabled`; name: reuse or create; ID: reuse; empty string: disabled. A new NSG with public IP requires `sshSourceCidr`. |
| `network.nicName`, `network.privateIpAddress` | Override the new NIC name and select static rather than dynamic private allocation. Addresses pass through without validation. |
| `network.nics` | Array of existing NIC names/IDs. Takes precedence over other network choices and creates no networking resources. First NIC is primary; reused NICs use `deleteOption: Detach`. No inventory required. |

New resources depend only on other newly created resources. Existing-resource references do not cause redeployment. Summary NIC outputs refer to the first NIC; reused NICs do not trigger public-IP discovery, so their public-IP summary is empty.

## Compute choices

| Choice | Behavior |
| --- | --- |
| `compute.image` | `Ubuntu2204`, a `publisher:offer:sku:version` URN, or full image ID. Overrides `parameters.imageReference`. A matching dummy entry's `name` resolves Marketplace/gallery `latest`; `plan` or `properties.purchasePlan` supplies a purchase plan. Dummy `properties.osDiskImage.sizeInGb` or `properties.storageProfile.osDisk.diskSizeGB` sets a default OS disk size of at least 32 GB when no explicit size was supplied. No Azure lookup. |
| `compute.attachDataDisks` | Dummy managed disk names/IDs. Attach at LUNs after the new `dataDiskSizesGb` disks with `deleteOption: Detach`. No inventory or disk-state checks. |
| Omitted `parameters.location` | A dummy resource-group entry at `/subscriptions/<subscription>/resourceGroups/<group>` supplies its `location`. Explicit locations win. Without that entry, location remains required. |
| `compute.availabilitySet`, `compute.vmss`, `compute.ppg` | Names/IDs of existing availability sets, Flexible VM scale sets, and proximity placement groups. Referenced without creation or existence/compatibility checks. |
| `compute.zones` | Array such as `["1"]`, forwarded without SKU or zone-support validation. Do not combine availability sets with zones. |
| `compute.bootDiagnosticsStorage` | Existing storage account name/ID or HTTPS blob endpoint. A dummy account's `properties.primaryEndpoints.blob` supplies its endpoint; absent metadata uses a dummy public-Azure endpoint derived from its name. Omit for managed boot diagnostics. No storage creation. |
| `compute.assignIdentity` | User-assigned identity names/IDs and/or `"[system]"`. An empty array selects system identity. IDs are built locally. |
| `compute.role` | Role for the VM's system identity in the configured resource group. Accepts a role-definition ID, UUID, or name matching exactly one dummy role's `properties.roleName`. `compute.scope`, if supplied, must equal the configured resource-group ID. Arbitrary scopes are unsupported. |

Python also accepts `parameters.imageReference: {"id": "<managed-image-or-gallery-version-id>"}`. C# retains Marketplace-only image validation.

For role-name lookup, merge this fragment into a base configuration:

```json
{
  "compute": {
    "assignIdentity": ["[system]"],
    "role": "Reader"
  },
  "mockResources": [
    {
      "id": "/subscriptions/00000000-0000-0000-0000-000000000000/providers/Microsoft.Authorization/roleDefinitions/11111111-1111-1111-1111-111111111111",
      "properties": {"roleName": "Reader"}
    }
  ]
}
```

The role UUID is dummy data. The principal ID is not fabricated: the generated template references the VM identity at deployment time. Build-only protection means that reference is never evaluated against Azure. Role assignment is a separate ARM resource depending on the VM; Bicep represents it in `role_assignment.bicep`.

## Limits

These modules assemble dummy JSON rather than reproduce Azure CLI validation. They do not check ownership, locations of explicitly selected resources, IP validity, subnet overlap, image/VM compatibility, or disk attachment state. Creating a subnet in another resource group is unsupported.

Build-only protection, basic JSON shapes, restricted SSH rules for new NSGs, and system-identity requirements for role assignment remain enforced. Disabling an NSG does not provide equivalent SSH protection.

## Comparison with Azure CLI

The reference revision is Azure CLI commit `51552fd56854d33f646a031bbbf024cf1fbc4d42`, including its [VM create/network validators](https://github.com/Azure/azure-cli/blob/51552fd56854d33f646a031bbbf024cf1fbc4d42/src/azure-cli/azure/cli/command_modules/vm/_validators.py#L884-L945), [resource assembly](https://github.com/Azure/azure-cli/blob/51552fd56854d33f646a031bbbf024cf1fbc4d42/src/azure-cli/azure/cli/command_modules/vm/custom.py#L1003-L1110), and [disk normalization](https://github.com/Azure/azure-cli/blob/51552fd56854d33f646a031bbbf024cf1fbc4d42/src/azure-cli/azure/cli/command_modules/vm/_vm_utils.py#L164-L340). This project's private-network default, deterministic names, and restricted SSH policy intentionally differ.

| Category | Azure CLI behavior in that revision | This project |
| --- | --- | --- |
| Resource group | [`resource_groups.get`](https://github.com/Azure/azure-cli/blob/51552fd56854d33f646a031bbbf024cf1fbc4d42/src/azure-cli-core/azure/cli/core/commands/validators.py#L74-L84) supplies omitted location. | Dummy resource-group entry. |
| Images | [Alias-document fetch and image/plan metadata reads](https://github.com/Azure/azure-cli/blob/51552fd56854d33f646a031bbbf024cf1fbc4d42/src/azure-cli/azure/cli/command_modules/vm/_validators.py#L263-L352). | Local alias and optional dummy version/plan/disk metadata. |
| Placement | [Availability-set/VMSS checks](https://github.com/Azure/azure-cli/blob/51552fd56854d33f646a031bbbf024cf1fbc4d42/src/azure-cli/azure/cli/command_modules/vm/_validators.py#L746-L847) and [PPG checks](https://github.com/Azure/azure-cli/blob/51552fd56854d33f646a031bbbf024cf1fbc4d42/src/azure-cli/azure/cli/command_modules/vm/_validators.py#L81-L94). | References only; existence checks omitted. |
| Identity/RBAC | User-assigned IDs built locally; [role names use role-definition listing](https://github.com/Azure/azure-cli/blob/51552fd56854d33f646a031bbbf024cf1fbc4d42/src/azure-cli/azure/cli/command_modules/vm/_validators.py#L1631-L1658). UUIDs/full IDs need no lookup. | Dummy role-name lookup and ARM role assignment. |
| Boot diagnostics | [Storage account listing](https://github.com/Azure/azure-cli/blob/51552fd56854d33f646a031bbbf024cf1fbc4d42/src/azure-cli/azure/cli/command_modules/vm/_vm_utils.py#L419-L430) finds a named account's blob endpoint. | Dummy endpoint resolution or supplied URI. |
| Advanced paths | [SKU checks](https://github.com/Azure/azure-cli/blob/51552fd56854d33f646a031bbbf024cf1fbc4d42/src/azure-cli/azure/cli/command_modules/vm/_validators.py#L375-L387), some accelerated-networking size reads, [attached OS-disk inspection](https://github.com/Azure/azure-cli/blob/51552fd56854d33f646a031bbbf024cf1fbc4d42/src/azure-cli/azure/cli/command_modules/vm/_validators.py#L1594-L1609), unmanaged storage, workspace preparation, and [extensions](https://github.com/Azure/azure-cli/blob/51552fd56854d33f646a031bbbf024cf1fbc4d42/src/azure-cli/azure/cli/command_modules/vm/custom.py#L1254-L1291). | Not implemented. |
| Post-deployment details | [VM instance-view, NIC, and public-IP reads](https://github.com/Azure/azure-cli/blob/51552fd56854d33f646a031bbbf024cf1fbc4d42/src/azure-cli/azure/cli/command_modules/vm/custom.py#L1404-L1446). | ARM deployment outputs, not client-side resource reads. |

Attaching existing disks or NICs by name/ID does not itself require preflight API calls in that Azure CLI flow. Client lookups are distinct from service operations ARM performs during deployment. This simulation does not provide full `az vm create` parity.
