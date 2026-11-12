from .diagnostics import resolve_diagnostics
from .disks import resolve_disks
from .identity import resolve_identity
from .image import resolve_image
from .placement import resolve_placement


def resolve_compute(options, parameters, subscription, group, resources=()):
    result = resolve_image(options, parameters, resources)
    plan = result.pop("plan", None)
    result["computeOptions"] = {
        **resolve_disks(options, parameters, subscription, group),
        **resolve_placement(options, subscription, group),
        **resolve_diagnostics(options, resources),
        **resolve_identity(options, subscription, group, resources),
    }
    if plan:
        result["computeOptions"]["plan"] = plan
    return result
