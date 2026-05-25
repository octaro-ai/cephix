from __future__ import annotations

from src.memory.models import EpisodeRecord, MemoryDistillation, ProcedureRecord, ProfileFactRecord
from src.telemetry import WideEvent
from src.utils import new_id


class MemoryDistiller:
    def distill(self, events: list[WideEvent]) -> MemoryDistillation:
        if not events:
            return MemoryDistillation()

        episode = self._build_episode(events)
        profile_facts = self._extract_profile_facts(events)
        procedures = self._extract_procedures(events, episode.episode_id)
        return MemoryDistillation(
            episodes=[episode],
            profile_facts=profile_facts,
            procedures=procedures,
        )

    def _build_episode(self, events: list[WideEvent]) -> EpisodeRecord:
        first = events[0]
        last = events[-1]
        input_event = next((event for event in events if event.event_type == "input.received"), first)
        output_event = next((event for event in events if event.event_type == "output.sent"), None)
        input_text = str(input_event.payload.get("text", "")).strip()
        output_text = str(output_event.payload.get("text", "")).strip() if output_event else None

        if input_text and output_text:
            summary = f"Input: {input_text} -> Output: {output_text}"
        elif input_text:
            summary = f"Input: {input_text}"
        else:
            summary = f"Run {first.run_id} processed {len(events)} events."

        return EpisodeRecord(
            episode_id=new_id("ep"),
            run_id=first.run_id,
            robot_id=first.robot_id,
            user_id=str(input_event.payload.get("sender_id") or "system"),
            conversation_id=first.conversation_id,
            summary=summary,
            started_at=first.timestamp,
            ended_at=last.timestamp,
            event_types=[event.event_type for event in events],
            source_event_ids=[event.event_id for event in events],
            output_text=output_text,
        )

    def _extract_profile_facts(self, events: list[WideEvent]) -> list[ProfileFactRecord]:
        input_event = next((event for event in events if event.event_type == "input.received"), None)
        if input_event is None:
            return []

        text = str(input_event.payload.get("text", "")).lower()
        subject_id = str(input_event.payload.get("sender_id") or "system")
        evidence = [input_event.event_id]
        facts: list[ProfileFactRecord] = []

        if "kurz" in text:
            facts.append(
                ProfileFactRecord(
                    fact_id=new_id("fact"),
                    subject_id=subject_id,
                    kind="response_style",
                    content="prefers concise answers",
                    confidence=0.8,
                    evidence_event_ids=evidence,
                )
            )
        if "zusammenfassung" in text or "postkorb" in text:
            facts.append(
                ProfileFactRecord(
                    fact_id=new_id("fact"),
                    subject_id=subject_id,
                    kind="task_preference",
                    content="likes summaries",
                    confidence=0.7,
                    evidence_event_ids=evidence,
                )
            )
        return facts

    def _extract_procedures(self, events: list[WideEvent], episode_id: str) -> list[ProcedureRecord]:
        requested = [event for event in events if event.event_type == "tool.requested"]
        procedures: list[ProcedureRecord] = []

        for event in requested:
            tool_name = str(event.payload.get("tool", "")).strip()
            if not tool_name:
                continue
            procedures.append(
                ProcedureRecord(
                    procedure_id=new_id("proc"),
                    name=f"{tool_name}.workflow.v1",
                    description=f"Execute {tool_name} and use the result in the current run.",
                    steps=[f"call {tool_name}", "revise the plan with the tool result", "finalize the response"],
                    source_episode_ids=[episode_id],
                    confidence=0.55,
                )
            )
        return procedures
