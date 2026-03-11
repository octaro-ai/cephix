"""Anthropic LLM provider — implements LLMPort using the Anthropic SDK."""

from __future__ import annotations

import json
from typing import Any

from src.llm.models import LLMCompletion, LLMMessage, LLMToolCall

try:
    import anthropic
except ImportError as exc:
    raise ImportError(
        "anthropic SDK is required for AnthropicProvider. "
        "Install it with: uv add 'cephix-drp[anthropic]'"
    ) from exc

_DEFAULT_MODEL = "claude-sonnet-4-20250514"
_DEFAULT_MAX_TOKENS = 4096


class AnthropicProvider:
    """LLMPort implementation backed by the Anthropic Messages API."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        default_model: str = _DEFAULT_MODEL,
        default_max_tokens: int = _DEFAULT_MAX_TOKENS,
    ) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._default_model = default_model
        self._default_max_tokens = default_max_tokens

    def complete(
        self,
        *,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMCompletion:
        system_text, api_messages = _split_system_and_messages(messages)
        kwargs: dict[str, Any] = {
            "model": model or self._default_model,
            "max_tokens": max_tokens or self._default_max_tokens,
            "messages": api_messages,
        }
        if system_text:
            kwargs["system"] = system_text
        if temperature is not None:
            kwargs["temperature"] = temperature

        # Anthropic tool names must match ^[a-zA-Z0-9_-]+$ — no dots.
        # We sanitize on the way out and restore on the way back.
        name_map: dict[str, str] = {}  # sanitized → original
        if tools:
            kwargs["tools"], name_map = _convert_tools_to_anthropic(tools)

        response = self._client.messages.create(**kwargs)
        return _parse_response(response, name_map)


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------


def _split_system_and_messages(
    messages: list[LLMMessage],
) -> tuple[str, list[dict[str, Any]]]:
    """Separate the system message and convert the rest to Anthropic format.

    Anthropic requires system as a top-level parameter, not in the messages
    list. Also, consecutive messages of the same role must be merged, and
    tool results use a specific block format.
    """
    system_parts: list[str] = []
    raw: list[dict[str, Any]] = []

    for msg in messages:
        if msg.role == "system":
            if msg.content:
                system_parts.append(msg.content)
            continue

        if msg.role == "tool":
            # Anthropic expects tool results as role="user" with
            # tool_result content blocks.
            block: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": msg.tool_call_id or "",
                "content": msg.content or "",
            }
            raw.append({"role": "user", "content": [block]})
            continue

        if msg.role == "assistant" and msg.tool_calls:
            # Assistant message with tool_use blocks.
            content_blocks: list[dict[str, Any]] = []
            if msg.content:
                content_blocks.append({"type": "text", "text": msg.content})
            for tc in msg.tool_calls:
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": _sanitize_name(tc.name),
                    "input": tc.arguments,
                })
            raw.append({"role": "assistant", "content": content_blocks})
            continue

        # Plain user or assistant text message.
        raw.append({"role": msg.role, "content": msg.content or ""})

    # Merge consecutive same-role messages (Anthropic requirement).
    merged = _merge_consecutive_roles(raw)
    return "\n\n".join(system_parts), merged


def _merge_consecutive_roles(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge consecutive messages with the same role into a single message."""
    if not messages:
        return []

    merged: list[dict[str, Any]] = [messages[0]]
    for msg in messages[1:]:
        if msg["role"] == merged[-1]["role"]:
            prev_content = merged[-1]["content"]
            curr_content = msg["content"]
            # Normalise to list-of-blocks for merging.
            if isinstance(prev_content, str):
                prev_content = [{"type": "text", "text": prev_content}] if prev_content else []
            if isinstance(curr_content, str):
                curr_content = [{"type": "text", "text": curr_content}] if curr_content else []
            merged[-1]["content"] = prev_content + curr_content
        else:
            merged.append(msg)
    return merged


def _sanitize_name(name: str) -> str:
    """Replace dots with underscores for Anthropic's name pattern."""
    return name.replace(".", "_")


def _convert_tools_to_anthropic(
    tools: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Convert OpenAI-style function schemas to Anthropic tool format.

    Returns (converted_tools, name_map) where name_map maps
    sanitized names back to the original dotted names.
    """
    converted: list[dict[str, Any]] = []
    name_map: dict[str, str] = {}
    for tool in tools:
        func = tool.get("function", {})
        original = func.get("name", "")
        sanitized = _sanitize_name(original)
        name_map[sanitized] = original
        converted.append({
            "name": sanitized,
            "description": func.get("description", ""),
            "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
        })
    return converted, name_map


def _parse_response(response: Any, name_map: dict[str, str] | None = None) -> LLMCompletion:
    """Convert an Anthropic API response to our LLMCompletion model."""
    name_map = name_map or {}
    content_text: str | None = None
    tool_calls: list[LLMToolCall] = []

    for block in response.content:
        if block.type == "text":
            content_text = (content_text or "") + block.text
        elif block.type == "tool_use":
            # Restore original dotted name from the sanitized version.
            original_name = name_map.get(block.name, block.name)
            tool_calls.append(
                LLMToolCall(
                    id=block.id,
                    name=original_name,
                    arguments=block.input if isinstance(block.input, dict) else {},
                )
            )

    finish_reason = "stop"
    if response.stop_reason == "tool_use":
        finish_reason = "tool_calls"
    elif response.stop_reason == "max_tokens":
        finish_reason = "length"

    usage: dict[str, int] = {}
    if hasattr(response, "usage") and response.usage:
        usage = {
            "prompt_tokens": response.usage.input_tokens,
            "completion_tokens": response.usage.output_tokens,
        }

    return LLMCompletion(
        content=content_text,
        tool_calls=tool_calls,
        model=response.model,
        finish_reason=finish_reason,
        usage=usage,
    )
