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

Result vocabulary
-----------------

Every event that can carry a success-or-failure outcome (``RobotOutput``,
``ComponentResponse``, ``KernelPhase``) inherits from :class:`Failable`.
That mixin pins the contract: a ``status`` discriminator
(:data:`ResultStatus`) plus an optional :class:`ErrorInfo`. The
invariant ``status == "ok"`` iff ``error is None`` is enforced in
:meth:`Failable.__post_init__`.

This unifies error-handling vocabulary across the bus: one place
to add a new canonical error code, one place to adjust the
invariant, one query (``"status":"error"``) to find every failure
in the wide-event log regardless of which event class produced it.

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


def component_lifecycle_topic(name: str) -> str:
    """Per-component lifecycle topic: ``component.<name>.lifecycle``.

    Each component owns its own retain slot via this dedicated topic,
    so a slow-updating component does not get its retained snapshot
    clobbered by a chatty neighbour. Subscribers that want to watch
    *one* component subscribe directly; observers that want to watch
    every component fall back to :meth:`BusPort.subscribe_all` and
    filter on the topic prefix until wildcard subscriptions land in
    the bus.

    The entity-first hierarchy (``component.<name>.*``) clusters all
    events for a single component together -- ``component.<name>.lifecycle``,
    ``component.<name>.mount``, future ``component.<name>.metrics`` --
    so per-component filtering in logs and dashboards is one prefix
    match.
    """
    if not name:
        raise ValueError("component_lifecycle_topic requires a non-empty name")
    return f"component.{name}.lifecycle"


def component_mount_topic(name: str) -> str:
    """Per-component mount-event topic: ``component.<name>.mount``.

    Mirrors :func:`component_lifecycle_topic` for :class:`MountEvent`
    streams. Mount events are intentionally *not* retained: the
    authoritative "what is currently mounted" snapshot lives in the
    retained :class:`ComponentLifecycle` (via
    :attr:`ComponentInfo.metadata`). Mount events are the realtime
    stream of transitions for subscribers that need to react when
    something gets swapped.
    """
    if not name:
        raise ValueError("component_mount_topic requires a non-empty name")
    return f"component.{name}.mount"


def _new_event_id() -> str:
    return f"evt-{uuid.uuid4().hex[:12]}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


ResultStatus = Literal["ok", "warn", "error"]
"""Discriminator on every :class:`Failable` event.

Three values, three cases:

- ``"ok"`` -- the event reports a successful outcome. The companion
  :attr:`Failable.error` field is ``None``.
- ``"warn"`` -- the event reports a soft issue: the operation
  succeeded (or partially succeeded) but the publisher wants to flag
  something. :attr:`Failable.error` carries a structured
  :class:`ErrorInfo` describing the issue (e.g. ``code="cache_fallback"``,
  ``code="rate_limit_retry"``, ``code="partial_result"``).
- ``"error"`` -- the event reports a failure. :attr:`Failable.error`
  carries the structured :class:`ErrorInfo`.

The split between ``warn`` and ``error`` lets channels render a
yellow indicator for soft issues while keeping the green/red
contract simple. Subscribers that care only about hard failures
filter on ``"status":"error"``; subscribers that want every
abnormality filter on ``status != "ok"``.

Stays a string (not an int / not an :class:`enum.Enum`) on purpose:
we are in dynamic Python, the JSONL telemetry is human-read first,
and ``"status":"warn"`` / ``"status":"error"`` filter cleanly with
``rg`` / ``jq`` / ``grep``. HTTP-style numeric status codes carry
web-protocol baggage that does not fit our domain; granular
categorisation lives on :attr:`ErrorInfo.code` instead.
"""


