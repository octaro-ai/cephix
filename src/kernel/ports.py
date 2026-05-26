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

from src.components import BusComponent


class KernelPort(BusComponent):
    """Marker base class for kernel implementations.

    Kernels differ from generic bus components only by role and
    privilege, not by surface. The role is documented; the privileges
    are enforced by governance middleware on the bus, not by this type.
    Future kernels can grow actor or capability APIs here without
    changing the generic :class:`BusComponent` lifecycle.
    """
