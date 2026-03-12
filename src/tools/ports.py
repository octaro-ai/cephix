from __future__ import annotations

from typing import Any, Protocol

from src.domain import ExecutionContext
from src.tools.models import ToolDefinition


# ---------------------------------------------------------------------------
# ToolDriverPort — the unified interface every tool source must implement
# ---------------------------------------------------------------------------


class ToolDriverPort(Protocol):
    """Unified source of tools — definitions + execution in one interface.

    Every tool provider (system tools, MCS adapters, domain tools, file-based
    packages) implements this protocol.  A ``ToolCollector`` aggregates
    multiple drivers into a single catalog + handler map.
    """

    def list_tools(self) -> list[ToolDefinition]:
        """Return all tool definitions this driver provides."""
        ...

    def execute(self, ctx: ExecutionContext, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Execute *tool_name* with the given arguments."""
        ...


# ---------------------------------------------------------------------------
# Catalog / Registry / Execution — downstream ports (unchanged contracts)
# ---------------------------------------------------------------------------


class ToolCatalogPort(Protocol):
    """What the robot KNOWS about (repository / warehouse)."""

    def list_available(self, *, tags: list[str] | None = None) -> list[ToolDefinition]:
        ...

    def get_definition(self, tool_name: str) -> ToolDefinition | None:
        ...


class ToolRegistryPort(Protocol):
    """What the robot currently has MOUNTED (runtime working set)."""

    def list_mounted(self) -> list[ToolDefinition]:
        ...

    def mount(self, tool_name: str) -> None:
        ...

    def unmount(self, tool_name: str) -> None:
        ...

    def get_schemas(self) -> list[dict[str, Any]]:
        ...


class ToolExecutionPort(Protocol):
    """Executes a mounted tool."""

    def execute(self, ctx: ExecutionContext, tool_name: str, arguments: dict[str, Any]) -> Any:
        ...
