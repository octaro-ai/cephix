"""Robot-wide persistence layer.

Two abstractions:

- :class:`EventSink` -- the per-stream write API used by individual
  components.
- :class:`PersistenceProvider` -- the robot-wide factory that hands
  out sinks by channel name. Configured once per robot; every
  component that needs to persist asks the provider for its channel
  and stays oblivious to the backend.

Built-in implementations: :class:`JsonlEventSink` (append-only
NDJSON file) and :class:`JsonlPersistenceProvider` (one file per
channel under the workspace root). Future implementations
(SQLite, Supabase, S3, ClickHouse, ...) plug in by satisfying the
same two protocols.
"""

from src.persistence.jsonl_sink import JsonlEventSink
from src.persistence.provider import JsonlPersistenceProvider, PersistenceProvider
from src.persistence.sink import EventSink

__all__ = [
    "EventSink",
    "JsonlEventSink",
    "JsonlPersistenceProvider",
    "PersistenceProvider",
]
