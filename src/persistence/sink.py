"""Storage protocol shared by telemetry and audit components.

The :class:`EventSink` is the DAO boundary: any record-oriented
backend can satisfy it (file, SQLite, Postgres/Supabase, S3 bucket,
ClickHouse, ...). Cephix components only ever depend on the protocol,
so swapping a backend is a one-liner in the builder.

Design notes:

- ``append`` accepts a plain ``Mapping[str, Any]`` so callers don't
  have to know about the storage format. Sinks are responsible for
  whatever serialization they need.
- ``flush`` is the explicit drain point used by component
  :meth:`drain` hooks during graceful shutdown.
- ``close`` is final: a sink that has been closed must not be
  reopened. Reuse needs a fresh instance.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EventSink(Protocol):
    """Append-only sink for cephix audit/telemetry records."""

    async def append(self, record: Mapping[str, Any]) -> None:
        """Persist ``record``.

        Records are JSONable mappings. Sinks should make ``append``
        non-blocking from the caller's point of view (own writer
        task, batched flushes, ...) but at minimum must guarantee
        durability after a successful :meth:`flush`.
        """

    async def flush(self) -> None:
        """Persist all pending records before returning.

        Called by component :meth:`drain` hooks during graceful
        shutdown. Sinks that batch internally must drain their
        batches here.
        """

    async def close(self) -> None:
        """Release every resource held by the sink.

        Idempotent. After ``close``, the sink must reject further
        :meth:`append` calls.
        """
