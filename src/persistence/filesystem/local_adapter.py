"""``LocalFSAdapter`` -- :class:`FilesystemPort` implemented on top of the OS.

Backend-level (BOOT level 0). Pure pathlib + ``asyncio.to_thread``;
no other dependency. Future remote adapters (S3, SMB) sit at the
same level and implement the same port -- the connection layer
above stays unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path, PurePath
from typing import TextIO

from src.components import ComponentCategory, ComponentHealth, RobotComponent
from src.persistence.filesystem.port import AppendWriter, FilesystemPort

logger = logging.getLogger(__name__)


class LocalFSAdapter(RobotComponent):
    """Local-OS filesystem backend.

    BACKEND-level (level 0): the "resource exists" stage. For the
    local FS this is trivially satisfied -- the OS guarantees its
    presence -- but the component is *visible* in the boot log so
    swapping in another adapter (S3FSAdapter, ...) later only
    changes this one line, not the layers above.

    Implements :class:`FilesystemPort` directly; instances are
    injected into a :class:`FilesystemConnection` at level 1.
    """

    component_name = "local-fs"
    component_category = ComponentCategory.BACKEND
    component_description = (
        "Local-OS filesystem backend (pathlib + asyncio.to_thread). "
        "Implements FilesystemPort at boot level 0. Injected into a "
        "FilesystemConnection at level 1."
    )

    async def start(self) -> None:
        # Stateless. Health is checked via ``FilesystemConnection.health_check``
        # which delegates to ``is_writable``.
        return None

    async def stop(self) -> None:
        return None

    # ---- FilesystemPort -----------------------------------------------------

    async def open_append(self, path: PurePath) -> AppendWriter:
        local = Path(path)
        # Parent must exist for ``open("a")`` to succeed; mkdir is
        # cheap and idempotent.
        await asyncio.to_thread(local.parent.mkdir, parents=True, exist_ok=True)
        handle = await asyncio.to_thread(
            local.open, "a", encoding="utf-8", buffering=1  # line-buffered
        )
        return _LocalAppendWriter(handle, local)

    async def mkdir(self, path: PurePath, *, parents: bool = True) -> None:
        local = Path(path)
        await asyncio.to_thread(local.mkdir, parents=parents, exist_ok=True)

    async def exists(self, path: PurePath) -> bool:
        return await asyncio.to_thread(Path(path).exists)

    async def is_writable(self, path: PurePath) -> bool:
        local = Path(path)
        # ``os.access`` returns False for non-existent paths; check
        # the closest existing ancestor instead so a fresh workspace
        # directory doesn't read as "not writable" before its first
        # ``mkdir``.
        probe = local
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        return await asyncio.to_thread(os.access, probe, os.W_OK)


class _LocalAppendWriter:
    """Adapter-internal :class:`AppendWriter` over a stdlib text file."""

    def __init__(self, handle: TextIO, path: Path) -> None:
        self._handle: TextIO | None = handle
        self._path = path
        self._lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        return self._path

    async def write_line(self, line: str) -> None:
        async with self._lock:
            if self._handle is None:
                raise RuntimeError(
                    f"_LocalAppendWriter({self._path}) is closed; "
                    "open a fresh writer to append more lines"
                )
            await asyncio.to_thread(self._handle.write, line)
            await asyncio.to_thread(self._handle.write, "\n")

    async def flush(self) -> None:
        async with self._lock:
            if self._handle is not None:
                await asyncio.to_thread(self._handle.flush)

    async def close(self) -> None:
        async with self._lock:
            if self._handle is None:
                return
            try:
                await asyncio.to_thread(self._handle.flush)
                await asyncio.to_thread(self._handle.close)
            except Exception:
                logger.exception(
                    "error while closing _LocalAppendWriter at %s", self._path
                )
            self._handle = None
