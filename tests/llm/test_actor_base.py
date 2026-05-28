"""Tests for :class:`LLMActorBase`.

Three layers exercised:

- Construction-time invariants (must override at least one of
  ``_chat_native`` / ``_stream_native``; identity validation).
- The default adapters (chat-from-stream, stream-from-chat) when
  a driver implements only one direction.
- Context shaping (``actor_context`` -> :class:`ChatMessage` list)
  in all variants the BaseKernel may produce.

Concrete drivers (:class:`MockLLMActor`, future ``LLMActorOpenAI``)
have their own test files.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from src.actor.types import ActorResponse
from src.bus.messages import ErrorInfo
from src.components import ComponentCategory
from src.llm.actor_base import LLMActorBase
from src.llm.types import ActorChunk, ChatMessage, LLMDelta, LLMReply, LLMUsage


# ---------------------------------------------------------------------------
# Test drivers
# ---------------------------------------------------------------------------


class _ChatOnlyDriver(LLMActorBase):
    """Native ``_chat_native``. ``stream`` is synthesised."""

    component_name = "chat-only"

    async def _chat_native(
        self,
        messages: list[ChatMessage],
        *,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMReply:
        del max_output_tokens, temperature
        text = "Yes. " + (messages[-1].content if messages else "")
        return LLMReply(
            text=text,
            finish_reason="stop",
            usage=LLMUsage(tokens_in=10, tokens_out=5, cost_usd=0.001),
            extras={"native": "chat"},
        )


class _StreamOnlyDriver(LLMActorBase):
    """Native ``_stream_native``. ``run`` is synthesised by collection."""

    component_name = "stream-only"

    async def _stream_native(
        self,
        messages: list[ChatMessage],
        *,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[LLMDelta]:
        del max_output_tokens, temperature
        last = messages[-1].content if messages else ""
        for word in ("ack:", last):
            yield LLMDelta(text=word + " ")
        yield LLMDelta(
            text="",
            finish_reason="stop",
            usage=LLMUsage(tokens_in=7, tokens_out=3),
            extras={"native": "stream"},
        )


class _NeitherDriver(LLMActorBase):
    """Subclass that overrides nothing -- construction must fail."""

    component_name = "neither"


class _ExplodingDriver(LLMActorBase):
    """Driver that raises in chat to exercise error translation."""

    component_name = "explodes"

    async def _chat_native(
        self,
        messages: list[ChatMessage],
        *,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMReply:
        del messages, max_output_tokens, temperature
        raise RuntimeError("driver blew up")


class _ExplodingStreamDriver(LLMActorBase):
    """Driver that raises mid-stream after some text was emitted."""

    component_name = "explodes-stream"

    async def _stream_native(
        self,
        messages: list[ChatMessage],
        *,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[LLMDelta]:
        del messages, max_output_tokens, temperature
        yield LLMDelta(text="partial ")
        raise RuntimeError("stream failed")


# ---------------------------------------------------------------------------
# Construction-time invariants
# ---------------------------------------------------------------------------


def test_base_rejects_subclass_without_implementation() -> None:
    with pytest.raises(TypeError, match="must override at least one"):
        _NeitherDriver(model_id="x", provider="y")


def test_base_rejects_empty_identity() -> None:
    with pytest.raises(ValueError, match="model_id"):
        _ChatOnlyDriver(model_id="", provider="y")
    with pytest.raises(ValueError, match="provider"):
        _ChatOnlyDriver(model_id="x", provider="")


def test_base_metadata() -> None:
    assert LLMActorBase.component_category is ComponentCategory.ACTOR


def test_base_default_token_count_is_4_chars_per_token() -> None:
    a = _ChatOnlyDriver(model_id="x", provider="y")
    assert a.count_tokens("") == 0
    assert a.count_tokens("abc") == 1
    assert a.count_tokens("a" * 16) == 4


# ---------------------------------------------------------------------------
# Run / stream paths
# ---------------------------------------------------------------------------


async def test_chat_only_actor_run_returns_response_with_metadata() -> None:
    a = _ChatOnlyDriver(model_id="m", provider="p")
    response = await a.run({"message": "hi"})
    assert isinstance(response, ActorResponse)
    assert response.status == "ok"
    assert response.message == "Yes. hi"
    assert response.metadata["provider"] == "p"
    assert response.metadata["model_id"] == "m"
    assert response.metadata["tokens_in"] == 10
    assert response.metadata["tokens_out"] == 5
    assert response.metadata["cost_usd"] == 0.001
    assert response.metadata["finish_reason"] == "stop"
    assert response.metadata["provider_extras"] == {"native": "chat"}


async def test_chat_only_actor_synthesises_stream() -> None:
    a = _ChatOnlyDriver(model_id="m", provider="p")
    chunks: list[ActorChunk] = []
    async for chunk in a.stream({"message": "hi"}):
        chunks.append(chunk)
    intermediates = [c for c in chunks if not c.final]
    finals = [c for c in chunks if c.final]
    assert len(finals) == 1
    # The synthesised stream emits one piece carrying the full text.
    text = "".join(c.delta for c in intermediates)
    assert text == "Yes. hi"
    assert finals[0].response is not None
    assert finals[0].response.metadata["tokens_in"] == 10


async def test_stream_only_actor_synthesises_run_by_collecting() -> None:
    a = _StreamOnlyDriver(model_id="m", provider="p")
    response = await a.run({"message": "hello"})
    assert response.status == "ok"
    assert response.message == "ack: hello "
    assert response.metadata["tokens_in"] == 7
    assert response.metadata["tokens_out"] == 3
    assert response.metadata["finish_reason"] == "stop"


async def test_stream_only_actor_native_streams_word_chunks() -> None:
    a = _StreamOnlyDriver(model_id="m", provider="p")
    chunks: list[ActorChunk] = []
    async for chunk in a.stream({"message": "world"}):
        chunks.append(chunk)
    intermediates = [c for c in chunks if not c.final]
    text = "".join(c.delta for c in intermediates)
    assert text == "ack: world "


# ---------------------------------------------------------------------------
# Error translation
# ---------------------------------------------------------------------------


async def test_run_returns_error_on_empty_context() -> None:
    a = _ChatOnlyDriver(model_id="m", provider="p")
    response = await a.run({})
    assert response.status == "error"
    assert response.error is not None
    assert response.error.code == "actor.context.empty"
    assert response.metadata["provider"] == "p"
    assert response.metadata["model_id"] == "m"


async def test_run_translates_driver_exception_into_error_response() -> None:
    a = _ExplodingDriver(model_id="m", provider="p")
    response = await a.run({"message": "x"})
    assert response.status == "error"
    assert response.error is not None
    assert response.error.code == "provider.error"
    assert "driver blew up" in response.error.message
    assert response.error.details == {"exception_type": "RuntimeError"}


async def test_stream_propagates_error_as_final_chunk_after_partial_text() -> None:
    a = _ExplodingStreamDriver(model_id="m", provider="p")
    chunks: list[ActorChunk] = []
    async for chunk in a.stream({"message": "x"}):
        chunks.append(chunk)
    # First chunk: partial text. Last chunk: final with error,
    # carrying the partial text already accumulated.
    assert chunks[0].delta == "partial "
    assert chunks[-1].final is True
    assert chunks[-1].response is not None
    assert chunks[-1].response.status == "error"
    assert chunks[-1].response.message == "partial "


# ---------------------------------------------------------------------------
# Context shaping
# ---------------------------------------------------------------------------


async def test_message_curated_from_flat_context() -> None:
    captured: list[list[ChatMessage]] = []

    class _Capturing(LLMActorBase):
        component_name = "cap"

        async def _chat_native(self, messages, **kwargs) -> LLMReply:  # noqa: ANN001
            captured.append(list(messages))
            return LLMReply(text="ok")

    a = _Capturing(model_id="m", provider="p")
    await a.run({"message": "hello"})
    assert len(captured[0]) == 1
    assert captured[0][0].role == "user"
    assert captured[0][0].content == "hello"


async def test_default_system_prompt_prepended() -> None:
    captured: list[list[ChatMessage]] = []

    class _Capturing(LLMActorBase):
        component_name = "cap"

        async def _chat_native(self, messages, **kwargs) -> LLMReply:  # noqa: ANN001
            captured.append(list(messages))
            return LLMReply(text="ok")

    a = _Capturing(
        model_id="m",
        provider="p",
        default_system_prompt="You are helpful.",
    )
    await a.run({"message": "hi"})
    assert captured[0][0] == ChatMessage(
        role="system", content="You are helpful."
    )
    assert captured[0][1] == ChatMessage(role="user", content="hi")


async def test_runtime_system_overrides_default() -> None:
    captured: list[list[ChatMessage]] = []

    class _Capturing(LLMActorBase):
        component_name = "cap"

        async def _chat_native(self, messages, **kwargs) -> LLMReply:  # noqa: ANN001
            captured.append(list(messages))
            return LLMReply(text="ok")

    a = _Capturing(
        model_id="m",
        provider="p",
        default_system_prompt="Default.",
    )
    await a.run({"message": "hi", "system": "Custom."})
    assert captured[0][0].content == "Custom."


async def test_history_is_inserted_between_system_and_user() -> None:
    captured: list[list[ChatMessage]] = []

    class _Capturing(LLMActorBase):
        component_name = "cap"

        async def _chat_native(self, messages, **kwargs) -> LLMReply:  # noqa: ANN001
            captured.append(list(messages))
            return LLMReply(text="ok")

    a = _Capturing(model_id="m", provider="p", default_system_prompt="S")
    await a.run(
        {
            "message": "now",
            "history": [
                {"role": "user", "content": "before"},
                {"role": "assistant", "content": "earlier"},
            ],
        }
    )
    roles = [m.role for m in captured[0]]
    assert roles == ["system", "user", "assistant", "user"]


async def test_explicit_messages_field_used_verbatim() -> None:
    captured: list[list[ChatMessage]] = []

    class _Capturing(LLMActorBase):
        component_name = "cap"

        async def _chat_native(self, messages, **kwargs) -> LLMReply:  # noqa: ANN001
            captured.append(list(messages))
            return LLMReply(text="ok")

    a = _Capturing(
        model_id="m",
        provider="p",
        default_system_prompt="ignored",
    )
    explicit = [
        ChatMessage(role="system", content="Custom"),
        ChatMessage(role="user", content="Hi"),
    ]
    await a.run({"messages": explicit})
    assert captured[0] == explicit


async def test_nested_input_message_shape_matches_basekernel_output() -> None:
    captured: list[list[ChatMessage]] = []

    class _Capturing(LLMActorBase):
        component_name = "cap"

        async def _chat_native(self, messages, **kwargs) -> LLMReply:  # noqa: ANN001
            captured.append(list(messages))
            return LLMReply(text="ok")

    a = _Capturing(model_id="m", provider="p")
    await a.run({"input": {"message": "from-kernel", "principal": "user"}})
    assert captured[0][0].role == "user"
    assert captured[0][0].content == "from-kernel"
