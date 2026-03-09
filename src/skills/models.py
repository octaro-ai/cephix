from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SkillDefinition:
    name: str
    description: str
    version: str
    instructions: str
    required_tools: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
