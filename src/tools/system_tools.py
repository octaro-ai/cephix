"""System tools that are always available to the LLM, regardless of SOP/Skill.

Three memory tools (read, write, search) and one procedure proposal tool.
These are mounted automatically by the ContextAssembler.
"""

from __future__ import annotations

from typing import Any

from src.domain import ExecutionContext
from src.memory.models import ProcedureRecord
from src.ports import MemoryPort
from src.tools.models import ToolDefinition, ToolParameter
from src.utils import new_id, utc_now_iso


# ---------------------------------------------------------------------------
# Tool definitions (schemas for the LLM)
# ---------------------------------------------------------------------------

MEMORY_READ = ToolDefinition(
    name="memory.read",
    description="Read specific memory facts or recent interactions for a user.",
    parameters=[
        ToolParameter(name="user_id", type="string", description="User ID to read memory for"),
        ToolParameter(
            name="kind",
            type="string",
            description="Filter by fact kind (e.g. 'response_style', 'task_preference'). Omit for all.",
            required=False,
        ),
    ],
    metadata={"system_tool": True},
)

MEMORY_WRITE = ToolDefinition(
    name="memory.write",
    description="Write a new fact or observation into profile memory.",
    parameters=[
        ToolParameter(name="user_id", type="string", description="User ID to store the fact for"),
        ToolParameter(
            name="kind",
            type="string",
            description="Category of the fact (e.g. 'preference', 'observation', 'task_preference')",
        ),
        ToolParameter(name="content", type="string", description="The fact or observation to remember"),
        ToolParameter(
            name="score",
            type="number",
            description="Confidence score between 0.0 and 1.0",
            required=False,
        ),
    ],
    metadata={"system_tool": True},
)

MEMORY_SEARCH = ToolDefinition(
    name="memory.search",
    description="Search memory for facts matching a query string.",
    parameters=[
        ToolParameter(name="query", type="string", description="Search term to match against fact content"),
        ToolParameter(
            name="user_id",
            type="string",
            description="Limit search to a specific user. Omit for all users.",
            required=False,
        ),
    ],
    metadata={"system_tool": True},
)

PROCEDURE_PROPOSE = ToolDefinition(
    name="procedure.propose",
    description=(
        "Propose a new reusable procedure (work pattern) based on observed interactions. "
        "The proposal will be stored as a candidate for human review."
    ),
    parameters=[
        ToolParameter(name="name", type="string", description="Procedure name (e.g. 'weekly-inbox-review.v1')"),
        ToolParameter(name="description", type="string", description="What this procedure does"),
        ToolParameter(
            name="steps",
            type="string",
            description="Comma-separated list of steps (e.g. 'list inbox, summarize, report')",
        ),
    ],
    metadata={"system_tool": True},
)

CORE_MEMORY_READ = ToolDefinition(
    name="core_memory.read",
    description="Read the user's core memory -- the curated essentials the robot knows about this person.",
    parameters=[
        ToolParameter(name="user_id", type="string", description="User ID to read core memory for"),
    ],
    metadata={"system_tool": True},
)

CORE_MEMORY_UPDATE = ToolDefinition(
    name="core_memory.update",
    description=(
        "Replace the user's core memory with updated content. "
        "Core memory is a short, curated text block (max 2000 chars) "
        "containing the most important facts about this user. "
        "You are responsible for deciding what stays and what gets removed."
    ),
    parameters=[
        ToolParameter(name="user_id", type="string", description="User ID to update core memory for"),
        ToolParameter(
            name="content",
            type="string",
            description="The new core memory content (replaces existing). Max 2000 characters.",
        ),
    ],
    metadata={"system_tool": True},
)

CORE_MEMORY_BUDGET = 2000

ALL_SYSTEM_TOOLS: list[ToolDefinition] = [
    MEMORY_READ,
    MEMORY_WRITE,
    MEMORY_SEARCH,
    CORE_MEMORY_READ,
    CORE_MEMORY_UPDATE,
    PROCEDURE_PROPOSE,
]


# ---------------------------------------------------------------------------
# Handler factory
# ---------------------------------------------------------------------------


