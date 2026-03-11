"""Tests for LLM provider implementations and factory.

These tests verify:
- Message conversion logic (our format → provider format)
- Tool schema conversion
- Response parsing
- Factory creation from config
- Layered API key resolution

Since the actual SDKs (anthropic, openai, litellm) are optional dependencies,
we test the conversion helpers directly and mock the SDK clients.
"""

from __future__ import annotations

import json
import os
import unittest
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

from src.llm.models import LLMCompletion, LLMMessage, LLMToolCall


# ===========================================================================
# Anthropic conversion tests
# ===========================================================================


class AnthropicConversionTests(unittest.TestCase):
    """Test Anthropic message/tool conversion helpers."""

    def _import_helpers(self):
        """Import helpers, skipping if anthropic SDK not installed."""
        try:
            from src.llm.anthropic import (
                _split_system_and_messages,
                _convert_tools_to_anthropic,
                _merge_consecutive_roles,
                _parse_response,
            )
            return _split_system_and_messages, _convert_tools_to_anthropic, _merge_consecutive_roles, _parse_response
        except ImportError:
            self.skipTest("anthropic SDK not installed")

    def test_system_message_is_extracted(self) -> None:
        split, _, _, _ = self._import_helpers()
        messages = [
            LLMMessage(role="system", content="You are helpful."),
            LLMMessage(role="user", content="Hi"),
        ]
        system_text, api_msgs = split(messages)
        self.assertEqual("You are helpful.", system_text)
        self.assertEqual(1, len(api_msgs))
        self.assertEqual("user", api_msgs[0]["role"])

    def test_tool_result_becomes_user_tool_result_block(self) -> None:
        split, _, _, _ = self._import_helpers()
        messages = [
            LLMMessage(role="user", content="Check mail"),
            LLMMessage(
                role="assistant",
                tool_calls=[LLMToolCall(id="call_1", name="mail.list", arguments={})],
            ),
            LLMMessage(role="tool", content='[{"id": "m1"}]', tool_call_id="call_1"),
        ]
        _, api_msgs = split(messages)
        # Tool result should be a user message with tool_result block
        tool_msg = api_msgs[2]
        self.assertEqual("user", tool_msg["role"])
        self.assertIsInstance(tool_msg["content"], list)
        self.assertEqual("tool_result", tool_msg["content"][0]["type"])
        self.assertEqual("call_1", tool_msg["content"][0]["tool_use_id"])

    def test_assistant_tool_calls_become_tool_use_blocks(self) -> None:
        split, _, _, _ = self._import_helpers()
        messages = [
            LLMMessage(
                role="assistant",
                tool_calls=[LLMToolCall(id="tc_1", name="mail.list", arguments={"limit": 5})],
            ),
        ]
        _, api_msgs = split(messages)
        self.assertEqual("assistant", api_msgs[0]["role"])
        blocks = api_msgs[0]["content"]
        self.assertEqual("tool_use", blocks[0]["type"])
        self.assertEqual("mail_list", blocks[0]["name"])
        self.assertEqual({"limit": 5}, blocks[0]["input"])

    def test_consecutive_same_role_messages_are_merged(self) -> None:
        _, _, merge, _ = self._import_helpers()
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "user", "content": "World"},
        ]
        merged = merge(messages)
        self.assertEqual(1, len(merged))
        self.assertEqual("user", merged[0]["role"])
        # Content should be merged into list of text blocks
        self.assertIsInstance(merged[0]["content"], list)
        self.assertEqual(2, len(merged[0]["content"]))

    def test_tool_schema_conversion(self) -> None:
        _, convert_tools, _, _ = self._import_helpers()
        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": "mail.list",
                    "description": "List messages",
                    "parameters": {
                        "type": "object",
                        "properties": {"limit": {"type": "integer"}},
                    },
                },
            }
        ]
        anthropic_tools, name_map = convert_tools(openai_tools)
        self.assertEqual("mail_list", anthropic_tools[0]["name"])
        self.assertEqual("List messages", anthropic_tools[0]["description"])
        self.assertIn("properties", anthropic_tools[0]["input_schema"])
        # name_map allows round-tripping back to dotted names
        self.assertEqual("mail.list", name_map["mail_list"])

    def test_parse_response_text(self) -> None:
        _, _, _, parse = self._import_helpers()
        response = MagicMock()
        response.content = [MagicMock(type="text", text="Hello!")]
        response.stop_reason = "end_turn"
        response.model = "claude-sonnet-4-20250514"
        response.usage = MagicMock(input_tokens=10, output_tokens=5)

        result = parse(response)
        self.assertEqual("Hello!", result.content)
        self.assertEqual([], result.tool_calls)
        self.assertEqual("stop", result.finish_reason)
        self.assertEqual(10, result.usage["prompt_tokens"])

    def test_parse_response_tool_use(self) -> None:
        _, _, _, parse = self._import_helpers()
        response = MagicMock()
        # MagicMock intercepts 'name' — use a simple namespace instead
        tool_block = type("Block", (), {
            "type": "tool_use", "id": "tc_1", "name": "mail.list", "input": {"limit": 5},
        })()
        response.content = [tool_block]
        response.stop_reason = "tool_use"
        response.model = "claude-sonnet-4-20250514"
        response.usage = MagicMock(input_tokens=20, output_tokens=10)

        result = parse(response)
        self.assertIsNone(result.content)
        self.assertEqual(1, len(result.tool_calls))
        self.assertEqual("mail.list", result.tool_calls[0].name)
        self.assertEqual("tool_calls", result.finish_reason)


