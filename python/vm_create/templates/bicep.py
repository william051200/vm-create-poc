import json
from pathlib import Path

from ..arm import run_process
from .base import TemplateBackend

TEMPLATE_DIR = Path(__file__).resolve().parent / "bicep"


def compile_template(bicep_path):
    output = run_process(str(bicep_path), ["build", str(TEMPLATE_DIR / "main.bicep"), "--stdout"])
    template = json.loads(output)
    if not isinstance(template, dict) or "resources" not in template:
        raise ValueError("Bicep compiler did not return an ARM template.")
    return template


class BicepBackend(TemplateBackend):
    def __init__(self, bicep_path="bicep"):
        self.bicep_path = bicep_path

    def build_template(self, config):
        return compile_template(self.bicep_path)