LifecyclePhase = Literal["boot", "ready", "warn", "failure", "shutdown"]
"""Discriminator on every :class:`LifecycleAware` event.

Five values, three groups:

- ``"boot"`` -- the entity has come up but is not yet operational.
  Equivalent to a kernel announcing itself in dmesg before userspace
  is reachable.
- ``"ready"`` -- the entity is fully operational. Equivalent to
  systemd's ``multi-user.target reached``.
- ``"warn"`` -- the entity is operational but flagging an issue
  (degraded mode, retried connection, slow upstream, ...). The
  publisher attaches details via :attr:`LifecycleAware.message` and
  through richer state on the carrier event (e.g. the fresh
  :attr:`ComponentInfo.metadata` snapshot on ``ComponentLifecycle``).
- ``"failure"`` -- the entity is not operational. Distinct from
  ``"shutdown"``: failure is unintended, shutdown is graceful.
- ``"shutdown"`` -- graceful drain has begun. Analog to POSIX
  ``SIGTERM``: the entity acknowledges it is going away.

There is intentionally no ``"update"`` phase. Re-publishing the
current phase with a fresh payload is the update mechanism: the
retained slot is overwritten and the latest state is what every
subscriber sees. This mirrors MQTT's retain semantics and keeps the
phase vocabulary tied to *state changes*, not refresh cadence.
"""


@dataclass(frozen=True)
class ErrorInfo:
    """Structured failure descriptor carried by :class:`Failable` events.

    Modelled on gRPC's ``google.rpc.Status`` and HTTP's RFC 9457
    Problem Details, but trimmed to what an in-process Python bus
    actually needs: a machine-readable code, a short human-readable
    message, and a free-form details dict for structured context.

    Fields:

    - ``code`` -- short, machine-readable error label. Should be one
      of the canonical platform codes documented in
      ``docs/architecture/robot-os-target.md`` ("Error vocabulary"):
      ``timeout``, ``unavailable``, ``not_found``, ``invalid_argument``,
      ``unauthorized``, ``internal``, ``cancelled``. Components may
      add namespaced codes (``tool.mail.quota_exceeded``,
      ``actor.openai.rate_limited``, ...) following the topic
      convention.
    - ``message`` -- short, human-readable description of *this*
      occurrence. Free text; not a category. Useful for log readers
      and end-user messages, not for filtering.
    - ``details`` -- arbitrary JSONable structured context. The
      Wide-Event-log slot, same role as :attr:`KernelPhase.details`
      and :attr:`RobotAuditNote.details`. Put attempted IDs, retry
      counts, partial responses, anything queryable here. *Not* a
      stack-trace dumping ground.

    Following the wide-event design used elsewhere on the bus:
    structured fields beat free-form blobs. The same query
    (``"code":"timeout"``) finds every timeout across every event
    class that ever rode the bus.
    """

    code: str
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("ErrorInfo requires a non-empty code")


@dataclass(frozen=True, kw_only=True)
class Failable:
    """Mixin contract for events that report a success-or-failure outcome.

    Adds two fields to whichever class mixes it in: a
    :data:`ResultStatus` discriminator and an optional
    :class:`ErrorInfo`. Enforces a hard invariant in
    :meth:`__post_init__`:

    - ``status == "ok"`` iff ``error is None``;
    - ``status in ("warn", "error")`` requires a non-``None``
      :class:`ErrorInfo`.

    The ``warn`` status carries a structured :class:`ErrorInfo` for
    the same reason ``error`` does: the wide-event log query
    ``"code":"<x>"`` finds every soft *and* hard occurrence of an
    issue across every Failable event class. The status discriminator
    tells subscribers how to render it (yellow vs. red); the code
    classifies it.

    Subclasses that override ``__post_init__`` *must* call
    ``super().__post_init__()`` so the invariant runs.

    Composes via cooperative multiple inheritance with
    :class:`RobotEvent`. Both are
    ``@dataclass(frozen=True, kw_only=True)`` so the field-ordering
    pitfalls of dataclass inheritance do not apply -- every field is
    keyword-only.
    """

    status: ResultStatus = "ok"
    error: ErrorInfo | None = None

    def __post_init__(self) -> None:
        if self.status == "ok" and self.error is not None:
            raise ValueError(
                f"{type(self).__name__}: status='ok' must not carry an ErrorInfo"
            )
        if self.status in ("warn", "error") and self.error is None:
            raise ValueError(
                f"{type(self).__name__}: status={self.status!r} requires an ErrorInfo"
            )


