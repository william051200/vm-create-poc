# C# VM creation comparison POC

This is the C#/.NET track of the Bicep VM creation POC, targeting the language context of [Azure/azclips](https://github.com/Azure/azclips/). It compares existing-style direct resource creation with the proposed Bicep-plus-ARM-deployment path. This standalone client is not registered in azclips.

Existing azclips `vm create` uses processor-based orchestration and individual resource REST calls, not a JSON/Bicep template-backend factory. See the [existing azclips design](docs/azclips-existing-design.md) for the verified call chain and upstream source links.

The separate [template deployment design](docs/template-deployment-design.md) shows proposed JSON/Bicep integration and the template-only end state after retiring direct resource creation.

The local VM orchestrator selects one path before any resource creation:

| Backend | Role | Azure execution |
| --- | --- | --- |
| `direct` | Simulate existing azclips-style orchestration | Create VNet/subnet, NSG, optional public IP, NIC, then VM through individual resource PUTs |
| `bicep` (default) | Evaluate the proposed approach | Compile shared Bicep and submit one ARM deployment; ARM manages dependencies |

`direct` is not a JSON template backend. The [Python JSON baseline](../python/README.md#compare-template-construction) models Azure CLI-style template assembly instead. Python is not required to run C#.

See [shared configuration](../docs/configuration.md) and [shared architecture](../docs/architecture.md) for common resource behavior, authentication, and outputs.

## Requirements and setup

Use the .NET 10 SDK. The default Bicep backend also needs a [standalone Bicep executable](https://learn.microsoft.com/azure/azure-resource-manager/bicep/install). Put Bicep on `PATH` or select it with `--bicep-path`. `az bicep` is not the standalone executable expected here. The direct backend never invokes Bicep.

There are no third-party runtime package dependencies. Live operations additionally require Azure CLI, `az login`, an existing resource group, and suitable permissions. Bicep needs deployment and resource permissions; direct deployment needs resource-provider permissions.

Copy the root `config.example.json` to `config.local.json`, fill in Azure settings, and set your SSH public key path. Relative key paths resolve from the configuration file's directory.

## Build, validate, and deploy

Run from the repository root:

```text
dotnet build .\csharp\VmCreate
dotnet run --project .\csharp\VmCreate -- --config config.local.json
dotnet run --project .\csharp\VmCreate -- --config config.local.json --operation validate
dotnet run --project .\csharp\VmCreate -- --config config.local.json --operation deploy
```

To compare existing-style orchestration:

```text
dotnet run --project .\csharp\VmCreate -- --config config.local.json --backend direct
dotnet run --project .\csharp\VmCreate -- --config config.local.json --backend direct --operation deploy
```

`build` is offline for both paths. Bicep prints a deployment request; direct prints an ordered `requests` plan containing resource types, PUT URIs, and bodies. The direct plan is not an ARM deployment template. `direct --operation validate` is rejected: individual resource creation has no ARM template-validation operation. Configuration validation still runs locally.

**Either deploy path creates billable resources.** A timeout stops local waiting, not the Azure operation.

| Option | Default | Meaning |
| --- | --- | --- |
| `--config` | Required | Configuration JSON file |
| `--operation` | `build` | `build`, `validate`, or `deploy` |
| `--backend` | `bicep` | `bicep` or `direct`; only Bicep supports `validate` |
| `--bicep-path` | `bicep` | Standalone executable name/path; ignored by direct mode |
| `--timeout-seconds` | `1800` | Overall compilation and ARM-operation timeout |

JSON goes to stdout; errors go to stderr with a nonzero exit code. Both deploy paths return `operation`, `state`, and the six shared [outputs](../docs/architecture.md#results).

If scoped downloads exist, `.tools\dotnet\dotnet.exe` can replace `dotnet`, and `--bicep-path .tools\bicep-win-x64.exe` selects local Bicep. These ignored tools are not part of the implementation.

## Templates and publishing

`VmCreate.csproj` copies the shared `.bicep` files from `python\vm_create\templates\bicep` into the output's `templates` directory. They are also included by `dotnet publish`; retain that directory with the published application.

```text
dotnet publish .\csharp\VmCreate -c Release
```

The Bicep backend compiles the copied sources directly with standalone Bicep. Neither backend accepts Python's `network`, `compute`, and `mockResources` configuration extensions. Images use the common Marketplace object, not managed/gallery image IDs.

This simulation uses sequential resource creation, deterministic resource IDs, and the shared configuration subset. It does not reproduce upstream parallel scheduling, image/resource discovery, existing-NIC handling, extension registration, or profile authentication. See [implementation details](docs/implementation.md#simulation-boundary).

See [C# implementation details](docs/implementation.md) for code ownership, timeout handling, and test structure.

## Tests

Run the C#-owned offline test harness from the repository root:

```text
dotnet run --project .\csharp\VmCreate.Tests
```

This is an executable test project: use `dotnet run`, not `dotnet test`. It needs no external test packages, Python installation, Bicep compiler, Azure login, or Azure resources.

Tests cover backend selection and isolation, offline direct plans, resource-body mapping, dependency ordering, synchronous/asynchronous operations, normalized results, failure/cancellation, credential protection, configuration/public-key validation, and Windows batch launching.

The optional [Python-owned compiler comparison](../python/README.md#tests) exercises actual Bicep compilation through both clients and checks complete request equivalence. It needs Python, .NET, and Bicep; it is separate from the C# test harness. Do not run that comparison concurrently with C# builds.
