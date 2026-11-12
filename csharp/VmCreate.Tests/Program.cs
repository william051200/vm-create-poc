using System.Buffers.Binary;
using System.Net;
using System.Text;
using System.Text.Json.Nodes;
using VmCreate;

var tests = new (string Name, Func<Task> Run)[]
{
    ("Configuration preserves types and resolves relative public key", TestConfig),
    ("Configuration rejects unsupported or unsafe parameters", TestInvalidConfig),
    ("Private keys are rejected", TestPrivateKey),
    ("Deploy polls status and fetches final outputs", TestDeploy),
    ("Deploy without async header polls deployment", TestDeploymentPolling),
    ("Validate supports synchronous and asynchronous responses", TestValidate),
    ("Failed and canceled states are errors", TestFailure),
    ("Cancellation stops local polling", TestCancellation),
    ("Foreign polling hosts never receive tokens", TestForeignHost),
    ("Bicep is the default and only sends deployment requests", TestBicepBackend),
    ("Direct build is offline and preserves resource defaults", TestDirectBuild),
    ("Direct build maps existing subnet, public IP, image, and disks", TestDirectOptions),
    ("Direct deploy waits for dependencies and normalizes results", TestDirectDeploy),
    ("Direct deploy returns the public IP with an existing subnet", TestDirectPublicIp),
    ("Direct failures and cancellation stop downstream creation", TestDirectStops),
    ("Direct polling rejects foreign hosts before authentication", TestDirectForeignHost),
    ("CLI selects direct mode and rejects unsupported combinations", TestBackendCli),
    ("Windows batch authentication launcher handles spaces", TestBatchLauncher)
};
var failures = 0;
foreach (var (name, run) in tests)
{
    try { await run(); Console.WriteLine($"PASS {name}"); }
    catch (Exception error) { failures++; Console.Error.WriteLine($"FAIL {name}: {error}"); }
}
Console.WriteLine($"{tests.Length - failures}/{tests.Length} tests passed.");
return failures == 0 ? 0 : 1;

static void Assert(bool condition, string message)
{
    if (!condition) throw new Exception(message);
}

static async Task Throws<T>(Func<Task> run) where T : Exception
{
    try { await run(); }
    catch (T) { return; }
    throw new Exception($"Expected {typeof(T).Name}.");
}

static JsonObject ConfigData() => JsonNode.Parse("""
{
  "subscriptionId": "11111111-1111-1111-1111-111111111111",
  "resourceGroup": "rg-test",
  "deploymentName": "vm-test",
  "sshPublicKeyPath": "key.pub",
  "parameters": {
    "vmName": "test-vm",
    "location": "eastus",
    "adminUsername": "azureuser",
    "dataDiskSizesGb": [32, 64],
    "publicIpEnabled": false
  }
}
""")!.AsObject();

static async Task WithConfig(Func<string, JsonObject, Task> test)
{
    var directory = Path.Combine(AppContext.BaseDirectory, $"test-work-{Guid.NewGuid():N}");
    Directory.CreateDirectory(directory);
    try
    {
        var type = Encoding.ASCII.GetBytes("ssh-ed25519");
        var key = new byte[4 + type.Length + 4 + 32];
        BinaryPrimitives.WriteInt32BigEndian(key, type.Length);
        type.CopyTo(key, 4);
        BinaryPrimitives.WriteInt32BigEndian(key.AsSpan(4 + type.Length), 32);
        File.WriteAllText(Path.Combine(directory, "key.pub"), "ssh-ed25519 " + Convert.ToBase64String(key));
        var config = ConfigData();
        var path = Path.Combine(directory, "config.json");
        File.WriteAllText(path, config.ToJsonString());
        await test(path, config);
    }
    finally { Directory.Delete(directory, recursive: true); }
}

static Task TestConfig() => WithConfig((path, data) =>
{
    var config = DeploymentConfig.Load(path);
    var parameters = config.Payload(new JsonObject { ["resources"] = new JsonArray() })
        ["properties"]!["parameters"]!;
    Assert(parameters["dataDiskSizesGb"]!["value"]!.AsArray().Count == 2, "Lost disk array.");
    Assert(!parameters["publicIpEnabled"]!["value"]!.GetValue<bool>(), "Lost boolean type.");
    Assert(parameters["sshPublicKey"]!["value"]!.GetValue<string>().StartsWith("ssh-ed25519 "), "Key not loaded.");
    data["parameters"]!["publicIpEnabled"] = true;
    data["parameters"]!["sshSourceCidr"] = "203.0.113.4/32";
    data["parameters"]!["existingSubnetId"] = "/subscriptions/11111111-1111-1111-1111-111111111111"
        + "/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/vnet/subnets/default";
    File.WriteAllText(path, data.ToJsonString());
    _ = DeploymentConfig.Load(path);
    return Task.CompletedTask;
});

