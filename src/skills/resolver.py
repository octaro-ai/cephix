from __future__ import annotations

from src.domain import RobotEvent
from src.skills.models import SkillDefinition
from src.skills.ports import SkillRepositoryPort


class DefaultSkillResolver:
    """Resolves skills for a given event.

    Strategy: if the event payload contains a ``skills`` key (list of skill
    names), those are resolved.  Otherwise all available skills are returned
    (useful during prototyping / when no SOP narrows the selection yet).
    """

    def __init__(self, repository: SkillRepositoryPort) -> None:
        self._repository = repository

    def resolve(self, event: RobotEvent, user_id: str) -> list[SkillDefinition]:
        requested_names: list[str] | None = event.payload.get("skills")

        if requested_names is not None:
            skills: list[SkillDefinition] = []
            for name in requested_names:
                skill = self._repository.get_skill(name)
                if skill is not None:
                    skills.append(skill)
            return skills

        return self._repository.list_available()
