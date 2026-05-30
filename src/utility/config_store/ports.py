"""Port for the config store.

One interface, :class:`ConfigStorePort`, with two methods:

- :meth:`configs` -- return the list of entries under a key. The
  key is the logical name the consumer asks for ("heartbeats",
  "webhooks", ...); the underlying store decides how to resolve
  it to a source (filename, table, ...). Entries are plain dicts:
  the port stays agnostic to what's being configured so it can be
  reused by any consumer that wants user-editable list data.
- :meth:`refresh` -- re-read every config from the underlying
  storage. Cheap for the file-backed default; intended for a
  future runtime ``config.reload`` command.

``abc.ABC`` (not ``typing.Protocol``) on purpose: the codebase
enforces Dependency Inversion via explicit inheritance.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ConfigStorePort(ABC):
    """Read-side view of user-editable configuration entries."""

    @abstractmethod
    def configs(self, key: str) -> list[dict[str, Any]]:
        """Return the configured entries stored under ``key``.

        Each entry is a plain dict; the caller does its own schema
        validation. Unknown keys return an empty list rather than
        raising -- a robot may not have e.g. a ``heartbeats.yaml``
        and that is normal, not an error.
        """

    @abstractmethod
    async def refresh(self) -> None:
        """Re-read every config from the underlying storage.

        Cheap for the filesystem-backed default (a handful of small
        YAML files); intended for runtime commands that let the
        operator hot-reload configs without restarting the robot.
        Async because concrete stores route IO through an injected
        transport (``FilesystemConnection`` today).
        """
