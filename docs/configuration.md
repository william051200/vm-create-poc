# Shared configuration and resource behavior

Copy the root `config.example.json` to `config.local.json`. Set your subscription, existing resource group, deployment name, region, VM name, and administrator username. Set `sshPublicKeyPath` to an existing RSA or Ed25519 **public** key file.

Relative key paths resolve from the configuration file's directory, not the current working directory. Never copy a private key into this project.

## Configuration fields

| Field | Required | Meaning |
| --- | --- | --- |
| `subscriptionId` | Yes | Subscription UUID |
| `resourceGroup` | Yes | Existing resource group; clients do not create it |
| `deploymentName` | Yes | ARM deployment name; unused by C# direct mode but still required by the shared configuration schema |
| `sshPublicKeyPath` | Yes | Public key path; contents become the `sshPublicKey` template parameter |
| `parameters.vmName` | Yes | Starts with a letter, contains letters/digits/hyphens, does not end with a hyphen, maximum 56 characters |
| `parameters.location` | Yes | Azure region |
| `parameters.adminUsername` | Yes | Linux username, maximum 32 characters; common reserved names are rejected locally |
| `parameters.vmSize` | No | Default `Standard_D2s_v5` |
| `parameters.osDiskSizeGb` | No | Default `32`; must meet the selected image's minimum size |
| `parameters.storageSku` | No | `Standard_LRS`, `StandardSSD_LRS`, or default `Premium_LRS` |
| `parameters.dataDiskSizesGb` | No | New empty managed disks, in LUN order; default none |
| `parameters.publicIpEnabled` | No | Default `false`; `true` requires a restricted SSH source CIDR |
| `parameters.sshSourceCidr` | When public IP is enabled | Canonical IPv4 CIDR, such as `203.0.113.4/32`; replace with your actual source. `/0` is rejected |
| `parameters.existingSubnetId` | No | Full subnet ID in the configured subscription; otherwise create a VNet and subnet |
| `parameters.imageReference` | No | Marketplace image object with `publisher`, `offer`, `sku`, and `version` |

The default image is Canonical Ubuntu 22.04 LTS Gen2, Marketplace version `latest`. Python template-backend parameter defaults are checked against compiled Bicep. C# direct creation applies equivalent resource defaults in code without invoking Bicep.

Local validation checks configuration shape and common unsafe inputs. Azure still validates image compatibility, reserved usernames, regional availability, quotas, subnet suitability, and VM-specific disk limits.

Python also accepts an image reference containing an `id` and supports [build-only resource choices](../python/docs/offline-resources.md), including a location default from dummy inventory. These extensions are not part of C#'s configuration contract.

## Resource defaults

Both template and direct-resource paths create a Trusted Launch Linux VM with SSH-only authentication, Secure Boot, vTPM, and managed boot diagnostics.

New networking uses VNet `10.20.0.0/16` and subnet `10.20.0.0/24`. An existing subnet bypasses VNet creation. Resource names derive from `vmName`; the shared configuration creates a new NIC and NSG.

Without a public IP, the NIC uses dynamic private allocation and no custom inbound SSH rule is added. Default NSG rules still apply, including virtual-network access. Private connectivity must already exist to reach the VM.

With public IP enabled, either path adds a Standard static public IP and allows inbound TCP `22` only from `sshSourceCidr`. Neither adds a NAT Gateway or another outbound connectivity service.

`dataDiskSizesGb: [32, 64]` creates empty disks at LUNs `0` and `1`. Disk definitions are inline in the VM's `storageProfile`, not separate disk resources. `storageSku` applies to the OS disk and all new data disks. Disk capacity must fit the image and VM size, and the selected SKU must support the storage configuration and Trusted Launch.

Python's offline options can change these resource choices, but cannot be used with `validate` or `deploy`.

## Costs and lifecycle

**`--operation deploy` creates billable Azure resources.** Deleting the VM deletes its OS disk and new NIC. Data disks are detached and can continue incurring charges. VNet, NSG, and public IP resources are not configured for automatic deletion with the VM.

Neither template deployment nor direct creation is transactional. Failure, timeout, or client interruption can leave resources in Azure. Neither client deletes resources, rolls back creation, or automatically cancels an Azure operation. Review and remove unneeded resources yourself.

## Scope

Both clients support Marketplace Linux images, SSH authentication, managed OS disks, new empty data disks, and new or existing subnets. They target public Azure.

Windows, password authentication, VM extensions, multiple VMs, sovereign clouds, what-if, automatic HTTP retries, and rollback are not implemented. Python's additional image, networking, placement, disk, and identity scenarios are documented as offline-only simulations, not live deployment parity with `az vm create`.
