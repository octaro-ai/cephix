"""In-memory implementation of :class:`BusPort` on top of asyncio.

Properties of iteration 0:

- Routing by exact topic match.
- One FIFO queue with its own consumer task per subscription, so a slow
  subscriber only slows down its own queue.
- Request/response correlation via ``correlation_id`` backed by
  :class:`asyncio.Future`.
- Timeouts are surfaced as failure ``ComponentResponse`` instances rather
  than raised exceptions.

Intentionally not here:

- Persistence, dead-letter queue, topic ACLs, wildcard topics, priority.
  Each of these is a future iteration; the port contract already allows
  them.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from src.bus.messages import (
    ComponentRequest,
    ComponentResponse,
    ErrorInfo,
    RobotEvent,
    _new_event_id,
    _now_iso,
)
from src.bus.ports import BusPort, EventHandler, Subscription
from src.components import ComponentCategory, RobotComponent

logger = logging.getLogger(__name__)


_ALL_TOPIC_MARKER = "__all__"


@dataclass
class _AsyncSubscription:
    topic: str
    handler: EventHandler
    queue: asyncio.Queue[RobotEvent] = field(default_factory=asyncio.Queue)
    task: asyncio.Task[None] | None = None
    bus: "AsyncioBus | None" = None
    is_broadcast: bool = False
    is_all: bool = False

    async def unsubscribe(self) -> None:
        if self.bus is not None:
            await self.bus._remove_subscription(self)


class AsyncioBus(RobotComponent, BusPort):
    """In-memory bus on top of asyncio primitives."""

    component_name = "asyncio"
    component_category = ComponentCategory.BUS
    component_description = "In-memory asyncio bus. Single-process, no persistence."

    def __init__(self) -> None:
        self._subscriptions: dict[str, list[_AsyncSubscription]] = {}
        self._broadcast_subscriptions: dict[str, list[_AsyncSubscription]] = {}
        self._all_subscriptions: list[_AsyncSubscription] = []
        self._retained: dict[str, RobotEvent] = {}
        self._pending: dict[str, asyncio.Future[ComponentResponse]] = {}
        self._running = False
        self._lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        async with self._lock:
            if self._running:
                return
            self._running = True
            for subs in self._subscriptions.values():
                for sub in subs:
                    self._ensure_consumer(sub)
            for subs in self._broadcast_subscriptions.values():
                for sub in subs:
                    self._ensure_consumer(sub)
            for sub in self._all_subscriptions:
                self._ensure_consumer(sub)

    async def _stop(self) -> None:
        async with self._lock:
            if not self._running:
                return
            self._running = False
            tasks: list[asyncio.Task[None]] = []
            for subs in self._subscriptions.values():
                for sub in subs:
                    if sub.task is not None:
                        sub.task.cancel()
                        tasks.append(sub.task)
            self._subscriptions.clear()
            for subs in self._broadcast_subscriptions.values():
                for sub in subs:
                    if sub.task is not None:
                        sub.task.cancel()
                        tasks.append(sub.task)
            self._broadcast_subscriptions.clear()
            for sub in self._all_subscriptions:
                if sub.task is not None:
                    sub.task.cancel()
                    tasks.append(sub.task)
            self._all_subscriptions.clear()
            self._retained.clear()

            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(asyncio.CancelledError("bus stopped"))
            self._pending.clear()

        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def publish(self, event: RobotEvent) -> None:
        if not self._running:
            raise RuntimeError("AsyncioBus is not running; call start() first")

        if isinstance(event, ComponentResponse):
            fut = self._pending.pop(event.correlation_id or "", None)
            if fut is not None and not fut.done():
                fut.set_result(event)

        for sub in list(self._subscriptions.get(event.topic, [])):
            await sub.queue.put(event)
        for sub in list(self._all_subscriptions):
            await sub.queue.put(event)

    def subscribe(self, topic: str, handler: EventHandler) -> Subscription:
        sub = _AsyncSubscription(topic=topic, handler=handler, bus=self)
        self._subscriptions.setdefault(topic, []).append(sub)
        if self._running:
            self._ensure_consumer(sub)
        return sub

    async def publish_broadcast(
        self,
        event: RobotEvent,
        *,
        retain: bool = False,
    ) -> None:
        if not self._running:
            raise RuntimeError("AsyncioBus is not running; call start() first")

        if retain:
            self._retained[event.topic] = event

        for sub in list(self._broadcast_subscriptions.get(event.topic, [])):
            await sub.queue.put(event)
        for sub in list(self._all_subscriptions):
            await sub.queue.put(event)

    def subscribe_broadcast(
        self,
        topic: str,
        handler: EventHandler,
    ) -> Subscription:
        sub = _AsyncSubscription(
            topic=topic,
            handler=handler,
            bus=self,
            is_broadcast=True,
        )
        self._broadcast_subscriptions.setdefault(topic, []).append(sub)

        retained = self._retained.get(topic)
        if retained is not None:
            sub.queue.put_nowait(retained)

        if self._running:
            self._ensure_consumer(sub)
        return sub

    def subscribe_all(self, handler: EventHandler) -> Subscription:
        sub = _AsyncSubscription(
            topic=_ALL_TOPIC_MARKER,
            handler=handler,
            bus=self,
            is_all=True,
        )
        self._all_subscriptions.append(sub)
        # NB: subscribe_all does NOT replay retained events. That's
        # deliberate, not an oversight:
        #
        # - ``announce_lifecycle`` publishes every ``ComponentLifecycle``
        #   with ``retain=True``, so the retained map fills up with one
        #   entry per attached component plus ``RobotLifecycle``,
        #   ``HarnessCapabilities``, MountEvents, etc. A late
        #   ``subscribe_all`` that replays would see all of that as if
        #   it were live -- and the typical consumers
        #   (``BusRecorder``, ``CapabilityCollector``,
        #   ``WebsocketChannel``) would double-count.
        #
        # - ``subscribe_broadcast(topic, ...)`` is the right tool for
        #   "give me the current state of X". Consumers that need a
        #   retained anchor can also pull it synchronously via
        #   ``bus.retained(topic)`` (see ``WebsocketChannel.start`` and
        #   ``BusRecorder.start``).
        if self._running:
            self._ensure_consumer(sub)
        return sub

    def retained(self, topic: str) -> RobotEvent | None:
        return self._retained.get(topic)

    async def request(
        self,
        request: ComponentRequest,
        *,
        timeout: float | None = None,
    ) -> ComponentResponse:
        if not self._running:
            raise RuntimeError("AsyncioBus is not running; call start() first")

        correlation_id = request.correlation_id
        if not correlation_id:
            raise ValueError("ComponentRequest requires a correlation_id")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[ComponentResponse] = loop.create_future()
        self._pending[correlation_id] = future

        try:
            await self.publish(request)
            if timeout is None:
                return await future
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(correlation_id, None)
            return ComponentResponse(
                event_id=_new_event_id(),
                topic=request.topic,
                principal=request.principal,
                source="bus",
                run_id=request.run_id,
                correlation_id=correlation_id,
                timestamp=_now_iso(),
                status="error",
                error=ErrorInfo(
                    code="timeout",
                    message=(
                        f"timeout after {timeout}s for action "
                        f"{request.action!r}"
                    ),
                    details={"timeout_s": timeout, "action": request.action},
                ),
            )
        except asyncio.CancelledError:
            self._pending.pop(correlation_id, None)
            raise

    async def _remove_subscription(self, sub: _AsyncSubscription) -> None:
        # Detach from the routing tables FIRST so no new event reaches
        # this subscription while we wait for its queue to drain. Any
        # ``publish`` racing with this call has already passed its
        # ``put`` and will be picked up by ``queue.join`` below.
        async with self._lock:
            if sub.is_all:
                if sub in self._all_subscriptions:
                    self._all_subscriptions.remove(sub)
            else:
                store = (
                    self._broadcast_subscriptions
                    if sub.is_broadcast
                    else self._subscriptions
                )
                subs = store.get(sub.topic, [])
                if sub in subs:
                    subs.remove(sub)
                if not subs:
                    store.pop(sub.topic, None)

        # Let the consumer drain whatever is still in flight. Without
        # this, tear-down during shutdown races with phase-3-down
        # publishes: a recorder cancelled mid-queue silently drops
        # every ``ComponentLifecycle.shutdown`` that hadn't been
        # consumed yet, leaving the telemetry log truncated. This is
        # the bus's contract -- every component treats the queue the
        # same way, none of them needs to know about ``queue.join``.
        if sub.task is not None and not sub.task.done():
            try:
                await sub.queue.join()
            except asyncio.CancelledError:
                # Cancellation propagating into ``_remove_subscription``
                # is treated as "give up cleanly". The cancel below
                # tears the consumer down regardless.
                pass

        async with self._lock:
            if sub.task is not None:
                sub.task.cancel()
                task = sub.task
                sub.task = None
            else:
                task = None
        if task is not None:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    def _ensure_consumer(self, sub: _AsyncSubscription) -> None:
        if sub.task is not None and not sub.task.done():
            return
        if sub.is_all:
            name = "bus-consumer:__all__"
        else:
            name = f"bus-consumer:{sub.topic}"
        sub.task = asyncio.create_task(self._run_consumer(sub), name=name)

    async def _run_consumer(self, sub: _AsyncSubscription) -> None:
        while True:
            event = await sub.queue.get()
            try:
                await sub.handler(event)
            except Exception:
                logger.exception(
                    "subscriber for topic %r raised on event %s",
                    sub.topic,
                    event.event_id,
                )
            finally:
                sub.queue.task_done()
