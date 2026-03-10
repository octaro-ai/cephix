"""Stub LLM provider for testing and demo mode.

Returns canned responses based on keyword matching.  This replaces the
previous inline keyword logic in ``LLMPlanner`` and satisfies ``LLMPort``
so the rest of the system can run without a real LLM backend.
"""

from __future__ import annotations

from typing import Any, Callable

from src.llm.models import LLMCompletion, LLMMessage, LLMToolCall
from src.utils import new_id


class StubLLMProvider:
    """Keyword-matching stub that satisfies LLMPort.

    Useful for tests, demos, and offline development.  Responses can be
    customized by passing a ``response_fn`` callback.
    """

    def __init__(
        self,
        *,
        default_response: str = "Dafuer brauche ich im Prototypen gerade eine konkretere Faehigkeit.",
        model_name: str = "stub",
        response_fn: Callable[[list[LLMMessage], list[dict[str, Any]] | None], LLMCompletion] | None = None,
    ) -> None:
        self._default_response = default_response
        self._model_name = model_name
        self._response_fn = response_fn

    def complete(
        self,
        *,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMCompletion:
        if self._response_fn is not None:
            return self._response_fn(messages, tools)

        # Extract the last user message for keyword matching.
        user_text = ""
        for msg in reversed(messages):
            if msg.role == "user" and msg.content:
                user_text = msg.content.lower()
                break

        # Check if tools are available and a keyword matches a tool call.
        if tools:
            for tool_schema in tools:
                func = tool_schema.get("function", {})
                tool_name = func.get("name", "")
                if self._should_call_tool(user_text, tool_name):
                    return LLMCompletion(
                        tool_calls=[
                            LLMToolCall(
                                id=new_id("call"),
                                name=tool_name,
                                arguments=self._default_args(tool_name),
                            )
                        ],
                        model=model or self._model_name,
                        finish_reason="tool_calls",
                        usage={"prompt_tokens": 0, "completion_tokens": 0},
                    )

        return LLMCompletion(
            content=self._default_response,
            model=model or self._model_name,
            finish_reason="stop",
            usage={"prompt_tokens": 0, "completion_tokens": 0},
        )

    @staticmethod
    def _should_call_tool(user_text: str, tool_name: str) -> bool:
        """Simple keyword → tool mapping."""
        if tool_name == "mail.list_new_messages":
            return any(kw in user_text for kw in ("postkorb", "nachrichten", "mail", "inbox"))
        return False

    @staticmethod
    def _default_args(tool_name: str) -> dict[str, Any]:
        if tool_name == "mail.list_new_messages":
            return {"limit": 10}
        return {}
