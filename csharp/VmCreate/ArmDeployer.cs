using System.Net.Http.Headers;
using System.Text;
using System.Text.Json.Nodes;

namespace VmCreate;

public sealed class ArmDeployer(
    HttpClient http, Func<CancellationToken, Task<string>> getToken)
{
    public const string Endpoint = "https://management.azure.com";
    private const string ApiVersion = "2022-09-01";

    public async Task<JsonObject> ExecuteAsync(
        DeploymentConfig config, JsonObject payload, string operation, CancellationToken cancellationToken)
    {
        if (operation is not ("validate" or "deploy"))
            throw new ArgumentException("ARM operation must be validate or deploy.");
        var resourceUrl = $"{Endpoint}/subscriptions/{config.SubscriptionId}"
            + $"/resourcegroups/{Uri.EscapeDataString(config.ResourceGroup)}"
            + "/providers/Microsoft.Resources/deployments/"
            + $"{Uri.EscapeDataString(config.DeploymentName)}?api-version={ApiVersion}";
        return await ExecuteRequestAsync(resourceUrl, payload, operation, false, cancellationToken);
    }

    public Task<JsonObject> PutResourceAsync(
        string resourceUrl, JsonObject payload, CancellationToken cancellationToken) =>
        ExecuteRequestAsync(resourceUrl, payload, "create resource", true, cancellationToken);

    private async Task<JsonObject> ExecuteRequestAsync(
        string resourceUrl, JsonObject payload, string operation, bool directResource,
        CancellationToken cancellationToken)
    {
        var validateUrl = resourceUrl.Replace("?api-version=", "/validate?api-version=");
        var response = await SendAsync(operation == "validate" ? HttpMethod.Post : HttpMethod.Put,
            operation == "validate" ? validateUrl : resourceUrl, payload, cancellationToken);
        string? pollUrl = null;
        while (true)
        {
            var properties = response.Body["properties"] as JsonObject;
            var state = response.Body["status"]?.GetValue<string>()
                ?? properties?["provisioningState"]?.GetValue<string>() ?? "";
            var error = response.Body["error"] ?? properties?["error"];
            if (error is not null || new[] { "failed", "canceled", "cancelled" }.Contains(state.ToLowerInvariant()))
                throw new InvalidOperationException($"ARM {operation} failed: {error ?? response.Body}");
            if (state.Equals("Succeeded", StringComparison.OrdinalIgnoreCase))
            {
                if (operation == "validate" || pollUrl == resourceUrl || pollUrl is null)
                    return response.Body;
                pollUrl = resourceUrl;
                response = await SendAsync(HttpMethod.Get, pollUrl, null, cancellationToken);
                continue;
            }
            if ((operation == "validate" || directResource)
                && response.Status is 200 or 201 or 204 && state.Length == 0
                && (pollUrl is null || pollUrl == resourceUrl)
                && Header(response, "Azure-AsyncOperation") is null
                && Header(response, "Operation-Location") is null
                && Header(response, "Location") is null)
                return response.Body;
            pollUrl = Header(response, "Azure-AsyncOperation")
                ?? Header(response, "Operation-Location") ?? Header(response, "Location") ?? pollUrl;
            if (pollUrl is null)
            {
                if (operation == "validate")
                    throw new InvalidOperationException("ARM validation returned no polling URL or terminal result.");
                pollUrl = resourceUrl;
            }
            var seconds = int.TryParse(Header(response, "Retry-After"), out var delay)
                ? Math.Clamp(delay, 0, 30) : 5;
            await Task.Delay(TimeSpan.FromSeconds(seconds), cancellationToken);
            response = await SendAsync(HttpMethod.Get, pollUrl, null, cancellationToken);
        }
    }

    private static string? Header(ArmResponse response, string name) =>
        response.Headers.GetValueOrDefault(name);

    private async Task<ArmResponse> SendAsync(
        HttpMethod method, string url, JsonObject? payload, CancellationToken cancellationToken)
    {
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri)
            || uri.Scheme != "https" || uri.Authority != "management.azure.com")
            throw new ArgumentException("Refusing to send credentials outside the public Azure ARM endpoint.");
        using var request = new HttpRequestMessage(method, uri);
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", await getToken(cancellationToken));
        if (payload is not null)
            request.Content = new StringContent(payload.ToJsonString(), Encoding.UTF8, "application/json");
        using var response = await http.SendAsync(request, cancellationToken);
        var text = await response.Content.ReadAsStringAsync(cancellationToken);
        if (!response.IsSuccessStatusCode)
            throw new HttpRequestException($"ARM returned HTTP {(int)response.StatusCode}: {text}");
        var body = string.IsNullOrWhiteSpace(text) ? new JsonObject()
            : JsonNode.Parse(text) as JsonObject
                ?? throw new InvalidOperationException("ARM returned a non-object response.");
        var headers = response.Headers.ToDictionary(
            pair => pair.Key, pair => string.Join(",", pair.Value), StringComparer.OrdinalIgnoreCase);
        return new((int)response.StatusCode, headers, body);
    }

    private sealed record ArmResponse(int Status, Dictionary<string, string> Headers, JsonObject Body);
}
