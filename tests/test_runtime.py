from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from src.bus import SemanticBus
from src.context import DefaultContextAssembler, FirmwareHeartbeat
from src.domain import Plan, PlanningContext, ReplyTarget, RobotEvent
from src.governance.composite import CompositeToolExecutionGuard
from src.gateways import ChannelHub, TelegramChannel
from src.memory import InMemoryMemoryStore
from src.planners import LLMPlanner
from src.app import build_demo_runtime
from src.runtime import DigitalRobotKernel, RuntimeEventLoop
from src.telemetry import EventLog, Telemetry
from src.tools.executor import GovernedToolExecutor
from src.tools.registry import InMemoryToolRegistry
from src.utils import new_id


class _EmptyToolExecutor:
    def execute(self, ctx, tool_name, arguments):
        return []


class RuntimeTests(unittest.TestCase):
    def test_idle_loop_is_zero_work_without_input(self) -> None:
        """When no external events exist and HEARTBEAT.md is empty, nothing runs."""

        class StubFirmware:
            def get_base_guidance(self) -> dict[str, str]:
                return {}

            def get_event_instruction(self, event_type: str) -> str:
                return ""

        class StubMemoryDocuments:
            def get_documents(self, event, user_id) -> dict[str, str]:
                return {}

        from src.context import DefaultContextAssembler, FirmwareHeartbeat

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "events.jsonl"
            firmware = StubFirmware()
            memory_store = InMemoryMemoryStore()
            telegram_channel = TelegramChannel()
            channel_hub = ChannelHub(ingress_ports=[telegram_channel], egress_ports={"telegram": telegram_channel})
            bus = SemanticBus()
            kernel = DigitalRobotKernel(
                robot_id="robot-1",
                default_output_target=ReplyTarget(channel="telegram", recipient_id="user-1", mode="notify"),
                message_delivery=channel_hub,
                tool_executor=_EmptyToolExecutor(),
                context_assembler=DefaultContextAssembler(
                    firmware=firmware,
                    memory_documents=StubMemoryDocuments(),
                    memory_store=memory_store,
                ),
                planner=LLMPlanner(),
                memory=memory_store,
                telemetry=Telemetry(EventLog(str(log_path))),
                bus=bus,
            )
            heartbeat = FirmwareHeartbeat(firmware=firmware)
            runtime = RuntimeEventLoop(kernel, channel_hub, heartbeat)

            did_run = runtime.run_once()

            self.assertFalse(did_run)
            self.assertEqual([], bus.messages)
            self.assertFalse(log_path.exists())

    def test_heartbeat_can_be_injected_without_external_events(self) -> None:
        class StubPlanner:
            def create_initial_plan(self, ctx, event, planning_context, **kwargs) -> Plan:
                return Plan(
                    plan_id=new_id("plan"),
                    goal="Heartbeat check",
                    steps=[],
                )

            def revise_plan_after_tool(self, ctx, event, previous_plan, results, planning_context, **kwargs) -> Plan:
                return previous_plan

        class StubHeartbeat:
            def build_idle_event(self) -> RobotEvent | None:
                return RobotEvent(
                    event_id=new_id("evt"),
                    event_type="heartbeat.tick",
                    source_channel="heartbeat",
                    text="Check open loops.",
                )

        class StubContextAssembler:
            def assemble(self, event, user_id) -> PlanningContext:
                return PlanningContext(
                    firmware_documents={"HEARTBEAT.md": "Check open loops."},
                    memory_documents={},
                    memory_context={},
                )

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "events.jsonl"
            telegram_channel = TelegramChannel()
            channel_hub = ChannelHub(ingress_ports=[telegram_channel], egress_ports={"telegram": telegram_channel})
            kernel = DigitalRobotKernel(
                robot_id="digital-robot-001",
                default_output_target=ReplyTarget(channel="telegram", recipient_id="user-telegram-42", mode="notify"),
                message_delivery=channel_hub,
                tool_executor=_EmptyToolExecutor(),
                context_assembler=StubContextAssembler(),
                planner=StubPlanner(),
                memory=InMemoryMemoryStore(),
                telemetry=Telemetry(EventLog(str(log_path))),
                bus=SemanticBus(),
            )
            runtime = RuntimeEventLoop(kernel, channel_hub, StubHeartbeat())

            with self.assertRaisesRegex(RuntimeError, "Planner returned a plan without steps"):
                runtime.run_once()

            self.assertTrue(log_path.exists())

    def test_demo_run_writes_events_and_bus_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "events.jsonl"
            runtime, bus = build_demo_runtime(log_path)

            runtime.push_event(
                RobotEvent(
                    event_id=new_id("evt"),
                    event_type="cron.fired",
                    source_channel="cron",
                    sender_id="user-42",
                    sender_name="Danny",
                    conversation_id="ops-inbox-summary",
                    text="Pruefe den Postkorb und antworte per Telegram.",
                    payload={"job": "check_inbox"},
                )
            )

            did_run = runtime.run_once()

            self.assertTrue(did_run)
            self.assertTrue(log_path.exists())
            self.assertEqual("input.received", bus.messages[0].name)
            self.assertEqual("plan.created", bus.messages[1].name)
            self.assertEqual("tool.requested", bus.messages[2].name)

            log_text = log_path.read_text(encoding="utf-8")
            self.assertIn('"event_type": "input.received"', log_text)
            self.assertIn('"actor": "gateway.cron"', log_text)
            self.assertIn('"actor": "gateway.telegram"', log_text)
            self.assertIn('"event_type": "run.completed"', log_text)

    def test_default_output_target_is_used_when_event_has_no_reply_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "events.jsonl"
            runtime, _ = build_demo_runtime(log_path)

            runtime.push_event(
                RobotEvent(
                    event_id=new_id("evt"),
                    event_type="cron.fired",
                    source_channel="cron",
                    sender_id="user-42",
                    sender_name="Danny",
                    conversation_id="ops-inbox-summary",
                    text="Pruefe den Postkorb und antworte per Telegram.",
                    payload={"job": "check_inbox"},
                )
            )

            runtime.run_once()

            log_text = log_path.read_text(encoding="utf-8")
            self.assertIn('"actor": "gateway.cron"', log_text)
            self.assertIn('"actor": "gateway.telegram"', log_text)
            self.assertIn('"delivery_channel": "telegram"', log_text)

    def test_reply_target_is_used_for_reply_capable_channels(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "events.jsonl"
            runtime, _ = build_demo_runtime(log_path)

            runtime.push_event(
                RobotEvent(
                    event_id=new_id("evt"),
                    event_type="message.received",
                    source_channel="telegram",
                    sender_id="user-telegram-42",
                    sender_name="Danny",
                    conversation_id="tg-conv-001",
                    text="Was ist neu in meinem Postkorb?",
                    reply_target=ReplyTarget(
                        channel="telegram",
                        recipient_id="user-telegram-42",
                        conversation_id="tg-conv-001",
                        mode="reply",
                    ),
                )
            )

            runtime.run_once()

            log_text = log_path.read_text(encoding="utf-8")
            self.assertIn('"reply_target_channel": "telegram"', log_text)
            self.assertIn('"delivery_channel": "telegram"', log_text)

    def test_empty_plan_fails_with_explicit_runtime_error(self) -> None:
        class EmptyPlanner:
            def create_initial_plan(self, ctx, event, planning_context, **kwargs) -> Plan:
                return Plan(plan_id=new_id("plan"), goal="broken plan", steps=[])

            def revise_plan_after_tool(self, ctx, event, previous_plan, results, planning_context, **kwargs) -> Plan:
                return previous_plan

        class StubFirmware:
            def get_base_guidance(self) -> dict[str, str]:
                return {}

            def get_event_instruction(self, event_type: str) -> str:
                return ""

        class StubMemoryDocuments:
            def get_documents(self, event, user_id) -> dict[str, str]:
                return {}

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "events.jsonl"
            telegram_channel = TelegramChannel()
            channel_hub = ChannelHub(ingress_ports=[telegram_channel], egress_ports={"telegram": telegram_channel})
            tool_executor = _EmptyToolExecutor()
            kernel = DigitalRobotKernel(
                robot_id="digital-robot-001",
                default_output_target=ReplyTarget(
                    channel="telegram",
                    recipient_id="user-telegram-42",
                    conversation_id="tg-conv-001",
                    mode="notify",
                ),
                message_delivery=channel_hub,
                tool_executor=tool_executor,
                context_assembler=DefaultContextAssembler(
                    firmware=StubFirmware(),
                    memory_documents=StubMemoryDocuments(),
                    memory_store=InMemoryMemoryStore(),
                ),
                planner=EmptyPlanner(),
                memory=InMemoryMemoryStore(),
                telemetry=Telemetry(EventLog(str(log_path))),
                bus=SemanticBus(),
            )
            runtime = RuntimeEventLoop(kernel, channel_hub)

            runtime.push_event(
                RobotEvent(
                    event_id=new_id("evt"),
                    event_type="message.received",
                    source_channel="telegram",
                    sender_id="user-telegram-42",
                    sender_name="Danny",
                    conversation_id="tg-conv-001",
                    text="Was ist neu in meinem Postkorb?",
                    reply_target=ReplyTarget(
                        channel="telegram",
                        recipient_id="user-telegram-42",
                        conversation_id="tg-conv-001",
                    ),
                )
            )

            with self.assertRaisesRegex(RuntimeError, "Planner returned a plan without steps"):
                runtime.run_once()

            log_text = log_path.read_text(encoding="utf-8")
            self.assertIn('"event_type": "run.failed"', log_text)


if __name__ == "__main__":
    unittest.main()
