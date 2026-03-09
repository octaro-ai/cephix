from __future__ import annotations

import re

from src.domain import RobotEvent
from src.sop.models import SOPDefinition
from src.sop.ports import SOPRepositoryPort


class DefaultSOPResolver:
    """Matches SOPs to an event using trigger_patterns.

    Each SOP can define ``trigger_patterns`` -- simple substring or regex
    patterns.  If the event text matches any pattern the SOP is selected.
    If no patterns match, no SOPs are active (the robot operates without
    workflow constraints).
    """

    def __init__(self, repository: SOPRepositoryPort) -> None:
        self._repository = repository

    def resolve(self, event: RobotEvent, user_id: str) -> list[SOPDefinition]:
        text = (event.text or "").lower()
        if not text:
            return []

        matched: list[SOPDefinition] = []
        for sop in self._repository.list_available():
            for pattern in sop.trigger_patterns:
                try:
                    if re.search(pattern, text, re.IGNORECASE):
                        matched.append(sop)
                        break
                except re.error:
                    if pattern.lower() in text:
                        matched.append(sop)
                        break
        return matched
