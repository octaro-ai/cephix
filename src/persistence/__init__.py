"""Cephix persistence layer -- DAO-modelled, format-orthogonal.

Four levels, each with one clear responsibility:

- **Codec** (library, no lifecycle) -- record-to-bytes serialization.
  Default: :class:`~src.persistence.codec.JsonlCodec`.
- **Backend** (boot level 0) -- abstract filesystem driver,
  e.g. :class:`~src.persistence.filesystem.LocalFSAdapter`.
  Implements :class:`~src.persistence.filesystem.FilesystemPort`.
- **Connection** (boot level 1) -- adapter + root + channel
  resolution. :class:`~src.persistence.filesystem.FilesystemConnection`.
- **Provider** (boot level 2) -- the DAO consumers depend on.
  :class:`~src.persistence.event_stream.EventStreamProviderPort` is
  the contract; :class:`~src.persistence.event_stream.FilesystemEventStreamProvider`
  the filesystem-backed implementation.

Consumers (``BusRecorder``, ``AuditNoteSink``, future stores) hold
a reference to the provider port plus a channel name; they call
``await provider.append(channel, record)``. The sink/handle layer
that used to sit between them is now an internal implementation
detail of the provider.
"""

from src.persistence.codec import JsonlCodec, RecordCodec
from src.persistence.event_stream import (
    EventStreamProviderPort,
    FilesystemEventStreamProvider,
)
from src.persistence.filesystem import (
    FilesystemConnection,
    FilesystemPort,
    LocalFSAdapter,
)

__all__ = [
    "EventStreamProviderPort",
    "FilesystemConnection",
    "FilesystemEventStreamProvider",
    "FilesystemPort",
    "JsonlCodec",
    "LocalFSAdapter",
    "RecordCodec",
]
