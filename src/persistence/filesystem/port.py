"""``FilesystemPort`` -- abstract filesystem driver.

The narrowest API every filesystem backend has to provide so the
layers above (``FilesystemConnection`` -> ``FilesystemEventStreamProvider``
etc.) stay backend-agnostic. Today only :class:`LocalFSAdapter`
implements it; later ``S3FSAdapter``, ``SmbAdapter`` and
``InMemoryFSAdapter`` (test double) join without changing a single
line in the layers above.

Verbs are deliberately byte-level / metadata-only -- there is no
"channel" or "record" concept here. The connection layer adds root
+ relative-path semantics; the provider layer adds the record
serialization.
"""

from __future__ import annotations

from pathlib import PurePath
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class FilesystemPort(Protocol):
    """Backend-level filesystem operations cephix needs.

    The signature works with both ``pathlib.PurePath`` (local) and
    arbitrary string-keyed remotes (S3, SMB, ...). Concrete adapters
    are free to interpret the path argument in whatever way matches
    their backend, as long as the same path round-trips.
    """

    async def open_append(self, path: PurePath) -> "AppendWriter":
        """Return a handle that appends bytes/strings to ``path``.

        The handle is created if the underlying object does not
        exist yet. The returned object satisfies the
        :class:`AppendWriter` protocol below: ``write_line``,
        ``flush``, ``close``.
        """

    async def mkdir(self, path: PurePath, *, parents: bool = True) -> None:
        """Ensure the directory at ``path`` exists.

        ``parents=True`` makes the operation idempotent for nested
        paths (the default; matches ``Path.mkdir(parents=True,
        exist_ok=True)``).
        """

    async def exists(self, path: PurePath) -> bool:
        """Return whether ``path`` points to an existing object."""

    async def is_writable(self, path: PurePath) -> bool:
        """Return whether ``path`` (or its parent) can be written to.

        Used by ``FilesystemConnection.health_check`` to surface
        permission or readonly issues to the bus before they bite
        on first write.
        """


@runtime_checkable
class AppendWriter(Protocol):
    """Append-only stream handle returned by :meth:`FilesystemPort.open_append`.

    Line-oriented on purpose: the codec layer above writes one
    serialized record per ``write_line`` call. Backends that don't
    have line semantics natively (S3 multipart upload, DB blob,
    ...) buffer internally and flush on demand.
    """

    async def write_line(self, line: str) -> None:
        """Append ``line`` plus a trailing newline."""

    async def flush(self) -> None:
        """Make every appended line durable."""

    async def close(self) -> None:
        """Release every resource held by the writer. Idempotent."""

    @property
    def path(self) -> Any:
        """Return the path/key the writer targets. For logging only."""
