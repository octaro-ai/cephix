"""Robot composition root.

The robot is a composition of three injected building blocks:

- a :class:`BusPort` -- the system bus;
- a :class:`KernelPort` -- the active kernel implementation;
- zero or more :class:`ChannelPort` instances -- outside-world bridges.

The robot owns the full lifecycle of those parts: ``start`` brings the
bus up first, then announces itself with a retained
:class:`RobotReady` broadcast on ``robot.lifecycle``, then attaches the
kernel and channels (which subscribe to that topic and learn the
robot's identity from the retained event). ``stop`` reverses the
order, but first publishes a retained :class:`RobotShutdown` broadcast
and waits for ``shutdown_grace`` seconds so components can drain --
analog to systemd sending SIGTERM before the eventual SIGKILL.

The robot is also the runtime of itself: there is no separate runtime
object or polling loop. Once started, the system is purely
event-driven -- bus subscriptions, channel sockets and any other
components block on their queues / sockets until something happens.
:meth:`run` is the synchronous entry point that owns the asyncio loop
and keeps the process alive until SIGINT (Ctrl-C) is received.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Sequence
from types import TracebackType
from typing import Self

from src.bus.messages import (
    LIFECYCLE_TOPIC,
    ComponentInfo,
    RobotReady,
    RobotShutdown,
)
from src.bus.ports import BusPort
from src.channels.ports import ChannelPort
from src.components import RobotComponent
from src.kernel.ports import KernelPort

logger = logging.getLogger(__name__)

DEFAULT_SHUTDOWN_GRACE_SECONDS = 5.0


class Robot:
    """A robot instance composed of bus, kernel and channels."""

    def __init__(
        self,
        *,
        bus: BusPort,
        kernel: KernelPort,
        channels: Sequence[ChannelPort] = (),
        robot_id: str | None = None,
        robot_name: str | None = None,
        shutdown_grace: float = DEFAULT_SHUTDOWN_GRACE_SECONDS,
    ) -> None:
        self._bus = bus
        self._kernel = kernel
        self._channels: list[ChannelPort] = list(channels)
        self._robot_id = robot_id
        self._robot_name = robot_name
        self._shutdown_grace = shutdown_grace
        self._boot_id: str = ""
        self._started: list[object] = []
        self._stop_event: asyncio.Event = asyncio.Event()

    @property
    def bus(self) -> BusPort:
        return self._bus

    @property
    def kernel(self) -> KernelPort:
        return self._kernel

    @property
    def channels(self) -> tuple[ChannelPort, ...]:
        return tuple(self._channels)

    @property
    def robot_id(self) -> str | None:
        return self._robot_id

    @property
    def robot_name(self) -> str | None:
        return self._robot_name

    @property
    def _label(self) -> str:
        """Subject token for log lines.

        With identity: ``robot 'Dreamgirl' (dreamgirl)``.
        Without identity: ``robot``.

        Anonymous robots keep the historical wording so existing
        operator dashboards / log greps don't break.
        """
        if self._robot_name and self._robot_id:
            return f"robot {self._robot_name!r} ({self._robot_id})"
        if self._robot_id:
            return f"robot ({self._robot_id})"
        if self._robot_name:
            return f"robot {self._robot_name!r}"
        return "robot"

    async def start(self) -> None:
        """Start bus, announce the robot, attach kernel and channels.

        The bus is constructed up front by the caller. After it is up,
        the robot publishes a retained :class:`RobotReady` broadcast on
        ``robot.lifecycle`` so that any later subscriber -- starting
        with the kernel and channels themselves, ending with audit
        sinks attached at any time -- learns the robot's identity and
        composition. Then kernel and channels are attached.

        Each step is logged at INFO so the operator gets a coherent
        boot narrative on the console. Per-component details (e.g. the
        WebSocket URL) are logged by the components themselves.
        """
        if self._started:
            return
        if self._robot_id or self._robot_name:
            logger.info("starting %s...", self._label)
        else:
            logger.info("starting...")
        self._boot_id = f"boot-{secrets.token_hex(4)}"
        try:
            await self._bus.start()
            self._started.append(self._bus)
            logger.info("%s started", type(self._bus).__name__)

            await self._announce_ready()

            await self._kernel.start(self._bus)
            self._started.append(self._kernel)
            logger.info("%s attached", type(self._kernel).__name__)

            for channel in self._channels:
                await channel.start(self._bus)
                self._started.append(channel)
                logger.info("%s attached", type(channel).__name__)

            logger.info("%s online (Ctrl-C to stop)", self._label)
        except BaseException:
            logger.warning("startup failed, rolling back")
            await self._teardown()
            raise

    async def stop(self) -> None:
        """Stop every previously started part in reverse order.

        First announces a retained :class:`RobotShutdown` broadcast and
        waits ``shutdown_grace`` seconds so components subscribed to
        ``robot.lifecycle`` may drain. Then components are stopped in
        reverse start order. Logs ``offline`` only if anything was
        actually running, and always signals :meth:`run_forever` to
        return.
        """
        was_started = bool(self._started)
        if was_started and self._bus in self._started:
            await self._announce_shutdown(reason="lifecycle.stop")
            if self._shutdown_grace > 0:
                logger.info(
                    "%s draining for %.1fs...",
                    self._label,
                    self._shutdown_grace,
                )
            try:
                # Always yield at least once so broadcast subscribers see
                # the shutdown event before their consumer tasks are
                # cancelled in _teardown. With grace > 0 we additionally
                # let components actually drain.
                await asyncio.sleep(self._shutdown_grace)
            except asyncio.CancelledError:
                pass
        await self._teardown()
        if was_started:
            logger.info("%s offline", self._label)
        self._stop_event.set()

    async def run_forever(self) -> None:
        """Block until :meth:`stop` is called or the current task is cancelled.

        Does no polling: simply awaits an internal event. The actual
        work happens in background tasks (bus consumer tasks,
        channel-owned servers).
        """
        await self._stop_event.wait()

    def run(self) -> None:
        """Synchronous entry point: boot the robot and run until Ctrl-C.

        Owns the asyncio loop. Performs ``start`` -> ``run_forever`` ->
        ``stop`` and ensures clean teardown on any exit path, including
        :class:`KeyboardInterrupt`.

        Use this in process entry points (``python -m src.app``). For
        embedding the robot in an existing asyncio program, use
        :meth:`start` / :meth:`stop` directly or ``async with robot``.
        """
        try:
            asyncio.run(self._run_async())
        except KeyboardInterrupt:
            # Already announced by the CancelledError branch in _run_async,
            # and the teardown narrative has already been logged.
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

    def _component_snapshot(self) -> tuple[ComponentInfo, ...]:
        """Snapshot of the components composing this robot, for RobotReady."""
        infos: list[ComponentInfo] = []
        for part in (self._bus, self._kernel, *self._channels):
            if isinstance(part, RobotComponent):
                infos.append(
                    ComponentInfo(
                        category=part.component_category.value,
                        type=part.component_type,
                        description=part.component_description,
                    )
                )
            else:
                infos.append(
                    ComponentInfo(
                        category="unknown",
                        type=type(part).__name__,
                    )
                )
        return tuple(infos)

    def _system_principal(self) -> str:
        return f"robot:{self._robot_id or 'anonymous'}"

    async def _announce_ready(self) -> None:
        event = RobotReady(
            topic=LIFECYCLE_TOPIC,
            principal=self._system_principal(),
            source="robot.system",
            run_id=self._boot_id,
            robot_id=self._robot_id,
            robot_name=self._robot_name,
            boot_id=self._boot_id,
            components=self._component_snapshot(),
        )
        await self._bus.publish_broadcast(event, retain=True)

    async def _announce_shutdown(self, *, reason: str) -> None:
        event = RobotShutdown(
            topic=LIFECYCLE_TOPIC,
            principal=self._system_principal(),
            source="robot.system",
            run_id=self._boot_id,
            robot_id=self._robot_id,
            robot_name=self._robot_name,
            boot_id=self._boot_id,
            grace_seconds=self._shutdown_grace,
            reason=reason,
        )
        try:
            await self._bus.publish_broadcast(event, retain=True)
        except RuntimeError:
            # bus already stopped -- nothing to broadcast on; fine.
            pass

    async def _teardown(self) -> None:
        while self._started:
            part = self._started.pop()
            name = type(part).__name__
            try:
                await part.stop()  # type: ignore[attr-defined]
            except Exception:
                logger.exception("error while stopping %s; continuing", name)
                continue
            if part is self._bus:
                logger.info("%s stopped", name)
            else:
                logger.info("%s detached", name)

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
