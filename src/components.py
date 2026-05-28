"""Self-description and lifecycle marker for cephix components.

A :class:`RobotComponent` is anything the robot composes itself out of:
the bus, kernels, channels, later audit/governance/tools, or local
resource holders that do not attach to the bus. Every component
carries:

- self-description (``component_name``, ``component_category``,
  ``component_description``) so the registry can index it and the
  manifest in :class:`RobotLifecycle` (``phase="boot"``/``"ready"``)
  can describe it. UI concerns (which fields the onboarding wizard
  prompts for) are not on the component anymore: that lives in
  :mod:`src.onboarding` so the component contract stays runtime-only;
- lifecycle hooks (``start``/``stop`` plus optional ``drain``). Plain
  robot components start without a bus; :class:`BusComponent` is the
  specialization for components that attach to the running bus;
- the :meth:`drain` lifecycle hook, called by the robot just before
  ``stop()`` to give the component bounded time for cleanup
  (close sessions, flush buffers, queue-flush for the bus, ...).

Lifecycle order is *not* hardcoded by class: the robot sorts its
components by :data:`BOOT_PRIORITY` (lower = earlier on boot), and
walks the same list in reverse on shutdown. Adding a new category
(e.g. ``AUDIT``) only requires extending the enum and the priority
mapping; the lifecycle code stays untouched.

Pydantic spec models are deliberately not used yet -- ``inspect.signature``
on the constructor covers the current set of parameters and lets us
ship without an extra dependency.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar

from src.bus.messages import ErrorInfo, ResultStatus

if TYPE_CHECKING:
    from src.bus.ports import BusPort


HealthStatus = ResultStatus
"""Alias of :data:`src.bus.messages.ResultStatus` exposed at the
component-health call site.

