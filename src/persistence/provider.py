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

Lifecycle: providers are :class:`BusComponent` instances. They are
**not bus-aware on the data path** -- the actual write API
(:meth:`open` -> :class:`EventSink`) is called directly by the
observer (the recorder / audit sink), no bus messages involved. The
bus attachment only carries lifecycle announcements
(``ComponentLifecycle`` on attach/detach) so the provider can be
health-checked and, later, gracefully swapped at runtime. That keeps
write volume off the bus while still treating storage as a first-
class component.

Boot ordering: providers boot at the ``PERSISTENCE`` priority
(between ``BUS`` and ``TELEMETRY``), so observers can call
:meth:`open` from their own ``start()`` and find the provider ready.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Protocol, runtime_checkable

from src.bus.ports import BusPort
from src.components import BusComponent, ComponentCategory, ComponentHealth
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


class JsonlPersistenceProvider(BusComponent):
    """Provider that maps channels to ``<root>/<channel>.jsonl`` files.

    The root is typically the bot's workspace, so a robot's full
    persistent footprint sits next to its ``robot.yaml``. Subdirectories
    can be expressed in the channel name (e.g. ``"runs/2026-05-25"``);
    parent directories are created lazily on first append.

    BusComponent for lifecycle (announces ``ready`` / ``shutdown`` on
    its own ``component.lifecycle.<name>`` topic) and health-check
    only -- the write path stays direct (each component receives an
    :class:`EventSink` reference via :meth:`open` and writes to it
    without going through the bus).
    """

    component_name = "jsonl"
    component_category = ComponentCategory.PERSISTENCE
    component_description = (
        "Filesystem persistence provider: one append-only JSONL file "
        "per channel under the configured root. Bus-attached for "
        "lifecycle and health-check only; write volume bypasses the "
        "bus and goes directly through EventSink references."
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
        self._bus: BusPort | None = None

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

    # ---- BusComponent lifecycle -------------------------------------------

    async def start(self, bus: BusPort) -> None:  # type: ignore[override]
        """Attach to the bus and announce ``ready``.

        Sinks are still opened lazily on demand via :meth:`open`; the
        provider just registers itself as available now. Observer
        components (bus_recorder, audit_note_sink) call :meth:`open`
        from their own ``start()`` once they are booted -- by then
        persistence is up because it sits at a lower
        ``BOOT_PRIORITY``.
        """
        self._bus = bus
        await self.announce_lifecycle(bus, "ready")

    async def drain(self) -> None:  # type: ignore[override]
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

    async def stop(self) -> None:  # type: ignore[override]
        """Announce ``shutdown`` and close every issued sink."""
        if self._bus is not None:
            await self.announce_lifecycle(self._bus, "shutdown")
        for channel, sink in list(self._cache.items()):
            try:
                await sink.close()
            except Exception:
                logger.exception(
                    "JsonlPersistenceProvider: close failed for channel %r",
                    channel,
                )
        self._cache.clear()
        self._bus = None

    async def health_check(self) -> ComponentHealth:  # type: ignore[override]
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
