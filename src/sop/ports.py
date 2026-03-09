from __future__ import annotations

from typing import Protocol

from src.domain import RobotEvent
from src.sop.models import SOPDefinition


class SOPRepositoryPort(Protocol):
    def list_available(self) -> list[SOPDefinition]:
        ...

    def get_sop(self, name: str) -> SOPDefinition | None:
        ...


class SOPResolverPort(Protocol):
    def resolve(self, event: RobotEvent, user_id: str) -> list[SOPDefinition]:
        ...


class SOPCompilerPort(Protocol):
    """Transforms text-based SOPs into DAG definitions."""

    def compile(self, raw_text: str) -> SOPDefinition:
        ...
