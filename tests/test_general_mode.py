"""Tests for General Mode, System Tools, Autonomy Levels, and Procedure Proposal."""

from __future__ import annotations

import unittest
from typing import Any

from src.context import DefaultContextAssembler
from src.domain import AutonomyLevel, ExecutionContext, RobotEvent
from src.memory import InMemoryMemoryStore
from src.tools.collector import ToolCollector
from src.tools.models import ToolDefinition, ToolParameter
from src.tools.registry import InMemoryToolRegistry
from src.tools.system_tools import ALL_SYSTEM_TOOLS, SystemToolDriver


class _StubFirmware:
    def get_base_guidance(self) -> dict[str, str]:
        return {}

    def get_event_instruction(self, event_type: str) -> str:
        return ""


class _StubMemoryDocuments:
    def get_documents(self, event: object, user_id: str) -> dict[str, str]:
        return {}


class _StubDomainDriver:
    """Minimal ToolDriverPort for test domain tools."""

    def __init__(self, tools: list[ToolDefinition]) -> None:
        self._tools = tools

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools)

    def execute(self, ctx: object, tool_name: str, arguments: dict) -> Any:
        return {"stub": True, "tool": tool_name}


_DOMAIN_TOOLS = [
    ToolDefinition(name="mail.list", description="List emails"),
    ToolDefinition(name="crm.search", description="Search CRM"),
]


class GeneralModeTests(unittest.TestCase):
    """When no SOP/Skill matches, all catalog tools are mounted (General Mode)."""

    def test_general_mode_mounts_all_catalog_tools(self) -> None:
        catalog = ToolCollector([SystemToolDriver(memory=InMemoryMemoryStore()), _StubDomainDriver(_DOMAIN_TOOLS)])
        registry = InMemoryToolRegistry(catalog)
        assembler = DefaultContextAssembler(
            firmware=_StubFirmware(),
            memory_documents=_StubMemoryDocuments(),
            memory_store=InMemoryMemoryStore(),
            tool_registry=registry,
            tool_catalog=catalog,
            system_tool_definitions=ALL_SYSTEM_TOOLS,
        )

        event = RobotEvent(
            event_id="evt-1",
            event_type="message.received",
            source_channel="telegram",
            sender_id="user-1",
            text="Wie ist das Wetter?",
        )
        ctx = assembler.assemble(event, "user-1")

        mounted_names = {d.name for d in registry.list_mounted()}

        # All domain tools from the catalog are mounted
        self.assertIn("mail.list", mounted_names)
        self.assertIn("crm.search", mounted_names)

        # System tools are always mounted
        self.assertIn("memory.read", mounted_names)
        self.assertIn("memory.write", mounted_names)
        self.assertIn("memory.search", mounted_names)
        self.assertIn("procedure.propose", mounted_names)

        # Tool schemas are in the planning context
        schema_names = {s["function"]["name"] for s in ctx.tool_schemas}
        self.assertIn("mail.list", schema_names)
        self.assertIn("memory.read", schema_names)

    def test_sop_mode_mounts_only_required_tools_plus_system(self) -> None:
        """When an SOP matches, only SOP-required tools + system tools are mounted."""
        catalog = ToolCollector([SystemToolDriver(memory=InMemoryMemoryStore()), _StubDomainDriver(_DOMAIN_TOOLS)])
        registry = InMemoryToolRegistry(catalog)

        class StubSOPResolver:
            def resolve(self, event, user_id):
                from src.sop.models import SOPDefinition
                return [SOPDefinition(
                    name="inbox.check",
                    description="Check inbox",
                    version="1",
                    entry_node="scan",
                    required_tools=["mail.list"],
                )]

        assembler = DefaultContextAssembler(
            firmware=_StubFirmware(),
            memory_documents=_StubMemoryDocuments(),
            memory_store=InMemoryMemoryStore(),
            tool_registry=registry,
            tool_catalog=catalog,
            sop_resolver=StubSOPResolver(),
            system_tool_definitions=ALL_SYSTEM_TOOLS,
        )

        event = RobotEvent(
            event_id="evt-1",
            event_type="message.received",
            source_channel="telegram",
            sender_id="user-1",
            text="Postkorb check",
        )
        assembler.assemble(event, "user-1")

        mounted_names = {d.name for d in registry.list_mounted()}

        # SOP-required tool is mounted
        self.assertIn("mail.list", mounted_names)
        # Non-required domain tool is NOT mounted
        self.assertNotIn("crm.search", mounted_names)
        # System tools are still mounted
        self.assertIn("memory.read", mounted_names)
        self.assertIn("procedure.propose", mounted_names)

    def test_registry_is_clean_per_run(self) -> None:
        """Each assemble() call starts with a clean registry."""
        catalog = ToolCollector([SystemToolDriver(memory=InMemoryMemoryStore()), _StubDomainDriver(_DOMAIN_TOOLS)])
        registry = InMemoryToolRegistry(catalog)
        assembler = DefaultContextAssembler(
            firmware=_StubFirmware(),
            memory_documents=_StubMemoryDocuments(),
            memory_store=InMemoryMemoryStore(),
            tool_registry=registry,
            tool_catalog=catalog,
            system_tool_definitions=ALL_SYSTEM_TOOLS,
        )

        event = RobotEvent(
            event_id="evt-1",
            event_type="message.received",
            source_channel="telegram",
            text="Anything",
        )

        # First run: general mode mounts everything
        assembler.assemble(event, "user-1")
        first_count = len(registry.list_mounted())

        # Second run: registry should be reset and re-mounted cleanly
        assembler.assemble(event, "user-1")
        second_count = len(registry.list_mounted())

        self.assertEqual(first_count, second_count)


