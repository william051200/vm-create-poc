using System.Text.Json.Nodes;

namespace VmCreate;

public sealed class VmCreateOrchestrator(
    ArmDeployer arm,
    Func<string, CancellationToken, Task<JsonObject>>? compile = null)
{
    public async Task<JsonObject> ExecuteAsync(
        DeploymentConfig config, string operation = "build", string backend = "bicep",
        string bicepPath = "bicep", CancellationToken cancellationToken = default)
    {
        if (operation is not ("build" or "validate" or "deploy"))
            throw new ArgumentException("--operation must be build, validate, or deploy.");
        IVmCreateBackend selected = backend switch
        {
            "bicep" => new BicepArmDeploymentBackend(arm,
                token => (compile ?? Processes.CompileAsync)(bicepPath, token)),
            "direct" => new DirectResourceBackend(arm),
            _ => throw new ArgumentException("--backend must be bicep or direct.")
        };
        if (backend == "direct" && operation == "validate")
            throw new ArgumentException(
                "Direct resource creation has no ARM template validation. Use --backend bicep --operation validate.");
        cancellationToken.ThrowIfCancellationRequested();
        var plan = await selected.BuildAsync(config, cancellationToken);
        if (operation == "build") return plan;
        var outputs = await selected.ExecuteAsync(config, plan, operation, cancellationToken);
        return new JsonObject
        {
            ["operation"] = operation,
            ["state"] = "Succeeded",
            ["outputs"] = outputs
        };
    }
}

internal interface IVmCreateBackend
{
    Task<JsonObject> BuildAsync(DeploymentConfig config, CancellationToken cancellationToken);
    Task<JsonObject> ExecuteAsync(
        DeploymentConfig config, JsonObject plan, string operation, CancellationToken cancellationToken);
}

internal sealed class BicepArmDeploymentBackend(
    ArmDeployer arm, Func<CancellationToken, Task<JsonObject>> compile) : IVmCreateBackend
{
    public async Task<JsonObject> BuildAsync(DeploymentConfig config, CancellationToken cancellationToken) =>
        config.Payload(await compile(cancellationToken));

    public async Task<JsonObject> ExecuteAsync(
        DeploymentConfig config, JsonObject plan, string operation, CancellationToken cancellationToken)
    {
        var response = await arm.ExecuteAsync(config, plan, operation, cancellationToken);
        var outputs = response["properties"]?["outputs"] as JsonObject ?? [];
        return new JsonObject(outputs.Select(pair =>
            KeyValuePair.Create(pair.Key, pair.Value?["value"]?.DeepClone())));
    }
}
