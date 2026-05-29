"""Filesystem family of the persistence layer.

Three concepts live here:

- :class:`FilesystemPort` -- the abstract driver every filesystem
  backend implements (local FS, S3, SMB, NFS). Verbs are pure
  byte-level / metadata primitives: ``open_append``, ``mkdir``,
  ``exists``, ``is_writable``.
- :class:`LocalFSAdapter` -- backend implementation of
  :class:`FilesystemPort` for the local OS filesystem.
- :class:`FilesystemConnection` -- the connection-level object: holds
  an adapter + a root path, exposes channel-relative file IO and a
  health check.

Adapter / Connection together let a future ``S3FSAdapter`` slot in
without touching the providers above them (level 2).
"""

from src.persistence.filesystem.connection import FilesystemConnection
from src.persistence.filesystem.local_adapter import LocalFSAdapter
from src.persistence.filesystem.port import FilesystemPort

__all__ = [
    "FilesystemConnection",
    "FilesystemPort",
    "LocalFSAdapter",
]