static Task TestInvalidConfig() => WithConfig(async (path, data) =>
{
    string[] invalid =
    [
        """{"publicIpEnabled":true}""",
        """{"publicIpEnabled":true,"sshSourceCidr":"0.0.0.0/0"}""",
        """{"sshSourceCidr":"10.0.0.1/24"}""",
        """{"dataDiskSizesGb":[true]}""",
        """{"dataDiskSizesGb":[0]}""",
        """{"dataDiskSizesGb":null}""",
        """{"vmName":"invalid;name"}""",
        """{"vmName":"invalid\n"}""",
        """{"adminUsername":"root"}""",
        """{"unknown":"value"}""",
        """{"imageReference":{"id":"unsupported"}}""",
        """{"existingSubnetId":"/subscriptions/other/resourceGroups/rg"}"""
    ];
    foreach (var update in invalid)
    {
        var copy = data.DeepClone().AsObject();
        foreach (var pair in JsonNode.Parse(update)!.AsObject())
            copy["parameters"]![pair.Key] = pair.Value?.DeepClone();
        File.WriteAllText(path, copy.ToJsonString());
        await Throws<ArgumentException>(() => { _ = DeploymentConfig.Load(path); return Task.CompletedTask; });
    }
});

static Task TestPrivateKey() => WithConfig(async (path, data) =>
{
    File.WriteAllText(Path.Combine(Path.GetDirectoryName(path)!, "key.pub"),
        "-----BEGIN OPENSSH PRIVATE KEY-----");
    await Throws<ArgumentException>(() => { _ = DeploymentConfig.Load(path); return Task.CompletedTask; });
});

static DeploymentConfig Deployment() =>
    new("11111111-1111-1111-1111-111111111111", "rg-test", "vm-test", new JsonObject());

static HttpResponseMessage Reply(int status, string body, string? header = null, string? value = null)
{
    var response = new HttpResponseMessage((HttpStatusCode)status)
    {
        Content = new StringContent(body)
    };
    response.Headers.TryAddWithoutValidation("Retry-After", "0");
    if (header is not null) response.Headers.TryAddWithoutValidation(header, value);
    return response;
}

static async Task TestDeploy()
{
    using var handler = new QueueHandler(
        Reply(202, "{}", "Azure-AsyncOperation", "https://management.azure.com/operations/test"),
        Reply(200, """{"status":"Running"}"""),
        Reply(200, """{"status":"Succeeded"}"""),
        Reply(200, """{"properties":{"provisioningState":"Succeeded","outputs":{"vmId":{"value":"vm-id"}}}}"""));
    using var http = new HttpClient(handler);
    var result = await new ArmDeployer(http, _ => Task.FromResult("test-token"))
        .ExecuteAsync(Deployment(), new JsonObject(), "deploy", CancellationToken.None);
    Assert(handler.Calls.Select(call => call.Method).SequenceEqual(["PUT", "GET", "GET", "GET"]),
        "Unexpected request sequence.");
    Assert(result["properties"]!["outputs"]!["vmId"]!["value"]!.GetValue<string>() == "vm-id", "Missing outputs.");
}

static async Task TestDeploymentPolling()
{
    using var handler = new QueueHandler(
        Reply(201, """{"properties":{"provisioningState":"Accepted"}}"""),
        Reply(200, """{"properties":{"provisioningState":"Succeeded"}}"""));
    using var http = new HttpClient(handler);
    await new ArmDeployer(http, _ => Task.FromResult("test-token"))
        .ExecuteAsync(Deployment(), new JsonObject(), "deploy", CancellationToken.None);
    Assert(handler.Calls[0].Url == handler.Calls[1].Url, "Did not poll deployment.");
}

