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

from enum import Enum
from typing import ClassVar


class ComponentCategory(str, Enum):
    """Coarse role buckets used by the registry, the wizard and the
    robot's lifecycle ordering."""

    BUS = "bus"
    KERNEL = "kernel"
    CHANNEL = "channel"
    # Future categories: AUDIT, GOVERNANCE, ACTOR, TOOL, ...


# Boot order, lower number = earlier. The robot uses this to sort its
# components on boot; shutdown walks the same order in reverse. Adding
# a new category here is the single edit needed for new lifecycle
# stages -- the robot itself does not know about specific categories.
BOOT_PRIORITY: dict[ComponentCategory, int] = {
    ComponentCategory.BUS: 0,
    # Reserved spots, no current implementation:
    # ComponentCategory.AUDIT: 5,        # observers right after the bus
    ComponentCategory.KERNEL: 10,
    # ComponentCategory.GOVERNANCE: 15,  # policy layer between kernel and channels
    ComponentCategory.CHANNEL: 20,
}


# Categories that make up the robot's *skeleton*: they come up before
# ``RobotBoot`` is broadcast and they take a slightly different
# ``start()`` shape than userspace components -- a bus has no upstream
# bus to attach to, it *is* the bus.
SKELETON_CATEGORIES: frozenset[ComponentCategory] = frozenset({
    ComponentCategory.BUS,
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
