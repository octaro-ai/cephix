from __future__ import annotations

from typing import Protocol

from src.notebooks.models import NotebookEntry, NotebookType


class NotebookStorePort(Protocol):
    def append(self, entry: NotebookEntry) -> None:
        ...

    def load(
        self,
        notebook_type: NotebookType,
        scope_id: str,
        *,
        limit: int = 50,
    ) -> list[NotebookEntry]:
        ...

    def load_by_principal(
        self,
        notebook_type: NotebookType,
        principal_id: str,
        *,
        scope_id: str | None = None,
        limit: int = 50,
    ) -> list[NotebookEntry]:
        ...