static async Task TestValidate()
{
    foreach (var asynchronous in new[] { false, true })
    {
        using var handler = asynchronous
            ? new QueueHandler(
                Reply(202, "{}", "Location", "https://management.azure.com/operations/check"),
                Reply(200, """{"status":"Succeeded"}"""))
            : new QueueHandler(Reply(200, """{"properties":{}}"""));
        using var http = new HttpClient(handler);
        await new ArmDeployer(http, _ => Task.FromResult("test-token"))
            .ExecuteAsync(Deployment(), new JsonObject(), "validate", CancellationToken.None);
        Assert(handler.Calls[0].Method == "POST" && handler.Calls[0].Url.Contains("/validate?"),
            "Validation used wrong operation.");
    }
    using var failureHandler = new QueueHandler(
        Reply(200, "{}", "Azure-AsyncOperation", "https://management.azure.com/operations/check"),
        Reply(200, """{"status":"Failed"}"""));
    using var failureHttp = new HttpClient(failureHandler);
    await Throws<InvalidOperationException>(() => new ArmDeployer(failureHttp, _ => Task.FromResult("test-token"))
        .ExecuteAsync(Deployment(), new JsonObject(), "validate", CancellationToken.None));
}

static async Task TestFailure()
{
    foreach (var body in new[] { """{"status":"Failed"}""", """{"status":"Canceled"}""",
        """{"error":{"code":"InvalidTemplate"}}""" })
    {
        using var handler = new QueueHandler(Reply(200, body));
        using var http = new HttpClient(handler);
        await Throws<InvalidOperationException>(() => new ArmDeployer(http, _ => Task.FromResult("test-token"))
            .ExecuteAsync(Deployment(), new JsonObject(), "deploy", CancellationToken.None));
    }
}

static async Task TestCancellation()
{
    using var handler = new QueueHandler(Reply(202, "{}"));
    using var http = new HttpClient(handler);
    using var cancellation = new CancellationTokenSource();
    cancellation.Cancel();
    await Throws<OperationCanceledException>(() => new ArmDeployer(http, _ => Task.FromResult("test-token"))
        .ExecuteAsync(Deployment(), new JsonObject(), "deploy", cancellation.Token));
}

static async Task TestForeignHost()
{
    var tokenCalls = 0;
    using var handler = new QueueHandler(
        Reply(202, "{}", "Location", "https://example.com/operations/test"));
    using var http = new HttpClient(handler);
    await Throws<ArgumentException>(() => new ArmDeployer(http, _ =>
    {
        tokenCalls++;
        return Task.FromResult("test-token");
    }).ExecuteAsync(Deployment(), new JsonObject(), "deploy", CancellationToken.None));
    Assert(tokenCalls == 1, "Sent token to foreign host.");
}

static DeploymentConfig VmDeployment()
{
    var parameters = ConfigData()["parameters"]!.DeepClone().AsObject();
    parameters["sshPublicKey"] = "ssh-ed25519 test-public-key";
    return Deployment() with { Parameters = parameters };
}

static JsonObject ResourceRequest(JsonObject plan, string type) =>
    plan["requests"]!.AsArray().Single(request =>
        request!["resourceType"]!.GetValue<string>() == type)!.AsObject();

static JsonObject ResourceBody(JsonObject plan, string type) => ResourceRequest(plan, type)["body"]!.AsObject();

static string ResourcePath(JsonObject plan, string type) =>
    new Uri(ResourceRequest(plan, type)["uri"]!.GetValue<string>()).AbsolutePath;

static VmCreateOrchestrator DirectOrchestrator(HttpClient http, Func<CancellationToken, Task<string>>? token = null) =>
    new(new ArmDeployer(http, token ?? (_ => throw new Exception("Unexpected authentication."))),
        (_, _) => throw new Exception("Direct mode invoked Bicep."));

