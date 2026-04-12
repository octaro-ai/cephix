"""End-to-end tests for the Approval Flow (Replan statt Suspend).

Exercises the full chain:
  Event -> ActorResolver -> Kernel -> GovernedToolExecutor -> PolicyGuard
  -> ApprovalStore -> ApprovalPrompt -> approval.decision -> ApprovalStore
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

from src.bus import SemanticBus
from src.context import DefaultContextAssembler, MarkdownFirmwareStore, MarkdownMemoryDocumentStore
from src.domain import ApprovalPrompt, OutboundMessage, ReplyTarget, RobotEvent
from src.gateways.hub import ChannelHub
from src.governance.actor_resolver import ConfigBasedActorResolver
from src.governance.approval_store import FileApprovalStore
from src.governance.risk_classifier import MetadataRiskClassifier
from src.governance.tool_guard import PolicyToolExecutionGuard
from src.memory import InMemoryMemoryStore
from src.notebooks.store import FileNotebookStore
from src.planners import LLMPlanner
from src.runtime.kernel import DigitalRobotKernel
from src.telemetry import EventLog, Telemetry
from src.tools.collector import ToolCollector
from src.tools.executor import GovernedToolExecutor
from src.tools.models import ToolDefinition, ToolParameter
from src.tools.registry import InMemoryToolRegistry
from src.tools.system_tools import ALL_SYSTEM_TOOLS, SystemToolDriver
from src.utils import new_id

from src.llm.models import LLMCompletion, LLMToolCall
from src.llm import StubLLMProvider


# ---------------------------------------------------------------------------
# Fake channel that captures approval prompts
# ---------------------------------------------------------------------------

class FakeChannel:
    """Records all outbound messages and approval prompts for assertions."""

    def __init__(self, channel_id: str = "test") -> None:
        self.channel_id = channel_id
        self.messages: list[OutboundMessage] = []
        self.approval_prompts: list[ApprovalPrompt] = []
        self.chunks: list[str] = []
        self._incoming: list[RobotEvent] = []

    def drain_events(self) -> list[RobotEvent]:
        events = list(self._incoming)
        self._incoming.clear()
        return events

    def send(self, target: ReplyTarget, message: OutboundMessage) -> None:
        self.messages.append(message)

    def send_chunk(self, target: ReplyTarget, token: str) -> None:
        self.chunks.append(token)

    def send_chunk_clear(self, target: ReplyTarget) -> None:
        self.chunks.clear()

    def send_approval_prompt(self, target: ReplyTarget, prompt: ApprovalPrompt) -> None:
        self.approval_prompts.append(prompt)

    def inject_event(self, event: RobotEvent) -> None:
        self._incoming.append(event)


# ---------------------------------------------------------------------------
# Inline tool driver with risk-classified tools
# ---------------------------------------------------------------------------

class InlineTestToolDriver:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._handlers: dict[str, Any] = {}

    def register(self, defn: ToolDefinition, handler: Any) -> None:
        self._tools[defn.name] = defn
        self._handlers[defn.name] = handler

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def execute(self, ctx: Any, tool_name: str, arguments: dict[str, Any]) -> Any:
        return self._handlers[tool_name](ctx, arguments)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

def _make_event(text: str, sender_id: str = "owner") -> RobotEvent:
    return RobotEvent(
        event_id=new_id("evt"),
        event_type="message.received",
        source_channel="test",
        sender_id=sender_id,
        sender_name=sender_id,
        conversation_id="conv-1",
        text=text,
        reply_target=ReplyTarget(channel="test", recipient_id="client-1", conversation_id="conv-1"),
    )


def _make_approval_event(button_payload: dict[str, Any], sender_id: str = "owner") -> RobotEvent:
    return RobotEvent(
        event_id=new_id("evt"),
        event_type="approval.decision",
        source_channel="test",
        sender_id=sender_id,
        conversation_id="conv-1",
        payload=button_payload,
        reply_target=ReplyTarget(channel="test", recipient_id="client-1", conversation_id="conv-1"),
    )


@pytest.fixture
def workspace(tmp_path):
    """Create a minimal workspace with firmware files."""
    fw = tmp_path / "firmware"
    fw.mkdir()
    (fw / "AGENTS.md").write_text("Du bist ein Test-Roboter.", encoding="utf-8")
    (fw / "POLICY.md").write_text("Teste alles.", encoding="utf-8")
    (fw / "CONSTITUTION.md").write_text("Sei korrekt.", encoding="utf-8")

    mem = tmp_path / "memory"
    mem.mkdir()
    (mem / "IDENTITY.md").write_text("Test-Bot", encoding="utf-8")
    (mem / "MEMORY.md").write_text("", encoding="utf-8")
    return tmp_path


def _build_test_kernel(
    workspace: Path,
    llm_stub: StubLLMProvider,
    extra_tools: list[tuple[ToolDefinition, Any]] | None = None,
) -> tuple[DigitalRobotKernel, FakeChannel, FileApprovalStore, FileNotebookStore]:
    """Build a fully wired kernel with governance for testing."""
    channel = FakeChannel()
    hub = ChannelHub(ingress_ports=[channel], egress_ports={"test": channel})

    memory = InMemoryMemoryStore()
    firmware = MarkdownFirmwareStore(workspace / "firmware")
    memory_docs = MarkdownMemoryDocumentStore(workspace / "memory")

    approval_store = FileApprovalStore(workspace / "approvals")
    notebook_store = FileNotebookStore(workspace / "notebooks")
    actor_resolver = ConfigBasedActorResolver(principal_id="owner")

    domain_driver = InlineTestToolDriver()
    domain_driver.register(
        ToolDefinition(
            name="mail.move",
            description="Move a mail to a folder",
            parameters=[
                ToolParameter(name="uid", type="string", description="Mail UID"),
                ToolParameter(name="folder", type="string", description="Target folder"),
            ],
            metadata={"risk_class": "low_risk_mutation"},
        ),
        lambda ctx, args: {"status": "ok", "moved": args["uid"], "to": args["folder"]},
    )
    domain_driver.register(
        ToolDefinition(
            name="mail.list",
            description="List mails",
            parameters=[],
            metadata={"risk_class": "read_only"},
        ),
        lambda ctx, args: [{"uid": "1", "subject": "Test"}, {"uid": "2", "subject": "Offer"}],
    )
    domain_driver.register(
        ToolDefinition(
            name="mail.send",
            description="Send a mail",
            parameters=[
                ToolParameter(name="to", type="string", description="Recipient"),
                ToolParameter(name="subject", type="string", description="Subject"),
                ToolParameter(name="body", type="string", description="Body"),
            ],
            metadata={"risk_class": "high_risk_mutation"},
        ),
        lambda ctx, args: {"status": "ok", "sent_to": args["to"]},
    )

    for tool_def, handler in (extra_tools or []):
        domain_driver.register(tool_def, handler)

    system_driver = SystemToolDriver(memory=memory)
    collector = ToolCollector([system_driver, domain_driver])
    registry = InMemoryToolRegistry(collector)
    risk_classifier = MetadataRiskClassifier(registry=registry)
    guard = PolicyToolExecutionGuard(
        risk_classifier=risk_classifier,
        approval_store=approval_store,
        registry=registry,
    )
    executor = GovernedToolExecutor(registry=registry, guard=guard, collector=collector)

    context_assembler = DefaultContextAssembler(
        firmware=firmware,
        memory_documents=memory_docs,
        memory_store=memory,
        tool_registry=registry,
        tool_catalog=collector,
        system_tool_definitions=ALL_SYSTEM_TOOLS,
        notebook_store=notebook_store,
    )

    telemetry = Telemetry(EventLog(str(workspace / "events.jsonl")))
    bus = SemanticBus()

    kernel = DigitalRobotKernel(
        robot_id="test-bot",
        default_output_target=ReplyTarget(channel="test", recipient_id="client-1"),
        message_delivery=hub,
        tool_executor=executor,
        context_assembler=context_assembler,
        planner=LLMPlanner(llm=llm_stub),
        memory=memory,
        telemetry=telemetry,
        bus=bus,
        actor_resolver=actor_resolver,
        approval_store=approval_store,
        notebook_store=notebook_store,
    )

    return kernel, channel, approval_store, notebook_store


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestReadOnlyToolAllowed:
    def test_read_only_tool_executes_without_approval(self, workspace):
        """read_only tools should pass through the guard immediately."""
        call_count = 0

        def response_fn(messages, tools):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMCompletion(
                    tool_calls=[LLMToolCall(id="c1", name="mail.list", arguments={})],
                    model="stub", finish_reason="tool_calls", usage={},
                )
            return LLMCompletion(content="Hier sind deine Mails.", model="stub", finish_reason="stop", usage={})

        kernel, channel, _, _ = _build_test_kernel(workspace, StubLLMProvider(response_fn=response_fn))
        kernel.handle_event(_make_event("Zeige meine Mails"))

        assert len(channel.approval_prompts) == 0
        assert any("Mails" in m.text for m in channel.messages)


class TestMutationRequiresApproval:
    def test_low_risk_mutation_triggers_approval_prompt(self, workspace):
        """mail.move is low_risk_mutation => guard returns approval_required."""
        call_count = 0

        def response_fn(messages, tools):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMCompletion(
                    tool_calls=[LLMToolCall(id="c1", name="mail.move", arguments={"uid": "42", "folder": "Archive"})],
                    model="stub", finish_reason="tool_calls", usage={},
                )
            return LLMCompletion(
                content="Soll ich die Mail verschieben?",
                model="stub", finish_reason="stop", usage={},
            )

        kernel, channel, approval_store, _ = _build_test_kernel(workspace, StubLLMProvider(response_fn=response_fn))
        kernel.handle_event(_make_event("Archiviere Mail 42"))

        assert len(channel.approval_prompts) == 1
        prompt = channel.approval_prompts[0]
        assert prompt.action_context["action"] == "mail.move"
        assert len(prompt.buttons) == 4
        labels = [b.label for b in prompt.buttons]
        assert "Einmal" in labels
        assert "Immer so" in labels
        assert "Nein" in labels
        assert "Nie so" in labels


class TestFullApprovalCycle:
    def test_approve_once_then_execute(self, workspace):
        """Full cycle: request -> approval_required -> button click -> re-request -> allowed.

        Call sequence (approval.decision is handled deterministically, no LLM call):
          1. LLM: tool_call mail.move -> gets approval_required result
          2. LLM: revise -> finalize with question text
          (approval.decision: early return, no LLM involved)
          3. LLM: tool_call mail.move -> this time guard allows it
          4. LLM: revise -> finalize "Erledigt!"
        """
        call_count = 0

        def response_fn(messages, tools):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMCompletion(
                    tool_calls=[LLMToolCall(id="c1", name="mail.move", arguments={"uid": "42", "folder": "Archive"})],
                    model="stub", finish_reason="tool_calls", usage={},
                )
            if call_count == 2:
                return LLMCompletion(
                    content="Darf ich die Mail verschieben?",
                    model="stub", finish_reason="stop", usage={},
                )
            if call_count == 3:
                return LLMCompletion(
                    tool_calls=[LLMToolCall(id="c2", name="mail.move", arguments={"uid": "42", "folder": "Archive"})],
                    model="stub", finish_reason="tool_calls", usage={},
                )
            return LLMCompletion(content="Erledigt!", model="stub", finish_reason="stop", usage={})

        kernel, channel, approval_store, _ = _build_test_kernel(workspace, StubLLMProvider(response_fn=response_fn))

        # Step 1: Initial request triggers approval_required
        kernel.handle_event(_make_event("Archiviere Mail 42"))
        assert len(channel.approval_prompts) == 1
        prompt = channel.approval_prompts[0]

        # Step 2: User clicks "Einmal" -- deterministic, no LLM
        einmal_button = next(b for b in prompt.buttons if b.label == "Einmal")
        kernel.handle_event(_make_approval_event(einmal_button.payload))
        assert any("Freigabe" in m.text for m in channel.messages)

        # Step 3: Re-request the same action -- guard finds the once-rule, allows it
        channel.approval_prompts.clear()
        channel.messages.clear()
        kernel.handle_event(_make_event("Archiviere Mail 42"))

        assert len(channel.approval_prompts) == 0
        assert any("Erledigt" in m.text for m in channel.messages)

    def test_persistent_approval_works_across_runs(self, workspace):
        """'Immer so' creates a persistent rule that survives multiple runs.

        Call sequence:
          1. LLM: tool_call mail.move -> approval_required
          2. LLM: revise -> "Freigabe?"
          (approval.decision: early return, no LLM)
          3. LLM: tool_call mail.move -> allowed now (persistent rule)
          4. LLM: finalize "Verschoben!"
          5. LLM: tool_call mail.move -> still allowed (persistent)
          6. LLM: finalize "Nochmal verschoben!"
        """
        call_count = 0

        def response_fn(messages, tools):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMCompletion(
                    tool_calls=[LLMToolCall(id="c1", name="mail.move", arguments={"uid": "99", "folder": "Spam"})],
                    model="stub", finish_reason="tool_calls", usage={},
                )
            if call_count == 2:
                return LLMCompletion(content="Freigabe?", model="stub", finish_reason="stop", usage={})
            if call_count == 3:
                return LLMCompletion(
                    tool_calls=[LLMToolCall(id="c3", name="mail.move", arguments={"uid": "99", "folder": "Spam"})],
                    model="stub", finish_reason="tool_calls", usage={},
                )
            if call_count == 4:
                return LLMCompletion(content="Verschoben!", model="stub", finish_reason="stop", usage={})
            if call_count == 5:
                return LLMCompletion(
                    tool_calls=[LLMToolCall(id="c5", name="mail.move", arguments={"uid": "99", "folder": "Spam"})],
                    model="stub", finish_reason="tool_calls", usage={},
                )
            return LLMCompletion(content="Nochmal verschoben!", model="stub", finish_reason="stop", usage={})

        kernel, channel, approval_store, _ = _build_test_kernel(workspace, StubLLMProvider(response_fn=response_fn))

        # First run: approval_required
        kernel.handle_event(_make_event("Verschiebe Mail 99 nach Spam"))
        assert len(channel.approval_prompts) == 1
        prompt = channel.approval_prompts[0]

        # Click "Immer so" (persistent)
        immer_button = next(b for b in prompt.buttons if b.label == "Immer so")
        assert immer_button.payload["scope"] == "persistent"
        kernel.handle_event(_make_approval_event(immer_button.payload))

        # Verify: the stored rule must have scope=persistent, not once
        from src.governance.domain import ApprovalScope
        rule = approval_store.check("owner", "mail.move", target_scope="Spam")
        assert rule is not None, "No approval rule found after 'Immer so' click"
        assert rule.scope == ApprovalScope.PERSISTENT, f"Expected persistent, got {rule.scope}"

        # Second run: should be allowed without approval
        channel.approval_prompts.clear()
        channel.messages.clear()
        kernel.handle_event(_make_event("Verschiebe Mail 99 nach Spam"))
        assert len(channel.approval_prompts) == 0
        assert any("Verschoben" in m.text for m in channel.messages)

        # Third run: persistent means it still works
        channel.approval_prompts.clear()
        channel.messages.clear()
        kernel.handle_event(_make_event("Verschiebe Mail 99 nach Spam nochmal"))
        assert len(channel.approval_prompts) == 0
        assert any("Nochmal verschoben" in m.text for m in channel.messages)


class TestContextMapping:
    """Verify that context_mapping in tool metadata routes the correct argument keys
    into action_context source/target (and therefore into approval rules)."""

    def test_context_mapping_extracts_correct_source_and_target(self, workspace):
        """A tool with context_mapping should use mapped keys, not generic guesses."""
        call_count = 0

        def response_fn(messages, tools):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return LLMCompletion(
                    tool_calls=[LLMToolCall(
                        id="c1", name="mail.move_mapped",
                        arguments={"uid": "5", "source_folder": "INBOX", "destination_folder": "Archive"},
                    )],
                    model="stub", finish_reason="tool_calls", usage={},
                )
            return LLMCompletion(content="Approval noetig.", model="stub", finish_reason="stop", usage={})

        kernel, channel, _, _ = _build_test_kernel(
            workspace, StubLLMProvider(response_fn=response_fn),
            extra_tools=[
                (
                    ToolDefinition(
                        name="mail.move_mapped",
                        description="Move with context_mapping",
                        parameters=[
                            ToolParameter(name="uid", type="string", description="UID"),
                            ToolParameter(name="source_folder", type="string", description="From"),
                            ToolParameter(name="destination_folder", type="string", description="To"),
                        ],
                        metadata={
                            "risk_class": "low_risk_mutation",
                            "context_mapping": {"source": "source_folder", "target": "destination_folder"},
                        },
                    ),
                    lambda ctx, args: {"status": "ok"},
                ),
            ],
        )

        kernel.handle_event(_make_event("Verschiebe Mail 5 von INBOX nach Archive"))
        assert len(channel.approval_prompts) == 1

        prompt = channel.approval_prompts[0]
        ctx = prompt.action_context
        assert ctx["source"] == "INBOX", f"Expected INBOX, got {ctx.get('source')}"
        assert ctx["target"] == "Archive", f"Expected Archive, got {ctx.get('target')}"


class TestNotebookAuditEntriesRemoved:
    """Audit notebook entries were replaced by the structured log.
    This test verifies that NO audit entries are written anymore."""

    def test_no_audit_notebook_entries(self, workspace):
        def response_fn(messages, tools):
            return LLMCompletion(content="Alles klar.", model="stub", finish_reason="stop", usage={})

        kernel, _, _, notebook_store = _build_test_kernel(workspace, StubLLMProvider(response_fn=response_fn))
        kernel.handle_event(_make_event("Hallo"))

        from src.notebooks.models import NotebookType
        all_entries = notebook_store.load(NotebookType.USER_TASK, scope_id="owner:general")
        assert len(all_entries) == 0
