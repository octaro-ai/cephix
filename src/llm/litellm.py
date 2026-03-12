"""LiteLLM provider — implements LLMPort using litellm for 100+ providers."""

from __future__ import annotations

import json
from typing import Any

from src.llm.models import LLMCompletion, LLMMessage, LLMToolCall, ThinkingCallback, TokenCallback

try:
    import litellm
except ImportError as exc:
    raise ImportError(
        "litellm is required for LiteLLMProvider. "
        "Install it with: uv add 'cephix-drp[litellm]'"
    ) from exc

_DEFAULT_MODEL = "claude-sonnet-4-20250514"


class LiteLLMProvider:
    """LLMPort implementation backed by litellm.

    litellm provides a unified interface to 100+ LLM providers
    (Anthropic, OpenAI, Ollama, Groq, Azure, Bedrock, etc.).

    The model string determines the provider, e.g.:
      - "claude-sonnet-4-20250514" → Anthropic
      - "gpt-4o" → OpenAI
      - "ollama/llama3" → Ollama (local)
      - "groq/mixtral-8x7b" → Groq
    """

    def __init__(
        self,
        *,
        default_model: str = _DEFAULT_MODEL,
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> None:
        self._default_model = default_model
        self._api_key = api_key
        self._api_base = api_base

    def complete(
        self,
        *,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMCompletion:
        api_messages = _convert_messages(messages)
        kwargs: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": api_messages,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if tools:
            kwargs["tools"] = tools  # OpenAI format — litellm handles conversion
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._api_base:
            kwargs["api_base"] = self._api_base

        response = litellm.completion(**kwargs)
        return _parse_response(response)

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
        if token_callback is None:
            return self.complete(
                messages=messages, tools=tools, model=model,
                temperature=temperature, max_tokens=max_tokens,
            )

        api_messages = _convert_messages(messages)
        kwargs: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": api_messages,
            "stream": True,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if tools:
            kwargs["tools"] = tools
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._api_base:
            kwargs["api_base"] = self._api_base

        collected_content = ""
        collected_tool_calls: list[LLMToolCall] = []
        _partial_tcs: dict[int, dict[str, Any]] = {}
        finish_reason = "stop"
        resp_model = model or self._default_model

        stream = litellm.completion(**kwargs)
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if getattr(chunk, "model", None):
                resp_model = chunk.model

            # Reasoning / thinking tokens (provider-dependent).
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning and thinking_callback is not None:
                thinking_callback(reasoning)

            content = getattr(delta, "content", None)
            if content:
                token_callback(content)
                collected_content += content

            tc_deltas = getattr(delta, "tool_calls", None)
            if tc_deltas:
                for tc_delta in tc_deltas:
                    idx = tc_delta.index
                    if idx not in _partial_tcs:
                        _partial_tcs[idx] = {"id": "", "name": "", "arguments": ""}
                    if tc_delta.id:
                        _partial_tcs[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            _partial_tcs[idx]["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            _partial_tcs[idx]["arguments"] += tc_delta.function.arguments

            if chunk.choices[0].finish_reason:
                fr = chunk.choices[0].finish_reason
                if fr == "tool_calls":
                    finish_reason = "tool_calls"
                elif fr == "length":
                    finish_reason = "length"

        for _idx in sorted(_partial_tcs):
            ptc = _partial_tcs[_idx]
            try:
                arguments = json.loads(ptc["arguments"])
            except (json.JSONDecodeError, TypeError):
                arguments = {}
            collected_tool_calls.append(LLMToolCall(
                id=ptc["id"], name=ptc["name"], arguments=arguments,
            ))

        return LLMCompletion(
            content=collected_content or None,
            tool_calls=collected_tool_calls,
            model=resp_model,
            finish_reason=finish_reason,
            usage={},
        )


# ---------------------------------------------------------------------------
# Conversion helpers (same as OpenAI — litellm uses OpenAI message format)
# ---------------------------------------------------------------------------


def _convert_messages(messages: list[LLMMessage]) -> list[dict[str, Any]]:
    """Convert our LLMMessage list to OpenAI/litellm API format."""
    result: list[dict[str, Any]] = []
    for msg in messages:
        if msg.role == "tool":
            result.append({
                "role": "tool",
                "content": msg.content or "",
                "tool_call_id": msg.tool_call_id or "",
            })
            continue

        if msg.role == "assistant" and msg.tool_calls:
            api_tool_calls = []
            for tc in msg.tool_calls:
                api_tool_calls.append({
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                })
            entry: dict[str, Any] = {
                "role": "assistant",
                "tool_calls": api_tool_calls,
            }
            if msg.content:
                entry["content"] = msg.content
            result.append(entry)
            continue

        result.append({"role": msg.role, "content": msg.content or ""})
    return result


def _parse_response(response: Any) -> LLMCompletion:
    """Convert a litellm response to our LLMCompletion model."""
    choice = response.choices[0]
    message = choice.message

    content_text = message.content
    tool_calls: list[LLMToolCall] = []

    if hasattr(message, "tool_calls") and message.tool_calls:
        for tc in message.tool_calls:
            try:
                arguments = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, TypeError):
                arguments = {}
            tool_calls.append(
                LLMToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=arguments,
                )
            )

    finish_reason = "stop"
    if choice.finish_reason == "tool_calls":
        finish_reason = "tool_calls"
    elif choice.finish_reason == "length":
        finish_reason = "length"

    usage: dict[str, int] = {}
    if hasattr(response, "usage") and response.usage:
        usage = {
            "prompt_tokens": getattr(response.usage, "prompt_tokens", 0),
            "completion_tokens": getattr(response.usage, "completion_tokens", 0),
        }

    return LLMCompletion(
        content=content_text,
        tool_calls=tool_calls,
        model=getattr(response, "model", ""),
        finish_reason=finish_reason,
        usage=usage,
    )