static async Task TestBicepBackend()
{
    var template = new JsonObject { ["resources"] = new JsonArray() };
    var outputs = JsonNode.Parse("""
        {"vmId":{"value":"vm-id"},"virtualMachine":{"value":{"id":"vm-id","properties":{"nested":[true,null]}}}}
        """)!.AsObject();
    using var handler = new QueueHandler(
        Reply(200, """{"properties":{}}"""),
        Reply(200, new JsonObject
        {
            ["properties"] = new JsonObject { ["provisioningState"] = "Succeeded", ["outputs"] = outputs }
        }.ToJsonString()));
    using var http = new HttpClient(handler);
    var compiled = 0;
    var tokenCalls = 0;
    var orchestrator = new VmCreateOrchestrator(new ArmDeployer(http, _ =>
    {
        tokenCalls++;
        return Task.FromResult("test-token");
    }), (path, _) =>
    {
        Assert(path == "selected-bicep", "Compiler path was not forwarded.");
        compiled++;
        return Task.FromResult(template);
    });
    var config = VmDeployment();
    var build = await orchestrator.ExecuteAsync(config, bicepPath: "selected-bicep");
    Assert(JsonNode.DeepEquals(build, config.Payload(template)), "Default build payload changed.");
    Assert(handler.Calls.Count == 0 && tokenCalls == 0, "Build contacted Azure.");
    var validation = await orchestrator.ExecuteAsync(config, "validate", "bicep", "selected-bicep");
    Assert(validation["outputs"]!.AsObject().Count == 0, "Validation produced resource outputs.");
    var result = await orchestrator.ExecuteAsync(config, "deploy", bicepPath: "selected-bicep");
    Assert(result["operation"]!.GetValue<string>() == "deploy"
        && result["state"]!.GetValue<string>() == "Succeeded", "Result was not normalized.");
    Assert(JsonNode.DeepEquals(result["outputs"]!["virtualMachine"], outputs["virtualMachine"]!["value"]),
        "Bicep output object was not preserved.");
    Assert(compiled == 3 && tokenCalls == 2, "Incorrect compilation or authentication count.");
    Assert(handler.Calls.Select(call => call.Method).SequenceEqual(["POST", "PUT"])
        && handler.Calls.All(call => call.Url.Contains("/providers/Microsoft.Resources/deployments/")),
        "Bicep mode created resources outside the deployment.");
}

static async Task TestDirectBuild()
{
    using var handler = new QueueHandler();
    using var http = new HttpClient(handler);
    var config = VmDeployment();
    config.Parameters.Remove("dataDiskSizesGb");
    config.Parameters.Remove("publicIpEnabled");
    var original = config.Parameters.DeepClone();
    var plan = await DirectOrchestrator(http).ExecuteAsync(config, backend: "direct");
    Assert(plan["requests"]!.AsArray().Select(request => request!["resourceType"]!.GetValue<string>())
        .SequenceEqual(["Microsoft.Network/virtualNetworks", "Microsoft.Network/networkSecurityGroups",
            "Microsoft.Network/networkInterfaces", "Microsoft.Compute/virtualMachines"]),
        "Incorrect default resources or dependency order.");
    var nic = ResourceBody(plan, "Microsoft.Network/networkInterfaces")["properties"]!;
    var ip = nic["ipConfigurations"]![0]!["properties"]!;
    Assert(ip["subnet"]!["id"]!.GetValue<string>()
        == ResourcePath(plan, "Microsoft.Network/virtualNetworks") + "/subnets/default", "Incorrect new subnet ID.");
    Assert(ip["publicIPAddress"] is null, "Private mode attached a public IP.");
    Assert(ResourceBody(plan, "Microsoft.Network/networkSecurityGroups")
        ["properties"]!["securityRules"]!.AsArray().Count == 0, "Private mode opened SSH.");
    var vm = ResourceBody(plan, "Microsoft.Compute/virtualMachines")["properties"]!;
    Assert(vm["storageProfile"]!["dataDisks"]!.AsArray().Count == 0, "Unexpected default data disks.");
    Assert(vm["osProfile"]!["linuxConfiguration"]!["disablePasswordAuthentication"]!.GetValue<bool>()
        && vm["securityProfile"]!["uefiSettings"]!["secureBootEnabled"]!.GetValue<bool>()
        && vm["securityProfile"]!["uefiSettings"]!["vTpmEnabled"]!.GetValue<bool>()
        && vm["diagnosticsProfile"]!["bootDiagnostics"]!["enabled"]!.GetValue<bool>(),
        "SSH, Trusted Launch, or diagnostics settings were lost.");
    Assert(vm["networkProfile"]!["networkInterfaces"]![0]!["id"]!.GetValue<string>()
        == ResourcePath(plan, "Microsoft.Network/networkInterfaces"), "VM did not reference the new NIC.");
    Assert(JsonNode.DeepEquals(config.Parameters, original), "Build mutated configuration.");
    Assert(handler.Calls.Count == 0, "Direct build contacted Azure.");
}

