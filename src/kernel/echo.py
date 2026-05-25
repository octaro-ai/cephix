"""Trivial echo kernel.

Iteration 1: subscribes to a single input topic, mirrors every
``RobotInput`` back as a ``RobotOutput`` on the configured output
topic. No context, no actor, no SOPs.

The kernel is constructed with its own configuration only; the bus
is injected at :meth:`start` time by the robot.
"""

from __future__ import annotations

from src.bus.messages import RobotEvent, RobotInput, RobotOutput
from src.bus.ports import BusPort, Subscription


class EchoKernel:
    """Echoes every input as an output on the configured topics."""

    def __init__(
        self,
        *,
        input_topic: str = "input.message",
        output_topic: str = "output.message",
        prefix: str = "echo: ",
    ) -> None:
        self._input_topic = input_topic
        self._output_topic = output_topic
        self._prefix = prefix
        self._bus: BusPort | None = None
        self._subscription: Subscription | None = None

    async def start(self, bus: BusPort) -> None:
        if self._bus is not None:
            return
        self._bus = bus
        self._subscription = bus.subscribe(self._input_topic, self._handle_input)

    async def stop(self) -> None:
        if self._subscription is not None:
            await self._subscription.unsubscribe()
            self._subscription = None
        self._bus = None

    def _require_bus(self) -> BusPort:
        if self._bus is None:
            raise RuntimeError("EchoKernel is not started")
        return self._bus

    async def _handle_input(self, event: RobotEvent) -> None:
        if not isinstance(event, RobotInput):
            return
        bus = self._require_bus()
        await bus.publish(
            RobotOutput(
                topic=self._output_topic,
                principal=event.principal,
                source="kernel.echo",
                run_id=event.run_id,
                text=f"{self._prefix}{event.text or ''}",
                payload=dict(event.payload),
            )
        )
