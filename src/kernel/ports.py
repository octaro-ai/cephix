"""Kernel port.

A kernel is the bus component that turns observations into decisions:
it subscribes to inputs, curates context, and -- in real iterations --
delegates to actors via ``RobotRequest``/``RobotResponse``. The robot
holds exactly one kernel.

The port intentionally only carries the lifecycle today. Every other
kernel responsibility (state introspection, context image, capability
registration) is reached through the bus, not through this protocol.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.bus.ports import BusComponent


@runtime_checkable
class KernelPort(BusComponent, Protocol):
    """Marker protocol for kernel implementations.

    Kernels differ from generic bus components only by role and
    privilege, not by surface. The role is documented; the privileges
    are enforced by governance middleware on the bus, not by this type.
    """
