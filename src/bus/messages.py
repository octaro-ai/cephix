"""Message types carried on the system bus.

The current set of subtypes covers the bus contract from
``docs/architecture/robot-os-target.md``:

- :class:`RobotInput`    -- conversational input from the outside world.
- :class:`RobotOutput`   -- message to the outside world (fire-and-forget).
- :class:`RobotRequest`  -- directed request between bus components
  that expects a response.
- :class:`RobotResponse` -- reply to a ``RobotRequest``, correlated via
  ``correlation_id``.
- :class:`RobotBoot`     -- early lifecycle broadcast: identity and the
  component manifest are now on the bus. Published *before* the kernel
  and channels attach so that their subscriptions immediately learn
  who the robot is. Analog: kernel boot messages / dmesg.
- :class:`RobotReady`    -- late lifecycle broadcast: every component
  has attached, the robot is in full service. Analog:
  ``systemd multi-user.target reached``.
- :class:`RobotShutdown` -- broadcast lifecycle event announcing the
  start of a graceful shutdown. Used by audit/observer subscribers;
  the robot itself coordinates the drain via the
  :meth:`RobotComponent.drain` lifecycle hook, not via a bus ack.
- :class:`RobotAuditNote` -- curated audit note. Components publish
  these via :meth:`RobotComponent.publish_audit` whenever they
  perform an action that should appear in the audit trail. The
  ``AuditNoteSink`` component subscribes to :data:`AUDIT_TOPIC` and
  persists every note. Distinct from raw telemetry: telemetry sees
  *every* event on the bus, audit sees only the deliberately
  recorded ones.

``RobotTrigger`` is intentionally added later, once a concrete use
case requires it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


LIFECYCLE_TOPIC = "robot.lifecycle"
AUDIT_TOPIC = "robot.audit.note"


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

    The :class:`src.robot.Robot` collects one of these for every
    component it composes itself out of and publishes them all
    together in :class:`RobotBoot` so that audit, observers and
    identity-aware components can learn the full composition with one
    event.
    """

    category: str
    type: str
    description: str = ""


@dataclass(frozen=True, kw_only=True)
class RobotBoot(RobotEvent):
    """Lifecycle broadcast: identity and component manifest are on the bus.

    Published by :class:`src.robot.Robot` exactly once per boot, as a
    retained broadcast on ``robot.lifecycle``, *before* the kernel and
    channels attach. Subscribers that arrive later (the kernel,
    channels, audit sinks attached at any time) receive this event
    immediately upon subscription and can use it as their
    authoritative source for robot identity and component composition.

    Analog: kernel boot messages (dmesg) -- "this is who I am, this is
    what I have loaded".
    """

    robot_id: str | None = None
    robot_name: str | None = None
    boot_id: str = ""
    components: tuple[ComponentInfo, ...] = ()


@dataclass(frozen=True, kw_only=True)
class RobotReady(RobotEvent):
    """Lifecycle broadcast: every component has attached, the robot serves.

    Published by :class:`src.robot.Robot` after the kernel and all
    channels have completed their ``start(bus)``. Retained on
    ``robot.lifecycle`` so a late subscriber knows whether the robot
    is in full service or still booting.

    Carries the same identity and component manifest as
    :class:`RobotBoot`: a late subscriber sees only the latest
    retained event on the topic, so the manifest must travel with
    whichever event is the latest one to land.

    Analog: ``systemd multi-user.target reached`` -- the moment the
    system is open for business.
    """

    robot_id: str | None = None
    robot_name: str | None = None
    boot_id: str = ""
    components: tuple[ComponentInfo, ...] = ()


@dataclass(frozen=True, kw_only=True)
class RobotShutdown(RobotEvent):
    """Lifecycle broadcast: the robot is starting a graceful shutdown.

    Published as a retained broadcast on the lifecycle topic.
    Components that need to drain (close pending sessions, flush
    buffers, write final audit notes) get up to ``grace_seconds`` to
    react before the robot starts calling :meth:`stop` on them.

    The drain itself is *not* coordinated through this event -- the
    robot calls :meth:`RobotComponent.drain` directly on each
    component as a lifecycle hook (analogous to ROS 2's
    ``on_shutdown`` callback or systemd's ``ExecStop``). This bus
    event is the parallel observer signal: audit sinks and other
    bystanders learn that a shutdown has begun.

    Analog: the SIGTERM that systemd sends before the eventual
    SIGKILL.
    """

    robot_id: str | None = None
    robot_name: str | None = None
    boot_id: str = ""
    grace_seconds: float = 5.0
    reason: str = ""


@dataclass(frozen=True, kw_only=True)
class RobotAuditNote(RobotEvent):
    """Curated audit note about an action a component performed.

    Published via :meth:`RobotComponent.publish_audit` and consumed by
    every component of category :attr:`ComponentCategory.AUDIT`. The
    fields mirror a "who did what, on whose behalf, with what
    arguments" structure:

    - ``actor`` -- the component identity (typically its
      ``component_type``).
    - ``action`` -- a short machine-readable label (e.g.
      ``"tool.invoke"``, ``"approval.deny"``, ``"mail.send"``).
    - ``details`` -- arbitrary JSONable payload. Sinks serialize this
      to their backing store.

    Distinct from regular bus traffic: an :class:`AuditNoteSink`
    persists *every* :class:`RobotAuditNote` and only those.
    Telemetry observers (``BusRecorder``) that subscribe to all bus
    events will of course also see the note in their full record,
    but the deliberate audit trail is the curated stream on
    ``AUDIT_TOPIC``.
    """

    actor: str = ""
    action: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.actor:
            raise ValueError("RobotAuditNote requires a non-empty actor")
        if not self.action:
            raise ValueError("RobotAuditNote requires a non-empty action")
