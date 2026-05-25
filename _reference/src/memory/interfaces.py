from __future__ import annotations

from typing import Protocol

from src.memory.models import EpisodeRecord, ProcedureRecord, ProfileFactRecord
from src.telemetry import WideEvent


class EventStorePort(Protocol):
    def append(self, event: WideEvent) -> None:
        ...

    def list_events(self, *, run_id: str | None = None) -> list[WideEvent]:
        ...


class EpisodeStorePort(Protocol):
    def append(self, episode: EpisodeRecord) -> None:
        ...

    def list_episodes(self, *, user_id: str | None = None, limit: int = 50) -> list[EpisodeRecord]:
        ...


class ProfileStorePort(Protocol):
    def upsert(self, fact: ProfileFactRecord) -> None:
        ...

    def list_facts(self, *, subject_id: str | None = None) -> list[ProfileFactRecord]:
        ...


class ProcedureStorePort(Protocol):
    def upsert(self, procedure: ProcedureRecord) -> None:
        ...

    def list_procedures(self) -> list[ProcedureRecord]:
        ...
