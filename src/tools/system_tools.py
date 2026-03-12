"""System tools that are always available to the LLM, regardless of SOP/Skill.

Memory, core-memory, document, and procedure tools.
Implements ``ToolDriverPort`` so it plugs into the standard tool pipeline.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
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

SELF_DOCUMENT_LIST = ToolDefinition(
    name="document.list",
    description=(
        "List all editable memory documents (e.g. IDENTITY.md, USER.md, MEMORY.md). "
        "Firmware documents (AGENTS.md, POLICY.md, CONSTITUTION.md) are read-only."
    ),
    parameters=[],
    metadata={"system_tool": True},
)

SELF_DOCUMENT_READ = ToolDefinition(
    name="document.read",
    description="Read one of the robot's own memory documents by filename.",
    parameters=[
        ToolParameter(
            name="filename",
            type="string",
            description="Document filename (e.g. 'IDENTITY.md', 'USER.md', 'MEMORY.md', 'BOOTSTRAP.md')",
        ),
    ],
    metadata={"system_tool": True},
)

SELF_DOCUMENT_WRITE = ToolDefinition(
    name="document.write",
    description=(
        "Overwrite one of the robot's own memory documents. "
        "Only memory documents may be written (IDENTITY.md, USER.md, MEMORY.md, etc.). "
        "Firmware documents are protected and cannot be modified."
    ),
    parameters=[
        ToolParameter(
            name="filename",
            type="string",
            description="Document filename (e.g. 'IDENTITY.md', 'USER.md', 'MEMORY.md')",
        ),
        ToolParameter(
            name="content",
            type="string",
            description="New content for the document (replaces existing).",
        ),
    ],
    metadata={"system_tool": True},
)

SELF_DOCUMENT_DELETE = ToolDefinition(
    name="document.delete",
    description=(
        "Delete one of the robot's own memory documents. "
        "Use this to remove one-time documents like BOOTSTRAP.md after they have been processed."
    ),
    parameters=[
        ToolParameter(
            name="filename",
            type="string",
            description="Document filename to delete (e.g. 'BOOTSTRAP.md')",
        ),
    ],
    metadata={"system_tool": True},
)

ALL_SYSTEM_TOOLS: list[ToolDefinition] = [
    MEMORY_READ,
    MEMORY_WRITE,
    MEMORY_SEARCH,
    CORE_MEMORY_READ,
    CORE_MEMORY_UPDATE,
    SELF_DOCUMENT_LIST,
    SELF_DOCUMENT_READ,
    SELF_DOCUMENT_WRITE,
    SELF_DOCUMENT_DELETE,
    PROCEDURE_PROPOSE,
]


# ---------------------------------------------------------------------------
# SystemToolDriver — implements ToolDriverPort
# ---------------------------------------------------------------------------


_FIRMWARE_FILES = frozenset({"AGENTS.md", "POLICY.md", "CONSTITUTION.md", "HEARTBEAT.md"})

# Handler type alias (matches ToolHandler in executor.py)
_Handler = Callable[[ExecutionContext, dict[str, Any]], Any]


class SystemToolDriver:
    """System tool driver — provides definitions AND execution.

    Implements the ``ToolDriverPort`` protocol so it can be plugged into
    a ``ToolCollector`` alongside MCS adapters, domain drivers, etc.
    """

    def __init__(
        self,
        *,
        memory: MemoryPort,
        memory_dir: str | Path | None = None,
        procedure_sink: _ProcedureSinkPort | None = None,
    ) -> None:
        self._memory = memory
        self._memory_dir = Path(memory_dir) if memory_dir else None
        self._procedure_sink = procedure_sink
        self._handlers: dict[str, _Handler] = {
            "memory.read": self._handle_memory_read,
            "memory.write": self._handle_memory_write,
            "memory.search": self._handle_memory_search,
            "core_memory.read": self._handle_core_memory_read,
            "core_memory.update": self._handle_core_memory_update,
            "document.list": self._handle_self_document_list,
            "document.read": self._handle_self_document_read,
            "document.write": self._handle_self_document_write,
            "document.delete": self._handle_self_document_delete,
            "procedure.propose": self._handle_procedure_propose,
        }

    # -- ToolDriverPort interface -------------------------------------------

    def list_tools(self) -> list[ToolDefinition]:
        return list(ALL_SYSTEM_TOOLS)

    def execute(self, ctx: ExecutionContext, tool_name: str, arguments: dict[str, Any]) -> Any:
        handler = self._handlers.get(tool_name)
        if handler is None:
            raise RuntimeError(f"SystemToolDriver has no handler for: {tool_name!r}")
        return handler(ctx, arguments)


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

    # -- document.list --------------------------------------------------

    def _handle_self_document_list(self, ctx: ExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._memory_dir is None:
            return {"error": "No memory directory configured."}
        files = []
        for path in sorted(self._memory_dir.glob("*.md")):
            files.append({
                "filename": path.name,
                "size": path.stat().st_size,
                "editable": path.name not in _FIRMWARE_FILES,
            })
        return {"documents": files}

    # -- document.read --------------------------------------------------

    def _handle_self_document_read(self, ctx: ExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._memory_dir is None:
            return {"error": "No memory directory configured."}
        filename = str(arguments["filename"])
        if "/" in filename or "\\" in filename or ".." in filename:
            return {"error": "Invalid filename."}
        path = self._memory_dir / filename
        if not path.exists():
            return {"filename": filename, "content": "", "exists": False}
        return {
            "filename": filename,
            "content": path.read_text(encoding="utf-8"),
            "exists": True,
        }

    # -- document.write -------------------------------------------------

    def _handle_self_document_write(self, ctx: ExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._memory_dir is None:
            return {"error": "No memory directory configured."}
        filename = str(arguments["filename"])
        if "/" in filename or "\\" in filename or ".." in filename:
            return {"error": "Invalid filename."}
        if filename in _FIRMWARE_FILES:
            return {"error": f"{filename} is firmware and read-only. Only memory documents can be written."}
        content = str(arguments["content"])
        path = self._memory_dir / filename
        path.write_text(content, encoding="utf-8")
        return {"written": True, "filename": filename, "length": len(content)}

    # -- document.delete ------------------------------------------------

    def _handle_self_document_delete(self, ctx: ExecutionContext, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._memory_dir is None:
            return {"error": "No memory directory configured."}
        filename = str(arguments["filename"])
        if "/" in filename or "\\" in filename or ".." in filename:
            return {"error": "Invalid filename."}
        if filename in _FIRMWARE_FILES:
            return {"error": f"{filename} is firmware and cannot be deleted."}
        path = self._memory_dir / filename
        if not path.exists():
            return {"deleted": False, "filename": filename, "reason": "File does not exist."}
        path.unlink()
        return {"deleted": True, "filename": filename}

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
