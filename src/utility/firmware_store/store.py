"""``FilesystemFirmwareStore`` -- firmware store on top of a FilesystemConnection.

Reads firmware documents from ``<connection-root>/<directory>/``
(default ``firmware/``) and exposes them through
:class:`~src.utility.firmware_store.ports.FirmwareStorePort`. Any
file matching one of the configured glob patterns counts as a
document; we deliberately do **not** ship an exclude list -- whatever
the operator drops into the folder becomes part of the system prompt.

System-prompt assembly: each non-empty document contributes a
``## <NAME>`` section, with sections joined by blank lines. The
document name is the file stem (filename without the matched
extension); the packaged starter set uses upper-case stems
(``HARNESS.md``, ``RUNTIME.md``, ...) and the rendering preserves
whatever the operator chose.

Lifecycle: :meth:`start` does the initial read so the kernel can
synchronously call :meth:`system_prompt` and :meth:`documents`
immediately afterwards (both read the in-memory cache). The async
:meth:`refresh` re-reads on demand; intended for a future runtime
``firmware.reload`` command.

DI: the store takes a :class:`FilesystemConnection` rather than a
raw path, so it shares the adapter chain with the persistence
provider and the session store. The ``directory`` field selects
its bucket inside the shared connection root, mirroring the same
pattern.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from pathlib import PurePath

from src.components import ComponentCategory, RobotComponent
from src.persistence.filesystem.connection import FilesystemConnection
from src.utility.firmware_store.ports import FirmwareStorePort

logger = logging.getLogger(__name__)

_DEFAULT_DIRECTORY = "firmware"
_DEFAULT_PATTERNS: tuple[str, ...] = ("*.md",)


class FilesystemFirmwareStore(RobotComponent, FirmwareStorePort):
    """Firmware store backed by a directory of text files.

    Constructor wiring (DI):

    - ``connection`` -- :class:`FilesystemConnection` (level 1).
      Routes the read IO through the same adapter stack the
      persistence provider and the session store use.
    - ``directory`` -- the store's bucket inside the connection
      root, default ``firmware/``.
    - ``patterns`` -- filename globs that count as documents,
      default ``("*.md",)``. The first matching suffix is stripped
      off to derive the document name.

    The store reads documents eagerly on :meth:`start` so consumers
    can call :meth:`system_prompt` without coroutine plumbing. A
    missing directory is tolerated -- the documents map is simply
    empty and ``system_prompt()`` returns an empty string, letting
    the kernel still function (the LLM just gets no system prompt).
    """

    component_name = "firmware-store"
    component_category = ComponentCategory.UTILITY
    component_description = (
        "Firmware store. Reads filename-glob-matched documents from "
        "the configured directory under the FilesystemConnection root "
        "(default <root>/firmware/, patterns *.md) and assembles them "
        "into a single system prompt. Off-bus utility consumed by "
        "chat-style kernels at planning time."
    )

    def __init__(
        self,
        *,
        connection: FilesystemConnection,
        directory: str = _DEFAULT_DIRECTORY,
        patterns: Sequence[str] = _DEFAULT_PATTERNS,
    ) -> None:
        if not isinstance(connection, FilesystemConnection):
            raise TypeError(
                "FilesystemFirmwareStore.connection must be a "
                "FilesystemConnection, got "
                f"{type(connection).__name__}"
            )
        if not isinstance(directory, str):
            raise TypeError(
                "FilesystemFirmwareStore.directory must be a string"
            )
        self._fs = connection
        self._directory = directory.strip("/").strip("\\")
        self._patterns: tuple[str, ...] = tuple(patterns) or _DEFAULT_PATTERNS
        self._documents: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Surface the connection -> store wiring, then load documents."""
        connection_id = getattr(self._fs, "instance_id", "")
        logger.info(
            "%s (%s) injected into %s (%s)",
            type(self._fs).__name__,
            connection_id,
            type(self).__name__,
            self.instance_id,
        )
        await self.refresh()

    async def _stop(self) -> None:
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

    async def refresh(self) -> None:
        """Re-read every matching document from the underlying store."""
        documents: dict[str, str] = {}
        directory = PurePath(self._directory) if self._directory else PurePath()
        try:
            names = await self._fs.listdir(directory)
        except Exception:
            logger.warning(
                "FilesystemFirmwareStore: failed to list %s; documents "
                "will be empty",
                directory or "<root>",
                exc_info=True,
            )
            self._documents = documents
            return

        for name in sorted(names):
            stem = self._match(name)
            if stem is None:
                continue
            rel_path = directory / name if self._directory else PurePath(name)
            try:
                text = await self._fs.read_text(rel_path)
            except FileNotFoundError:
                # File disappeared between listdir and read; tolerate.
                continue
            except Exception:
                logger.warning(
                    "FilesystemFirmwareStore: failed to read %s",
                    rel_path,
                    exc_info=True,
                )
                continue
            documents[stem] = text
        self._documents = documents

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _match(self, filename: str) -> str | None:
        """Return the document stem if ``filename`` matches any pattern.

        Uses :func:`fnmatch.fnmatch` for glob semantics and strips
        the longest matching glob-tail (e.g. ``*.md`` -> drop
        trailing ``.md``). When no pattern matches, returns ``None``
        so the file is skipped silently -- the operator may keep
        ``.txt`` notes next to the markdown without polluting the
        prompt.
        """
        import fnmatch

        path = PurePath(filename)
        for pattern in self._patterns:
            if fnmatch.fnmatchcase(filename, pattern):
                # Strip the suffix derived from the pattern's trailing
                # extension when present (e.g. "*.md" -> ".md").
                pat_path = PurePath(pattern)
                if pat_path.suffix and path.suffix == pat_path.suffix:
                    return path.stem
                return filename
        return None