@dataclass(frozen=True, kw_only=True)
class LifecycleAware:
    """Mixin contract for events that report a lifecycle phase.

    Adds two fields to whichever class mixes it in: a
    :data:`LifecyclePhase` discriminator and a status-agnostic
    :attr:`message` slot. Enforces in :meth:`__post_init__` that
    :attr:`phase` is one of the five canonical
    :data:`LifecyclePhase` values.

    Used by both :class:`RobotLifecycle` (the robot-as-a-whole
    broadcast) and :class:`ComponentLifecycle` (per-component
    broadcasts under ``component.<name>.lifecycle``). Single shared
    vocabulary so a subscriber can apply the same render rules
    (green / yellow / red / shutdown) to either source.

    :attr:`message` is a short, human-readable note about *this*
    transition (``"draining queue"``, ``"model loaded"``,
    ``"rate limit hit, falling back to cache"``). Status-agnostic:
    publishers may attach a message on every phase, including
    ``ready``. Detailed structured context belongs on the carrier
    event (e.g. :attr:`ComponentInfo.metadata` on
    :class:`ComponentLifecycle`) or on a :class:`RobotAuditNote`.

    Subclasses that override ``__post_init__`` *must* call
    ``super().__post_init__()`` so the phase check runs.
    """

    phase: LifecyclePhase = "boot"
    message: str = ""

    def __post_init__(self) -> None:
        allowed = ("boot", "ready", "warn", "failure", "shutdown")
        if self.phase not in allowed:
            raise ValueError(
                f"{type(self).__name__}.phase must be one of {allowed}, "
                f"got {self.phase!r}"
            )


@dataclass(frozen=True, kw_only=True)
class RobotEvent:
    """Base class for every bus message.

    The required fields follow the bus contract from
    ``docs/architecture/robot-os-target.md``. ``correlation_id`` is
    optional here and becomes mandatory on requests and responses.

    Two source fields, two roles:

    - :attr:`source` -- *semantic* publisher identity. The
      registered :attr:`src.components.RobotComponent.component_name`
      (``"echo"``, ``"base"``, ``"asyncio"``, ...) or a hierarchical
      derivative (``"kernel.base"`` for events the kernel publishes
      about its own pipeline). What an operator filters on when
      asking "show me everything from the kernel".
    - :attr:`source_id` -- *instance* publisher identity. The
      :attr:`src.components.RobotComponent.instance_id` (12 hex
      chars) of the publishing component instance. Discriminates
      between two instances of the same ``component_name`` (e.g.
      a robot with two ``BaseKernel`` instances). Default is
      empty so legacy publishers and synthetic events (built in
      tests, cooked up by the robot itself) still validate; a
      regular component publishing on its own behalf should fill
      it.
    """

    topic: str
    principal: str
    source: str
    run_id: str
    source_id: str = ""
    event_id: str = field(default_factory=_new_event_id)
    correlation_id: str | None = None
    timestamp: str = field(default_factory=_now_iso)


@dataclass(frozen=True, kw_only=True)
class RobotInput(RobotEvent):
    """Conversational input from the outside world.

    Published by a channel when the outside world hands something to the
    bus. Fire-and-forget: the sender does not wait for a specific reply.

    ``message`` is the conversational payload (text the user sent);
    ``payload`` is the structured side-channel for channel-specific
    metadata (session id, attachments, ...).
    """

    message: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class RobotOutput(Failable, RobotEvent):
    """Message destined for the outside world.

    Published by the kernel or a privileged component and delivered to
    the outside world by a channel. Fire-and-forget; a ``RobotOutput``
    can also be unsolicited and is not necessarily a reply to a previous
    input.

    Carries :class:`Failable`, so the channel knows whether to render
    this as a normal output or as an error notification (sysout vs.
    syserr semantics). On failure, ``status="error"`` and ``error``
    carries the structured :class:`ErrorInfo`; ``message`` may still
    contain a user-facing text the kernel chose to surface.
    """

    message: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__post_init__()


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
class ComponentResponse(Failable, RobotEvent):
    """Reply to a ``ComponentRequest``.

    ``correlation_id`` must exactly match the originating request. The
    bus uses this field to route the response back to the original
    sender.

    Inherits :class:`Failable` for the success/failure outcome:
    ``status="ok"`` plus the result on ``payload``, or
    ``status="error"`` plus a structured :class:`ErrorInfo`.
    """

    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.correlation_id:
            raise ValueError("ComponentResponse requires a correlation_id")


