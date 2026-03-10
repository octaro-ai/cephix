"""Data models for LLM completions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMMessage:
    """A single message in the conversation history sent to the LLM."""

    role: str  # "system", "user", "assistant", "tool"
    content: str | None = None
    tool_calls: list[LLMToolCall] | None = None
    tool_call_id: str | None = None  # For role="tool" responses
    name: str | None = None  # Tool name for role="tool"


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
