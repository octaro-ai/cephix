"""Kernels: deterministic state machines that turn inputs into bus traffic.

A kernel is the bus component that receives :class:`RobotInput` from
channels, walks a fixed-shape pipeline (``observe -> plan -> act ->
finalize -> respond``) and publishes the resulting traffic. It does
*not* make decisions itself -- it consults an :class:`ActorPort`
during its ``act`` phase and shapes the result into a bus message.

Iteration 1 ships :class:`BaseKernel`: a directly-usable, fully
default-implemented kernel that paired with a configured actor
forms the smallest end-to-end loop. Specializing kernels
(``ChatKernel``, ``LLMKernel``, ``PlanExecuteKernel``) override the
phases that need their own logic without touching the loop, the
phase telemetry or the lifecycle.
"""

from src.kernel.base import BaseKernel
from src.kernel.ports import KernelPort
from src.kernel.run import RunContext, RunPhase

__all__ = ["BaseKernel", "KernelPort", "RunContext", "RunPhase"]
