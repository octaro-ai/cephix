"""Append-only JSON-Lines (NDJSON) implementation of :class:`EventSink`.

The simplest persistent backend cephix ships with: one record per
line, encoded as JSON, terminated by ``\n``. Files written this way
are usable directly with ``jq``, ``rg``, ``head``, ``cat``,
``less``, log shippers, and pretty much every observability tool.

Properties:

- Append-only. Existing entries are never rewritten.
- Crash-safe at line granularity: a partially written line is
  always at the tail and can be detected/skipped by readers.
- Concurrent-safe within one process: an :class:`asyncio.Lock`
  serializes writes from multiple producers.
- Newer iterations may add an optional fsync-on-flush, batched
  background writer, or rotation; the protocol stays the same.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TextIO

logger = logging.getLogger(__name__)


class JsonlEventSink:
    """Persist records as one JSON object per line.

    The sink lazily opens its file on the first :meth:`append` call
    so a robot that never produces an event leaves no empty file
    behind. Parent directories are created on demand.

    ``ensure_ascii`` defaults to ``False`` so non-ASCII text (German
    umlauts, emoji in user messages) lands in the file unmangled.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        ensure_ascii: bool = False,
    ) -> None:
        self._path = Path(path)
        self._ensure_ascii = ensure_ascii
        self._file: TextIO | None = None
        self._lock = asyncio.Lock()
        self._closed = False

    @property
    def path(self) -> Path:
        return self._path

    async def append(self, record: Mapping[str, Any]) -> None:
        if self._closed:
            raise RuntimeError(
                f"JsonlEventSink({self._path}) has been closed; "
                "construct a new sink to record more records"
            )
        async with self._lock:
            if self._file is None:
                self._file = await asyncio.to_thread(self._open_for_append)
            line = json.dumps(
                dict(record),
                ensure_ascii=self._ensure_ascii,
                separators=(",", ":"),
                default=str,
            )
            await asyncio.to_thread(self._write_line, line)

    async def flush(self) -> None:
        async with self._lock:
            if self._file is not None:
                await asyncio.to_thread(self._file.flush)

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._file is not None:
                try:
                    await asyncio.to_thread(self._file.flush)
                    await asyncio.to_thread(self._file.close)
                except Exception:
                    logger.exception(
                        "error while closing JsonlEventSink at %s", self._path
                    )
                self._file = None

    def _open_for_append(self) -> TextIO:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        return self._path.open("a", encoding="utf-8")

    def _write_line(self, line: str) -> None:
        assert self._file is not None
        self._file.write(line)
        self._file.write("\n")
