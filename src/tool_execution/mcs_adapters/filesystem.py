"""``MCSFilesystemAdapter`` -- Cephix-side filesystem adapter for MCS.

Implements ``mcs.driver.filesystem.FilesystemPort`` structurally
(duck typing -- no inheritance, no import of the port type at
runtime) and routes every operation against a Cephix
:class:`FilesystemConnection`. The builder roots that connection
at the robot's ``<robot_home>/workspace/`` sandbox, so MCS tool
calls are confined to the robot's working files and cannot reach
its machinery (telemetry, audit, sessions, firmware, configs,
secrets), which live elsewhere under the robot home. The
connection's traversal guard keeps calls inside that root.

Method shapes (return JSON strings for list/read/write, ``bool``
for ``exists``, ``list[str]`` for ``list_files``) match the
contract the upstream ``FilesystemToolDriver`` expects -- see
``mcs-adapter-localfs/localfs_adapter.py`` for the reference
implementation. The wrapping ``FilesystemToolDriver`` returns
these strings verbatim from ``execute_tool``; the cephix
:class:`MCSToolExecutionLayer` then wraps non-dict results into
``{"result": <value>}`` for the bus response.

Synchronous on purpose: MCS's ``MCSToolDriver.execute_tool`` is
sync, and the cephix tool layer already hops to a worker thread
via ``asyncio.to_thread`` before dispatching. Inside that worker
there is no running event loop, so the adapter cannot ``await``
the async :class:`FilesystemConnection` methods. We reach the
underlying filesystem with :mod:`pathlib` directly, anchored at
``connection.root`` and pre-validated through
``connection.resolve()`` -- the connection's traversal-guard
method is itself synchronous.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path, PurePath
from typing import Any

from src.persistence.filesystem.connection import FilesystemConnection

logger = logging.getLogger(__name__)


class MCSFilesystemAdapter:
    """``FilesystemPort``-shaped adapter backed by a ``FilesystemConnection``.

    Constructor wiring (DI):

    - ``connection`` -- :class:`FilesystemConnection` (level 1).
      ``connection.root`` becomes the adapter's base directory;
      all relative paths the LLM passes are resolved against it.

    The adapter holds the connection reference (rather than only
    its ``root``) so a later iteration can route writes through
    ``connection.write_bytes`` to participate in any audit /
    replication policy the connection chain adds.
    """

    def __init__(self, *, connection: FilesystemConnection) -> None:
        self._connection = connection
        self._root = Path(connection.root).resolve()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _safe_path(self, path: str) -> Path:
        """Resolve ``path`` against the connection root with two guards.

        First the connection's own ``resolve()`` rejects absolute
        paths and ``..`` segments before any filesystem call. Then
        the final ``Path.resolve()`` catches symlink escapes that
        would otherwise jump out of the workspace sandbox.
        """
        rel = PurePath(path)
        # Connection-level guard (sync, raises ValueError on bad input).
        self._connection.resolve(rel)
        # Symlink-aware guard against the real filesystem.
        target = (self._root / rel).resolve()
        if target != self._root and self._root not in target.parents:
            raise ValueError(
                f"Path escapes connection root {self._root}: {path}"
            )
        return target

    # ------------------------------------------------------------------
    # FilesystemPort surface
    # ------------------------------------------------------------------

    def list_dir(self, path: str) -> str:
        target = self._safe_path(path)
        if not target.is_dir():
            return json.dumps({"error": f"Not a directory: {target}"})
        entries: list[dict[str, Any]] = []
        for entry in sorted(target.iterdir()):
            entries.append({
                "name": entry.name,
                "type": "directory" if entry.is_dir() else "file",
                "size": entry.stat().st_size if entry.is_file() else None,
            })
        return json.dumps(
            {"path": str(target), "entries": entries}, indent=2
        )

    def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
        target = self._safe_path(path)
        if not target.is_file():
            return json.dumps({"error": f"Not a file: {target}"})
        try:
            content = target.read_text(encoding=encoding)
        except Exception as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps({"path": str(target), "content": content})

    def write_text(
        self, path: str, content: str, *, encoding: str = "utf-8"
    ) -> str:
        target = self._safe_path(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding=encoding)
        except Exception as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps({
            "path": str(target),
            "bytes_written": len(content.encode(encoding)),
        })

    def list_files(self, path: str, pattern: str = "*") -> list[str]:
        target = self._safe_path(path)
        if not target.is_dir():
            return []
        return sorted(
            str(p.relative_to(self._root))
            for p in target.glob(pattern)
            if p.is_file()
        )

    def read_raw(self, path: str, *, encoding: str = "utf-8") -> str:
        target = self._safe_path(path)
        return target.read_text(encoding=encoding)

    def exists(self, path: str) -> bool:
        try:
            target = self._safe_path(path)
        except ValueError:
            return False
        return target.exists()
