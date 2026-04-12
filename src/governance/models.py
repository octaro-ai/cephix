from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.governance.domain import RiskClass


@dataclass
class GuardDecision:
    allowed: bool
    approval_required: bool = False
    reason: str | None = None
    guard_name: str | None = None
    risk_class: RiskClass | None = None
    action_context: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def allow() -> GuardDecision:
        return GuardDecision(allowed=True)

    @staticmethod
    def deny(reason: str, guard_name: str) -> GuardDecision:
        return GuardDecision(allowed=False, reason=reason, guard_name=guard_name)

    @staticmethod
    def require_approval(
        *,
        reason: str,
        guard_name: str,
        risk_class: RiskClass | None = None,
        action_context: dict[str, Any] | None = None,
    ) -> GuardDecision:
        return GuardDecision(
            allowed=False,
            approval_required=True,
            reason=reason,
            guard_name=guard_name,
            risk_class=risk_class,
            action_context=action_context or {},
        )
