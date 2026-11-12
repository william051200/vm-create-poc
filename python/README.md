# Python Bicep VM creation POC

This is the Python track of the Bicep VM creation POC, reflecting the language used by the existing [Azure CLI](https://github.com/Azure/azure-cli). It is a standalone experiment, not an installed Azure CLI command or a change to `az vm create`.

The `bicep` backend is the proposed template-authoring approach. The `arm-template` backend reproduces the existing in-code ARM JSON assembly approach for comparison, within this project's supported options. Both submit JSON through the same ARM deployment flow; ARM remains the deployment service.

See [shared configuration](../docs/configuration.md) and [shared architecture](../docs/architecture.md) for behavior common to Python and C#.

## Requirements and setup

Use Python 3.10 or later. The default Bicep backend requires a [standalone Bicep executable](https://learn.microsoft.com/azure/azure-resource-manager/bicep/install), on `PATH` or selected with `--bicep-path`. `az bicep` is not the standalone executable expected here.

The `arm-template` backend does not need Bicep. Neither backend has third-party runtime package dependencies.

Run all commands below from the repository root:

```text
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .\python
```

Copy `config.example.json` to `config.local.json`, fill in your Azure settings, and point `sshPublicKeyPath` at your public key.

## Compare template construction

Run the same configuration through both paths:

```text
.\.venv\Scripts\python.exe -m vm_create --config config.local.json --backend bicep
.\.venv\Scripts\python.exe -m vm_create --config config.local.json --backend arm-template
```

These commands only print deployment requests. Compare parameters, resource choices, dependencies, and outputs rather than expecting identical templates: Bicep emits nested deployments, while the JSON baseline emits flat resources.

## Validate and deploy through ARM

```text
.\.venv\Scripts\python.exe -m vm_create --config config.local.json --operation validate
.\.venv\Scripts\python.exe -m vm_create --config config.local.json --operation deploy
```

These commands use Bicep by default. Add `--backend arm-template` to use the JSON comparison baseline with the same ARM operations. `validate` and `deploy` require Azure CLI, `az login`, an existing resource group, and deployment/resource permissions. **Deploy creates billable resources.**

The package also installs the `vm-create` console entry point. Bicep sources ship as package data, so template lookup does not depend on the checkout layout. Bicep compilation still requires an external executable.

| Option | Default | Meaning |
| --- | --- | --- |
| `--config` | Required | Configuration JSON file |
| `--operation` | `build` | `build`, `validate`, or `deploy` |
| `--backend` | `bicep` | `bicep` or `arm-template` |
| `--bicep-path` | `bicep` | Standalone executable name/path; unused by `arm-template` |
| `--timeout-seconds` | `1800` | Maximum ARM polling duration |

Both backends use the same authentication, ARM deployment API, polling, result handling, and six [deployment outputs](../docs/architecture.md#results). JSON goes to stdout; errors go to stderr with a nonzero exit code.

If local tool downloads exist under `.tools`, use `--bicep-path .tools\bicep-win-x64.exe`. Those ignored tools are not part of the package.

## Implementation documentation

- [Backends and module ownership](docs/backends.md)
- [Offline resource choices and Azure CLI comparison](docs/offline-resources.md)

## Tests

After the editable installation, run from the repository root:

```text
.\.venv\Scripts\python.exe -m unittest discover -s .\python\tests -v
.\.venv\Scripts\python.exe .\python\tests\verify_dynamic_builds.py --bicep-path <bicep-executable>
```

Unit tests cover factory selection, configuration safety, typed parameters, offline resolution, resource dependencies, full resource outputs, compilation routing, asynchronous ARM operations, failures, timeouts, and polling credential protection. Compiler calls and HTTP are mocked where appropriate.

`verify_dynamic_builds.py` uses real Bicep to compile both Python backends' scenarios. It checks resolved parameters, resource choices, and ARM decompile/recompile round trips. It blocks authentication and HTTP during builds.

An optional cross-language compiler check also needs the .NET 10 SDK:

```text
.\.venv\Scripts\python.exe .\python\tests\verify_builds.py --bicep-path <bicep-executable> --dotnet-path <dotnet-executable>
```

This compares complete Python/C# Bicep request payloads for private/public networking, new/existing subnets, and data disks. It also checks ARM backend parameters/defaults, output contracts, round-trip compilation, and network API selection. Run it separately from C# builds to avoid concurrent writes to the same build output.

All tests live under `python\tests`; they create no Azure resources. Compiler success is not live ARM validation. [C#'s own tests](../csharp/README.md#tests) are separate.
