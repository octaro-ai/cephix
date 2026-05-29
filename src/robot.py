"""The robot: identity, control plane, components, lifecycle.

The :class:`Robot` is the single class that owns everything that makes
a cephix robot what it is:

- **identity** -- id and name, available without a bus;
- **out-of-band control plane** -- own port, own auth, reachable even
  if the bus is down;
- **components** -- bus, kernel, channels (and, later, audit /
  governance / tools), all uniform :class:`RobotComponent` instances;
- **lifecycle** -- a 3-phase boot, mirrored by a 3-phase shutdown, both
  sequenced by :data:`src.components.BOOT_PRIORITY` so adding a new
  category does not change the lifecycle code.

Boot sequence (analog to BIOS / bootloader / userspace-init on a PC,
or systemd's targets ``sysinit -> basic -> multi-user``):

1. **Phase 1 -- control plane up.** Out-of-band, no bus required.
   Operators can reach the maintenance hatch even if the rest fails.
2. **Phase 2 -- skeleton up + ``RobotLifecycle(phase="boot")`` retained.**
   Skeleton components (currently: bus) start; their identities go
   into the manifest; a retained :class:`RobotLifecycle` carrying
   identity + manifest is broadcast.
3. **Phase 3 -- userspace up + ``RobotLifecycle(phase="ready")`` retained.**
   All other components start in priority order, then a retained
   :class:`RobotLifecycle` announces full service.

Shutdown mirrors this strictly:

- **Phase 3 down -- userspace verabschieden.** Retained
  :class:`RobotLifecycle` (``phase="shutdown"``) is broadcast; then
  each userspace component in reverse-priority order gets ``drain()``
  (with ``shutdown_grace`` as hard cap) followed by ``stop()``.
- **Phase 2 down -- skeleton verabschieden.** Skeleton components in
  reverse-priority order get the same drain+stop treatment. The bus
  is last -- by then no userspace component is still publishing, so
  ``bus.drain()`` (queue flush) converges quickly.
- **Phase 1 down -- persona offline.** The control plane is torn down
  last. The robot is then ``FINALIZED``.

The robot is also the runtime of itself: there is no separate runtime
object or polling loop. Once started, the system is purely
event-driven. :meth:`run` is the synchronous entry point that owns the
asyncio loop and keeps the process alive until SIGINT (Ctrl-C) is
received.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from types import TracebackType
from typing import TYPE_CHECKING, Literal, Self

from src.bus.messages import (
    LIFECYCLE_TOPIC,
    ComponentInfo,
    RobotLifecycle,
)
from src.bus.ports import BusPort
from src.components import (
    BOOT_PRIORITY,
    SKELETON_CATEGORIES,
    BusComponent,
    ComponentCategory,
    RobotComponent,
)
from src.configuration import CONTROL_PLANE_TOKEN_ENV

if TYPE_CHECKING:
    from src.ops.server import ControlPlane


logger = logging.getLogger(__name__)


DEFAULT_SHUTDOWN_GRACE_SECONDS = 5.0


# ---------------------------------------------------------------------------
# Identity, control plane config, lifecycle phases
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RobotIdentity:
    """The robot's persona: who it is, regardless of which kernel runs.

    Injected at construction time so the robot knows itself without
    depending on the bus. ``id`` is the slug used everywhere
    machine-side, ``name`` is the human-friendly display label.
    """

    id: str | None = None
    name: str | None = None

    @property
    def label(self) -> str:
        """Format identity for log lines and prompts."""
        if self.name and self.id:
            return f"robot {self.name!r} ({self.id})"
        if self.id:
            return f"robot ({self.id})"
        if self.name:
            return f"robot {self.name!r}"
        return "robot"


@dataclass(frozen=True)
class ControlPlaneConfig:
    """Configuration of the out-of-band control plane.

    The control plane runs on its own TCP port -- not the bus. It is
    intentionally restricted to localhost. Token authentication keeps
    it usable but resists drive-by access.

    Auto-port resolution: ``port`` is the preferred port, ``port_range``
    is tried on conflict, and ``port=0`` lets the OS pick if everything
    is busy.
    """

    host: str = "127.0.0.1"
    port: int = 9876
    port_range: tuple[int, int] = (9876, 9999)
    path: str = "/control"
    enabled: bool = True


class RobotPhase(str, Enum):
    """ROS-2-inspired lifecycle states the robot walks through.

    The control plane reports the current phase in ``status`` so an
    operator can tell at a glance "the bus didn't come up" from "the
    kernel crashed" from "it's fine".
    """

    OFFLINE = "offline"            # not yet started
    BOOTING = "booting"            # phase 1 in progress
    BOOTED = "booted"              # control plane up, no skeleton yet
    ATTACHING = "attaching"        # phase 2 in progress
    ATTACHED = "attached"          # skeleton up, lifecycle "boot" retained
    ACTIVATING = "activating"      # phase 3 in progress
    SERVING = "serving"            # everything up, lifecycle "ready" retained
    DRAINING = "draining"          # phase 3 down, lifecycle "shutdown" sent
    STOPPING = "stopping"          # phase 2 down + 1 down
    FINALIZED = "finalized"        # done


# ---------------------------------------------------------------------------
# The Robot
# ---------------------------------------------------------------------------


class Robot:
    """A robot instance. One class, three phases, sorted components.

    Construct with an :class:`RobotIdentity`, a list of
    :class:`RobotComponent` instances and (optionally) a control-plane
    configuration. The robot sorts the components by
    :data:`BOOT_PRIORITY` once, then walks the same list both ways for
    boot and shutdown.
    """

    def __init__(
        self,
        *,
        identity: RobotIdentity,
        components: Sequence[RobotComponent],
        control_plane_config: ControlPlaneConfig | None = None,
        control_plane_token: str | None = None,
        shutdown_grace: float = DEFAULT_SHUTDOWN_GRACE_SECONDS,
    ) -> None:
        self._identity = identity
        self._components: list[RobotComponent] = self._sort_for_boot(components)
        self._control_plane_config = control_plane_config or ControlPlaneConfig()
        self._control_plane_token = control_plane_token
        self._shutdown_grace = shutdown_grace

        self._control_plane: ControlPlane | None = None
        self._bus: BusPort | None = None
        self._boot_id: str = ""
        self._started_at: datetime | None = None
        self._phase: RobotPhase = RobotPhase.OFFLINE
        self._started: list[RobotComponent] = []
        self._stop_event: asyncio.Event = asyncio.Event()

    # ---- public read-only properties --------------------------------------

    @property
    def identity(self) -> RobotIdentity:
        return self._identity

    @property
    def boot_id(self) -> str:
        return self._boot_id

    @property
    def started_at(self) -> datetime | None:
        return self._started_at

    @property
    def phase(self) -> RobotPhase:
        return self._phase

    @property
    def components(self) -> tuple[RobotComponent, ...]:
        """All components in boot order (skeleton first, channels last)."""
        return tuple(self._components)

    @property
    def component_manifest(self) -> tuple[ComponentInfo, ...]:
        """Roster snapshot for ``RobotLifecycle`` ``boot``/``ready`` payloads.

        Pure "who exists" information: category, name, description. The
        robot is the skeleton; it does *not* advertise capabilities
        here. Commands (later: tools, models) are component-driven and
        travel on each component's self-published
        :class:`~src.bus.messages.ComponentLifecycle`, which the
        ``CapabilityCollector`` aggregates. Keeping the roster
        capability-free leaves a single source of truth for "what the
        robot can do" -- the live per-component lifecycle, not the boot
        snapshot.
        """
        return tuple(
            ComponentInfo(
                category=c.component_category.value,
                name=c.component_name,
                description=c.component_description,
            )
            for c in self._components
        )

    @property
    def bus(self) -> BusPort | None:
        return self._bus

    @property
    def control_plane(self) -> ControlPlane | None:
        return self._control_plane

    @property
    def control_plane_endpoint(self) -> str | None:
        """Resolved ``ws://host:port/path`` URL once the control plane is up."""
        if self._control_plane is None:
            return None
        return self._control_plane.endpoint

    @property
    def _label(self) -> str:
        return self._identity.label

    # ---- start (3-phase boot) ---------------------------------------------

    async def start(self) -> None:
        """Three-phase boot. Roll back on failure.

        Phase 1: control plane up (no bus required).
        Phase 2: skeleton components + retained ``RobotLifecycle(phase="boot")``.
        Phase 3: userspace components + retained ``RobotLifecycle(phase="ready")``.

        On failure during phase 2 or 3 the robot tears down what it
        already started and propagates the original exception. The
        control plane stays up across failures so an operator can
        still call ``status`` and ``shutdown``.
        """
        if self._phase is not RobotPhase.OFFLINE:
            return

        if self._identity.id or self._identity.name:
            logger.info("starting %s...", self._label)
        else:
            logger.info("starting...")

        try:
            await self._phase1_control_plane()
            await self._phase2_skeleton()
            await self._phase3_userspace()
            logger.info("%s online (Ctrl-C to stop)", self._label)
        except BaseException:
            logger.warning("startup failed, rolling back")
            await self._teardown()
            raise

    async def _phase1_control_plane(self) -> None:
        """Phase 1: bring up the out-of-band control plane.

        Generates a fresh ``boot_id`` and records ``started_at`` so
        every later phase can stamp its events with the same boot
        identity. The control plane is optional: if disabled or no
        token is configured, this phase only sets the ids and skips
        binding the WebSocket.
        """
        self._phase = RobotPhase.BOOTING
        self._boot_id = f"boot-{secrets.token_hex(4)}"
        self._started_at = datetime.now(UTC)
        logger.info("%s boot (boot_id=%s)", self._label, self._boot_id)

        if self._control_plane_config.enabled:
            if not self._control_plane_token:
                # Deny by default: the control plane offers sovereign
                # operations and must never be started without a
                # credential. We still come up so the operator gets a
                # bot reachable over the bus -- they just won't have
                # the maintenance hatch until they fix the .env.
                logger.error(
                    "%s control plane is enabled but no token was provided; "
                    "skipping it. Set %s in the bot-local .env to enable it.",
                    self._label,
                    CONTROL_PLANE_TOKEN_ENV,
                )
            else:
                # Local import: keep the heavy aiohttp dependency out
                # of the import path of any caller that only needs
                # identity or config.
                from src.ops.server import ControlPlane

                self._control_plane = ControlPlane(
                    config=self._control_plane_config,
                    token=self._control_plane_token,
                    robot=self,
                )
                await self._control_plane.start()
                endpoint = self._control_plane.endpoint
                if endpoint:
                    logger.info("control plane online at %s", endpoint)

        self._phase = RobotPhase.BOOTED

    async def _phase2_skeleton(self) -> None:
        """Phase 2: start skeleton components, broadcast ``RobotLifecycle(phase="boot")``.

        Skeleton components start in priority order: the bus first
        (priority 0, ``start()`` without arguments), then
        cross-cutting observers (telemetry, ``start(bus)``). The
        bus is registered on the robot as soon as it is up so the
        observers can attach to it. Only after every skeleton
        component is online is the lifecycle ``boot`` event
        broadcast -- otherwise the first lifecycle event would slip
        past observers that are about to subscribe.
        """
        self._phase = RobotPhase.ATTACHING

        for prio, category, group in self._categories_in_phase(skeleton=True):
            if not group:
                self._log_phase_marker(prio, category, "boot_empty")
                continue
            self._log_phase_marker(prio, category, "boot_enter")
            for component in group:
                await self._start_component(component)
                # Once the bus is up, every subsequent skeleton
                # component needs it as a constructor-time argument.
                # Cache it as soon as it appears.
                if (
                    self._bus is None
                    and component.component_category is ComponentCategory.BUS
                ):
                    self._bus = self._locate_bus()
            # self._log_phase_marker(prio, category, "boot_complete")  # closing marker silenced -- noisy in dense logs

        if self._bus is None:
            # No bus component was registered at all -- _locate_bus
            # raises with the canonical error message.
            self._bus = self._locate_bus()

        boot = RobotLifecycle(
            topic=LIFECYCLE_TOPIC,
            principal=self._system_principal(),
            source="robot",
            run_id=self._boot_id,
            phase="boot",
            robot_id=self._identity.id,
            robot_name=self._identity.name,
            boot_id=self._boot_id,
            components=self.component_manifest,
        )
        await self._bus.publish_broadcast(boot, retain=True)
        self._phase = RobotPhase.ATTACHED

    async def _phase3_userspace(self) -> None:
        """Phase 3: start userspace components, broadcast ``RobotLifecycle(phase="ready")``."""
        self._phase = RobotPhase.ACTIVATING

        for prio, category, group in self._categories_in_phase(skeleton=False):
            if not group:
                self._log_phase_marker(prio, category, "boot_empty")
                continue
            self._log_phase_marker(prio, category, "boot_enter")
            for component in group:
                await self._start_component(component)
            # self._log_phase_marker(prio, category, "boot_complete")  # closing marker silenced -- noisy in dense logs

        assert self._bus is not None  # set by phase 2
        ready = RobotLifecycle(
            topic=LIFECYCLE_TOPIC,
            principal=self._system_principal(),
            source="robot",
            run_id=self._boot_id,
            phase="ready",
            robot_id=self._identity.id,
            robot_name=self._identity.name,
            boot_id=self._boot_id,
            components=self.component_manifest,
        )
        await self._bus.publish_broadcast(ready, retain=True)
        self._phase = RobotPhase.SERVING

    # ---- stop (3-phase shutdown, mirrored) --------------------------------

    async def stop(
        self,
        *,
        grace_override: float | None = None,
        message: str | None = None,
    ) -> None:
        """Graceful shutdown, mirroring the boot sequence component by
        component.

        Phase 3 down: announce :class:`RobotLifecycle` (``phase="shutdown"``)
        retained, then drain+stop every userspace component in
        reverse-boot order. Phase 2 down: drain+stop every skeleton
        component (the bus comes last; its ``drain()`` is the queue
        flush). Phase 1 down: tear down the control plane.

        Each component's drain hook is bounded by ``shutdown_grace``
        (or ``grace_override``) -- coroutines that don't return are
        cancelled before the matching ``stop()`` is called. The
        grace window is robot-internal policy and does not travel on
        the bus; ``message`` is the broadcast announcement and
        defaults to ``"Robot shutting down"`` if not supplied.
        """
        if self._phase in (RobotPhase.OFFLINE, RobotPhase.FINALIZED):
            self._stop_event.set()
            return

        grace = grace_override if grace_override is not None else self._shutdown_grace
        announce = message or "Robot shutting down"

        try:
            await self._phase3_down(grace, announce)
            await self._phase2_down(grace)
            await self._phase1_down()
            logger.info("%s offline", self._label)
        finally:
            self._phase = RobotPhase.FINALIZED
            self._stop_event.set()

    async def _phase3_down(self, grace: float, message: str) -> None:
        """Phase 3 down: announce shutdown, drain+stop userspace."""
        self._phase = RobotPhase.DRAINING
        await self._announce_shutdown(message)

        # Yield once so broadcast subscribers (audit sinks, channels
        # that haven't been drained yet, ...) get a chance to see the
        # lifecycle ``shutdown`` event before we start tearing
        # components down underneath them.
        await asyncio.sleep(0)

        userspace_groups = self._group_by_category(self._userspace_components())
        for prio, category, group in reversed(userspace_groups):
            # self._log_phase_marker(prio, category, "shutdown_enter")  # shutdown markers silenced -- only Entering kept on boot side
            for component in reversed(group):
                if component in self._started:
                    await self._drain_then_stop(component, grace)
            # self._log_phase_marker(prio, category, "shutdown_complete")  # shutdown markers silenced -- only Entering kept on boot side

    async def _phase2_down(self, grace: float) -> None:
        """Phase 2 down: drain+stop skeleton (bus is last)."""
        self._phase = RobotPhase.STOPPING

        skeleton_groups = self._group_by_category(self._skeleton_components())
        for prio, category, group in reversed(skeleton_groups):
            # self._log_phase_marker(prio, category, "shutdown_enter")  # shutdown markers silenced -- only Entering kept on boot side
            for component in reversed(group):
                if component in self._started:
                    await self._drain_then_stop(component, grace)
            # self._log_phase_marker(prio, category, "shutdown_complete")  # shutdown markers silenced -- only Entering kept on boot side

        # The bus is gone now; clear the pointer for status reporters.
        self._bus = None

    async def _phase1_down(self) -> None:
        """Phase 1 down: tear down the control plane."""
        if self._control_plane is not None:
            try:
                await self._control_plane.stop()
            except Exception:
                logger.exception("error stopping control plane; continuing")
            self._control_plane = None

    async def _announce_shutdown(self, message: str) -> None:
        """Broadcast :class:`RobotLifecycle` (``phase="shutdown"``) retained.

        Audit sinks and other observers learn here that a shutdown
        has begun. The drain itself is *not* coordinated via this
        event -- the robot calls ``drain()`` directly on each
        component as a lifecycle hook. The grace window is robot
        policy, not part of the broadcast (POSIX SIGTERM analogue).
        """
        if self._bus is None:
            return
        shutdown = RobotLifecycle(
            topic=LIFECYCLE_TOPIC,
            principal=self._system_principal(),
            source="robot",
            run_id=self._boot_id,
            phase="shutdown",
            robot_id=self._identity.id,
            robot_name=self._identity.name,
            boot_id=self._boot_id,
            message=message,
        )
        try:
            await self._bus.publish_broadcast(shutdown, retain=True)
        except RuntimeError:
            # bus already stopped -- nothing to broadcast on; fine.
            pass

    # ---- per-component lifecycle helpers ----------------------------------

    async def _start_component(self, component: RobotComponent) -> None:
        """Bring up a single component.

        Plain robot components start without a bus. Bus components
        attach to the already-running bus, which makes the dependency
        visible in the component type instead of smuggling it through
        category-specific call signatures.
        """
        label = self._component_label(component)
        if isinstance(component, BusComponent):
            assert self._bus is not None, (
                f"bus component {label} cannot start without a bus; "
                "the bus must boot first"
            )
            await component.start(self._bus)
            logger.info("%s attached", label)
        else:
            await component.start()
            logger.info("%s started", label)
        self._started.append(component)

    async def _drain_then_stop(
        self, component: RobotComponent, grace: float
    ) -> None:
        """Drain a single component (bounded), then stop it.

        Per-component grace: the timeout starts fresh for each
        component. A coroutine that doesn't return within the cap is
        cancelled with a warning, then ``stop()`` is called either
        way -- skipping ``stop()`` would leak resources.

        ``grace == 0`` is the cephix equivalent of "no SIGTERM, just
        SIGKILL": the drain is invoked but cancelled at the first
        await; trivial drains (default ``return None``) still complete.
        """
        label = self._component_label(component)
        timeout = grace if grace > 0 else 0.001
        try:
            await asyncio.wait_for(component.drain(), timeout=timeout)
        except asyncio.TimeoutError:
            if grace > 0:
                logger.warning(
                    "%s drain grace %.1fs elapsed for %s; forcing stop",
                    self._label,
                    grace,
                    label,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "%s drain hook for %s raised; forcing stop", self._label, label
            )

        try:
            await component.stop()
        except Exception:
            logger.exception("error while stopping %s; continuing", label)
            return

        # Shutdown verb mirrors the boot verb: a ``BusComponent``
        # was ``attached`` and is now ``detached``; a plain
        # ``RobotComponent`` (the bus itself, actors, off-bus
        # utilities) was ``started`` and is now ``stopped``.
        if isinstance(component, BusComponent):
            logger.info("%s detached", label)
        else:
            logger.info("%s stopped", label)

        with contextlib.suppress(ValueError):
            self._started.remove(component)

    # ---- logging helpers --------------------------------------------------

    @staticmethod
    def _component_label(component: RobotComponent) -> str:
        """Format a single component for log lines: ``<ClassName> (<id>)``.

        The 12-char :attr:`RobotComponent.instance_id` is the
        discriminator that lets a reader pick one of two
        ``BaseKernel`` instances apart at a glance. The class name
        stays in front so the existing log idiom ("EchoActor
        started") survives -- the id is the new addition, in
        parentheses where Linux-style logs put auxiliary identity.
        """
        return f"{type(component).__name__} ({component.instance_id})"

    @staticmethod
    def _group_by_category(
        components: list[RobotComponent],
    ) -> list[tuple[int, ComponentCategory, list[RobotComponent]]]:
        """Bucket ``components`` by category, ordered by boot priority.

        Each tuple is ``(priority, category, components_in_category)``.
        Only categories that actually have members are returned --
        this is the inventory-only view, used today by the shutdown
        path. The boot path uses :meth:`_categories_in_phase` instead,
        which surfaces *every* level (including empty ones) so the
        log mirrors the architecture, not just the actual inventory.
        """
        groups: dict[ComponentCategory, list[RobotComponent]] = {}
        for c in components:
            groups.setdefault(c.component_category, []).append(c)
        unknown_priority = max(BOOT_PRIORITY.values()) + 100
        return sorted(
            (
                (BOOT_PRIORITY.get(cat, unknown_priority), cat, members)
                for cat, members in groups.items()
            ),
            key=lambda t: t[0],
        )

    def _categories_in_phase(
        self,
        *,
        skeleton: bool,
    ) -> list[tuple[int, ComponentCategory, list[RobotComponent]]]:
        """Return every category in this boot phase, ordered by priority.

        Differs from :meth:`_group_by_category` in one crucial way:
        the returned list contains an entry for **every** category
        whose boot priority falls into the requested phase
        (``skeleton=True`` -> categories in
        :data:`SKELETON_CATEGORIES`; ``skeleton=False`` -> the rest),
        whether or not this robot has components there. Empty
        categories surface with an empty member list.

        The boot path uses this so the log mirrors the architecture
        (e.g. a reserved-but-empty ``BUS_PROVIDER`` shows up as
        ``=== Boot Level 5 (BUS_PROVIDER) -- empty ===``), not just
        the actual inventory.
        """
        grouped: dict[ComponentCategory, list[RobotComponent]] = {}
        for c in self._components:
            grouped.setdefault(c.component_category, []).append(c)
        result: list[tuple[int, ComponentCategory, list[RobotComponent]]] = []
        for cat, prio in sorted(BOOT_PRIORITY.items(), key=lambda kv: kv[1]):
            is_skeleton = cat in SKELETON_CATEGORIES
            if skeleton != is_skeleton:
                continue
            result.append((prio, cat, grouped.get(cat, [])))
        return result

    @staticmethod
    def _log_phase_marker(
        priority: int,
        category: ComponentCategory,
        kind: Literal[
            "boot_enter",
            "boot_empty",
            "boot_complete",
            "shutdown_enter",
            "shutdown_complete",
        ],
    ) -> None:
        """Emit a Linux-style boot-level marker on the robot logger.

        Five kinds:

        - ``boot_enter`` -- introduces a category whose members are
          about to start. Logged once before the first component of
          the category boots.
        - ``boot_empty`` -- single-line marker for a category that
          exists in :data:`BOOT_PRIORITY` but has no components in
          this robot. Logged in lieu of the ``boot_enter`` so the
          boot log mirrors the *architecture*, not just the actual
          inventory: a reserved level (e.g. ``BUS_PROVIDER``) shows
          up as ``=== Boot Level 5 (BUS_PROVIDER) -- empty ===``.
        - ``boot_complete`` -- closing bracket of ``boot_enter``;
          currently silenced for readability.
        - ``shutdown_enter`` / ``shutdown_complete`` -- mirror on
          shutdown; also silenced.

        ``priority`` and ``category`` come from
        :data:`src.components.BOOT_PRIORITY` so the marker matches
        the boot-priority numbers in the log line.
        """
        templates = {
            "boot_enter": "=== Boot Level %d (%s) ===",
            "boot_empty": "=== Boot Level %d (%s) -- empty ===",
            "boot_complete": "=== Boot Level %d (%s) complete ===",
            "shutdown_enter": "=== Leaving Boot Level %d (%s) ===",
            "shutdown_complete": "=== Boot Level %d (%s) shutdown complete ===",
        }
        logger.info(templates[kind], priority, category.name)

    # ---- component navigation ---------------------------------------------

    def _skeleton_components(self) -> list[RobotComponent]:
        return [
            c for c in self._components
            if c.component_category in SKELETON_CATEGORIES
        ]

    def _userspace_components(self) -> list[RobotComponent]:
        return [
            c for c in self._components
            if c.component_category not in SKELETON_CATEGORIES
        ]

    def _locate_bus(self) -> BusPort:
        """Find the bus component after phase 2 has started it."""
        for component in self._components:
            if component.component_category is ComponentCategory.BUS:
                if not isinstance(component, BusPort):
                    raise TypeError(
                        f"component with category=bus must implement BusPort; "
                        f"got {type(component).__name__}"
                    )
                return component
        raise RuntimeError(
            "robot has no bus component; every robot needs exactly one "
            "component with category=bus"
        )

    @staticmethod
    def _sort_for_boot(
        components: Sequence[RobotComponent],
    ) -> list[RobotComponent]:
        """Order components by their category's :data:`BOOT_PRIORITY`.

        Components in unknown categories (forward-compatibility hook)
        sort after every known one so a misconfigured plugin can't
        end up *before* the bus.
        """
        sortable = list(components)
        unknown_priority = max(BOOT_PRIORITY.values()) + 100

        def key(c: RobotComponent) -> tuple[int, str]:
            prio = BOOT_PRIORITY.get(c.component_category, unknown_priority)
            return (prio, c.component_name)

        sortable.sort(key=key)
        return sortable

    # ---- shutdown bridge for the control plane ----------------------------

    async def request_shutdown(self, *, force: bool = False) -> None:
        """Schedule a shutdown initiated from the control plane.

        Done as a fire-and-forget task so the WebSocket response to
        the operator can return *before* the bus that delivers it is
        torn down.
        """
        del force  # currently no difference; keeps the signature stable
        loop = asyncio.get_running_loop()
        loop.create_task(self.stop())

    # ---- run loop ---------------------------------------------------------

    async def run_forever(self) -> None:
        """Block until :meth:`stop` is called or the task is cancelled.

        Does no polling: awaits an internal event. Real work happens
        in background tasks (bus consumer tasks, channel servers,
        control plane).
        """
        await self._stop_event.wait()

    def run(self) -> None:
        """Synchronous entry point: boot the robot and run until Ctrl-C."""
        try:
            asyncio.run(self._run_async())
        except KeyboardInterrupt:
            # Already handled by the CancelledError branch in
            # _run_async; the teardown narrative has been logged.
            pass

    async def _run_async(self) -> None:
        try:
            await self.start()
            try:
                await self.run_forever()
            except asyncio.CancelledError:
                logger.info("shutdown signal received")
                raise
        finally:
            await self.stop()

    # ---- emergency teardown (used during failed boot) ---------------------

    async def _teardown(self) -> None:
        """Stop whatever managed to start, in reverse order. No drain.

        Used only on failed boot, where ``drain()`` would be premature
        (components may not even be in a state where draining is
        meaningful).
        """
        while self._started:
            component = self._started.pop()
            label = self._component_label(component)
            try:
                await component.stop()
            except Exception:
                logger.exception("error while stopping %s; continuing", label)
                continue
            if isinstance(component, BusComponent):
                logger.info("%s detached", label)
            else:
                logger.info("%s stopped", label)

        self._bus = None

        if self._control_plane is not None:
            try:
                await self._control_plane.stop()
            except Exception:
                logger.exception("error stopping control plane; continuing")
            self._control_plane = None

        self._phase = RobotPhase.FINALIZED

    # ---- async-with sugar -------------------------------------------------

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.stop()

    # ---- internal helpers -------------------------------------------------

    def _system_principal(self) -> str:
        return f"robot:{self._identity.id or 'anonymous'}"
