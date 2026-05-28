"""MockLLMProvider: a real-acting fake.

Not a stub that returns a canned string. The mock is deliberately
*real-acting*: it consults the same :class:`~src.llm.ports.ModelCatalogPort`
and :class:`~src.llm.ports.PricingPort` that an OpenAI- or Anthropic-
backed provider would, computes token counts via its own
:meth:`count_tokens`, and assembles a realistic
:class:`~src.llm.types.LLMReply` (with usage *and* cost). Only the
*content* generation is mocked: the mock either echoes the last
user message or runs a configurable template against the input.

Why bother: it lets the entire LLM stack -- actor, kernel,
metadata service, audit trail -- run end-to-end *under realistic
load* in tests and CI, without a network call. When we add
:class:`OpenAICompatProvider` later, only the content-generation
step changes; everything around it has been exercised against the
mock.

Streaming: the mock supports it natively. It tokenises the reply
into word-sized chunks and yields them with a configurable delay
(default zero).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

from src.llm.ports import ModelCatalogPort, PricingPort
from src.llm.providers.base import BaseLLMProvider
from src.llm.types import ChatMessage, LLMDelta, LLMReply, LLMUsage

ResponseFn = Callable[[list[ChatMessage]], str]


def _default_responder(messages: list[ChatMessage]) -> str:
    """Default mock response: echo the last user message with a prefix."""
    for msg in reversed(messages):
        if msg.role == "user":
            return f"[mock-reply] {msg.content}"
    return "[mock-reply] (no user message)"


class MockLLMProvider(BaseLLMProvider):
    """Catalog-aware mock that returns real metadata.

    Constructor arguments:

    - ``model_id`` / ``provider`` -- the (potentially fictitious)
      identity this mock pretends to be. Default: ``("echo", "mock")``,
      which matches the entry in the bundled
      :file:`src/llm/data/models.json`.
    - ``catalog`` -- read-only :class:`ModelCatalogPort`. Used to
      validate that the model identity exists during ``open`` and to
      look up the spec on every call so the reply carries realistic
      metadata.
    - ``pricing`` -- optional :class:`PricingPort`. When provided,
      the reply's :attr:`LLMReply.usage.cost_usd` is computed from
      the actual token counts and pricing snapshot.
    - ``responder`` -- pluggable callable that produces the reply
      content from the message list. Default: echo the last user
      message.
    - ``stream_delay_seconds`` -- async sleep between streamed
      chunks. Useful for stream-handling tests; default 0 (no
      sleep).
    - ``chunk_words`` -- how many whitespace-separated tokens to
      emit per stream chunk. Default 1 (word-by-word).

    The mock implements both ``_chat_impl`` and ``_stream_impl``
    natively so the run-by-collect-stream / stream-by-yield-chat
    adapters in :class:`BaseLLMProvider` can each be exercised by
    their own dedicated tests against a different provider.
    """

    def __init__(
        self,
        *,
        catalog: ModelCatalogPort,
        pricing: PricingPort | None = None,
        model_id: str = "echo",
        provider: str = "mock",
        responder: ResponseFn | None = None,
        stream_delay_seconds: float = 0.0,
        chunk_words: int = 1,
    ) -> None:
        super().__init__(model_id=model_id, provider=provider)
        if stream_delay_seconds < 0:
            raise ValueError("stream_delay_seconds must be >= 0")
        if chunk_words < 1:
            raise ValueError("chunk_words must be >= 1")
        self._catalog = catalog
        self._pricing = pricing
        self._responder = responder or _default_responder
        self._stream_delay = stream_delay_seconds
        self._chunk_words = chunk_words

    async def open(self) -> None:
        """Validate that ``(model_id, provider)`` exists in the catalog."""
        spec = self._catalog.lookup(self._model_id, self._provider)
        if spec is None:
            raise LookupError(
                f"MockLLMProvider: model "
                f"{self._provider}/{self._model_id} not found in catalog. "
                f"Either register it in the bundled snapshot or pick an "
                f"existing identity."
            )
        await super().open()

    async def _chat_impl(
        self,
        messages: list[ChatMessage],
        *,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        extra: dict[str, Any] | None = None,
    ) -> LLMReply:
        text = self._responder(messages)
        text = self._truncate_to_max_tokens(text, max_output_tokens)
        usage = self._compute_usage(messages, text)
        return LLMReply(
            text=text,
            finish_reason="stop",
            usage=usage,
            request_id="mock-req-0",
            extras={"mock": True},
        )

    async def _stream_impl(
        self,
        messages: list[ChatMessage],
        *,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        extra: dict[str, Any] | None = None,
    ) -> AsyncIterator[LLMDelta]:
        text = self._responder(messages)
        text = self._truncate_to_max_tokens(text, max_output_tokens)
        words = text.split(" ")
        if not words or words == [""]:
            yield LLMDelta(
                text="",
                finish_reason="stop",
                usage=self._compute_usage(messages, ""),
                extras={"mock": True},
            )
            return

        # Stream word-grouped chunks. Spaces re-introduced so the
        # concatenation reconstructs the original text exactly.
        for i in range(0, len(words), self._chunk_words):
            group = words[i : i + self._chunk_words]
            chunk_text = " ".join(group)
            if i > 0:
                chunk_text = " " + chunk_text
            if self._stream_delay:
                await asyncio.sleep(self._stream_delay)
            yield LLMDelta(text=chunk_text)
        # Closing delta carries usage + finish_reason.
        yield LLMDelta(
            text="",
            finish_reason="stop",
            usage=self._compute_usage(messages, text),
            extras={"mock": True},
        )

    def count_tokens(self, text: str) -> int:
        """Override: simple whitespace word count, minimum 1 for non-empty.

        Deterministic and easy to reason about in tests. Real
        providers override with their tokenizer; the mock keeps
        the heuristic transparent.
        """
        if not text:
            return 0
        return max(1, len(text.split()))

    # ---- internals --------------------------------------------------------

    def _truncate_to_max_tokens(
        self, text: str, max_output_tokens: int | None
    ) -> str:
        if max_output_tokens is None or max_output_tokens <= 0:
            return text
        words = text.split(" ")
        if len(words) <= max_output_tokens:
            return text
        return " ".join(words[:max_output_tokens])

    def _compute_usage(
        self, messages: list[ChatMessage], reply_text: str
    ) -> LLMUsage:
        tokens_in = sum(self.count_tokens(m.content) for m in messages)
        tokens_out = self.count_tokens(reply_text)
        cost = 0.0
        if self._pricing is not None:
            pricing = self._pricing.lookup(self._model_id, self._provider)
            if pricing is not None:
                cost = (
                    tokens_in * pricing.input_cost_per_token
                    + tokens_out * pricing.output_cost_per_token
                )
        return LLMUsage(
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
        )