# ===========================================================================
# OpenAI conversion tests
# ===========================================================================


class OpenAIConversionTests(unittest.TestCase):
    """Test OpenAI message conversion helpers."""

    def _import_helpers(self):
        try:
            from src.llm.openai import _convert_messages, _parse_response
            return _convert_messages, _parse_response
        except ImportError:
            self.skipTest("openai SDK not installed")

    def test_system_message_stays_in_messages(self) -> None:
        convert, _ = self._import_helpers()
        messages = [
            LLMMessage(role="system", content="You are helpful."),
            LLMMessage(role="user", content="Hi"),
        ]
        api_msgs = convert(messages)
        self.assertEqual(2, len(api_msgs))
        self.assertEqual("system", api_msgs[0]["role"])

    def test_tool_result_has_tool_call_id(self) -> None:
        convert, _ = self._import_helpers()
        messages = [
            LLMMessage(role="tool", content="result", tool_call_id="call_1"),
        ]
        api_msgs = convert(messages)
        self.assertEqual("tool", api_msgs[0]["role"])
        self.assertEqual("call_1", api_msgs[0]["tool_call_id"])

    def test_assistant_tool_calls_have_function_format(self) -> None:
        convert, _ = self._import_helpers()
        messages = [
            LLMMessage(
                role="assistant",
                tool_calls=[LLMToolCall(id="tc_1", name="mail.list", arguments={"limit": 5})],
            ),
        ]
        api_msgs = convert(messages)
        tc = api_msgs[0]["tool_calls"][0]
        self.assertEqual("function", tc["type"])
        self.assertEqual("mail.list", tc["function"]["name"])
        self.assertEqual('{"limit": 5}', tc["function"]["arguments"])

    def test_parse_response_text(self) -> None:
        _, parse = self._import_helpers()
        response = MagicMock()
        message = MagicMock()
        message.content = "Hello!"
        message.tool_calls = None
        choice = MagicMock()
        choice.message = message
        choice.finish_reason = "stop"
        response.choices = [choice]
        response.model = "gpt-4o"
        response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)

        result = parse(response)
        self.assertEqual("Hello!", result.content)
        self.assertEqual("stop", result.finish_reason)

    def test_parse_response_tool_calls(self) -> None:
        _, parse = self._import_helpers()
        response = MagicMock()
        tc = MagicMock()
        tc.id = "tc_1"
        tc.function.name = "mail.list"
        tc.function.arguments = '{"limit": 5}'
        message = MagicMock()
        message.content = None
        message.tool_calls = [tc]
        choice = MagicMock()
        choice.message = message
        choice.finish_reason = "tool_calls"
        response.choices = [choice]
        response.model = "gpt-4o"
        response.usage = MagicMock(prompt_tokens=20, completion_tokens=10)

        result = parse(response)
        self.assertEqual(1, len(result.tool_calls))
        self.assertEqual("mail.list", result.tool_calls[0].name)
        self.assertEqual({"limit": 5}, result.tool_calls[0].arguments)


