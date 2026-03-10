"""Tests for LLMPort, StubLLMProvider, and LLM-backed planner."""

from __future__ import annotations

import unittest
from typing import Any

from src.domain import ExecutionContext, PlanningContext, RobotEvent
from src.llm.models import LLMCompletion, LLMMessage, LLMToolCall
from src.llm.stub import StubLLMProvider
from src.planners.llm import LLMPlanner
from src.utils import new_id


def _make_ctx() -> ExecutionContext:
    return ExecutionContext(
        run_id="run-1",
        robot_id="robot-1",
        user_id="user-1",
        conversation_id="conv-1",
        channel="test",
        trace_id="trace-1",
    )


def _make_event(text: str) -> RobotEvent:
    return RobotEvent(
        event_id="evt-1",
        event_type="message.received",
        source_channel="test",
        sender_id="user-1",
        text=text,
    )


class StubLLMProviderTests(unittest.TestCase):
    def test_default_response(self) -> None:
        provider = StubLLMProvider()
        result = provider.complete(messages=[LLMMessage(role="user", content="Hi")])
        self.assertEqual("stop", result.finish_reason)
        self.assertIn("Prototypen", result.content)
        self.assertEqual("stub", result.model)

    def test_custom_default_response(self) -> None:
        provider = StubLLMProvider(default_response="Custom reply")
        result = provider.complete(messages=[LLMMessage(role="user", content="Hi")])
        self.assertEqual("Custom reply", result.content)

    def test_mail_keyword_triggers_tool_call(self) -> None:
        tool_schema = {
            "type": "function",
            "function": {"name": "mail.list_new_messages", "description": "List mail"},
        }
        provider = StubLLMProvider()
        result = provider.complete(
            messages=[LLMMessage(role="user", content="Check mein Postkorb")],
            tools=[tool_schema],
        )
        self.assertEqual("tool_calls", result.finish_reason)
        self.assertEqual(1, len(result.tool_calls))
        self.assertEqual("mail.list_new_messages", result.tool_calls[0].name)

    def test_no_tool_call_without_keyword(self) -> None:
        tool_schema = {
            "type": "function",
            "function": {"name": "mail.list_new_messages", "description": "List mail"},
        }
        provider = StubLLMProvider()
        result = provider.complete(
            messages=[LLMMessage(role="user", content="Wie ist das Wetter?")],
            tools=[tool_schema],
        )
        self.assertEqual("stop", result.finish_reason)
        self.assertEqual(0, len(result.tool_calls))

    def test_custom_response_fn(self) -> None:
        def my_fn(messages: list[LLMMessage], tools: Any) -> LLMCompletion:
            return LLMCompletion(content="Custom from fn", model="test")

        provider = StubLLMProvider(response_fn=my_fn)
        result = provider.complete(messages=[LLMMessage(role="user", content="anything")])
        self.assertEqual("Custom from fn", result.content)

    def test_model_override(self) -> None:
        provider = StubLLMProvider(model_name="my-model")
        result = provider.complete(messages=[LLMMessage(role="user", content="Hi")])
        self.assertEqual("my-model", result.model)


class LLMPlannerWithProviderTests(unittest.TestCase):
    """LLMPlanner using a real LLMPort (StubLLMProvider)."""

    def test_text_response_becomes_finalize_step(self) -> None:
        provider = StubLLMProvider(default_response="Hallo! Wie kann ich helfen?")
        planner = LLMPlanner(llm=provider)
        ctx = _make_ctx()
        event = _make_event("Hi")
        planning_context = PlanningContext()

        plan = planner.create_initial_plan(ctx, event, planning_context)
        self.assertEqual(1, len(plan.steps))
        self.assertEqual("finalize", plan.steps[0].kind)
        self.assertEqual("Hallo! Wie kann ich helfen?", plan.steps[0].response_text)

    def test_tool_call_becomes_tool_call_step(self) -> None:
        provider = StubLLMProvider()
        planner = LLMPlanner(llm=provider)
        ctx = _make_ctx()
        event = _make_event("Zeig mir meinen Postkorb")
        tool_schema = {
            "type": "function",
            "function": {"name": "mail.list_new_messages", "description": "List mail"},
        }
        planning_context = PlanningContext(tool_schemas=[tool_schema])

        plan = planner.create_initial_plan(ctx, event, planning_context)
        self.assertEqual(1, len(plan.steps))
        self.assertEqual("tool_call", plan.steps[0].kind)
        self.assertEqual("mail.list_new_messages", plan.steps[0].tool_name)

    def test_revise_plan_after_tool(self) -> None:
        """After a tool call, the planner calls the LLM again with results."""
        call_count = [0]

        def response_fn(messages: list[LLMMessage], tools: Any) -> LLMCompletion:
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: request tool
                return LLMCompletion(
                    tool_calls=[LLMToolCall(id="call-1", name="mail.list_new_messages", arguments={"limit": 5})],
                    finish_reason="tool_calls",
                )
            # Second call: finalize with results
            return LLMCompletion(content="Du hast 3 neue Nachrichten.", finish_reason="stop")

        provider = StubLLMProvider(response_fn=response_fn)
        planner = LLMPlanner(llm=provider)
        ctx = _make_ctx()
        event = _make_event("Postkorb")
        tool_schema = {
            "type": "function",
            "function": {"name": "mail.list_new_messages", "description": "List mail"},
        }
        planning_context = PlanningContext(tool_schemas=[tool_schema])

        # First call
        plan1 = planner.create_initial_plan(ctx, event, planning_context)
        self.assertEqual("tool_call", plan1.steps[0].kind)

        # Revise after tool execution
        results = {"mail.list_new_messages": [{"id": 1}, {"id": 2}, {"id": 3}]}
        plan2 = planner.revise_plan_after_tool(ctx, event, plan1, results, planning_context)
        self.assertEqual("finalize", plan2.steps[0].kind)
        self.assertEqual("Du hast 3 neue Nachrichten.", plan2.steps[0].response_text)
        self.assertEqual(2, call_count[0])


