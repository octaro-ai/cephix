"""Cephix system bus -- contract and first implementation.

``AsyncioBus`` is exposed lazily so importing :mod:`src.bus.messages`
(which :mod:`src.components` does at import time) doesn't trigger
loading ``asyncio_bus``, which itself imports ``src.components`` --
the classic mid-init circular import. The lazy ``__getattr__``
below keeps ``from src.bus import AsyncioBus`` working without
participating in that cycle.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.bus.asyncio_bus import AsyncioBus as AsyncioBus  # re-exported

from src.bus.messages import (
    AUDIT_TOPIC,
    HARNESS_CAPABILITIES_TOPIC,
    INPUT_TOPIC,
    KERNEL_PHASE_TOPIC,
    LIFECYCLE_TOPIC,
    OUTPUT_TOPIC,
    CommandNotify,
    CommandRequest,
    CommandResponse,
    ComponentInfo,
    ComponentLifecycle,
    ComponentRequest,
    ComponentResponse,
    ErrorInfo,
    Failable,
    HarnessCapabilities,
    KernelPhase,
    LifecycleAware,
    LifecyclePhase,
    MountEvent,
    ResultStatus,
    RobotAuditNote,
    RobotEvent,
    RobotInput,
    RobotLifecycle,
    RobotOutput,
    command_notify_topic,
    command_request_topic,
    command_response_topic,
    component_lifecycle_topic,
    component_mount_topic,
)
from src.bus.ports import BusPort, Subscription

__all__ = [
    "AUDIT_TOPIC",
    "AsyncioBus",
    "BusPort",
    "CommandNotify",
    "CommandRequest",
    "CommandResponse",
    "ComponentInfo",
    "ComponentLifecycle",
    "ComponentRequest",
    "ComponentResponse",
    "ErrorInfo",
    "Failable",
    "HARNESS_CAPABILITIES_TOPIC",
    "HarnessCapabilities",
    "INPUT_TOPIC",
    "KERNEL_PHASE_TOPIC",
    "KernelPhase",
    "LIFECYCLE_TOPIC",
    "LifecycleAware",
    "LifecyclePhase",
    "MountEvent",
    "OUTPUT_TOPIC",
    "ResultStatus",
    "RobotAuditNote",
    "RobotEvent",
    "RobotInput",
    "RobotLifecycle",
    "RobotOutput",
    "Subscription",
    "command_notify_topic",
    "command_request_topic",
    "command_response_topic",
    "component_lifecycle_topic",
    "component_mount_topic",
]


def __getattr__(name: str) -> Any:
    """Lazy attribute access for the bus package.

    Lets ``from src.bus import AsyncioBus`` work without importing
    :mod:`src.bus.asyncio_bus` at package-init time. The deferred
    import breaks the circular dependency between
    :mod:`src.components` and :mod:`src.bus.asyncio_bus`.
    """
    if name == "AsyncioBus":
        from src.bus.asyncio_bus import AsyncioBus

        return AsyncioBus
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
