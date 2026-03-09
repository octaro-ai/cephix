from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GuardDecision:
    allowed: bool
    reason: str | None = None
    guard_name: str | None = None

    @staticmethod
    def allow() -> GuardDecision:
        return GuardDecision(allowed=True)

    @staticmethod
    def deny(reason: str, guard_name: str) -> GuardDecision:
        return GuardDecision(allowed=False, reason=reason, guard_name=guard_name)
