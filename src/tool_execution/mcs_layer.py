"""``MCSToolExecutionLayer`` -- BUS_PROVIDER component, MCS-backed tool layer.

Boot category :attr:`~src.components.ComponentCategory.BUS_PROVIDER`
(boot priority 8). Starts after AUDIT (every tool invocation can be
audited) and before ACTOR (kernels and actors consume the layer
through Convention-DI by reference).

Current status: **skeleton with a stub tool catalog**. The MCS engine
itself (orchestrator + drivers + adapters) is not yet wired in. The
class is structured so the next iteration drops MCS in without
changing the port-facing API:

- :meth:`list_tools` returns the current stub catalog -- one tool
  (``mailbox.fetch_unread``) that returns five dummy messages so
  downstream wiring (HeartbeatChannel, bus subscribers) can be
  built and smoke-tested end-to-end.
- :meth:`invoke_tool` dispatches by name to internal handlers. The
  next iteration replaces the handler body with an
  ``asyncio.to_thread(driver.execute_tool, ...)`` against an MCS
  driver constructed at start time.
- :meth:`_stop` will tear down the MCS orchestrator and any open
  adapter connections when they exist.

Why a stub now: the architecture decisions (port shape, boot level,
Convention-DI in the builder, ``provides_commands`` exposure on the
bus) need to settle before MCS goes in. A stub lets us prove the
flow end-to-end -- bus subscribes, capability manifest updates,
heartbeats fire, mail batches land -- without dragging MCS, requests,
or imap config into the smoke loop yet.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from src.bus.ports import BusPort
from src.components import BusComponent, ComponentCategory
from src.tool_execution.ports import (
    ToolDescriptor,
    ToolExecutionLayerPort,
    ToolInvocationResult,
)

logger = logging.getLogger(__name__)


_STUB_FETCH_UNREAD = ToolDescriptor(
    name="mailbox.fetch_unread",
    title="Fetch unread mails",
    description=(
        "Return the most recent unread messages from the configured "
        "mailbox, up to ``limit`` entries. Stub implementation: returns "
        "five fixed dummy messages until the MCS IMAP adapter lands."
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
            "mailbox_id": {
                "type": "string",
                "description": "Tenant identifier; ignored in the stub.",
            },
        },
    },
)


class MCSToolExecutionLayer(BusComponent, ToolExecutionLayerPort):
    """Bus-attached tool-execution surface backed (eventually) by MCS.

    Constructor wiring (DI): no required arguments yet. When MCS is
    introduced, the constructor will take a configured
    ``MCSOrchestrator`` plus a list of registered drivers, and the
    builder will resolve them from a ``tool_execution:`` YAML
    section in the same shape as ``persistence:`` -- a list of
    ``{driver: ..., adapter: ..., credentials: ...}`` entries the
    builder lowers to MCS construction calls.

    Lifecycle:

    - :meth:`start` records the bus and announces lifecycle; future
      MCS startup hooks (open SMB sessions, validate IMAP auth)
      land here.
    - :meth:`_stop` will close the engine cleanly.
    """

    component_name = "tool-execution"
    component_category = ComponentCategory.BUS_PROVIDER
    component_description = (
        "Bus-attached tool execution layer. Exposes registered tools "
        "via list_tools() and direct invoke_tool(), and (once wired) "
        "will route LLM-shaped tool calls through the MCS engine. "
        "Currently ships with a stub mailbox.fetch_unread tool so "
        "downstream wiring can be tested before the MCS engine lands."
    )

    def __init__(self) -> None:
        # Internal tool registry. With MCS this becomes an
        # ``Orchestrator`` reference; for the stub it is just a
        # dict keyed by tool name.
        self._tools: dict[str, ToolDescriptor] = {
            _STUB_FETCH_UNREAD.name: _STUB_FETCH_UNREAD,
        }
        self._bus: BusPort | None = None

    # ---- BusComponent lifecycle --------------------------------------------

    async def start(self, bus: BusPort) -> None:
        self._bus = bus
        logger.info(
            "%s (%s) ready with %d tool(s): %s",
            type(self).__name__,
            self.instance_id,
            len(self._tools),
            ", ".join(sorted(self._tools)),
        )
        await self.announce_lifecycle(bus, "ready")

    async def _stop(self) -> None:
        if self._bus is not None:
            await self.announce_lifecycle(self._bus, "shutdown")
        self._bus = None

    # ---- ToolExecutionLayerPort --------------------------------------------

    def list_tools(self) -> Sequence[ToolDescriptor]:
        return tuple(self._tools[name] for name in sorted(self._tools))

    async def invoke_tool(
        self, name: str, arguments: Mapping[str, Any]
    ) -> ToolInvocationResult:
        if name not in self._tools:
            raise KeyError(
                f"unknown tool {name!r}; registered: {sorted(self._tools)}"
            )
        handler = self._handler_for(name)
        try:
            payload = await handler(dict(arguments))
        except Exception as exc:
            logger.exception(
                "%s: tool %r raised; returning failure result",
                type(self).__name__,
                name,
            )
            return ToolInvocationResult(
                name=name, success=False, result=None, error=str(exc)
            )
        return ToolInvocationResult(
            name=name, success=True, result=payload, error=None
        )

    # ---- Internals ---------------------------------------------------------

    def _handler_for(self, name: str):
        if name == _STUB_FETCH_UNREAD.name:
            return self._stub_fetch_unread
        raise KeyError(f"no handler bound for {name!r}")

    @staticmethod
    async def _stub_fetch_unread(arguments: dict[str, Any]) -> dict[str, Any]:
        """Return a fixed batch of dummy messages.

        Replaced by an MCS-driven IMAP fetch in the next iteration.
        Shape mirrors what the real tool will return so consumers
        (HeartbeatChannel, RuleBasedKernel) can be wired against it
        already.
        """
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
