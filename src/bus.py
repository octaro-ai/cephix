from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class BusMessage:
    msg_type: str
    name: str
    payload: dict[str, Any]


class SemanticBus:
    def __init__(self) -> None:
        self.messages: list[BusMessage] = []

    def publish(self, msg_type: str, name: str, payload: dict[str, Any]) -> None:
        self.messages.append(BusMessage(msg_type=msg_type, name=name, payload=payload))
