from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.utils import utc_now_iso


class ActorRole(str, Enum):
    PRINCIPAL = "principal"
    DELEGATE = "delegate"
    COUNTERPARTY = "counterparty"
    OPERATOR = "operator"
    SYSTEM = "system"


@dataclass
class ActorContext:
    actor_id: str
    actor_role: ActorRole
    principal_id: str
    approval_authority_id: str


class RiskClass(str, Enum):
    READ_ONLY = "read_only"
    LOW_RISK_MUTATION = "low_risk_mutation"
    HIGH_RISK_MUTATION = "high_risk_mutation"


class ApprovalScope(str, Enum):
    ONCE = "once"
    SESSION = "session"
    SCOPED = "scoped"
    PERSISTENT = "persistent"
    DENY = "deny"


@dataclass
class ApprovalRule:
    principal_id: str
    action: str
    source_scope: str | None
    target_scope: str | None
    scope: ApprovalScope
    granted_by: str
    granted_at: str = field(default_factory=utc_now_iso)
    expires_at: str | None = None
    sop_name: str | None = None


@dataclass
class ApprovalRequest:
    """Intermediate object representing a pending approval request."""
    request_id: str
    run_id: str
    action: str
    action_context: dict[str, Any] = field(default_factory=dict)
    principal_id: str = ""
    created_at: str = field(default_factory=utc_now_iso)
