from __future__ import annotations

from typing import Protocol

from src.skills.models import SkillDefinition
from src.sop.models import SOPDefinition
from src.tools.models import ToolDefinition


class ToolRepositoryWritePort(Protocol):
    """Write access to the shared tool repository."""

    def publish(self, definition: ToolDefinition) -> None:
        ...

    def unpublish(self, tool_name: str) -> None:
        ...


class SkillRepositoryWritePort(Protocol):
    """Write access to the shared skill repository."""

    def publish(self, definition: SkillDefinition) -> None:
        ...

    def unpublish(self, skill_name: str) -> None:
        ...


class SOPRepositoryWritePort(Protocol):
    """Write access to the shared SOP repository."""

    def publish(self, definition: SOPDefinition) -> None:
        ...

    def unpublish(self, sop_name: str) -> None:
        ...
