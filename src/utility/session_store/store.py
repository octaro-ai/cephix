"""``JsonlSessionStore`` -- append-only OCF-compatible JSONL store.

Persistence layout: one file per session at
``<sessions_dir>/<session_id>.jsonl``. Each line is a complete
:class:`~src.utility.session_store.types.SessionMessage` rendered
via :meth:`SessionMessage.to_jsonl_line` -- structurally identical
to an OCF ``message_envelope``, so the export adapter (later) only
has to wrap the lines into a single ``{ocf_version, conversation,
messages}`` document.

Crash tolerance: a half-written tail line (a power cut between
``write`` and ``flush`` is rare but possible) is skipped during
:meth:`messages`. We never reject the whole session for one bad
record.

Concurrency: per-session :class:`asyncio.Lock` ensures appends
from the same loop interleave cleanly. Cross-process safety is
explicitly out of scope -- one robot owns its workspace; another
process touching the same files is a misconfiguration.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path

from src.components import ComponentCategory, RobotComponent
from src.utility.session_store.ports import SessionStorePort
from src.utility.session_store.types import SessionMessage, SessionSummary

logger = logging.getLogger(__name__)

_SESSION_ID_PREFIX = "sess_"
_SESSION_ID_HEX_LENGTH = 12
_INDEX_FILENAME = "index.json"


class JsonlSessionStore(RobotComponent, SessionStorePort):
    """JSONL-backed session store rooted at ``sessions_dir``.

    Constructor:

    - ``sessions_dir`` -- where session files live. The builder
      typically passes ``<workspace>/sessions``. The directory is
      created lazily on first write so a fresh workspace stays
      clutter-free until the first conversation actually starts.

    Lifecycle hooks are near-no-ops: there is no client to bring
    up, no file handle to keep open. The store opens, writes and
    closes per ``append`` call (single line per call -- the OS
    write is the unit of atomicity we rely on for ordered
    appends).
    """

    component_name = "session-store"
    component_category = ComponentCategory.UTILITY
    component_description = (
        "Append-only chat session store. One JSONL file per session "
        "with OCF message_envelope-shaped records. Off-bus utility "
        "consumed by chat-style kernels."
    )

    def __init__(self, *, sessions_dir: str | Path) -> None:
        self._sessions_dir = Path(sessions_dir)
        # Per-session locks are created lazily so we don't pin
        # locks to a non-running loop at construction time.
        self._locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """No-op: the sessions directory is created on first write."""
        return None

    async def stop(self) -> None:
        """Drop the per-session lock cache so a restart is clean."""
        self._locks.clear()

    # ------------------------------------------------------------------
    # SessionStorePort
    # ------------------------------------------------------------------

    def new_session(self) -> str:
        """Mint a fresh session id of the form ``sess_<12-hex>``.

        Collision avoidance: extremely small (48 random bits) but
        still checked against the on-disk inventory to keep the
        guarantee absolute.
        """
        for _ in range(100):
            candidate = (
                f"{_SESSION_ID_PREFIX}{uuid.uuid4().hex[:_SESSION_ID_HEX_LENGTH]}"
            )
            if not self._session_path(candidate).exists():
                return candidate
        # 100 collisions in a row with 48 random bits means something
        # is fundamentally wrong with the entropy source.
        raise RuntimeError(
            "JsonlSessionStore.new_session(): could not find a free "
            "session id after 100 attempts"
        )

    def open(self, session_id: str) -> bool:
        """Lazy-create the session file. Returns ``True`` if just created."""
        self._validate_session_id(session_id)
        path = self._session_path(session_id)
        if path.exists():
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        # ``touch`` is atomic enough for our "exists or not" purposes;
        # we never rely on the empty file being readable as JSONL.
        path.touch()
        return True

    async def append(
        self, session_id: str, message: SessionMessage
    ) -> None:
        """Append one record to ``session_id``. Creates the file if missing."""
        self._validate_session_id(session_id)
        line = message.to_jsonl_line()
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            path = self._session_path(session_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)
                fh.flush()

    def messages(
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
        path = self._session_path(session_id)
        if not path.exists():
            return []
        out: list[SessionMessage] = []
        with path.open("r", encoding="utf-8") as fh:
            for line_no, raw in enumerate(fh, start=1):
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    out.append(SessionMessage.from_jsonl_line(stripped))
                except Exception:
                    # Crash-tolerant tail. Don't fail the kernel
                    # because of one broken record.
                    logger.warning(
                        "JsonlSessionStore: skipping unparseable line "
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

    def list_sessions(self) -> list[SessionSummary]:
        """Build a :class:`SessionSummary` for every persisted session.

        Every field except the title is derived from the session's own
        JSONL so the listing can never drift from the conversation; the
        title is pulled from the ``index.json`` sidecar. Ordered
        most-recently-active first (ties broken by session id) so the
        result drops straight into a chat sidebar.
        """
        if not self._sessions_dir.exists():
            return []
        titles = self._read_index()
        summaries = [
            self._summarize(path.stem, path, titles.get(path.stem))
            for path in self._sessions_dir.glob("*.jsonl")
            if path.is_file()
        ]
        summaries.sort(
            key=lambda s: (s.last_activity_at, s.session_id), reverse=True
        )
        return summaries

    def set_title(self, session_id: str, title: str) -> None:
        """Persist (or clear) the title of ``session_id`` in the index."""
        self._validate_session_id(session_id)
        index = self._read_index()
        if title:
            index[session_id] = title
        else:
            index.pop(session_id, None)
        self._write_index(index)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _summarize(
        self, session_id: str, path: Path, title: str | None
    ) -> SessionSummary:
        """Derive a :class:`SessionSummary` from one session file."""
        messages = self.messages(session_id)
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

    def _index_path(self) -> Path:
        return self._sessions_dir / _INDEX_FILENAME

    def _read_index(self) -> dict[str, str]:
        """Load the ``session_id -> title`` map; tolerate missing/corrupt.

        A missing or unreadable index is treated as empty rather than
        fatal: the index only holds titles, and a session without a
        title is perfectly valid.
        """
        path = self._index_path()
        if not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            logger.warning(
                "JsonlSessionStore: ignoring unreadable session index %s", path
            )
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            str(k): str(v) for k, v in data.items() if isinstance(v, str)
        }

    def _write_index(self, index: dict[str, str]) -> None:
        """Write the title index atomically (temp file + replace)."""
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        path = self._index_path()
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(index, fh, ensure_ascii=False, indent=2)
            fh.flush()
        os.replace(tmp, path)

    def _session_path(self, session_id: str) -> Path:
        return self._sessions_dir / f"{session_id}.jsonl"

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
