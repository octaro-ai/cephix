"""OpenAI LLM provider — implements LLMPort using the OpenAI SDK."""

from __future__ import annotations

import json
from typing import Any

from src.llm.models import LLMCompletion, LLMMessage, LLMToolCall, TokenCallback

try:
    import openai
except ImportError as exc:
    raise ImportError(
        "openai SDK is required for OpenAIProvider. "
        "Install it with: uv add 'cephix-drp[openai]'"
    ) from exc

_DEFAULT_MODEL = "gpt-4o"


class OpenAIProvider:
    """LLMPort implementation backed by the OpenAI Chat Completions API."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        default_model: str = _DEFAULT_MODEL,
        base_url: str | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {}
        if api_key is not None:
            kwargs["api_key"] = api_key
        if base_url is not None:
            kwargs["base_url"] = base_url
        self._client = openai.OpenAI(**kwargs)
        self._default_model = default_model

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
            kwargs["tools"] = tools  # Already in OpenAI format

        response = self._client.chat.completions.create(**kwargs)
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

        collected_content = ""
        collected_tool_calls: list[LLMToolCall] = []
        # Track partial tool calls being assembled from deltas.
        _partial_tcs: dict[int, dict[str, Any]] = {}
        finish_reason = "stop"
        resp_model = model or self._default_model

        stream = self._client.chat.completions.create(**kwargs)
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if chunk.model:
                resp_model = chunk.model

            if delta.content:
                token_callback(delta.content)
                collected_content += delta.content

            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
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
# Conversion helpers
# ---------------------------------------------------------------------------


def _convert_messages(messages: list[LLMMessage]) -> list[dict[str, Any]]:
    """Convert our LLMMessage list to OpenAI API format."""
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
    """Convert an OpenAI API response to our LLMCompletion model."""
    choice = response.choices[0]
    message = choice.message

    content_text = message.content
    tool_calls: list[LLMToolCall] = []

    if message.tool_calls:
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
    if response.usage:
        usage = {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
        }

    return LLMCompletion(
        content=content_text,
        tool_calls=tool_calls,
        model=response.model,
        finish_reason=finish_reason,
        usage=usage,
    )