class LLMPlannerWithoutProviderTests(unittest.TestCase):
    """LLMPlanner without LLMPort falls back to keyword matching."""

    def test_keyword_fallback_postkorb(self) -> None:
        planner = LLMPlanner()  # No LLM
        ctx = _make_ctx()
        event = _make_event("Zeig mir meinen Postkorb")
        plan = planner.create_initial_plan(ctx, event, PlanningContext())
        self.assertEqual("tool_call", plan.steps[0].kind)
        self.assertEqual("mail.list_new_messages", plan.steps[0].tool_name)

    def test_keyword_fallback_generic(self) -> None:
        planner = LLMPlanner()
        ctx = _make_ctx()
        event = _make_event("Wie ist das Wetter?")
        plan = planner.create_initial_plan(ctx, event, PlanningContext())
        self.assertEqual("finalize", plan.steps[0].kind)
        self.assertIn("Prototypen", plan.steps[0].response_text)


class MessageBuildingTests(unittest.TestCase):
    """Test that the planner builds proper messages from context."""

    def test_system_prompt_includes_firmware(self) -> None:
        messages_captured: list[list[LLMMessage]] = []

        def capture_fn(messages: list[LLMMessage], tools: Any) -> LLMCompletion:
            messages_captured.append(messages)
            return LLMCompletion(content="OK")

        provider = StubLLMProvider(response_fn=capture_fn)
        planner = LLMPlanner(llm=provider)
        ctx = _make_ctx()
        event = _make_event("Hi")
        planning_context = PlanningContext(
            firmware_documents={"AGENTS": "Du bist ein hilfreicher Assistent."},
        )

        planner.create_initial_plan(ctx, event, planning_context)
        msgs = messages_captured[0]
        system_msg = msgs[0]
        self.assertEqual("system", system_msg.role)
        self.assertIn("hilfreicher Assistent", system_msg.content)

    def test_core_memory_in_system_prompt(self) -> None:
        messages_captured: list[list[LLMMessage]] = []

        def capture_fn(messages: list[LLMMessage], tools: Any) -> LLMCompletion:
            messages_captured.append(messages)
            return LLMCompletion(content="OK")

        provider = StubLLMProvider(response_fn=capture_fn)
        planner = LLMPlanner(llm=provider)
        ctx = _make_ctx()
        event = _make_event("Hi")
        planning_context = PlanningContext(
            memory_context={"core_memory": "- Likes dark mode\n- Works on Cephix"},
        )

        planner.create_initial_plan(ctx, event, planning_context)
        msgs = messages_captured[0]
        system_msg = msgs[0]
        self.assertIn("Core Memory", system_msg.content)
        self.assertIn("dark mode", system_msg.content)

    def test_recent_interactions_as_history(self) -> None:
        messages_captured: list[list[LLMMessage]] = []

        def capture_fn(messages: list[LLMMessage], tools: Any) -> LLMCompletion:
            messages_captured.append(messages)
            return LLMCompletion(content="OK")

        provider = StubLLMProvider(response_fn=capture_fn)
        planner = LLMPlanner(llm=provider)
        ctx = _make_ctx()
        event = _make_event("Und jetzt?")
        planning_context = PlanningContext(
            memory_context={
                "recent_interactions": [
                    {"user_text": "Hallo", "robot_text": "Hi!"},
                    {"user_text": "Wie geht's?", "robot_text": "Gut!"},
                ],
            },
        )

        planner.create_initial_plan(ctx, event, planning_context)
        msgs = messages_captured[0]

        # Should have: user "Hallo", assistant "Hi!", user "Wie geht's?", assistant "Gut!", user "Und jetzt?"
        roles = [m.role for m in msgs]
        self.assertEqual(["user", "assistant", "user", "assistant", "user"], roles)
        self.assertEqual("Und jetzt?", msgs[-1].content)


if __name__ == "__main__":
    unittest.main()
