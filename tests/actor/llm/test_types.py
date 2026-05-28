"""Tests for the LLM actor IO types.

Covers invariants on :class:`ChatMessage`, :class:`LLMUsage`,
:class:`LLMReply`, :class:`LLMDelta`, and :class:`ActorChunk`.
The fields are dataclass-shallow but the validation rules in
:meth:`__post_init__` are load-bearing for the rest of the stack.

Catalog value types (:class:`ModelSpec`, :class:`ModelPricing`) are
tested separately under :mod:`tests.utility.model_catalog.test_types`
since they live with the catalog, not with the actor.
"""

from __future__ import annotations

import pytest

from src.actor.types import ActorResponse
from src.actor.llm.types import (
    ActorChunk,
    ChatMessage,
    LLMDelta,
    LLMReply,
    LLMUsage,
)


# ---------------------------------------------------------------------------
# ChatMessage / LLMUsage / LLMReply / LLMDelta
# ---------------------------------------------------------------------------


def test_chat_message_role_must_be_canonical() -> None:
    with pytest.raises(ValueError, match="role"):
        ChatMessage(role="banana", content="x")
    for role in ("system", "user", "assistant", "tool"):
        ChatMessage(role=role, content="x")


def test_llm_usage_rejects_negative_values() -> None:
    with pytest.raises(ValueError):
        LLMUsage(tokens_in=-1)
    with pytest.raises(ValueError):
        LLMUsage(tokens_out=-1)
    with pytest.raises(ValueError):
        LLMUsage(cost_usd=-0.01)


def test_llm_reply_default_is_empty_stop() -> None:
    reply = LLMReply()
    assert reply.text is None
    assert reply.finish_reason == "stop"
    assert reply.usage.tokens_in == 0


def test_llm_delta_defaults() -> None:
    d = LLMDelta()
    assert d.text == ""
    assert d.finish_reason is None
    assert d.usage is None


# ---------------------------------------------------------------------------
# ActorChunk
# ---------------------------------------------------------------------------


def test_actor_chunk_intermediate_carries_only_delta() -> None:
    chunk = ActorChunk(delta="hello")
    assert chunk.final is False
    assert chunk.response is None


def test_actor_chunk_final_requires_response() -> None:
    with pytest.raises(ValueError, match="final=True"):
        ActorChunk(final=True)


def test_actor_chunk_intermediate_must_not_carry_response() -> None:
    response = ActorResponse(message="x", status="ok")
    with pytest.raises(ValueError, match="final=False"):
        ActorChunk(delta="x", final=False, response=response)


def test_actor_chunk_final_with_response_is_valid() -> None:
    response = ActorResponse(message="hello", status="ok")
    chunk = ActorChunk(delta="", final=True, response=response)
    assert chunk.final is True
    assert chunk.response is response
