from abc import ABC, abstractmethod

from ..arm import ArmDeployer, ArmTransport, AzureCliToken


class TemplateBackend(ABC):
    @abstractmethod
    def build_template(self, config):
        pass

    def execute(self, config, operation, timeout_seconds=1800):
        if config.simulated and operation != "build":
            raise ValueError("Offline resource simulation supports build only; no Azure calls are allowed.")
        payload = config.payload(self.build_template(config))
        if operation == "build":
            return payload
        deployer = ArmDeployer(ArmTransport(AzureCliToken(config.subscription_id)))
        response = deployer.execute(config, payload, operation, timeout_seconds)
        return {
            "operation": operation,
            "state": "Succeeded",
            "outputs": {
                name: value.get("value")
                for name, value in (response.get("properties", {}).get("outputs") or {}).items()
            },
        }
