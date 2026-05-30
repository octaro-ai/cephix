"""``HeartbeatChannel`` -- a scheduled input source for the bot.

The heartbeat is modelled as a CHANNEL (boot priority 11): it
brings input *into* the bot from the outside (here: a clock), much
like a WebSocket channel brings input from a user. The robot
itself does not know that "a heartbeat ticked" -- it sees regular
:class:`RobotInput` arrivals on a channel-owned topic, exactly the
same shape an authenticated WebSocket message would take.

Architecturally the channel works on behalf of an implicit user
(the robot's owner): every published event carries the robot's
identity as ``principal``, never an externally connected client.
This keeps the bus invariant that every input has a producer
behind it intact, without inventing a phantom "system" identity.

Cycle:

1. Wake on a configurable interval (default 5 min).
2. Direct-invoke a configured tool through the injected
   :class:`ToolExecutionLayerPort` (default
   ``mailbox.fetch_unread`` with limit 5).
3. Publish the result as a :class:`RobotInput` on
   ``input.heartbeat``. Anyone (a RuleBasedKernel, an audit
   subscriber, a future UI) can react -- if nobody listens that's
   fine, the heartbeat keeps ticking.

The channel does **not** wait for downstream processing or queue
heartbeat batches if no consumer exists; an idle heartbeat is just
a publish into a topic with no broadcast subscribers.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.bus.messages import RobotInput
from src.bus.ports import BusPort
from src.channels.ports import ChannelPort
from src.components import ComponentCategory
from src.tool_execution.ports import ToolExecutionLayerPort

logger = logging.getLogger(__name__)


_DEFAULT_TOPIC = "input.heartbeat"
_DEFAULT_TOOL = "mailbox.fetch_unread"
_DEFAULT_INTERVAL_SECONDS = 300.0
_DEFAULT_PRINCIPAL_TEMPLATE = "robot:heartbeat"


class HeartbeatChannel(ChannelPort):
    """Periodically invoke a tool and publish the result as input.

    Constructor wiring (DI):

    - ``tool_layer`` -- the :class:`ToolExecutionLayerPort` to call
      every tick (Convention-DI: the builder injects the single
      ``tool-execution`` BUS_PROVIDER instance).
    - ``interval_seconds`` -- delay between ticks (default 300).
      Set to a small value in tests to keep them fast.
    - ``tool_name`` -- which tool to invoke (default
      ``mailbox.fetch_unread``).
    - ``tool_arguments`` -- arguments passed verbatim to
      ``invoke_tool``. Default ``{"limit": 5}``.
    - ``topic`` -- bus topic the resulting input is published on.
    - ``principal`` -- attribution for the published event.

    Lifecycle:

    - :meth:`start` launches the background tick task.
    - :meth:`_stop` cancels the task and waits for clean exit.
      Drain is a no-op (the loop polls; nothing to flush).
    """

    component_name = "heartbeat"
    component_category = ComponentCategory.CHANNEL
    component_description = (
        "Channel-level scheduled input source. Wakes on a fixed "
        "interval, directly invokes a configured tool through the "
        "ToolExecutionLayer, and publishes the result as a "
        "RobotInput on its configured topic. Acts on behalf of the "
        "robot's implicit owner; no external connection involved."
    )

    def __init__(
        self,
        *,
        tool_layer: ToolExecutionLayerPort,
        interval_seconds: float = _DEFAULT_INTERVAL_SECONDS,
        tool_name: str = _DEFAULT_TOOL,
        tool_arguments: dict[str, Any] | None = None,
        topic: str = _DEFAULT_TOPIC,
        principal: str = _DEFAULT_PRINCIPAL_TEMPLATE,
    ) -> None:
        if not isinstance(tool_layer, ToolExecutionLayerPort):
            raise TypeError(
                "HeartbeatChannel.tool_layer must implement "
                "ToolExecutionLayerPort, got "
                f"{type(tool_layer).__name__}"
            )
        if interval_seconds <= 0:
            raise ValueError(
                "HeartbeatChannel.interval_seconds must be > 0, "
                f"got {interval_seconds!r}"
            )
        if not tool_name:
            raise ValueError("HeartbeatChannel.tool_name must be non-empty")
        if not topic:
            raise ValueError("HeartbeatChannel.topic must be non-empty")
        if not principal:
            raise ValueError("HeartbeatChannel.principal must be non-empty")

        self._tool_layer = tool_layer
        self._interval = float(interval_seconds)
        self._tool_name = tool_name
        self._tool_arguments: dict[str, Any] = (
            dict(tool_arguments) if tool_arguments else {}
        )
        self._topic = topic
        self._principal = principal

        self._bus: BusPort | None = None
        self._task: asyncio.Task[None] | None = None
        self._tick_count = 0

    # ---- BusComponent lifecycle --------------------------------------------

    async def start(self, bus: BusPort) -> None:
        if self._task is not None:
            return
        self._bus = bus
        tool_layer_id = getattr(self._tool_layer, "instance_id", "")
        logger.info(
            "%s (%s) injected into %s (%s) on tool %r every %.1fs",
            type(self._tool_layer).__name__,
            tool_layer_id,
            type(self).__name__,
            self.instance_id,
            self._tool_name,
            self._interval,
        )
        self._task = asyncio.create_task(
            self._heartbeat_loop(),
            name=f"heartbeat:{self._tool_name}",
        )
        await self.announce_lifecycle(bus, "ready")

    async def _stop(self) -> None:
        if self._bus is not None:
            await self.announce_lifecycle(self._bus, "shutdown")
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        self._bus = None

    # ---- Loop --------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        """Sleep, invoke, publish, repeat. Cancels cleanly on stop()."""
        try:
            while True:
                await asyncio.sleep(self._interval)
                await self._tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "HeartbeatChannel: tick loop died; no further ticks "
                "will be emitted until the component is restarted"
            )

    async def _tick(self) -> None:
        """One iteration: invoke the tool, publish the result."""
        if self._bus is None:
            return
        self._tick_count += 1
        run_id = f"heartbeat-{self._tick_count:08d}"
        try:
            result = await self._tool_layer.invoke_tool(
                self._tool_name, self._tool_arguments
            )
        except Exception:
            logger.exception(
                "HeartbeatChannel: invoke_tool(%r) raised; tick skipped",
                self._tool_name,
            )
            return

        payload: dict[str, Any] = {
            "tool": self._tool_name,
            "success": result.success,
            "tick": self._tick_count,
        }
        if result.success:
            payload["result"] = result.result
        else:
            payload["error"] = result.error

        try:
            await self._bus.publish(
                RobotInput(
                    topic=self._topic,
                    principal=self._principal,
                    source=self.component_name,
                    source_id=self.instance_id,
                    run_id=run_id,
                    message="",
                    payload=payload,
                )
            )
        except Exception:
            logger.exception(
                "HeartbeatChannel: failed to publish tick %d on %s",
                self._tick_count,
                self._topic,
            )
