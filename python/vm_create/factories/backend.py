from enum import Enum

from ..templates.arm_template import ArmTemplateBackend
from ..templates.bicep import BicepBackend


class BackendType(str, Enum):
    BICEP = "bicep"
    ARM_TEMPLATE = "arm-template"


def create_backend(backend=BackendType.BICEP, *, bicep_path="bicep"):
    backend = BackendType(backend)
    if backend is BackendType.BICEP:
        return BicepBackend(bicep_path)
    if backend is BackendType.ARM_TEMPLATE:
        return ArmTemplateBackend()
    raise ValueError(f"Unsupported backend: {backend.value}")
