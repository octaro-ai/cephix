"""Turn a component's ``provides_commands`` into bus subscriptions.

The wiring helper is the only boilerplate a command-providing component
needs. A component declares its :class:`~src.command.spec.CommandSpec`
tuple and implements the matching ``cmd_*`` methods; in its ``start``
hook it calls :func:`wire_commands(self, bus)` and keeps the returned
subscriptions to unsubscribe on ``stop``.

Design choices (from the command-layer discussion):

- **Direct subscription, no central dispatcher.** Each command's
  request topic is subscribed by the owning component itself.
- **No reflection on the hot path.** The handler method is resolved
  once here via ``getattr``; a bad ``handler`` name fails loudly at
  wire time.
- **Handlers stay pure.** A ``cmd_*`` method takes the
  :class:`CommandRequest` and returns a plain ``dict`` payload (or
  raises). This helper wraps that into the bus protocol: it builds and
  publishes the :class:`CommandResponse`, turning a raised exception
  into a ``status="error"`` response so the caller always hears back
  under its ``correlation_id``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from src.bus.messages import (
    CommandRequest,
    CommandResponse,
    ErrorInfo,
    command_request_topic,
    command_response_topic,
)

if TYPE_CHECKING:
    from src.bus.ports import BusPort, Subscription
    from src.command.spec import CommandSpec
    from src.components import RobotComponent

logger = logging.getLogger(__name__)

CommandHandler = Callable[[CommandRequest], Awaitable[dict[str, Any]]]


def wire_commands(
    component: "RobotComponent",
    bus: "BusPort",
) -> list["Subscription"]:
    """Subscribe every spec in ``component.provides_commands``.

    Returns the list of :class:`Subscription` handles the component
    should unsubscribe on ``stop``. Raises ``AttributeError`` at call
    time if a spec names a handler the component does not implement.
    """
    subscriptions: list[Subscription] = []
    specs: tuple[CommandSpec, ...] = getattr(component, "provides_commands", ())
    for spec in specs:
        handler = getattr(component, spec.handler)
        if not callable(handler):
            raise TypeError(
                f"{type(component).__name__}.{spec.handler} is not callable; "
                f"CommandSpec({spec.action!r}) cannot be wired"
            )
        topic = command_request_topic(spec.action, spec.discriminator)
        consumer = _make_consumer(component, spec, handler, bus)
        subscriptions.append(bus.subscribe(topic, consumer))
    return subscriptions


def _make_consumer(
    component: "RobotComponent",
    spec: "CommandSpec",
    handler: CommandHandler,
    bus: "BusPort",
) -> Callable[[Any], Awaitable[None]]:
    async def _consume(event: Any) -> None:
        if not isinstance(event, CommandRequest):
            return
        await _dispatch(component, spec, handler, bus, event)

    return _consume


async def _dispatch(
    component: "RobotComponent",
    spec: "CommandSpec",
    handler: CommandHandler,
    bus: "BusPort",
    request: CommandRequest,
) -> None:
    response_topic = command_response_topic(spec.action, spec.discriminator)
    try:
        result = await handler(request)
        payload = dict(result) if result else {}
        response = CommandResponse(
            topic=response_topic,
            principal=request.principal,
            source=component.component_name,
            source_id=component.instance_id,
            run_id=request.run_id,
            correlation_id=request.correlation_id,
            action=spec.action,
            target=spec.discriminator,
            payload=payload,
        )
    except Exception as exc:  # noqa: BLE001 -- turn any failure into a response
        logger.exception(
            "command handler %s.%s failed for action %s",
            type(component).__name__,
            spec.handler,
            spec.action,
        )
        response = CommandResponse(
            topic=response_topic,
            principal=request.principal,
            source=component.component_name,
            source_id=component.instance_id,
            run_id=request.run_id,
            correlation_id=request.correlation_id,
            action=spec.action,
            target=spec.discriminator,
            status="error",
            error=ErrorInfo(
                code="command.handler_failed",
                message=f"{type(exc).__name__}: {exc}",
                details={"action": spec.action},
            ),
        )
    await bus.publish(response)
