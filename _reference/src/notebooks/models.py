from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.utils import utc_now_iso


class NotebookType(str, Enum):
    USER_TASK = "user_task"
    SOP = "sop"


class NotebookEntryKind(str, Enum):
    OBSERVATION = "observation"
    DEVIATION = "deviation"
    RULE = "rule"
    DECISION = "decision"
    APPROVAL_LOG = "approval_log"
    FEEDBACK = "feedback"
    SUMMARY = "summary"


@dataclass
class NotebookEntry:
    entry_id: str
    notebook_type: NotebookType
    scope_type: str
    scope_id: str
    principal_id: str
    kind: NotebookEntryKind
    content: str
    actor_id: str = ""
    related_sop: str | None = None
    confidence: float = 1.0
    created_at: str = field(default_factory=utc_now_iso)
    source_run_id: str | None = None
    suggested_promotion: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
