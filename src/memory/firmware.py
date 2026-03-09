from __future__ import annotations

from pathlib import Path


class FirmwareLoader:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def load_documents(self) -> dict[str, str]:
        documents: dict[str, str] = {}
        if not self.root.exists():
            return documents

        for path in sorted(self.root.rglob("*.md")):
            documents[path.relative_to(self.root).as_posix()] = path.read_text(encoding="utf-8")
        return documents
