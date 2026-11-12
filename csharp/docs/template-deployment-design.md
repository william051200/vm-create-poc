# Template-based VM creation design

This document describes proposed JSON/Bicep integration into azclips VM creation. The [existing upstream design](azclips-existing-design.md) remains documented separately.

Blue shows existing components. Orange shows additions to the VM-create path, which may reuse existing Resources extension helpers.

## Template-only end state

If direct resource creation is retired, remove execution-path selection and the separate template-backend factory. Keep the existing CLI dispatcher, execution session, and VM-specific input resolution.

```mermaid
flowchart TD
    CLI["CLI: vm create"]
    Dispatch["Existing ExtensionDispatcher"]
    Session["Existing Engine / ExecutionSession<br/>Profile, authentication, HTTP, output context"]
    VM["VM orchestrator<br/>Resolve inputs and prepare parameters"]
    Loader["Template loader<br/>Read ARM JSON, or compile Bicep to ARM JSON"]
    Deploy["ARM deployment adapter<br/>Template + parameters<br/>Submit deployment and poll"]
    Mapper["Result mapper<br/>Map deployment outputs to compatible VM result"]
    Result["Existing typed VirtualMachine output contract"]
    Output["Existing host output formatting"]
    User["User"]

    CLI --> Dispatch --> Session --> VM
    VM --> Loader --> Deploy --> Mapper --> Result
    Result --> Output --> User

    classDef existing fill:#E8F0FE,stroke:#2563EB,color:#172554
    classDef added fill:#FFF0D6,stroke:#D97706,color:#7C2D12,stroke-width:2px
    class CLI,Dispatch,Session,VM,Result,Output,User existing
    class Loader,Deploy,Mapper added
```

The loader still needs a small format check when accepting both JSON and Bicep. JSON is already an ARM template; Bicep must be compiled first. This does not require separate backend-selection infrastructure.

The VM orchestrator keeps common validation, input resolution, and parameter mapping, but no longer creates NICs or VMs directly. The template owns resource creation and dependencies; ARM executes them. The deployment adapter uses the existing session's authenticated HTTP context and operation lifecycle.

The result mapper preserves the upstream VM output contract, rather than returning a raw deployment response. The template must return sufficient VM data, or the mapper must read the VM by its output resource ID.

## Comparison while retaining the direct path

During comparison, execution-path selection is necessary because direct resource creation and template deployment have different owners. The template path branches before any direct networking or VM creation.

```mermaid
flowchart TD
    CLI["CLI: vm create"]
    Dispatch["Existing ExtensionDispatcher"]
    Session["Existing Engine / ExecutionSession<br/>Profile, authentication, HTTP, output context"]
    VM["Existing VM orchestrator<br/>Common input resolution"]
    Select{"NEW: execution-path selection"}
    Network["Existing NIC orchestrator<br/>Create or reuse networking"]
    Direct["Existing VM processor<br/>Direct resource PUT and LRO polling"]
    Template{"NEW: template backend selection"}
    Json["JSON template backend<br/>Read or build ARM JSON"]
    Bicep["Bicep template backend<br/>Compile Bicep to ARM JSON"]
    Deploy["NEW: shared deployment adapter<br/>Template + parameters<br/>ARM deployment PUT and polling"]
    Normalize["NEW: result adapter<br/>Map deployment outputs to compatible VM result"]
    Result["Existing typed VirtualMachine output contract"]
    Output["Existing host output formatting"]
    User["User"]

    CLI --> Dispatch --> Session --> VM --> Select
    Select -->|existing direct path| Network --> Direct
    Select -->|existing NICs supplied| Direct
    Select -->|new template path| Template
    Template -->|JSON| Json --> Deploy
    Template -->|Bicep| Bicep --> Deploy
    Deploy --> Normalize --> Result
    Direct --> Result
    Result --> Output --> User

    classDef existing fill:#E8F0FE,stroke:#2563EB,color:#172554
    classDef added fill:#FFF0D6,stroke:#D97706,color:#7C2D12,stroke-width:2px
    class CLI,Dispatch,Session,VM,Network,Direct,Result,Output,User existing
    class Select,Template,Json,Bicep,Deploy,Normalize added
```

Both template backends feed the same ARM deployment adapter. Neither runs the existing NIC/resource creation chain first. A shared loader could also replace the separate JSON/Bicep backend-selection layer during comparison.

## Integration boundary and implementation status

No new dispatcher or login session is needed. The separate Resources extension already has JSON/Bicep and deployment support; reuse or extraction of those helpers needs explicit integration, not an automatic backend hook.

Neither diagram describes an implemented upstream change. The local C# POC still implements `direct` and `bicep`, not a JSON-template backend or the template-only end state. Its six-output result envelope also differs from upstream's typed VM output. See the [local implementation](implementation.md#simulation-boundary).