class SystemToolHandlers:
    """Produces handler callables for system tools.

    Each handler receives ``(ExecutionContext, dict[str, Any])`` and returns
    a result, matching the ``ToolHandler`` signature used by
    ``GovernedToolExecutor``.
    """

    def __init__(
        self,
        *,
        memory: MemoryPort,
        procedure_sink: _ProcedureSinkPort | None = None,
    ) -> None:
        self._memory = memory
        self._procedure_sink = procedure_sink

    def get_handlers(self) -> dict[str, Any]:
        return {
            "memory.read": self._handle_memory_read,
            "memory.write": self._handle_memory_write,
            "memory.search": self._handle_memory_search,
            "core_memory.read": self._handle_core_memory_read,
            "core_memory.update": self._handle_core_memory_update,
            "procedure.propose": self._handle_procedure_propose,
        }

    # -- memory.read ---------------------------------------------------------

    def _handle_memory_read(self, ctx: ExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
        user_id = str(arguments.get("user_id", ctx.user_id))
        context = self._memory.build_context(user_id, ctx.conversation_id)

        kind_filter = arguments.get("kind")
        facts = context.get("facts", [])
        if kind_filter:
            facts = [f for f in facts if f.get("kind") == kind_filter]

        return {
            "facts": facts,
            "recent_interactions": context.get("recent_interactions", []),
        }

    # -- memory.write --------------------------------------------------------

    def _handle_memory_write(self, ctx: ExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
        user_id = str(arguments.get("user_id", ctx.user_id))
        kind = str(arguments["kind"])
        content = str(arguments["content"])
        score = float(arguments.get("score", 0.8))

        self._memory.remember_fact(user_id, kind, content, score)
        return {"stored": True, "user_id": user_id, "kind": kind, "content": content}

    # -- memory.search -------------------------------------------------------

    def _handle_memory_search(self, ctx: ExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments["query"]).lower()
        user_id = arguments.get("user_id")

        search_user = str(user_id) if user_id else ctx.user_id
        context = self._memory.build_context(search_user, None)
        facts = context.get("facts", [])

        matches = [f for f in facts if query in str(f.get("content", "")).lower()]
        return {"matches": matches, "total_searched": len(facts)}

    # -- core_memory.read ----------------------------------------------------

    def _handle_core_memory_read(self, ctx: ExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
        user_id = str(arguments.get("user_id", ctx.user_id))
        content = self._memory.get_core_memory(user_id)
        return {"user_id": user_id, "content": content, "length": len(content), "budget": CORE_MEMORY_BUDGET}

    # -- core_memory.update --------------------------------------------------

    def _handle_core_memory_update(self, ctx: ExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
        user_id = str(arguments.get("user_id", ctx.user_id))
        content = str(arguments["content"])

        if len(content) > CORE_MEMORY_BUDGET:
            return {
                "stored": False,
                "error": f"Content exceeds budget ({len(content)}/{CORE_MEMORY_BUDGET} chars). Shorten it.",
                "length": len(content),
                "budget": CORE_MEMORY_BUDGET,
            }

        self._memory.set_core_memory(user_id, content)
        return {"stored": True, "user_id": user_id, "length": len(content), "budget": CORE_MEMORY_BUDGET}

    # -- procedure.propose ---------------------------------------------------

    def _handle_procedure_propose(self, ctx: ExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
        name = str(arguments["name"])
        description = str(arguments["description"])
        steps = [s.strip() for s in str(arguments["steps"]).split(",") if s.strip()]

        record = ProcedureRecord(
            procedure_id=new_id("proc"),
            name=name,
            description=description,
            steps=steps,
            confidence=0.5,
            status="proposed",
        )

        if self._procedure_sink is not None:
            self._procedure_sink.upsert(record)

        return {
            "proposed": True,
            "procedure_id": record.procedure_id,
            "name": name,
            "status": "proposed",
        }


# ---------------------------------------------------------------------------
# Minimal protocol so we don't couple to concrete stores
# ---------------------------------------------------------------------------

class _ProcedureSinkPort:
    """Structural typing placeholder -- matches ProcedureStorePort.upsert."""

    def upsert(self, procedure: ProcedureRecord) -> None:
        ...
