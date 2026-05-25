from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from src.context import DefaultContextAssembler, FirmwareHeartbeat, MarkdownFirmwareStore, MarkdownMemoryDocumentStore
from src.domain import ReplyTarget, RobotEvent
from src.memory import InMemoryMemoryStore


class ContextTests(unittest.TestCase):
    def test_markdown_firmware_store_returns_base_guidance_only_for_existing_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "AGENTS.md").write_text("agents", encoding="utf-8")
            (root / "CONSTITUTION.md").write_text("constitution", encoding="utf-8")

            store = MarkdownFirmwareStore(root)

            self.assertEqual(
                {"AGENTS.md": "agents", "CONSTITUTION.md": "constitution"},
                store.get_base_guidance(),
            )

    def test_markdown_firmware_store_returns_heartbeat_instruction(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "HEARTBEAT.md").write_text("Check pending loops.", encoding="utf-8")

            store = MarkdownFirmwareStore(root)

            self.assertEqual("Check pending loops.", store.get_event_instruction("heartbeat.tick"))
            self.assertEqual("", store.get_event_instruction("message.received"))

    def test_markdown_memory_document_store_skips_user_for_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "IDENTITY.md").write_text("identity", encoding="utf-8")
            (root / "TOOLS.md").write_text("tools", encoding="utf-8")
            (root / "MEMORY.md").write_text("memory", encoding="utf-8")
            (root / "USER.md").write_text("user", encoding="utf-8")
            store = MarkdownMemoryDocumentStore(root)

            heartbeat_event = RobotEvent(
                event_id="evt-1",
                event_type="heartbeat.tick",
                source_channel="heartbeat",
            )
            message_event = RobotEvent(
                event_id="evt-2",
                event_type="message.received",
                source_channel="telegram",
                sender_id="user-1",
            )

            heartbeat_docs = store.get_documents(heartbeat_event, "system")
            message_docs = store.get_documents(message_event, "user-1")

            self.assertNotIn("USER.md", heartbeat_docs)
            self.assertIn("USER.md", message_docs)

    def test_context_assembler_combines_base_guidance_event_instruction_and_memory(self) -> None:
        class StubFirmware:
            def get_base_guidance(self) -> dict[str, str]:
                return {"AGENTS.md": "agents", "POLICY.md": "policy"}

            def get_event_instruction(self, event_type: str) -> str:
                return "heartbeat" if event_type == "heartbeat.tick" else ""

        class StubMemoryDocuments:
            def get_documents(self, event, user_id) -> dict[str, str]:
                return {"IDENTITY.md": "identity"}

        memory_store = InMemoryMemoryStore()
        memory_store.remember_fact("user-1", "response_style", "prefers concise answers")

        assembler = DefaultContextAssembler(
            firmware=StubFirmware(),
            memory_documents=StubMemoryDocuments(),
            memory_store=memory_store,
        )

        context = assembler.assemble(
            RobotEvent(event_id="evt-1", event_type="heartbeat.tick", source_channel="heartbeat"),
            "user-1",
        )

        self.assertEqual("agents", context.firmware_documents["AGENTS.md"])
        self.assertEqual("heartbeat", context.firmware_documents["heartbeat.tick.instruction"])
        self.assertEqual("identity", context.memory_documents["IDENTITY.md"])
        self.assertEqual("prefers concise answers", context.memory_context["facts"][0]["content"])

    def test_firmware_heartbeat_returns_none_for_blank_instruction(self) -> None:
        class StubFirmware:
            def get_base_guidance(self) -> dict[str, str]:
                return {}

            def get_event_instruction(self, event_type: str) -> str:
                return "   "

        heartbeat = FirmwareHeartbeat(firmware=StubFirmware())

        self.assertIsNone(heartbeat.build_idle_event())

    def test_firmware_heartbeat_uses_default_output_target(self) -> None:
        class StubFirmware:
            def get_base_guidance(self) -> dict[str, str]:
                return {}

            def get_event_instruction(self, event_type: str) -> str:
                return "Review follow-ups."

        target = ReplyTarget(channel="telegram", recipient_id="user-1", mode="notify")
        heartbeat = FirmwareHeartbeat(firmware=StubFirmware(), default_output_target=target)

        event = heartbeat.build_idle_event()

        assert event is not None
        self.assertEqual("heartbeat.tick", event.event_type)
        self.assertEqual("Review follow-ups.", event.text)
        self.assertEqual("telegram", event.reply_target.channel)


if __name__ == "__main__":
    unittest.main()