class AutonomyLevelTests(unittest.TestCase):
    """Test that autonomy levels control what gets mounted."""

    def _make_assembler(self, level: AutonomyLevel) -> tuple[DefaultContextAssembler, InMemoryToolRegistry]:
        catalog = ToolCollector([SystemToolDriver(memory=InMemoryMemoryStore()), _StubDomainDriver(_DOMAIN_TOOLS)])
        registry = InMemoryToolRegistry(catalog)
        assembler = DefaultContextAssembler(
            firmware=_StubFirmware(),
            memory_documents=_StubMemoryDocuments(),
            memory_store=InMemoryMemoryStore(),
            tool_registry=registry,
            tool_catalog=catalog,
            system_tool_definitions=ALL_SYSTEM_TOOLS,
            autonomy_level=level,
        )
        return assembler, registry

    def _assemble_general(self, assembler: DefaultContextAssembler) -> None:
        """Assemble with an event that matches no SOP (General Mode)."""
        event = RobotEvent(
            event_id="evt-1",
            event_type="message.received",
            source_channel="telegram",
            text="Something random",
        )
        assembler.assemble(event, "user-1")

    def test_scripted_mounts_nothing_without_sop(self) -> None:
        assembler, registry = self._make_assembler(AutonomyLevel.SCRIPTED)
        self._assemble_general(assembler)
        self.assertEqual([], registry.list_mounted())

    def test_guided_mounts_memory_tools_but_not_propose(self) -> None:
        assembler, registry = self._make_assembler(AutonomyLevel.GUIDED)
        self._assemble_general(assembler)
        mounted_names = {d.name for d in registry.list_mounted()}
        self.assertIn("memory.read", mounted_names)
        self.assertIn("memory.write", mounted_names)
        self.assertIn("memory.search", mounted_names)
        self.assertNotIn("procedure.propose", mounted_names)
        # GUIDED without SOP does not mount catalog tools
        self.assertNotIn("mail.list", mounted_names)

    def test_autonomous_mounts_full_catalog_in_general_mode(self) -> None:
        assembler, registry = self._make_assembler(AutonomyLevel.AUTONOMOUS)
        self._assemble_general(assembler)
        mounted_names = {d.name for d in registry.list_mounted()}
        self.assertIn("mail.list", mounted_names)
        self.assertIn("crm.search", mounted_names)
        self.assertIn("memory.read", mounted_names)
        # AUTONOMOUS still doesn't get procedure.propose
        self.assertNotIn("procedure.propose", mounted_names)

    def test_creative_mounts_everything_including_propose(self) -> None:
        assembler, registry = self._make_assembler(AutonomyLevel.CREATIVE)
        self._assemble_general(assembler)
        mounted_names = {d.name for d in registry.list_mounted()}
        self.assertIn("mail.list", mounted_names)
        self.assertIn("crm.search", mounted_names)
        self.assertIn("memory.read", mounted_names)
        self.assertIn("procedure.propose", mounted_names)

    def test_scripted_with_sop_mounts_only_required(self) -> None:
        """Even SCRIPTED mounts SOP tools when an SOP matches."""
        catalog = ToolCollector([SystemToolDriver(memory=InMemoryMemoryStore()), _StubDomainDriver(_DOMAIN_TOOLS)])
        registry = InMemoryToolRegistry(catalog)

        class StubSOPResolver:
            def resolve(self, event, user_id):
                from src.sop.models import SOPDefinition
                return [SOPDefinition(
                    name="inbox.check",
                    description="",
                    version="1",
                    entry_node="scan",
                    required_tools=["mail.list"],
                )]

        assembler = DefaultContextAssembler(
            firmware=_StubFirmware(),
            memory_documents=_StubMemoryDocuments(),
            memory_store=InMemoryMemoryStore(),
            tool_registry=registry,
            tool_catalog=catalog,
            sop_resolver=StubSOPResolver(),
            system_tool_definitions=ALL_SYSTEM_TOOLS,
            autonomy_level=AutonomyLevel.SCRIPTED,
        )
        event = RobotEvent(
            event_id="evt-1",
            event_type="message.received",
            source_channel="telegram",
            text="Postkorb check",
        )
        assembler.assemble(event, "user-1")

        mounted_names = {d.name for d in registry.list_mounted()}
        self.assertIn("mail.list", mounted_names)
        self.assertNotIn("crm.search", mounted_names)
        # SCRIPTED: no system tools even with SOP
        self.assertNotIn("memory.read", mounted_names)


