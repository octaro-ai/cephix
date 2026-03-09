from __future__ import annotations

from typing import Any, Protocol

from src.domain import ExecutionContext
from src.tools.models import ToolDefinition


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
