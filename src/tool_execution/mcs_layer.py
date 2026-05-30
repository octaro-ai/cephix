"""``MCSToolExecutionLayer`` -- BUS_PROVIDER, MCS-backed tool layer (stub).

Boot category :attr:`~src.components.ComponentCategory.BUS_PROVIDER`
(boot priority 8). Starts after AUDIT (every tool invocation can be
audited) and before ACTOR/KERNEL/CHANNEL (consumers reach the layer
exclusively through the bus -- no shared references).

Two entry points on the bus, mirroring MCS's two call paths:

- **Direct tool invocation** (the "I know the tool, run it"
  pattern). Topic: ``tool.invoke``. Producers (Heartbeat,
  Channels, future schedulers) publish a
  :class:`ComponentRequest` with ``action=<tool_name>`` and
  arguments in ``payload``. The layer dispatches by ``action``,
  runs the matching tool, and replies with a
  :class:`ComponentResponse` carrying the result.

- **LLM-shaped tool invocation** (the "here's an LLM output,
  figure out if it called a tool" pattern). Topic:
  ``tool.process_llm_output``. Producers (Actors, Kernels) send
  a request carrying the raw LLM text/dict; the layer routes it
  through MCS's ``process_llm_response`` and replies with the
  parsed result + agent-facing messages.

  *Not implemented yet*: only the direct path is wired in the
  stub. The LLM path lands in the next iteration when MCS engine
  + RestDriver are pulled in.

Status: **stub catalog**, no real MCS engine yet. Mailbox
``mailbox.fetch_unread`` returns five dummy messages so the bus
flow can be smoke-tested end-to-end. The interface this exposes on
the bus is exactly the shape the MCS-backed version will keep --
only the handler bodies change.
"""

from __future__ import annotations

import logging
from typing import Any

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

logger = logging.getLogger(__name__)


_TOOL_INVOKE_TOPIC = "tool.invoke"


_STUB_FETCH_UNREAD = ToolDescriptor(
    name="mailbox.fetch_unread",
    title="Fetch unread mails",
    description=(
        "Return the most recent unread messages from the configured "
        "mailbox, up to ``limit`` entries. Stub: returns five fixed "
        "dummy messages until the MCS IMAP adapter lands."
    ),
    parameters={
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "default": 5,
            },
            "mailbox_id": {"type": "string"},
        },
    },
)


class MCSToolExecutionLayer(BusComponent, ToolExecutionLayerPort):
    """Bus-attached tool execution surface (MCS-backed in a later iteration).

    Holds the tool registry and dispatches requests on the
    ``tool.invoke`` topic. Replies are :class:`ComponentResponse`
    instances correlated by ``correlation_id`` so
    :meth:`BusPort.request` callers get their result back without
    extra plumbing.

    The layer never holds direct references from consumers; the bus
    is the only contact surface. That keeps the layer
    hot-swappable (different engine, different adapter mix, future
    federation) without touching producers.
    """

    component_name = "tool-execution"
    component_category = ComponentCategory.BUS_PROVIDER
    component_description = (
        "Bus-attached tool execution layer. Subscribes ``tool.invoke`` "
        "for direct tool invocations and dispatches by ``action``. "
        "Currently ships with a stub mailbox.fetch_unread tool; the "
        "MCS engine drop-in is planned for the next iteration."
    )

    def __init__(self) -> None:
        self._tools: dict[str, ToolDescriptor] = {
            _STUB_FETCH_UNREAD.name: _STUB_FETCH_UNREAD,
        }
        self._bus: BusPort | None = None
        self._invoke_subscription: Subscription | None = None

    # ---- BusComponent lifecycle --------------------------------------------

    async def start(self, bus: BusPort) -> None:
        self._bus = bus
        self._invoke_subscription = bus.subscribe(
            _TOOL_INVOKE_TOPIC, self._handle_invoke
        )
        logger.info(
            "%s (%s) ready with %d tool(s) on %s: %s",
            type(self).__name__,
            self.instance_id,
            len(self._tools),
            _TOOL_INVOKE_TOPIC,
            ", ".join(sorted(self._tools)),
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

    def list_tools(self):
        return tuple(self._tools[name] for name in sorted(self._tools))

    async def invoke_tool(self, name, arguments) -> ToolInvocationResult:
        """Direct off-bus invocation. Used by tests and by code paths
        that already hold the layer instance (today: none -- the bus
        path is the canonical one)."""
        if name not in self._tools:
            raise KeyError(
                f"unknown tool {name!r}; registered: {sorted(self._tools)}"
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

        if tool_name not in self._tools:
            await self._reply_error(
                event,
                code="tool.unknown",
                message=f"unknown tool {tool_name!r}",
                details={"registered": sorted(self._tools)},
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

    # ---- Internal handler dispatch (engine boundary) -----------------------

    async def _run(
        self, name: str, arguments: dict[str, Any]
    ) -> ToolInvocationResult:
        """Single in-process dispatch. The MCS engine will replace
        this body with ``asyncio.to_thread(driver.execute_tool, ...)``
        once the orchestrator is wired in."""
        handler = self._handler_for(name)
        try:
            result_payload = await handler(arguments)
        except Exception as exc:
            return ToolInvocationResult(
                name=name, success=False, error=str(exc)
            )
        return ToolInvocationResult(name=name, success=True, result=result_payload)

    def _handler_for(self, name: str):
        if name == _STUB_FETCH_UNREAD.name:
            return self._stub_fetch_unread
        raise KeyError(f"no handler bound for {name!r}")

    @staticmethod
    async def _stub_fetch_unread(arguments: dict[str, Any]) -> dict[str, Any]:
        """Fixed-batch dummy fetch. Replaced by MCS IMAP adapter later."""
        limit = int(arguments.get("limit", 5) or 5)
        limit = max(1, min(limit, 50))
        mailbox_id = str(arguments.get("mailbox_id", "stub-mailbox"))
        return {
            "mailbox_id": mailbox_id,
            "fetched_at": "stub-timestamp",
            "messages": [
                {
                    "id": f"stub-msg-{i}",
                    "from": f"sender{i}@example.com",
                    "subject": f"Stub message {i}",
                    "snippet": f"This is dummy mail body number {i}.",
                }
                for i in range(1, limit + 1)
            ],
        }
