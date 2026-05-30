"""The CapabilityCollector component.

Input: per-component :class:`ComponentLifecycle` events, **self-published
by each BusComponent** on attach (``ready``) and detach (``shutdown``),
plus ``failure`` published by the owner when a component cannot speak
for itself (crash / health loop). Capabilities are component-driven:
the robot is only the skeleton, the life happens on the bus through the
components.

Output: a retained :class:`HarnessCapabilities` on
:data:`HARNESS_CAPABILITIES_TOPIC`, rebuilt whenever the aggregate set
of advertised commands changes.

Aggregation:

- ``ready`` / ``warn`` -> take that component's
  ``info.metadata["provides_commands"]`` into the aggregate (keyed by
  ``info.name``).
- ``shutdown`` / ``failure`` -> drop that component's entry.

So a component going offline cleanly **retracts** its capabilities; the
UI loses the affordance instead of being stuck on a stale boot snapshot.
That is the resilience property: a failing component subtracts a
capability, it does not crash the robot.

Subscription: the bus has no wildcard subscribe, so the collector uses
:meth:`BusPort.subscribe_all` and filters for :class:`ComponentLifecycle`.
``subscribe_all`` does not replay retained events, so the collector must
be subscribed before anyone announces ``ready``. It therefore boots at
the **telemetry** level (6, part of the skeleton, after the bus) --
ahead of every utility, actor, kernel and channel. The
skeleton's retained ``RobotLifecycle`` is **not** consulted -- it is the
"who exists" roster, not the source of capabilities.

Ordering: the aggregate preserves first-seen (= boot) order, so the
command list is stable across runs.
"""

from __future__ import annotations

import logging
from typing import Any

from src.bus.messages import (
    HARNESS_CAPABILITIES_TOPIC,
    ComponentLifecycle,
    HarnessCapabilities,
    RobotEvent,
)
from src.bus.ports import BusPort, Subscription
from src.components import BusComponent, ComponentCategory

logger = logging.getLogger(__name__)


class CapabilityCollector(BusComponent):
    """Aggregate self-announced component commands into a retained manifest."""

    component_name = "capability-collector"
    # TELEMETRY (level 6): read-mostly bus-wide observer. Must subscribe
    # before any capability-providing component announces ``ready``.
    component_category = ComponentCategory.TELEMETRY
    component_description = (
        "Aggregates the commands each component self-announces via its "
        "ComponentLifecycle into a retained HarnessCapabilities manifest, "
        "so UIs render a failsafe, dynamic harness that follows components "
        "coming online and going offline."
    )

    def __init__(self) -> None:
        self._bus: BusPort | None = None
        self._subscription: Subscription | None = None
        # component name -> its serialized command entries. Insertion
        # order (= boot order) gives a stable manifest ordering.
        self._by_component: dict[str, tuple[dict[str, Any], ...]] = {}
        self._last_commands: tuple[dict[str, Any], ...] | None = None

    async def start(self, bus: BusPort) -> None:
        if self._subscription is not None:
            return
        self._bus = bus
        self._subscription = bus.subscribe_all(self._on_event)
        await self.announce_lifecycle(bus, "ready")

    async def _stop(self) -> None:
        if self._bus is not None:
            await self.announce_lifecycle(self._bus, "shutdown")
        if self._subscription is not None:
            try:
                await self._subscription.unsubscribe()
            finally:
                self._subscription = None
        self._bus = None
        self._by_component.clear()
        self._last_commands = None

    async def _on_event(self, event: RobotEvent) -> None:
        if not isinstance(event, ComponentLifecycle):
            return
        name = event.info.name
        if not name:
            return

        changed = False
        if event.phase in ("ready", "warn"):
            commands = self._extract_commands(event)
            if commands:
                if self._by_component.get(name) != commands:
                    self._by_component[name] = commands
                    changed = True
            elif name in self._by_component:
                # Component re-announced without commands -> it dropped
                # what it used to advertise; retract it.
                del self._by_component[name]
                changed = True
            # ready/warn with no commands and not tracked: nothing to do
            # (observers, channels, the collector itself).
        elif event.phase in ("shutdown", "failure"):
            if name in self._by_component:
                del self._by_component[name]
                changed = True
        # "boot" carries no capabilities yet (not operational); ignore.

        if changed:
            await self._republish(event)

    @staticmethod
    def _extract_commands(event: ComponentLifecycle) -> tuple[dict[str, Any], ...]:
        specs = event.info.metadata.get("provides_commands") if event.info.metadata else None
        if not specs:
            return ()
        return tuple(dict(entry) for entry in specs if isinstance(entry, dict))

    def _aggregate(self) -> tuple[dict[str, Any], ...]:
        collected: list[dict[str, Any]] = []
        for commands in self._by_component.values():
            collected.extend(commands)
        return tuple(collected)

    async def _republish(self, event: ComponentLifecycle) -> None:
        commands = self._aggregate()
        if commands == self._last_commands:
            return
        assert self._bus is not None
        manifest = HarnessCapabilities(
            topic=HARNESS_CAPABILITIES_TOPIC,
            principal=event.principal,
            source=self.component_name,
            source_id=self.instance_id,
            run_id=event.run_id,
            commands=commands,
        )
        await self._bus.publish_broadcast(manifest, retain=True)
        self._last_commands = commands
        logger.debug(
            "capability manifest republished with %d command(s)", len(commands)
        )
