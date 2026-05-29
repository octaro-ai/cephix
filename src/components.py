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

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar

from src.bus.messages import (
    ComponentInfo,
    ComponentLifecycle,
    ErrorInfo,
    LifecyclePhase,
    ResultStatus,
    component_lifecycle_topic,
)

if TYPE_CHECKING:
    from src.bus.ports import BusPort
    from src.command.spec import CommandSpec

logger = logging.getLogger(__name__)


INSTANCE_ID_LENGTH: int = 12
"""Length of the per-instance ID generated for every :class:`RobotComponent`.

Twelve hex chars give us 48 bits of randomness -- enough to keep
collisions vanishingly unlikely across the components of a single
robot, while staying short enough to read in one glance in log
lines like ``EchoActor (a3f7c2b1d4e9) started``. Matches the
short-id style already used elsewhere on the bus
(:func:`src.bus.messages._new_event_id` clips event UUIDs to the
same width).
"""


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

    Cross-cutting roles distinguished from regular userspace:

    - :attr:`UTILITY`: off-bus helpers other components consult
      synchronously during their own ``start()`` -- model catalogs,
      tokenizers, pure data services. No bus dependency at all, so
      they boot *before* the bus (priority 0): the bus does not need
      them, but bus-attached components may need them already
      resolved. Reference implementation is the ``ModelCatalog``.
    - :attr:`PERSISTENCE`: storage providers (filesystem-backed today,
      DB / S3 / Supabase later). Bus-attached for lifecycle and
      health-check, but **not bus-aware** for the data path: writes
      flow through a direct sink API, not through bus messages.
      Boots between the bus and telemetry so observers can open
      their sinks at ``start()``.
    - :attr:`TELEMETRY`: read-only observers that watch *everything*
      that flows over the bus. Boots immediately after persistence so
      it can open sinks and capture the full lifetime of every
      userspace component. Reference implementations: ``BusRecorder``
      and the ``CapabilityCollector``.
    - :attr:`AUDIT`: subscribers that record curated, semantic notes
      published via :meth:`RobotComponent.publish_audit`. Boots right
      after telemetry. Reference implementation is the
      ``AuditNoteSink``.
    - :attr:`BUS_UTILITY`: bus-attached infrastructure that other
      components share at runtime -- a future cost aggregator, a
      future approval gate, the credential broker. Boots between
      audit and the actor so it is online before any consumer
      ``start()`` runs.
    - :attr:`ACTOR`: the entity the kernel consults to turn a curated
      context into a reply. *Not* a bus participant: the kernel
      holds the actor as a direct in-process collaborator and calls
      its :meth:`ActorPort.run` method. Actors are still
      :class:`RobotComponent`s so the robot owns their lifecycle.
      Reference implementations: :class:`EchoActor`,
      :class:`MockLLMActor`, :class:`LLMActorOpenAI`.

    The full boot order is therefore:
    utility -> bus -> persistence -> telemetry -> audit ->
    bus_utility -> actor -> kernel -> channels. Stop runs in the
    reverse order.
    """

    UTILITY = "utility"
    BUS = "bus"
    PERSISTENCE = "persistence"
    TELEMETRY = "telemetry"
    AUDIT = "audit"
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
# Renumbered so off-bus utilities come up before the bus (they do not
# need it and bus-attached components may need them resolved), and
# persistence sits between the bus and telemetry (telemetry opens its
# sinks via persistence).
BOOT_PRIORITY: dict[ComponentCategory, int] = {
    ComponentCategory.UTILITY: 0,
    ComponentCategory.BUS: 1,
    ComponentCategory.PERSISTENCE: 2,
    ComponentCategory.TELEMETRY: 3,
    ComponentCategory.AUDIT: 4,
    ComponentCategory.BUS_UTILITY: 8,
    ComponentCategory.ACTOR: 9,
    ComponentCategory.KERNEL: 11,
    ComponentCategory.CHANNEL: 21,
}


# Categories that make up the robot's *skeleton*: they come up in
# Phase 2 -- before the ``RobotLifecycle`` ``boot`` event is
# broadcast and before any userspace component starts.
#
# - ``UTILITY``     -- off-bus helpers. Booted first so anything
#                      that depends on them is guaranteed a resolved
#                      reference.
# - ``BUS``         -- the routing fabric.
# - ``PERSISTENCE`` -- bus-attached storage provider. Telemetry/audit
#                      open their sinks via persistence at their own
#                      ``start()``, so persistence must be up first.
# - ``TELEMETRY``   -- read-all observers. Must boot before the
#                      lifecycle ``boot`` event is published,
#                      otherwise the very first lifecycle event would
#                      be missing from the recording.
#
# ``AUDIT`` is *not* in here on purpose: audit only consumes curated
# ``RobotAuditNote`` events, which can only be produced by userspace
# components that themselves boot in Phase 3. There is nothing for
# audit to record before userspace exists.
SKELETON_CATEGORIES: frozenset[ComponentCategory] = frozenset({
    ComponentCategory.UTILITY,
    ComponentCategory.BUS,
    ComponentCategory.PERSISTENCE,
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

    provides_commands: ClassVar[tuple["CommandSpec", ...]] = ()
    """Commands this component advertises on the bus.

    Default empty: a component opts in to the command layer by listing
    :class:`~src.command.spec.CommandSpec` entries here and implementing
    the matching ``cmd_*`` handler methods. The owner serializes these
    into :attr:`src.bus.messages.ComponentInfo.metadata` so the
    ``CapabilityCollector`` can build the capability manifest, and the
    component wires them to the bus via
    :func:`src.command.wiring.wire_commands` in its ``start`` hook.
    "Alles kann nichts muss": components without commands simply leave
    this empty.
    """

    @property
    def instance_id(self) -> str:
        """Stable, unique short ID for *this* component instance.

        Generated lazily on first access and cached on the instance
        for the rest of its lifetime. Twelve hex chars
        (:data:`INSTANCE_ID_LENGTH`), so a log reader can spot two
        ``BaseKernel`` instances of the same name apart at a glance:
        ``BaseKernel (a3f7c2b1d4e9) attached`` vs
        ``BaseKernel (b91d44e5f0a8) attached``.

        Distinct from :attr:`component_name`: the *name* is the
        registered type identifier shared by every instance of the
        same class (``"base"`` for every ``BaseKernel``). The
        instance id is the per-instance discriminator. Bus events
        carry both: :attr:`src.bus.messages.RobotEvent.source` is
        the semantic name, :attr:`RobotEvent.source_id` is the
        instance id.

        Lazy generation (instead of ``__init__``-time) so subclasses
        do not have to remember to call ``super().__init__()`` for
        the id to exist; a missing ``__init__`` is the dataclass
        idiom and we accommodate it.
        """
        cached = getattr(self, "_instance_id", None)
        if cached:
            return cached
        new_id = uuid.uuid4().hex[:INSTANCE_ID_LENGTH]
        # ``object.__setattr__`` bypasses ``frozen``-style guards
        # subclasses might add; the field is intentionally
        # write-once so the cached value sticks for the lifetime of
        # the instance.
        object.__setattr__(self, "_instance_id", new_id)
        return new_id

    def component_info(self) -> ComponentInfo:
        """Bus-free self-description as a :class:`ComponentInfo`.

        The single place that renders a component into the
        :class:`~src.bus.messages.ComponentInfo` shape, including the
        serialized :attr:`provides_commands` under
        ``metadata["provides_commands"]`` (each entry built via
        :meth:`~src.command.spec.CommandSpec.manifest_entry` so it
        already carries ``owner_component`` / ``owner_instance_id``).

        Shared by two callers so both produce identical snapshots:

        - :meth:`BusComponent.announce_lifecycle` -- a bus-attached
          component announcing *itself* on ``component.lifecycle.<name>``.
        - the owner (the robot / a kernel) when it has to publish a
          ``ComponentLifecycle`` on a component's behalf -- in the
          failure path, where the component can no longer speak for
          itself.

        Pure and bus-free on purpose: a plain :class:`RobotComponent`
        has no bus to announce on, but it can still *describe* itself,
        and the owner needs exactly that description to bridge it.
        """
        metadata: dict[str, Any] = {}
        specs = self.provides_commands
        if specs:
            metadata["provides_commands"] = [
                spec.manifest_entry(
                    owner_component=self.component_name,
                    owner_instance_id=self.instance_id,
                )
                for spec in specs
            ]
        return ComponentInfo(
            category=self.component_category.value,
            name=self.component_name,
            description=self.component_description,
            metadata=metadata,
        )

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
            source_id=self.instance_id,
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

    async def announce_lifecycle(
        self,
        bus: "BusPort",
        phase: LifecyclePhase,
        *,
        message: str = "",
    ) -> None:
        """Self-publish this component's :class:`ComponentLifecycle`.

        The capability story is component-driven: a bus-attached
        component announces *itself* -- ``"ready"`` at the end of its
        own ``start(bus)`` (carrying its
        :attr:`provides_commands` via :meth:`component_info`), and
        ``"shutdown"`` at the start of its ``stop()`` (the bus boots
        first / stops last, so it is still alive then). The
        ``CapabilityCollector`` aggregates these into the retained
        ``harness.capabilities`` manifest, so a component going offline
        cleanly retracts its capabilities instead of leaving a stale
        boot snapshot.

        Only :class:`BusComponent` carries this method: a plain
        :class:`RobotComponent` has no bus to announce on (the bus
        itself, actors, off-bus utilities). Their lifecycle, when it
        matters, is published by the owner -- and only in the failure
        path, because in the happy path a component that cannot speak
        for itself simply has no bus presence (it shows up as
        ``started`` / ``stopped`` in the boot log, not
        ``attached`` / ``detached``).

        Broadcast + retained: a late subscriber (a UI connecting after
        boot) reads the component's current state from the retained
        slot. Best-effort: a publish failure is logged but never
        aborts the lifecycle.
        """
        info = self.component_info()
        try:
            await bus.publish_broadcast(
                ComponentLifecycle(
                    topic=component_lifecycle_topic(self.component_name),
                    principal=f"component:{self.component_name}",
                    source=self.component_name,
                    source_id=self.instance_id,
                    run_id="",
                    phase=phase,
                    info=info,
                    message=message,
                ),
                retain=True,
            )
        except Exception:
            logger.exception(
                "%s: failed to announce lifecycle (phase=%s)",
                type(self).__name__,
                phase,
            )