static async Task TestDirectOptions()
{
    using var handler = new QueueHandler();
    using var http = new HttpClient(handler);
    var config = VmDeployment();
    var parameters = config.Parameters;
    parameters["existingSubnetId"] = "/subscriptions/11111111-1111-1111-1111-111111111111"
        + "/resourceGroups/network/providers/Microsoft.Network/virtualNetworks/shared/subnets/default";
    parameters["publicIpEnabled"] = true;
    parameters["sshSourceCidr"] = "203.0.113.4/32";
    parameters["adminUsername"] = "pocadmin";
    parameters["vmSize"] = "Standard_D4s_v5";
    parameters["osDiskSizeGb"] = 64;
    parameters["storageSku"] = "StandardSSD_LRS";
    parameters["imageReference"] = JsonNode.Parse("""
        {"publisher":"Canonical","offer":"ubuntu-24_04-lts","sku":"server","version":"latest"}
        """);
    var plan = await DirectOrchestrator(http).ExecuteAsync(config, backend: "direct");
    Assert(plan["requests"]!.AsArray().Count == 4
        && plan["requests"]!.AsArray().All(request =>
            request!["resourceType"]!.GetValue<string>() != "Microsoft.Network/virtualNetworks"),
        "Existing subnet was recreated.");
    var ip = ResourceBody(plan, "Microsoft.Network/networkInterfaces")
        ["properties"]!["ipConfigurations"]![0]!["properties"]!;
    Assert(JsonNode.DeepEquals(ip["subnet"]!["id"], parameters["existingSubnetId"]), "Lost existing subnet.");
    Assert(ip["publicIPAddress"]!["id"]!.GetValue<string>()
        == ResourcePath(plan, "Microsoft.Network/publicIPAddresses"), "NIC lost public IP reference.");
    var rules = ResourceBody(plan, "Microsoft.Network/networkSecurityGroups")
        ["properties"]!["securityRules"]!.AsArray();
    Assert(rules.Count == 1
        && JsonNode.DeepEquals(rules[0]!["properties"]!["sourceAddressPrefix"], parameters["sshSourceCidr"]),
        "SSH rule did not restrict the configured source.");
    var vm = ResourceBody(plan, "Microsoft.Compute/virtualMachines")["properties"]!;
    var storage = vm["storageProfile"]!;
    Assert(JsonNode.DeepEquals(vm["hardwareProfile"]!["vmSize"], parameters["vmSize"])
        && JsonNode.DeepEquals(storage["imageReference"], parameters["imageReference"])
        && JsonNode.DeepEquals(storage["osDisk"]!["diskSizeGB"], parameters["osDiskSizeGb"])
        && JsonNode.DeepEquals(storage["osDisk"]!["managedDisk"]!["storageAccountType"], parameters["storageSku"]),
        "VM options were not mapped.");
    var key = vm["osProfile"]!["linuxConfiguration"]!["ssh"]!["publicKeys"]![0]!;
    Assert(key["path"]!.GetValue<string>() == "/home/pocadmin/.ssh/authorized_keys"
        && JsonNode.DeepEquals(key["keyData"], parameters["sshPublicKey"]), "SSH key was not mapped.");
    var disks = storage["dataDisks"]!.AsArray();
    Assert(disks.Count == 2 && disks.Select(disk => disk!["lun"]!.GetValue<int>()).SequenceEqual([0, 1])
        && disks.Select(disk => disk!["diskSizeGB"]!.GetValue<int>()).SequenceEqual([32, 64])
        && disks.All(disk => disk!["deleteOption"]!.GetValue<string>() == "Detach"
            && JsonNode.DeepEquals(disk["managedDisk"]!["storageAccountType"], parameters["storageSku"])),
        "Data disk mapping or lifecycle changed.");
}

