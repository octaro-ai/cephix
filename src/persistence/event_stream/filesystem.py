"""``FilesystemEventStreamProvider`` -- PROVIDER level (2), filesystem-backed.

Implements :class:`EventStreamProviderPort`. Receives a
:class:`~src.persistence.filesystem.FilesystemConnection` by
constructor injection. Records are serialized as JSONL inline:
one JSON object per line, NDJSON-style. The wire format is fixed
on purpose -- a future need for another format (parquet, msgpack)
would be a separate provider, not a configurable knob here.

Internally caches one :class:`AppendWriter` per channel so the hot
path (``append``) is one dict lookup + one ``write_line``. The
cache is invisible to the consumer; from outside this object is
just a DAO with ``append`` / ``flush``.

The provider knows nothing about the robot's run-id. Per-run
path scoping is a **sink-side** concern: an on-bus sink
(:class:`~src.telemetry.bus_recorder.BusRecorder`,
:class:`~src.audit.note_sink.AuditNoteSink`) learns the current
``robot_run_id`` by reading the retained ``RobotLifecycle.boot``
event off the bus and passes a pre-scoped channel string
(``"<run_id>/telemetry"``, ``"<run_id>/audit"``) into
:meth:`append`. The provider just resolves whatever it gets to
``<root>/<directory>/<channel>.jsonl``; ``"/"`` segments inside
the channel name become subdirs (see :meth:`FilesystemConnection.path_for`).

Boot log:

    LocalFSAdapter (...) injected into FilesystemConnection (...)
    FilesystemConnection (...) started
    FilesystemConnection (...) injected into FilesystemEventStreamProvider (...)
    FilesystemEventStreamProvider (...) started
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from typing import Any

from src.components import ComponentCategory, RobotComponent
from src.persistence.filesystem import FilesystemConnection
from src.persistence.filesystem.port import AppendWriter

logger = logging.getLogger(__name__)


_FILE_EXTENSION = ".jsonl"


class FilesystemEventStreamProvider(RobotComponent):
    """Append-only event streams on top of a :class:`FilesystemConnection`.

    Constructor wiring (DI):

    - ``connection`` -- the level-1 :class:`FilesystemConnection`
      that resolves channels to file paths and opens writers.
    - ``directory`` -- the provider's bucket inside the shared
      connection root. Channels resolve to
      ``<root>/<directory>/<channel>.jsonl``. Default ``logs/``.

    Implements :class:`EventStreamProviderPort` directly; consumers
    type-hint the port, the builder injects this concrete provider.
    """

    component_name = "filesystem-events"
    component_category = ComponentCategory.PROVIDER
    component_description = (
        "Append-only event streams backed by a FilesystemConnection. "
        "Implements EventStreamProviderPort at boot level 2; receives "
        "the connection by constructor injection. Channels resolve to "
        "<root>/<directory>/<channel>.jsonl, one JSON object per line."
    )

    def __init__(
        self,
        *,
        connection: FilesystemConnection,
        directory: str = "logs",
    ) -> None:
        if not isinstance(connection, FilesystemConnection):
            raise TypeError(
                "FilesystemEventStreamProvider.connection must be a "
                "FilesystemConnection, got "
                f"{type(connection).__name__}"
            )
        # The directory is the provider's bucket inside the shared
        # connection root: telemetry / audit / ... all land under
        # ``<root>/<directory>/``. An empty string means "channels
        # sit directly under root", which is fine for tests but not
        # the default -- a robot home shared with other utilities
        # would otherwise collide.
        if not isinstance(directory, str):
            raise TypeError(
                "FilesystemEventStreamProvider.directory must be a string"
            )
        self._connection = connection
        self._directory = directory.strip("/").strip("\\")
        # channel -> writer; lazily opened on first append, closed on stop().
        # The cache key is the verbatim channel string the consumer
        # hands in -- ``"telemetry"`` and ``"run-abc/telemetry"``
        # are two distinct entries, so a sink that switches its
        # scope mid-stream gets a fresh writer on the new path
        # rather than appending across paths.
        self._writers: dict[str, AppendWriter] = {}
        self._writer_lock = asyncio.Lock()

    @property
    def connection(self) -> FilesystemConnection:
        return self._connection

    @property
    def directory(self) -> str:
        return self._directory

    # ---- EventStreamProviderPort -------------------------------------------

    async def append(self, channel: str, record: Mapping[str, Any]) -> None:
        writer = await self._writer_for(channel)
        line = _encode_line(record)
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

    async def _drain(self) -> None:
        # Best-effort flush before the consumer components stop.
        await self.flush()

    async def _stop(self) -> None:
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
            resolved = (
                f"{self._directory}/{channel}" if self._directory else channel
            )
            writer = await self._connection.open_append(
                resolved, suffix=_FILE_EXTENSION
            )
            self._writers[channel] = writer
            return writer


def _encode_line(record: Mapping[str, Any]) -> str:
    """Render ``record`` as a single NDJSON line (no trailing newline).

    ``ensure_ascii=False`` so German umlauts / emoji round-trip
    cleanly. ``default=str`` so the writer never crashes on rich
    types like ``datetime`` or ``UUID`` -- they end up as their
    canonical string representation. Compact separators because
    each event has its own line; whitespace is just noise.
    """
    return json.dumps(
        dict(record),
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
