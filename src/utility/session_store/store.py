"""``FilesystemSessionStore`` -- append-only OCF-compatible JSONL store.

Persistence layout (rooted at ``FilesystemConnection.root``):

    sessions/<session_id>.jsonl   -- one JSONL stream per session
    sessions/index.json           -- title sidecar (atomic snapshot)

Each message line is a complete
:class:`~src.utility.session_store.types.SessionMessage` rendered
via :meth:`SessionMessage.to_jsonl_line` -- structurally identical
to an OCF ``message_envelope``, so the export adapter (later) only
has to wrap the lines into a single ``{ocf_version, conversation,
messages}`` document.

DI: the store takes a :class:`FilesystemConnection` (no path / no
adapter; the connection holds the root and routes byte-level IO
to the adapter). The same store class therefore runs unchanged on
a local FS today and on S3/SMB tomorrow -- swap the adapter at
boot level 0.

Crash tolerance: a half-written tail line (a power cut between
``write`` and ``flush`` is rare but possible) is skipped during
:meth:`messages`. We never reject the whole session for one bad
record. The title index is written atomically (temp file + rename
in the connection) so a partial snapshot is impossible.

Concurrency: per-session :class:`asyncio.Lock` ensures appends
from the same loop interleave cleanly; the writer cache reuses
a single :class:`AppendWriter` per session for the whole process
lifetime. Cross-process safety is explicitly out of scope -- one
robot owns its home, another process touching the same files
is a misconfiguration.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import PurePath

from src.components import ComponentCategory, RobotComponent
from src.persistence.filesystem.connection import FilesystemConnection
from src.persistence.filesystem.port import AppendWriter
from src.utility.session_store.ports import SessionStorePort
from src.utility.session_store.types import SessionMessage, SessionSummary

logger = logging.getLogger(__name__)

_SESSION_ID_PREFIX = "sess_"
_SESSION_ID_HEX_LENGTH = 12
_DEFAULT_SESSIONS_DIRECTORY = "sessions"
_INDEX_FILENAME = "index.json"


class FilesystemSessionStore(RobotComponent, SessionStorePort):
    """Filesystem-backed session store on top of a :class:`FilesystemConnection`.

    Constructor wiring (DI):

    - ``connection`` -- a :class:`FilesystemConnection` from level
      1. The store does not know or care which adapter sits behind
      it; it only uses connection-level verbs (``append_path``,
      ``write_bytes``, ``read_text``, ``listdir``).

    Lifecycle: no client to start, no socket to open. ``stop``
    closes every cached writer and drops the lock cache so a
    fresh start can rebuild them cleanly.
    """

    component_name = "session-store"
    component_category = ComponentCategory.UTILITY
    component_description = (
        "Append-only chat session store. One JSONL file per session "
        "with OCF message_envelope-shaped records, plus an atomic "
        "title index. Off-bus utility consumed by chat-style kernels. "
        "Storage routed through an injected FilesystemConnection; the "
        "store keeps its own bucket below the connection root, "
        "configurable via ``directory:`` (default ``sessions/``)."
    )

    def __init__(
        self,
        *,
        connection: FilesystemConnection,
        directory: str = _DEFAULT_SESSIONS_DIRECTORY,
    ) -> None:
        if not isinstance(connection, FilesystemConnection):
            raise TypeError(
                "FilesystemSessionStore.connection must be a "
                "FilesystemConnection, got "
                f"{type(connection).__name__}"
            )
        if not isinstance(directory, str):
            raise TypeError(
                "FilesystemSessionStore.directory must be a string"
            )
        self._fs = connection
        # The bucket inside the shared connection root where this
        # store lives. Empty string means "sit directly under root",
        # which keeps tests and small robot homes simple. The default
        # ``sessions/`` keeps the store next to the event provider's
        # ``logs/`` without collisions.
        self._directory = directory.strip("/").strip("\\")
        self._writers: dict[str, AppendWriter] = {}
        # Per-session locks are created lazily so we don't pin
        # locks to a non-running loop at construction time.
        self._locks: dict[str, asyncio.Lock] = {}
        # The title index is a small snapshot. Serialize updates
        # so two ``set_title`` calls don't race on read-modify-write.
        self._index_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Surface the connection -> store wiring; no IO required."""
        connection_id = getattr(self._fs, "instance_id", "")
        logger.info(
            "%s (%s) injected into %s (%s)",
            type(self._fs).__name__,
            connection_id,
            type(self).__name__,
            self.instance_id,
        )

    async def _stop(self) -> None:
        """Close cached writers and drop lock/writer caches."""
        for sid, writer in list(self._writers.items()):
            try:
                await writer.close()
            except Exception:
                logger.exception(
                    "FilesystemSessionStore: failed to close writer for %s",
                    sid,
                )
        self._writers.clear()
        self._locks.clear()

    # ------------------------------------------------------------------
    # SessionStorePort
    # ------------------------------------------------------------------

    async def new_session(self) -> str:
        """Mint a fresh session id of the form ``sess_<12-hex>``.

        Collision avoidance: extremely small (48 random bits) but
        still checked against the on-disk inventory to keep the
        guarantee absolute.
        """
        for _ in range(100):
            candidate = (
                f"{_SESSION_ID_PREFIX}{uuid.uuid4().hex[:_SESSION_ID_HEX_LENGTH]}"
            )
            if not await self._fs.exists(self._session_rel(candidate)):
                return candidate
        # 100 collisions in a row with 48 random bits means something
        # is fundamentally wrong with the entropy source.
        raise RuntimeError(
            "FilesystemSessionStore.new_session(): could not find a free "
            "session id after 100 attempts"
        )

    async def open(self, session_id: str) -> bool:
        """Lazy-create the session file. Returns ``True`` if just created."""
        self._validate_session_id(session_id)
        if await self._fs.exists(self._session_rel(session_id)):
            return False
        # An empty JSONL is a valid empty session. ``write_bytes`` is
        # atomic (temp + replace), so a parallel ``new_session`` can
        # never see a half-created file.
        await self._fs.write_bytes(self._session_rel(session_id), b"")
        return True

    async def append(
        self, session_id: str, message: SessionMessage
    ) -> None:
        """Append one record to ``session_id``. Creates the file if missing."""
        self._validate_session_id(session_id)
        line = message.to_jsonl_line().rstrip("\n")
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            writer = self._writers.get(session_id)
            if writer is None:
                writer = await self._fs.append_path(
                    self._session_rel(session_id)
                )
                self._writers[session_id] = writer
            await writer.write_line(line)

    async def messages(
        self, session_id: str, limit: int | None = None
    ) -> list[SessionMessage]:
        """Read back the persisted records, oldest first.

        Unknown / empty sessions return ``[]``. A line that fails
        to parse is logged at WARNING level and skipped -- a
        half-written tail must not poison the entire history.
        """
        self._validate_session_id(session_id)
        if limit is not None and limit < 0:
            raise ValueError("limit must be >= 0 or None")
        # Flush any in-process writer so a reader sees just-written
        # records (the OS buffer is otherwise in front of pathlib).
        writer = self._writers.get(session_id)
        if writer is not None:
            await writer.flush()
        try:
            text = await self._fs.read_text(self._session_rel(session_id))
        except FileNotFoundError:
            return []
        out: list[SessionMessage] = []
        for line_no, raw in enumerate(text.splitlines(), start=1):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                out.append(SessionMessage.from_jsonl_line(stripped))
            except Exception:
                logger.warning(
                    "FilesystemSessionStore: skipping unparseable line "
                    "%d in session %s",
                    line_no,
                    session_id,
                )
        if limit is not None:
            if limit == 0:
                return []
            if limit < len(out):
                out = out[-limit:]
        return out

    async def list_sessions(self) -> list[SessionSummary]:
        """Build a :class:`SessionSummary` for every persisted session.

        Every field except the title is derived from the session's own
        JSONL so the listing can never drift from the conversation;
        the title is pulled from the ``index.json`` sidecar. Ordered
        most-recently-active first (ties broken by session id) so the
        result drops straight into a chat sidebar.
        """
        names = await self._fs.listdir(PurePath(self._directory))
        titles = await self._read_index()
        summaries: list[SessionSummary] = []
        for name in names:
            if not name.endswith(".jsonl"):
                continue
            session_id = name[: -len(".jsonl")]
            summaries.append(
                await self._summarize(session_id, titles.get(session_id))
            )
        summaries.sort(
            key=lambda s: (s.last_activity_at, s.session_id), reverse=True
        )
        return summaries

    async def set_title(self, session_id: str, title: str) -> None:
        """Persist (or clear) the title of ``session_id`` in the index."""
        self._validate_session_id(session_id)
        async with self._index_lock:
            index = await self._read_index()
            if title:
                index[session_id] = title
            else:
                index.pop(session_id, None)
            await self._write_index(index)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _summarize(
        self, session_id: str, title: str | None
    ) -> SessionSummary:
        """Derive a :class:`SessionSummary` from one session file."""
        messages = await self.messages(session_id)
        if not messages:
            return SessionSummary(session_id=session_id, title=title)
        model_id: str | None = None
        for msg in reversed(messages):
            if msg.model:
                model_id = msg.model
                break
        return SessionSummary(
            session_id=session_id,
            title=title,
            created_at=messages[0].created_at,
            last_activity_at=messages[-1].created_at,
            message_count=len(messages),
            model_id=model_id,
        )

    def _session_rel(self, session_id: str) -> PurePath:
        leaf = f"{session_id}.jsonl"
        return PurePath(self._directory) / leaf if self._directory else PurePath(leaf)

    def _index_rel(self) -> PurePath:
        return (
            PurePath(self._directory) / _INDEX_FILENAME
            if self._directory
            else PurePath(_INDEX_FILENAME)
        )

    async def _read_index(self) -> dict[str, str]:
        """Load the ``session_id -> title`` map; tolerate missing/corrupt.

        A missing or unreadable index is treated as empty rather than
        fatal: the index only holds titles, and a session without a
        title is perfectly valid.
        """
        try:
            text = await self._fs.read_text(self._index_rel())
        except FileNotFoundError:
            return {}
        try:
            data = json.loads(text)
        except ValueError:
            logger.warning(
                "FilesystemSessionStore: ignoring unreadable session index"
            )
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            str(k): str(v) for k, v in data.items() if isinstance(v, str)
        }

    async def _write_index(self, index: dict[str, str]) -> None:
        """Write the title index atomically via the connection."""
        payload = json.dumps(index, ensure_ascii=False, indent=2).encode("utf-8")
        await self._fs.write_bytes(self._index_rel(), payload)

    @staticmethod
    def _validate_session_id(session_id: str) -> None:
        if not session_id:
            raise ValueError("session_id must be a non-empty string")
        # Defensive: reject path separators so a malicious payload
        # can't escape the sessions directory. session_ids are
        # opaque tokens, not user-controlled text.
        if "/" in session_id or "\\" in session_id or session_id in (".", ".."):
            raise ValueError(
                f"session_id contains path separators or is reserved: "
                f"{session_id!r}"
            )