# ===========================================================================
# LiteLLM conversion tests
# ===========================================================================


class LiteLLMConversionTests(unittest.TestCase):
    """Test LiteLLM message conversion helpers."""

    def _import_helpers(self):
        try:
            from src.llm.litellm import _convert_messages, _parse_response
            return _convert_messages, _parse_response
        except ImportError:
            self.skipTest("litellm SDK not installed")

    def test_messages_use_openai_format(self) -> None:
        convert, _ = self._import_helpers()
        messages = [
            LLMMessage(role="system", content="Be helpful."),
            LLMMessage(role="user", content="Hi"),
        ]
        api_msgs = convert(messages)
        self.assertEqual("system", api_msgs[0]["role"])
        self.assertEqual("user", api_msgs[1]["role"])


# ===========================================================================
# Factory tests (no SDK needed)
# ===========================================================================


class FactoryTests(unittest.TestCase):
    """Test create_llm_provider factory."""

    def test_empty_config_returns_none(self) -> None:
        from src.llm.factory import create_llm_provider
        self.assertIsNone(create_llm_provider({}))

    def test_missing_provider_returns_none(self) -> None:
        from src.llm.factory import create_llm_provider
        self.assertIsNone(create_llm_provider({"llm": {}}))

    def test_stub_provider_returns_stub(self) -> None:
        from src.llm.factory import create_llm_provider
        from src.llm.stub import StubLLMProvider
        provider = create_llm_provider({"llm": {"provider": "stub"}})
        self.assertIsInstance(provider, StubLLMProvider)

    def test_unknown_provider_raises(self) -> None:
        from src.llm.factory import create_llm_provider
        with self.assertRaises(ValueError):
            create_llm_provider({"llm": {"provider": "unknown_xyz"}})

    def test_anthropic_provider_created_when_sdk_available(self) -> None:
        from src.llm.factory import create_llm_provider
        try:
            import anthropic
        except ImportError:
            self.skipTest("anthropic SDK not installed")
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        try:
            provider = create_llm_provider({"llm": {"provider": "anthropic"}})
            from src.llm.anthropic import AnthropicProvider
            self.assertIsInstance(provider, AnthropicProvider)
        finally:
            del os.environ["ANTHROPIC_API_KEY"]

    def test_openai_provider_created_when_sdk_available(self) -> None:
        from src.llm.factory import create_llm_provider
        try:
            import openai
        except ImportError:
            self.skipTest("openai SDK not installed")
        os.environ["OPENAI_API_KEY"] = "test-key"
        try:
            provider = create_llm_provider({"llm": {"provider": "openai"}})
            from src.llm.openai import OpenAIProvider
            self.assertIsInstance(provider, OpenAIProvider)
        finally:
            del os.environ["OPENAI_API_KEY"]

    def test_api_key_env_override(self) -> None:
        from src.llm.factory import _resolve_api_key
        os.environ["MY_CUSTOM_KEY"] = "custom-value"
        try:
            key = _resolve_api_key("MY_CUSTOM_KEY", "anthropic")
            self.assertEqual("custom-value", key)
        finally:
            del os.environ["MY_CUSTOM_KEY"]

    def test_default_api_key_env_per_provider(self) -> None:
        from src.llm.factory import _resolve_api_key
        os.environ["ANTHROPIC_API_KEY"] = "anthropic-value"
        try:
            key = _resolve_api_key("", "anthropic")
            self.assertEqual("anthropic-value", key)
        finally:
            del os.environ["ANTHROPIC_API_KEY"]


if __name__ == "__main__":
    unittest.main()
