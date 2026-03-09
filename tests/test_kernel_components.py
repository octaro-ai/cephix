from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from src.bus import SemanticBus
from src.domain import DeliveryDirective, Plan, PlanStep, PlanningContext, ReplyTarget, RobotEvent
from src.memory import InMemoryMemoryStore
from src.runtime.kernel import DigitalRobotKernel
from src.telemetry import EventLog, Telemetry
from src.utils import new_id


class RecordingDelivery:
    def __init__(self) -> None:
        self.sent: list[tuple[ReplyTarget, str]] = []

    def send(self, target, message) -> None:
        self.sent.append((target, message.text))


class RecordingContextAssembler:
    def __init__(self, context: PlanningContext | None = None) -> None:
        self.context = context or PlanningContext(
            firmware_documents={"AGENTS.md": "agents"},
            memory_documents={"IDENTITY.md": "identity"},
            memory_context={"facts": [], "recent_interactions": []},
        )
        self.calls: list[tuple[RobotEvent, str]] = []

    def assemble(self, event, user_id) -> PlanningContext:
        self.calls.append((event, user_id))
        return self.context


class EmptyToolExecutor:
    def execute(self, ctx, tool_name, arguments):
        raise AssertionError("No tool execution expected in this test")


class KernelComponentTests(unittest.TestCase):
    def _build_kernel(
        self,
        *,
        planner,
        delivery=None,
        tool_executor=None,
        context_assembler=None,
        default_output_target=None,
        memory=None,
        log_path: Path,
    ) -> tuple[DigitalRobotKernel, RecordingDelivery, RecordingContextAssembler]:
        recording_delivery = delivery or RecordingDelivery()
        recording_context = context_assembler or RecordingContextAssembler()
        kernel = DigitalRobotKernel(
            robot_id="robot-1",
            default_output_target=default_output_target,
            message_delivery=recording_delivery,
            tool_executor=tool_executor or EmptyToolExecutor(),
            context_assembler=recording_context,
            planner=planner,
            memory=memory or InMemoryMemoryStore(),
            telemetry=Telemetry(EventLog(str(log_path))),
            bus=SemanticBus(),
        )
        return kernel, recording_delivery, recording_context

    def test_finalize_plan_sends_reply_target_and_updates_memory(self) -> None:
        class FinalizePlanner:
            def create_initial_plan(self, ctx, event, planning_context) -> Plan:
                return Plan(
                    plan_id=new_id("plan"),
                    goal="Respond immediately",
                    steps=[
                        PlanStep(
                            step_id=new_id("step"),
                            kind="finalize",
                            reason="Direct response",
                            response_text="Done.",
                        )
                    ],
                )

            def revise_plan_after_tool(self, ctx, event, previous_plan, results, planning_context) -> Plan:
                return previous_plan

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "events.jsonl"
            memory = InMemoryMemoryStore()
            kernel, delivery, context_assembler = self._build_kernel(
                planner=FinalizePlanner(),
                log_path=log_path,
                memory=memory,
            )
            event = RobotEvent(
                event_id="evt-1",
                event_type="message.received",
                source_channel="telegram",
                sender_id="user-1",
                text="Hi",
                reply_target=ReplyTarget(channel="telegram", recipient_id="user-1"),
            )

            kernel.handle_event(event)

            self.assertEqual(("telegram", "Done."), (delivery.sent[0][0].channel, delivery.sent[0][1]))
            self.assertEqual("user-1", context_assembler.calls[0][1])
            context = memory.build_context("user-1", None)
            self.assertEqual("Done.", context["recent_interactions"][0]["robot_text"])

    def test_silent_delivery_skips_egress(self) -> None:
        class SilentPlanner:
            def create_initial_plan(self, ctx, event, planning_context) -> Plan:
                return Plan(
                    plan_id=new_id("plan"),
                    goal="Silent finalize",
                    steps=[
                        PlanStep(
                            step_id=new_id("step"),
                            kind="finalize",
                            reason="No notification required",
                            response_text="Internal note",
                            delivery_directive=DeliveryDirective(mode="silent"),
                        )
                    ],
                )

            def revise_plan_after_tool(self, ctx, event, previous_plan, results, planning_context) -> Plan:
                return previous_plan

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "events.jsonl"
            kernel, delivery, _ = self._build_kernel(planner=SilentPlanner(), log_path=log_path)

            kernel.handle_event(
                RobotEvent(
                    event_id="evt-1",
                    event_type="message.received",
                    source_channel="telegram",
                    sender_id="user-1",
                    text="Hi",
                    reply_target=ReplyTarget(channel="telegram", recipient_id="user-1"),
                )
            )

            self.assertEqual([], delivery.sent)
            log_text = log_path.read_text(encoding="utf-8")
            self.assertNotIn('"event_type": "output.sent"', log_text)

    def test_missing_default_and_reply_target_fails(self) -> None:
        class FinalizePlanner:
            def create_initial_plan(self, ctx, event, planning_context) -> Plan:
                return Plan(
                    plan_id=new_id("plan"),
                    goal="Respond",
                    steps=[PlanStep(step_id=new_id("step"), kind="finalize", reason="Done", response_text="Done.")],
                )

            def revise_plan_after_tool(self, ctx, event, previous_plan, results, planning_context) -> Plan:
                return previous_plan

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "events.jsonl"
            kernel, _, _ = self._build_kernel(
                planner=FinalizePlanner(),
                log_path=log_path,
                default_output_target=None,
            )

            with self.assertRaisesRegex(RuntimeError, "No reply target available"):
                kernel.handle_event(RobotEvent(event_id="evt-1", event_type="cron.fired", source_channel="cron"))

            self.assertIn('"event_type": "run.failed"', log_path.read_text(encoding="utf-8"))

    def test_unavailable_delivery_channel_fails(self) -> None:
        class DirectivePlanner:
            def create_initial_plan(self, ctx, event, planning_context) -> Plan:
                return Plan(
                    plan_id=new_id("plan"),
                    goal="Respond",
                    steps=[
                        PlanStep(
                            step_id=new_id("step"),
                            kind="finalize",
                            reason="Switch channel",
                            response_text="Done.",
                            delivery_directive=DeliveryDirective(channel="slack"),
                        )
                    ],
                )

            def revise_plan_after_tool(self, ctx, event, previous_plan, results, planning_context) -> Plan:
                return previous_plan

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "events.jsonl"
            kernel, _, _ = self._build_kernel(planner=DirectivePlanner(), log_path=log_path)
            event = RobotEvent(
                event_id="evt-1",
                event_type="cron.fired",
                source_channel="cron",
                available_targets=[ReplyTarget(channel="telegram", recipient_id="user-1")],
            )

            with self.assertRaisesRegex(RuntimeError, "Delivery channel not available"):
                kernel.handle_event(event)

    def test_context_counts_are_logged(self) -> None:
        class FinalizePlanner:
            def create_initial_plan(self, ctx, event, planning_context) -> Plan:
                return Plan(
                    plan_id=new_id("plan"),
                    goal="Respond",
                    steps=[PlanStep(step_id=new_id("step"), kind="finalize", reason="Done", response_text="Done.")],
                )

            def revise_plan_after_tool(self, ctx, event, previous_plan, results, planning_context) -> Plan:
                return previous_plan

        planning_context = PlanningContext(
            firmware_documents={"AGENTS.md": "agents", "POLICY.md": "policy"},
            memory_documents={"IDENTITY.md": "identity"},
            memory_context={"facts": [{"kind": "x"}], "recent_interactions": [{"user_text": "x"}]},
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "events.jsonl"
            kernel, _, _ = self._build_kernel(
                planner=FinalizePlanner(),
                log_path=log_path,
                context_assembler=RecordingContextAssembler(planning_context),
                default_output_target=ReplyTarget(channel="telegram", recipient_id="user-1"),
            )

            kernel.handle_event(RobotEvent(event_id="evt-1", event_type="cron.fired", source_channel="cron"))

            log_text = log_path.read_text(encoding="utf-8")
            self.assertIn('"firmware_documents_count": 2', log_text)
            self.assertIn('"memory_documents_count": 1', log_text)
            self.assertIn('"facts_count": 1', log_text)


if __name__ == "__main__":
    unittest.main()
