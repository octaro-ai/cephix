"""``MCSToolExecutionLayer`` -- BUS_PROVIDER, hosts MCS ToolDrivers.

Boot category :attr:`~src.components.ComponentCategory.BUS_PROVIDER`
(boot priority 8). Starts after AUDIT (every tool invocation can be
audited) and before ACTOR/KERNEL/CHANNEL (consumers reach the layer
exclusively through the bus -- no shared references).

The layer is the bus-facing seam for the Model Context Standard
(MCS). It owns one or more :class:`mcs.driver.core.MCSToolDriver`
instances, aggregates their tool catalogues, and routes invocations
back to the driver that owns each tool. Tools are contributed by
ToolDrivers built the MCS way (port -> adapter -> tooldriver).

The layer exposes one DI seam for backend transports: a
:class:`FilesystemConnection` (today: optional). When passed, the
layer builds an :class:`MCSFilesystemAdapter` over it and wires the
upstream ``mcs-driver-filesystem`` hybrid driver into its driver
list, so MCS file-IO tools (``list_directory``, ``read_file``,
``write_file``) route through the same adapter chain the rest of
Cephix's persistence layer uses.

Capability surfacing: the layer overrides ``component_info()`` so
its ``ComponentLifecycle`` events carry one ``provides_commands``
entry per registered Tool. The ``CapabilityCollector`` aggregates
those into the retained ``harness.capabilities`` manifest, so UIs
see the available tools the same way they see chat session
commands.

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

- :class:`ClockToolDriver` (from ``mcs-driver-clock``) -- exposes
  ``current_time``. UTC by default; optional IANA timezone for the
  local representation.
- :class:`CalculatorToolDriver` (from ``mcs-driver-calculator``) --
  exposes ``calculate``. Scientific-calculator-grade math
  expression evaluator sandboxed via an AST whitelist (no
  ``eval()``, no imports, no attribute access).
- :class:`FilesystemDriver` (from ``mcs-driver-filesystem``) wired
  over :class:`MCSFilesystemAdapter`, opt-in: only when the layer
  is constructed with ``filesystem_connection``. Exposes
  ``list_directory``, ``read_file``, ``write_file``.

Swapping in a real ToolDriver is a constructor swap: the layer
takes whatever ToolDrivers it is given and asks no questions
about how (or whether) they reach external backends.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

from mcs.driver.calculator import CalculatorToolDriver
from mcs.driver.clock import ClockToolDriver
from mcs.driver.core import MCSToolDriver, Tool
from mcs.driver.filesystem import FilesystemDriver
from mcs.driver.filesystem.tooldriver import FilesystemToolDriver

from src.bus.messages import (
    CommandRequest,
    CommandResponse,
    ComponentInfo,
    ComponentRequest,
    ComponentResponse,
    ErrorInfo,
    RobotEvent,
    _new_event_id,
    _now_iso,
    command_request_topic,
    command_response_topic,
)
from src.bus.ports import BusPort, Subscription
from src.components import BusComponent, ComponentCategory
from src.persistence.filesystem.connection import FilesystemConnection
from src.tool_execution.mcs_adapters import MCSFilesystemAdapter
from src.tool_execution.ports import (
    ToolDescriptor,
    ToolExecutionLayerPort,
    ToolInvocationResult,
)

logger = logging.getLogger(__name__)


_TOOL_INVOKE_TOPIC = "tool.invoke"


_MUTATION_VERB_PREFIXES = (
    "write_", "set_", "delete_", "remove_", "update_", "create_",
)


def _risk_class_for(tool_name: str) -> str:
    """Map a tool name to a capability ``risk_class`` value.

    Pure heuristic on the verb component of the tool name. The MCS
    side is verb-prefixed by convention (``list_directory``,
    ``read_file``, ``write_file``, ``mailbox.fetch_unread``); the
    leaf after the last dot is the verb candidate. Returns the
    string the manifest carries (``"read_only"`` /
    ``"low_risk_mutation"``); the value space matches
    :class:`src.command.spec.RiskClass`.
    """
    leaf = tool_name.rsplit(".", 1)[-1].lower()
    if any(leaf.startswith(p) for p in _MUTATION_VERB_PREFIXES):
        return "low_risk_mutation"
    return "read_only"


def _default_tool_drivers() -> list[MCSToolDriver]:
    """Driver set the layer wires by default at boot.

    Both ship as standalone packages under ``packages/`` and run
    entirely in-process -- no transport layer, no backend service
    -- so a fresh robot exposes useful tools immediately.

    - :class:`ClockToolDriver` -- ``current_time`` (UTC + optional
      IANA timezone).
    - :class:`CalculatorToolDriver` -- ``calculate`` (sandboxed
      scientific math expression evaluator).

    The filesystem driver is wired separately when a
    ``FilesystemConnection`` is injected; transport-bound drivers
    in general (future IMAP, REST, ...) join the list through
    constructor injection of their adapters, not here.
    """
    return [ClockToolDriver(), CalculatorToolDriver()]


def _build_filesystem_driver(
    connection: FilesystemConnection,
) -> FilesystemDriver:
    """Wire the upstream filesystem driver over a Cephix connection.

    Uses ``mcs-driver-filesystem``'s ``_tooldriver=`` seam to inject
    a pre-built :class:`FilesystemToolDriver` whose ``_adapter`` is
    our :class:`MCSFilesystemAdapter`. The hybrid driver
    (``DriverBase`` = ``MCSDriver + MCSToolDriver``) is returned
    intact so a future ChatKernel can also reach its
    ``process_llm_response`` half by ``isinstance``-filtering the
    layer's driver list.
    """
    adapter = MCSFilesystemAdapter(connection=connection)
    tool_driver = FilesystemToolDriver(_adapter=adapter)
    return FilesystemDriver(_tooldriver=tool_driver)


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
        filesystem_connection: FilesystemConnection | None = None,
    ) -> None:
        drivers = (
            list(tool_drivers)
            if tool_drivers is not None
            else _default_tool_drivers()
        )
        if filesystem_connection is not None:
            drivers.append(
                _build_filesystem_driver(filesystem_connection)
            )
        for index, driver in enumerate(drivers):
            if not isinstance(driver, MCSToolDriver):
                raise TypeError(
                    f"MCSToolExecutionLayer.tool_drivers[{index}] must "
                    f"be an MCSToolDriver, got {type(driver).__name__}"
                )
        self._drivers: list[MCSToolDriver] = drivers
        self._filesystem_connection = filesystem_connection
        self._tool_index: dict[str, tuple[MCSToolDriver, Tool]] = {}
        self._rebuild_index()
        self._bus: BusPort | None = None
        self._invoke_subscription: Subscription | None = None
        # Per-tool ``command.request.<name>`` subscriptions, so a UI
        # (the CLI client, a future web panel) can fire a tool the
        # same way it fires a chat command. Built lazily in
        # :meth:`start` because subscription needs a live bus.
        self._command_subscriptions: list[Subscription] = []

    # ---- Capability surface (provides_tools) -------------------------------

    def component_info(self) -> ComponentInfo:
        """Surface registered MCS tools under ``metadata.provides_tools``.

        Overrides :meth:`BusComponent.component_info` so the layer's
        ``ComponentLifecycle`` snapshots carry one manifest entry per
        registered Tool. The :class:`CapabilityCollector` reads them
        into the :attr:`HarnessCapabilities.tools` slot (next to the
        existing ``commands`` aggregate from class-level
        ``provides_commands``), keeping LLM-callable tools separate
        from UI slash-commands in the wire vocabulary.

        UI-side slash aliases for tools (``/time`` for
        ``current_time``, ``/calc`` for ``calculate``) live in the
        CLI catalog -- the tool layer subscribes a
        ``command.request.<tool_name>`` topic for each tool so any
        UI that builds a request frame from the manifest reaches
        the same execute path the LLM hits via ``tool.invoke``.

        Driver-supplied catalogues change at construction time
        (different driver mix per robot), so they cannot live on the
        class-level :attr:`provides_commands` attribute and must be
        computed per instance.
        """
        info = super().component_info()
        tools = [
            self._tool_to_manifest_entry(tool)
            for _, tool in sorted(
                self._tool_index.values(), key=lambda pair: pair[1].name
            )
        ]
        if tools:
            metadata = dict(info.metadata)
            metadata["provides_tools"] = tools
            return ComponentInfo(
                category=info.category,
                name=info.name,
                description=info.description,
                metadata=metadata,
            )
        return info

    def _tool_to_manifest_entry(self, tool: Tool) -> dict[str, Any]:
        """Translate an MCS :class:`Tool` to a capability manifest dict.

        Field map mirrors :meth:`CommandSpec.manifest_entry`:

        - ``action`` <- ``tool.name`` (already namespaced like
          ``mailbox.fetch_unread`` / ``list_directory``).
        - ``label`` <- ``tool.title or tool.name``.
        - ``description`` <- ``tool.description or tool.title or
          tool.name``.
        - ``args_schema`` <- one entry per :class:`ToolParameter`,
          where the value is the parameter's JSON-schema-ish dict
          if present, else ``{"type": "string"}``.
        - ``risk_class`` <- heuristic on the tool's verb prefix:
          ``read_/list_/fetch_/get_/exists`` are ``read_only``,
          ``write_/set_/delete_/remove_/update_`` are
          ``low_risk_mutation``. Anything else defaults to
          ``read_only``. Per-tool override lands when a real
          mutation-gating layer needs it.
        """
        args_schema: dict[str, Any] = {}
        for param in tool.parameters or ():
            args_schema[param.name] = (
                dict(param.schema) if param.schema else {"type": "string"}
            )
        return {
            "action": tool.name,
            "label": tool.title or tool.name,
            "description": tool.description or tool.title or tool.name,
            "args_schema": args_schema,
            "risk_class": _risk_class_for(tool.name),
            "discriminator": None,
            "ui_hints": {},
            "owner_component": self.component_name,
            "owner_instance_id": self.instance_id,
        }

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
        # Also expose each tool as a UI-callable command: a request
        # frame on ``command.request.<tool_name>`` arrives via
        # ``_handle_command_request`` and travels the same execute
        # path the bus-internal ``tool.invoke`` flow uses. The CLI
        # / UI sees the tool in the capability manifest's ``tools``
        # slot and can build a request frame straight from it.
        for name in sorted(self._tool_index):
            sub = bus.subscribe(
                command_request_topic(name), self._handle_command_request
            )
            self._command_subscriptions.append(sub)
        names = sorted(self._tool_index)
        logger.info(
            "%s (%s) ready with %d driver(s), %d tool(s) on %s "
            "(also on command.request.<name>): %s",
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
        for sub in self._command_subscriptions:
            try:
                await sub.unsubscribe()
            except Exception:
                logger.exception(
                    "%s: failed to unsubscribe a command-request topic",
                    type(self).__name__,
                )
        self._command_subscriptions = []
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

    # ---- Command-request bridge (UI / CLI path) ----------------------------

    async def _handle_command_request(self, event: RobotEvent) -> None:
        """Subscriber for ``command.request.<tool_name>`` topics.

        The CLI / UI builds a :class:`CommandRequest` straight from a
        tool entry in the manifest. We accept it, route it through
        the same ``_run`` path the bus-internal ``tool.invoke`` flow
        uses, and reply with a :class:`CommandResponse` on the
        matching ``command.response.<action>`` topic -- which is
        exactly what the WebSocket channel forwards back to the CLI
        as a ``command_response`` frame.
        """
        if not isinstance(event, CommandRequest):
            return
        if self._bus is None:
            return

        tool_name = event.action
        arguments = dict(event.payload or {})

        if tool_name not in self._tool_index:
            await self._reply_command_error(
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
                "%s: command dispatch for tool %r raised",
                type(self).__name__,
                tool_name,
            )
            await self._reply_command_error(
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
            await self._reply_command_ok(event, payload=payload)
        else:
            await self._reply_command_error(
                event,
                code="tool.execution_failed",
                message=result.error or "tool reported failure",
                details={"tool": tool_name},
            )

    async def _reply_command_ok(
        self, request: CommandRequest, *, payload: dict[str, Any]
    ) -> None:
        assert self._bus is not None
        await self._bus.publish(
            CommandResponse(
                event_id=_new_event_id(),
                topic=command_response_topic(request.action, request.target),
                principal=request.principal,
                source=self.component_name,
                source_id=self.instance_id,
                run_id=request.run_id,
                correlation_id=request.correlation_id,
                timestamp=_now_iso(),
                status="ok",
                action=request.action,
                target=request.target,
                payload=payload,
            )
        )

    async def _reply_command_error(
        self,
        request: CommandRequest,
        *,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        assert self._bus is not None
        await self._bus.publish(
            CommandResponse(
                event_id=_new_event_id(),
                topic=command_response_topic(request.action, request.target),
                principal=request.principal,
                source=self.component_name,
                source_id=self.instance_id,
                run_id=request.run_id,
                correlation_id=request.correlation_id,
                timestamp=_now_iso(),
                status="error",
                action=request.action,
                target=request.target,
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