Component health uses the same three-valued vocabulary as every
other :class:`Failable` event on the bus -- ``"ok"``, ``"warn"``,
``"error"`` -- so the owner can map a health-check result onto a
:class:`ComponentLifecycle` phase with no translation table:
``"ok" -> "ready"``, ``"warn" -> "warn"``, ``"error" -> "failure"``.
The alias is purely for ergonomics in code that reads
``ComponentHealth(status="warn", ...)``.
"""


@dataclass(frozen=True)
class ComponentHealth:
    """Return value of :meth:`RobotComponent.health_check`.

    Three fields, one invariant:

    - ``status`` -- :data:`HealthStatus` (``"ok"`` / ``"warn"`` /
      ``"error"``). The discriminator. ``"ok"`` means fully
      operational; ``"warn"`` means operational but flagging an
      issue; ``"error"`` means not operational.
    - ``error`` -- :class:`ErrorInfo` carrying the structured
      diagnostic. Required when ``status`` is ``"warn"`` or
      ``"error"``; forbidden when ``status`` is ``"ok"``. Same
      structure as the ``Failable.error`` field on bus events,
      so the same ``code`` taxonomy works in both worlds.
    - ``metadata`` -- free-form JSONable telemetry the owner is
      welcome to copy into :attr:`ComponentInfo.metadata` on the
      next :class:`ComponentLifecycle` event. Use for component-
      specific state: an LLM actor's loaded model, a queue's
      pending count, a tool's registered tool set.

    The invariant matches :class:`Failable`: ``status == "ok"``
    iff ``error is None``. ``warn`` and ``error`` both carry an
    :class:`ErrorInfo` -- the status discriminates *severity*, the
    code discriminates *kind*. There is intentionally no separate
    ``notes`` slot: the human-readable note lives on
    :attr:`ErrorInfo.message` for ``warn``/``error``, and on the
    :attr:`metadata` slot for ``ok`` (where a free-form note
    rarely makes sense anyway).
    """

    status: HealthStatus = "ok"
    error: ErrorInfo | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status == "ok" and self.error is not None:
            raise ValueError(
                "ComponentHealth: status='ok' must not carry an ErrorInfo"
            )
        if self.status in ("warn", "error") and self.error is None:
            raise ValueError(
                f"ComponentHealth: status={self.status!r} requires an ErrorInfo"
            )


class ComponentCategory(str, Enum):
    """Coarse role buckets used by the registry, the wizard and the
    robot's lifecycle ordering.

    Five cross-cutting roles are distinguished from regular userspace:

    - :attr:`TELEMETRY`: read-only observers that watch *everything*
      that flows over the bus. Boots immediately after the bus so
      it captures the full lifetime of every userspace component.
      The reference implementation is the ``BusRecorder``.
    - :attr:`AUDIT`: subscribers that record curated, semantic notes
      published via :meth:`RobotComponent.publish_audit`. Boots right
      after telemetry so audit notes are captured from the very
      first userspace event. The reference implementation is the
      ``AuditNoteSink``.
    - :attr:`UTILITY`: off-bus helpers other components consult
      synchronously during their own ``start()`` -- model catalogs,
      tokenizers, pure data services, future credential stores
      *before* they grow a bus interface. No bus dependency at all;
      the robot owns their lifecycle and publishes
      :class:`ComponentLifecycle` events on their behalf. Reference
      implementation is the ``ModelCatalog``.
    - :attr:`BUS_UTILITY`: bus-attached infrastructure that other
      components share at runtime -- a future cost aggregator, a
      future approval gate, a future credentials broker. Boots
      between audit and the actor so it is online before any
      consumer ``start()`` runs. None ship today; the slot is here
      for the first concrete need.
    - :attr:`ACTOR`: the entity the kernel consults to turn a curated
      context into a reply. *Not* a bus participant: the kernel
      holds the actor as a direct in-process collaborator and calls
      its :meth:`ActorPort.run` method. Actors are still
      :class:`RobotComponent`s so the robot owns their lifecycle
      (handy for subprocess actors, HTTP clients, ...). Reference
      implementations: :class:`EchoActor`, :class:`MockLLMActor`;
      later iterations add ``LLMActorOpenAI``, ``LLMActorAnthropic``,
      ``HumanActor``, ``PlaywrightActor``.

    The full boot order is therefore:
    bus -> telemetry -> audit -> utility -> bus_utility ->
    actor -> kernel -> channels. Stop runs in the reverse order.
    """

    BUS = "bus"
    TELEMETRY = "telemetry"
    AUDIT = "audit"
    UTILITY = "utility"
    BUS_UTILITY = "bus_utility"
    ACTOR = "actor"
    KERNEL = "kernel"
    CHANNEL = "channel"
    # Future categories: TOOL, ...


# Boot order, lower number = earlier. The robot uses this to sort its
# components on boot; shutdown walks the same order in reverse. Adding
# a new category here is the single edit needed for new lifecycle
# stages -- the robot itself does not know about specific categories.
#
# Skeleton runs first (BUS), then the cross-cutting observers
# (TELEMETRY records *everything*, including the boot of AUDIT
# itself; AUDIT subscribes before any userspace component publishes
# audit notes), and finally userspace.
BOOT_PRIORITY: dict[ComponentCategory, int] = {
    ComponentCategory.BUS: 0,
    ComponentCategory.TELEMETRY: 1,
    ComponentCategory.AUDIT: 2,
    ComponentCategory.UTILITY: 5,
    ComponentCategory.BUS_UTILITY: 7,
    ComponentCategory.ACTOR: 8,
    ComponentCategory.KERNEL: 10,
    ComponentCategory.CHANNEL: 20,
}


# Categories that make up the robot's *skeleton*: they come up in
# Phase 2 -- before the ``RobotLifecycle`` ``boot`` event is
# broadcast and before any userspace component starts.
#
# Includes the bus itself and any cross-cutting infrastructure that
# must witness the entire robot lifetime, including the boot:
#
# - ``BUS``       -- the routing fabric. ``start()`` with no
#                    arguments because it *is* the upstream.
# - ``TELEMETRY`` -- read-all observers (``BusRecorder``). Must
#                    boot before the lifecycle ``boot`` event is
#                    published, otherwise the very first lifecycle
#                    event would be missing from the recording.
#
# ``AUDIT`` is *not* in here on purpose: audit only consumes curated
# ``RobotAuditNote`` events, which can only be produced by userspace
# components that themselves boot in Phase 3. There is nothing for
# audit to record before userspace exists.
SKELETON_CATEGORIES: frozenset[ComponentCategory] = frozenset({
    ComponentCategory.BUS,
    ComponentCategory.TELEMETRY,
})


class RobotComponent:
    """A configurable, lifecycle-aware part of a robot.

    Subclasses must define :attr:`component_name` and
    :attr:`component_category` as class-level attributes.

    :attr:`component_name` is the short identifier under which the
    registry references this implementation (``"echo"``,
    ``"asyncio"``, ``"base"``, ...). The class itself is the *type*;
    the name is what users put in YAML and what the manifest reports.

    :attr:`component_description` is the one-liner shown by tooling
    (onboarding wizard, manifest dumps, control-plane introspection).

    Lifecycle hooks:

    - :meth:`start` is called by the robot during boot. Plain
      components receive no bus. Components that need the running bus
      should inherit :class:`BusComponent`, whose ``start`` hook is
      called with the bus.
    - :meth:`stop` is called by the robot during shutdown to release
      resources acquired in ``start``.
    - :meth:`drain` is called by the robot during graceful shutdown,
      *before* the component's ``stop()`` is invoked. The default
      returns immediately ("nothing to drain"). Override to close
      sessions, flush buffers, queue-flush for the bus, etc. The
      robot bounds each call by ``shutdown_grace``; coroutines that
      haven't returned by then are cancelled and the teardown
      proceeds.

    Auditing helper:

    - :meth:`publish_audit` lets a component declare a curated note
      about an action it just performed (or refused). The note travels
      as a :class:`RobotAuditNote` on the dedicated ``AUDIT_TOPIC``
      and is picked up by every component of category
      :attr:`ComponentCategory.AUDIT`. Components that talk to the
      outside world (HTTP, file system, SMTP, LLM provider, ...) are
      expected to leave a trace on the bus for every observable side
      effect; otherwise the audit log is silently incomplete.
    """

    component_name: ClassVar[str]
    component_category: ClassVar[ComponentCategory]
    component_description: ClassVar[str] = ""

    async def start(self) -> None:
        """Bring the component online.

        Plain components do not receive the system bus. Use
        :class:`BusComponent` for components that subscribe to or
        publish on the bus during their lifetime.
        """
        raise NotImplementedError(f"{type(self).__name__}.start() not implemented")

    async def stop(self) -> None:
        """Release every resource acquired in :meth:`start`."""
        raise NotImplementedError(f"{type(self).__name__}.stop() not implemented")

    async def drain(self) -> None:
        """Pre-stop drain hook. Default: nothing to do, return immediately.

        Override in components that need to do bounded cleanup work
        before they are stopped. The robot calls ``drain()`` on every
        component sequentially in reverse-boot order, with the
        configured ``shutdown_grace`` as a hard cap per component.

        Analog: ROS 2's ``on_shutdown(state)`` lifecycle callback,
        Erlang/OTP's ``gen_server:terminate/2``, Windows SCM's
        ``OnStop()``.
        """
        return None

    async def health_check(self) -> ComponentHealth:
        """Report current health and component-specific telemetry.

        Called by the *owner* of the component (the robot for its
        directly registered components; a kernel for its in-process
        actor; future tool layers for their managed tools). The
        owner translates the result into a
        :class:`src.bus.messages.ComponentLifecycle` event:

        - ``ComponentHealth(status="ok", ...)`` -> ``phase="ready"``
          (or ``phase="boot"`` during the initial transition).
        - ``ComponentHealth(status="warn", error=..., ...)`` ->
          ``phase="warn"``.
        - ``ComponentHealth(status="error", error=..., ...)`` ->
          ``phase="failure"``.

        Default returns ``"ok"``: a component that does not override
        this hook is assumed healthy as long as it is started.
        Override when the component has meaningful runtime state
        worth surfacing -- a loaded LLM model, a pending queue depth,
        a remote-connection flag, a degraded-mode indicator. Put
        component-specific telemetry on
        :attr:`ComponentHealth.metadata`; the owner copies it onto
        :attr:`src.bus.messages.ComponentInfo.metadata` of the next
        retained :class:`src.bus.messages.ComponentLifecycle`.

        The contract is intentionally similar to Kubernetes' liveness
        / readiness probes: a fast, idempotent observation, *not* a
        heavy diagnostic. The owner may call this on a poll cadence;
        implementations must not block.
        """
        return ComponentHealth()

    async def publish_audit(
        self,
        bus: "BusPort",
        action: str,
        details: Mapping[str, Any] | None = None,
        *,
        principal: str = "system",
        run_id: str = "",
        correlation_id: str | None = None,
        on_behalf_of: str | None = None,
    ) -> None:
        """Publish a curated :class:`RobotAuditNote` for this component.

        Use this whenever the component performs (or refuses) an
        action that should be visible in the audit trail: invoking
        an external tool, calling an LLM provider, sending mail,
        denying an authorization, escalating to an operator, ...

        The note is published on ``AUDIT_TOPIC`` and carries:

        - ``source`` -- the publisher of the bus event (this
          component's ``component_name``);
        - ``component`` -- the component the note is *about*. Defaults
          to ``source``; pass ``on_behalf_of`` to record an action
          this component is logging on someone else's behalf (e.g.
          a kernel auditing its in-process actor).
        - ``action`` -- a short, machine-readable label (e.g.
          ``"tool.invoke"``, ``"approval.deny"``);
        - ``details`` -- arbitrary serializable payload. Implementers
          should keep it JSONable so any audit sink can persist it.

        The ``bus`` argument is taken explicitly to avoid hidden
        state on the component; pass the same bus that ``start()``
        gave you.

        Off-bus rule: if a component performs work that does not
        already produce a regular bus event (a tool call, a remote
        request, a file write), it must publish an audit note so the
        audit log reflects what the robot actually did.
        """
        from src.bus.messages import AUDIT_TOPIC, RobotAuditNote

        note = RobotAuditNote(
            topic=AUDIT_TOPIC,
            principal=principal,
            source=self.component_name,
            run_id=run_id,
            correlation_id=correlation_id,
            component=on_behalf_of or "",
            action=action,
            details=dict(details or {}),
        )
        await bus.publish(note)


class BusComponent(RobotComponent):
    """A :class:`RobotComponent` that attaches to the system bus.

    The robot starts bus components only after the bus itself is
    running, and injects that bus into :meth:`start`. This keeps
    generic components available for local resource holders while
    making the bus dependency explicit for kernels, channels,
    telemetry, audit, governance and similar observers or actors.
    """

    async def start(self, bus: "BusPort") -> None:  # type: ignore[override]
        """Bring the component online on ``bus``."""
        raise NotImplementedError(f"{type(self).__name__}.start() not implemented")
