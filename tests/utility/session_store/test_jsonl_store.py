"""Tests for :class:`JsonlSessionStore`.

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
from src.utility.session_store import (
    JsonlSessionStore,
    SessionMessage,
    SessionStorePort,
    new_message_id,
)


# ---------------------------------------------------------------------------
# Identity + lifecycle
# ---------------------------------------------------------------------------


def test_metadata() -> None:
    assert JsonlSessionStore.component_name == "session-store"
    assert JsonlSessionStore.component_category is ComponentCategory.UTILITY


def test_is_plain_robot_component(tmp_path: Path) -> None:
    """Session store is off-bus -- never inherits :class:`BusComponent`."""
    store = JsonlSessionStore(sessions_dir=tmp_path)
    assert isinstance(store, RobotComponent)
    assert not isinstance(store, BusComponent)
    assert isinstance(store, SessionStorePort)


async def test_lifecycle_is_noop(tmp_path: Path) -> None:
    store = JsonlSessionStore(sessions_dir=tmp_path)
    await store.start()
    await store.stop()


# ---------------------------------------------------------------------------
# Session-id semantics
# ---------------------------------------------------------------------------


def test_new_session_ids_are_unique(tmp_path: Path) -> None:
    store = JsonlSessionStore(sessions_dir=tmp_path)
    ids = {store.new_session() for _ in range(100)}
    assert len(ids) == 100


def test_new_session_returns_well_formed_id(tmp_path: Path) -> None:
    store = JsonlSessionStore(sessions_dir=tmp_path)
    sid = store.new_session()
    assert sid.startswith("sess_")
    assert len(sid) == 5 + 12


def test_open_lazy_creates_session_and_reports_creation(tmp_path: Path) -> None:
    store = JsonlSessionStore(sessions_dir=tmp_path)
    sid = "sess_test1"
    assert store.open(sid) is True
    # Second open of the same id reports "already known".
    assert store.open(sid) is False


def test_open_creates_directory_lazily(tmp_path: Path) -> None:
    """The sessions/ folder is only conjured up on first write."""
    target = tmp_path / "fresh" / "sessions"
    assert not target.exists()
    store = JsonlSessionStore(sessions_dir=target)
    store.open("sess_first")
    assert target.is_dir()


def test_open_rejects_path_traversal(tmp_path: Path) -> None:
    store = JsonlSessionStore(sessions_dir=tmp_path)
    with pytest.raises(ValueError):
        store.open("../escape")
    with pytest.raises(ValueError):
        store.open("nested/path")
    with pytest.raises(ValueError):
        store.open("")
    with pytest.raises(ValueError):
        store.open(".")


def test_messages_on_unknown_session_returns_empty(tmp_path: Path) -> None:
    store = JsonlSessionStore(sessions_dir=tmp_path)
    assert store.messages("sess_ghost") == []


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
    store = JsonlSessionStore(sessions_dir=tmp_path)
    sid = "sess_rt"
    store.open(sid)
    user = _user_msg("hello")
    assistant = _assistant_msg(
        "hi back",
        model="gpt-4o-mini",
        duration_ms=42,
        usage={"input": 1, "output": 2, "cost_usd": 0.001},
    )
    await store.append(sid, user)
    await store.append(sid, assistant)

    history = store.messages(sid)
    assert history == [user, assistant]


async def test_jsonl_lines_are_ocf_message_shaped(tmp_path: Path) -> None:
    """Persisted lines have OCF envelope keys; ``message`` is OpenAI-wire."""
    import json

    store = JsonlSessionStore(sessions_dir=tmp_path)
    sid = "sess_shape"
    await store.append(sid, _user_msg("hi"))

    path = tmp_path / f"{sid}.jsonl"
    contents = path.read_text(encoding="utf-8").splitlines()
    assert len(contents) == 1
    data = json.loads(contents[0])
    assert {"id", "created_at", "message"} <= set(data)
    assert data["message"] == {"role": "user", "content": "hi"}


async def test_messages_limit_returns_tail(tmp_path: Path) -> None:
    store = JsonlSessionStore(sessions_dir=tmp_path)
    sid = "sess_tail"
    msgs = [_user_msg(str(i)) for i in range(5)]
    for m in msgs:
        await store.append(sid, m)

    assert store.messages(sid, limit=None) == msgs
    assert store.messages(sid, limit=0) == []
    assert store.messages(sid, limit=2) == msgs[-2:]
    assert store.messages(sid, limit=10) == msgs


def test_messages_rejects_negative_limit(tmp_path: Path) -> None:
    store = JsonlSessionStore(sessions_dir=tmp_path)
    with pytest.raises(ValueError):
        store.messages("sess_x", limit=-1)


def test_list_sessions_empty_workspace(tmp_path: Path) -> None:
    store = JsonlSessionStore(sessions_dir=tmp_path / "ghost")
    assert store.list_sessions() == []


async def test_list_sessions_returns_summaries(tmp_path: Path) -> None:
    store = JsonlSessionStore(sessions_dir=tmp_path)
    for sid in ("sess_b", "sess_a", "sess_c"):
        await store.append(sid, _user_msg("x"))
    summaries = store.list_sessions()
    assert {s.session_id for s in summaries} == {"sess_a", "sess_b", "sess_c"}
    # All have one message, no title yet.
    for s in summaries:
        assert s.message_count == 1
        assert s.title is None


async def test_list_sessions_orders_most_recent_first(tmp_path: Path) -> None:
    store = JsonlSessionStore(sessions_dir=tmp_path)
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
    order = [s.session_id for s in store.list_sessions()]
    assert order == ["sess_new", "sess_old"]


async def test_summary_derives_fields_from_messages(tmp_path: Path) -> None:
    store = JsonlSessionStore(sessions_dir=tmp_path)
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
    (summary,) = store.list_sessions()
    assert summary.session_id == sid
    assert summary.created_at == "2024-01-01T00:00:00Z"
    assert summary.last_activity_at == "2024-01-01T00:05:00Z"
    assert summary.message_count == 2
    assert summary.model_id == "gpt-4o-mini"


async def test_set_title_appears_in_summary(tmp_path: Path) -> None:
    store = JsonlSessionStore(sessions_dir=tmp_path)
    sid = "sess_title"
    await store.append(sid, _user_msg("x"))
    store.set_title(sid, "Relativitätstheorie")
    (summary,) = store.list_sessions()
    assert summary.title == "Relativitätstheorie"


async def test_set_title_empty_clears_title(tmp_path: Path) -> None:
    store = JsonlSessionStore(sessions_dir=tmp_path)
    sid = "sess_clear"
    await store.append(sid, _user_msg("x"))
    store.set_title(sid, "Temp")
    store.set_title(sid, "")
    (summary,) = store.list_sessions()
    assert summary.title is None


async def test_set_title_persists_across_instances(tmp_path: Path) -> None:
    store = JsonlSessionStore(sessions_dir=tmp_path)
    sid = "sess_persist"
    await store.append(sid, _user_msg("x"))
    store.set_title(sid, "Persisted")

    reopened = JsonlSessionStore(sessions_dir=tmp_path)
    (summary,) = reopened.list_sessions()
    assert summary.title == "Persisted"


def test_set_title_rejects_bad_session_id(tmp_path: Path) -> None:
    store = JsonlSessionStore(sessions_dir=tmp_path)
    with pytest.raises(ValueError):
        store.set_title("../escape", "x")


async def test_corrupt_index_is_ignored(tmp_path: Path) -> None:
    store = JsonlSessionStore(sessions_dir=tmp_path)
    sid = "sess_robust"
    await store.append(sid, _user_msg("x"))
    (tmp_path / "index.json").write_text("{ not json", encoding="utf-8")
    # A broken index must not crash listing; titles just fall back to None.
    (summary,) = store.list_sessions()
    assert summary.session_id == sid
    assert summary.title is None


# ---------------------------------------------------------------------------
# Concurrency + crash tolerance
# ---------------------------------------------------------------------------


async def test_concurrent_appends_to_same_session_are_serialised(
    tmp_path: Path,
) -> None:
    """Per-session lock guarantees in-order writes across concurrent tasks."""
    store = JsonlSessionStore(sessions_dir=tmp_path)
    sid = "sess_concurrent"

    msgs = [_user_msg(f"msg-{i}") for i in range(20)]

    async def _append(m: SessionMessage) -> None:
        await store.append(sid, m)

    await asyncio.gather(*[_append(m) for m in msgs])

    history = store.messages(sid)
    # All 20 messages persisted; no record was clobbered.
    assert sorted(m.id for m in history) == sorted(m.id for m in msgs)
    assert len({m.id for m in history}) == 20


async def test_half_written_tail_line_is_skipped_on_read(tmp_path: Path) -> None:
    """A truncated final line must not poison the whole history."""
    store = JsonlSessionStore(sessions_dir=tmp_path)
    sid = "sess_crash"
    good = _user_msg("clean record")
    await store.append(sid, good)

    # Simulate a crash mid-write by appending a partial line.
    path = tmp_path / f"{sid}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"id": "msg_partial", "created_at": "2024-01')  # no newline, no closing brace

    history = store.messages(sid)
    assert history == [good]


async def test_reopen_after_restart_recovers_history(tmp_path: Path) -> None:
    """The store has no in-memory state; a fresh instance sees prior writes."""
    store_a = JsonlSessionStore(sessions_dir=tmp_path)
    sid = "sess_persist"
    msg = _user_msg("survives restart")
    await store_a.append(sid, msg)
    await store_a.stop()

    store_b = JsonlSessionStore(sessions_dir=tmp_path)
    assert store_b.messages(sid) == [msg]
    # ``open`` on the existing session reports it was already known.
    assert store_b.open(sid) is False


async def test_open_does_not_clobber_existing_session(tmp_path: Path) -> None:
    """Calling open() after data exists must keep the file intact."""
    store = JsonlSessionStore(sessions_dir=tmp_path)
    sid = "sess_keep"
    msg = _user_msg("keep me")
    await store.append(sid, msg)

    # open() on an existing session is a no-op for the file content.
    assert store.open(sid) is False
    assert store.messages(sid) == [msg]
