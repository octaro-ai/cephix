"""Robot composition root.

The robot is a composition of three injected building blocks:

- a :class:`BusPort` -- the system bus;
- a :class:`KernelPort` -- the active kernel implementation;
- zero or more :class:`ChannelPort` instances -- outside-world bridges.

The robot owns the full lifecycle of those parts: ``start`` brings the
bus up first, then the kernel, then every channel. ``stop`` reverses
the order. If a startup step fails, every previously started part is
torn down again before the exception propagates.

The robot is also the runtime of itself: there is no separate runtime
object or polling loop. Once started, the system is purely
event-driven -- bus subscriptions, channel sockets and any other
components block on their queues / sockets until something happens.
:meth:`run` is the synchronous entry point that owns the asyncio loop
and keeps the process alive until SIGINT (Ctrl-C) is received.

Identity (``robot_id``, name, keys), persistence and bootstrap are
deliberately not modelled here yet. The robot only owns its
composition and lifecycle.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from types import TracebackType
from typing import Self

from src.bus.ports import BusPort
from src.channels.ports import ChannelPort
from src.kernel.ports import KernelPort

logger = logging.getLogger(__name__)


class Robot:
    """A robot instance composed of bus, kernel and channels."""

    def __init__(
        self,
        *,
        bus: BusPort,
        kernel: KernelPort,
        channels: Sequence[ChannelPort] = (),
    ) -> None:
        self._bus = bus
        self._kernel = kernel
        self._channels: list[ChannelPort] = list(channels)
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

    async def start(self) -> None:
        """Start bus, kernel and channels. Roll back on failure.

        The bus is constructed up front by the caller; the kernel and
        channels are constructed with their own configuration only and
        receive the bus here at start-time.

        Each step is logged at INFO so the operator gets a coherent
        boot narrative on the console. Per-component details (e.g. the
        WebSocket URL) are logged by the components themselves.
        """
        if self._started:
            return
        logger.info("starting...")
        try:
            await self._bus.start()
            self._started.append(self._bus)
            logger.info("%s started", type(self._bus).__name__)

            await self._kernel.start(self._bus)
            self._started.append(self._kernel)
            logger.info("%s attached", type(self._kernel).__name__)

            for channel in self._channels:
                await channel.start(self._bus)
                self._started.append(channel)
                logger.info("%s attached", type(channel).__name__)

            logger.info("robot online (Ctrl-C to stop)")
        except BaseException:
            logger.warning("startup failed, rolling back")
            await self._teardown()
            raise

    async def stop(self) -> None:
        """Stop every previously started part in reverse order.

        Logs ``robot offline`` only if anything was actually running so
        repeated calls don't produce ghost shutdown messages. Always
        signals :meth:`run_forever` to return.
        """
        was_started = bool(self._started)
        await self._teardown()
        if was_started:
            logger.info("robot offline")
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
