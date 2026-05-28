"""Tests for the LLM value types.

Covers invariants on :class:`ModelSpec`, :class:`ModelPricing`,
:class:`ChatMessage`, :class:`LLMUsage`, :class:`LLMReply`,
:class:`LLMDelta`, and :class:`ActorChunk`. The fields are
dataclass-shallow but the validation rules in :meth:`__post_init__`
are load-bearing for the rest of the stack.
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
    ModelPricing,
    ModelSpec,
)


# ---------------------------------------------------------------------------
# ModelSpec
# ---------------------------------------------------------------------------


def test_model_spec_minimal() -> None:
    spec = ModelSpec(
        model_id="gpt-5",
        provider="openai",
        context_window_tokens=272_000,
        max_output_tokens=128_000,
    )
    assert spec.model_id == "gpt-5"
    assert spec.supports_function_calling is False
    assert spec.supports_system_messages is True
    assert spec.extras == {}


def test_model_spec_rejects_empty_id_or_provider() -> None:
    with pytest.raises(ValueError, match="model_id"):
        ModelSpec(
            model_id="",
            provider="openai",
            context_window_tokens=1,
            max_output_tokens=1,
        )
    with pytest.raises(ValueError, match="provider"):
        ModelSpec(
            model_id="x",
            provider="",
            context_window_tokens=1,
            max_output_tokens=1,
        )


def test_model_spec_rejects_negative_limits() -> None:
    with pytest.raises(ValueError, match="context_window"):
        ModelSpec(
            model_id="x",
            provider="y",
            context_window_tokens=-1,
            max_output_tokens=1,
        )
    with pytest.raises(ValueError, match="max_output"):
        ModelSpec(
            model_id="x",
            provider="y",
            context_window_tokens=1,
            max_output_tokens=-1,
        )


# ---------------------------------------------------------------------------
# ModelPricing
# ---------------------------------------------------------------------------


def test_model_pricing_zero_cost_is_valid_for_local_models() -> None:
    pricing = ModelPricing(model_id="llama3.2", provider="ollama")
    assert pricing.input_cost_per_token == 0.0
    assert pricing.output_cost_per_token == 0.0


def test_model_pricing_rejects_negative_costs() -> None:
    with pytest.raises(ValueError, match="input_cost_per_token"):
        ModelPricing(model_id="x", provider="y", input_cost_per_token=-1.0)
    with pytest.raises(ValueError, match="output_cost_per_token"):
        ModelPricing(model_id="x", provider="y", output_cost_per_token=-0.1)


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
