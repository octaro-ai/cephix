from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.utils import utc_now_iso


@dataclass
class ReplyTarget:
    channel: str
    recipient_id: str
    conversation_id: str | None = None
    mode: str = "reply"


@dataclass
class DeliveryDirective:
    channel: str | None = None
    mode: str = "reply"
    reason: str | None = None


@dataclass
class RobotEvent:
    event_id: str
    event_type: str
    source_channel: str
    sender_id: str | None = None
    sender_name: str | None = None
    conversation_id: str | None = None
    text: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    reply_target: ReplyTarget | None = None
    available_targets: list[ReplyTarget] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=utc_now_iso)


@dataclass
class PlanningContext:
    firmware_documents: dict[str, str] = field(default_factory=dict)
    memory_documents: dict[str, str] = field(default_factory=dict)
    memory_context: dict[str, Any] = field(default_factory=dict)
    tool_schemas: list[dict[str, Any]] = field(default_factory=list)
    active_skills: list[Any] = field(default_factory=list)
    active_sops: list[Any] = field(default_factory=list)
    sop_current_node: Any = None


@dataclass
class ExecutionContext:
    run_id: str
    robot_id: str
    user_id: str
    conversation_id: str | None
    channel: str
    trace_id: str
    created_at: str = field(default_factory=utc_now_iso)


@dataclass
class MessageRecord:
    message_id: str
    sender: str
    subject: str
    body: str
    received_at: str
    unread: bool = True


@dataclass
class PlanStep:
    step_id: str
    kind: str
    reason: str
    tool_name: str | None = None
    tool_arguments: dict[str, Any] | None = None
    response_text: str | None = None
    delivery_directive: DeliveryDirective | None = None


@dataclass
class Plan:
    plan_id: str
    goal: str
    steps: list[PlanStep]


@dataclass
class OutboundMessage:
    text: str
    subject: str | None = None


@dataclass
class ControlRequest:
    request_id: str
    source_channel: str
    recipient_id: str
    request_type: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryFact:
    kind: str
    content: str
    score: float = 1.0
    updated_at: str = field(default_factory=utc_now_iso)


@dataclass
class InteractionRecord:
    user_text: str
    robot_text: str
    timestamp: str = field(default_factory=utc_now_iso)


class RobotState(str, Enum):
    IDLE = "IDLE"
    OBSERVING = "OBSERVING"
    PLANNING = "PLANNING"
    ACTING = "ACTING"
    FINALIZING = "FINALIZING"
    RESPONDING = "RESPONDING"
    DONE = "DONE"
    ERROR = "ERROR"
