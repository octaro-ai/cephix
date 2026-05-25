from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from src.memory import (
    FileEpisodeStore,
    FileEventStore,
    FileProcedureStore,
    FileProfileStore,
    FirmwareLoader,
    MemoryDistiller,
    ProcedureRecord,
    ProfileFactRecord,
    RobotBrainExporter,
)
from src.telemetry import WideEvent
from src.utils import new_id, utc_now_iso


def make_event(*, event_type: str, payload: dict[str, object], run_id: str = "run-1") -> WideEvent:
    return WideEvent(
        event_id=new_id("evt"),
        event_type=event_type,
        timestamp=utc_now_iso(),
        run_id=run_id,
        trace_id="trace-1",
        robot_id="robot-1",
        conversation_id="conv-1",
        actor="test.actor",
        payload=payload,
    )


class MemoryStackTests(unittest.TestCase):
    def test_file_stores_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            event_store = FileEventStore(root / "events.jsonl")
            episode_store = FileEpisodeStore(root / "episodes.jsonl")
            profile_store = FileProfileStore(root / "profiles.json")
            procedure_store = FileProcedureStore(root / "procedures")

            event = make_event(event_type="input.received", payload={"sender_id": "user-1", "text": "Kurz bitte"})
            event_store.append(event)
            episode_store.append(
                distill_episode := MemoryDistiller().distill(
                    [
                        event,
                        make_event(event_type="output.sent", payload={"text": "Kurze Antwort"}, run_id=event.run_id),
                    ]
                ).episodes[0]
            )
            profile_store.upsert(
                ProfileFactRecord(
                    fact_id="fact-1",
                    subject_id="user-1",
                    kind="response_style",
                    content="prefers concise answers",
                    confidence=0.8,
                )
            )
            procedure_store.upsert(
                ProcedureRecord(
                    procedure_id="proc-1",
                    name="mail.list_new_messages.workflow.v1",
                    description="Read inbox items and summarize them.",
                )
            )

            self.assertEqual(1, len(event_store.list_events(run_id=event.run_id)))
            self.assertEqual(distill_episode.summary, episode_store.list_episodes(user_id="user-1")[0].summary)
            self.assertEqual("prefers concise answers", profile_store.list_facts(subject_id="user-1")[0].content)
            self.assertEqual("proc-1", procedure_store.list_procedures()[0].procedure_id)

    def test_memory_distiller_builds_episode_profile_and_procedure_candidates(self) -> None:
        distiller = MemoryDistiller()
        events = [
            make_event(
                event_type="input.received",
                payload={"sender_id": "user-1", "text": "Bitte kurz eine Zusammenfassung aus dem Postkorb"},
            ),
            make_event(
                event_type="tool.requested",
                payload={"tool": "mail.list_new_messages", "arguments": {"limit": 10}},
            ),
            make_event(
                event_type="output.sent",
                payload={"recipient_id": "user-1", "channel": "telegram", "text": "Hier ist die Zusammenfassung."},
            ),
            make_event(event_type="run.completed", payload={"final_state": "DONE"}),
        ]

        distillation = distiller.distill(events)

        self.assertEqual(1, len(distillation.episodes))
        self.assertIn("Input:", distillation.episodes[0].summary)
        self.assertEqual(2, len(distillation.profile_facts))
        self.assertEqual("mail.list_new_messages.workflow.v1", distillation.procedures[0].name)

    def test_brain_exporter_writes_cloneable_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            firmware_root = root / "firmware"
            firmware_root.mkdir(parents=True, exist_ok=True)
            (firmware_root / "SOUL.md").write_text("# SOUL\n\nKeep behavior stable.\n", encoding="utf-8")
            (firmware_root / "POLICY.md").write_text("# POLICY\n\nHuman owned.\n", encoding="utf-8")

            profile_store = FileProfileStore(root / "profiles.json")
            profile_store.upsert(
                ProfileFactRecord(
                    fact_id="fact-1",
                    subject_id="user-1",
                    kind="response_style",
                    content="prefers concise answers",
                    confidence=0.8,
                )
            )

            procedure_store = FileProcedureStore(root / "procedures")
            procedure_store.upsert(
                ProcedureRecord(
                    procedure_id="proc-1",
                    name="mail.list_new_messages.workflow.v1",
                    description="Read inbox items and summarize them.",
                )
            )

            exporter = RobotBrainExporter(
                robot_id="robot-1",
                firmware_loader=FirmwareLoader(firmware_root),
                profile_store=profile_store,
                procedure_store=procedure_store,
            )

            output_path = exporter.export_to_file(root / "brain.json")
            payload = json.loads(output_path.read_text(encoding="utf-8"))

            self.assertEqual("robot-1", payload["robot_id"])
            self.assertIn("SOUL.md", payload["firmware_documents"])
            self.assertEqual("prefers concise answers", payload["profile_facts"][0]["content"])
            self.assertEqual("proc-1", payload["procedures"][0]["procedure_id"])


if __name__ == "__main__":
    unittest.main()
