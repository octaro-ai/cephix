"""LLM port -- provider-agnostic interface for language model calls.

Any LLM backend (Anthropic, OpenAI, litellm, local) implements this
protocol.  The planner and other components depend only on this
abstraction, never on a specific SDK.
"""

from __future__ import annotations

from typing import Any, Protocol

from src.llm.models import LLMCompletion, LLMMessage, ThinkingCallback, TokenCallback


class LLMPort(Protocol):
    """Make a completion call to a language model."""

    def complete(
        self,
        *,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMCompletion:
        ...

    def stream_complete(
        self,
        *,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        token_callback: TokenCallback | None = None,
        thinking_callback: ThinkingCallback | None = None,
    ) -> LLMCompletion:
        ...
