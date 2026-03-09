from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any, Protocol

from src.domain import ExecutionContext
from src.utils import new_id, utc_now_iso


@dataclass
class WideEvent:
    event_id: str
    event_type: str
    timestamp: str
    run_id: str
    trace_id: str
    robot_id: str
    conversation_id: str | None
    actor: str
    payload: dict[str, Any]


class EventSinkPort(Protocol):
    """Accepts a single wide event for storage or forwarding."""

    def append(self, event: WideEvent) -> None:
        ...


class EventLog:
    def __init__(self, path: str = "robot_events.jsonl") -> None:
        self.path = path

    def append(self, event: WideEvent) -> None:
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")


class FanoutEventSink:
    def __init__(self, sinks: list[EventSinkPort] | None = None) -> None:
        self.sinks: list[EventSinkPort] = sinks or []

    def append(self, event: WideEvent) -> None:
        for sink in self.sinks:
            sink.append(event)


class Telemetry:
    def __init__(self, sink: EventSinkPort) -> None:
        self.sink = sink

    def emit(
        self,
        *,
        ctx: ExecutionContext,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
    ) -> None:
        event = WideEvent(
            event_id=new_id("evt"),
            event_type=event_type,
            timestamp=utc_now_iso(),
            run_id=ctx.run_id,
            trace_id=ctx.trace_id,
            robot_id=ctx.robot_id,
            conversation_id=ctx.conversation_id,
            actor=actor,
            payload=payload,
        )
        self.sink.append(event)
