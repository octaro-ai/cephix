"""Event-stream family of the persistence layer.

A :class:`EventStreamProviderPort` is the DAO components use to
record events into a named channel. Concrete providers wire the
domain API to a specific storage stack:

- :class:`FilesystemEventStreamProvider` -- channels become files
  under a :class:`~src.persistence.filesystem.FilesystemConnection`
  root, encoded by a configurable :class:`~src.persistence.codec.RecordCodec`
  (JSONL by default).
- Future: ``DatabaseEventStreamProvider``, ``ObjectStoreEventStreamProvider``
  -- different storage backends, same domain port.

Consumers (``BusRecorder``, ``AuditNoteSink``) depend on the port,
not on a concrete implementation; the builder injects whatever stack
the YAML configures.
"""

from src.persistence.event_stream.filesystem import FilesystemEventStreamProvider
from src.persistence.event_stream.port import EventStreamProviderPort

__all__ = [
    "EventStreamProviderPort",
    "FilesystemEventStreamProvider",
]
