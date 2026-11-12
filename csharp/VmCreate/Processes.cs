using System.Diagnostics;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace VmCreate;

public static class Processes
{
    public static async Task<string> RunAsync(
        string executable, string[] arguments, CancellationToken cancellationToken)
    {
        var path = FindExecutable(executable);
        var info = new ProcessStartInfo
        {
            FileName = path,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true
        };
        if (OperatingSystem.IsWindows() && Path.GetExtension(path).ToLowerInvariant() is ".bat" or ".cmd")
        {
            if (path.IndexOfAny(['"', '%', '!']) >= 0
                || arguments.Any(value => !Regex.IsMatch(value, @"^[A-Za-z0-9:/._-]+$")))
                throw new ArgumentException("Unsafe batch-command path or argument.");
            info.FileName = Environment.GetEnvironmentVariable("ComSpec") ?? "cmd.exe";
            info.Arguments = $"/d /s /c \"\"{path}\" {string.Join(" ", arguments)}\"";
        }
        else
        {
            foreach (var argument in arguments) info.ArgumentList.Add(argument);
        }
        using var process = Process.Start(info)
            ?? throw new IOException($"Unable to start {executable}.");
        var stdout = process.StandardOutput.ReadToEndAsync(cancellationToken);
        var stderr = process.StandardError.ReadToEndAsync(cancellationToken);
        try { await process.WaitForExitAsync(cancellationToken); }
        catch (OperationCanceledException)
        {
            if (!process.HasExited) process.Kill(entireProcessTree: true);
            throw;
        }
        var output = await stdout;
        var error = await stderr;
        if (process.ExitCode != 0)
            throw new InvalidOperationException($"{executable} failed: {error.Trim()}");
        return output;
    }

    private static string FindExecutable(string executable)
    {
        if (Path.IsPathRooted(executable) || executable.Contains(Path.DirectorySeparatorChar)
            || executable.Contains(Path.AltDirectorySeparatorChar))
            return Path.GetFullPath(executable);
        var extensions = OperatingSystem.IsWindows()
            ? new[] { "", ".exe", ".cmd", ".bat" } : new[] { "" };
        foreach (var directory in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(Path.PathSeparator))
        {
            foreach (var extension in extensions)
            {
                var candidate = Path.Combine(directory.Trim('"'), executable + extension);
                if (File.Exists(candidate)) return Path.GetFullPath(candidate);
            }
        }
        throw new FileNotFoundException($"Executable not found: {executable}");
    }

    public static async Task<JsonObject> CompileAsync(string bicepPath, CancellationToken cancellationToken)
    {
        var template = Path.Combine(AppContext.BaseDirectory, "templates", "main.bicep");
        var output = await RunAsync(bicepPath, ["build", template, "--stdout"], cancellationToken);
        var result = JsonNode.Parse(output) as JsonObject;
        if (result is null || !result.ContainsKey("resources"))
            throw new InvalidOperationException("Bicep compiler did not return an ARM template.");
        return result;
    }
}

public sealed class AzureCliToken(string subscriptionId)
{
    private string? token;
    private DateTimeOffset refreshAt;

    public async Task<string> GetAsync(CancellationToken cancellationToken)
    {
        if (token is null || DateTimeOffset.UtcNow >= refreshAt)
        {
            token = (await Processes.RunAsync("az",
                ["account", "get-access-token", "--subscription", subscriptionId,
                 "--resource", ArmDeployer.Endpoint + "/", "--query", "accessToken", "--output", "tsv"],
                cancellationToken)).Trim();
            if (token.Length == 0) throw new InvalidOperationException("Azure CLI returned an empty access token.");
            refreshAt = DateTimeOffset.UtcNow.AddSeconds(60);
        }
        return token;
    }
}
