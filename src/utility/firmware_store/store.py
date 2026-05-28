"""``MarkdownFirmwareStore`` -- the default firmware store.

Reads every ``*.md`` from ``firmware_dir`` (typically
``<workspace>/firmware/``) and exposes them through
:class:`~src.utility.firmware_store.ports.FirmwareStorePort`.
Whatever the operator drops into that folder becomes part of the
system prompt; we deliberately do **not** ship an exclude list
(``HEARTBEAT.md`` and friends are not part of the packaged
starter set, so the issue does not arise).

System-prompt assembly: each non-empty document contributes a
``## <NAME>`` section, with sections joined by blank lines. The
document name is the filename without the ``.md`` extension and
upper-case (matches the convention used in the packaged
templates).

Lifecycle: :meth:`start` does the initial read so the kernel can
synchronously call :meth:`system_prompt` immediately afterwards.
:meth:`refresh` re-reads on demand (intended for a future runtime
command).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

from src.components import ComponentCategory, RobotComponent
from src.utility.firmware_store.ports import FirmwareStorePort

logger = logging.getLogger(__name__)


class MarkdownFirmwareStore(RobotComponent, FirmwareStorePort):
    """Firmware store backed by Markdown files in a single directory.

    Constructor:

    - ``firmware_dir`` -- path of the firmware folder. The builder
      typically passes ``<workspace>/firmware/`` after seeding the
      packaged templates into it.

    The store reads documents eagerly on :meth:`start` so consumers
    can call :meth:`system_prompt` without coroutine plumbing. A
    missing directory is tolerated -- the documents map is simply
    empty and ``system_prompt()`` returns an empty string, letting
    the kernel still function (the LLM just gets no system prompt).
    """

    component_name = "firmware-store"
    component_category = ComponentCategory.UTILITY
    component_description = (
        "Markdown firmware store. Reads every *.md from "
        "<workspace>/firmware/ and assembles them into a single "
        "system prompt. Off-bus utility consumed by chat-style "
        "kernels at planning time."
    )

    def __init__(self, *, firmware_dir: str | Path) -> None:
        self._firmware_dir = Path(firmware_dir)
        self._documents: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Read every Markdown file into memory."""
        self.refresh()

    async def stop(self) -> None:
        """Drop the in-memory document cache."""
        self._documents = {}

    # ------------------------------------------------------------------
    # FirmwareStorePort
    # ------------------------------------------------------------------

    def documents(self) -> Mapping[str, str]:
        """Return the cached ``{name: content}`` mapping."""
        return dict(self._documents)

    def system_prompt(self) -> str:
        """Concatenate every non-empty document into a system prompt.

        Format: ``## <NAME>\\n<content>``, sections separated by a
        blank line. Empty / whitespace-only documents are skipped
        so a placeholder file does not pollute the prompt.
        """
        parts: list[str] = []
        for name, content in self._documents.items():
            stripped = content.strip()
            if not stripped:
                continue
            parts.append(f"## {name}\n{stripped}")
        return "\n\n".join(parts)

    def refresh(self) -> None:
        """Re-read every ``*.md`` from disk."""
        documents: dict[str, str] = {}
        if not self._firmware_dir.exists():
            logger.info(
                "MarkdownFirmwareStore: firmware_dir %s does not exist; "
                "documents will be empty",
                self._firmware_dir,
            )
            self._documents = documents
            return
        for path in sorted(self._firmware_dir.glob("*.md")):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning(
                    "MarkdownFirmwareStore: failed to read %s: %s",
                    path,
                    exc,
                )
                continue
            documents[path.stem] = text
        self._documents = documents
