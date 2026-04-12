from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.notebooks.models import NotebookEntry, NotebookEntryKind, NotebookType

logger = logging.getLogger(__name__)


class FileNotebookStore:
    """File-based notebook store using JSON-Lines per notebook scope.

    Directory layout::

        <base_dir>/
            audit/<scope_id>.jsonl
            user/<scope_id>.jsonl
            user_task/<scope_id>.jsonl
            artifact/<scope_id>.jsonl
    """

    def __init__(self, base_dir: str | Path) -> None:
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)

    def append(self, entry: NotebookEntry) -> None:
        notebook_dir = self._base / entry.notebook_type.value
        notebook_dir.mkdir(parents=True, exist_ok=True)
        path = notebook_dir / f"{entry.scope_id}.jsonl"
        record = {
            "entry_id": entry.entry_id,
            "notebook_type": entry.notebook_type.value,
            "scope_type": entry.scope_type,
            "scope_id": entry.scope_id,
            "principal_id": entry.principal_id,
            "actor_id": entry.actor_id,
            "related_sop": entry.related_sop,
            "kind": entry.kind.value,
            "content": entry.content,
            "confidence": entry.confidence,
            "created_at": entry.created_at,
            "source_run_id": entry.source_run_id,
            "suggested_promotion": entry.suggested_promotion,
            "metadata": entry.metadata,
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def load(
        self,
        notebook_type: NotebookType,
        scope_id: str,
        *,
        limit: int = 50,
    ) -> list[NotebookEntry]:
        path = self._base / notebook_type.value / f"{scope_id}.jsonl"
        return self._read_entries(path, limit=limit)

    def load_by_principal(
        self,
        notebook_type: NotebookType,
        principal_id: str,
        *,
        scope_id: str | None = None,
        limit: int = 50,
    ) -> list[NotebookEntry]:
        notebook_dir = self._base / notebook_type.value
        if not notebook_dir.exists():
            return []

        entries: list[NotebookEntry] = []
        patterns = [f"{scope_id}.jsonl"] if scope_id else ["*.jsonl"]
        for pattern in patterns:
            for path in notebook_dir.glob(pattern):
                for entry in self._read_entries(path, limit=0):
                    if entry.principal_id == principal_id:
                        entries.append(entry)

        entries.sort(key=lambda e: e.created_at, reverse=True)
        return entries[:limit] if limit > 0 else entries

    @staticmethod
    def _read_entries(path: Path, *, limit: int = 50) -> list[NotebookEntry]:
        if not path.exists():
            return []
        entries: list[NotebookEntry] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                entries.append(NotebookEntry(
                    entry_id=data["entry_id"],
                    notebook_type=NotebookType(data["notebook_type"]),
                    scope_type=data.get("scope_type", ""),
                    scope_id=data.get("scope_id", ""),
                    principal_id=data.get("principal_id", ""),
                    actor_id=data.get("actor_id", ""),
                    related_sop=data.get("related_sop"),
                    kind=NotebookEntryKind(data["kind"]),
                    content=data["content"],
                    confidence=float(data.get("confidence", 1.0)),
                    created_at=data.get("created_at", ""),
                    source_run_id=data.get("source_run_id"),
                    suggested_promotion=data.get("suggested_promotion"),
                    metadata=data.get("metadata", {}),
                ))
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                logger.warning("Skipping malformed notebook entry: %s", exc)

        if limit > 0:
            entries = entries[-limit:]
        return entries
