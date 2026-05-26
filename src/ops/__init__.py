"""Out-of-band operations for the robot.

The ``ops`` package holds everything that talks to a running robot
*outside* the system bus:

- :class:`ControlPlane` -- WebSocket-based maintenance hatch on its
  own TCP port, token-authenticated, localhost-only by default;
- :mod:`src.ops.operations` -- the sovereign operations the control
  plane exposes (``status``, ``component.list``, ``shutdown``, ...).

These run in the same process as the robot but speak to it through
plain Python method calls -- bypassing the bus entirely. That is the
point: when the bus is wedged, an operator must still be able to
inspect and stop the robot.
"""

from src.ops.operations import (
    OPERATIONS,
    UnknownOperation,
    component_list,
    dispatch,
    shutdown,
    status,
)
from src.ops.server import ControlPlane, ControlPlaneAuthRequired

__all__ = [
    "OPERATIONS",
    "ControlPlane",
    "ControlPlaneAuthRequired",
    "UnknownOperation",
    "component_list",
    "dispatch",
    "shutdown",
    "status",
]
