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
    MountEvent,
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
        # Three parallel "what did each component announce" stores,
        # keyed by ``component_name``. Insertion order (= boot
        # order) gives a stable manifest ordering. Each tracks a
        # different slot on :class:`HarnessCapabilities`:
        #
        # - ``commands``: slash-callable UI operations (chat session
        #   commands etc.) -- ``provides_commands`` metadata.
        # - ``tools``: LLM-callable tool definitions exposed by a
        #   tool execution layer -- ``provides_tools`` metadata.
        # - ``models``: model / actor descriptors a kernel-or-actor
        #   reports (``model_id``, ``provider``, capabilities) --
        #   ``provides_models`` metadata.
        self._commands_by_component: dict[str, tuple[dict[str, Any], ...]] = {}
        self._tools_by_component: dict[str, tuple[dict[str, Any], ...]] = {}
        self._models_by_component: dict[str, tuple[dict[str, Any], ...]] = {}
        self._last_snapshot: tuple[
            tuple[dict[str, Any], ...],
            tuple[dict[str, Any], ...],
            tuple[dict[str, Any], ...],
        ] | None = None

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
        self._commands_by_component.clear()
        self._tools_by_component.clear()
        self._models_by_component.clear()
        self._last_snapshot = None

    async def _on_event(self, event: RobotEvent) -> None:
        if isinstance(event, ComponentLifecycle):
            await self._on_lifecycle(event)
            return
        if isinstance(event, MountEvent):
            await self._on_mount(event)
            return

    async def _on_lifecycle(self, event: ComponentLifecycle) -> None:
        name = event.info.name
        if not name:
            return

        changed = False
        if event.phase in ("ready", "warn"):
            # Models are NOT synced from lifecycle metadata: their
            # source is the kernel's :class:`MountEvent` (handled in
            # :meth:`_on_mount`). Doing both would let an empty
            # ``provides_models`` on a kernel's lifecycle wipe the
            # model the mount event just registered. The mount path
            # is the single source of truth.
            for store, key in (
                (self._commands_by_component, "provides_commands"),
                (self._tools_by_component, "provides_tools"),
            ):
                changed |= self._update_store(store, name, event, key)
        elif event.phase in ("shutdown", "failure"):
            # Shutdown DOES drop the kernel's model entry too: a
            # kernel going down takes its mounted actor with it.
            # The MountEvent for ``unmounted`` would land at the
            # same time in the happy path, but on a failure path
            # the kernel may go down without an explicit unmount
            # event, so the lifecycle gate stays the backstop.
            for store in (
                self._commands_by_component,
                self._tools_by_component,
                self._models_by_component,
            ):
                if name in store:
                    del store[name]
                    changed = True
        # "boot" carries no capabilities yet (not operational); ignore.

        if changed:
            await self._republish(event)

    async def _on_mount(self, event: MountEvent) -> None:
        """Lift actor mount snapshots into the ``models`` slot.

        An LLM actor is :class:`RobotComponent` (off-bus): it has no
        :class:`ComponentLifecycle` of its own. Its identity reaches
        the bus only through the kernel-emitted
        :class:`MountEvent`, whose ``mounted`` field carries the
        actor's :meth:`component_info` snapshot. When that snapshot
        contains a ``model_id``, the collector promotes it into the
        :attr:`HarnessCapabilities.models` aggregate keyed by the
        kernel's component name (``owner`` strips its ``kernel.``
        prefix) so the UI can render ``kernel.<name> (<model_id>)``.

        Unmount (or remount with a non-LLM actor) drops the entry.
        Other slots / non-LLM actors are ignored.
        """
        if event.slot != "actor":
            return
        owner_key = _strip_kernel_prefix(event.owner)
        if not owner_key:
            return

        changed = False
        if event.phase == "mounted":
            metadata = event.mounted.metadata if event.mounted else {}
            model_id = metadata.get("model_id") if metadata else None
            if isinstance(model_id, str) and model_id:
                entry = {
                    "model_id": model_id,
                    "provider": metadata.get("provider", ""),
                    "owner_component": owner_key,
                    "owner_instance_id": metadata.get("kernel_instance_id", ""),
                }
                new_value = (entry,)
                if self._models_by_component.get(owner_key) != new_value:
                    self._models_by_component[owner_key] = new_value
                    changed = True
            elif owner_key in self._models_by_component:
                # Remount with a non-LLM actor (e.g. swap to echo)
                # retracts the previously announced model.
                del self._models_by_component[owner_key]
                changed = True
        else:  # unmounted
            if owner_key in self._models_by_component:
                del self._models_by_component[owner_key]
                changed = True

        if changed:
            # MountEvents are not retained, but our manifest is --
            # republish so a late subscriber still sees the current
            # model set.
            await self._republish_from(event)

    @staticmethod
    def _update_store(
        store: dict[str, tuple[dict[str, Any], ...]],
        name: str,
        event: ComponentLifecycle,
        metadata_key: str,
    ) -> bool:
        """Diff one capability slot for ``name`` against the live event.

        Returns ``True`` when the slot changed (entries appeared,
        disappeared or were replaced). The boolean drives whether
        the aggregated :class:`HarnessCapabilities` gets republished;
        callers OR the per-slot results together so a single boot
        event with new tools AND new models still publishes exactly
        once.
        """
        entries = _extract_metadata_entries(event, metadata_key)
        if entries:
            if store.get(name) != entries:
                store[name] = entries
                return True
            return False
        if name in store:
            del store[name]
            return True
        return False

    def _aggregate(
        self, store: dict[str, tuple[dict[str, Any], ...]]
    ) -> tuple[dict[str, Any], ...]:
        collected: list[dict[str, Any]] = []
        for entries in store.values():
            collected.extend(entries)
        return tuple(collected)

    async def _republish_from(self, event: RobotEvent) -> None:
        """Alias for :meth:`_republish` accepting any bus event.

        Mount events drive a republish too -- they aren't
        :class:`ComponentLifecycle` so they don't match the
        original parameter type. Same behaviour: we use the event
        only as the source of ``principal`` / ``run_id`` for the
        outgoing manifest.
        """
        await self._republish(event)

    async def _republish(self, event: RobotEvent) -> None:
        commands = self._aggregate(self._commands_by_component)
        tools = self._aggregate(self._tools_by_component)
        models = self._aggregate(self._models_by_component)
        snapshot = (commands, tools, models)
        if snapshot == self._last_snapshot:
            return
        assert self._bus is not None
        manifest = HarnessCapabilities(
            topic=HARNESS_CAPABILITIES_TOPIC,
            principal=event.principal,
            source=self.component_name,
            source_id=self.instance_id,
            run_id=event.run_id,
            commands=commands,
            tools=tools,
            models=models,
        )
        await self._bus.publish_broadcast(manifest, retain=True)
        self._last_snapshot = snapshot
        logger.debug(
            "capability manifest republished: %d command(s), %d tool(s), "
            "%d model(s)",
            len(commands),
            len(tools),
            len(models),
        )


def _extract_metadata_entries(
    event: ComponentLifecycle, key: str
) -> tuple[dict[str, Any], ...]:
    """Pull the ``key`` list out of ``event.info.metadata`` as dicts."""
    specs = event.info.metadata.get(key) if event.info.metadata else None
    if not specs:
        return ()
    return tuple(dict(entry) for entry in specs if isinstance(entry, dict))


def _strip_kernel_prefix(owner: str) -> str:
    """Reduce ``"kernel.chat"`` -> ``"chat"`` for slot keying.

    Other prefixes pass through unchanged so a future composite
    component (``"tool-layer.<name>"`` mounting sub-tools, ...) can
    reuse the same collector path.
    """
    if owner.startswith("kernel."):
        return owner[len("kernel."):]
    return owner
