from __future__ import annotations

from typing import Any

from src.domain import ExecutionContext, OutboundMessage, RobotEvent
from src.governance.models import GuardDecision
from src.governance.ports import InputGuardPort, OutputGuardPort, ToolExecutionGuardPort


class CompositeInputGuard:
    """Iterates through a list of input guards. First deny stops the chain."""

    def __init__(self, guards: list[InputGuardPort] | None = None) -> None:
        self._guards: list[InputGuardPort] = guards or []

    def check(self, event: RobotEvent) -> GuardDecision:
        for guard in self._guards:
            decision = guard.check(event)
            if not decision.allowed:
                return decision
        return GuardDecision.allow()


class CompositeToolExecutionGuard:
    """Iterates through a list of tool-execution guards. First deny stops the chain."""

    def __init__(self, guards: list[ToolExecutionGuardPort] | None = None) -> None:
        self._guards: list[ToolExecutionGuardPort] = guards or []

    def check(self, ctx: ExecutionContext, tool_name: str, arguments: dict[str, Any]) -> GuardDecision:
        for guard in self._guards:
            decision = guard.check(ctx, tool_name, arguments)
            if not decision.allowed:
                return decision
        return GuardDecision.allow()


class CompositeOutputGuard:
    """Iterates through a list of output guards. First deny stops the chain."""

    def __init__(self, guards: list[OutputGuardPort] | None = None) -> None:
        self._guards: list[OutputGuardPort] = guards or []

    def check(self, ctx: ExecutionContext, message: OutboundMessage) -> GuardDecision:
        for guard in self._guards:
            decision = guard.check(ctx, message)
            if not decision.allowed:
                return decision
        return GuardDecision.allow()