@dataclass(frozen=True)
class ComponentInfo:
    """Snapshot of a single component the robot booted with.

    The :class:`src.robot.Robot` collects one of these for every
    component it composes itself out of and publishes them all
    together in :class:`RobotLifecycle` (``phase="boot"`` and
    ``phase="ready"``) so that audit, observers and identity-aware
    components can learn the full composition with one event. The
    same snapshot also rides on :class:`ComponentLifecycle` for the
    per-component status broadcasts.

    ``category`` is the role bucket (``"bus"``, ``"actor"``,
    ``"kernel"``, ...). ``name`` is the registered component name
    (``"asyncio"``, ``"echo"``, ``"base"``, ...) -- the same string
    users put under ``name:`` in their ``robot.yaml``.

    ``metadata`` is the component-specific telemetry slot.
    Components that want to expose richer state to the manifest and
    to per-component lifecycle events populate it with structured,
    JSONable fields. Examples: an ``LLMActor`` puts ``model_name``,
    ``provider`` and ``context_window_tokens`` here; a tool layer
    puts the count of mounted tools; a queue-backed channel puts
    pending message counters. Keep it queryable -- the wide-event
    rule applies (structured fields, no full payloads or stack
    traces).
    """

    category: str
    name: str
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class RobotLifecycle(LifecycleAware, RobotEvent):
    """Broadcast lifecycle phase transition for the robot as a whole.

    One event class for the entire lifecycle. The :attr:`phase` field
    (inherited from :class:`LifecycleAware`) discriminates between
    retained broadcasts on :data:`LIFECYCLE_TOPIC`, all published by
    :class:`src.robot.Robot` itself:

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
    - ``warn`` -- the robot is operational but flagging an issue
      with one or more components. Reserved for the owner-loop /
      health-check path; not produced by the boot path itself.
    - ``failure`` -- the robot reports an unrecoverable degradation.
      Distinct from ``shutdown``: failure is unintended, shutdown
      is graceful.
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
    variation lives in a field, not in a subclass. All phases share
    envelope and manifest; splitting them into separate classes
    would have produced ~95% structural overlap -- the InfoQ
    "schema proliferation" pattern -- without any subscriber-side
    benefit, since the lifecycle topic and the publishing component
    are identical for all phases.

    Why no ``grace_seconds``: the shutdown grace window is
    supervisor policy, not part of the broadcast. It lives on
    :class:`src.robot.Robot` (configured via ``shutdown_grace``)
    and is enforced by the robot when calling ``drain()`` on each
    component. Components and observers learn *that* a shutdown
    has begun via this event; the *when* is the robot's
    responsibility, not the message's. Mirrors POSIX ``SIGTERM``
    (no payload), systemd ``TimeoutStopSec=`` (unit property),
    Kubernetes ``terminationGracePeriodSeconds`` (pod spec).

    Why ``message`` (inherited from :class:`LifecycleAware`)
    instead of ``reason``: the robot is always the publisher, so
    this is the robot's announcement, not a justification given by
    some external party. ``message`` is short, human-readable text
    the robot supplies a default for and that operators can
    override (e.g. via ``Robot.stop(message=...)``). Following the
    same convention as :attr:`KernelPhase.message`, this is a short
    label, not a free-form payload -- detailed context belongs in
    a :class:`RobotAuditNote`.

    No ``update`` phase: when component manifest snapshots change
    (e.g. a component swapped its mounted slot), the robot
    re-publishes the current phase with the fresh manifest. The
    retained slot is overwritten, late subscribers see the latest
    state, and there is no separate "refresh" phase to interpret.
    """

    robot_id: str | None = None
    robot_name: str | None = None
    boot_id: str = ""
    components: tuple[ComponentInfo, ...] = ()

    def __post_init__(self) -> None:
        super().__post_init__()


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
class KernelPhase(Failable, RobotEvent):
    """Phase transition emitted by a kernel as it processes one input.

    Every kernel run walks a deterministic state machine
    (``observing`` -> ``planning`` -> ``acting`` -> ``finalizing`` ->
    ``responding`` -> ``done``). On each transition the kernel
    publishes one of these events on :data:`KERNEL_PHASE_TOPIC`.

    Two orthogonal axes
    -------------------

    A :class:`KernelPhase` event answers two independent questions:

    - **Where are we?** -- the :attr:`phase` field (one of the six
      run-state values). Pure run-state-machine position; never
      includes ``"error"``.
    - **How is it going?** -- the inherited :data:`Failable.status`
      field plus :attr:`Failable.error`. Same vocabulary every other
      :class:`Failable` event uses; ``rg '"status":"error"'`` finds
      every failed phase across every kernel.

    This split intentionally undoes the earlier overloading of
    ``phase="error"``: an actor failing during the ``acting`` phase
    used to clobber the phase to ``"error"``, mixing "where in the
    run" with "did it work". They are different questions and now
    travel on different fields.

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
      iterations, total actor time, outcome.
    - failed phases carry ``status="error"`` plus a structured
      :class:`ErrorInfo` with a canonical ``code`` and a
      ``failed_phase`` entry in ``details``.

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

    - ``phase`` -- the phase being reported (mirrors :class:`RunPhase`,
      minus the legacy ``error`` value).
    - ``iteration`` -- 0-based iteration counter; increments only when
      tool round-trips reopen the loop.
    - ``kernel`` -- ``component_name`` of the emitting kernel so
      observers can attribute traffic with multiple kernels on a bus.
    - ``message`` -- short, status-agnostic note about *this* phase
      (``"used cached actor response"``, ``"thinking..."``, ``""``).
      Always allowed; not tied to ``status``. Channels may forward
      these as live progress indicators.
    - ``details`` -- arbitrary JSONable analytics fields. Empty by
      default; the kernel decides what to put in.

    Plus the inherited :class:`Failable` fields: ``status`` and
    ``error``. On failure the kernel populates ``error`` with an
    :class:`ErrorInfo` whose ``details["failed_phase"]`` names the
    phase that broke -- redundant with ``phase`` on the failing-phase
    event, but useful on the trailing ``done`` event whose ``phase``
    is by definition ``"done"``.

    Distinct from :class:`RobotAuditNote`: phase events are
    fine-grained operational telemetry that the kernel emits for
    *every* run; audit notes are deliberate, semantic records of
    curated actions (tool invocations, approvals, denials).
    """

    phase: str = ""
    iteration: int = 0
    kernel: str = ""
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.phase:
            raise ValueError("KernelPhase requires a non-empty phase")
        if not self.kernel:
            raise ValueError("KernelPhase requires a non-empty kernel")


