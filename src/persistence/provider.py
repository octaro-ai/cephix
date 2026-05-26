"""Roboter-weiter Persistenz-Layer: ``PersistenceProvider`` und JSONL-Default.

A :class:`PersistenceProvider` is the single point that hands out
:class:`EventSink` instances to components. Components ask for a
*channel* by name ("telemetry", "audit", later "memory",
"notebooks", ...); the provider decides how that channel lands
physically.

This decouples component code from the storage backend:

- Today the default :class:`JsonlPersistenceProvider` writes one
  ``<channel>.jsonl`` file per channel into the workspace.
- Tomorrow a ``SqlitePersistenceProvider`` can map every channel to
  a table without changing a single component.
- The day after, a ``SupabasePersistenceProvider`` can map them to
  remote rows; same component code.

The provider is currently a *builder helper*, not a
:class:`RobotComponent`. Reason: the only built-in implementation
holds no shared resource -- every JSONL sink owns its own file
handle and closes itself when its component stops. As soon as a
backend introduces a shared resource (a SQLite connection pool, a
Supabase client, an open S3 multipart upload), the provider becomes
a real component with its own lifecycle and the boot order grows by
one (``PERSISTENCE`` would slot in between ``BUS`` and ``TELEMETRY``,
because telemetry already needs a sink at start time). The
component contract is intentionally trivial enough that this is a
small refactor, not a rewrite.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol, runtime_checkable

from src.persistence.jsonl_sink import JsonlEventSink
from src.persistence.sink import EventSink

logger = logging.getLogger(__name__)


@runtime_checkable
class PersistenceProvider(Protocol):
    """Hands out :class:`EventSink` instances per channel name."""

    def open(self, channel: str) -> EventSink:
        """Return the :class:`EventSink` for the given channel.

        Implementations may cache and return the same sink for repeat
        calls with the same channel name, or hand out fresh ones on
        each call -- callers must not assume either. The component
        receiving the sink is responsible for its lifecycle: it must
        ``flush()`` during ``drain()`` and ``close()`` during
        ``stop()``.
        """


class JsonlPersistenceProvider:
    """Provider that maps channels to ``<root>/<channel>.jsonl`` files.

    The root is typically the bot's workspace, so a robot's full
    persistent footprint sits next to its ``robot.yaml``. Subdirectories
    can be expressed in the channel name (e.g. ``"runs/2026-05-25"``);
    parent directories are created lazily on first append.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        suffix: str = ".jsonl",
    ) -> None:
        self._root = Path(root)
        self._suffix = suffix
        # Channel -> sink cache so a single channel always resolves to
        # the same sink instance for the lifetime of the provider.
        self._cache: dict[str, JsonlEventSink] = {}

    @property
    def root(self) -> Path:
        return self._root

    def path_for(self, channel: str) -> Path:
        """Return the file path the given channel will write to."""
        if not channel:
            raise ValueError("channel name must be non-empty")
        if channel.startswith(("/", "\\")):
            raise ValueError(
                f"channel {channel!r} must be a relative name, "
                "not an absolute path"
            )
        return self._root / f"{channel}{self._suffix}"

    def open(self, channel: str) -> EventSink:
        if channel in self._cache:
            return self._cache[channel]
        path = self.path_for(channel)
        sink = JsonlEventSink(path)
        self._cache[channel] = sink
        logger.debug(
            "JsonlPersistenceProvider opened channel %r at %s", channel, path
        )
        return sink
