"""ToolBuilder robot configuration.

A ToolBuilder is a standard DigitalRobot configured with the
``tool-authoring`` skill and wired to write-capable repository ports.
It creates new tools, skills, and SOPs and publishes them to a shared
repository that worker robots read from.

This module provides:

* ``FileRepositoryWriter`` -- a concrete implementation of all three
  write ports that persists definitions as YAML files on disk.
* ``build_toolbuilder_skill`` -- creates the SkillDefinition for the
  tool-authoring skill.

The actual ToolBuilder robot is assembled via normal ``DigitalRobot``
construction, injecting the writer as a tool handler.

Example wiring::

    from src.toolbuilder import FileRepositoryWriter, build_toolbuilder_skill

    writer = FileRepositoryWriter(
        tools_dir="shared_repo/tools",
        skills_dir="shared_repo/skills",
        sops_dir="shared_repo/sops",
    )
    authoring_skill = build_toolbuilder_skill()

    # Register tool handlers that the LLM can invoke
    executor.register_handler("repo.publish_tool", writer.handle_publish_tool)
    executor.register_handler("repo.publish_skill", writer.handle_publish_skill)
    executor.register_handler("repo.publish_sop", writer.handle_publish_sop)
    executor.register_handler("repo.unpublish_tool", writer.handle_unpublish)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from src.domain import ExecutionContext
from src.skills.models import SkillDefinition
from src.sop.models import SOPDefinition, SOPEdge, SOPNode
from src.tools.models import ToolDefinition, ToolParameter


def build_toolbuilder_skill() -> SkillDefinition:
    return SkillDefinition(
        name="tool-authoring",
        description="Skill for creating, updating, and publishing tools, skills, and SOPs.",
        version="1.0",
        instructions=(
            "You are a tool-authoring assistant. When the user describes a new tool, "
            "skill, or SOP, you create a well-structured definition and publish it to "
            "the shared repository. Always validate the schema before publishing. "
            "Ask clarifying questions if the specification is ambiguous."
        ),
        required_tools=[
            "repo.publish_tool",
            "repo.publish_skill",
            "repo.publish_sop",
            "repo.unpublish_tool",
        ],
    )


def build_toolbuilder_tool_definitions() -> list[ToolDefinition]:
    """Returns the tool definitions the ToolBuilder robot needs mounted."""
    return [
        ToolDefinition(
            name="repo.publish_tool",
            description="Publish a new tool definition to the shared repository.",
            parameters=[
                ToolParameter(name="name", description="Tool name"),
                ToolParameter(name="description", description="Tool description"),
                ToolParameter(name="parameters", type="array", description="List of parameter objects", required=False),
                ToolParameter(name="metadata", type="object", description="Optional metadata", required=False),
            ],
        ),
        ToolDefinition(
            name="repo.publish_skill",
            description="Publish a new skill definition to the shared repository.",
            parameters=[
                ToolParameter(name="name", description="Skill name"),
                ToolParameter(name="description", description="Skill description"),
                ToolParameter(name="version", description="Semantic version"),
                ToolParameter(name="instructions", description="LLM instructions for this skill"),
                ToolParameter(name="required_tools", type="array", description="List of tool names", required=False),
            ],
        ),
        ToolDefinition(
            name="repo.publish_sop",
            description="Publish a new SOP definition to the shared repository.",
            parameters=[
                ToolParameter(name="name", description="SOP name"),
                ToolParameter(name="description", description="SOP description"),
                ToolParameter(name="version", description="Semantic version"),
                ToolParameter(name="definition_yaml", description="Full SOP definition as YAML string"),
            ],
        ),
        ToolDefinition(
            name="repo.unpublish_tool",
            description="Remove a tool from the shared repository.",
            parameters=[
                ToolParameter(name="name", description="Name of the tool to unpublish"),
            ],
        ),
    ]


class FileRepositoryWriter:
    """Persists tool/skill/SOP definitions as YAML files on disk.

    Implements all three write ports and provides handler methods
    suitable for registration with a ``GovernedToolExecutor``.
    """

    def __init__(
        self,
        *,
        tools_dir: str | Path,
        skills_dir: str | Path,
        sops_dir: str | Path,
    ) -> None:
        self._tools_dir = Path(tools_dir)
        self._skills_dir = Path(skills_dir)
        self._sops_dir = Path(sops_dir)
        for d in (self._tools_dir, self._skills_dir, self._sops_dir):
            d.mkdir(parents=True, exist_ok=True)

    # --- ToolRepositoryWritePort ---

    def publish_tool(self, definition: ToolDefinition) -> None:
        data = {
            "name": definition.name,
            "description": definition.description,
            "parameters": [
                {
                    "name": p.name,
                    "type": p.type,
                    "description": p.description,
                    "required": p.required,
                    **({"enum": p.enum} if p.enum else {}),
                }
                for p in definition.parameters
            ],
            "metadata": definition.metadata,
        }
        self._write_yaml(self._tools_dir / f"{definition.name}.yaml", data)

    def unpublish_tool(self, tool_name: str) -> None:
        path = self._tools_dir / f"{tool_name}.yaml"
        if path.exists():
            path.unlink()

    # --- SkillRepositoryWritePort ---

    def publish_skill(self, definition: SkillDefinition) -> None:
        data = {
            "name": definition.name,
            "description": definition.description,
            "version": definition.version,
            "instructions": definition.instructions,
            "required_tools": definition.required_tools,
            "metadata": definition.metadata,
        }
        self._write_yaml(self._skills_dir / f"{definition.name}.yaml", data)

    def unpublish_skill(self, skill_name: str) -> None:
        path = self._skills_dir / f"{skill_name}.yaml"
        if path.exists():
            path.unlink()

    # --- SOPRepositoryWritePort ---

    def publish_sop(self, definition: SOPDefinition) -> None:
        data = {
            "name": definition.name,
            "description": definition.description,
            "version": definition.version,
            "entry_node": definition.entry_node,
            "nodes": [
                {
                    "node_id": n.node_id,
                    "description": n.description,
                    "available_tools": n.available_tools,
                    **({"skill_name": n.skill_name} if n.skill_name else {}),
                }
                for n in definition.nodes
            ],
            "edges": [
                {
                    "from_node": e.from_node,
                    "to_node": e.to_node,
                    **({"condition": e.condition} if e.condition else {}),
                }
                for e in definition.edges
            ],
            "required_skills": definition.required_skills,
            "required_tools": definition.required_tools,
            "trigger_patterns": definition.trigger_patterns,
            "metadata": definition.metadata,
        }
        self._write_yaml(self._sops_dir / f"{definition.name}.yaml", data)

    def unpublish_sop(self, sop_name: str) -> None:
        path = self._sops_dir / f"{sop_name}.yaml"
        if path.exists():
            path.unlink()

    # --- Handler methods for GovernedToolExecutor ---

    def handle_publish_tool(self, ctx: ExecutionContext, arguments: dict[str, Any]) -> dict[str, str]:
        params = [
            ToolParameter(
                name=p.get("name", ""),
                type=p.get("type", "string"),
                description=p.get("description", ""),
                required=p.get("required", True),
                enum=p.get("enum"),
            )
            for p in arguments.get("parameters", [])
            if isinstance(p, dict)
        ]
        defn = ToolDefinition(
            name=arguments["name"],
            description=arguments.get("description", ""),
            parameters=params,
            metadata=arguments.get("metadata", {}),
        )
        self.publish_tool(defn)
        return {"status": "published", "name": defn.name}

    def handle_publish_skill(self, ctx: ExecutionContext, arguments: dict[str, Any]) -> dict[str, str]:
        defn = SkillDefinition(
            name=arguments["name"],
            description=arguments.get("description", ""),
            version=arguments.get("version", "0.1"),
            instructions=arguments.get("instructions", ""),
            required_tools=arguments.get("required_tools", []),
        )
        self.publish_skill(defn)
        return {"status": "published", "name": defn.name}

    def handle_publish_sop(self, ctx: ExecutionContext, arguments: dict[str, Any]) -> dict[str, str]:
        raw_yaml = arguments.get("definition_yaml", "")
        data = yaml.safe_load(raw_yaml) if raw_yaml else arguments
        if not isinstance(data, dict) or "name" not in data:
            raise ValueError("SOP definition must contain at least a 'name' field")

        nodes = [
            SOPNode(
                node_id=n["node_id"],
                description=n.get("description", ""),
                available_tools=n.get("available_tools", []),
                skill_name=n.get("skill_name"),
            )
            for n in data.get("nodes", [])
            if isinstance(n, dict) and "node_id" in n
        ]
        edges = [
            SOPEdge(
                from_node=e["from_node"],
                to_node=e["to_node"],
                condition=e.get("condition"),
            )
            for e in data.get("edges", [])
            if isinstance(e, dict) and "from_node" in e and "to_node" in e
        ]

        defn = SOPDefinition(
            name=data["name"],
            description=data.get("description", ""),
            version=data.get("version", "0.1"),
            entry_node=data.get("entry_node", ""),
            nodes=nodes,
            edges=edges,
            required_skills=data.get("required_skills", []),
            required_tools=data.get("required_tools", []),
            trigger_patterns=data.get("trigger_patterns", []),
            metadata=data.get("metadata", {}),
        )
        self.publish_sop(defn)
        return {"status": "published", "name": defn.name}

    def handle_unpublish(self, ctx: ExecutionContext, arguments: dict[str, Any]) -> dict[str, str]:
        name = arguments["name"]
        self.unpublish_tool(name)
        return {"status": "unpublished", "name": name}

    @staticmethod
    def _write_yaml(path: Path, data: dict[str, Any]) -> None:
        path.write_text(
            yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