@dataclass(frozen=True, kw_only=True)
class ComponentLifecycle(LifecycleAware, RobotEvent):
    """Per-component lifecycle phase transition.

    Each component owns its own lifecycle topic:
    ``component.<info.name>.lifecycle`` (built via
    :func:`component_lifecycle_topic`). Retain semantics are the
    standard "one retained event per topic", so a late subscriber
    sees the component's most recent state without needing a
    multi-slot retain map.

    Owner-pattern: components do not publish their own lifecycle
    events. The owner publishes for them.

    - The :class:`src.robot.Robot` owns its directly registered
      components and publishes ``boot``, ``ready``, ``warn``,
      ``failure`` and ``shutdown`` for each of them based on
      :meth:`RobotComponent.health_check` results.
    - A kernel owns its in-process actor (which has no bus
      attachment) and publishes the actor's lifecycle on the
      kernel's behalf.
    - Future tool layers, governance components and similar
      sub-orchestrators do the same for whatever they manage.

    The ``parent`` field names the owner's
    :attr:`RobotComponent.component_name` so a subscriber can tell
    "actor.echo went to warn -- which kernel owns it?" at a glance.
    Empty ``parent`` means "owned directly by the robot".

    The :attr:`info` snapshot rides on every event, so a subscriber
    that only wants the current state of the world subscribes
    broadcast and reads ``info.metadata`` from the retained slot.
    There is no separate ``update`` phase: when metadata changes
    (an LLM actor swaps its model, a tool layer mounts a new tool),
    the owner re-publishes the current phase with a fresh
    :class:`ComponentInfo`. Last-writer-wins on the retained slot.

    Distinct from :class:`MountEvent`: lifecycle is the *state of
    the component itself* (running, degraded, shutting down). Mount
    events are the *transitions of internal slots* a component
    manages (which actor / tool / SOP it currently has wired).
    """

    info: ComponentInfo
    parent: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.info.name:
            raise ValueError(
                "ComponentLifecycle.info requires a non-empty ComponentInfo.name"
            )


