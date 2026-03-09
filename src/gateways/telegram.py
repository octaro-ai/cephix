from __future__ import annotations

from src.domain import OutboundMessage, ReplyTarget, RobotEvent


class TelegramChannel:
    def __init__(self) -> None:
        self._incoming_events: list[RobotEvent] = []

    def enqueue_event(self, event: RobotEvent) -> None:
        self._incoming_events.append(event)

    def drain_events(self) -> list[RobotEvent]:
        events = list(self._incoming_events)
        self._incoming_events.clear()
        return events

    def send(self, target: ReplyTarget, message: OutboundMessage) -> None:
        print("\n--- TELEGRAM OUT ---")
        print(f"to={target.recipient_id}")
        print(message.text)
        print("--- /TELEGRAM OUT ---\n")
