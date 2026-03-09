from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.domain import ExecutionContext
from src.governance.models import GuardDecision
from src.governance.ports import ToolExecutionGuardPort
from src.tools.models import ToolDefinition
from src.tools.ports import ToolRegistryPort


ToolHandler = Callable[[ExecutionContext, dict[str, Any]], Any]


class GovernedToolExecutor:
    """Executes mounted tools while enforcing governance guards.

    Holds a mapping from tool names to handler callables.  Before every
    execution the ``ToolExecutionGuardPort`` is consulted.
    """

    def __init__(
        self,
        *,
        registry: ToolRegistryPort,
        guard: ToolExecutionGuardPort,
        handlers: dict[str, ToolHandler] | None = None,
    ) -> None:
        self._registry = registry
        self._guard = guard
        self._handlers: dict[str, ToolHandler] = handlers or {}

    def register_handler(self, tool_name: str, handler: ToolHandler) -> None:
        self._handlers[tool_name] = handler

    def execute(self, ctx: ExecutionContext, tool_name: str, arguments: dict[str, Any]) -> Any:
        mounted_names = {d.name for d in self._registry.list_mounted()}
        if tool_name not in mounted_names:
            raise RuntimeError(f"Tool not mounted: {tool_name!r}")

        decision: GuardDecision = self._guard.check(ctx, tool_name, arguments)
        if not decision.allowed:
            raise PermissionError(
                f"Tool execution denied by {decision.guard_name}: {decision.reason}"
            )

        handler = self._handlers.get(tool_name)
        if handler is None:
            raise RuntimeError(f"No handler registered for tool: {tool_name!r}")
        return handler(ctx, arguments)
