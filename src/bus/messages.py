"""Message types carried on the system bus.

The current set of subtypes covers the bus contract from
``docs/architecture/robot-os-target.md``:

- :class:`RobotInput`    -- conversational input from the outside world.
- :class:`RobotOutput`   -- message to the outside world (fire-and-forget).
- :class:`ComponentRequest`  -- directed request between two
  components on the bus that expects a response. ROS-style "service"
  call: each service owns its own topic (e.g. ``tool.invoke`` for
  the future tool execution layer); there is *no* global request
  topic. Correlation via ``correlation_id``.
- :class:`ComponentResponse` -- reply to a ``ComponentRequest``,
  routed back to the original sender via ``correlation_id``.
- :class:`RobotLifecycle` -- single lifecycle broadcast. The
  ``phase`` field discriminates between ``boot`` (skeleton up,
  identity and manifest on the bus), ``ready`` (full service,
  userspace attached) and ``shutdown`` (graceful drain begins).
  All three travel on :data:`LIFECYCLE_TOPIC`, all retained, all
  published by :class:`src.robot.Robot`. Analogues: ``boot`` ~ kernel
  dmesg, ``ready`` ~ ``systemd multi-user.target reached``,
  ``shutdown`` ~ POSIX ``SIGTERM``.
- :class:`RobotAuditNote` -- curated audit note. Components publish
  these via :meth:`RobotComponent.publish_audit` whenever they
  perform an action that should appear in the audit trail. The
  ``AuditNoteSink`` component subscribes to :data:`AUDIT_TOPIC` and
  persists every note. Distinct from raw telemetry: telemetry sees
  *every* event on the bus, audit sees only the deliberately
  recorded ones.
- :class:`KernelPhase` -- emitted by a kernel as it walks through
  the per-input run state machine (``observing`` -> ``planning`` ->
  ``acting`` -> ``finalizing`` -> ``responding`` -> ``done``). The
  Wide-Event log of the kernel: each phase transition carries a
  free-form ``details`` dict where the kernel attaches structured,
  queryable fields (durations, counters, IDs, model names, token
  costs, error codes). The ``done`` event of each run is the
  canonical wide-event row aggregating the full run.

``RobotTrigger`` is intentionally added later, once a concrete use
case requires it.

Topic constants
---------------

Every well-known bus topic lives in this module. Components that
publish or subscribe should import the constant rather than hard-code
the string. This keeps the wire vocabulary in one place and makes
renames a one-file refactor.

- :data:`LIFECYCLE_TOPIC` -- :class:`RobotLifecycle` (all phases).
- :data:`AUDIT_TOPIC` -- :class:`RobotAuditNote`.
- :data:`KERNEL_PHASE_TOPIC` -- :class:`KernelPhase`.
- :data:`INPUT_TOPIC` -- :class:`RobotInput` from channels into the
  kernel.
- :data:`OUTPUT_TOPIC` -- :class:`RobotOutput` from the kernel back
  to channels.

Actors deliberately do *not* have a topic here: the kernel calls
into its actor as a direct in-process method call, not via the bus.

:class:`ComponentRequest` / :class:`ComponentResponse` are the
ROS-style "services" of the bus: each service owns its own topic
(e.g. a future ``TOOL_TOPIC = "tool.invoke"``) and the request /
response correlation is end-to-end. There is no global request
topic on purpose -- each service-providing component publishes a
constant in the same module, so subscribers and senders agree on
the wire vocabulary in one place.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal


LIFECYCLE_TOPIC = "robot.lifecycle"
AUDIT_TOPIC = "audit.note"
KERNEL_PHASE_TOPIC = "kernel.phase"
INPUT_TOPIC = "input.message"
OUTPUT_TOPIC = "output.message"


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
class ComponentRequest(RobotEvent):
    """Directed request between bus components.

    Expects a response. ``correlation_id`` must be set so the bus can
    deliver the matching ``ComponentResponse`` back to the original sender.
    """

    action: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.correlation_id:
            raise ValueError("ComponentRequest requires a correlation_id")
        if not self.action:
            raise ValueError("ComponentRequest requires an action")


@dataclass(frozen=True, kw_only=True)
class ComponentResponse(RobotEvent):
    """Reply to a ``ComponentRequest``.

    ``correlation_id`` must exactly match the originating request. The
    bus uses this field to route the response back to the original
    sender. ``ok`` distinguishes success from failure replies.
    """

    ok: bool = True
    payload: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def __post_init__(self) -> None:
        if not self.correlation_id:
            raise ValueError("ComponentResponse requires a correlation_id")
        if not self.ok and not self.error:
            raise ValueError("Failed ComponentResponse must carry an error message")


@dataclass(frozen=True)
class ComponentInfo:
    """Snapshot of a single component the robot booted with.

    The :class:`src.robot.Robot` collects one of these for every
    component it composes itself out of and publishes them all
    together in :class:`RobotLifecycle` (``phase="boot"`` and
    ``phase="ready"``) so that audit, observers and identity-aware
    components can learn the full composition with one event.

    ``category`` is the role bucket (``"bus"``, ``"actor"``,
    ``"kernel"``, ...). ``name`` is the registered component name
    (``"asyncio"``, ``"echo"``, ``"base"``, ...) -- the same string
    users put under ``name:`` in their ``robot.yaml``.
    """

    category: str
    name: str
    description: str = ""


@dataclass(frozen=True, kw_only=True)
class RobotLifecycle(RobotEvent):
    """Broadcast lifecycle phase transition.

    One event class for the entire lifecycle. The :attr:`phase` field
    discriminates between three retained broadcasts on
    :data:`LIFECYCLE_TOPIC`, all published by :class:`src.robot.Robot`
    itself:

    - ``boot`` -- early lifecycle broadcast, *before* kernel and
      channels attach. Carries identity and the full component
      manifest so subscriptions that arrive later (kernel, channels,
      audit sinks) can pick up identity from the retained slot.
      Analog: kernel boot messages (dmesg).
    - ``ready`` -- late lifecycle broadcast, *after* kernel and all
      channels have attached. Same identity and manifest, overrides
      the retained slot so a late subscriber sees the most recent
      authoritative state.
      Analog: ``systemd multi-user.target reached``.
    - ``shutdown`` -- graceful shutdown is starting. Audit sinks
      and other observers learn here that drain has begun. The
      drain itself is *not* coordinated through this event -- the
      robot calls :meth:`RobotComponent.drain` directly on each
      component as a lifecycle hook (analogous to ROS 2's
      ``on_shutdown`` callback or systemd's ``ExecStop``). This
      event is the parallel awareness signal.
      Analog: the SIGTERM that systemd sends before the eventual
      SIGKILL.

    Discriminator design follows :class:`KernelPhase`: structural
    variation lives in a field, not in a subclass. ``boot`` and
    ``ready`` share envelope and manifest; ``shutdown`` shares the
    envelope and may carry an explanatory ``message``. Splitting
    these into three classes would have produced ~95% structural
    overlap -- the InfoQ "schema proliferation" pattern -- without
    any subscriber-side benefit, since the lifecycle topic and the
    publishing component are identical for all three.

    Why no ``grace_seconds``: the shutdown grace window is
    supervisor policy, not part of the broadcast. It lives on
    :class:`src.robot.Robot` (configured via ``shutdown_grace``)
    and is enforced by the robot when calling ``drain()`` on each
    component. Components and observers learn *that* a shutdown
    has begun via this event; the *when* is the robot's
    responsibility, not the message's. Mirrors POSIX ``SIGTERM``
    (no payload), systemd ``TimeoutStopSec=`` (unit property),
    Kubernetes ``terminationGracePeriodSeconds`` (pod spec).

    Why ``message`` instead of ``reason``: the robot is always the
    publisher, so this is the robot's announcement, not a
    justification given by some external party. ``message`` is
    short, human-readable text the robot supplies a default for and
    that operators can override (e.g. via ``Robot.stop(message=...)``).
    Following :class:`KernelPhase.error`, this is a label, not a
    free-form payload -- detailed context belongs in a
    :class:`RobotAuditNote`.
    """

    phase: Literal["boot", "ready", "shutdown"] = "boot"
    robot_id: str | None = None
    robot_name: str | None = None
    boot_id: str = ""
    components: tuple[ComponentInfo, ...] = ()
    message: str = ""

    def __post_init__(self) -> None:
        allowed = ("boot", "ready", "shutdown")
        if self.phase not in allowed:
            raise ValueError(
                f"RobotLifecycle.phase must be one of {allowed}, got {self.phase!r}"
            )


@dataclass(frozen=True, kw_only=True)
class RobotAuditNote(RobotEvent):
    """Curated audit note about an action a component performed.

    Published via :meth:`RobotComponent.publish_audit` and consumed by
    every component of category :attr:`ComponentCategory.AUDIT`. The
    fields mirror a "who did what, on whose behalf, with what
    arguments" structure:

    - ``component`` -- the component whose action this note records.
      Defaults to the publisher (:attr:`RobotEvent.source`) if not
      given. Override only for *publish-on-behalf-of* records, where
      one component records what another did. Canonical example: the
      kernel publishes an audit note for its in-process actor (which
      is not on the bus), so ``source = "kernel.base"`` while
      ``component = "actor.echo"``. Linux audit makes the same split
      between ``auid`` (logging user) and ``uid`` (effective user).
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

    component: str = ""
    action: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.component:
            # Default the doer to the publisher: the common case is a
            # component auditing its own action. Frozen dataclass, so
            # we have to bypass __setattr__.
            object.__setattr__(self, "component", self.source)
        if not self.component:
            raise ValueError(
                "RobotAuditNote requires a non-empty component "
                "(no fallback available because source is empty too)"
            )
        if not self.action:
            raise ValueError("RobotAuditNote requires a non-empty action")


@dataclass(frozen=True, kw_only=True)
class KernelPhase(RobotEvent):
    """Phase transition emitted by a kernel as it processes one input.

    Every kernel run walks a deterministic state machine
    (``observing`` -> ``planning`` -> ``acting`` -> ``finalizing`` ->
    ``responding`` -> ``done``). On each transition the kernel
    publishes one of these events on :data:`KERNEL_PHASE_TOPIC`.

    Wide-event design
    -----------------

    These events are the kernel's *wide-event log* (in the Charity
    Majors / canonical-log-line sense). They are emitted at phase
    *completion*, not at entry -- analogous to an OpenTelemetry span
    that is reported once when its scope closes. Each event carries
    a free-form ``details`` dict where the kernel attaches
    structured, queryable analytics:

    - ``acting`` events carry actor name, actor latency, success
      flag, and -- for LLM actors -- model name, token counts, cost.
    - ``done`` events aggregate the whole run: total duration, total
      iterations, total actor time, outcome (``"ok"`` / ``"error"`` /
      ``"empty"``).
    - ``error`` events name the failed phase and a short error type.

    The ``done`` event is the canonical wide-event row for an entire
    run; phase-specific events are the span-level breakdown. The
    persistence layer writes both to JSONL, so the same data answers
    both "where in the run did time go?" and "show me all failed
    runs in the last hour with model X" without a second source of
    truth.

    Bus-order note: because each event is the *closing* report of
    its phase, a :class:`RobotOutput` produced inside the
    ``responding`` phase appears on the bus *before* the matching
    ``responding`` :class:`KernelPhase` event. The phase event then
    documents the publish via ``output_event_id`` /
    ``output_text_len`` in its details. This is intentional and
    consistent: read phase events as "what just finished",
    not as "what is starting".

    Cardinal rule for ``details``: structured fields only. Durations,
    counts, IDs, model names, hashes, error codes, flags. *Not*: full
    payloads (chat history, full text, raw images) or stack traces.
    Inhalt belongs in :class:`RobotAuditNote`; debug minutiae stay
    out of the wide-event log.

    Fields:

    - ``phase`` -- the phase being entered (mirrors :class:`RunPhase`).
    - ``iteration`` -- 0-based iteration counter; increments only when
      tool round-trips reopen the loop.
    - ``kernel`` -- ``component_name`` of the emitting kernel so
      observers can attribute traffic with multiple kernels on a bus.
    - ``error`` -- non-empty only when ``phase == "error"``; carries
      a short, human-readable error label. Detailed error context
      goes into ``details``.
    - ``details`` -- arbitrary JSONable analytics fields. Empty by
      default; the kernel decides what to put in.

    Distinct from :class:`RobotAuditNote`: phase events are
    fine-grained operational telemetry that the kernel emits for
    *every* run; audit notes are deliberate, semantic records of
    curated actions (tool invocations, approvals, denials).
    """

    phase: str = ""
    iteration: int = 0
    kernel: str = ""
    error: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.phase:
            raise ValueError("KernelPhase requires a non-empty phase")
        if not self.kernel:
            raise ValueError("KernelPhase requires a non-empty kernel")
