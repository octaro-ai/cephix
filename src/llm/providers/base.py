"""Reusable scaffolding for :class:`~src.llm.ports.LLMProviderPort`.

Concrete providers inherit :class:`BaseLLMProvider` and override
*at least one* of :meth:`_chat_impl` and :meth:`_stream_impl`. The
base class fills in the missing direction:

- a streaming-only provider gets :meth:`chat` for free as
  *collect-stream-into-reply*;
- a non-streaming provider gets :meth:`stream_chat` for free as
  *yield-single-final-delta*.

This keeps native efficiency where the SDK supports it (OpenAI's
SSE streaming, Anthropic's message streaming) and shields
roundtrip-only providers (a hypothetical batch API, a local
sync model) from having to fake an async iterator manually.

The default ``count_tokens`` implementation is the 4-character
heuristic. Concrete providers with real tokenizers (OpenAI tiktoken,
Anthropic's count-tokens API, ...) override it.

Why not a :class:`~src.components.RobotComponent`: providers are
managed *by the actor* (the actor opens them in ``start``, closes
in ``stop``), not by the robot's lifecycle directly. The actor is
the :class:`RobotComponent`. The provider is an injectible
dependency.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from src.llm.ports import LLMProviderPort
from src.llm.types import ChatMessage, LLMDelta, LLMReply, LLMUsage

logger = logging.getLogger(__name__)


class BaseLLMProvider(LLMProviderPort):
    """Scaffolding for chat and stream symmetry.

    Subclasses override **at least one** of:

    - :meth:`_chat_impl` -- single-shot path; returns a final
      :class:`LLMReply`.
    - :meth:`_stream_impl` -- streaming path; yields
      :class:`LLMDelta` chunks.

    The other direction is provided by the base. Subclasses that
    have efficient native implementations of *both* override both.

    A subclass that overrides neither raises :class:`TypeError` at
    construction time -- otherwise the default implementations
    would recurse forever.
    """

    def __init__(self, *, model_id: str, provider: str) -> None:
        self._model_id = model_id
        self._provider = provider
        self._opened = False

        cls = type(self)
        chat_overridden = cls._chat_impl is not BaseLLMProvider._chat_impl
        stream_overridden = (
            cls._stream_impl is not BaseLLMProvider._stream_impl
        )
        if not (chat_overridden or stream_overridden):
            raise TypeError(
                f"{cls.__name__} must override at least one of "
                f"_chat_impl / _stream_impl"
            )
        self._has_native_chat = chat_overridden
        self._has_native_stream = stream_overridden

    # ---- Identity ---------------------------------------------------------

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def provider(self) -> str:
        return self._provider

    # ---- Lifecycle --------------------------------------------------------

    async def open(self) -> None:
        """Default: idempotent no-op. Override to set up clients."""
        self._opened = True

    async def close(self) -> None:
        """Default: idempotent no-op. Override to release clients."""
        self._opened = False

    # ---- Tokenization (overridable) ---------------------------------------

    def count_tokens(self, text: str) -> int:
        """Heuristic tokenizer (4 characters per token).

        Conservative and provider-agnostic. Subclasses with real
        tokenizers override this.
        """
        if not text:
            return 0
        return max(1, len(text) // 4)

    # ---- Public chat / stream interface (do NOT override in subclasses) ---

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        extra: dict[str, Any] | None = None,
    ) -> LLMReply:
        if self._has_native_chat:
            return await self._chat_impl(
                messages,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
                extra=extra,
            )
        return await self._collect_stream(
            messages,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            extra=extra,
        )

    def stream_chat(
        self,
        messages: list[ChatMessage],
        *,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        extra: dict[str, Any] | None = None,
    ) -> AsyncIterator[LLMDelta]:
        if self._has_native_stream:
            return self._stream_impl(
                messages,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
                extra=extra,
            )
        return self._fake_stream_from_chat(
            messages,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            extra=extra,
        )

    # ---- Subclass extension points (override one or both) -----------------

    async def _chat_impl(
        self,
        messages: list[ChatMessage],
        *,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        extra: dict[str, Any] | None = None,
    ) -> LLMReply:
        """Override to provide a native single-shot chat call."""
        raise NotImplementedError

    def _stream_impl(
        self,
        messages: list[ChatMessage],
        *,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        extra: dict[str, Any] | None = None,
    ) -> AsyncIterator[LLMDelta]:
        """Override to provide a native streaming chat call."""
        raise NotImplementedError

    # ---- Default adapters -------------------------------------------------

    async def _collect_stream(
        self,
        messages: list[ChatMessage],
        *,
        max_output_tokens: int | None,
        temperature: float | None,
        extra: dict[str, Any] | None,
    ) -> LLMReply:
        """``chat`` adapter: drain the native stream into a final reply."""
        text_parts: list[str] = []
        finish_reason: str | None = None
        usage: LLMUsage | None = None
        extras: dict[str, Any] = {}
        async for delta in self._stream_impl(
            messages,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            extra=extra,
        ):
            if delta.text:
                text_parts.append(delta.text)
            if delta.finish_reason is not None:
                finish_reason = delta.finish_reason
            if delta.usage is not None:
                usage = delta.usage
            if delta.extras:
                extras.update(delta.extras)
        return LLMReply(
            text="".join(text_parts) if text_parts else None,
            finish_reason=finish_reason or "stop",
            usage=usage or LLMUsage(),
            extras=extras,
        )

    async def _fake_stream_from_chat(
        self,
        messages: list[ChatMessage],
        *,
        max_output_tokens: int | None,
        temperature: float | None,
        extra: dict[str, Any] | None,
    ) -> AsyncIterator[LLMDelta]:
        """``stream_chat`` adapter: yield one final delta from a chat call."""
        reply = await self._chat_impl(
            messages,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            extra=extra,
        )
        yield LLMDelta(
            text=reply.text or "",
            finish_reason=reply.finish_reason,
            usage=reply.usage,
            extras=dict(reply.extras),
        )
