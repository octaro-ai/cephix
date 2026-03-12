from __future__ import annotations

from typing import Any

from src.domain import ExecutionContext
from src.governance.models import GuardDecision
from src.governance.ports import ToolExecutionGuardPort
from src.tools.collector import ToolCollector
from src.tools.ports import ToolRegistryPort


class GovernedToolExecutor:
    """Executes mounted tools while enforcing governance guards.

    Delegates execution to the ``ToolCollector`` which routes to the
    appropriate ``ToolDriverPort``.  Before every execution the
    ``ToolExecutionGuardPort`` is consulted.
    """

    def __init__(
        self,
        *,
        registry: ToolRegistryPort,
        guard: ToolExecutionGuardPort,
        collector: ToolCollector,
    ) -> None:
        self._registry = registry
        self._guard = guard
        self._collector = collector

    def execute(self, ctx: ExecutionContext, tool_name: str, arguments: dict[str, Any]) -> Any:
        mounted_names = {d.name for d in self._registry.list_mounted()}
        if tool_name not in mounted_names:
            raise RuntimeError(f"Tool not mounted: {tool_name!r}")

        decision: GuardDecision = self._guard.check(ctx, tool_name, arguments)
        if not decision.allowed:
            raise PermissionError(
                f"Tool execution denied by {decision.guard_name}: {decision.reason}"
            )

        return self._collector.execute(ctx, tool_name, arguments)
