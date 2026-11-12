import json
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

ARM_ENDPOINT = "https://management.azure.com"
DEPLOYMENT_API_VERSION = "2022-09-01"


def run_process(executable, arguments):
    try:
        result = subprocess.run(
            [executable, *arguments], capture_output=True, text=True,
            encoding="utf-8", check=False, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(f"Unable to run {executable}: {error}") from error
    if result.returncode:
        raise ValueError(f"{executable} failed: {result.stderr.strip()}")
    return result.stdout


class AzureCliToken:
    def __init__(self, subscription_id):
        self.subscription_id = subscription_id
        self.token = None
        self.refresh_at = 0

    def __call__(self):
        if self.token is None or time.monotonic() >= self.refresh_at:
            executable = shutil.which("az")
            if executable is None:
                raise ValueError("Azure CLI is required for authentication. Run az login first.")
            self.token = run_process(executable, [
                "account", "get-access-token", "--subscription", self.subscription_id,
                "--resource", ARM_ENDPOINT + "/", "--query", "accessToken", "--output", "tsv",
            ]).strip()
            if not self.token:
                raise ValueError("Azure CLI returned an empty access token.")
            self.refresh_at = time.monotonic() + 60
        return self.token


@dataclass
class Response:
    status: int
    headers: dict
    body: dict


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("ARM redirects are not supported.")


class ArmTransport:
    def __init__(self, token):
        self.token = token
        self.opener = urllib.request.build_opener(NoRedirect())

    def __call__(self, method, url, payload=None):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc.lower() != "management.azure.com":
            raise ValueError("Refusing to send credentials outside the public Azure ARM endpoint.")
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8") if payload is not None else None,
            headers={
                "Authorization": "Bearer " + self.token(),
                "Content-Type": "application/json",
            },
            method=method,
        )
        try:
            with self.opener.open(request, timeout=60) as result:
                content = result.read()
                return Response(
                    result.status,
                    {key.lower(): value for key, value in result.headers.items()},
                    json.loads(content) if content else {},
                )
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise ValueError(f"ARM returned HTTP {error.code}: {detail}") from error


class ArmDeployer:
    def __init__(self, transport, sleep=time.sleep, clock=time.monotonic):
        self.transport = transport
        self.sleep = sleep
        self.clock = clock

    def execute(self, config, payload, operation, timeout_seconds=1800):
        if operation not in {"validate", "deploy"}:
            raise ValueError("ARM operation must be validate or deploy.")
        segment = lambda value: urllib.parse.quote(value, safe="")
        resource_url = (
            f"{ARM_ENDPOINT}/subscriptions/{config.subscription_id}"
            f"/resourcegroups/{segment(config.resource_group)}"
            f"/providers/Microsoft.Resources/deployments/{segment(config.deployment_name)}"
            f"?api-version={DEPLOYMENT_API_VERSION}"
        )
        validate_url = resource_url.replace("?api-version=", "/validate?api-version=")
        deadline = self.clock() + timeout_seconds
        response = self.transport(
            "POST" if operation == "validate" else "PUT",
            validate_url if operation == "validate" else resource_url, payload,
        )
        poll_url = None
        while True:
            properties = response.body.get("properties") or {}
            state = response.body.get("status") or properties.get("provisioningState", "")
            error = response.body.get("error") or properties.get("error")
            if error or state.lower() in {"failed", "canceled", "cancelled"}:
                raise ValueError(f"ARM {operation} failed: {json.dumps(error or response.body)}")
            if state.lower() == "succeeded":
                if operation == "validate" or poll_url == resource_url or poll_url is None:
                    return response.body
                poll_url = resource_url
                response = self.transport("GET", poll_url)
                continue
            if (
                operation == "validate" and response.status in {200, 201, 204}
                and not state and poll_url is None
                and not response.headers.get("azure-asyncoperation")
                and not response.headers.get("location")
            ):
                return response.body
            poll_url = (
                response.headers.get("azure-asyncoperation")
                or response.headers.get("location") or poll_url
            )
            if poll_url is None:
                if operation == "validate":
                    raise ValueError("ARM validation returned no polling URL or terminal result.")
                poll_url = resource_url
            try:
                delay = min(max(int(response.headers.get("retry-after", "5")), 0), 30)
            except ValueError:
                delay = 5
            if self.clock() + delay >= deadline:
                raise TimeoutError("ARM operation timed out; Azure may still be creating resources.")
            self.sleep(delay)
            response = self.transport("GET", poll_url)
