"""Telegram channel adapter.

Currently a local-dev stub that logs to stdout.  A future iteration will
use the Bot API (``python-telegram-bot`` or direct ``aiohttp`` calls) for
real inline-keyboard approval prompts and callback-query handling.
"""

from __future__ import annotations

import logging
from typing import Any

from src.domain import ApprovalPrompt, OutboundMessage, ReplyTarget, RobotEvent
from src.utils import new_id

logger = logging.getLogger(__name__)


class TelegramChannel:
    def __init__(self, *, channel_id: str = "telegram") -> None:
        self.channel_id = channel_id
        self._incoming_events: list[RobotEvent] = []

    def enqueue_event(self, event: RobotEvent) -> None:
        self._incoming_events.append(event)

    def drain_events(self) -> list[RobotEvent]:
        events = list(self._incoming_events)
        self._incoming_events.clear()
        return events

    def send(self, target: ReplyTarget, message: OutboundMessage) -> None:
        logger.info("[TG] -> %s: %s", target.recipient_id, message.text[:120])

    def send_chunk(self, target: ReplyTarget, token: str) -> None:
        pass

    def send_chunk_clear(self, target: ReplyTarget) -> None:
        pass

    def send_approval_prompt(self, target: ReplyTarget, prompt: ApprovalPrompt) -> None:
        """Log the approval prompt.  Real implementation would send an InlineKeyboard."""
        ctx = prompt.action_context
        buttons_repr = " | ".join(f"[{b.label}]" for b in prompt.buttons)
        logger.info(
            "[TG] APPROVAL -> %s | %s: %s %s  Buttons: %s",
            target.recipient_id,
            ctx.get("action", "?"),
            ctx.get("source", ""),
            ctx.get("target", ""),
            buttons_repr,
        )

    def inject_approval_decision(
        self,
        *,
        sender_id: str,
        conversation_id: str,
        button_payload: dict[str, Any],
    ) -> None:
        """Simulate a Telegram callback-query (button press) for testing."""
        event = RobotEvent(
            event_id=new_id("evt"),
            event_type="approval.decision",
            source_channel=self.channel_id,
            sender_id=sender_id,
            conversation_id=conversation_id,
            payload=button_payload,
            reply_target=ReplyTarget(
                channel=self.channel_id,
                recipient_id=sender_id,
                conversation_id=conversation_id,
                mode="reply",
            ),
        )
        self._incoming_events.append(event)
