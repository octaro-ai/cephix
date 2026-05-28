"""MockLLMActor: real-acting offline driver.

Not a stub that returns a canned string. The mock is deliberately
*real-acting*: it consults the same :class:`ModelCatalogPort` an
LLM-aware kernel would, computes token counts via its own
:meth:`count_tokens`, and assembles a realistic
:class:`~src.actor.llm.types.LLMReply` (with usage *and* cost). Only the
*content* generation is mocked: the mock either echoes the last
user message or runs a configurable template against the input.

Why bother: it lets the entire LLM stack -- actor, kernel, catalog,
audit trail -- run end-to-end *under realistic load* in tests and
CI, without a network call. When we add ``LLMActorOpenAI`` later,
only the content-generation step changes; everything around it has
been exercised against this driver.

Streaming: native. Word-grouped chunks with configurable delay
(default zero).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
import asyncio

from src.actor.llm.actor_base import LLMActorBase
from src.actor.llm.types import ChatMessage, LLMDelta, LLMReply, LLMUsage
from src.utility.model_catalog import ModelCatalogPort

ResponseFn = Callable[[list[ChatMessage]], str]


def _default_responder(messages: list[ChatMessage]) -> str:
    """Default mock response: echo the last user message."""
    for msg in reversed(messages):
        if msg.role == "user":
            return f"[mock-reply] {msg.content}"
    return "[mock-reply] (no user message)"


class MockLLMActor(LLMActorBase):
    """Catalog-aware mock LLM driver.

    Constructor:

    - ``model_id`` / ``provider`` -- the (potentially fictitious)
      identity this driver pretends to be. Default
      ``("mock-echo", "mock")`` so the mock does not collide with
      any real model id while still passing canonical-looking
      identity through the metadata.
    - ``catalog`` -- optional :class:`ModelCatalogPort`. When
      provided and the identity exists in the catalog, the mock
      computes ``cost_usd`` from real pricing and surfaces the
      catalog-recorded ``finish_reason`` semantics. Without a
      catalog the cost is ``0.0`` and tokens are still counted
      via the local heuristic.
    - ``default_system_prompt`` -- forwarded to
      :class:`LLMActorBase`.
    - ``responder`` -- pluggable callable that produces the reply
      content from the message list. Default: echo the last user
      message.
    - ``stream_delay_seconds`` -- async sleep between streamed
      chunks. Useful for stream-handling tests; default 0.
    - ``chunk_words`` -- whitespace-separated tokens per stream
      chunk. Default 1 (word-by-word).

    Both :meth:`_chat_native` and :meth:`_stream_native` are
    implemented natively so the run-by-collect-stream and
    stream-by-yield-chat adapters in :class:`LLMActorBase` can
    each be exercised by their own dedicated tests against a
    different driver.
    """

    component_name = "llm.mock"
    component_description = (
        "Mock LLM driver. Real-acting (consults the model catalog "
        "for realistic token counts and cost); content generation "
        "is templated. The default end-to-end test driver."
    )

    def __init__(
        self,
        *,
        model_id: str = "mock-echo",
        provider: str = "mock",
        catalog: ModelCatalogPort | None = None,
        default_system_prompt: str = "",
        responder: ResponseFn | None = None,
        stream_delay_seconds: float = 0.0,
        chunk_words: int = 1,
    ) -> None:
        super().__init__(
            model_id=model_id,
            provider=provider,
            default_system_prompt=default_system_prompt,
        )
        if stream_delay_seconds < 0:
            raise ValueError("stream_delay_seconds must be >= 0")
        if chunk_words < 1:
            raise ValueError("chunk_words must be >= 1")
        self._catalog = catalog
        self._responder = responder or _default_responder
        self._stream_delay = stream_delay_seconds
        self._chunk_words = chunk_words

    def count_tokens(self, text: str) -> int:
        """Whitespace word count, minimum 1 for non-empty.

        Deterministic and easy to reason about in tests. Real
        drivers override with their tokenizer.
        """
        if not text:
            return 0
        return max(1, len(text.split()))

    async def _chat_native(
        self,
        messages: list[ChatMessage],
        *,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
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

    async def _stream_native(
        self,
        messages: list[ChatMessage],
        *,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
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

        for i in range(0, len(words), self._chunk_words):
            group = words[i : i + self._chunk_words]
            chunk_text = " ".join(group)
            if i > 0:
                chunk_text = " " + chunk_text
            if self._stream_delay:
                await asyncio.sleep(self._stream_delay)
            yield LLMDelta(text=chunk_text)
        yield LLMDelta(
            text="",
            finish_reason="stop",
            usage=self._compute_usage(messages, text),
            extras={"mock": True},
        )

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
        if self._catalog is not None:
            pricing = self._catalog.lookup_pricing(
                self._model_id, self._provider
            )
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
