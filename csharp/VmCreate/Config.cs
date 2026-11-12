using System.Buffers.Binary;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace VmCreate;

public sealed record DeploymentConfig(
    string SubscriptionId, string ResourceGroup, string DeploymentName, JsonObject Parameters)
{
    public JsonObject Payload(JsonObject template) => new()
    {
        ["properties"] = new JsonObject
        {
            ["mode"] = "Incremental",
            ["template"] = template.DeepClone(),
            ["parameters"] = new JsonObject(Parameters.Select(pair =>
                KeyValuePair.Create<string, JsonNode?>(
                    pair.Key, new JsonObject { ["value"] = pair.Value?.DeepClone() })))
        }
    };

    public static DeploymentConfig Load(string path)
    {
        path = Path.GetFullPath(path);
        var data = JsonNode.Parse(File.ReadAllText(path)) as JsonObject
            ?? throw new ArgumentException("Configuration must be a JSON object.");
        string[] fields =
            ["subscriptionId", "resourceGroup", "deploymentName", "sshPublicKeyPath", "parameters"];
        Require(data.All(pair => fields.Contains(pair.Key)), "Unknown configuration field.");
        foreach (var field in fields)
            Require(data.ContainsKey(field), $"Missing configuration field: {field}");
        Require(Guid.TryParseExact(String(data, "subscriptionId"), "D", out var subscription),
            "subscriptionId must be a UUID.");
        foreach (var (name, limit) in new[] { ("resourceGroup", 90), ("deploymentName", 64) })
        {
            var value = String(data, name);
            Require(Regex.IsMatch(value, $@"\A[A-Za-z0-9_.()-]{{1,{limit}}}\z")
                && !value.EndsWith('.'), $"{name} has an invalid name.");
        }
        var parameters = data["parameters"] as JsonObject
            ?? throw new ArgumentException("parameters must be an object.");
        string[] strings =
            ["vmName", "location", "adminUsername", "vmSize", "storageSku", "sshSourceCidr", "existingSubnetId"];
        string[] allowed = [.. strings, "osDiskSizeGb", "dataDiskSizesGb", "publicIpEnabled", "imageReference"];
        Require(parameters.All(pair => allowed.Contains(pair.Key)),
            "Unknown template parameter; supply the SSH key through sshPublicKeyPath.");
        foreach (var field in new[] { "vmName", "location", "adminUsername" })
            Require(parameters.ContainsKey(field), $"Missing template parameter: {field}");
        foreach (var field in strings.Where(parameters.ContainsKey))
            _ = String(parameters, field);
        var nameValue = String(parameters, "vmName");
        Require(Regex.IsMatch(nameValue, @"\A[A-Za-z][A-Za-z0-9-]{0,55}\z")
            && !nameValue.EndsWith('-'),
            "vmName must start with a letter, contain letters/digits/hyphens, "
            + "not end with a hyphen, and be at most 56 characters.");
        Require(!string.IsNullOrWhiteSpace(String(parameters, "location")), "location cannot be empty.");
        var username = String(parameters, "adminUsername");
        Require(Regex.IsMatch(username, @"\A[a-z_][a-z0-9_-]{0,31}\z")
            && !new[] { "root", "admin", "administrator" }.Contains(username),
            "adminUsername must be a non-reserved Linux username, at most 32 characters.");
        if (parameters.ContainsKey("vmSize"))
            Require(!string.IsNullOrWhiteSpace(String(parameters, "vmSize")), "vmSize cannot be empty.");
        if (parameters.ContainsKey("storageSku"))
            Require(new[] { "Standard_LRS", "StandardSSD_LRS", "Premium_LRS" }
                .Contains(String(parameters, "storageSku")), "Unsupported storageSku.");
        if (parameters.ContainsKey("osDiskSizeGb"))
            Require(PositiveInteger(parameters["osDiskSizeGb"]), "osDiskSizeGb must be a positive integer.");
        if (parameters.ContainsKey("dataDiskSizesGb"))
        {
            Require(parameters["dataDiskSizesGb"] is JsonArray, "dataDiskSizesGb must be an array.");
            var sizes = (JsonArray)parameters["dataDiskSizesGb"]!;
            Require(sizes.Count <= 64 && sizes.All(PositiveInteger),
                "dataDiskSizesGb must contain at most 64 positive integers.");
        }
        var publicIp = false;
        if (parameters.ContainsKey("publicIpEnabled"))
        {
            Require(parameters["publicIpEnabled"] is JsonValue flag && flag.TryGetValue<bool>(out _),
                "publicIpEnabled must be a boolean.");
            publicIp = parameters["publicIpEnabled"]!.GetValue<bool>();
        }
        var cidr = parameters.ContainsKey("sshSourceCidr") ? String(parameters, "sshSourceCidr") : "";
        if (cidr.Length > 0)
        {
            var parts = cidr.Split('/');
            Require(parts.Length == 2 && IPAddress.TryParse(parts[0], out _),
                "sshSourceCidr must be a canonical IPv4 CIDR.");
            var ip = IPAddress.Parse(parts[0]);
            Require(ip.AddressFamily == AddressFamily.InterNetwork && ip.ToString() == parts[0]
                && int.TryParse(parts[1], out var _) && int.Parse(parts[1]) is > 0 and <= 32,
                "sshSourceCidr must be IPv4 and cannot allow the entire Internet.");
            var prefix = int.Parse(parts[1]);
            Require(prefix.ToString() == parts[1], "sshSourceCidr must be a canonical IPv4 CIDR.");
            var hostMask = prefix == 32 ? 0u : uint.MaxValue >> prefix;
            Require((BinaryPrimitives.ReadUInt32BigEndian(ip.GetAddressBytes()) & hostMask) == 0,
                "sshSourceCidr must be a canonical IPv4 CIDR.");
        }
        Require(!publicIp || cidr.Length > 0, "Public IP requires sshSourceCidr.");
        var subnet = parameters.ContainsKey("existingSubnetId") ? String(parameters, "existingSubnetId") : "";
        if (subnet.Length > 0)
            Require(Regex.IsMatch(subnet,
                $@"\A/subscriptions/{subscription:D}/resourceGroups/[^/]+"
                + @"/providers/Microsoft.Network/virtualNetworks/[^/]+/subnets/[^/]+\z",
                RegexOptions.IgnoreCase),
                "existingSubnetId must be a subnet ID in the configured subscription.");
        if (parameters.ContainsKey("imageReference"))
        {
            var image = parameters["imageReference"] as JsonObject;
            string[] imageFields = ["publisher", "offer", "sku", "version"];
            Require(image is not null && image.Count == 4 && imageFields.All(image.ContainsKey),
                "imageReference requires non-empty publisher, offer, sku, and version.");
            foreach (var field in imageFields)
                Require(!string.IsNullOrWhiteSpace(String(image!, field)),
                    "imageReference fields cannot be empty.");
        }
        var keyPath = String(data, "sshPublicKeyPath");
        Require(!string.IsNullOrWhiteSpace(keyPath), "sshPublicKeyPath is required.");
        var key = File.ReadAllText(Path.Combine(Path.GetDirectoryName(path)!, keyPath)).Trim();
        var keyParts = key.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries);
        Require(!key.Contains('\n') && !key.Contains('\r') && keyParts.Length >= 2
            && keyParts[0] is "ssh-rsa" or "ssh-ed25519",
            "Supply a single-line RSA or Ed25519 SSH public key, never a private key.");
        byte[] decoded;
        try { decoded = Convert.FromBase64String(keyParts[1]); }
        catch (FormatException error) { throw new ArgumentException("SSH public key is not valid base64.", error); }
        var keyType = Encoding.ASCII.GetBytes(keyParts[0]);
        Require(decoded.Length > 4 + keyType.Length
            && BinaryPrimitives.ReadInt32BigEndian(decoded) == keyType.Length
            && decoded.AsSpan(4, keyType.Length).SequenceEqual(keyType),
            "SSH public key has an invalid key type header.");
        parameters = (JsonObject)parameters.DeepClone();
        parameters["sshPublicKey"] = key;
        return new(subscription.ToString("D"), String(data, "resourceGroup"),
            String(data, "deploymentName"), parameters);
    }

    private static bool PositiveInteger(JsonNode? node) =>
        node is JsonValue value && value.TryGetValue<int>(out var number) && number > 0;

    private static string String(JsonObject data, string name) =>
        data[name] is JsonValue value && value.TryGetValue<string>(out var text)
            ? text : throw new ArgumentException($"{name} must be a string.");

    private static void Require(bool condition, string message)
    {
        if (!condition) throw new ArgumentException(message);
    }
}
