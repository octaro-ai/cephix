"""Persistent memory store backed by file stores.

Replaces InMemoryMemoryStore in the runtime path.  Facts are written to
a FileProfileStore (JSON on disk) and interactions are written to a
FileEpisodeStore (JSONL on disk).  Both survive process restarts.

Facts are keyed by user_id (long-term memory).
Interactions are keyed by conversation_id (session memory).
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from src.domain import InteractionRecord, MemoryFact
from src.memory.compaction import CompactionStrategy, NullCompactor
from src.memory.file_stores import FileProfileStore
from src.memory.models import ProfileFactRecord
from src.utils import new_id, utc_now_iso

import json


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


class PersistentMemoryStore:
    """File-backed memory store that satisfies MemoryPort.

    Layout on disk::

        data_dir/
          profiles.json        -- all profile facts (JSON array)
          conversations/
            {conversation_id}.jsonl  -- interaction records per session
    """

    def __init__(
        self,
        data_dir: str | Path,
        *,
        compactor: CompactionStrategy | None = None,
        compaction_threshold: int = 10,
        recent_window: int = 5,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._profile_store = FileProfileStore(self._data_dir / "profiles.json")
        self._conversations_dir = self._data_dir / "conversations"
        self._compactor = compactor or NullCompactor()
        self._compaction_threshold = compaction_threshold
        self._recent_window = recent_window

    # -- MemoryPort.build_context -------------------------------------------

    def build_context(self, user_id: str, conversation_id: str | None) -> dict[str, Any]:
        all_facts = self._profile_store.list_facts(subject_id=user_id)
        facts = all_facts[-10:]

        conv_key = conversation_id or f"user:{user_id}"
        all_interactions = self._read_interactions(conv_key)
        recent = all_interactions[-self._recent_window:]

        # Compact older interactions into a summary when threshold is exceeded.
        summary = ""
        if len(all_interactions) > self._compaction_threshold:
            older = all_interactions[: -self._recent_window]
            summary = self._load_or_build_summary(conv_key, older)

        return {
            "facts": [
                {"kind": f.kind, "content": f.content, "score": f.confidence, "updated_at": f.updated_at}
                for f in facts
            ],
            "recent_interactions": [asdict(i) for i in recent],
            "conversation_summary": summary,
            "core_memory": self.get_core_memory(user_id),
        }

    # -- MemoryPort.remember_interaction ------------------------------------

    def remember_interaction(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        user_text: str,
        robot_text: str,
    ) -> None:
        conv_key = conversation_id or f"user:{user_id}"
        record = InteractionRecord(user_text=user_text, robot_text=robot_text)
        path = self._conversation_path(conv_key)
        _ensure_parent(path)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")

    # -- MemoryPort.remember_fact -------------------------------------------

    def remember_fact(self, user_id: str, kind: str, content: str, score: float = 1.0) -> None:
        existing = self._profile_store.list_facts(subject_id=user_id)

        for fact in existing:
            if fact.kind == kind and fact.content == content:
                fact.confidence = max(fact.confidence, score)
                fact.updated_at = utc_now_iso()
                self._profile_store.upsert(fact)
                return

        self._profile_store.upsert(ProfileFactRecord(
            fact_id=new_id("fact"),
            subject_id=user_id,
            kind=kind,
            content=content,
            confidence=score,
        ))

    # -- Helpers ------------------------------------------------------------

    def _conversation_path(self, conv_key: str) -> Path:
        safe_name = conv_key.replace("/", "_").replace("\\", "_").replace(":", "_")
        return self._conversations_dir / f"{safe_name}.jsonl"

    def _read_interactions(self, conv_key: str) -> list[InteractionRecord]:
        path = self._conversation_path(conv_key)
        if not path.exists():
            return []

        interactions: list[InteractionRecord] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            interactions.append(InteractionRecord(**data))
        return interactions

    def _summary_path(self, conv_key: str) -> Path:
        safe_name = conv_key.replace("/", "_").replace("\\", "_").replace(":", "_")
        return self._conversations_dir / f"{safe_name}.summary"

    def _load_or_build_summary(self, conv_key: str, older: list[InteractionRecord]) -> str:
        path = self._summary_path(conv_key)
        # Rebuild summary when the interaction count changed since last build.
        expected_count = len(older)
        if path.exists():
            stored = json.loads(path.read_text(encoding="utf-8"))
            if stored.get("interaction_count") == expected_count:
                return stored["summary"]

        summary = self._compactor.compact(older)
        _ensure_parent(path)
        path.write_text(
            json.dumps({"interaction_count": expected_count, "summary": summary}, ensure_ascii=False),
            encoding="utf-8",
        )
        return summary

    def _core_memory_path(self, user_id: str) -> Path:
        safe_name = user_id.replace("/", "_").replace("\\", "_").replace(":", "_")
        return self._data_dir / "core_memories" / f"{safe_name}.md"

    def get_core_memory(self, user_id: str) -> str:
        path = self._core_memory_path(user_id)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def set_core_memory(self, user_id: str, content: str) -> None:
        path = self._core_memory_path(user_id)
        _ensure_parent(path)
        path.write_text(content, encoding="utf-8")

    def list_conversations(self) -> list[str]:
        """List all known conversation IDs (for session switching)."""
        if not self._conversations_dir.exists():
            return []
        return sorted(
            p.stem for p in self._conversations_dir.glob("*.jsonl")
        )
