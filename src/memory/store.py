from __future__ import annotations

from dataclasses import asdict
from typing import Any

from src.domain import InteractionRecord, MemoryFact
from src.utils import utc_now_iso


class InMemoryMemoryStore:
    """
    Simple prototype memory store.

    Long term this should likely become a layered memory system:
    - working memory
    - episodic memory
    - profile memory
    - procedural memory
    """

    def __init__(self) -> None:
        self._facts_by_user: dict[str, list[MemoryFact]] = {}
        self._episodes_by_conversation: dict[str, list[InteractionRecord]] = {}

    def build_context(self, user_id: str, conversation_id: str | None) -> dict[str, Any]:
        facts = self._facts_by_user.get(user_id, [])[-10:]
        episodes = self._episodes_by_conversation.get(self._conversation_key(user_id, conversation_id), [])[-5:]
        return {
            "facts": [asdict(fact) for fact in facts],
            "recent_interactions": [asdict(episode) for episode in episodes],
        }

    def remember_interaction(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        user_text: str,
        robot_text: str,
    ) -> None:
        self._episodes_by_conversation.setdefault(self._conversation_key(user_id, conversation_id), []).append(
            InteractionRecord(user_text=user_text, robot_text=robot_text)
        )

    def remember_fact(self, user_id: str, kind: str, content: str, score: float = 1.0) -> None:
        facts = self._facts_by_user.setdefault(user_id, [])
        for fact in facts:
            if fact.kind == kind and fact.content == content:
                fact.score = max(fact.score, score)
                fact.updated_at = utc_now_iso()
                return
        facts.append(MemoryFact(kind=kind, content=content, score=score))

    @staticmethod
    def _conversation_key(user_id: str, conversation_id: str | None) -> str:
        return conversation_id or f"user:{user_id}"
