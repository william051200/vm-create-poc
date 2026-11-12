using System.Text.Json;
using VmCreate;

return await Cli.RunAsync(args);

public static class Cli
{
    public static async Task<int> RunAsync(string[] args)
    {
        if (args.Contains("--help") || args.Contains("-h"))
        {
            Console.WriteLine(
                "vm-create --config <file> [--operation build|validate|deploy] "
                + "[--backend bicep|direct] "
                + "[--bicep-path <executable>] [--timeout-seconds <positive integer>]\n"
                + "Defaults: build, bicep. validate requires bicep. Live operations require az login.");
            return 0;
        }
        try
        {
            var options = new Dictionary<string, string>();
            string[] allowed = ["--config", "--operation", "--backend", "--bicep-path", "--timeout-seconds"];
            for (var index = 0; index < args.Length; index += 2)
            {
                if (!allowed.Contains(args[index]) || index + 1 >= args.Length
                    || !options.TryAdd(args[index], args[index + 1]))
                    throw new ArgumentException("Invalid or duplicate option. Use --help for usage.");
            }
            if (!options.TryGetValue("--config", out var path))
                throw new ArgumentException("--config is required.");
            var operation = options.GetValueOrDefault("--operation", "build");
            if (operation is not ("build" or "validate" or "deploy"))
                throw new ArgumentException("--operation must be build, validate, or deploy.");
            var backend = options.GetValueOrDefault("--backend", "bicep");
            if (backend is not ("bicep" or "direct"))
                throw new ArgumentException("--backend must be bicep or direct.");
            if (!int.TryParse(options.GetValueOrDefault("--timeout-seconds", "1800"), out var seconds) || seconds <= 0)
                throw new ArgumentException("--timeout-seconds must be a positive integer.");
            var config = DeploymentConfig.Load(path);
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(seconds));
            using var handler = new HttpClientHandler { AllowAutoRedirect = false };
            using var http = new HttpClient(handler) { Timeout = TimeSpan.FromSeconds(60) };
            var token = new AzureCliToken(config.SubscriptionId);
            var orchestrator = new VmCreateOrchestrator(new ArmDeployer(http, token.GetAsync));
            var result = await orchestrator.ExecuteAsync(config, operation, backend,
                options.GetValueOrDefault("--bicep-path", "bicep"), timeout.Token);
            Console.WriteLine(result.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
            return 0;
        }
        catch (OperationCanceledException)
        {
            Console.Error.WriteLine("Error: operation timed out; Azure may still be creating resources.");
            return 1;
        }
        catch (Exception error) when (error is ArgumentException or IOException
            or InvalidOperationException or JsonException or HttpRequestException
            or System.ComponentModel.Win32Exception)
        {
            Console.Error.WriteLine($"Error: {error.Message}");
            return 1;
        }
    }
}
