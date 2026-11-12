# Python backends and module ownership

The POC evaluates Bicep in the Python language context of Azure CLI. `bicep` is the proposed authoring path; `arm-template` is a comparison implementation of the existing direct JSON-building method. Both use ARM for validation and deployment. This package does not depend on Azure CLI's internal template builders.

The Python flow is `CLI -> factory -> selected template backend -> result -> user`. The CLI prints the result dictionary directly as JSON; there is no separate formatter layer.

## Backend selection

`factories\backend.py` defines `BackendType.BICEP` (`bicep`, the default) and `BackendType.ARM_TEMPLATE` (`arm-template`). `create_backend` returns `BicepBackend` or `ArmTemplateBackend`.

Both inherit `TemplateBackend`, which wraps typed parameters, authenticates for live operations, submits ARM requests, and extracts results. Switching backends does not change the configuration's common options or default `build` operation.

| Backend | Template construction | Dependency handling |
| --- | --- | --- |
| `bicep` (POC) | Compile `templates\bicep\main.bicep` with standalone Bicep | Bicep emits nested network and compute deployments and derives dependencies |
| `arm-template` (baseline) | Build a flat ARM JSON resource list directly in Python | Explicit dependencies on newly created resources |

The ARM builder selects resources and disk count from configuration, so rebuild after changing configuration. Existing resources are referenced, not redeployed.

The shared Bicep files are included as package data and found relative to the wrapper. Compilation uses `--stdout`, without modifying source files or writing generated JSON beside them. The entry point can also be compiled directly with standalone Bicep.

## Network API selection

The ARM backend calls `operations\api_versions.py`'s `get_target_network_api()` for VNet, NSG, public IP, and NIC declarations and network output references. The helper simulates Azure CLI's pinned selection for this project's public-Azure environment. It does not query Azure or implement cloud profiles.

Bicep uses fixed resource API-version literals, including in `network.bicep`. It never calls the Python selector. A resource declaration such as `'Microsoft.Network/publicIPAddresses@${network_api_version}'` is invalid: Bicep reports BCP047 because resource types cannot use string interpolation. Ordinary VM settings remain deployment parameters.

## Module ownership

Paths below are relative to `python\vm_create`.

| Module | Responsibility |
| --- | --- |
| `cli.py` | Arguments, backend selection, JSON output, and CLI errors |
| `config.py` | Configuration shape, safety checks, relative public key paths, and offline resolution |
| `arm.py` | Process execution, Azure CLI token caching, HTTP transport, and ARM polling |
| `factories\backend.py` | Backend enum and factory |
| `templates\base.py` | Shared template execution, typed payloads, and result extraction |
| `templates\bicep.py` | Standalone Bicep compilation |
| `templates\arm_template.py` | Flat ARM resource assembly |
| `operations\api_versions.py` | ARM backend network API selection |
| `operations\network.py` | Network choices from dummy inventory |
| `operations\compute.py` | Compose compute choices from the modules below |
| `operations\resource_group.py` | Default region from a dummy resource group |
| `operations\image.py` | Image reference, version, purchase plan, and disk size |
| `operations\disks.py` | Attached disk IDs and LUNs |
| `operations\placement.py` | Availability set, VMSS, PPG, and zones |
| `operations\diagnostics.py` | Boot diagnostics storage endpoint |
| `operations\identity.py` | Identity IDs and dummy role-definition resolution |
| `templates\bicep` | Shared resource definitions also consumed by C# |

Offline operations return plain dictionaries; there is no shared lookup class or generic resource framework. Both backends receive the same resolved `networkOptions` and `computeOptions`. See [offline resource choices](offline-resources.md) for supported inputs and restrictions.

Authentication, polling, resource ownership, lifecycle, and output contracts are documented in the root [architecture](../../docs/architecture.md) and [configuration guide](../../docs/configuration.md).