static async Task TestDirectDeploy()
{
    var config = VmDeployment();
    using var previewHandler = new QueueHandler();
    using var previewHttp = new HttpClient(previewHandler);
    var plan = await DirectOrchestrator(previewHttp).ExecuteAsync(config, backend: "direct");
    var vnetId = ResourcePath(plan, "Microsoft.Network/virtualNetworks");
    var nsgId = ResourcePath(plan, "Microsoft.Network/networkSecurityGroups");
    var nicId = ResourcePath(plan, "Microsoft.Network/networkInterfaces");
    var vmId = ResourcePath(plan, "Microsoft.Compute/virtualMachines");
    var nic = new JsonObject
    {
        ["id"] = nicId,
        ["properties"] = new JsonObject
        {
            ["ipConfigurations"] = new JsonArray(new JsonObject
            {
                ["properties"] = new JsonObject { ["privateIPAddress"] = "10.20.0.4" }
            })
        }
    };
    var vm = new JsonObject
    {
        ["id"] = vmId,
        ["properties"] = new JsonObject { ["provisioningState"] = "Succeeded" }
    };
    using var handler = new QueueHandler(
        Reply(202, "{}", "Operation-Location", "https://management.azure.com/operations/network"),
        Reply(200, """{"status":"Running"}"""),
        Reply(200, """{"status":"Succeeded"}"""),
        Reply(200, new JsonObject { ["id"] = vnetId }.ToJsonString()),
        Reply(200, new JsonObject { ["id"] = nsgId }.ToJsonString()),
        Reply(201, nic.ToJsonString()),
        Reply(202, """{"properties":{"provisioningState":"Accepted"}}"""),
        Reply(200, vm.ToJsonString()));
    using var http = new HttpClient(handler);
    var result = await DirectOrchestrator(http, _ => Task.FromResult("test-token"))
        .ExecuteAsync(config, "deploy", "direct");
    Assert(handler.Calls.Select(call => call.Method)
        .SequenceEqual(["PUT", "GET", "GET", "GET", "PUT", "PUT", "PUT", "GET"]),
        "Resources did not wait for their prerequisites.");
    Assert(handler.Calls[0].Url == handler.Calls[3].Url
        && handler.Calls[6].Url == handler.Calls[7].Url, "Did not fetch final resources.");
    Assert(handler.Calls.All(call => !call.Url.Contains("/Microsoft.Resources/deployments/")),
        "Direct mode submitted an ARM deployment.");
    Assert(handler.Calls[5].Body!["properties"]!["networkSecurityGroup"]!["id"]!.GetValue<string>() == nsgId
        && handler.Calls[6].Body!["properties"]!["networkProfile"]!["networkInterfaces"]![0]!["id"]!
            .GetValue<string>() == nicId, "Dependency references were lost in HTTP bodies.");
    var outputs = result["outputs"]!;
    Assert(result["state"]!.GetValue<string>() == "Succeeded" && outputs.AsObject().Count == 6
        && outputs["vmId"]!.GetValue<string>() == vmId && outputs["nicId"]!.GetValue<string>() == nicId
        && outputs["privateIpAddress"]!.GetValue<string>() == "10.20.0.4"
        && outputs["publicIpAddress"]!.GetValue<string>() == ""
        && JsonNode.DeepEquals(outputs["virtualMachine"], vm)
        && JsonNode.DeepEquals(outputs["networkInterface"], nic), "Direct results were not normalized.");
}

static async Task TestDirectPublicIp()
{
    var config = VmDeployment();
    config.Parameters["existingSubnetId"] = "/subscriptions/11111111-1111-1111-1111-111111111111"
        + "/resourceGroups/network/providers/Microsoft.Network/virtualNetworks/shared/subnets/default";
    config.Parameters["publicIpEnabled"] = true;
    config.Parameters["sshSourceCidr"] = "203.0.113.4/32";
    using var handler = new QueueHandler(
        Reply(200, "{}"),
        Reply(201, """{"properties":{"ipAddress":"203.0.113.20"}}"""),
        Reply(200, """{"id":"nic-id","properties":{"ipConfigurations":[]}}"""),
        Reply(200, """{"id":"vm-id"}"""));
    using var http = new HttpClient(handler);
    var result = await DirectOrchestrator(http, _ => Task.FromResult("test-token"))
        .ExecuteAsync(config, "deploy", "direct");
    Assert(handler.Calls.Count == 4 && handler.Calls.All(call => call.Method == "PUT"),
        "Synchronous resources were unnecessarily polled.");
    Assert(result["outputs"]!["publicIpAddress"]!.GetValue<string>() == "203.0.113.20",
        "Direct mode lost the public IP.");
}

