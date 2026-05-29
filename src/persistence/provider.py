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

Lifecycle: providers are plain :class:`RobotComponent`s at boot
level ``PROVIDER`` (2). They boot **before** the bus and are injected
by constructor -- no bus traffic on the data path. A future
``BUS_PROVIDER`` (level 5) can expose the same provider over the bus
when bus-visible storage access is desired.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Protocol, runtime_checkable

from src.components import ComponentCategory, ComponentHealth, RobotComponent
from src.bus.messages import ErrorInfo
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


class JsonlPersistenceProvider(RobotComponent):
    """Interim provider: maps channels to ``<root>/<channel>.jsonl`` files.

    The root is typically the bot's workspace. Subdirectories can be
    expressed in the channel name; parent directories are created lazily
    on first append.

    Boots at ``PROVIDER`` (level 2), off-bus. Telemetry and audit
    receive sinks via :meth:`open` at build/constructor time. Later
    this class splits into Backend + Connection + Provider with
    ``filesystem`` as the layer name and ``jsonl`` as the format.
    """

    component_name = "jsonl"
    component_category = ComponentCategory.PROVIDER
    component_description = (
        "Interim filesystem-backed persistence provider (JSONL format). "
        "Off-bus PROVIDER at boot level 2; injectable by constructor. "
        "Write volume bypasses the bus and goes directly through "
        "EventSink references."
    )

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
        # Also used by ``stop()`` to flush + close every issued sink.
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

    # ---- RobotComponent lifecycle -----------------------------------------

    async def start(self) -> None:
        """Ensure the root exists; sinks open lazily via :meth:`open`."""
        self._root.mkdir(parents=True, exist_ok=True)

    async def drain(self) -> None:
        """Flush every issued sink before the robot starts stopping.

        The observers that own these sinks also drain them on their
        own ``drain()``; calling ``flush()`` here once more is cheap
        (idempotent) and guarantees the provider's accounting is in
        sync if a sink was issued to a component that did not
        register a drain hook.
        """
        for channel, sink in self._cache.items():
            try:
                await sink.flush()
            except Exception:
                logger.exception(
                    "JsonlPersistenceProvider: flush failed for channel %r",
                    channel,
                )

    async def stop(self) -> None:
        """Close every issued sink."""
        for channel, sink in list(self._cache.items()):
            try:
                await sink.close()
            except Exception:
                logger.exception(
                    "JsonlPersistenceProvider: close failed for channel %r",
                    channel,
                )
        self._cache.clear()

    async def health_check(self) -> ComponentHealth:
        """Report ``ok`` while the root is writable, ``warn`` otherwise.

        First-cut check: the configured root directory must exist (or
        be creatable) and be writable. DB-backed providers will check
        their connection here; S3-backed ones the bucket reachability.
        """
        try:
            self._root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return ComponentHealth(
                status="warn",
                error=ErrorInfo(
                    code="persistence_root_unavailable",
                    message=f"cannot create root {self._root}: {exc}",
                ),
                metadata={"root": str(self._root)},
            )
        if not os.access(self._root, os.W_OK):
            return ComponentHealth(
                status="warn",
                error=ErrorInfo(
                    code="persistence_root_readonly",
                    message=f"root {self._root} is not writable",
                ),
                metadata={"root": str(self._root)},
            )
        return ComponentHealth(
            status="ok",
            metadata={"root": str(self._root), "channels": len(self._cache)},
        )
