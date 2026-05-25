"""Bus port and the shared lifecycle contract for bus components.

The bus is the system's central organ. Every component the robot is
composed of -- kernel, channels, audit, governance -- attaches to the
bus through this port and exposes the :class:`BusComponent` lifecycle
so the robot can manage them uniformly.

The first bus implementation lives in :mod:`src.bus.asyncio_bus`. Later
implementations (NATS, Redis, SQLite-backed) must satisfy the same
contract so that swapping them is just an adapter exchange.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Protocol, runtime_checkable

from src.bus.messages import RobotEvent, RobotRequest, RobotResponse

EventHandler = Callable[[RobotEvent], Awaitable[None]]


@runtime_checkable
class BusComponent(Protocol):
    """Common lifecycle contract for everything that lives on the bus.

    A component subscribes to topics, publishes events, and possibly
    runs background tasks. The robot owns the component and drives
    its lifecycle. Both methods must be idempotent so the robot can
    recover from partial startup failures.

    Components are constructed with their own configuration only;
    the bus itself is injected at :meth:`start` time. This keeps
    "who am I" (constructor parameters: topics, ports, prefixes)
    cleanly separated from "where am I plugged in" (runtime context:
    the bus). It also lets the same component be moved between
    robots without reconstruction.
    """

    async def start(self, bus: "BusPort") -> None:
        """Bring the component online on ``bus`` (subscribe, open sockets, ...)."""

    async def stop(self) -> None:
        """Release every resource the component acquired in ``start``."""


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

    The bus carries :class:`RobotEvent` messages, routes them to
    per-subscriber queues by topic, and additionally supports a
    request/response pattern via ``correlation_id``.
    """

    async def start(self) -> None:
        """Start the bus. Idempotent."""

    async def stop(self) -> None:
        """Stop the bus. Remaining subscribers are unregistered and
        outstanding ``request`` calls fail with an error."""

    async def publish(self, event: RobotEvent) -> None:
        """Publish a message on the bus.

        Delivered to every subscriber whose topic exactly matches
        ``event.topic``. ``RobotResponse`` messages are additionally
        routed to outstanding ``request`` calls via ``correlation_id``.
        """

    def subscribe(self, topic: str, handler: EventHandler) -> Subscription:
        """Register a subscriber for a topic.

        Each subscriber gets its own FIFO queue, so a slow subscriber
        only slows down its own queue.
        """

    async def request(
        self,
        request: RobotRequest,
        *,
        timeout: float | None = None,
    ) -> RobotResponse:
        """Send a ``RobotRequest`` and await the matching ``RobotResponse``.

        ``timeout`` is in seconds; ``None`` waits indefinitely. On
        timeout, a ``RobotResponse`` with ``ok=False`` and a timeout
        error message is returned instead of raising.
        """
