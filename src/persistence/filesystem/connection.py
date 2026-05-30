"""``FilesystemConnection`` -- CONNECTION level (1) of the persistence layer.

Holds a :class:`FilesystemPort` adapter (DI from level 0) plus a
``root`` path and exposes channel-relative file IO. Channels are
plain strings ("telemetry", "audit", later "sessions/sess-abc",
"firmware/AGENTS"); the connection resolves them against ``root``,
appends the codec's file extension, and delegates IO to the adapter.

Boot order:

    BACKEND   LocalFSAdapter started
    CONNECTION  LocalFSAdapter (...) injected into FilesystemConnection (...)
                FilesystemConnection started

The connection's job is *transport policy*: where things land, who
may write, what extension files get. The actual byte writing is the
adapter's job, the record serialization is the provider's.
"""

from __future__ import annotations

import logging
from pathlib import Path, PurePath

from src.bus.messages import ErrorInfo
from src.components import ComponentCategory, ComponentHealth, RobotComponent
from src.persistence.filesystem.port import AppendWriter, FilesystemPort

logger = logging.getLogger(__name__)


class FilesystemConnection(RobotComponent):
    """Adapter + root + channel-relative path resolution.

    Constructor wiring (DI):

    - ``adapter`` -- a :class:`FilesystemPort` from level 0
      (``LocalFSAdapter`` today, ``S3FSAdapter`` later).
    - ``root`` -- where channel-relative paths land. Typically the
      robot's workspace ``logs/`` directory; the builder injects it.

    The connection does *not* know about record formats or codecs.
    Its API is line-oriented byte IO (via :class:`AppendWriter`),
    not record-oriented.
    """

    component_name = "filesystem"
    component_category = ComponentCategory.CONNECTION
    component_description = (
        "Filesystem connection: holds a FilesystemPort adapter and a "
        "root path, resolves channel-relative paths and exposes "
        "append handles. Boot level 1. The transport-policy seam: "
        "swap the adapter and the same connection serves S3, SMB, ..."
    )

    def __init__(
        self,
        *,
        adapter: FilesystemPort,
        root: str | Path,
    ) -> None:
        if not isinstance(adapter, FilesystemPort):
            raise TypeError(
                "FilesystemConnection.adapter must implement FilesystemPort, "
                f"got {type(adapter).__name__}"
            )
        self._adapter = adapter
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def adapter(self) -> FilesystemPort:
        return self._adapter

    def path_for(self, channel: str, *, suffix: str = "") -> PurePath:
        """Return the path the given ``channel`` resolves to.

        The channel must be a relative name (no leading ``/`` or
        backslash, no path traversal). Sub-folders via ``/`` are
        allowed: ``"sessions/sess-abc"`` resolves to
        ``<root>/sessions/sess-abc<suffix>``.
        """
        if not channel:
            raise ValueError("channel name must be non-empty")
        if channel.startswith(("/", "\\")):
            raise ValueError(
                f"channel {channel!r} must be a relative name, "
                "not an absolute path"
            )
        if any(part == ".." for part in PurePath(channel).parts):
            raise ValueError(
                f"channel {channel!r} must not contain parent-traversal "
                "('..') segments"
            )
        return PurePath(self._root) / f"{channel}{suffix}"

    def resolve(self, rel_path: PurePath) -> PurePath:
        """Resolve ``rel_path`` against ``root`` with traversal guard.

        Companion to :meth:`path_for` for callers that already hold a
        ``PurePath`` (typically a ``SessionStore`` building
        ``sessions/<id>.jsonl`` itself). Empty path resolves to
        ``root`` -- handy for ``listdir(PurePath())``.
        """
        parts = rel_path.parts
        if parts and parts[0] in ("/", "\\"):
            raise ValueError(
                f"path {rel_path!s} must be relative, not absolute"
            )
        if any(part == ".." for part in parts):
            raise ValueError(
                f"path {rel_path!s} must not contain parent-traversal "
                "('..') segments"
            )
        return PurePath(self._root) / rel_path

    async def open_append(
        self,
        channel: str,
        *,
        suffix: str = "",
    ) -> AppendWriter:
        """Open an append handle for the given channel."""
        return await self._adapter.open_append(self.path_for(channel, suffix=suffix))

    async def append_path(self, rel_path: PurePath) -> AppendWriter:
        """Open an append handle for a root-relative path.

        Mirror of :meth:`open_append` for callers that already hold
        a fully formed ``PurePath`` (e.g. ``sessions/<id>.jsonl``)
        and don't want to round-trip through the channel/suffix API.
        """
        return await self._adapter.open_append(self.resolve(rel_path))

    async def write_bytes(self, rel_path: PurePath, data: bytes) -> None:
        """Atomically overwrite ``<root>/rel_path`` with ``data``."""
        await self._adapter.write_bytes(self.resolve(rel_path), data)

    async def read_bytes(self, rel_path: PurePath) -> bytes:
        """Return the bytes at ``<root>/rel_path``.

        Raises :class:`FileNotFoundError` if the path does not exist.
        """
        return await self._adapter.read_bytes(self.resolve(rel_path))

    async def read_text(
        self,
        rel_path: PurePath,
        *,
        encoding: str = "utf-8",
    ) -> str:
        """Return the text at ``<root>/rel_path`` (UTF-8 by default).

        Convenience wrapper around :meth:`read_bytes`. Same error
        semantics: ``FileNotFoundError`` on missing paths. Performs
        universal-newline normalization (``\\r\\n`` / ``\\r`` ->
        ``\\n``) so callers don't have to deal with platform-specific
        line endings -- matches ``Path.read_text``'s implicit
        ``newline=None`` behaviour. Use :meth:`read_bytes` if the
        raw bytes are required.
        """
        raw = await self.read_bytes(rel_path)
        return raw.decode(encoding).replace("\r\n", "\n").replace("\r", "\n")

    async def listdir(
        self,
        rel_path: PurePath = PurePath(),
    ) -> list[str]:
        """Return the leaf names of entries in ``<root>/rel_path``.

        Empty input lists ``root`` itself. Returns ``[]`` for a
        missing directory rather than raising -- a fresh workspace
        has no sessions yet, but that is not an error.
        """
        return await self._adapter.listdir(self.resolve(rel_path))

    async def exists(self, rel_path: PurePath) -> bool:
        """Return whether ``<root>/rel_path`` exists."""
        return await self._adapter.exists(self.resolve(rel_path))

    # ---- RobotComponent lifecycle ------------------------------------------

    async def start(self) -> None:
        """Log the adapter -> connection wiring, then ensure root exists.

        Includes the resolved ``root`` in the log line so two
        connections in the same robot (e.g. a workspace-rooted one
        plus a ``~/.cephix``-rooted one) are distinguishable at a
        glance:

            === Boot Level 1 (CONNECTION) ===
            LocalFSAdapter (xxx) injected into FilesystemConnection [root='<path>'] (yyy)
            FilesystemConnection (yyy) started
        """
        adapter_id = getattr(self._adapter, "instance_id", "")
        logger.info(
            "%s (%s) injected into %s [root=%r] (%s)",
            type(self._adapter).__name__,
            adapter_id,
            type(self).__name__,
            str(self._root),
            self.instance_id,
        )
        await self._adapter.mkdir(PurePath(self._root), parents=True)

    async def stop(self) -> None:
        # The connection holds no handles itself -- providers above
        # own their writers and close them. Adapter is also stateless
        # (its writers are independent objects). Nothing to release.
        return None

    async def health_check(self) -> ComponentHealth:
        """Report ``ok`` when the root is reachable and writable."""
        try:
            writable = await self._adapter.is_writable(PurePath(self._root))
        except Exception as exc:  # pragma: no cover -- defensive
            return ComponentHealth(
                status="warn",
                error=ErrorInfo(
                    code="filesystem_probe_failed",
                    message=f"is_writable({self._root}) raised: {exc}",
                ),
                metadata={"root": str(self._root)},
            )
        if not writable:
            return ComponentHealth(
                status="warn",
                error=ErrorInfo(
                    code="filesystem_root_readonly",
                    message=f"root {self._root} is not writable",
                ),
                metadata={"root": str(self._root)},
            )
        return ComponentHealth(
            status="ok",
            metadata={"root": str(self._root)},
        )
