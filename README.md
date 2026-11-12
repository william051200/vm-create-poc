# VM creation with Bicep: proof of concept

This proof of concept (POC) evaluates Bicep for VM creation. The Python comparison uses Azure CLI-style ARM JSON template assembly; existing azclips VM creation instead orchestrates individual resource REST calls. The Bicep POC compiles templates to JSON and submits them as Azure Resource Manager deployments.

The two language tracks reflect [Azure CLI](https://github.com/Azure/azure-cli)'s existing Python implementation and the C#/.NET context of [Azure/azclips](https://github.com/Azure/azclips/). These are standalone experiments, not integrations into either upstream project.

## What this POC compares

| Track | Existing-method comparison | Bicep POC |
| --- | --- | --- |
| Python / Azure CLI context | Build ARM JSON directly in Python with `arm-template` | Compile Bicep, then submit the resulting JSON to ARM |
| C# / azclips context | Simulate existing-style direct resource REST calls with `direct`; see [upstream design](csharp/docs/azclips-existing-design.md) | Compile the same Bicep templates, then submit the resulting JSON to ARM |

The focus is Bicep template authoring, resource composition, and deployment from both languages. Python's ARM JSON backend and C#'s direct-resource path are comparison baselines, not proposed replacements. Both clients default to Bicep; neither registers an upstream command or changes `az vm create`.

## Operations

| Operation | Behavior |
| --- | --- |
| `build` (default) | Print a deployment request, or a C# direct-resource plan, without authentication or Azure calls. |
| `validate` | Ask ARM to validate the deployment request; unavailable for C# direct mode. |
| `deploy` | Run an incremental template deployment or direct resource PUTs, wait, and print common resource outputs. |

Both clients use native HTTP libraries and have no third-party runtime package dependencies. Azure CLI is used only to obtain a token.

## POC implementations

| Implementation | Requirements | Guide |
| --- | --- | --- |
| Python | Python 3.10+; standalone Bicep for the default backend | [Python setup, usage, and tests](python/README.md) |
| C# | .NET 10 SDK; standalone Bicep for the default backend | [C# setup, usage, and tests](csharp/README.md) |

Python also has an `arm-template` backend that does not require Bicep, plus build-only dummy resource resolution. C# selects existing-style direct orchestration or the new Bicep deployment path below its VM orchestrator.

## Shared documentation

- [Architecture](docs/architecture.md): execution flow, template ownership, resource dependencies, authentication, and outputs.
- [Configuration and resource behavior](docs/configuration.md): common fields, defaults, networking, costs, and limitations.
- [Example configuration](config.example.json): starting point for either client.

Language-specific documentation and tests live under `python` and `csharp`; there is no root test suite.

## Safety and scope

The shared configuration supports a Trusted Launch Linux VM, SSH public-key authentication, managed OS/data disks, and new or existing subnets. Public IP creation is off by default; enabling it requires a restricted SSH source CIDR.

`validate` and `deploy` require Azure CLI, `az login`, an existing resource group, and suitable Azure permissions. These clients target public Azure, not full `az vm create` feature parity.

**Deployment creates billable resources.** Failure, interruption, or timeout can leave resources behind. Neither client automatically cancels, rolls back, or deletes an Azure deployment.
