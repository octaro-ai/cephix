"""``MCSToolExecutionLayer`` -- BUS_PROVIDER, hosts MCS ToolDrivers.

Boot category :attr:`~src.components.ComponentCategory.BUS_PROVIDER`
(boot priority 8). Starts after AUDIT (every tool invocation can be
audited) and before ACTOR/KERNEL/CHANNEL (consumers reach the layer
exclusively through the bus -- no shared references).

The layer is the bus-facing seam for the Model Context Standard
(MCS). It owns one or more :class:`mcs.driver.core.MCSToolDriver`
instances, aggregates their tool catalogues, and routes invocations
back to the driver that owns each tool. There is no in-line stub
anymore -- every tool is contributed by a ToolDriver built the
MCS way (port -> adapter -> tooldriver).

Two entry points on the bus, mirroring MCS's two call paths:

- **Direct tool invocation** (the "I know the tool, run it"
  pattern). Topic: ``tool.invoke``. Producers (HeartbeatChannel,
  future schedulers) publish a :class:`ComponentRequest` with
  ``action=<tool_name>`` and arguments in ``payload``. The layer
  dispatches by ``action``, calls the owning driver's
  ``execute_tool`` (wrapped in :func:`asyncio.to_thread` because
  MCS's ``execute_tool`` is synchronous), and replies with a
  :class:`ComponentResponse` carrying the result.

- **LLM-shaped tool invocation** (``tool.process_llm_output``)
  -- planned. Producers (Actors, Kernels) will send a request
  carrying the raw LLM text/dict; the layer will route it
  through a higher-level MCS Driver's ``process_llm_response``
  and reply with the parsed result + agent-facing messages.
  Not implemented in this iteration.

Today's drivers, wired by the layer's default constructor:

- :class:`MailboxToolDriver` -- in-process, exposes
  ``mailbox.fetch_unread`` and returns a constant batch of dummy
  messages. No adapter or port layer until a real backend is
  introduced.

Swapping in a real ToolDriver is a constructor swap: the layer
takes whatever ToolDrivers it is given and asks no questions
about how (or whether) they reach external backends.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

from mcs.driver.core import MCSToolDriver, Tool

from src.bus.messages import (
    ComponentRequest,
    ComponentResponse,
    ErrorInfo,
    RobotEvent,
    _new_event_id,
    _now_iso,
)
from src.bus.ports import BusPort, Subscription
from src.components import BusComponent, ComponentCategory
from src.tool_execution.ports import (
    ToolDescriptor,
    ToolExecutionLayerPort,
    ToolInvocationResult,
)
from mcs.driver.mailbox import MailboxToolDriver

logger = logging.getLogger(__name__)


_TOOL_INVOKE_TOPIC = "tool.invoke"


def _default_tool_drivers() -> list[MCSToolDriver]:
    """Driver set the layer wires by default at boot.

    Today: a single :class:`MailboxToolDriver` whose
    ``execute_tool`` runs entirely in-process, so a fresh robot
    can exercise the full bus -> tool -> response path end-to-end
    without configuring a backend. When real backends arrive
    (IMAP, JMAP, ...) they ship as ``mcs-adapter-*`` packages
    behind a then-introduced ``MailboxAdapterPort`` and the
    ToolDriver delegates to them.
    """
    return [MailboxToolDriver()]


class MCSToolExecutionLayer(BusComponent, ToolExecutionLayerPort):
    """Bus-attached tool execution surface backed by MCS ToolDrivers.

    Holds an ordered list of MCS ToolDrivers and dispatches
    requests on the ``tool.invoke`` topic. Replies are
    :class:`ComponentResponse` instances correlated by
    ``correlation_id`` so :meth:`BusPort.request` callers get
    their result back without extra plumbing.

    The layer never holds direct references from consumers; the bus
    is the only contact surface. That keeps the layer
    hot-swappable (different driver mix, different adapters,
    future federation) without touching producers.
    """

    component_name = "tool-execution"
    component_category = ComponentCategory.BUS_PROVIDER
    component_description = (
        "Bus-attached tool execution layer. Hosts MCS ToolDrivers "
        "(built per the MCS Port -> Adapter -> ToolDriver pattern) "
        "and dispatches direct ``tool.invoke`` requests by action "
        "to the owning driver. Sync ``execute_tool`` is run on a "
        "worker thread so the bus loop stays responsive."
    )

    def __init__(
        self,
        *,
        tool_drivers: Sequence[MCSToolDriver] | None = None,
    ) -> None:
        drivers = (
            list(tool_drivers)
            if tool_drivers is not None
            else _default_tool_drivers()
        )
        for index, driver in enumerate(drivers):
            if not isinstance(driver, MCSToolDriver):
                raise TypeError(
                    f"MCSToolExecutionLayer.tool_drivers[{index}] must "
                    f"be an MCSToolDriver, got {type(driver).__name__}"
                )
        self._drivers: list[MCSToolDriver] = drivers
        self._tool_index: dict[str, tuple[MCSToolDriver, Tool]] = {}
        self._rebuild_index()
        self._bus: BusPort | None = None
        self._invoke_subscription: Subscription | None = None

    def _rebuild_index(self) -> None:
        """Recompute name -> (driver, Tool) lookup from the driver list.

        Last writer wins on collision and a warning is logged; in
        practice driver authors namespace their tools (the MCS
        convention is ``<capability>.<verb>``) so collisions are
        rare and the warning is the right signal.
        """
        index: dict[str, tuple[MCSToolDriver, Tool]] = {}
        for driver in self._drivers:
            for tool in driver.list_tools():
                if tool.name in index:
                    logger.warning(
                        "MCSToolExecutionLayer: tool name collision on "
                        "%r; %s overrides earlier registration",
                        tool.name,
                        type(driver).__name__,
                    )
                index[tool.name] = (driver, tool)
        self._tool_index = index

    # ---- BusComponent lifecycle --------------------------------------------

    async def start(self, bus: BusPort) -> None:
        self._bus = bus
        self._invoke_subscription = bus.subscribe(
            _TOOL_INVOKE_TOPIC, self._handle_invoke
        )
        names = sorted(self._tool_index)
        logger.info(
            "%s (%s) ready with %d driver(s), %d tool(s) on %s: %s",
            type(self).__name__,
            self.instance_id,
            len(self._drivers),
            len(names),
            _TOOL_INVOKE_TOPIC,
            ", ".join(names) if names else "<none>",
        )
        await self.announce_lifecycle(bus, "ready")

    async def _stop(self) -> None:
        if self._bus is not None:
            await self.announce_lifecycle(self._bus, "shutdown")
        if self._invoke_subscription is not None:
            try:
                await self._invoke_subscription.unsubscribe()
            finally:
                self._invoke_subscription = None
        self._bus = None

    # ---- Direct ToolExecutionLayerPort surface (off-bus) -------------------

    def list_tools(self) -> Sequence[ToolDescriptor]:
        """Return aggregated, sorted descriptors across every driver."""
        return tuple(
            self._to_descriptor(tool)
            for _, tool in sorted(
                self._tool_index.values(), key=lambda pair: pair[1].name
            )
        )

    async def invoke_tool(
        self, name: str, arguments: Any
    ) -> ToolInvocationResult:
        """Direct off-bus invocation, used by tests and code paths
        that already hold the layer instance."""
        if name not in self._tool_index:
            raise KeyError(
                f"unknown tool {name!r}; registered: {sorted(self._tool_index)}"
            )
        return await self._run(name, dict(arguments))

    # ---- Bus dispatch ------------------------------------------------------

    async def _handle_invoke(self, event: RobotEvent) -> None:
        """Subscriber for ``tool.invoke`` requests.

        Expects a :class:`ComponentRequest` whose ``action`` is the
        tool name and whose ``payload`` is the argument dict.
        Replies with a :class:`ComponentResponse` correlated on
        ``correlation_id``.
        """
        if not isinstance(event, ComponentRequest):
            return
        if self._bus is None:
            return

        tool_name = event.action
        arguments = dict(event.payload or {})

        if tool_name not in self._tool_index:
            await self._reply_error(
                event,
                code="tool.unknown",
                message=f"unknown tool {tool_name!r}",
                details={"registered": sorted(self._tool_index)},
            )
            return

        try:
            result = await self._run(tool_name, arguments)
        except Exception as exc:
            logger.exception(
                "%s: dispatch for tool %r raised",
                type(self).__name__,
                tool_name,
            )
            await self._reply_error(
                event,
                code="tool.dispatch_failed",
                message=f"{type(exc).__name__}: {exc}",
                details={"tool": tool_name},
            )
            return

        if result.success:
            payload: dict[str, Any] = (
                dict(result.result) if isinstance(result.result, dict) else
                {"result": result.result}
            )
            await self._reply_ok(event, payload=payload)
        else:
            await self._reply_error(
                event,
                code="tool.execution_failed",
                message=result.error or "tool reported failure",
                details={"tool": tool_name},
            )

    async def _reply_ok(
        self, request: ComponentRequest, *, payload: dict[str, Any]
    ) -> None:
        assert self._bus is not None
        await self._bus.publish(
            ComponentResponse(
                event_id=_new_event_id(),
                topic=request.topic,
                principal=request.principal,
                source=self.component_name,
                source_id=self.instance_id,
                run_id=request.run_id,
                correlation_id=request.correlation_id,
                timestamp=_now_iso(),
                status="ok",
                payload=payload,
            )
        )

    async def _reply_error(
        self,
        request: ComponentRequest,
        *,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        assert self._bus is not None
        await self._bus.publish(
            ComponentResponse(
                event_id=_new_event_id(),
                topic=request.topic,
                principal=request.principal,
                source=self.component_name,
                source_id=self.instance_id,
                run_id=request.run_id,
                correlation_id=request.correlation_id,
                timestamp=_now_iso(),
                status="error",
                error=ErrorInfo(
                    code=code,
                    message=message,
                    details=dict(details or {}),
                ),
            )
        )

    # ---- Internal dispatch (engine boundary) -------------------------------

    async def _run(
        self, name: str, arguments: dict[str, Any]
    ) -> ToolInvocationResult:
        """Run a tool on its owning driver.

        MCS's ``execute_tool`` is synchronous (so adapter authors
        do not have to write async wrappers around imaplib /
        msal / etc.). We hop to a worker thread with
        :func:`asyncio.to_thread` so the bus loop stays responsive
        even when an adapter blocks on network IO.
        """
        driver, _ = self._tool_index[name]
        try:
            result_payload = await asyncio.to_thread(
                driver.execute_tool, name, arguments
            )
        except Exception as exc:
            return ToolInvocationResult(
                name=name, success=False, error=str(exc)
            )
        return ToolInvocationResult(name=name, success=True, result=result_payload)

    # ---- MCS Tool -> cephix ToolDescriptor adapter -------------------------

    @staticmethod
    def _to_descriptor(tool: Tool) -> ToolDescriptor:
        """Convert an MCS :class:`Tool` to the cephix-facing descriptor.

        The two shapes match for the most part; this method does
        the field rename / parameter-flatten so consumers of the
        cephix port (``ToolExecutionLayerPort``) do not have to
        depend on the MCS types directly.
        """
        properties: dict[str, Any] = {}
        required: list[str] = []
        for param in tool.parameters or ():
            properties[param.name] = dict(param.schema or {})
            if not properties[param.name].get("description"):
                properties[param.name]["description"] = param.description
            if param.required:
                required.append(param.name)
        schema: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        return ToolDescriptor(
            name=tool.name,
            title=tool.title or tool.name,
            description=tool.description or tool.title or tool.name,
            parameters=schema,
        )
