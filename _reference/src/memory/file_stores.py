from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

from src.memory.models import EpisodeRecord, ProcedureRecord, ProfileFactRecord
from src.telemetry import WideEvent


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


class FileEventStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, event: WideEvent) -> None:
        _ensure_parent(self.path)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")

    def list_events(self, *, run_id: str | None = None) -> list[WideEvent]:
        if not self.path.exists():
            return []

        events: list[WideEvent] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            event = WideEvent(**payload)
            if run_id is None or event.run_id == run_id:
                events.append(event)
        return events


class FileEpisodeStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, episode: EpisodeRecord) -> None:
        _ensure_parent(self.path)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(episode), ensure_ascii=False) + "\n")

    def list_episodes(self, *, user_id: str | None = None, limit: int = 50) -> list[EpisodeRecord]:
        if not self.path.exists():
            return []

        episodes: list[EpisodeRecord] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            episode = EpisodeRecord(**json.loads(line))
            if user_id is None or episode.user_id == user_id:
                episodes.append(episode)
        return episodes[-limit:]


class FileProfileStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def upsert(self, fact: ProfileFactRecord) -> None:
        facts = {stored.fact_id: stored for stored in self.list_facts()}
        facts[fact.fact_id] = fact
        _ensure_parent(self.path)
        payload = [asdict(item) for item in facts.values()]
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def list_facts(self, *, subject_id: str | None = None) -> list[ProfileFactRecord]:
        if not self.path.exists():
            return []

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        facts = [ProfileFactRecord(**item) for item in payload]
        if subject_id is None:
            return facts
        return [fact for fact in facts if fact.subject_id == subject_id]


class FileProcedureStore:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def upsert(self, procedure: ProcedureRecord) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{procedure.procedure_id}.json"
        path.write_text(json.dumps(asdict(procedure), ensure_ascii=False, indent=2), encoding="utf-8")

    def list_procedures(self) -> list[ProcedureRecord]:
        if not self.directory.exists():
            return []

        procedures: list[ProcedureRecord] = []
        for path in sorted(self.directory.glob("*.json")):
            procedures.append(ProcedureRecord(**json.loads(path.read_text(encoding="utf-8"))))
        return procedures
