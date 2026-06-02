"""Firmware store: read-only catalogue of system-prompt documents.

Boot category: :attr:`~src.components.ComponentCategory.UTILITY`
(boot priority 5). Off-bus, file-system-backed, consumed by
chat-style kernels (today :class:`~src.kernel.chat.ChatKernel`)
via reference injection.

What "firmware" means here: a small set of text/Markdown files at
``<connection-root>/<directory>/`` (typically
``<robot_home>/firmware/`` -- ``CONSTITUTION.md``, ``POLICY.md``,
``AGENTS.md``, ...). The store reads every file matching its
configured glob patterns from that directory and concatenates them
into a single system prompt the kernel hands to the LLM at
planning time.

The builder seeds the directory with the packaged starter
templates (``src/firmware/``) on first build via a
**copy-if-missing** step, so a fresh robot has something to read
without us forcing a content choice. User edits survive across
builds because we never overwrite an existing file.
"""

from src.utility.firmware_store.ports import FirmwareStorePort
from src.utility.firmware_store.store import FilesystemFirmwareStore

__all__ = [
    "FilesystemFirmwareStore",
    "FirmwareStorePort",
]
