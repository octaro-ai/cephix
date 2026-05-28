"""Tests for :class:`BaseLLMProvider` and :class:`MockLLMProvider`.

Two layers exercised:

- :class:`BaseLLMProvider` enforces "implement at least one of
  chat/stream" at construction time and provides default adapters
  for the other direction.
- :class:`MockLLMProvider` is the catalog-aware fake we use for
  every other LLM-stack test. It must validate model identity in
  ``open``, count tokens consistently, and stream word-by-word.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from src.bus import AsyncioBus
from src.llm.metadata_service import ModelMetadataService
from src.llm.providers.base import BaseLLMProvider
from src.llm.providers.mock import MockLLMProvider
from src.llm.types import ChatMessage, LLMDelta, LLMReply, LLMUsage


# ---------------------------------------------------------------------------
# BaseLLMProvider: must override at least one direction
# ---------------------------------------------------------------------------


class _NeitherProvider(BaseLLMProvider):
    """Subclass that overrides nothing -- construction must fail."""


class _ChatOnlyProvider(BaseLLMProvider):
    """Native ``_chat_impl``. ``stream_chat`` is synthesised."""

    async def _chat_impl(
        self,
        messages: list[ChatMessage],
        *,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        extra: dict[str, Any] | None = None,
    ) -> LLMReply:
        del max_output_tokens, temperature, extra
        text = "Yes. " + (messages[-1].content if messages else "")
        return LLMReply(
            text=text,
            finish_reason="stop",
            usage=LLMUsage(tokens_in=10, tokens_out=5),
            extras={"native": "chat"},
        )


class _StreamOnlyProvider(BaseLLMProvider):
    """Native ``_stream_impl``. ``chat`` is synthesised by collection."""

    async def _stream_impl(
        self,
        messages: list[ChatMessage],
        *,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        extra: dict[str, Any] | None = None,
    ) -> AsyncIterator[LLMDelta]:
        del max_output_tokens, temperature, extra
        last = messages[-1].content if messages else ""
        for word in ("ack:", last):
            yield LLMDelta(text=word + " ")
        yield LLMDelta(
            text="",
            finish_reason="stop",
            usage=LLMUsage(tokens_in=7, tokens_out=3),
            extras={"native": "stream"},
        )


def test_base_provider_rejects_subclass_without_implementation() -> None:
    with pytest.raises(TypeError, match="must override at least one"):
        _NeitherProvider(model_id="x", provider="y")


async def test_base_provider_synthesises_stream_from_chat() -> None:
    p = _ChatOnlyProvider(model_id="x", provider="y")
    chunks = []
    async for delta in p.stream_chat([ChatMessage(role="user", content="hi")]):
        chunks.append(delta)
    assert len(chunks) == 1
    assert chunks[0].text == "Yes. hi"
    assert chunks[0].finish_reason == "stop"
    assert chunks[0].usage is not None
    assert chunks[0].usage.tokens_in == 10
    assert chunks[0].extras == {"native": "chat"}


async def test_base_provider_synthesises_chat_from_stream() -> None:
    p = _StreamOnlyProvider(model_id="x", provider="y")
    reply = await p.chat([ChatMessage(role="user", content="hello")])
    assert reply.text == "ack: hello "
    assert reply.finish_reason == "stop"
    assert reply.usage.tokens_in == 7
    assert reply.usage.tokens_out == 3
    assert reply.extras == {"native": "stream"}


def test_base_provider_default_token_count_is_4_chars_per_token() -> None:
    p = _ChatOnlyProvider(model_id="x", provider="y")
    assert p.count_tokens("") == 0
    assert p.count_tokens("abc") == 1
    assert p.count_tokens("a" * 16) == 4


# ---------------------------------------------------------------------------
# MockLLMProvider: catalog-aware behaviour
# ---------------------------------------------------------------------------


async def _start_metadata() -> tuple[AsyncioBus, ModelMetadataService]:
    bus = AsyncioBus()
    await bus.start()
    service = ModelMetadataService()
    await service.start(bus)
    return bus, service


async def test_mock_provider_open_rejects_unknown_model() -> None:
    bus, service = await _start_metadata()
    try:
        provider = MockLLMProvider(
            catalog=service.as_catalog_port(),
            model_id="nope",
            provider="mock",
        )
        with pytest.raises(LookupError, match="not found in catalog"):
            await provider.open()
    finally:
        await service.stop()
        await bus.stop()


async def test_mock_provider_returns_reply_with_real_metadata() -> None:
    bus, service = await _start_metadata()
    try:
        provider = MockLLMProvider(
            catalog=service.as_catalog_port(),
            pricing=service.as_pricing_port(),
            model_id="echo",
            provider="mock",
        )
        await provider.open()
        try:
            reply = await provider.chat(
                [ChatMessage(role="user", content="hello world")],
            )
            assert reply.text == "[mock-reply] hello world"
            assert reply.finish_reason == "stop"
            assert reply.usage.tokens_in == 2  # "hello world" -> 2
            # "[mock-reply] hello world" -> 3 whitespace tokens
            assert reply.usage.tokens_out == 3
            assert reply.usage.cost_usd == 0.0  # mock pricing is zero
            assert reply.extras["mock"] is True
        finally:
            await provider.close()
    finally:
        await service.stop()
        await bus.stop()


async def test_mock_provider_streams_word_by_word_then_finalises() -> None:
    bus, service = await _start_metadata()
    try:
        provider = MockLLMProvider(
            catalog=service.as_catalog_port(),
            pricing=service.as_pricing_port(),
            model_id="echo",
            provider="mock",
            chunk_words=1,
        )
        await provider.open()
        try:
            chunks = []
            async for delta in provider.stream_chat(
                [ChatMessage(role="user", content="ping")]
            ):
                chunks.append(delta)
        finally:
            await provider.close()
    finally:
        await service.stop()
        await bus.stop()

    # The mock prefixes "[mock-reply] " then the user message.
    text = "".join(c.text for c in chunks)
    assert text == "[mock-reply] ping"
    # Last chunk carries finish_reason + usage.
    assert chunks[-1].finish_reason == "stop"
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.tokens_in == 1
    # Intermediate chunks must NOT carry usage.
    for c in chunks[:-1]:
        assert c.usage is None


async def test_mock_provider_chunk_words_groups_tokens() -> None:
    bus, service = await _start_metadata()
    try:
        provider = MockLLMProvider(
            catalog=service.as_catalog_port(),
            model_id="echo",
            provider="mock",
            chunk_words=2,
        )
        await provider.open()
        try:
            chunks = []
            async for delta in provider.stream_chat(
                [ChatMessage(role="user", content="four word user message")]
            ):
                chunks.append(delta)
        finally:
            await provider.close()
    finally:
        await service.stop()
        await bus.stop()

    text = "".join(c.text for c in chunks)
    assert text == "[mock-reply] four word user message"


async def test_mock_provider_count_tokens_matches_whitespace_words() -> None:
    bus, service = await _start_metadata()
    try:
        provider = MockLLMProvider(
            catalog=service.as_catalog_port(),
            model_id="echo",
            provider="mock",
        )
        assert provider.count_tokens("") == 0
        assert provider.count_tokens("hi") == 1
        assert provider.count_tokens("one two three four") == 4
    finally:
        await service.stop()
        await bus.stop()


async def test_mock_provider_truncates_to_max_output_tokens() -> None:
    bus, service = await _start_metadata()
    try:
        provider = MockLLMProvider(
            catalog=service.as_catalog_port(),
            model_id="echo",
            provider="mock",
            responder=lambda _msgs: "one two three four five six",
        )
        await provider.open()
        try:
            reply = await provider.chat(
                [ChatMessage(role="user", content="x")],
                max_output_tokens=3,
            )
            assert reply.text == "one two three"
        finally:
            await provider.close()
    finally:
        await service.stop()
        await bus.stop()


async def test_mock_provider_pricing_zero_for_anthropic_haiku() -> None:
    """Cost computation on a real-pricing model.

    Sanity-check the pricing math against the bundled anthropic
    haiku entry. ``input_cost_per_token=0.0000008``,
    ``output_cost_per_token=0.000004``.
    """
    bus, service = await _start_metadata()
    try:
        provider = MockLLMProvider(
            catalog=service.as_catalog_port(),
            pricing=service.as_pricing_port(),
            model_id="claude-3-5-haiku-20241022",
            provider="anthropic",
        )
        await provider.open()
        try:
            reply = await provider.chat(
                [ChatMessage(role="user", content="hi there")]
            )
            # tokens_in = 2 ("hi there"), tokens_out = 3
            # ("[mock-reply] hi there" -> 3 whitespace tokens)
            expected = 2 * 0.0000008 + 3 * 0.000004
            assert reply.usage.cost_usd == pytest.approx(expected)
        finally:
            await provider.close()
    finally:
        await service.stop()
        await bus.stop()