@dataclass(frozen=True, kw_only=True)
class MountEvent(RobotEvent):
    """Realtime stream event: an internal slot of a component changed.

    Published by composite components (kernels, tool layers, SOP
    libraries, ...) when one of their managed slots gets wired or
    unwired. Travels on ``component.<owner>.mount`` (built via
    :func:`component_mount_topic`).

    Use cases:

    - A kernel reporting which actor it currently runs:
      ``MountEvent(owner="kernel.base", slot="actor",
      mounted=ComponentInfo(category="actor", name="openai",
      metadata={"model": "gpt-5"}))``.
    - A future tool layer reporting which tools are wired:
      ``slot="tool.weather"``, ``slot="tool.mail"``, ...

    Mount events are *not retained*: the authoritative "what is
    currently mounted" snapshot lives in
    :attr:`ComponentInfo.metadata` on the retained
    :class:`ComponentLifecycle`. Mount events are the
    fire-and-forget realtime stream for subscribers that need to
    react to swaps. Late subscribers reconstruct the current state
    from the retained lifecycle, not from a mount-event replay.

    Two phases only: ``mounted`` and ``unmounted``. Replacing a
    slot is two events (unmounted-old, mounted-new) so each slot
    has at most one occupant at any timestamp; this keeps observer
    state machines trivial and matches the way ROS service
    handlers are reported.

    The :attr:`mounted` field is the snapshot of what is in the
    slot. Required when ``phase == "mounted"`` (the snapshot is the
    point of the event); ``None`` when ``phase == "unmounted"``.

    Failure semantics on purpose absent: ``MountEvent`` is *not* a
    :class:`Failable` event. It reports a successful transition
    after the fact, never an attempted one. Mount failures travel
    via :class:`ComponentLifecycle` (``phase="warn"`` or
    ``phase="failure"``, ``error=ErrorInfo(code="mount_failed",
    ...)``) on the owner's lifecycle topic, optionally accompanied
    by a :class:`RobotAuditNote` when the attempt itself needs an
    audit trail. Splitting these concerns keeps each event class
    semantically pure: observations are fire-and-forget, status
    lives on lifecycle, deliberate actions are in the audit log.
    """

    phase: Literal["mounted", "unmounted"] = "mounted"
    owner: str = ""
    slot: str = ""
    mounted: ComponentInfo | None = None

    def __post_init__(self) -> None:
        if self.phase not in ("mounted", "unmounted"):
            raise ValueError(
                f"MountEvent.phase must be 'mounted' or 'unmounted', "
                f"got {self.phase!r}"
            )
        if not self.owner:
            raise ValueError("MountEvent requires a non-empty owner")
        if not self.slot:
            raise ValueError("MountEvent requires a non-empty slot")
        if self.phase == "mounted" and self.mounted is None:
            raise ValueError(
                "MountEvent(phase='mounted') requires a mounted ComponentInfo"
            )
        if self.phase == "unmounted" and self.mounted is not None:
            raise ValueError(
                "MountEvent(phase='unmounted') must not carry a mounted ComponentInfo"
            )
