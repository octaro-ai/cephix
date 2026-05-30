"""Port for the tool execution layer.

The tool execution layer is the IO surface for LLM-driven tool calls
and for direct, code-initiated tool invocations (heartbeats, control
plane, future schedulers). It abstracts what an underlying engine
(initially MCS -- the Model Context Standard -- but a second
implementation is explicitly anticipated) does into three
operations:

- :meth:`list_tools` -- what can be invoked? Returns descriptors
  with name, title, description, and a JSON-schema-ish parameter
  shape. These end up as ``provides_commands`` so the
  CapabilityCollector exposes them on the bus.

- :meth:`invoke_tool` -- direct call by name with structured
  arguments. Used by code paths that already know what they want
  (a HeartbeatChannel polling for new mail, a future control-plane
  action). No LLM involved, no parsing, no schema healing.

- :meth:`process_llm_output` (later) -- feed an LLM response
  through the layer; the layer detects tool calls, executes them
  via the same internal path as :meth:`invoke_tool`, and reports
  back what happened. The agent never needs to know the
  serialization format.

This port intentionally hides the engine: a consumer holds a
``ToolExecutionLayerPort`` reference, never an ``MCSDriver`` or
``MCSOrchestrator``. That lets us add a second layer (different
prompt strategy, different extraction format, in-house tools)
without touching consumers. Configuration cardinality is "at
least one layer instance, possibly several" -- the builder picks
which one a consumer gets via Convention-DI.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolDescriptor:
    """Catalog entry for a tool the layer can invoke.

    Mirrors the shape MCS uses for ``Tool`` so a future MCS-backed
    implementation can produce these without translation. Kept as
    plain dataclass (not pydantic) because the layer port should
    not pull pydantic into every consumer.
    """

    name: str
    """Machine identifier -- stable, lowercase, dotted, e.g. ``mailbox.fetch_unread``."""

    title: str
    """One-line UI label."""

    description: str
    """Free-text explanation. Fed to LLMs verbatim in the tool list."""

    parameters: dict[str, Any] = field(default_factory=dict)
    """JSON-schema-shaped parameter spec. Empty dict = no arguments."""


@dataclass(frozen=True)
class ToolInvocationResult:
    """Outcome of a single tool invocation.

    ``success`` means the tool ran to completion and produced a
    payload in ``result``. ``success=False`` means structurally
    valid call, failed execution (network error, parse error,
    auth challenge, ...); ``error`` carries the human description.

    A *structurally invalid* call (unknown tool name, missing
    required argument) raises rather than returning a result --
    that is a programming error, not a runtime failure.
    """

    name: str
    success: bool
    result: Any = None
    error: str | None = None


class ToolExecutionLayerPort(ABC):
    """The cephix-side abstraction over an LLM tool-execution engine."""

    @abstractmethod
    def list_tools(self) -> Sequence[ToolDescriptor]:
        """Return the currently registered tools.

        Synchronous because consumers may want it at construction
        time (Convention-DI builder, ``provides_commands`` lookup).
        Implementations should return a stable, sorted view.
        """

    @abstractmethod
    async def invoke_tool(
        self, name: str, arguments: Mapping[str, Any]
    ) -> ToolInvocationResult:
        """Invoke a tool by name with structured arguments.

        Direct path: no LLM, no parsing, no prompt. Used by code
        paths that already know what they want -- a Heartbeat
        polling for mail, a control-plane action, an integration
        test. Equivalent to MCS's ``driver.execute_tool(name, args)``
        in terms of side effects; the result format is normalised
        through :class:`ToolInvocationResult` so consumers don't
        depend on engine internals.

        Raises ``KeyError`` if ``name`` is not in :meth:`list_tools`.
        """
