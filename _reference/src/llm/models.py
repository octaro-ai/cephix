"""Data models for LLM completions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# Callback type for streaming: called with each text token as it arrives.
TokenCallback = Callable[[str], None]

# Callback for reasoning/thinking tokens (extended thinking, chain-of-thought).
# Same signature as TokenCallback but semantically separate so callers can
# decide independently what to do with thinking output (e.g. debug display).
ThinkingCallback = Callable[[str], None]


@dataclass
class LLMMessage:
    """A single message in the conversation history sent to the LLM."""

    role: str  # "system", "user", "assistant", "tool"
    content: str | None = None
    tool_calls: list[LLMToolCall] | None = None
    tool_call_id: str | None = None  # For role="tool" responses
    name: str | None = None  # Tool name for role="tool"
    # Thinking/reasoning text from the assistant (must be preserved in
    # multi-turn tool flows for Anthropic extended thinking).
    thinking: str | None = None
    thinking_signature: str | None = None  # Anthropic signing token


@dataclass
class LLMToolCall:
    """A tool call requested by the LLM."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMCompletion:
    """Result of an LLM completion call."""

    content: str | None = None
    tool_calls: list[LLMToolCall] = field(default_factory=list)
    model: str = ""
    finish_reason: str = "stop"  # "stop", "tool_calls", "length"
    usage: dict[str, int] = field(default_factory=dict)
    # Thinking/reasoning text produced by the model (extended thinking,
    # chain-of-thought).  Preserved so callers can include it in the
    # conversation history for multi-turn tool flows.
    thinking: str | None = None
    thinking_signature: str | None = None  # Anthropic signing token
