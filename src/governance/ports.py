from __future__ import annotations

from typing import Any, Protocol

from src.domain import ExecutionContext, OutboundMessage, RobotEvent
from src.governance.domain import ActorContext, ApprovalRule, ApprovalScope, RiskClass
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


class ActorResolverPort(Protocol):
    def resolve(self, event: RobotEvent) -> ActorContext:
        ...


class RiskClassifierPort(Protocol):
    def classify(self, tool_name: str) -> RiskClass:
        ...


class ApprovalStorePort(Protocol):
    def check(
        self,
        principal_id: str,
        action: str,
        source_scope: str | None = None,
        target_scope: str | None = None,
    ) -> ApprovalRule | None:
        ...

    def grant(self, rule: ApprovalRule) -> None:
        ...

    def revoke(
        self,
        principal_id: str,
        action: str,
        source_scope: str | None = None,
        target_scope: str | None = None,
    ) -> bool:
        ...
