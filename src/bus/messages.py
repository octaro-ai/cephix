"""Message types carried on the system bus.

The current set of subtypes covers the bus contract from
``docs/architecture/robot-os-target.md``:

- :class:`RobotInput`    -- conversational input from the outside world.
- :class:`RobotOutput`   -- message to the outside world (fire-and-forget).
- :class:`RobotRequest`  -- directed request between bus components
  that expects a response.
- :class:`RobotResponse` -- reply to a ``RobotRequest``, correlated via
  ``correlation_id``.
- :class:`RobotReady`    -- broadcast lifecycle event published once
  bring-up is complete; carries the robot's identity and a snapshot of
  the components it composed itself from.
- :class:`RobotShutdown` -- broadcast lifecycle event announcing the
  start of a graceful shutdown. Components that need to drain may
  subscribe and react before they are stopped.

``RobotTrigger`` and ``RobotAuditNote`` are intentionally added later,
once a concrete use case requires them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


LIFECYCLE_TOPIC = "robot.lifecycle"


def _new_event_id() -> str:
    return f"evt-{uuid.uuid4().hex[:12]}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, kw_only=True)
class RobotEvent:
    """Base class for every bus message.

    The required fields follow the bus contract from
    ``docs/architecture/robot-os-target.md``. ``correlation_id`` is
    optional here and becomes mandatory on requests and responses.
    """

    topic: str
    principal: str
    source: str
    run_id: str
    event_id: str = field(default_factory=_new_event_id)
    correlation_id: str | None = None
    timestamp: str = field(default_factory=_now_iso)


@dataclass(frozen=True, kw_only=True)
class RobotInput(RobotEvent):
    """Conversational input from the outside world.

    Published by a channel when the outside world hands something to the
    bus. Fire-and-forget: the sender does not wait for a specific reply.
    """

    text: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class RobotOutput(RobotEvent):
    """Message destined for the outside world.

    Published by the kernel or a privileged component and delivered to
    the outside world by a channel. Fire-and-forget; a ``RobotOutput``
    can also be unsolicited and is not necessarily a reply to a previous
    input.
    """

    text: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class RobotRequest(RobotEvent):
    """Directed request between bus components.

    Expects a response. ``correlation_id`` must be set so the bus can
    deliver the matching ``RobotResponse`` back to the original sender.
    """

    action: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.correlation_id:
            raise ValueError("RobotRequest requires a correlation_id")
        if not self.action:
            raise ValueError("RobotRequest requires an action")


@dataclass(frozen=True, kw_only=True)
class RobotResponse(RobotEvent):
    """Reply to a ``RobotRequest``.

    ``correlation_id`` must exactly match the originating request. The
    bus uses this field to route the response back to the original
    sender. ``ok`` distinguishes success from failure replies.
    """

    ok: bool = True
    payload: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def __post_init__(self) -> None:
        if not self.correlation_id:
            raise ValueError("RobotResponse requires a correlation_id")
        if not self.ok and not self.error:
            raise ValueError("Failed RobotResponse must carry an error message")


@dataclass(frozen=True)
class ComponentInfo:
    """Snapshot of a single component the robot booted with.

    The robot collects one of these for every bus component it owns
    and publishes them all together in :class:`RobotReady` so that
    audit, observers, and identity-aware components can learn the
    full composition with one event.
    """

    category: str
    type: str
    description: str = ""


@dataclass(frozen=True, kw_only=True)
class RobotReady(RobotEvent):
    """Lifecycle broadcast: the robot is fully booted and ready.

    Published by :class:`src.robot.Robot` exactly once per boot, on
    the lifecycle topic, as a retained broadcast. Components that
    subscribe to ``robot.lifecycle`` receive this event immediately
    upon subscription and can use it as their authoritative source for
    robot identity and component composition.

    Analog: the BIOS POST beep handing off to the bootloader.
    """

    robot_id: str | None = None
    robot_name: str | None = None
    boot_id: str = ""
    components: tuple[ComponentInfo, ...] = ()


@dataclass(frozen=True, kw_only=True)
class RobotShutdown(RobotEvent):
    """Lifecycle broadcast: the robot is starting a graceful shutdown.

    Published as a retained broadcast on the lifecycle topic. Components
    that need to drain (close pending sessions, flush buffers, write
    final audit notes) get ``grace_seconds`` to react before the robot
    starts calling :meth:`stop` on them. After the grace window or once
    every component has acknowledged, the robot proceeds to a hard
    teardown.

    Analog: the SIGTERM that systemd sends before the eventual SIGKILL.
    """

    robot_id: str | None = None
    robot_name: str | None = None
    boot_id: str = ""
    grace_seconds: float = 5.0
    reason: str = ""
