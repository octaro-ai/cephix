from __future__ import annotations

from typing import Any, Protocol

from src.domain import ExecutionContext, OutboundMessage, RobotEvent
from src.governance.models import GuardDecision


class InputGuardPort(Protocol):
    def check(self, event: RobotEvent) -> GuardDecision:
        ...


class ToolExecutionGuardPort(Protocol):
    def check(self, ctx: ExecutionContext, tool_name: str, arguments: dict[str, Any]) -> GuardDecision:
        ...


class OutputGuardPort(Protocol):
    def check(self, ctx: ExecutionContext, message: OutboundMessage) -> GuardDecision:
        ...
