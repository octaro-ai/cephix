"""Tests for :class:`SessionMessage` (OCF-shaped envelope)."""

from __future__ import annotations

import json

import pytest

from src.actor.llm.types import ChatMessage
from src.utility.session_store import SessionMessage, new_message_id


def test_new_message_id_has_expected_prefix_and_length() -> None:
    mid = new_message_id()
    assert mid.startswith("msg_")
    # ``msg_`` + 12 hex chars.
    assert len(mid) == 4 + 12


def test_new_message_ids_are_unique_across_calls() -> None:
    ids = {new_message_id() for _ in range(100)}
    assert len(ids) == 100


def test_session_message_requires_id_and_created_at() -> None:
    with pytest.raises(ValueError, match="id"):
        SessionMessage(
            id="",
            created_at="2024-01-01T00:00:00Z",
            message=ChatMessage(role="user", content="x"),
        )
    with pytest.raises(ValueError, match="created_at"):
        SessionMessage(
            id="msg_1",
            created_at="",
            message=ChatMessage(role="user", content="x"),
        )


def test_session_message_rejects_negative_duration() -> None:
    with pytest.raises(ValueError, match="duration_ms"):
        SessionMessage(
            id="msg_1",
            created_at="2024-01-01T00:00:00Z",
            message=ChatMessage(role="user", content="x"),
            duration_ms=-1,
        )


def test_to_jsonl_line_uses_ocf_required_floor_only() -> None:
    """Minimum-shape record drops every optional envelope field."""
    msg = SessionMessage(
        id="msg_1",
        created_at="2024-01-01T00:00:00Z",
        message=ChatMessage(role="user", content="hi"),
    )
    line = msg.to_jsonl_line()
    assert line.endswith("\n")
    data = json.loads(line)
    assert set(data) == {"id", "created_at", "message"}
    assert data["message"] == {"role": "user", "content": "hi"}


def test_to_jsonl_line_emits_full_envelope_when_set() -> None:
    msg = SessionMessage(
        id="msg_2",
        created_at="2024-01-01T00:00:00Z",
        parent_id="msg_1",
        model="gpt-4o-mini",
        duration_ms=1234,
        usage={"input": 100, "output": 20, "cost_usd": 0.001},
        message=ChatMessage(role="assistant", content="hello"),
    )
    data = json.loads(msg.to_jsonl_line())
    assert data["parent_id"] == "msg_1"
    assert data["model"] == "gpt-4o-mini"
    assert data["duration_ms"] == 1234
    assert data["usage"] == {"input": 100, "output": 20, "cost_usd": 0.001}
    assert data["message"]["role"] == "assistant"


def test_roundtrip_json_preserves_record() -> None:
    msg = SessionMessage(
        id="msg_a",
        created_at="2024-01-01T00:00:00Z",
        model="gpt-4o-mini",
        duration_ms=500,
        usage={"input": 10, "output": 5},
        message=ChatMessage(role="assistant", content="ok"),
    )
    restored = SessionMessage.from_jsonl_line(msg.to_jsonl_line())
    assert restored == msg


def test_from_jsonl_line_rejects_empty_input() -> None:
    with pytest.raises(ValueError):
        SessionMessage.from_jsonl_line("")
    with pytest.raises(ValueError):
        SessionMessage.from_jsonl_line("   \n")


def test_from_jsonl_line_rejects_missing_message_block() -> None:
    raw = json.dumps({"id": "msg_x", "created_at": "2024-01-01T00:00:00Z"})
    with pytest.raises(ValueError, match="message"):
        SessionMessage.from_jsonl_line(raw)


def test_from_jsonl_line_rejects_non_object_top_level() -> None:
    with pytest.raises(ValueError):
        SessionMessage.from_jsonl_line("[1, 2, 3]")


def test_inner_message_is_openai_wire_shape() -> None:
    """The persisted ``message`` block is what OpenAI expects on the wire.

    Concretely: ``[json.loads(line)["message"] for line in jsonl]``
    can be fed directly into ``chat.completions.create(messages=...)``
    with no mapping. This is the whole point of using OCF.
    """
    user = SessionMessage(
        id="msg_u",
        created_at="2024-01-01T00:00:00Z",
        message=ChatMessage(role="user", content="hi"),
    )
    assistant = SessionMessage(
        id="msg_a",
        created_at="2024-01-01T00:00:00Z",
        message=ChatMessage(role="assistant", content="hi back"),
    )
    on_wire = [
        json.loads(user.to_jsonl_line())["message"],
        json.loads(assistant.to_jsonl_line())["message"],
    ]
    assert on_wire == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hi back"},
    ]
