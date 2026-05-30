"""Tests for :class:`FilesystemSessionStore`.

Three groups:

- session-id semantics (``new_session`` uniqueness, ``open``
  lazy-create return value, validation);
- read/write round-trips (single message, many messages, limit
  windowing, ``list_sessions``);
- concurrency + crash-tolerance (per-session lock serialises
  appends; a half-written tail line is skipped on read).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.actor.llm.types import ChatMessage
from src.components import BusComponent, ComponentCategory, RobotComponent
from src.persistence.filesystem.connection import FilesystemConnection
from src.persistence.filesystem.local_adapter import LocalFSAdapter
from src.utility.session_store import (
    FilesystemSessionStore,
    SessionMessage,
    SessionStorePort,
    new_message_id,
)


def _make_store(root: Path) -> FilesystemSessionStore:
    """Build a :class:`FilesystemSessionStore` rooted at ``root``.

    Adapter and connection are constructed inline because the
    persistence-layer integration is exercised end-to-end through the
    builder elsewhere; here we just need the store under realistic
    wiring.
    """
    connection = FilesystemConnection(adapter=LocalFSAdapter(), root=root)
    return FilesystemSessionStore(connection=connection)


# ---------------------------------------------------------------------------
# Identity + lifecycle
# ---------------------------------------------------------------------------


def test_metadata() -> None:
    assert FilesystemSessionStore.component_name == "session-store"
    assert FilesystemSessionStore.component_category is ComponentCategory.UTILITY


def test_is_plain_robot_component(tmp_path: Path) -> None:
    """Session store is off-bus -- never inherits :class:`BusComponent`."""
    store = _make_store(tmp_path)
    assert isinstance(store, RobotComponent)
    assert not isinstance(store, BusComponent)
    assert isinstance(store, SessionStorePort)


async def test_lifecycle_logs_di_and_closes_cleanly(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    await store.start()
    await store.stop()


# ---------------------------------------------------------------------------
# Session-id semantics
# ---------------------------------------------------------------------------


async def test_new_session_ids_are_unique(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    ids = {await store.new_session() for _ in range(100)}
    assert len(ids) == 100


async def test_new_session_returns_well_formed_id(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    sid = await store.new_session()
    assert sid.startswith("sess_")
    assert len(sid) == 5 + 12


async def test_open_lazy_creates_session_and_reports_creation(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    sid = "sess_test1"
    assert await store.open(sid) is True
    assert await store.open(sid) is False


async def test_open_creates_directory_lazily(tmp_path: Path) -> None:
    """The sessions/ folder is conjured up on first write."""
    root = tmp_path / "fresh"
    assert not root.exists()
    store = _make_store(root)
    await store.open("sess_first")
    assert (root / "sessions").is_dir()


async def test_open_rejects_path_traversal(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    with pytest.raises(ValueError):
        await store.open("../escape")
    with pytest.raises(ValueError):
        await store.open("nested/path")
    with pytest.raises(ValueError):
        await store.open("")
    with pytest.raises(ValueError):
        await store.open(".")


async def test_messages_on_unknown_session_returns_empty(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    assert await store.messages("sess_ghost") == []


# ---------------------------------------------------------------------------
# Append / read round-trip
# ---------------------------------------------------------------------------


def _user_msg(content: str) -> SessionMessage:
    return SessionMessage(
        id=new_message_id(),
        created_at="2024-01-01T00:00:00Z",
        message=ChatMessage(role="user", content=content),
    )


def _assistant_msg(content: str, **envelope) -> SessionMessage:
    return SessionMessage(
        id=new_message_id(),
        created_at="2024-01-01T00:00:00Z",
        message=ChatMessage(role="assistant", content=content),
        **envelope,
    )


async def test_append_and_read_back(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    sid = "sess_rt"
    await store.open(sid)
    user = _user_msg("hello")
    assistant = _assistant_msg(
        "hi back",
        model="gpt-4o-mini",
        duration_ms=42,
        usage={"input": 1, "output": 2, "cost_usd": 0.001},
    )
    await store.append(sid, user)
    await store.append(sid, assistant)

    history = await store.messages(sid)
    assert history == [user, assistant]


async def test_jsonl_lines_are_ocf_message_shaped(tmp_path: Path) -> None:
    """Persisted lines have OCF envelope keys; ``message`` is OpenAI-wire."""
    import json

    store = _make_store(tmp_path)
    sid = "sess_shape"
    await store.append(sid, _user_msg("hi"))
    await store.stop()  # flush + close cached writer

    path = tmp_path / "sessions" / f"{sid}.jsonl"
    contents = path.read_text(encoding="utf-8").splitlines()
    assert len(contents) == 1
    data = json.loads(contents[0])
    assert {"id", "created_at", "message"} <= set(data)
    assert data["message"] == {"role": "user", "content": "hi"}


async def test_messages_limit_returns_tail(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    sid = "sess_tail"
    msgs = [_user_msg(str(i)) for i in range(5)]
    for m in msgs:
        await store.append(sid, m)

    assert await store.messages(sid, limit=None) == msgs
    assert await store.messages(sid, limit=0) == []
    assert await store.messages(sid, limit=2) == msgs[-2:]
    assert await store.messages(sid, limit=10) == msgs


async def test_messages_rejects_negative_limit(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    with pytest.raises(ValueError):
        await store.messages("sess_x", limit=-1)


async def test_list_sessions_empty_workspace(tmp_path: Path) -> None:
    store = _make_store(tmp_path / "ghost")
    assert await store.list_sessions() == []


async def test_list_sessions_returns_summaries(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    for sid in ("sess_b", "sess_a", "sess_c"):
        await store.append(sid, _user_msg("x"))
    summaries = await store.list_sessions()
    assert {s.session_id for s in summaries} == {"sess_a", "sess_b", "sess_c"}
    for s in summaries:
        assert s.message_count == 1
        assert s.title is None


async def test_list_sessions_orders_most_recent_first(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    await store.append(
        "sess_old",
        SessionMessage(
            id=new_message_id(),
            created_at="2024-01-01T00:00:00Z",
            message=ChatMessage(role="user", content="x"),
        ),
    )
    await store.append(
        "sess_new",
        SessionMessage(
            id=new_message_id(),
            created_at="2024-06-01T00:00:00Z",
            message=ChatMessage(role="user", content="y"),
        ),
    )
    order = [s.session_id for s in await store.list_sessions()]
    assert order == ["sess_new", "sess_old"]


async def test_summary_derives_fields_from_messages(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    sid = "sess_sum"
    await store.append(
        sid,
        SessionMessage(
            id=new_message_id(),
            created_at="2024-01-01T00:00:00Z",
            message=ChatMessage(role="user", content="hi"),
        ),
    )
    await store.append(
        sid,
        SessionMessage(
            id=new_message_id(),
            created_at="2024-01-01T00:05:00Z",
            message=ChatMessage(role="assistant", content="hello"),
            model="gpt-4o-mini",
        ),
    )
    (summary,) = await store.list_sessions()
    assert summary.session_id == sid
    assert summary.created_at == "2024-01-01T00:00:00Z"
    assert summary.last_activity_at == "2024-01-01T00:05:00Z"
    assert summary.message_count == 2
    assert summary.model_id == "gpt-4o-mini"


async def test_set_title_appears_in_summary(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    sid = "sess_title"
    await store.append(sid, _user_msg("x"))
    await store.set_title(sid, "Relativitätstheorie")
    (summary,) = await store.list_sessions()
    assert summary.title == "Relativitätstheorie"


async def test_set_title_empty_clears_title(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    sid = "sess_clear"
    await store.append(sid, _user_msg("x"))
    await store.set_title(sid, "Temp")
    await store.set_title(sid, "")
    (summary,) = await store.list_sessions()
    assert summary.title is None


async def test_set_title_persists_across_instances(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    sid = "sess_persist"
    await store.append(sid, _user_msg("x"))
    await store.set_title(sid, "Persisted")
    await store.stop()

    reopened = _make_store(tmp_path)
    (summary,) = await reopened.list_sessions()
    assert summary.title == "Persisted"


async def test_set_title_rejects_bad_session_id(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    with pytest.raises(ValueError):
        await store.set_title("../escape", "x")


async def test_corrupt_index_is_ignored(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    sid = "sess_robust"
    await store.append(sid, _user_msg("x"))
    # Hand-write a broken index next to the session file.
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / "index.json").write_text("{ not json", encoding="utf-8")
    # A broken index must not crash listing; titles just fall back to None.
    (summary,) = await store.list_sessions()
    assert summary.session_id == sid
    assert summary.title is None


# ---------------------------------------------------------------------------
# Concurrency + crash tolerance
# ---------------------------------------------------------------------------


async def test_concurrent_appends_to_same_session_are_serialised(
    tmp_path: Path,
) -> None:
    """Per-session lock guarantees in-order writes across concurrent tasks."""
    store = _make_store(tmp_path)
    sid = "sess_concurrent"

    msgs = [_user_msg(f"msg-{i}") for i in range(20)]

    async def _append(m: SessionMessage) -> None:
        await store.append(sid, m)

    await asyncio.gather(*[_append(m) for m in msgs])

    history = await store.messages(sid)
    # All 20 messages persisted; no record was clobbered.
    assert sorted(m.id for m in history) == sorted(m.id for m in msgs)
    assert len({m.id for m in history}) == 20


async def test_half_written_tail_line_is_skipped_on_read(
    tmp_path: Path,
) -> None:
    """A truncated final line must not poison the whole history."""
    store = _make_store(tmp_path)
    sid = "sess_crash"
    good = _user_msg("clean record")
    await store.append(sid, good)
    # Drop the cached writer so the external append below isn't
    # racing the in-process file handle. ``messages`` re-opens via
    # the connection.
    await store.stop()

    path = tmp_path / "sessions" / f"{sid}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"id": "msg_partial", "created_at": "2024-01')

    reopened = _make_store(tmp_path)
    history = await reopened.messages(sid)
    assert history == [good]


async def test_reopen_after_restart_recovers_history(tmp_path: Path) -> None:
    """The store has no in-memory state; a fresh instance sees prior writes."""
    store_a = _make_store(tmp_path)
    sid = "sess_persist"
    msg = _user_msg("survives restart")
    await store_a.append(sid, msg)
    await store_a.stop()

    store_b = _make_store(tmp_path)
    assert await store_b.messages(sid) == [msg]
    # ``open`` on the existing session reports it was already known.
    assert await store_b.open(sid) is False


async def test_open_does_not_clobber_existing_session(tmp_path: Path) -> None:
    """Calling open() after data exists must keep the file intact."""
    store = _make_store(tmp_path)
    sid = "sess_keep"
    msg = _user_msg("keep me")
    await store.append(sid, msg)

    assert await store.open(sid) is False
    assert await store.messages(sid) == [msg]
