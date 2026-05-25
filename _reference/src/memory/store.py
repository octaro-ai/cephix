from __future__ import annotations

from dataclasses import asdict
from typing import Any

from src.domain import InteractionRecord, MemoryFact
from src.memory.compaction import CompactionStrategy, NullCompactor
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

    def __init__(
        self,
        *,
        compactor: CompactionStrategy | None = None,
        compaction_threshold: int = 10,
        recent_window: int = 5,
    ) -> None:
        self._facts_by_user: dict[str, list[MemoryFact]] = {}
        self._episodes_by_conversation: dict[str, list[InteractionRecord]] = {}
        self._compactor = compactor or NullCompactor()
        self._compaction_threshold = compaction_threshold
        self._recent_window = recent_window
        self._core_memories: dict[str, str] = {}

    def build_context(self, user_id: str, conversation_id: str | None) -> dict[str, Any]:
        facts = self._facts_by_user.get(user_id, [])[-10:]
        key = self._conversation_key(user_id, conversation_id)
        all_episodes = self._episodes_by_conversation.get(key, [])
        recent = all_episodes[-self._recent_window:]

        summary = ""
        if len(all_episodes) > self._compaction_threshold:
            older = all_episodes[: -self._recent_window]
            summary = self._compactor.compact(older)

        return {
            "facts": [asdict(fact) for fact in facts],
            "recent_interactions": [asdict(episode) for episode in recent],
            "conversation_summary": summary,
            "core_memory": self._core_memories.get(user_id, ""),
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

    def get_core_memory(self, user_id: str) -> str:
        return self._core_memories.get(user_id, "")

    def set_core_memory(self, user_id: str, content: str) -> None:
        self._core_memories[user_id] = content

    def list_conversations(self) -> list[str]:
        """List all known conversation IDs."""
        return sorted(self._episodes_by_conversation.keys())

    @staticmethod
    def _conversation_key(user_id: str, conversation_id: str | None) -> str:
        return conversation_id or f"user:{user_id}"
