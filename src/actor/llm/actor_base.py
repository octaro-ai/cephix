"""LLMActorBase: scaffolding for every LLM driver.

A concrete LLM actor is a *driver* for one SDK or wire protocol:
:class:`MockLLMActor` for the offline fake, future
``LLMActorOpenAI`` for OpenAI-compatible endpoints,
``LLMActorAnthropic`` for the Anthropic SDK, and so on. Each
inherits :class:`LLMActorBase` and implements the SDK call --
nothing else.

What this base provides:

- **Context shaping**: the kernel hands in a curated ``actor_context``
  dict; the base turns it into a list of
  :class:`~src.actor.llm.types.ChatMessage` (system, history, user). The
  driver never sees the dict.
- **Run/stream symmetry**: drivers implement *at least one* of
  :meth:`_chat_native` and :meth:`_stream_native`. The base
  synthesises the missing direction (collect-stream-into-reply, or
  yield-single-final-delta).
- **ActorResponse / ActorChunk assembly**: the base bundles the
  driver's :class:`~src.actor.llm.types.LLMReply` /
  :class:`~src.actor.llm.types.LLMDelta` into the actor-system value
  types, attaches identity metadata (provider, model, tokens, cost)
  and the right :class:`Failable` status.
- **Error translation**: any exception from the driver becomes
  ``ActorResponse(status="error", error=ErrorInfo(code="provider.error", ...))``.
  The kernel surfaces that as a ``KernelPhase(status="error")``.

What drivers add: just the SDK call. Each driver is a thin file --
this is the deliberate pay-off of dropping the separate provider
abstraction.

Token counting: the default :meth:`count_tokens` is the 4-character
heuristic. SDK-specific drivers (OpenAI tiktoken, Anthropic count-
tokens API) override it; the heuristic stays a safe fallback.

Audit attribution: the actor stays *off the bus* (it inherits from
:class:`~src.actor.ports.ActorPort` which is plain
:class:`~src.components.RobotComponent`). Bookkeeping rides on
:attr:`~src.actor.types.ActorResponse.metadata`; the kernel emits
the :class:`RobotAuditNote`.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from src.actor.types import ActorResponse
from src.bus.messages import ErrorInfo
from src.components import ComponentCategory
from src.actor.llm.ports import LLMActorPort
from src.actor.llm.types import (
    ActorChunk,
    ChatMessage,
    LLMDelta,
    LLMReply,
    LLMUsage,
)

logger = logging.getLogger(__name__)


class LLMActorBase(LLMActorPort):
    """Reusable scaffolding for every LLM driver.

    Subclasses **must** override at least one of
    :meth:`_chat_native` and :meth:`_stream_native`. The constructor
    enforces that (otherwise the default adapters would recurse
    forever).

    Subclasses **must** provide a class-level ``component_name``
    (e.g. ``"llm.mock"``, ``"llm.openai"``) so audit attribution
    distinguishes drivers in mixed setups.
    """

    component_category = ComponentCategory.ACTOR

    def __init__(
        self,
        *,
        model_id: str,
        provider: str,
        default_system_prompt: str = "",
    ) -> None:
        if not model_id:
            raise ValueError("LLMActorBase requires a non-empty model_id")
        if not provider:
            raise ValueError("LLMActorBase requires a non-empty provider")
        self._model_id = model_id
        self._provider = provider
        self._default_system_prompt = default_system_prompt

        cls = type(self)
        chat_overridden = cls._chat_native is not LLMActorBase._chat_native
        stream_overridden = (
            cls._stream_native is not LLMActorBase._stream_native
        )
        if not (chat_overridden or stream_overridden):
            raise TypeError(
                f"{cls.__name__} must override at least one of "
                f"_chat_native / _stream_native"
            )
        self._has_native_chat = chat_overridden
        self._has_native_stream = stream_overridden

    # ---- LLMActorPort identity --------------------------------------------

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def provider(self) -> str:
        return self._provider

    def count_tokens(self, text: str) -> int:
        """Heuristic tokenizer (4 characters per token).

        Conservative and SDK-agnostic. Subclasses with real
        tokenizers override this.
        """
        if not text:
            return 0
        return max(1, len(text) // 4)

    # ---- Lifecycle (default no-op) ----------------------------------------

    async def start(self) -> None:
        """Default: nothing to bring online. Override for SDK clients."""
        return None

    async def _stop(self) -> None:
        """Default: nothing to release. Override for SDK clients."""
        return None

    # ---- ActorPort.run / LLMActorPort.stream ------------------------------

    async def run(self, actor_context: dict[str, Any]) -> ActorResponse:
        messages = self._build_messages(actor_context)
        if not messages:
            return ActorResponse(
                message=None,
                status="error",
                error=ErrorInfo(
                    code="actor.context.empty",
                    message="actor_context did not contain a user message",
                ),
                metadata=self._identity_metadata(),
            )

        try:
            if self._has_native_chat:
                reply = await self._chat_native(
                    messages,
                    max_output_tokens=actor_context.get("max_output_tokens"),
                    temperature=actor_context.get("temperature"),
                )
            else:
                reply = await self._collect_stream(
                    messages,
                    max_output_tokens=actor_context.get("max_output_tokens"),
                    temperature=actor_context.get("temperature"),
                )
        except Exception as exc:  # noqa: BLE001 -- driver failure
            return ActorResponse(
                message=None,
                status="error",
                error=ErrorInfo(
                    code="provider.error",
                    message=str(exc),
                    details={"exception_type": type(exc).__name__},
                ),
                metadata=self._identity_metadata(),
            )

        return ActorResponse(
            message=reply.text,
            status="ok",
            metadata=self._reply_metadata(
                reply.usage,
                reply.finish_reason,
                reply.request_id,
                reply.extras,
            ),
        )

    async def stream(
        self, actor_context: dict[str, Any]
    ) -> AsyncIterator[ActorChunk]:
        messages = self._build_messages(actor_context)
        if not messages:
            yield ActorChunk(
                delta="",
                final=True,
                response=ActorResponse(
                    message=None,
                    status="error",
                    error=ErrorInfo(
                        code="actor.context.empty",
                        message="actor_context did not contain a user message",
                    ),
                    metadata=self._identity_metadata(),
                ),
            )
            return

        text_parts: list[str] = []
        finish_reason: str | None = None
        usage: LLMUsage | None = None
        provider_extras: dict[str, Any] = {}

        try:
            if self._has_native_stream:
                source = self._stream_native(
                    messages,
                    max_output_tokens=actor_context.get("max_output_tokens"),
                    temperature=actor_context.get("temperature"),
                )
            else:
                source = self._fake_stream_from_chat(
                    messages,
                    max_output_tokens=actor_context.get("max_output_tokens"),
                    temperature=actor_context.get("temperature"),
                )
            async for delta in source:
                if delta.text:
                    text_parts.append(delta.text)
                    yield ActorChunk(delta=delta.text, final=False)
                if delta.finish_reason is not None:
                    finish_reason = delta.finish_reason
                if delta.usage is not None:
                    usage = delta.usage
                if delta.extras:
                    provider_extras.update(delta.extras)
        except Exception as exc:  # noqa: BLE001 -- driver failure
            yield ActorChunk(
                delta="",
                final=True,
                response=ActorResponse(
                    message="".join(text_parts) or None,
                    status="error",
                    error=ErrorInfo(
                        code="provider.error",
                        message=str(exc),
                        details={"exception_type": type(exc).__name__},
                    ),
                    metadata=self._identity_metadata(),
                ),
            )
            return

        yield ActorChunk(
            delta="",
            final=True,
            response=ActorResponse(
                message="".join(text_parts) or None,
                status="ok",
                metadata=self._reply_metadata(
                    usage or LLMUsage(),
                    finish_reason or "stop",
                    "",
                    provider_extras,
                ),
            ),
        )

    # ---- Subclass extension points (override at least one) ----------------

    async def _chat_native(
        self,
        messages: list[ChatMessage],
        *,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMReply:
        """Override for a native single-shot chat call.

        Receives the curated message list. Returns a
        :class:`LLMReply`. Exceptions are caught by :meth:`run` and
        translated into an error :class:`ActorResponse`.
        """
        raise NotImplementedError

    def _stream_native(
        self,
        messages: list[ChatMessage],
        *,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[LLMDelta]:
        """Override for a native streaming chat call.

        Yields :class:`LLMDelta` chunks. Returns when the model
        signals stop.
        """
        raise NotImplementedError

    # ---- Default adapters -------------------------------------------------

    async def _collect_stream(
        self,
        messages: list[ChatMessage],
        *,
        max_output_tokens: int | None,
        temperature: float | None,
    ) -> LLMReply:
        """``_chat_native`` adapter: drain the native stream."""
        text_parts: list[str] = []
        finish_reason: str | None = None
        usage: LLMUsage | None = None
        extras: dict[str, Any] = {}
        async for delta in self._stream_native(
            messages,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
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
    ) -> AsyncIterator[LLMDelta]:
        """``_stream_native`` adapter: yield a single final delta."""
        reply = await self._chat_native(
            messages,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
        )
        yield LLMDelta(
            text=reply.text or "",
            finish_reason=reply.finish_reason,
            usage=reply.usage,
            extras=dict(reply.extras),
        )

    # ---- Context shaping --------------------------------------------------

    def _build_messages(
        self, actor_context: dict[str, Any]
    ) -> list[ChatMessage]:
        """Curate ``actor_context`` into a list of :class:`ChatMessage`.

        Accepted keys (in priority order):

        - ``messages`` -- already-shaped list of
          :class:`ChatMessage` (or dicts with ``role`` / ``content``).
          Used as-is. When the caller also supplies
          ``system_prompt`` (preferred) or ``system``, that string is
          prepended as a ``system`` :class:`ChatMessage` unless the
          first explicit entry already has ``role=="system"``. This
          is the path the :class:`~src.kernel.chat.ChatKernel`
          takes: it computes a system prompt from firmware and a
          history list from the session store, then hands both
          fields to the actor.
        - ``system`` / ``system_prompt`` -- system prompt to prepend
          when there is no explicit ``messages`` list. Falls back to
          :attr:`_default_system_prompt`.
        - ``history`` -- prior turns as a list of dicts /
          :class:`ChatMessage`.
        - ``message`` or ``input.message`` -- the user message. The
          BaseKernel produces ``input.message``; ad-hoc callers
          often use the flat ``message`` shape.
        """
        explicit = actor_context.get("messages")
        if isinstance(explicit, list) and explicit:
            converted = [
                m if isinstance(m, ChatMessage) else ChatMessage(**m)
                for m in explicit
            ]
            system = (
                actor_context.get("system_prompt")
                or actor_context.get("system")
                or self._default_system_prompt
            )
            if system and (not converted or converted[0].role != "system"):
                converted.insert(
                    0, ChatMessage(role="system", content=system)
                )
            return converted

        out: list[ChatMessage] = []
        system = (
            actor_context.get("system_prompt")
            or actor_context.get("system")
            or self._default_system_prompt
        )
        if system:
            out.append(ChatMessage(role="system", content=system))

        history = actor_context.get("history")
        if isinstance(history, list):
            for entry in history:
                if isinstance(entry, ChatMessage):
                    out.append(entry)
                elif isinstance(entry, dict):
                    role = entry.get("role")
                    content = entry.get("content")
                    if isinstance(role, str) and isinstance(content, str):
                        out.append(ChatMessage(role=role, content=content))

        user_text = self._extract_user_message(actor_context)
        if user_text:
            out.append(ChatMessage(role="user", content=user_text))
        return out

    @staticmethod
    def _extract_user_message(actor_context: dict[str, Any]) -> str:
        text = actor_context.get("message")
        if isinstance(text, str):
            return text
        nested = actor_context.get("input")
        if isinstance(nested, dict):
            inner = nested.get("message")
            if isinstance(inner, str):
                return inner
        return ""

    # ---- Metadata helpers -------------------------------------------------

    def _identity_metadata(self) -> dict[str, Any]:
        return {
            "provider": self._provider,
            "model_id": self._model_id,
        }

    def _reply_metadata(
        self,
        usage: LLMUsage,
        finish_reason: str,
        request_id: str,
        extras: dict[str, Any],
    ) -> dict[str, Any]:
        """Build the metadata dict the kernel merges into phase_details.

        Always carries the full token vocabulary (input, output,
        cache_read, cache_write, reasoning) so a kernel can map them
        onto the OCF ``usage`` field without per-provider knowledge.
        ``cost_usd`` reflects what the driver knows (typically ``0.0``
        -- pricing is the kernel's job via the model catalog).
        """
        metadata: dict[str, Any] = {
            "provider": self._provider,
            "model_id": self._model_id,
            "tokens_in": usage.tokens_in,
            "tokens_out": usage.tokens_out,
            "cache_read_tokens": usage.cache_read_tokens,
            "cache_write_tokens": usage.cache_write_tokens,
            "reasoning_tokens": usage.reasoning_tokens,
            "cost_usd": usage.cost_usd,
            "finish_reason": finish_reason,
        }
        if request_id:
            metadata["request_id"] = request_id
        if extras:
            metadata["provider_extras"] = dict(extras)
        return metadata
