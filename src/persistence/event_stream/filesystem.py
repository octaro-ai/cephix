"""``FilesystemEventStreamProvider`` -- PROVIDER level (2), filesystem-backed.

Implements :class:`EventStreamProviderPort`. Receives a
:class:`~src.persistence.filesystem.FilesystemConnection` by
constructor injection and a :class:`~src.persistence.codec.RecordCodec`
to turn records into bytes (JSONL by default).

Internally caches one :class:`AppendWriter` per channel so the hot
path (``append``) is one dict lookup + one ``write_line``. The
cache is invisible to the consumer; from outside this object is
just a DAO with ``append`` / ``flush``.

Boot log:

    LocalFSAdapter (...) injected into FilesystemConnection (...)
    FilesystemConnection (...) started
    FilesystemConnection (...) injected into FilesystemEventStreamProvider (...)
    FilesystemEventStreamProvider (...) started
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from src.components import ComponentCategory, RobotComponent
from src.persistence.codec import JsonlCodec, RecordCodec
from src.persistence.filesystem import FilesystemConnection
from src.persistence.filesystem.port import AppendWriter

logger = logging.getLogger(__name__)


class FilesystemEventStreamProvider(RobotComponent):
    """Append-only event streams on top of a :class:`FilesystemConnection`.

    Constructor wiring (DI):

    - ``connection`` -- the level-1 :class:`FilesystemConnection`
      that resolves channels to file paths and opens writers.
    - ``codec`` -- a :class:`RecordCodec` (defaults to
      :class:`JsonlCodec`). Sits next to the storage layer in its
      own subpackage because format is orthogonal to backend: a
      future DB provider can reuse :class:`JsonlCodec` to write
      JSON into a BLOB column.

    Implements :class:`EventStreamProviderPort` directly; consumers
    type-hint the port, the builder injects this concrete provider.
    """

    component_name = "filesystem-events"
    component_category = ComponentCategory.PROVIDER
    component_description = (
        "Append-only event streams backed by a FilesystemConnection. "
        "Implements EventStreamProviderPort at boot level 2; receives "
        "the connection by constructor injection. Channels resolve "
        "to files in the connection's root, encoded by the configured "
        "codec (JSONL by default)."
    )

    def __init__(
        self,
        *,
        connection: FilesystemConnection,
        codec: RecordCodec | None = None,
    ) -> None:
        if not isinstance(connection, FilesystemConnection):
            raise TypeError(
                "FilesystemEventStreamProvider.connection must be a "
                "FilesystemConnection, got "
                f"{type(connection).__name__}"
            )
        self._connection = connection
        self._codec: RecordCodec = codec or JsonlCodec()
        # channel -> writer; lazily opened on first append, closed on stop().
        self._writers: dict[str, AppendWriter] = {}
        self._writer_lock = asyncio.Lock()

    @property
    def connection(self) -> FilesystemConnection:
        return self._connection

    @property
    def codec(self) -> RecordCodec:
        return self._codec

    # ---- EventStreamProviderPort -------------------------------------------

    async def append(self, channel: str, record: Mapping[str, Any]) -> None:
        writer = await self._writer_for(channel)
        line = self._codec.encode_line(record)
        await writer.write_line(line)

    async def flush(self, channel: str | None = None) -> None:
        if channel is None:
            targets = list(self._writers.values())
        else:
            existing = self._writers.get(channel)
            targets = [existing] if existing is not None else []
        for writer in targets:
            try:
                await writer.flush()
            except Exception:
                logger.exception(
                    "FilesystemEventStreamProvider: flush failed for %s",
                    writer.path,
                )

    # ---- RobotComponent lifecycle ------------------------------------------

    async def start(self) -> None:
        # Log the connection -> provider wiring, symmetric to the
        # adapter -> connection log line in FilesystemConnection.start.
        # The robot logs ``started`` right after this returns, so the
        # log reads:
        #
        #   === Boot Level 2 (PROVIDER) ===
        #   FilesystemConnection (yyy) injected into FilesystemEventStreamProvider (zzz)
        #   FilesystemEventStreamProvider (zzz) started
        conn_id = getattr(self._connection, "instance_id", "")
        logger.info(
            "%s (%s) injected into %s (%s)",
            type(self._connection).__name__,
            conn_id,
            type(self).__name__,
            self.instance_id,
        )
        # All record-write work is lazy per channel; nothing else to do.

    async def drain(self) -> None:
        # Best-effort flush before the consumer components stop.
        await self.flush()

    async def stop(self) -> None:
        async with self._writer_lock:
            writers = list(self._writers.items())
            self._writers.clear()
        for channel, writer in writers:
            try:
                await writer.close()
            except Exception:
                logger.exception(
                    "FilesystemEventStreamProvider: close failed for "
                    "channel %r (path %s)",
                    channel,
                    writer.path,
                )

    # ---- internals ---------------------------------------------------------

    async def _writer_for(self, channel: str) -> AppendWriter:
        # Fast path without lock: dict reads are atomic in CPython
        # for str keys, but two coroutines could race to open the
        # same channel on cold start -- hence the lock around the
        # open + insert.
        writer = self._writers.get(channel)
        if writer is not None:
            return writer
        async with self._writer_lock:
            writer = self._writers.get(channel)
            if writer is not None:
                return writer
            writer = await self._connection.open_append(
                channel, suffix=self._codec.extension
            )
            self._writers[channel] = writer
            return writer
