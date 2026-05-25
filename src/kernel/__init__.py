"""Kernel implementations.

Iteration 1 ships a single trivial kernel (:class:`EchoKernel`) that
turns every ``RobotInput`` directly into a ``RobotOutput``. Real
context-curating kernels with actor delegation come in later iterations.
"""

from src.kernel.echo import EchoKernel
from src.kernel.ports import KernelPort

__all__ = ["EchoKernel", "KernelPort"]
