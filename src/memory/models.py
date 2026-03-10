from __future__ import annotations

from dataclasses import dataclass, field

from src.utils import utc_now_iso


@dataclass
class EpisodeRecord:
    episode_id: str
    run_id: str
    robot_id: str
    user_id: str
    conversation_id: str | None
    summary: str
    started_at: str
    ended_at: str
    event_types: list[str] = field(default_factory=list)
    source_event_ids: list[str] = field(default_factory=list)
    output_text: str | None = None


@dataclass
class ProfileFactRecord:
    fact_id: str
    subject_id: str
    kind: str
    content: str
    confidence: float
    evidence_event_ids: list[str] = field(default_factory=list)
    updated_at: str = field(default_factory=utc_now_iso)


@dataclass
class ProcedureRecord:
    procedure_id: str
    name: str
    description: str
    steps: list[str] = field(default_factory=list)
    source_episode_ids: list[str] = field(default_factory=list)
    confidence: float = 0.5
    status: str = "active"
    updated_at: str = field(default_factory=utc_now_iso)


@dataclass
class MemoryDistillation:
    episodes: list[EpisodeRecord] = field(default_factory=list)
    profile_facts: list[ProfileFactRecord] = field(default_factory=list)
    procedures: list[ProcedureRecord] = field(default_factory=list)


@dataclass
class RobotBrainSnapshot:
    robot_id: str
    exported_at: str
    firmware_documents: dict[str, str]
    profile_facts: list[ProfileFactRecord]
    procedures: list[ProcedureRecord]
    notes: list[str] = field(default_factory=list)
