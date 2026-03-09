from __future__ import annotations

from typing import Protocol

from src.domain import RobotEvent
from src.skills.models import SkillDefinition


class SkillRepositoryPort(Protocol):
    def list_available(self) -> list[SkillDefinition]:
        ...

    def get_skill(self, name: str) -> SkillDefinition | None:
        ...


class SkillResolverPort(Protocol):
    """Selects the relevant skills for a given run."""

    def resolve(self, event: RobotEvent, user_id: str) -> list[SkillDefinition]:
        ...
