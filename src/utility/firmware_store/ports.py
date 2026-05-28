"""Port for the firmware store.

One interface, :class:`FirmwareStorePort`, with two methods:

- :meth:`documents` -- mapping of filename (without ``.md``) to
  raw content. Stable insertion order matches sorted filename so
  the assembled system prompt is deterministic.
- :meth:`system_prompt` -- the concatenated system prompt the
  kernel hands to the LLM. The default rendering is one ``##
  <NAME>`` header per non-empty document, separated by blank
  lines.

``abc.ABC`` (not ``typing.Protocol``) on purpose: the codebase
enforces Dependency Inversion via explicit inheritance.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping


class FirmwareStorePort(ABC):
    """Read-side view of the robot's firmware Markdown set."""

    @abstractmethod
    def documents(self) -> Mapping[str, str]:
        """Return ``{name: content}`` mapping, ordered by filename."""

    @abstractmethod
    def system_prompt(self) -> str:
        """Assemble the documents into a single system-prompt string."""

    @abstractmethod
    def refresh(self) -> None:
        """Re-read every document from disk.

        Cheap (a handful of small Markdown files); intended for
        runtime commands that let the operator hot-reload the
        firmware without restarting the robot.
        """
