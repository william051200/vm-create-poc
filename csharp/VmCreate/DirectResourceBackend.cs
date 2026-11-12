using System.Text.Json.Nodes;

namespace VmCreate;

internal sealed class DirectResourceBackend(ArmDeployer arm) : IVmCreateBackend
{
    private const string NetworkApiVersion = "2023-11-01";
    private const string ComputeApiVersion = "2024-07-01";

    public Task<JsonObject> BuildAsync(DeploymentConfig config, CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        var parameters = config.Parameters;
        var vmName = parameters["vmName"]!.GetValue<string>();
        var admin = parameters["adminUsername"]!.GetValue<string>();
        var publicIp = parameters["publicIpEnabled"]?.GetValue<bool>() ?? false;
        var storageSku = parameters["storageSku"]?.GetValue<string>() ?? "Premium_LRS";
        var prefix = $"/subscriptions/{config.SubscriptionId}"
            + $"/resourceGroups/{Uri.EscapeDataString(config.ResourceGroup)}/providers/";
        string Id(string type, string name) => $"{prefix}{type}/{Uri.EscapeDataString(name)}";
        var requests = new JsonArray();

        void Add(string type, string name, JsonObject body)
        {
            var version = type.StartsWith("Microsoft.Network/") ? NetworkApiVersion : ComputeApiVersion;
            body["location"] = parameters["location"]!.DeepClone();
            requests.Add(new JsonObject
            {
                ["resourceType"] = type,
                ["method"] = "PUT",
                ["uri"] = $"{ArmDeployer.Endpoint}{Id(type, name)}?api-version={version}",
                ["body"] = body
            });
        }

        var subnetId = parameters["existingSubnetId"]?.GetValue<string>() ?? "";
        if (subnetId.Length == 0)
        {
            subnetId = $"{Id("Microsoft.Network/virtualNetworks", $"{vmName}-vnet")}/subnets/default";
            Add("Microsoft.Network/virtualNetworks", $"{vmName}-vnet", new JsonObject
            {
                ["properties"] = new JsonObject
                {
                    ["addressSpace"] = new JsonObject { ["addressPrefixes"] = new JsonArray("10.20.0.0/16") },
                    ["subnets"] = new JsonArray(new JsonObject
                    {
                        ["name"] = "default",
                        ["properties"] = new JsonObject { ["addressPrefix"] = "10.20.0.0/24" }
                    })
                }
            });
        }

        var rules = new JsonArray();
        if (publicIp)
        {
            rules.Add(new JsonObject
            {
                ["name"] = "allow-ssh",
                ["properties"] = new JsonObject
                {
                    ["priority"] = 1000,
                    ["direction"] = "Inbound",
                    ["access"] = "Allow",
                    ["protocol"] = "Tcp",
                    ["sourcePortRange"] = "*",
                    ["destinationPortRange"] = "22",
                    ["sourceAddressPrefix"] = parameters["sshSourceCidr"]!.DeepClone(),
                    ["destinationAddressPrefix"] = "*"
                }
            });
        }
        Add("Microsoft.Network/networkSecurityGroups", $"{vmName}-nsg",
            new JsonObject { ["properties"] = new JsonObject { ["securityRules"] = rules } });
        if (publicIp)
        {
            Add("Microsoft.Network/publicIPAddresses", $"{vmName}-pip", new JsonObject
            {
                ["sku"] = new JsonObject { ["name"] = "Standard" },
                ["properties"] = new JsonObject { ["publicIPAllocationMethod"] = "Static" }
            });
        }

        var ipConfiguration = new JsonObject
        {
            ["privateIPAllocationMethod"] = "Dynamic",
            ["subnet"] = new JsonObject { ["id"] = subnetId }
        };
        if (publicIp)
            ipConfiguration["publicIPAddress"] = new JsonObject
            {
                ["id"] = Id("Microsoft.Network/publicIPAddresses", $"{vmName}-pip")
            };
        Add("Microsoft.Network/networkInterfaces", $"{vmName}-nic", new JsonObject
        {
            ["properties"] = new JsonObject
            {
                ["networkSecurityGroup"] = new JsonObject
                {
                    ["id"] = Id("Microsoft.Network/networkSecurityGroups", $"{vmName}-nsg")
                },
                ["ipConfigurations"] = new JsonArray(new JsonObject
                {
                    ["name"] = "ipconfig1",
                    ["properties"] = ipConfiguration
                })
            }
        });

        var disks = new JsonArray();
        foreach (var size in parameters["dataDiskSizesGb"]?.AsArray() ?? [])
        {
            disks.Add(new JsonObject
            {
                ["lun"] = disks.Count,
                ["createOption"] = "Empty",
                ["diskSizeGB"] = size!.DeepClone(),
                ["managedDisk"] = new JsonObject { ["storageAccountType"] = storageSku },
                ["caching"] = "None",
                ["deleteOption"] = "Detach"
            });
        }
        Add("Microsoft.Compute/virtualMachines", vmName, new JsonObject
        {
            ["properties"] = new JsonObject
            {
                ["hardwareProfile"] = new JsonObject
                {
                    ["vmSize"] = parameters["vmSize"]?.GetValue<string>() ?? "Standard_D2s_v5"
                },
                ["osProfile"] = new JsonObject
                {
                    ["computerName"] = vmName,
                    ["adminUsername"] = admin,
                    ["linuxConfiguration"] = new JsonObject
                    {
                        ["disablePasswordAuthentication"] = true,
                        ["ssh"] = new JsonObject
                        {
                            ["publicKeys"] = new JsonArray(new JsonObject
                            {
                                ["path"] = $"/home/{admin}/.ssh/authorized_keys",
                                ["keyData"] = parameters["sshPublicKey"]!.DeepClone()
                            })
                        }
                    }
                },
                ["storageProfile"] = new JsonObject
                {
                    ["imageReference"] = parameters["imageReference"]?.DeepClone() ?? new JsonObject
                    {
                        ["publisher"] = "Canonical",
                        ["offer"] = "0001-com-ubuntu-server-jammy",
                        ["sku"] = "22_04-lts-gen2",
                        ["version"] = "latest"
                    },
                    ["osDisk"] = new JsonObject
                    {
                        ["createOption"] = "FromImage",
                        ["diskSizeGB"] = parameters["osDiskSizeGb"]?.GetValue<int>() ?? 32,
                        ["managedDisk"] = new JsonObject { ["storageAccountType"] = storageSku },
                        ["caching"] = "ReadWrite",
                        ["deleteOption"] = "Delete"
                    },
                    ["dataDisks"] = disks
                },
                ["networkProfile"] = new JsonObject
                {
                    ["networkInterfaces"] = new JsonArray(new JsonObject
                    {
                        ["id"] = Id("Microsoft.Network/networkInterfaces", $"{vmName}-nic"),
                        ["properties"] = new JsonObject { ["primary"] = true, ["deleteOption"] = "Delete" }
                    })
                },
                ["securityProfile"] = new JsonObject
                {
                    ["securityType"] = "TrustedLaunch",
                    ["uefiSettings"] = new JsonObject { ["secureBootEnabled"] = true, ["vTpmEnabled"] = true }
                },
                ["diagnosticsProfile"] = new JsonObject
                {
                    ["bootDiagnostics"] = new JsonObject { ["enabled"] = true }
                }
            }
        });
        return Task.FromResult(new JsonObject { ["backend"] = "direct", ["requests"] = requests });
    }

