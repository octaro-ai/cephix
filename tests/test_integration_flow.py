"""Comprehensive integration tests for the full robot flow.

These tests wire up a complete robot with StubLLMProvider and verify
that all ports are exercised: LLMPort, MemoryPort (including core memory
and compaction), Telemetry, SemanticBus, and message delivery.

The goal is to catch wiring issues *before* a real LLM provider is plugged in.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from src.bus import SemanticBus
from src.context import DefaultContextAssembler
from src.domain import (
    ExecutionContext,
    MessageRecord,
    PlanningContext,
    ReplyTarget,
    RobotEvent,
)
from src.gateways import ChannelHub, TelegramChannel
from src.llm import LLMCompletion, LLMMessage, LLMToolCall, StubLLMProvider
from src.memory import InMemoryMemoryStore, TruncatingCompactor
from src.planners import LLMPlanner
from src.runtime import DigitalRobotKernel, RuntimeEventLoop
from src.telemetry import EventLog, Telemetry
from src.tools.collector import ToolCollector
from src.tools.executor import GovernedToolExecutor
from src.tools.models import ToolDefinition, ToolParameter
from src.tools.registry import InMemoryToolRegistry
from src.tools.system_tools import ALL_SYSTEM_TOOLS, SystemToolDriver
from src.governance.composite import CompositeToolExecutionGuard
from src.app import InlineToolDriver
from src.utils import new_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEMO_MESSAGES = [
    MessageRecord(
        message_id="m1",
        sender="Anna Becker <anna@example.com>",
        subject="Termin am Dienstag bestaetigt",
        body="Der Termin am Dienstag um 10 Uhr passt.",
        received_at="2026-03-08T08:10:00Z",
        unread=True,
    ),
]


class _StubFirmware:
    def get_base_guidance(self) -> dict[str, str]:
        return {"AGENTS.md": "You are a helpful digital robot."}

    def get_event_instruction(self, event_type: str) -> str:
        return ""


class _StubMemoryDocuments:
    def get_documents(self, event: RobotEvent, user_id: str) -> dict[str, str]:
        return {"IDENTITY.md": "I am Cephix, a digital robot."}


def _mail_list_handler(ctx: ExecutionContext, arguments: dict[str, Any]) -> list[MessageRecord]:
    limit = int(arguments.get("limit", 10))
    return [m for m in _DEMO_MESSAGES if m.unread][:limit]


def _build_kernel(
    *,
    log_path: Path,
    llm: StubLLMProvider | None = None,
    memory: InMemoryMemoryStore | None = None,
) -> tuple[DigitalRobotKernel, SemanticBus, InMemoryMemoryStore]:
    """Build a fully wired kernel with all ports connected."""
    bus = SemanticBus()
    memory = memory or InMemoryMemoryStore()
    telegram = TelegramChannel()
    channel_hub = ChannelHub(ingress_ports=[telegram], egress_ports={"telegram": telegram})

    mail_tool = ToolDefinition(
        name="mail.list_new_messages",
        description="List new inbox messages",
        parameters=[
            ToolParameter(name="limit", type="integer", description="Max messages", required=False),
        ],
    )
    domain_driver = InlineToolDriver()
    domain_driver.register(mail_tool, _mail_list_handler)
    system_driver = SystemToolDriver(memory=memory)
    collector = ToolCollector([system_driver, domain_driver])
    registry = InMemoryToolRegistry(collector)
    guard = CompositeToolExecutionGuard()
    executor = GovernedToolExecutor(registry=registry, guard=guard, collector=collector)

    context_assembler = DefaultContextAssembler(
        firmware=_StubFirmware(),
        memory_documents=_StubMemoryDocuments(),
        memory_store=memory,
        tool_registry=registry,
        tool_catalog=collector,
        system_tool_definitions=ALL_SYSTEM_TOOLS,
    )

    kernel = DigitalRobotKernel(
        robot_id="test-robot",
        default_output_target=ReplyTarget(
            channel="telegram", recipient_id="user-1", mode="notify",
        ),
        message_delivery=channel_hub,
        tool_executor=executor,
        context_assembler=context_assembler,
        planner=LLMPlanner(llm=llm),
        memory=memory,
        telemetry=Telemetry(EventLog(str(log_path))),
        bus=bus,
    )
    return kernel, bus, memory


def _make_event(
    text: str,
    *,
    sender_id: str = "user-42",
    conversation_id: str = "conv-test-1",
    source_channel: str = "telegram",
    reply_target: ReplyTarget | None = None,
    payload: dict[str, Any] | None = None,
) -> RobotEvent:
    return RobotEvent(
        event_id=new_id("evt"),
        event_type="message.received",
        source_channel=source_channel,
        sender_id=sender_id,
        sender_name="Test User",
        conversation_id=conversation_id,
        text=text,
        payload=payload or {},
        reply_target=reply_target,
    )


def _read_telemetry_events(log_path: Path) -> list[dict[str, Any]]:
    if not log_path.exists():
        return []
    lines = log_path.read_text(encoding="utf-8").strip().split("\n")
    return [json.loads(line) for line in lines if line.strip()]


# ===========================================================================
# Test: Full flow with StubLLMProvider — direct response (no tool call)
# ===========================================================================


class DirectResponseFlowTests(unittest.TestCase):
    """LLM returns a direct text response without calling any tools."""

    def test_direct_response_flow_emits_all_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "events.jsonl"
            stub = StubLLMProvider(default_response="Hallo! Wie kann ich helfen?")
            kernel, bus, memory = _build_kernel(log_path=log_path, llm=stub)

            event = _make_event("Hallo Roboter")
            kernel.handle_event(event)

            events = _read_telemetry_events(log_path)
            event_types = [e["event_type"] for e in events]

            self.assertIn("input.received", event_types)
            self.assertIn("memory.context_loaded", event_types)
            self.assertIn("plan.created", event_types)
            self.assertIn("response.created", event_types)
            self.assertIn("memory.updated", event_types)
            self.assertIn("output.sent", event_types)
            self.assertIn("run.completed", event_types)

    def test_direct_response_bus_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "events.jsonl"
            stub = StubLLMProvider(default_response="Alles klar!")
            kernel, bus, memory = _build_kernel(log_path=log_path, llm=stub)

            kernel.handle_event(_make_event("Hallo"))

            bus_names = [m.name for m in bus.messages]
            self.assertIn("input.received", bus_names)
            self.assertIn("plan.created", bus_names)

    def test_direct_response_stores_interaction_in_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "events.jsonl"
            stub = StubLLMProvider(default_response="Guten Tag!")
            kernel, bus, memory = _build_kernel(log_path=log_path, llm=stub)

            kernel.handle_event(_make_event("Hallo", sender_id="danny"))

            ctx = memory.build_context("danny", "conv-test-1")
            interactions = ctx["recent_interactions"]
            self.assertEqual(1, len(interactions))
            self.assertEqual("Hallo", interactions[0]["user_text"])
            self.assertEqual("Guten Tag!", interactions[0]["robot_text"])


# ===========================================================================
# Test: Multi-turn flow — tool_call → revise → finalize
# ===========================================================================


class MultiTurnToolCallFlowTests(unittest.TestCase):
    """LLM requests a tool call, gets results, then finalizes."""

    def test_tool_call_then_finalize(self) -> None:
        """Stub LLM calls mail.list_new_messages, then finalizes with summary."""
        call_count = 0

        def response_fn(
            messages: list[LLMMessage],
            tools: list[dict[str, Any]] | None,
        ) -> LLMCompletion:
            nonlocal call_count
            call_count += 1

            if call_count == 1:
                # First call: request tool
                return LLMCompletion(
                    tool_calls=[
                        LLMToolCall(
                            id=new_id("call"),
                            name="mail.list_new_messages",
                            arguments={"limit": 5},
                        )
                    ],
                    model="stub",
                    finish_reason="tool_calls",
                    usage={"prompt_tokens": 0, "completion_tokens": 0},
                )
            # Second call: finalize after seeing tool results
            return LLMCompletion(
                content="Du hast 1 neue Nachricht von Anna Becker.",
                model="stub",
                finish_reason="stop",
                usage={"prompt_tokens": 0, "completion_tokens": 0},
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "events.jsonl"
            stub = StubLLMProvider(response_fn=response_fn)
            kernel, bus, memory = _build_kernel(log_path=log_path, llm=stub)

            kernel.handle_event(_make_event("Was ist in meinem Postkorb?"))

            self.assertEqual(2, call_count)

            events = _read_telemetry_events(log_path)
            event_types = [e["event_type"] for e in events]

            self.assertIn("tool.requested", event_types)
            self.assertIn("tool.completed", event_types)
            self.assertIn("plan.revised", event_types)
            self.assertIn("response.created", event_types)
            self.assertIn("run.completed", event_types)

            # Bus should have tool.requested
            bus_names = [m.name for m in bus.messages]
            self.assertIn("tool.requested", bus_names)

    def test_multi_turn_conversation_history_grows(self) -> None:
        """After tool results, the LLM receives conversation history with tool results."""
        received_messages: list[list[LLMMessage]] = []

        def response_fn(
            messages: list[LLMMessage],
            tools: list[dict[str, Any]] | None,
        ) -> LLMCompletion:
            received_messages.append(list(messages))

            if len(received_messages) == 1:
                return LLMCompletion(
                    tool_calls=[
                        LLMToolCall(id="call_1", name="mail.list_new_messages", arguments={"limit": 5})
                    ],
                    model="stub",
                    finish_reason="tool_calls",
                    usage={},
                )
            return LLMCompletion(
                content="Zusammenfassung",
                model="stub",
                finish_reason="stop",
                usage={},
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "events.jsonl"
            stub = StubLLMProvider(response_fn=response_fn)
            kernel, bus, memory = _build_kernel(log_path=log_path, llm=stub)

            kernel.handle_event(_make_event("Postkorb pruefen"))

            # First call: system + user messages
            first_call = received_messages[0]
            roles_1 = [m.role for m in first_call]
            self.assertIn("system", roles_1)
            self.assertIn("user", roles_1)

            # Second call: should include tool result
            second_call = received_messages[1]
            roles_2 = [m.role for m in second_call]
            self.assertIn("tool", roles_2)


# ===========================================================================
# Test: Memory context flows through to LLM
# ===========================================================================


class MemoryContextInFlowTests(unittest.TestCase):
    """Verify that stored facts, core memory, and compaction summaries
    reach the LLM via the system prompt."""

    def test_facts_appear_in_llm_system_prompt(self) -> None:
        received_messages: list[list[LLMMessage]] = []

        def response_fn(messages, tools):
            received_messages.append(list(messages))
            return LLMCompletion(content="OK", model="stub", finish_reason="stop", usage={})

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "events.jsonl"
            memory = InMemoryMemoryStore()
            memory.remember_fact("user-42", "preference", "prefers concise answers")
            stub = StubLLMProvider(response_fn=response_fn)
            kernel, bus, _ = _build_kernel(log_path=log_path, llm=stub, memory=memory)

            kernel.handle_event(_make_event("Test", sender_id="user-42"))

            system_msg = next(m for m in received_messages[0] if m.role == "system")
            self.assertIn("prefers concise answers", system_msg.content)

    def test_core_memory_appears_in_llm_system_prompt(self) -> None:
        received_messages: list[list[LLMMessage]] = []

        def response_fn(messages, tools):
            received_messages.append(list(messages))
            return LLMCompletion(content="OK", model="stub", finish_reason="stop", usage={})

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "events.jsonl"
            memory = InMemoryMemoryStore()
            memory.set_core_memory("user-42", "Name: Danny\nRole: CEO")
            stub = StubLLMProvider(response_fn=response_fn)
            kernel, bus, _ = _build_kernel(log_path=log_path, llm=stub, memory=memory)

            kernel.handle_event(_make_event("Hallo", sender_id="user-42"))

            system_msg = next(m for m in received_messages[0] if m.role == "system")
            self.assertIn("Name: Danny", system_msg.content)
            self.assertIn("Role: CEO", system_msg.content)

    def test_compaction_summary_appears_in_system_prompt(self) -> None:
        received_messages: list[list[LLMMessage]] = []

        def response_fn(messages, tools):
            received_messages.append(list(messages))
            return LLMCompletion(content="OK", model="stub", finish_reason="stop", usage={})

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "events.jsonl"
            memory = InMemoryMemoryStore(
                compactor=TruncatingCompactor(),
                compaction_threshold=3,
                recent_window=2,
            )
            # Pre-fill 6 interactions so compaction triggers
            for i in range(6):
                memory.remember_interaction(
                    user_id="user-42",
                    conversation_id="conv-test-1",
                    user_text=f"Frage {i}",
                    robot_text=f"Antwort {i}",
                )

            stub = StubLLMProvider(response_fn=response_fn)
            kernel, bus, _ = _build_kernel(log_path=log_path, llm=stub, memory=memory)

            kernel.handle_event(_make_event("Naechste Frage", sender_id="user-42"))

            system_msg = next(m for m in received_messages[0] if m.role == "system")
            self.assertIn("Summary of", system_msg.content)
            self.assertIn("earlier message(s)", system_msg.content)

    def test_recent_interactions_appear_as_conversation_history(self) -> None:
        received_messages: list[list[LLMMessage]] = []

        def response_fn(messages, tools):
            received_messages.append(list(messages))
            return LLMCompletion(content="OK", model="stub", finish_reason="stop", usage={})

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "events.jsonl"
            memory = InMemoryMemoryStore()
            memory.remember_interaction(
                user_id="user-42",
                conversation_id="conv-test-1",
                user_text="Wie geht es dir?",
                robot_text="Mir geht es gut, danke!",
            )

            stub = StubLLMProvider(response_fn=response_fn)
            kernel, bus, _ = _build_kernel(log_path=log_path, llm=stub, memory=memory)

            kernel.handle_event(_make_event("Und jetzt?", sender_id="user-42"))

            msgs = received_messages[0]
            # Should have: system, user (history), assistant (history), user (current)
            user_msgs = [m for m in msgs if m.role == "user"]
            assistant_msgs = [m for m in msgs if m.role == "assistant"]
            self.assertTrue(any("Wie geht es dir?" in (m.content or "") for m in user_msgs))
            self.assertTrue(any("Mir geht es gut" in (m.content or "") for m in assistant_msgs))


# ===========================================================================
# Test: System tools via LLM (core_memory.update, memory.write)
# ===========================================================================


class SystemToolViaLLMTests(unittest.TestCase):
    """LLM calls system tools and the results flow back correctly."""

    def test_llm_calls_core_memory_update(self) -> None:
        call_count = 0

        def response_fn(messages, tools):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMCompletion(
                    tool_calls=[
                        LLMToolCall(
                            id="call_cm",
                            name="core_memory.update",
                            arguments={"user_id": "user-42", "content": "Name: Danny\nLiebt Kaffee"},
                        )
                    ],
                    model="stub",
                    finish_reason="tool_calls",
                    usage={},
                )
            return LLMCompletion(
                content="Ich habe mir das gemerkt!",
                model="stub",
                finish_reason="stop",
                usage={},
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "events.jsonl"
            stub = StubLLMProvider(response_fn=response_fn)
            kernel, bus, memory = _build_kernel(log_path=log_path, llm=stub)

            kernel.handle_event(_make_event("Ich heisse Danny und liebe Kaffee", sender_id="user-42"))

            self.assertEqual("Name: Danny\nLiebt Kaffee", memory.get_core_memory("user-42"))

    def test_llm_calls_memory_write(self) -> None:
        call_count = 0

        def response_fn(messages, tools):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMCompletion(
                    tool_calls=[
                        LLMToolCall(
                            id="call_mw",
                            name="memory.write",
                            arguments={
                                "user_id": "user-42",
                                "kind": "preference",
                                "content": "prefers formal language",
                                "score": 0.9,
                            },
                        )
                    ],
                    model="stub",
                    finish_reason="tool_calls",
                    usage={},
                )
            return LLMCompletion(content="Notiert!", model="stub", finish_reason="stop", usage={})

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "events.jsonl"
            stub = StubLLMProvider(response_fn=response_fn)
            kernel, bus, memory = _build_kernel(log_path=log_path, llm=stub)

            kernel.handle_event(_make_event("Bitte sieze mich", sender_id="user-42"))

            ctx = memory.build_context("user-42", None)
            facts = ctx["facts"]
            self.assertTrue(any(f["content"] == "prefers formal language" for f in facts))


# ===========================================================================
# Test: Full telemetry event sequence
# ===========================================================================


class TelemetrySequenceTests(unittest.TestCase):
    """Verify the exact telemetry event sequence for a multi-turn run."""

    def test_full_telemetry_sequence_with_tool_call(self) -> None:
        call_count = 0

        def response_fn(messages, tools):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMCompletion(
                    tool_calls=[
                        LLMToolCall(id="c1", name="mail.list_new_messages", arguments={"limit": 5})
                    ],
                    model="stub",
                    finish_reason="tool_calls",
                    usage={},
                )
            return LLMCompletion(content="Fertig.", model="stub", finish_reason="stop", usage={})

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "events.jsonl"
            stub = StubLLMProvider(response_fn=response_fn)
            kernel, bus, memory = _build_kernel(log_path=log_path, llm=stub)

            kernel.handle_event(_make_event("Postkorb"))

            events = _read_telemetry_events(log_path)
            event_types = [e["event_type"] for e in events]

            expected_sequence = [
                "input.received",
                "memory.context_loaded",
                "plan.created",
                "tool.requested",
                "tool.completed",
                "plan.revised",
                "response.created",
                "memory.updated",
                "output.sent",
                "run.completed",
            ]
            self.assertEqual(expected_sequence, event_types)

    def test_telemetry_actors_are_correct(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "events.jsonl"
            stub = StubLLMProvider(default_response="Hi")
            kernel, bus, memory = _build_kernel(log_path=log_path, llm=stub)

            kernel.handle_event(_make_event("Hi", source_channel="telegram"))

            events = _read_telemetry_events(log_path)
            actors = {e["event_type"]: e["actor"] for e in events}

            self.assertEqual("gateway.telegram", actors["input.received"])
            self.assertEqual("memory.store", actors["memory.context_loaded"])
            self.assertEqual("planner.llm", actors["plan.created"])
            self.assertEqual("planner.llm", actors["response.created"])
            self.assertEqual("memory.store", actors["memory.updated"])
            self.assertEqual("executive.kernel", actors["run.completed"])


# ===========================================================================
# Test: Tool schemas are passed to LLM
# ===========================================================================


class ToolSchemaFlowTests(unittest.TestCase):
    """Verify that mounted tool schemas reach the LLM provider."""

    def test_tool_schemas_are_passed_to_llm(self) -> None:
        received_tools: list[list[dict[str, Any]] | None] = []

        def response_fn(messages, tools):
            received_tools.append(tools)
            return LLMCompletion(content="OK", model="stub", finish_reason="stop", usage={})

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "events.jsonl"
            stub = StubLLMProvider(response_fn=response_fn)
            kernel, bus, memory = _build_kernel(log_path=log_path, llm=stub)

            kernel.handle_event(_make_event("Hallo"))

            tools = received_tools[0]
            self.assertIsNotNone(tools)
            tool_names = {t["function"]["name"] for t in tools}
            # Domain tool
            self.assertIn("mail.list_new_messages", tool_names)
            # System tools
            self.assertIn("memory.read", tool_names)
            self.assertIn("memory.write", tool_names)
            self.assertIn("core_memory.read", tool_names)
            self.assertIn("core_memory.update", tool_names)
            self.assertIn("procedure.propose", tool_names)


# ===========================================================================
# Test: Firmware + memory documents flow into LLM context
# ===========================================================================


class DocumentContextFlowTests(unittest.TestCase):
    """Verify firmware and memory documents appear in the LLM system prompt."""

    def test_firmware_documents_in_system_prompt(self) -> None:
        received_messages: list[list[LLMMessage]] = []

        def response_fn(messages, tools):
            received_messages.append(list(messages))
            return LLMCompletion(content="OK", model="stub", finish_reason="stop", usage={})

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "events.jsonl"
            stub = StubLLMProvider(response_fn=response_fn)
            kernel, bus, memory = _build_kernel(log_path=log_path, llm=stub)

            kernel.handle_event(_make_event("Test"))

            system_msg = next(m for m in received_messages[0] if m.role == "system")
            # From _StubFirmware
            self.assertIn("helpful digital robot", system_msg.content)
            # From _StubMemoryDocuments
            self.assertIn("Cephix", system_msg.content)


# ===========================================================================
# Test: Keyword fallback (no LLM) still works end-to-end
# ===========================================================================


class KeywordFallbackFlowTests(unittest.TestCase):
    """Without an LLM provider the keyword planner should still produce
    a valid end-to-end flow."""

    def test_keyword_flow_without_llm(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "events.jsonl"
            kernel, bus, memory = _build_kernel(log_path=log_path, llm=None)

            kernel.handle_event(_make_event("Zeig mir den Postkorb"))

            events = _read_telemetry_events(log_path)
            event_types = [e["event_type"] for e in events]

            self.assertIn("tool.requested", event_types)
            self.assertIn("run.completed", event_types)

    def test_keyword_unknown_request_finalizes_directly(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "events.jsonl"
            kernel, bus, memory = _build_kernel(log_path=log_path, llm=None)

            kernel.handle_event(_make_event("Wie wird das Wetter morgen?"))

            events = _read_telemetry_events(log_path)
            event_types = [e["event_type"] for e in events]

            self.assertNotIn("tool.requested", event_types)
            self.assertIn("response.created", event_types)
            self.assertIn("run.completed", event_types)


# ===========================================================================
# Test: Real instance integration — full context assembly from workspace
# ===========================================================================


class RealInstanceIntegrationTests(unittest.TestCase):
    """Tests using a real onboarded robot instance.

    These tests verify that the full context-assembly pipeline (firmware,
    memory documents, tool schemas) works correctly when wired from an
    actual workspace — not from test stubs.
    """

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.home_dir = self._tmpdir.name

        from src.configuration import onboard_robot_instance
        onboard_robot_instance(
            robot_id="test-bot",
            robot_name="TestBot",
            home_override=self.home_dir,
        )

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _build_kernel(
        self,
        *,
        llm: StubLLMProvider | None = None,
        log_path: Path | None = None,
    ):
        from src.app import build_kernel_for_instance

        log = log_path or Path(self.home_dir) / "events.jsonl"
        return build_kernel_for_instance(
            home_dir=self.home_dir,
            robot_id="test-bot",
            event_log_path=log,
            llm=llm,
            default_output_target=ReplyTarget(
                channel="test", recipient_id="user-1", mode="notify",
            ),
        )

    def test_system_prompt_contains_firmware_and_memory_documents(self) -> None:
        """Firmware (AGENTS.md, CONSTITUTION.md) and memory docs (BOOTSTRAP.md,
        IDENTITY.md) should all appear in the system prompt."""
        received_messages: list[list[LLMMessage]] = []

        def capture_fn(messages, tools):
            received_messages.append(list(messages))
            return LLMCompletion(content="Hallo!", model="stub", finish_reason="stop", usage={})

        stub = StubLLMProvider(response_fn=capture_fn)
        kernel, bus, memory = self._build_kernel(llm=stub)

        kernel.handle_event(_make_event("Hi"))

        system_msg = next(m for m in received_messages[0] if m.role == "system")

        # Firmware documents
        self.assertIn("AGENTS", system_msg.content)
        self.assertIn("CONSTITUTION", system_msg.content)
        self.assertIn("POLICY", system_msg.content)

        # Memory documents
        self.assertIn("BOOTSTRAP", system_msg.content)
        self.assertIn("IDENTITY", system_msg.content)

    def test_bootstrap_content_is_injected_into_system_prompt(self) -> None:
        """The actual BOOTSTRAP.md content (onboarding script) should be
        present in the system prompt — not requiring a document.read call."""
        received_messages: list[list[LLMMessage]] = []

        def capture_fn(messages, tools):
            received_messages.append(list(messages))
            return LLMCompletion(content="Hey!", model="stub", finish_reason="stop", usage={})

        stub = StubLLMProvider(response_fn=capture_fn)
        kernel, bus, memory = self._build_kernel(llm=stub)

        kernel.handle_event(_make_event("Hallo!"))

        system_msg = next(m for m in received_messages[0] if m.role == "system")

        # BOOTSTRAP.md key phrases
        self.assertIn("gerade aufgewacht", system_msg.content)
        self.assertIn("Dein Name", system_msg.content)

    def test_all_system_tools_are_mounted(self) -> None:
        """All 10 system tools + domain tools should be passed to the LLM."""
        received_tools: list[list[dict[str, Any]] | None] = []

        def capture_fn(messages, tools):
            received_tools.append(tools)
            return LLMCompletion(content="OK", model="stub", finish_reason="stop", usage={})

        stub = StubLLMProvider(response_fn=capture_fn)
        kernel, bus, memory = self._build_kernel(llm=stub)

        kernel.handle_event(_make_event("Test"))

        tools = received_tools[0]
        self.assertIsNotNone(tools)
        tool_names = {t["function"]["name"] for t in tools}

        # System tools
        self.assertIn("memory.read", tool_names)
        self.assertIn("memory.write", tool_names)
        self.assertIn("core_memory.read", tool_names)
        self.assertIn("core_memory.update", tool_names)
        self.assertIn("document.list", tool_names)
        self.assertIn("document.read", tool_names)
        self.assertIn("document.write", tool_names)
        self.assertIn("document.delete", tool_names)
        self.assertIn("procedure.propose", tool_names)

        # Domain tool
        self.assertIn("mail.list_new_messages", tool_names)

    def test_document_write_persists_to_workspace(self) -> None:
        """LLM calls document.write → file appears in instance memory dir."""
        from src.configuration import resolve_robot_instance
        instance = resolve_robot_instance(robot_id="test-bot", home_override=self.home_dir)
        call_count = 0

        def response_fn(messages, tools):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMCompletion(
                    tool_calls=[
                        LLMToolCall(
                            id="call_dw",
                            name="document.write",
                            arguments={"filename": "IDENTITY.md", "content": "# IDENTITY\nName: Aria\nEmoji: 🦊"},
                        )
                    ],
                    model="stub", finish_reason="tool_calls", usage={},
                )
            return LLMCompletion(content="Done!", model="stub", finish_reason="stop", usage={})

        stub = StubLLMProvider(response_fn=response_fn)
        kernel, bus, memory = self._build_kernel(llm=stub)

        kernel.handle_event(_make_event("Nenn mich Aria"))

        identity_path = instance.paths.memory_dir / "IDENTITY.md"
        self.assertTrue(identity_path.exists())
        content = identity_path.read_text(encoding="utf-8")
        self.assertIn("Aria", content)

    def test_multi_turn_tool_flow_with_real_instance(self) -> None:
        """Full multi-turn: LLM calls tool → gets result → finalizes."""
        call_count = 0

        def response_fn(messages, tools):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMCompletion(
                    tool_calls=[
                        LLMToolCall(id="c1", name="document.list", arguments={})
                    ],
                    model="stub", finish_reason="tool_calls", usage={},
                )
            # Second call: should have tool result in messages
            tool_msgs = [m for m in messages if m.role == "tool"]
            assert len(tool_msgs) > 0, "Tool result should be in conversation history"
            return LLMCompletion(
                content="Ich sehe die Memory-Dateien.",
                model="stub", finish_reason="stop", usage={},
            )

        stub = StubLLMProvider(response_fn=response_fn)
        log_path = Path(self.home_dir) / "events.jsonl"
        kernel, bus, memory = self._build_kernel(llm=stub, log_path=log_path)

        kernel.handle_event(_make_event("Was steht in meinem Memory?"))

        self.assertEqual(2, call_count)
        events = _read_telemetry_events(log_path)
        event_types = [e["event_type"] for e in events]
        self.assertIn("tool.requested", event_types)
        self.assertIn("tool.completed", event_types)
        self.assertIn("run.completed", event_types)


if __name__ == "__main__":
    unittest.main()