class SystemToolHandlerTests(unittest.TestCase):
    def _make_ctx(self) -> ExecutionContext:
        return ExecutionContext(
            run_id="run-1",
            robot_id="robot-1",
            user_id="user-1",
            conversation_id="conv-1",
            channel="telegram",
            trace_id="trace-1",
        )

    def test_memory_write_and_read(self) -> None:
        memory = InMemoryMemoryStore()
        driver = SystemToolDriver(memory=memory)
        ctx = self._make_ctx()

        # Write
        result = driver.execute(ctx, "memory.write", {
            "user_id": "user-1",
            "kind": "preference",
            "content": "prefers dark mode",
            "score": 0.9,
        })
        self.assertTrue(result["stored"])

        # Read back
        result = driver.execute(ctx, "memory.read", {"user_id": "user-1"})
        self.assertEqual(1, len(result["facts"]))
        self.assertEqual("prefers dark mode", result["facts"][0]["content"])

    def test_memory_read_filters_by_kind(self) -> None:
        memory = InMemoryMemoryStore()
        memory.remember_fact("user-1", "preference", "likes dark mode")
        memory.remember_fact("user-1", "task_preference", "likes summaries")
        driver = SystemToolDriver(memory=memory)
        ctx = self._make_ctx()

        result = driver.execute(ctx, "memory.read", {"user_id": "user-1", "kind": "preference"})
        self.assertEqual(1, len(result["facts"]))
        self.assertEqual("likes dark mode", result["facts"][0]["content"])

    def test_memory_search(self) -> None:
        memory = InMemoryMemoryStore()
        memory.remember_fact("user-1", "preference", "prefers concise answers")
        memory.remember_fact("user-1", "preference", "likes dark mode")
        driver = SystemToolDriver(memory=memory)
        ctx = self._make_ctx()

        result = driver.execute(ctx, "memory.search", {"query": "concise"})
        self.assertEqual(1, len(result["matches"]))
        self.assertEqual(2, result["total_searched"])

    def test_procedure_propose_stores_candidate(self) -> None:
        memory = InMemoryMemoryStore()
        stored: list[Any] = []

        class FakeProcedureSink:
            def upsert(self, procedure):
                stored.append(procedure)

        driver = SystemToolDriver(memory=memory, procedure_sink=FakeProcedureSink())
        ctx = self._make_ctx()

        result = driver.execute(ctx, "procedure.propose", {
            "name": "weekly-report.v1",
            "description": "Generate weekly status report",
            "steps": "gather data, summarize, send report",
        })

        self.assertTrue(result["proposed"])
        self.assertEqual("proposed", result["status"])
        self.assertEqual(1, len(stored))
        self.assertEqual("proposed", stored[0].status)
        self.assertEqual(["gather data", "summarize", "send report"], stored[0].steps)

    def test_procedure_propose_works_without_sink(self) -> None:
        """Proposal works even without a procedure store (returns result, doesn't persist)."""
        driver = SystemToolDriver(memory=InMemoryMemoryStore())
        ctx = self._make_ctx()

        result = driver.execute(ctx, "procedure.propose", {
            "name": "test.v1",
            "description": "Test procedure",
            "steps": "step one, step two",
        })
        self.assertTrue(result["proposed"])


if __name__ == "__main__":
    unittest.main()