    public async Task<JsonObject> ExecuteAsync(
        DeploymentConfig config, JsonObject plan, string operation, CancellationToken cancellationToken)
    {
        var resources = new Dictionary<string, JsonObject>();
        foreach (var request in plan["requests"]!.AsArray())
        {
            var result = await arm.PutResourceAsync(request!["uri"]!.GetValue<string>(),
                request["body"]!.AsObject(), cancellationToken);
            resources[request["resourceType"]!.GetValue<string>()] = result;
        }
        var vm = resources["Microsoft.Compute/virtualMachines"];
        var nic = resources["Microsoft.Network/networkInterfaces"];
        resources.TryGetValue("Microsoft.Network/publicIPAddresses", out var publicIp);
        return new JsonObject
        {
            ["vmId"] = vm["id"]?.DeepClone()
                ?? throw new InvalidOperationException("ARM returned no VM resource ID."),
            ["nicId"] = nic["id"]?.DeepClone()
                ?? throw new InvalidOperationException("ARM returned no NIC resource ID."),
            ["privateIpAddress"] = nic["properties"]?["ipConfigurations"]?.AsArray()
                .FirstOrDefault()?["properties"]?["privateIPAddress"]?.DeepClone() ?? JsonValue.Create(""),
            ["publicIpAddress"] = publicIp?["properties"]?["ipAddress"]?.DeepClone() ?? JsonValue.Create(""),
            ["virtualMachine"] = vm.DeepClone(),
            ["networkInterface"] = nic.DeepClone()
        };
    }
}
