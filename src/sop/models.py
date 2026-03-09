from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SOPNode:
    node_id: str
    description: str
    available_tools: list[str] = field(default_factory=list)
    skill_name: str | None = None


@dataclass
class SOPEdge:
    from_node: str
    to_node: str
    condition: str | None = None


@dataclass
class SOPDefinition:
    name: str
    description: str
    version: str
    entry_node: str
    nodes: list[SOPNode] = field(default_factory=list)
    edges: list[SOPEdge] = field(default_factory=list)
    required_skills: list[str] = field(default_factory=list)
    required_tools: list[str] = field(default_factory=list)
    trigger_patterns: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
