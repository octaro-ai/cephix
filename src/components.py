"""Self-description and lifecycle marker for cephix components.

A :class:`RobotComponent` is anything the robot composes itself out of:
the bus, the kernel, channels, later audit/governance/tools. Every
component carries:

- self-description (``component_type``, ``component_category``,
  ``component_description``, ``component_wizard_fields``) so the
  registry can index it, the wizard can offer it, and the manifest in
  ``RobotBoot`` / ``RobotReady`` can describe it;
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
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from src.bus.ports import BusPort


class ComponentCategory(str, Enum):
    """Coarse role buckets used by the registry, the wizard and the
    robot's lifecycle ordering.

    Two cross-cutting categories are distinguished from regular
    userspace:

    - :attr:`TELEMETRY`: read-only observers that watch *everything*
      that flows over the bus. The reference implementation is the
      ``BusRecorder``; future telemetry components might emit metrics
      or distributed traces.
    - :attr:`AUDIT`: subscribers that record curated, semantic notes
      published via :meth:`RobotComponent.publish_audit`. The reference
      implementation is the ``AuditNoteSink``.

    Both boot right after the bus so they capture the full lifetime
    of every userspace component, and both shut down right before the
    bus so they see every farewell event.
    """

    BUS = "bus"
    TELEMETRY = "telemetry"
    AUDIT = "audit"
    KERNEL = "kernel"
    CHANNEL = "channel"
    # Future categories: GOVERNANCE, ACTOR, TOOL, ...


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
    ComponentCategory.TELEMETRY: 5,
    ComponentCategory.AUDIT: 6,
    ComponentCategory.KERNEL: 10,
    # ComponentCategory.GOVERNANCE: 15,  # policy layer between kernel and channels
    ComponentCategory.CHANNEL: 20,
}


# Categories that make up the robot's *skeleton*: they come up in
# Phase 2 -- before ``RobotBoot`` is broadcast and before any
# userspace component starts.
#
# Includes the bus itself and any cross-cutting infrastructure that
# must witness the entire robot lifetime, including the boot:
#
# - ``BUS``       -- the routing fabric. ``start()`` with no
#                    arguments because it *is* the upstream.
# - ``TELEMETRY`` -- read-all observers (``BusRecorder``). Must
#                    boot before ``RobotBoot`` is published, otherwise
#                    the very first lifecycle event would be missing
#                    from the recording.
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

    Subclasses must define :attr:`component_type` and
    :attr:`component_category` as class-level attributes.

    :attr:`component_description` is the one-liner shown by the
    onboarding wizard.

    :attr:`component_wizard_fields` is the *allow-list* of constructor
    parameters the wizard prompts the user for. Any other constructor
    parameter is treated as plumbing/wiring and uses its default.
    ``None`` means "ask for every parameter" (safe default for external
    classes that don't opt in). An empty tuple means "ask nothing"
    (all defaults).

    Lifecycle hooks (override as needed):

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

    component_type: ClassVar[str]
    component_category: ClassVar[ComponentCategory]
    component_description: ClassVar[str] = ""
    component_wizard_fields: ClassVar[tuple[str, ...] | None] = None

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

    async def publish_audit(
        self,
        bus: "BusPort",
        action: str,
        details: Mapping[str, Any] | None = None,
        *,
        principal: str = "system",
        run_id: str = "",
        correlation_id: str | None = None,
    ) -> None:
        """Publish a curated :class:`RobotAuditNote` for this component.

        Use this whenever the component performs (or refuses) an
        action that should be visible in the audit trail: invoking
        an external tool, calling an LLM provider, sending mail,
        denying an authorization, escalating to an operator, ...

        The note is published on ``AUDIT_TOPIC`` and carries:

        - ``actor`` -- the component's ``component_type``;
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
            source=self.component_type,
            run_id=run_id,
            correlation_id=correlation_id,
            actor=self.component_type,
            action=action,
            details=dict(details or {}),
        )
        await bus.publish(note)