static async Task TestDirectStops()
{
    var config = VmDeployment();
    config.Parameters["existingSubnetId"] = "/subscriptions/11111111-1111-1111-1111-111111111111"
        + "/resourceGroups/network/providers/Microsoft.Network/virtualNetworks/shared/subnets/default";
    foreach (var status in new[] { 200, 400 })
    {
        using var handler = new QueueHandler(Reply(status, """{"error":{"code":"ResourceFailed"}}"""));
        using var http = new HttpClient(handler);
        var orchestrator = DirectOrchestrator(http, _ => Task.FromResult("test-token"));
        if (status == 200)
            await Throws<InvalidOperationException>(() => orchestrator.ExecuteAsync(config, "deploy", "direct"));
        else
            await Throws<HttpRequestException>(() => orchestrator.ExecuteAsync(config, "deploy", "direct"));
        Assert(handler.Calls.Count == 1, "Creation continued after a failure.");
    }
    using var cancellation = new CancellationTokenSource();
    using var canceledHandler = new QueueHandler(Reply(200, "{}"));
    using var canceledHttp = new HttpClient(canceledHandler);
    var tokenCalls = 0;
    await Throws<OperationCanceledException>(() => DirectOrchestrator(canceledHttp, _ =>
    {
        if (++tokenCalls == 2) cancellation.Cancel();
        return Task.FromResult("test-token");
    }).ExecuteAsync(config, "deploy", "direct", cancellationToken: cancellation.Token));
    Assert(canceledHandler.Calls.Count == 1, "Creation continued after cancellation.");
}

static async Task TestDirectForeignHost()
{
    var tokens = 0;
    using var handler = new QueueHandler(
        Reply(202, "{}", "Operation-Location", "https://example.com/operations/network"));
    using var http = new HttpClient(handler);
    var arm = new ArmDeployer(http, _ =>
    {
        tokens++;
        return Task.FromResult("test-token");
    });
    await Throws<ArgumentException>(() => arm.PutResourceAsync(
        "https://example.com/resource", new JsonObject(), CancellationToken.None));
    Assert(tokens == 0 && handler.Calls.Count == 0, "Foreign initial host received credentials.");
    await Throws<ArgumentException>(() => new VmCreateOrchestrator(arm)
        .ExecuteAsync(VmDeployment(), "deploy", "direct"));
    Assert(tokens == 1 && handler.Calls.Count == 1, "Foreign polling host received credentials.");
}

static Task TestBackendCli() => WithConfig(async (path, _) =>
{
    var previousOut = Console.Out;
    var previousError = Console.Error;
    using var output = new StringWriter();
    using var error = new StringWriter();
    try
    {
        Console.SetOut(output);
        Console.SetError(error);
        var status = await Cli.RunAsync(["--config", path, "--backend", "direct",
            "--bicep-path", "nonexistent-bicep-executable"]);
        Assert(status == 0 && error.ToString() == ""
            && JsonNode.Parse(output.ToString())!["backend"]!.GetValue<string>() == "direct",
            "Direct CLI build failed or invoked Bicep.");
        output.GetStringBuilder().Clear();
        status = await Cli.RunAsync(["--config", path, "--backend", "direct", "--operation", "validate"]);
        Assert(status == 1 && output.ToString() == "" && error.ToString().Contains("no ARM template validation"),
            "Direct validation was not explicitly rejected.");
        error.GetStringBuilder().Clear();
        status = await Cli.RunAsync(["--config", path, "--backend", "unknown"]);
        Assert(status == 1 && error.ToString().Contains("--backend must be"), "Unknown backend was accepted.");
    }
    finally
    {
        Console.SetOut(previousOut);
        Console.SetError(previousError);
    }
});

static async Task TestBatchLauncher()
{
    if (!OperatingSystem.IsWindows()) return;
    var directory = Path.Combine(AppContext.BaseDirectory, $"test batch {Guid.NewGuid():N}");
    Directory.CreateDirectory(directory);
    try
    {
        var path = Path.Combine(directory, "fake-az.cmd");
        File.WriteAllText(path, "@echo off\r\necho test-token\r\n");
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(10));
        var output = await Processes.RunAsync(path, ["account", "get-access-token"], timeout.Token);
        Assert(output.Trim() == "test-token", "Batch launcher did not capture output.");
    }
    finally { Directory.Delete(directory, recursive: true); }
}

sealed class QueueHandler(params HttpResponseMessage[] responses) : HttpMessageHandler
{
    private readonly Queue<HttpResponseMessage> responses = new(responses);
    public List<(string Method, string Url, JsonObject? Body)> Calls { get; } = [];

    protected override async Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request, CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        var body = request.Content is null ? null
            : JsonNode.Parse(await request.Content.ReadAsStringAsync(cancellationToken))!.AsObject();
        Calls.Add((request.Method.Method, request.RequestUri!.ToString(), body));
        if (responses.Count == 0) throw new Exception("Unexpected HTTP request.");
        return responses.Dequeue();
    }
}
