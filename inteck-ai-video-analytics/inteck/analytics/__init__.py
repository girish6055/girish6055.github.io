"""Analytic registry.

Every analytic is addressed by the ``type`` string used in config.json.
"""
from typing import Dict, List, Type

from ..config import CameraConfig
from .base import Analytic, FrameContext, Services
from .canteen_timing import CanteenTimingAnalytic
from .counting import PeopleCountingAnalytic, VehicleCountingAnalytic
from .crowd_gathering import CrowdGatheringAnalytic
from .door_tailgating import DoorTailgatingAnalytic
from .machine_idle import MachineIdleAnalytic
from .mobile_phone import MobilePhoneAnalytic
from .ppe_violation import PPEViolationAnalytic
from .restricted_area import RestrictedAreaAnalytic
from .security_post import SecurityPostAnalytic

REGISTRY: Dict[str, Type[Analytic]] = {
    CanteenTimingAnalytic.type_name: CanteenTimingAnalytic,
    RestrictedAreaAnalytic.type_name: RestrictedAreaAnalytic,
    SecurityPostAnalytic.type_name: SecurityPostAnalytic,
    CrowdGatheringAnalytic.type_name: CrowdGatheringAnalytic,
    MobilePhoneAnalytic.type_name: MobilePhoneAnalytic,
    MachineIdleAnalytic.type_name: MachineIdleAnalytic,
    PPEViolationAnalytic.type_name: PPEViolationAnalytic,
    DoorTailgatingAnalytic.type_name: DoorTailgatingAnalytic,
    PeopleCountingAnalytic.type_name: PeopleCountingAnalytic,
    VehicleCountingAnalytic.type_name: VehicleCountingAnalytic,
}


class UnknownAnalytic(KeyError):
    pass


def build_analytics(camera: CameraConfig, services: Services) -> List[Analytic]:
    instances: List[Analytic] = []
    for entry in camera.analytics:
        if not entry.enabled:
            continue
        cls = REGISTRY.get(entry.type)
        if cls is None:
            raise UnknownAnalytic(
                f"Unknown analytic type {entry.type!r} on camera {camera.id}. "
                f"Available: {', '.join(sorted(REGISTRY))}"
            )
        instances.append(cls(camera, entry, services))
    return instances


__all__ = ["REGISTRY", "build_analytics", "Analytic", "FrameContext", "Services", "UnknownAnalytic"]
