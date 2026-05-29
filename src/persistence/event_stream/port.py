"""``EventStreamProviderPort`` -- the DAO for append-only record streams.

The contract every event-stream provider satisfies: record
*append* into a *named channel*, plus an explicit *flush*. The
channel is a string parameter on every call -- not a sink-handle
the caller carries. That is the central DAO refinement compared to
the earlier ``EventSink``-per-channel design:

- The consumer holds **one** reference (the provider) and decides
  per call which channel a record belongs to.
- The provider owns whatever per-channel state is needed (file
  handles, prepared statements, connection slots) **internally**.
  No state leaks into the consumer.
- Swapping the storage backend (filesystem -> database -> S3) is a
  consumer-transparent change: as long as a new class implements
  this port, ``BusRecorder`` and ``AuditNoteSink`` do not need to
  know it exists.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EventStreamProviderPort(Protocol):
    """Append-only record-stream DAO.

    Calls are async because the underlying storage may be
    networked. Implementations are free to batch writes and return
    fast, as long as :meth:`flush` makes outstanding records
    durable by the time it resolves.
    """

    async def append(self, channel: str, record: Mapping[str, Any]) -> None:
        """Append ``record`` to ``channel``.

        ``channel`` is a relative, slash-separated name (e.g.
        ``"telemetry"``, ``"audit"``, later ``"sessions/sess-abc"``).
        ``record`` is a JSONable mapping; the provider's codec
        decides how it is serialized.

        Idempotency: implementations may treat duplicate appends as
        new records. Deduplication is the caller's job if it cares.
        """

    async def flush(self, channel: str | None = None) -> None:
        """Make pending appends durable.

        Without arguments: flush every channel the provider has
        open. With a ``channel``: flush only that one. Called by
        ``drain()`` hooks of the consumer components during
        graceful shutdown -- a slow flush of one channel must not
        block flush of another.
        """
