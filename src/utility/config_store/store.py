"""``FilesystemConfigStore`` -- config store on top of a FilesystemConnection.

Reads YAML files from ``<connection-root>/<directory>/`` (default
``configs/``) and exposes their parsed contents through
:class:`~src.utility.config_store.ports.ConfigStorePort`. Each
file's stem becomes the lookup key
(``heartbeats.yaml`` -> ``configs("heartbeats")``); the file
content must be a top-level YAML list whose entries are mappings
the consumer parses itself.

Lifecycle: :meth:`start` does the initial read so the consumer can
synchronously call :meth:`configs` immediately afterwards (reads
the in-memory cache). The async :meth:`refresh` re-reads on
demand; intended for a future runtime ``config.reload`` command.

DI: takes a :class:`FilesystemConnection` rather than a raw path,
so it shares the adapter chain with the persistence provider,
session store and firmware store. The ``directory`` field selects
its bucket inside the shared connection root, mirroring the same
pattern.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import PurePath
from typing import Any

import yaml

from src.components import ComponentCategory, RobotComponent
from src.persistence.filesystem.connection import FilesystemConnection
from src.utility.config_store.ports import ConfigStorePort

logger = logging.getLogger(__name__)

_DEFAULT_DIRECTORY = "configs"
_DEFAULT_PATTERNS: tuple[str, ...] = ("*.yaml", "*.yml")


class FilesystemConfigStore(RobotComponent, ConfigStorePort):
    """Config store backed by a directory of YAML files.

    Constructor wiring (DI):

    - ``connection`` -- :class:`FilesystemConnection` (level 1).
      Routes the read IO through the same adapter stack the
      persistence provider, session store and firmware store use.
    - ``directory`` -- the store's bucket inside the connection
      root, default ``configs/``.
    - ``patterns`` -- filename globs that count as configs,
      default ``("*.yaml", "*.yml")``. The first matching suffix
      is stripped off to derive the lookup key.

    The store reads files eagerly on :meth:`start` so consumers
    can call :meth:`configs` without coroutine plumbing. A missing
    directory is tolerated -- the configs map is simply empty and
    ``configs(key)`` returns ``[]`` for every key, letting the
    consumer fall back to its own defaults.
    """

    component_name = "config-store"
    component_category = ComponentCategory.UTILITY
    component_description = (
        "Config store. Reads YAML files from the configured "
        "directory under the FilesystemConnection root (default "
        "<root>/configs/, patterns *.yaml / *.yml). Each file's "
        "stem becomes the lookup key; the file content must be a "
        "top-level list. Off-bus utility consumed by components "
        "that need user-editable list configs."
    )

    def __init__(
        self,
        *,
        connection: FilesystemConnection,
        directory: str = _DEFAULT_DIRECTORY,
        patterns: Sequence[str] = _DEFAULT_PATTERNS,
    ) -> None:
        if not isinstance(connection, FilesystemConnection):
            raise TypeError(
                "FilesystemConfigStore.connection must be a "
                "FilesystemConnection, got "
                f"{type(connection).__name__}"
            )
        if not isinstance(directory, str):
            raise TypeError(
                "FilesystemConfigStore.directory must be a string"
            )
        self._fs = connection
        self._directory = directory.strip("/").strip("\\")
        self._patterns: tuple[str, ...] = tuple(patterns) or _DEFAULT_PATTERNS
        self._configs: dict[str, list[dict[str, Any]]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Surface the connection -> store wiring, then load configs."""
        connection_id = getattr(self._fs, "instance_id", "")
        logger.info(
            "%s (%s) injected into %s (%s)",
            type(self._fs).__name__,
            connection_id,
            type(self).__name__,
            self.instance_id,
        )
        await self.refresh()

    async def _stop(self) -> None:
        """Drop the in-memory config cache."""
        self._configs = {}

    # ------------------------------------------------------------------
    # ConfigStorePort
    # ------------------------------------------------------------------

    def configs(self, key: str) -> list[dict[str, Any]]:
        """Return the entries stored under ``key`` (empty list if absent)."""
        return list(self._configs.get(key, []))

    async def refresh(self) -> None:
        """Re-read every matching config file from the underlying store."""
        configs: dict[str, list[dict[str, Any]]] = {}
        directory = PurePath(self._directory) if self._directory else PurePath()
        try:
            names = await self._fs.listdir(directory)
        except Exception:
            logger.warning(
                "FilesystemConfigStore: failed to list %s; configs "
                "will be empty",
                directory or "<root>",
                exc_info=True,
            )
            self._configs = configs
            return

        for name in sorted(names):
            stem = self._match(name)
            if stem is None:
                continue
            rel_path = directory / name if self._directory else PurePath(name)
            try:
                text = await self._fs.read_text(rel_path)
            except FileNotFoundError:
                # File disappeared between listdir and read; tolerate.
                continue
            except Exception:
                logger.warning(
                    "FilesystemConfigStore: failed to read %s",
                    rel_path,
                    exc_info=True,
                )
                continue
            entries = self._parse(rel_path, text)
            if entries is None:
                continue
            configs[stem] = entries
        self._configs = configs

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _match(self, filename: str) -> str | None:
        """Return the lookup key if ``filename`` matches any pattern."""
        import fnmatch

        path = PurePath(filename)
        for pattern in self._patterns:
            if fnmatch.fnmatchcase(filename, pattern):
                pat_path = PurePath(pattern)
                if pat_path.suffix and path.suffix == pat_path.suffix:
                    return path.stem
                return filename
        return None

    @staticmethod
    def _parse(
        rel_path: PurePath, text: str
    ) -> list[dict[str, Any]] | None:
        """Parse ``text`` as YAML, require a top-level list of mappings.

        Returns the list on success, ``None`` on any failure (with a
        warning logged). A single bad file does not block the rest.
        """
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError:
            logger.warning(
                "FilesystemConfigStore: %s is not valid YAML; "
                "skipping",
                rel_path,
                exc_info=True,
            )
            return None
        if data is None:
            return []
        if not isinstance(data, list):
            logger.warning(
                "FilesystemConfigStore: %s top level must be a list, "
                "got %s; skipping",
                rel_path,
                type(data).__name__,
            )
            return None
        entries: list[dict[str, Any]] = []
        for index, item in enumerate(data):
            if not isinstance(item, dict):
                logger.warning(
                    "FilesystemConfigStore: %s entry #%d is not a "
                    "mapping (got %s); skipping entry",
                    rel_path,
                    index,
                    type(item).__name__,
                )
                continue
            entries.append(dict(item))
        return entries
