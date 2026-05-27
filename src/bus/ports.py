"""Bus port and the bus-attached component contract.

The bus is the system's central organ. Every component the robot is
composed of -- kernel, channels, audit, governance -- attaches to the
bus through this port when it inherits :class:`src.components.BusComponent`.
Plain :class:`src.components.RobotComponent` instances can exist
without a bus dependency.

The first bus implementation lives in :mod:`src.bus.asyncio_bus`. Later
implementations (NATS, Redis, SQLite-backed) must satisfy the same
contract so that swapping them is just an adapter exchange.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Protocol, runtime_checkable

from src.bus.messages import RobotEvent, ComponentRequest, ComponentResponse
from src.components import BusComponent

EventHandler = Callable[[RobotEvent], Awaitable[None]]


class Subscription(Protocol):
    """Handle of a subscriber.

    Subscribers stay alive until :meth:`unsubscribe` is called or the
    bus is stopped.
    """

    @property
    def topic(self) -> str:
        ...

    async def unsubscribe(self) -> None:
        ...


@runtime_checkable
class BusPort(Protocol):
    """Contract of the system bus.

    The bus carries :class:`RobotEvent` messages and supports two
    distinct delivery semantics:

    * **Routable queue** (default): :meth:`publish` and :meth:`subscribe`.
      Each subscriber gets its own FIFO queue. Used for the bulk of
      traffic where every event needs to be processed.
    * **Broadcast** with optional retention: :meth:`publish_broadcast`
      and :meth:`subscribe_broadcast`. Used for lifecycle events and
      audit notes where multiple observers need to see the same event
      and late subscribers may need to learn the latest state.

    On top of routable queues, :meth:`request` provides a directed
    request/response pattern via ``correlation_id``.

    Cross-cutting observers (telemetry, tracing, metrics) subscribe
    via :meth:`subscribe_all`. That subscription is read-only by
    design -- the bus delivers a copy of every event to it, but the
    handler cannot block, modify, or veto the event. ACL-style
    interception ("this principal must not publish on this topic")
    will be a separate, actively-designed mechanism, not an extension
    of ``subscribe_all``.
    """

    async def start(self) -> None:
        """Start the bus. Idempotent."""

    async def stop(self) -> None:
        """Stop the bus. Remaining subscribers are unregistered and
        outstanding ``request`` calls fail with an error."""

    async def publish(self, event: RobotEvent) -> None:
        """Publish a message on the bus.

        Delivered to every subscriber whose topic exactly matches
        ``event.topic``. ``ComponentResponse`` messages are additionally
        routed to outstanding ``request`` calls via ``correlation_id``.
        """

    def subscribe(self, topic: str, handler: EventHandler) -> Subscription:
        """Register a subscriber for a topic.

        Each subscriber gets its own FIFO queue, so a slow subscriber
        only slows down its own queue.
        """

    async def publish_broadcast(
        self,
        event: RobotEvent,
        *,
        retain: bool = False,
    ) -> None:
        """Broadcast ``event`` to every broadcast subscriber of its topic.

        When ``retain`` is true, the bus keeps ``event`` as the latest
        retained message for the topic. Subsequent calls to
        :meth:`subscribe_broadcast` on that topic deliver the retained
        event to the new subscriber immediately as their first event.
        Only one retained event is kept per topic; the latest replaces
        any previous one.
        """

    def subscribe_broadcast(self, topic: str, handler: EventHandler) -> Subscription:
        """Register a broadcast subscriber for a topic.

        If a retained event exists on the topic, it is delivered to the
        new subscriber as the first event. Otherwise the subscriber
        receives only future broadcasts.
        """

    def subscribe_all(self, handler: EventHandler) -> Subscription:
        """Subscribe to *every* event published on the bus.

        Each call registers an additional read-only observer that
        receives a copy of every event delivered through
        :meth:`publish` and :meth:`publish_broadcast`, regardless of
        topic. Each all-subscriber gets its own FIFO queue, so a slow
        recorder cannot back up the routable subscribers it is
        listening alongside.

        Intended for cross-cutting observers: audit recorders,
        telemetry sinks, metrics collectors, distributed tracers.
        The handler cannot block, modify, or refuse delivery to the
        regular subscribers. Pre-delivery interception (topic ACLs,
        governance) will arrive as a separately-designed mechanism
        if and when it is needed.
        """

    def retained(self, topic: str) -> RobotEvent | None:
        """Return the latest retained event for ``topic`` if any.

        Synchronous lookup that lets a component bootstrap its state
        from the retained snapshot without having to wait for an async
        consumer task to drain the queue.
        """

    async def request(
        self,
        request: ComponentRequest,
        *,
        timeout: float | None = None,
    ) -> ComponentResponse:
        """Send a ``ComponentRequest`` and await the matching ``ComponentResponse``.

        ``timeout`` is in seconds; ``None`` waits indefinitely. On
        timeout, a ``ComponentResponse`` with ``ok=False`` and a timeout
        error message is returned instead of raising.
        """
