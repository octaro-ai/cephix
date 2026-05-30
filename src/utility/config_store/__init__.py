"""Config store: read-only catalogue of structured configuration entries.

Boot category: :attr:`~src.components.ComponentCategory.UTILITY`
(boot priority 3). Off-bus, file-system-backed, consumed by
components that need user-editable configuration which isn't
appropriate for the robot's main ``robot.yaml`` builder script --
typically lists of entries that can be added/removed/edited
independently of the robot's structure (today: heartbeat
schedules; later: webhook subscriptions, mailbox mappings, ...).

What "config" means here: any YAML file at
``<connection-root>/<directory>/<name>.yaml`` whose top level is
a list. The file stem becomes the lookup key; the entries are
plain dicts the consumer parses itself. ``heartbeats.yaml`` ->
``configs("heartbeats")`` returns a list of dicts.

The store is a sibling of :mod:`src.utility.firmware_store` --
same FilesystemConnection-based mechanics, different output shape
(parsed YAML lists instead of raw text per file).
"""

from src.utility.config_store.ports import ConfigStorePort
from src.utility.config_store.store import FilesystemConfigStore

__all__ = [
    "ConfigStorePort",
    "FilesystemConfigStore",
]
