from __future__ import annotations

import base64
import logging
from typing import Any

from src.domain import ExecutionContext
from src.tools.models import ToolDefinition, ToolParameter
from src.workstation.ports import WorkstationPort

logger = logging.getLogger(__name__)

MAX_OUTPUT_CHARS = 8000


# -- Tool definitions --------------------------------------------------------

_TOOL_WORKSTATION_START = ToolDefinition(
    name="workstation.start",
    description="Start or resume the workstation environment",
    parameters=[],
)

_TOOL_WORKSTATION_STOP = ToolDefinition(
    name="workstation.stop",
    description="Stop the workstation (state is preserved for next start)",
    parameters=[],
)

_TOOL_WORKSTATION_INFO = ToolDefinition(
    name="workstation.info",
    description="Show workstation configuration and current status",
    parameters=[],
)

_TOOL_SHELL_EXEC = ToolDefinition(
    name="shell.exec",
    description="Execute a shell command on the workstation",
    parameters=[
        ToolParameter(name="command", type="string", description="The shell command to execute", required=True),
        ToolParameter(name="timeout", type="integer", description="Timeout in seconds (default: 30)", required=False),
    ],
)

_TOOL_FILE_PUT = ToolDefinition(
    name="file.put",
    description="Upload a file to the workstation",
    parameters=[
        ToolParameter(name="path", type="string", description="Absolute path on the workstation (e.g. /workspace/script.py)", required=True),
        ToolParameter(name="content", type="string", description="File content as text", required=True),
    ],
)

_TOOL_FILE_GET = ToolDefinition(
    name="file.get",
    description="Download a file from the workstation",
    parameters=[
        ToolParameter(name="path", type="string", description="Absolute path on the workstation", required=True),
    ],
)

# Lifecycle tools are always visible.
_LIFECYCLE_TOOLS = [_TOOL_WORKSTATION_START, _TOOL_WORKSTATION_STOP, _TOOL_WORKSTATION_INFO]

# Session tools appear only when the workstation is running.
_SESSION_TOOLS = [_TOOL_SHELL_EXEC, _TOOL_FILE_PUT, _TOOL_FILE_GET]


class WorkstationToolDriver:
    """ToolDriverPort that exposes a workstation as tools.

    Lifecycle tools (start, stop, info) are always available.
    Session tools (shell.exec, file.put, file.get) appear only after
    the workstation has been started.
    """

    def __init__(self, backend: WorkstationPort) -> None:
        self._backend = backend
        self._running = False

    def list_tools(self) -> list[ToolDefinition]:
        tools = list(_LIFECYCLE_TOOLS)
        if self._running:
            tools += _SESSION_TOOLS
        return tools

    def execute(self, ctx: ExecutionContext, tool_name: str, arguments: dict[str, Any]) -> Any:
        if tool_name == "workstation.start":
            return self._handle_start()
        if tool_name == "workstation.stop":
            return self._handle_stop()
        if tool_name == "workstation.info":
            return self._handle_info()
        if tool_name == "shell.exec":
            return self._handle_shell_exec(arguments)
        if tool_name == "file.put":
            return self._handle_file_put(arguments)
        if tool_name == "file.get":
            return self._handle_file_get(arguments)
        raise RuntimeError(f"WorkstationToolDriver has no handler for: {tool_name!r}")

    # -- Handlers ------------------------------------------------------------

    def _handle_start(self) -> dict[str, Any]:
        info = self._backend.start()
        self._running = True
        return {"status": "running", **info}

    def _handle_stop(self) -> dict[str, Any]:
        self._backend.stop()
        self._running = False
        return {"status": "stopped"}

    def _handle_info(self) -> dict[str, Any]:
        return self._backend.status()

    def _handle_shell_exec(self, arguments: dict[str, Any]) -> dict[str, Any]:
        command = arguments.get("command", "")
        timeout = int(arguments.get("timeout", 30))
        if not command:
            return {"error": "command is required"}

        result = self._backend.exec(command, timeout=timeout)

        stdout = result.stdout
        stderr = result.stderr
        truncated = False
        if len(stdout) > MAX_OUTPUT_CHARS:
            total = len(stdout)
            stdout = stdout[:MAX_OUTPUT_CHARS] + f"\n... truncated ({total} chars total)"
            truncated = True
        if len(stderr) > MAX_OUTPUT_CHARS:
            total = len(stderr)
            stderr = stderr[:MAX_OUTPUT_CHARS] + f"\n... truncated ({total} chars total)"
            truncated = True

        return {
            "exit_code": result.exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": truncated,
        }

    def _handle_file_put(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = arguments.get("path", "")
        content = arguments.get("content", "")
        if not path:
            return {"error": "path is required"}
        self._backend.put_file(path, content.encode("utf-8"))
        return {"path": path, "bytes_written": len(content)}

    def _handle_file_get(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = arguments.get("path", "")
        if not path:
            return {"error": "path is required"}
        data = self._backend.get_file(path)
        try:
            text = data.decode("utf-8")
            return {"path": path, "content": text, "bytes": len(data)}
        except UnicodeDecodeError:
            return {"path": path, "content_base64": base64.b64encode(data).decode(), "bytes": len(data)}
