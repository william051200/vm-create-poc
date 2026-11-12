import argparse
import json
import sys
import urllib.error

from .config import load_config
from .factories.backend import BackendType, create_backend


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build, validate, or deploy a Linux VM using Bicep or ARM JSON.")
    parser.add_argument("--config", required=True, help="Path to deployment configuration JSON.")
    parser.add_argument(
        "--backend", choices=[backend.value for backend in BackendType],
        default=BackendType.BICEP.value,
    )
    parser.add_argument("--operation", choices=["build", "validate", "deploy"], default="build")
    parser.add_argument("--bicep-path", default="bicep", help="Path to the Bicep executable.")
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    args = parser.parse_args(argv)
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive.")
    try:
        config = load_config(args.config)
        backend = create_backend(args.backend, bicep_path=args.bicep_path)
        result = backend.execute(config, args.operation, args.timeout_seconds)
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, urllib.error.URLError, TimeoutError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
