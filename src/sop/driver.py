"""SOP tool driver — exposes SOPs as tools for the LLM.

Implements ``ToolDriverPort`` so it plugs into the standard tool pipeline.

Tools:
  - sop.list       — List all available SOPs in the repository
  - sop.read       — Read a specific SOP (full details)
  - sop.activate   — Load a SOP: injects steps as task.plan, returns instructions
  - sop.deactivate — Unload the current SOP when work is done
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.domain import ExecutionContext
from src.sop.ports import SOPRepositoryPort
from src.tools.models import ToolDefinition, ToolParameter

logger = logging.getLogger(__name__)


# -- Tool definitions --------------------------------------------------------

_TOOL_SOP_LIST = ToolDefinition(
    name="sop.list",
    description=(
        "List all available Standard Operating Procedures (SOPs) in the repository. "
        "Use this to discover SOPs that can help with complex or recurring tasks."
    ),
    parameters=[],
    metadata={"system_tool": True},
)

_TOOL_SOP_READ = ToolDefinition(
    name="sop.read",
    description="Read a specific SOP by name — returns full steps and instructions",
    parameters=[
        ToolParameter(name="name", type="string", description="SOP name (e.g. 'order-export')", required=True),
    ],
    metadata={"system_tool": True},
)

_TOOL_SOP_ACTIVATE = ToolDefinition(
    name="sop.activate",
    description=(
        "Activate a SOP for the current task. "
        "This loads the SOP steps as your task checklist and returns "
        "the full instructions. Use task.update to track progress through the steps. "
        "Deactivate with sop.deactivate when the work is complete."
    ),
    parameters=[
        ToolParameter(name="name", type="string", description="SOP name to activate", required=True),
    ],
    metadata={"system_tool": True},
)

_TOOL_SOP_DEACTIVATE = ToolDefinition(
    name="sop.deactivate",
    description=(
        "Deactivate the currently active SOP. "
        "Call this when the SOP work is complete and confirmed by the user, "
        "or when you need to switch to a different SOP."
    ),
    parameters=[],
    metadata={"system_tool": True},
)


class SOPToolDriver:
    """ToolDriverPort that exposes SOPs as tools.

    The repository is the library of available SOPs (read-only).
    The driver tracks which SOP is currently active.
    """

    def __init__(self, repository: SOPRepositoryPort) -> None:
        self._repository = repository
        self._active_sop_name: str | None = None

    @property
    def active_sop_name(self) -> str | None:
        return self._active_sop_name

    def list_tools(self) -> list[ToolDefinition]:
        tools = [_TOOL_SOP_LIST, _TOOL_SOP_READ, _TOOL_SOP_ACTIVATE]
        if self._active_sop_name is not None:
            tools.append(_TOOL_SOP_DEACTIVATE)
        return tools

    def execute(self, ctx: ExecutionContext, tool_name: str, arguments: dict[str, Any]) -> Any:
        if tool_name == "sop.list":
            return self._handle_list()
        if tool_name == "sop.read":
            return self._handle_read(arguments)
        if tool_name == "sop.activate":
            return self._handle_activate(arguments)
        if tool_name == "sop.deactivate":
            return self._handle_deactivate()
        raise RuntimeError(f"SOPToolDriver has no handler for: {tool_name!r}")

    # -- Handlers ------------------------------------------------------------

    def _handle_list(self) -> dict[str, Any]:
        sops = self._repository.list_available()
        return {
            "sops": [
                {
                    "name": s.name,
                    "description": s.description,
                    "version": s.version,
                    "steps_count": len(s.steps),
                    "required_tools": s.required_tools,
                    "trigger_patterns": s.trigger_patterns,
                }
                for s in sops
            ],
            "count": len(sops),
            "active_sop": self._active_sop_name,
        }

    def _handle_read(self, arguments: dict[str, Any]) -> dict[str, Any]:
        name = str(arguments.get("name", ""))
        sop = self._repository.get_sop(name)
        if sop is None:
            return {"error": f"SOP '{name}' not found"}
        return self._sop_to_dict(sop)

    def _handle_activate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        name = str(arguments.get("name", ""))
        sop = self._repository.get_sop(name)
        if sop is None:
            return {"error": f"SOP '{name}' not found"}

        self._active_sop_name = name
        logger.info("Activated SOP: %s v%s", sop.name, sop.version)

        # Build the task list items that the LLM should pass to task.plan.
        task_items = [
            {"content": step.name, "status": "pending"}
            for step in sop.steps
        ]

        result = self._sop_to_dict(sop)
        result["activated"] = True
        result["task_items"] = task_items
        result["task_items_json"] = json.dumps(task_items, ensure_ascii=False)
        result["hint"] = (
            "SOP activated. Call task.plan with the task_items_json above "
            "to set up your checklist, then work through each step. "
            "Use task.update to mark steps as completed. "
            "Call sop.deactivate when the work is done."
        )
        if sop.learnings_document:
            result["learnings_hint"] = (
                f"Read '{sop.learnings_document}' via document.read for known issues and solutions. "
                f"Write new learnings back to this document via document.write when you encounter new problems."
            )
        return result

    def _handle_deactivate(self) -> dict[str, Any]:
        previous = self._active_sop_name
        self._active_sop_name = None
        logger.info("Deactivated SOP: %s", previous)
        return {"deactivated": previous, "active_sop": None}

    @staticmethod
    def _sop_to_dict(sop) -> dict[str, Any]:
        return {
            "name": sop.name,
            "description": sop.description,
            "version": sop.version,
            "required_tools": sop.required_tools,
            "learnings_document": sop.learnings_document,
            "steps": [
                {
                    "id": step.id,
                    "name": step.name,
                    "instructions": step.instructions.strip(),
                }
                for step in sop.steps
            ],
        }
